import datetime as dt
from datetime import timezone
import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.shared.models import User, Shop, ShopInvoice, NotificationLog
from app.domains.auth.services import require_superadmin
from app.shared.schemas import PaginatedShopInvoiceResponse, ShopInvoiceResponse
from pydantic import BaseModel


router = APIRouter(prefix="/api/platform-billing", tags=["Platform Billing"])

@router.get("/invoices", response_model=PaginatedShopInvoiceResponse)
async def list_invoices(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    shop_name: Optional[str] = Query(None),
    client_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    query = select(ShopInvoice).options(selectinload(ShopInvoice.shop), selectinload(ShopInvoice.subscription))
    
    conditions = []
    if status:
        conditions.append(ShopInvoice.status.ilike(status))
    if shop_name:
        conditions.append(ShopInvoice.shop.has(Shop.name.ilike(f"%{shop_name}%")))
    if client_name:
        # Assuming client_name implies searching by the owner's username
        conditions.append(ShopInvoice.shop.has(Shop.users.any(User.username.ilike(f"%{client_name}%"))))
        
    if conditions:
        query = query.where(and_(*conditions))
        
    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_res = await db.execute(count_query)
    total = total_res.scalar() or 0
    
    # Apply pagination
    query = query.order_by(ShopInvoice.created_at.desc()).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    invoices = result.scalars().all()
    
    items = []
    for inv in invoices:
        items.append(inv.to_dict())
        
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "pages": math.ceil(total / size) if size else 0
    }

@router.get("/invoices/{invoice_id}", response_model=ShopInvoiceResponse)
async def get_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    result = await db.execute(select(ShopInvoice).options(selectinload(ShopInvoice.shop), selectinload(ShopInvoice.subscription)).where(ShopInvoice.id == invoice_id))
    invoice = result.scalars().first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice.to_dict()

class ShopBillingStatus(BaseModel):
    shop_id: int
    shop_name: str
    client_name: str
    subscription_name: Optional[str]
    price: float
    billing_cycle: str
    next_billing_date: Optional[str]
    payment_status: str
    pending_invoice_id: Optional[int]
    contact: Optional[str]

class PaginatedShopBillingStatus(BaseModel):
    items: list[ShopBillingStatus]
    total: int
    page: int
    size: int
    pages: int

@router.get("/shops-status", response_model=PaginatedShopBillingStatus)
async def list_shops_billing_status(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    query = select(Shop).options(selectinload(Shop.subscription), selectinload(Shop.users))
    
    if search:
        query = query.where(or_(
            Shop.name.ilike(f"%{search}%"),
            Shop.users.any(User.username.ilike(f"%{search}%"))
        ))
        
    count_query = select(func.count()).select_from(query.subquery())
    total_res = await db.execute(count_query)
    total = total_res.scalar() or 0
    
    query = query.order_by(Shop.name.asc()).offset((page - 1) * size).limit(size)
    shops_res = await db.execute(query)
    shops = shops_res.scalars().all()
    
    # Batch fetch pending invoices
    shop_ids = [s.id for s in shops]
    pending_invoices = {}
    if shop_ids:
        inv_query = select(ShopInvoice).where(ShopInvoice.shop_id.in_(shop_ids), ShopInvoice.status != 'Paid').order_by(ShopInvoice.created_at.desc())
        inv_res = await db.execute(inv_query)
        for inv in inv_res.scalars().all():
            if inv.shop_id not in pending_invoices:
                pending_invoices[inv.shop_id] = inv
                
    items = []
    for shop in shops:
        client_name = shop.users[0].username if shop.users else "Unknown"
        pending_inv = pending_invoices.get(shop.id)
        
        if pending_inv:
            status = pending_inv.status
            pending_inv_id = pending_inv.id
        else:
            status = "Up to date"
            pending_inv_id = None
            
        items.append({
            "shop_id": shop.id,
            "shop_name": shop.name,
            "client_name": client_name,
            "subscription_name": shop.subscription.name if shop.subscription else None,
            "price": float(shop.subscription.price) if shop.subscription else 0.0,
            "billing_cycle": shop.subscription.billing_cycle if shop.subscription else "monthly",
            "next_billing_date": shop.next_billing_date.isoformat() if shop.next_billing_date else None,
            "payment_status": status,
            "pending_invoice_id": pending_inv_id,
            "contact": shop.contact
        })
        
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "pages": math.ceil(total / size) if size else 0
    }

@router.post("/invoices/{invoice_id}/pay")
async def mark_invoice_paid(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    result = await db.execute(select(ShopInvoice).where(ShopInvoice.id == invoice_id))
    invoice = result.scalars().first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
        
    if invoice.status == "Paid":
        raise HTTPException(status_code=400, detail="Invoice already paid")
        
    invoice.status = "Paid"
    invoice.paid_at = dt.datetime.now(timezone.utc)
    await db.commit()
    
    return {"message": "Invoice marked as paid"}

@router.post("/invoices/{invoice_id}/notify")
async def send_invoice_notification(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    result = await db.execute(select(ShopInvoice).options(selectinload(ShopInvoice.shop)).where(ShopInvoice.id == invoice_id))
    invoice = result.scalars().first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
        
    # Build WhatsApp message text
    msg = f"Hello from BurgerPOS Platform.\\nYour invoice {invoice.invoice_number} for amount ₹{invoice.amount} is {invoice.status}. Due date: {invoice.due_date.strftime('%Y-%m-%d')}."
    
    # Log to NotificationLog
    log = NotificationLog(
        shop_id=invoice.shop_id,
        channel="whatsapp",
        message=msg,
        status="sent" # stubbed
    )
    db.add(log)
    await db.commit()
    
    # Return a wa.me link that the frontend can optionally open
    # We would use shop.contact or owner's phone number. Shop has 'contact' field.
    phone = invoice.shop.contact if invoice.shop and invoice.shop.contact else ""
    phone_clean = ''.join(filter(str.isdigit, phone))
    
    if not phone_clean:
        raise HTTPException(status_code=400, detail="No contact number available for this shop. Please update the shop's contact details first.")
    
    whatsapp_url = f"https://wa.me/{phone_clean}?text={msg}"
    
    return {"message": "Notification logged", "whatsapp_url": whatsapp_url}
