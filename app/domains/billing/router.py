from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date
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
from app.shared.models import Bill, MenuItem, Shop, User, Customer, Subscription, PlanFeature, StockMovement, DEFAULT_LOW_STOCK_THRESHOLD
from app.domains.features.service import FeatureService
from app.core.redis import get_redis
from app.infrastructure.integrations.printer import print_bill_bg
from app.infrastructure.integrations.notifications import get_notification_service
from app.domains.auth.router import get_current_user
from app.shared.schemas import CartItem, BillCreate, BillActionResponse, CustomerSearchResponse, OrderStatusUpdate
from app.core.dependencies.csrf import verify_csrf
from app.core.dependencies.features import require_feature, require_active_shop
from app.core.config import settings
from app.shared.time_utils import utc_iso, shop_local

router = APIRouter(prefix="/billing", tags=["Billing"], dependencies=[Depends(verify_csrf)])
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "frontend" / "templates"))
# Timestamps are stored in UTC. These filters let templates print them safely:
# utc_iso   -> raw UTC string with an explicit offset (for JS that does its own conversion)
# shop_local -> converted to the shop's configured timezone (Shop.timezone), for display
templates.env.filters["utc_iso"] = utc_iso
templates.env.filters["shop_local"] = shop_local

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

    # Country code for the phone-number picker on this page: prefer the shop's own
    # saved country code, otherwise fall back to the app-wide default. Resolved
    # here on the server so the template/JS never has to hardcode a country.
    default_country_code = (shop.country_code if shop and shop.country_code else settings.DEFAULT_COUNTRY_CODE)

    return templates.TemplateResponse(request, "pos.html", {
        "items": items,
        "currency": currency,
        "shop": shop,
        "default_country_code": default_country_code,
        "all_shops": all_shops,
        "is_superadmin": current_user.role == "superadmin",
        "user": current_user,
        "features": shop_features,
        "error": error_msg
    })


@router.post("/create", response_model=BillActionResponse)
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
        "message": result["message"],
        "updated_items": result.get("updated_items", []),
        "warning": result.get("warning"),
    }

@router.get("/recent-bills", response_class=JSONResponse)
async def get_recent_bills(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Fetch recent bills (Held and Completed, excluding Cancelled) for the POS UI"""
    if not current_user.shop_id:
        return JSONResponse({"bills": []})

    shop_res = await db.execute(select(Shop).where(Shop.id == current_user.shop_id))
    shop = shop_res.scalars().first()

    # Get last 20 bills for this shop (exclude cancelled bills)
    res = await db.execute(
        select(Bill).options(selectinload(Bill.customer))
        .where(
            Bill.shop_id == current_user.shop_id,
            Bill.status != "Cancelled"
        )
        .order_by(Bill.timestamp.desc())
        .limit(20)
    )
    bills = res.scalars().all()

    return JSONResponse({
        "bills": [
            {
                "id": b.slug,
                "bill_number": b.bill_number,
                "status": getattr(b, "status", "Completed"),
                "delivery_charge": float(b.delivery_charge) if b.delivery_charge is not None else 0.0,
                "total_amount": float(b.total_amount),
                "timestamp": b.timestamp.isoformat() if b.timestamp else None,
                # Pre-formatted in the shop's local timezone so the POS UI can
                # display it directly, without doing its own timezone math in JS.
                "timestamp_display": shop_local(b.timestamp, shop.timezone if shop else "UTC").strftime("%d %b %Y, %I:%M %p") if b.timestamp else None,
                "items": b.items_snapshot,
                "payment_method": getattr(b, "payment_method", "Cash"),
                "customer_name": b.customer.name if b.customer else None,
                "customer_phone": b.customer.phone_number if b.customer else None
            }
            for b in bills
        ]
    })


@router.get("/orders", response_class=JSONResponse, dependencies=[Depends(require_active_shop)])
async def list_orders(
    date_from: Optional[dt.date] = None,
    date_to: Optional[dt.date] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    payment_status: Optional[str] = None,
    delivery_status: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pay Later orders for this shop — used by both the POS Orders shortcut
    (small default limit, no filters) and the Dashboard Orders tab (full
    filters + pagination, same pattern as credit/router.py's
    list_customer_bills).

    Deliberately NOT gated behind require_feature("credit_billing") — creating
    a new Credit-method bill IS gated (see create_bill/update_bill), but a shop
    that later loses the feature must still be able to view and fulfill Pay
    Later orders it already committed to. Gating this list/update pair would
    permanently strand any bill left "awaiting delivery/payment" at the moment
    the feature is disabled. Still gated behind require_active_shop — a
    deactivated/suspended shop must not be able to touch its bills at all."""
    if not current_user.shop_id:
        return JSONResponse({"orders": [], "total": 0})

    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    conditions = [
        Bill.shop_id == current_user.shop_id,
        Bill.payment_method == "Credit",
        Bill.status == "Completed",
    ]
    if date_from:
        conditions.append(cast(Bill.timestamp, Date) >= date_from)
    if date_to:
        conditions.append(cast(Bill.timestamp, Date) <= date_to)
    if min_amount is not None:
        conditions.append(Bill.total_amount >= min_amount)
    if max_amount is not None:
        conditions.append(Bill.total_amount <= max_amount)
    if payment_status:
        conditions.append(Bill.payment_status == payment_status)
    if delivery_status:
        conditions.append(Bill.delivery_status == delivery_status)

    result = await db.execute(
        select(Bill, func.count().over().label("total_count"))
        .options(selectinload(Bill.customer))
        .where(*conditions)
        .order_by(Bill.timestamp.desc())
        .limit(limit).offset(offset)
    )
    rows = result.all()
    total = rows[0][1] if rows else 0

    orders = []
    for bill, _ in rows:
        order = bill.to_dict()
        order["customer_name"] = bill.customer.name if bill.customer else None
        order["customer_phone"] = bill.customer.phone_number if bill.customer else None
        order["is_credit_customer"] = bool(bill.customer.is_credit_customer) if bill.customer else False
        orders.append(order)

    return JSONResponse({"status": "success", "orders": orders, "total": total})


@router.post("/orders/{bill_slug}/update", response_class=JSONResponse, dependencies=[Depends(require_active_shop)])
async def update_order_status(
    bill_slug: str,
    data: OrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delivery-status and/or payment-status update for a Pay Later order,
    including manual correction of an accidental status change.
    payment_status/mark_paid are only accepted for non-credit customers — an
    actual is_credit_customer bill must be paid via POST /admin/credit/payments
    so the CreditPayment audit trail and credit_balance stay correct."""
    result = await db.execute(
        select(Bill).options(selectinload(Bill.customer))
        .where(
            Bill.slug == bill_slug,
            Bill.shop_id == current_user.shop_id,
            Bill.payment_method == "Credit",
            Bill.status == "Completed",
        )
    )
    bill = result.scalars().first()
    if not bill:
        raise HTTPException(status_code=404, detail="Order not found")

    if data.delivery_status is not None:
        bill.delivery_status = data.delivery_status

    if data.payment_status is not None or data.mark_paid:
        if bill.customer and bill.customer.is_credit_customer:
            raise HTTPException(
                status_code=400,
                detail="This bill belongs to a credit account — record the payment from the Credit Book instead.",
            )
        new_status = data.payment_status or "Paid"
        bill.payment_status = new_status
        if new_status == "Paid":
            bill.amount_paid = bill.total_amount
        elif new_status == "Unpaid":
            bill.amount_paid = Decimal("0.00")

    await db.commit()
    await db.refresh(bill)
    return JSONResponse({"status": "success", "order": bill.to_dict()})

@router.post("/update/{bill_slug}", response_model=BillActionResponse)
async def update_bill(
    bill_slug: str,
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
    
    result = await billing_svc.update_bill(bill_slug, bill_in, current_user)
    
    if bill_in.status == "Completed":
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
        "message": result["message"],
        "updated_items": result.get("updated_items", []),
        "warning": result.get("warning"),
    }


@router.post("/cancel/{bill_slug}", response_model=BillActionResponse)
async def cancel_bill(
    bill_slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    try:
        current_user = await get_current_user(request, db)
    except Exception:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    from app.domains.billing.service import BillingService
    billing_svc = BillingService(db)
    
    result = await billing_svc.cancel_bill(bill_slug, current_user)
    
    return {
        "status": result["status"],
        "message": result["message"],
        "updated_items": result.get("updated_items", []),
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

    threshold = item.low_stock_threshold or DEFAULT_LOW_STOCK_THRESHOLD
    item_dict = item.to_dict()
    available = item_dict.get("available_stock")

    return JSONResponse({
        **item_dict,
        "is_tracked": item.stock_quantity is not None,
        "is_low_stock": available is not None and available <= threshold,
        "is_out_of_stock": available is not None and available <= 0,
    })


@router.get("/customer/search", response_model=CustomerSearchResponse)
async def search_customer(
    query: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_feature("customer_management"))
):
    """Search for customer by phone number or name in current shop."""
    if not current_user.shop_id:
        return CustomerSearchResponse(found=False)
    
    query_clean = query.strip()
    if not query_clean or len(query_clean) < 3:
        return CustomerSearchResponse(found=False)
    
    from sqlalchemy import or_
    phone_clean = re.sub(r'\D', '', query_clean)
    conditions = [Customer.name.ilike(f"%{query_clean}%")]
    if phone_clean:
        conditions.append(Customer.phone_number.like(f"%{phone_clean}%"))
    
    result = await db.execute(
        select(Customer).where(
            Customer.shop_id == current_user.shop_id,
            or_(*conditions)
        )
    )
    customer = result.scalars().first()
    
    if customer:
        return CustomerSearchResponse(
            found=True,
            id=customer.id,
            name=customer.name,
            phone_number=customer.phone_number,
            company_name=customer.company_name,
            gst_number=customer.gst_number,
            is_credit_customer=customer.is_credit_customer,
            credit_limit=float(customer.credit_limit) if customer.credit_limit else 0.0,
            credit_balance=float(customer.credit_balance) if customer.credit_balance else 0.0,
            payment_term_type=customer.payment_term_type,
            payment_term_value=customer.payment_term_value
        )
    else:
        return CustomerSearchResponse(found=False)


@router.get("/bill/{bill_id}", response_class=HTMLResponse)
async def view_bill(
    request: Request,
    bill_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Display bill details in a printable HTML format. Staff-only — requires login."""

    # Fetch bill with customer relationship
    query = select(Bill).where(Bill.id == bill_id).options(selectinload(Bill.customer))

    # Non-superadmins are restricted to their own shop's bills
    if current_user.role != "superadmin" and current_user.shop_id:
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
        "user": current_user,
    })

