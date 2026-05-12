from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import List, Optional
import uuid
import datetime as dt
from datetime import timezone
from decimal import Decimal
import json
import re

from app.core.database import get_db
from app.shared.models import Bill, MenuItem, Shop, User, Customer, Subscription, PlanFeature
from app.domains.features.service import FeatureService
from app.core.redis import get_redis
from app.infrastructure.integrations.printer import print_bill_bg
from app.domains.auth.router import get_current_user, get_optional_current_user
from app.shared.schemas import CartItem, BillCreate
from app.core.dependencies.csrf import verify_csrf
from app.core.dependencies.features import require_feature

router = APIRouter(prefix="/billing", tags=["Billing"], dependencies=[Depends(verify_csrf)])
templates = Jinja2Templates(directory="app/frontend/templates")

class DecimalEncoder(json.JSONEncoder):
    """Custom encoder that converts Decimal to float for JSON serialization."""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def json_serializer(obj):
    if isinstance(obj, list):
        return json.dumps(
            [i.to_dict() if hasattr(i, "to_dict") else jsonable_encoder(i) for i in obj],
            cls=DecimalEncoder,
        )
    if hasattr(obj, "to_dict"):
        return json.dumps(obj.to_dict(), cls=DecimalEncoder)
    return json.dumps(jsonable_encoder(obj), cls=DecimalEncoder)

templates.env.filters["tojson"] = json_serializer


@router.get("/", response_class=HTMLResponse)
async def pos_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(get_current_user),
    shop_id: Optional[int] = None
):
    """
    Serves the POS UI with Menu Items loaded for the user's shop.
    Unified logic for Superadmins (can select any shop) and regular users (fixed to their shop).
    """
    from sqlalchemy.orm import selectinload
    
    all_shops = []
    target_shop_id = shop_id
    
    # 1. Access Control & Shop Selection
    if current_user.role == "superadmin":
        # Superadmins see all active shops for switching
        shops_res = await db.execute(
            select(Shop).where(Shop.is_active == True).options(selectinload(Shop.subscription))
        )
        all_shops = shops_res.scalars().all()
        # Default to first shop if none selected via query param
        if not target_shop_id and all_shops:
            target_shop_id = all_shops[0].id
    else:
        # Owners and Staff are restricted to their assigned shop
        target_shop_id = current_user.shop_id

    # 2. Fetch Shop with Subscription (Unified)
    shop = None
    if target_shop_id:
        shop_res = await db.execute(
            select(Shop).where(Shop.id == target_shop_id).options(selectinload(Shop.subscription))
        )
        shop = shop_res.scalars().first()
    
    # 3. Feature Gating — DB-driven via FeatureService (Redis-cached)
    feature_service = FeatureService(db=db, redis=redis)
    shop_features = await feature_service.get_all_features_for_shop(shop) if shop else {}
    items = []
    currency = "\u20b9"
    error_msg = None

    if shop:
        # Fetch Active Menu Items for this shop
        items_res = await db.execute(
            select(MenuItem).where(
                MenuItem.is_active == True,
                MenuItem.shop_id == shop.id
            )
        )
        items = items_res.scalars().all()
        currency = shop.currency_symbol
    else:
        error_msg = "No shop available. Please contact admin."

    return templates.TemplateResponse(request, "pos.html", {
        "items": items,
        "currency": currency,
        "shop": shop,
        "all_shops": all_shops,
        "is_superadmin": current_user.role == "superadmin",
        "user": current_user,
        "features": shop_features,
        "error": error_msg
    })


@router.post("/create")
async def create_bill(
    bill_in: BillCreate, 
    background_tasks: BackgroundTasks, 
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Receives cart [ {id, qty}, ... ], calculates total, saves bill, triggers print.
    Associates bill with the current user's shop.
    """
    # Get current user from cookie
    try:
        current_user = await get_current_user(request, db)
    except Exception:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not bill_in.items:
        raise HTTPException(status_code=400, detail="Cart is empty")
    
    # Get user's shop
    shop = None
    if current_user.shop_id:
        shop_res = await db.execute(select(Shop).where(Shop.id == current_user.shop_id))
        shop = shop_res.scalars().first()

    total_amount = Decimal("0.00")
    items_snapshot = []
    
    # 1. Fetch Items and Calculate Total
    for cart_item in bill_in.items:
        # Only allow items from user's shop
        query = select(MenuItem).where(MenuItem.id == cart_item.id)
        if shop:
            query = query.where(MenuItem.shop_id == shop.id)
        
        result = await db.execute(query)
        menu_item = result.scalars().first()
        
        if not menu_item:
            continue
            
        line_total = menu_item.price * Decimal(str(cart_item.qty))
        total_amount += line_total
        
        items_snapshot.append({
            "id": menu_item.id,
            "name": menu_item.name,
            "category": menu_item.category,
            "price": float(menu_item.price),
            "unit": menu_item.unit,
            "qty": cart_item.qty,
            "line_total": float(line_total)
        })

    if not items_snapshot:
        raise HTTPException(status_code=400, detail="No valid items found")

    # Handle customer if phone number provided
    customer_id = None
    if bill_in.customer_phone and current_user.shop_id:
        phone = re.sub(r'\D', '', bill_in.customer_phone)
        if phone:
            # Check if customer exists
            customer_res = await db.execute(
                select(Customer).where(
                    Customer.shop_id == current_user.shop_id,
                    Customer.phone_number == phone
                )
            )
            customer = customer_res.scalars().first()
            
            if not customer:
                # Create new customer with provided name or placeholder
                customer_name = bill_in.customer_name if bill_in.customer_name else f"Customer {phone[-4:]}"
                
                customer = Customer(
                    name=customer_name,
                    phone_number=phone,
                    shop_id=current_user.shop_id
                )
                db.add(customer)
                await db.flush()  # Get ID without committing
            
            customer_id = customer.id

    # Prepare Shop Data (Do this before commit prevents lazy load errors)
    shop_data = {
        "name": shop.name if shop else "Burger Shop",
        "address": shop.address if shop else "Local Branch",
    }
    printer_ip = shop.printer_ip if shop else None

    # 2. Save Bill with shop_id and customer_id
    try:
        new_bill = Bill(
            bill_number=str(uuid.uuid4())[:8].upper(),
            total_amount=total_amount,
            payment_method=bill_in.payment_method,
            items_snapshot=items_snapshot,
            timestamp=dt.datetime.now(timezone.utc),
            shop_id=shop.id if shop else None,
            customer_id=customer_id
        )
        
        db.add(new_bill)
        await db.commit()
        await db.refresh(new_bill)
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create bill. Please try again.")

    # 3. Trigger Print Task

    bill_data = {
        "bill_number": new_bill.bill_number,
        "items_snapshot": items_snapshot,
        "total_amount": total_amount,
        "date": new_bill.timestamp.strftime("%Y-%m-%d %H:%M:%S")
    }

    background_tasks.add_task(print_bill_bg, bill_data, shop_data, printer_ip)

    return {
        "status": "success",
        "bill_number": new_bill.bill_number,
        "bill_id": new_bill.id,
        "total": total_amount,
        "message": "Bill created and printing"
    }


@router.get("/customer/search")
async def search_customer(
    phone: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_feature("customer_management"))
):
    """Search for customer by phone number in current shop."""
    if not current_user.shop_id:
        return JSONResponse({"found": False})
    
    phone_clean = re.sub(r'\D', '', phone)
    if not phone_clean or len(phone_clean) < 3:
        return JSONResponse({"found": False})
    
    result = await db.execute(
        select(Customer).where(
            Customer.shop_id == current_user.shop_id,
            Customer.phone_number.like(f"%{phone_clean}%")
        )
    )
    customer = result.scalars().first()
    
    if customer:
        return JSONResponse({
            "found": True,
            "id": customer.id,
            "name": customer.name,
            "phone_number": customer.phone_number
        })
    else:
        return JSONResponse({"found": False})


@router.get("/bill/{bill_id}", response_class=HTMLResponse)
async def view_bill(
    request: Request,
    bill_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_current_user)
):
    """Display bill details in a printable HTML format."""
    
    # Fetch bill with customer relationship
    query = select(Bill).where(Bill.id == bill_id).options(selectinload(Bill.customer))
    
    # If user is logged in and not superadmin, restrict to their shop
    if current_user and current_user.role != "superadmin" and current_user.shop_id:
        query = query.where(Bill.shop_id == current_user.shop_id)
    
    result = await db.execute(query)
    bill = result.scalars().first()
    
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    
    # Fetch shop details
    shop = None
    if bill.shop_id:
        shop_res = await db.execute(select(Shop).where(Shop.id == bill.shop_id))
        shop = shop_res.scalars().first()
    
    if not shop:
        # Default shop details if not found
        shop = type('obj', (object,), {
            'name': 'Burger Shop',
            'address': '',
            'currency_symbol': '₹',
            'logo_url': None
        })()
    
    return templates.TemplateResponse(request, "bill_detail.html", {
        "bill": bill,
        "shop": shop,
        "user": current_user
    })

