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
from decimal import Decimal, ROUND_HALF_UP
import json
import re

from app.core.database import get_db
from app.shared.models import Bill, MenuItem, Shop, User, Customer, Subscription, PlanFeature, StockMovement
from app.domains.features.service import FeatureService
from app.core.redis import get_redis
from app.infrastructure.integrations.printer import print_bill_bg
from app.infrastructure.integrations.notifications import get_notification_service
from app.domains.auth.router import get_current_user, get_optional_current_user
from app.shared.schemas import CartItem, BillCreate
from app.core.dependencies.csrf import verify_csrf
from app.core.dependencies.features import require_feature

router = APIRouter(prefix="/billing", tags=["Billing"], dependencies=[Depends(verify_csrf)])
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "frontend" / "templates"))

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
    shop_slug: Optional[str] = None
):
    """
    Serves the POS UI with Menu Items loaded for the user's shop.
    Unified logic for Superadmins (can select any shop) and regular users (fixed to their shop).
    """
    from sqlalchemy.orm import selectinload
    
    all_shops = []
    target_shop_id = current_user.shop_id
    
    # 1. Access Control & Shop Selection
    if shop_slug:
        shop_res = await db.execute(select(Shop).where(Shop.slug == shop_slug).options(selectinload(Shop.subscription)))
        requested_shop = shop_res.scalars().first()
        if requested_shop:
            target_shop_id = requested_shop.id
            if current_user.role != "superadmin" and target_shop_id != current_user.shop_id:
                target_shop_id = current_user.shop_id # Fallback to own shop if unauthorized

    if current_user.role == "superadmin":
        shops_res = await db.execute(
            select(Shop).where(Shop.is_active == True).options(selectinload(Shop.subscription))
        )
        all_shops = shops_res.scalars().all()
        if not target_shop_id and all_shops:
            target_shop_id = all_shops[0].id

    # 2. Fetch Shop with Subscription (Unified)
    shop = None
    if target_shop_id:
        if shop_slug and locals().get("requested_shop") and requested_shop.id == target_shop_id:
            shop = requested_shop
        else:
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
        items = [item.to_dict() for item in items_res.scalars().all()]
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
    try:
        current_user = await get_current_user(request, db)
    except Exception:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    from app.domains.billing.service import BillingService
    billing_svc = BillingService(db)
    
    # Delegate logic to service layer
    result = await billing_svc.create_bill(bill_in, current_user)
    
    # Trigger background print
    background_tasks.add_task(
        print_bill_bg, 
        result["bill_data"], 
        result["shop_data"], 
        result["printer_ip"]
    )
    
    return {
        "status": result["status"],
        "bill_number": result["bill_number"],
        "bill_id": result["bill_id"],
        "total": result["total"],
        "message": result["message"]
    }


@router.get("/item-by-sku/{sku}", response_class=JSONResponse)
async def get_item_by_sku_for_pos(
    sku: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Scanner endpoint for POS screen.
    Returns item details so Alpine.js can add it to the cart.
    Always scoped to the requesting user's shop.
    """
    if not current_user.shop_id:
        raise HTTPException(status_code=403, detail="No shop assigned")

    result = await db.execute(
        select(MenuItem).where(
            MenuItem.shop_id == current_user.shop_id,
            MenuItem.sku == sku.upper().strip(),
            MenuItem.is_active == True,
        )
    )
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail=f"No item found with SKU '{sku}'")

    threshold = item.low_stock_threshold or 5.0
    return JSONResponse({
        **item.to_dict(),
        "is_tracked": item.stock_quantity is not None,
        "is_low_stock": item.stock_quantity is not None and item.stock_quantity <= threshold,
        "is_out_of_stock": item.stock_quantity is not None and item.stock_quantity <= 0,
    })


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

