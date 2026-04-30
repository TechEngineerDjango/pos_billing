from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.encoders import jsonable_encoder

from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional
import uuid
import datetime
import json
import re

from database.session import get_db
from database.models import Bill, MenuItem, Shop, User, Customer
from services.printer import print_bill_bg
from routers.auth import get_current_user, get_optional_current_user

router = APIRouter(prefix="/billing", tags=["Billing"])
templates = Jinja2Templates(directory="templates")

def json_serializer(obj):
    if isinstance(obj, list):
        return json.dumps([i.to_dict() if hasattr(i, "to_dict") else jsonable_encoder(i) for i in obj])
    if hasattr(obj, "to_dict"):
        return json.dumps(obj.to_dict())
    return json.dumps(jsonable_encoder(obj))

templates.env.filters["tojson"] = json_serializer


class CartItem(BaseModel):
    id: int
    qty: int

class BillCreate(BaseModel):
    items: List[CartItem]
    payment_method: str = "Cash"
    customer_phone: Optional[str] = None
    customer_name: Optional[str] = None


@router.get("/", response_class=HTMLResponse)
async def pos_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    shop_id: int = None
):
    """
    Serves the POS UI with Menu Items loaded for the user's shop.
    Superadmin can select any shop via shop_id query parameter.
    """
    from sqlalchemy.orm import selectinload
    from features import get_shop_features, has_feature
    
    # Authorization checks continue based on current_user
    
    shop = None
    all_shops = []  # For superadmin dropdown
    
    # Superadmin: can select any shop
    if current_user and current_user.role == "superadmin":
        # Fetch all shops for dropdown with subscription loaded
        shops_res = await db.execute(
            select(Shop).where(Shop.is_active == True).options(selectinload(Shop.subscription))
        )
        all_shops = shops_res.scalars().all()
        
        # If shop_id provided, use that shop
        if shop_id:
            shop_res = await db.execute(
                select(Shop).where(Shop.id == shop_id).options(selectinload(Shop.subscription))
            )
            shop = shop_res.scalars().first()
        elif all_shops:
            # Default to first shop if none selected
            shop = all_shops[0]
    elif current_user:
        # Regular user: use their assigned shop
        if current_user.shop_id:
            shop_res = await db.execute(
                select(Shop).where(Shop.id == current_user.shop_id).options(selectinload(Shop.subscription))
            )
            shop = shop_res.scalars().first()
    else:
        # Preview Mode (No User): Load default shop for visualization
        shop_res = await db.execute(select(Shop).options(selectinload(Shop.subscription)).limit(1))
        shop = shop_res.scalars().first()
    
    # Get shop features
    shop_features = get_shop_features(shop) if shop else {}
    
    if not shop:
        return templates.TemplateResponse(request, "pos.html", {
            "items": [],
            "currency": "₹",
            "shop": None,
            "all_shops": all_shops,
            "is_superadmin": current_user.role == "superadmin" if current_user else False,
            "user": current_user,
            "features": shop_features,
            "error": "No shop available. Please contact admin."
        })
    
    # Fetch Active Menu Items for this shop
    result = await db.execute(
        select(MenuItem).where(
            MenuItem.is_active == True,
            MenuItem.shop_id == shop.id
        )
    )
    items = result.scalars().all()
    
    currency = shop.currency_symbol if shop else "₹"
    
    return templates.TemplateResponse(request, "pos.html", {
        "items": items,
        "currency": currency,
        "shop": shop,
        "all_shops": all_shops,
        "is_superadmin": current_user.role == "superadmin" if current_user else False,
        "user": current_user,
        "features": shop_features
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
    from routers.auth import get_current_user
    try:
        current_user = await get_current_user(request, db)
    except:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not bill_in.items:
        raise HTTPException(status_code=400, detail="Cart is empty")
    
    # Get user's shop
    shop = None
    if current_user.shop_id:
        shop_res = await db.execute(select(Shop).where(Shop.id == current_user.shop_id))
        shop = shop_res.scalars().first()

    total_amount = 0.0
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
            
        line_total = menu_item.price * cart_item.qty
        total_amount += line_total
        
        items_snapshot.append({
            "id": menu_item.id,
            "name": menu_item.name,
            "category": menu_item.category,
            "price": menu_item.price,
            "qty": cart_item.qty,
            "line_total": line_total
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
    new_bill = Bill(
        bill_number=str(uuid.uuid4())[:8].upper(),
        total_amount=total_amount,
        payment_method=bill_in.payment_method,
        items_snapshot=items_snapshot,
        timestamp=datetime.datetime.utcnow(),
        shop_id=shop.id if shop else None,
        customer_id=customer_id
    )
    
    db.add(new_bill)
    await db.commit()
    await db.refresh(new_bill)

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
    current_user: User = Depends(get_current_user)
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
    from sqlalchemy.orm import selectinload
    
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

