import html as html_mod
import re
import urllib.parse
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, cast, String, func, and_, or_

from app.core.database import get_db
from app.shared.models import User, MenuItem, Bill, Shop, Customer, DEFAULT_LOW_STOCK_THRESHOLD
from app.domains.auth.router import get_current_user, get_optional_current_user
from app.shared.schemas import UserCreate, MenuItemCreate, CustomerCreate, ShopCreate
from app.infrastructure.integrations.image import save_uploaded_image
from app.domains.auth.services import require_owner_or_above, require_any_staff
from app.core.dependencies.csrf import verify_csrf
from app.domains.inventory.sku import generate_sku
from app.shared.time_utils import utc_iso, shop_local
from app.shared.timezones import TIMEZONE_CHOICES
from app.domains.expenses.service import CATEGORIES as EXPENSE_CATEGORIES
from app.domains.billing.profit_loss import compute_profit_loss

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(verify_csrf)])
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "frontend" / "templates"))
templates.env.filters["utc_iso"] = utc_iso
templates.env.filters["shop_local"] = shop_local


@router.get("/")
async def admin_overview(
    request: Request,
    shop_slug: Optional[str] = None,
    period: Optional[str] = "7d",  # 7d, 1m, 6m, 1y
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy.orm import selectinload
    from app.domains.features.service import FeatureService

    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    
    # Get target shop ID
    target_shop_id = current_user.shop_id
    
    if shop_slug:
        shop_res = await db.execute(select(Shop).where(Shop.slug == shop_slug).options(selectinload(Shop.subscription)))
        requested_shop = shop_res.scalars().first()
        if not requested_shop:
            raise HTTPException(status_code=404, detail="Shop not found")
        target_shop_id = requested_shop.id
        
        # IDOR protection
        if current_user.role == "owner" and target_shop_id != current_user.shop_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: You do not own this shop")
            
    # If superadmin and no shop_slug provided, default to first active shop
    if current_user.role == "superadmin" and not target_shop_id:
        shop_res = await db.execute(select(Shop).where(Shop.is_active == True).limit(1).options(selectinload(Shop.subscription)))
        first_shop = shop_res.scalars().first()
        target_shop_id = getattr(first_shop, "id", None)

    # Get user's shop
    shop = None
    menu_items = []
    customers = []
    bills = []
    staff = []
    stats = {"total_sales": 0.0, "customer_count": 0, "order_count": 0, "menu_count": 0, "staff_count": 0}
    analytics = {}
    
    if target_shop_id:
        if shop_slug and requested_shop:
            shop = requested_shop
        else:
            shop_res = await db.execute(
                select(Shop).where(Shop.id == target_shop_id).options(selectinload(Shop.subscription))
            )
            shop = shop_res.scalars().first()
        
        # Fetch Data for Dashboard Tabs
        if shop:
            # Menu Items
            menu_res = await db.execute(select(MenuItem).where(MenuItem.shop_id == shop.id))
            menu_items = menu_res.scalars().all()
            stats["menu_count"] = len(menu_items)
            
            # Customers
            cust_res = await db.execute(select(Customer).where(Customer.shop_id == shop.id))
            customers = cust_res.scalars().all()
            stats["customer_count"] = len(customers)
            
            # Bills for Reports — Completed only. "Held" (parked/draft carts,
            # never paid — only Held bills can even be cancelled, see
            # billing/service.py's cancel_bill) is a distinct status from
            # Cancelled and was previously leaking into every revenue figure
            # below via a `!= "Cancelled"` filter. Matches the status filter
            # already used for tax reporting in reports/strategies/gst_tax.py.
            bills_res = await db.execute(
                select(Bill).where(
                    Bill.shop_id == shop.id,
                    Bill.status == "Completed"
                ).order_by(Bill.timestamp.desc())
            )
            bills = bills_res.scalars().all()
            stats["order_count"] = len(bills)
            stats["total_sales"] = sum(float(b.total_amount) for b in bills if b.total_amount is not None)

            # Staff
            staff_res = await db.execute(select(User).where(User.shop_id == shop.id, User.role == "cashier"))
            staff = staff_res.scalars().all()
            stats["staff_count"] = len(staff)
            
            # ============== ADVANCED ANALYTICS WITH PERIOD FILTER ==============
            from datetime import datetime, timedelta, timezone
            from collections import defaultdict
            
            now = datetime.now(timezone.utc)
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Define period ranges
            period_configs = {
                "7d": {"days": 7, "prev_days": 7, "label": "Last 7 Days", "chart_days": 7},
                "1m": {"days": 30, "prev_days": 30, "label": "Last 30 Days", "chart_days": 30},
                "6m": {"days": 180, "prev_days": 180, "label": "Last 6 Months", "chart_days": 180},
                "1y": {"days": 365, "prev_days": 365, "label": "Last Year", "chart_days": 365},
            }
            
            config = period_configs.get(period, period_configs["7d"])
            period_start = today - timedelta(days=config["days"])
            prev_period_start = period_start - timedelta(days=config["prev_days"])
            
            # For chart labels - use appropriate granularity
            if config["days"] <= 7:
                # Daily labels for 7 days
                chart_labels = [(today - timedelta(days=i)).strftime("%a") for i in range(config["days"]-1, -1, -1)]
                date_format = "%a"
                group_by = "day"
            elif config["days"] <= 30:
                # Daily labels for month
                chart_labels = [(today - timedelta(days=i)).strftime("%d %b") for i in range(config["days"]-1, -1, -1)]
                date_format = "%d %b"
                group_by = "day"
            elif config["days"] <= 180:
                # Weekly labels for 6 months
                weeks = config["days"] // 7
                chart_labels = [f"W{i+1}" for i in range(weeks)]
                group_by = "week"
            else:
                # Monthly labels for year
                chart_labels = [(today - timedelta(days=30*i)).strftime("%b") for i in range(11, -1, -1)]
                group_by = "month"
            
            # Initialize data structures
            period_revenue = defaultdict(float)
            period_orders = defaultdict(int)
            hourly_sales = defaultdict(float)
            item_sales = defaultdict(lambda: {"qty": 0, "revenue": 0.0})
            payment_breakdown = defaultdict(lambda: {"count": 0, "total": 0.0})
            
            current_period_total = 0.0
            prev_period_total = 0.0
            today_total = 0.0
            today_orders = 0
            period_orders_count = 0
            period_net_revenue = 0.0
            period_uncollected = 0.0
            
            for bill in bills:
                bill_date = bill.timestamp
                if not bill_date:
                    continue
                
                # Ensure bill_date is aware (asyncpg can return naive datetimes for TIMESTAMPTZ columns)
                if bill_date.tzinfo is None:
                    bill_date = bill_date.replace(tzinfo=timezone.utc)
                
                # Today's stats
                if bill_date >= today:
                    today_total += float(bill.total_amount)
                    today_orders += 1
                
                # Current period
                if bill_date >= period_start:
                    current_period_total += float(bill.total_amount)
                    period_orders_count += 1
                    period_net_revenue += float(bill.total_amount - (bill.tax_amount or 0))
                    if bill.payment_status != "Paid":
                        period_uncollected += float(bill.total_amount - (bill.amount_paid or 0))

                    # Group by appropriate granularity
                    if group_by == "day":
                        key = bill_date.strftime(date_format)
                    elif group_by == "week":
                        week_num = (bill_date - period_start).days // 7
                        key = f"W{week_num + 1}"
                    else:  # month
                        key = bill_date.strftime("%b")
                    
                    period_revenue[key] += float(bill.total_amount)
                    period_orders[key] += 1
                    
                    # Hourly distribution (only current period)
                    hourly_sales[bill_date.hour] += float(bill.total_amount)
                    
                    # Top selling items (only current period)
                    if bill.items_snapshot:
                        for item in bill.items_snapshot:
                            name = item.get("name", "Unknown")
                            qty = item.get("qty", 1)
                            line_total = item.get("line_total", 0)
                            item_sales[name]["qty"] += qty
                            item_sales[name]["revenue"] += float(line_total)
                    
                    # Payment breakdown (only current period)
                    method = bill.payment_method or "Cash"
                    payment_breakdown[method]["count"] += 1
                    payment_breakdown[method]["total"] += float(bill.total_amount)
                
                # Previous period (for comparison)
                elif prev_period_start <= bill_date < period_start:
                    prev_period_total += float(bill.total_amount)
            
            # Build chart data
            chart_revenue = [round(period_revenue.get(label, 0), 2) for label in chart_labels]
            chart_orders = [period_orders.get(label, 0) for label in chart_labels]
            
            # Calculate period change
            if prev_period_total > 0:
                period_change = round(((current_period_total - prev_period_total) / prev_period_total * 100), 1)
            else:
                period_change = 0 if current_period_total == 0 else 100
            
            analytics = {
                # Period info
                "period": period,
                "period_label": config["label"],
                
                # Chart data
                "chart_labels": chart_labels[-min(len(chart_labels), 12):],  # Show max 12 labels
                "chart_revenue": chart_revenue[-min(len(chart_revenue), 12):],
                "chart_orders": chart_orders[-min(len(chart_orders), 12):],
                
                # Top 5 selling items
                "top_items": sorted(
                    [{"name": k, **v} for k, v in item_sales.items()],
                    key=lambda x: x["revenue"], reverse=True
                )[:5],
                
                # Peak hours
                "hourly_labels": [f"{h}:00" for h in range(24)],
                "hourly_revenue": [round(hourly_sales[h], 2) for h in range(24)],
                "peak_hour": max(hourly_sales.keys(), key=lambda h: hourly_sales[h]) if hourly_sales else 12,
                
                # Payment breakdown
                "payment_methods": [
                    {"method": k, "count": v["count"], "total": round(v["total"], 2)}
                    for k, v in payment_breakdown.items()
                ],
                
                # Key metrics
                "today_revenue": round(today_total, 2),
                "today_orders": today_orders,
                "current_period": round(current_period_total, 2),
                "prev_period": round(prev_period_total, 2),
                "period_change": period_change,
                "period_orders": period_orders_count,
                
                # Average order value
                "avg_order_value": round(current_period_total / period_orders_count, 2) if period_orders_count > 0 else 0,
            }

            analytics["profit_loss"] = await compute_profit_loss(
                db, shop.id, period_net_revenue, period_uncollected, period_start, today
            )

    # Get shop features based on subscription (Using FeatureService to get M2M features correctly)
    feature_service = FeatureService(db=db)
    shop_features = await feature_service.get_all_features_for_shop(shop) if shop else {}

    # Inventory: low stock count for badge (only if feature enabled)
    low_stock_count = 0
    if shop and shop_features.get("inventory_management"):
        low_res = await db.execute(
            select(MenuItem).where(
                and_(
                    MenuItem.shop_id == shop.id,
                    MenuItem.is_active == True,
                    MenuItem.stock_quantity.isnot(None),
                    MenuItem.stock_quantity <= MenuItem.low_stock_threshold,
                )
            )
        )
        low_stock_count = len(low_res.scalars().all())

    from app.core.config import settings
    default_country_code = (shop.country_code if shop and shop.country_code else settings.DEFAULT_COUNTRY_CODE)

    # Render the new SPA Dashboard
    return templates.TemplateResponse(request, "dashboard.html", {
        "user": current_user,
        "shop": shop,
        "menu_items": menu_items,
        "customers": customers,
        "bills": bills,
        "staff": staff,
        "stats": stats,
        "analytics": analytics,
        "selected_period": period,
        "features": shop_features,
        "active_page": "overview",
        "low_stock_count": low_stock_count,
        "default_country_code": default_country_code,
        "timezone_choices": TIMEZONE_CHOICES,
        "expense_categories": EXPENSE_CATEGORIES,
    })


@router.post("/staff/add")
async def add_staff(
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
    data: UserCreate = Depends(UserCreate.as_form)
):
    from app.domains.auth.router import get_password_hash
    
    target_shop_id = data.shop_id or current_user.shop_id
    if not target_shop_id:
        return RedirectResponse(url="/admin/?tab=staff&error=No shop assigned", status_code=303)
    
    # Check if username exists
    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalars().first():
        # Handle error gracefully - maybe redirect with error
        return RedirectResponse(url=f"/admin/?shop_id={target_shop_id}&tab=staff&error=Username already taken", status_code=303)
    
    new_staff = User(
        username=data.username,
        hashed_password=get_password_hash(data.password),
        role="cashier",
        shop_id=target_shop_id
    )
    db.add(new_staff)
    await db.commit()
    return RedirectResponse(url=f"/admin/?shop_id={target_shop_id}&tab=staff", status_code=303)


@router.post("/staff/delete/{staff_slug}")
async def delete_staff(
    staff_slug: str,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db)
):
    
    target_shop_id = current_user.shop_id
    
    query = select(User).where(User.slug == staff_slug, User.role == "cashier")
    if current_user.role != "superadmin":
        query = query.where(User.shop_id == target_shop_id)
        
    res = await db.execute(query)
    target = res.scalars().first()
    
    if target:
        await db.delete(target)
        await db.commit()
    
    redirect_url = f"/admin/?tab=staff"
    if target_shop_id:
        redirect_url += f"&shop_id={target_shop_id}"
        
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/customers")
async def admin_customers(
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    return RedirectResponse(url="/admin/?tab=customers")


@router.get("/products")
async def admin_products(
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    return RedirectResponse(url="/admin/?tab=menu")


@router.get("/reports")
async def admin_reports(
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    return RedirectResponse(url="/admin/?tab=reports")


@router.get("/branding")
@router.get("/settings")
async def admin_branding(
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    return RedirectResponse(url="/admin/?tab=branding")


@router.get("/admin/menu/search")
@router.get("/menu/search")
async def search_menu_items(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    active_only: bool = True,
    stock_status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Type-ahead menu item search — backs the Rate Cards item picker
    (active_only=true, minimal fields) and the Menu/Inventory tabs'
    search-driven lists (active_only=false, full fields), same scalability
    treatment as customer search: capped, paginated, server-side, no
    full-table embed. stock_status (untracked/out/low/ok) mirrors the badge
    logic already shown per row in the Inventory tab."""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    conditions = [MenuItem.shop_id == current_user.shop_id]
    if active_only:
        conditions.append(MenuItem.is_active == True)
    query_clean = q.strip()
    if query_clean:
        conditions.append(or_(MenuItem.name.ilike(f"%{query_clean}%"), MenuItem.sku.ilike(f"%{query_clean}%")))
    if stock_status:
        threshold = func.coalesce(MenuItem.low_stock_threshold, DEFAULT_LOW_STOCK_THRESHOLD)
        if stock_status == "untracked":
            conditions.append(MenuItem.stock_quantity.is_(None))
        elif stock_status == "out":
            conditions.append(and_(MenuItem.stock_quantity.isnot(None), MenuItem.stock_quantity <= 0))
        elif stock_status == "low":
            conditions.append(and_(
                MenuItem.stock_quantity.isnot(None), MenuItem.stock_quantity > 0, MenuItem.stock_quantity <= threshold,
            ))
        elif stock_status == "ok":
            conditions.append(and_(MenuItem.stock_quantity.isnot(None), MenuItem.stock_quantity > threshold))

    # Single round trip: count(*) over() computes the total matching-row
    # count as a window function on every returned row, instead of a
    # separate COUNT(*) query. Note: if offset skips past every matching
    # row, this returns zero rows and total falls back to 0 rather than the
    # true count — acceptable here since total only drives "Load More"
    # visibility, and the client never requests an offset beyond what it
    # already knows total to be from an earlier page.
    result = await db.execute(
        select(MenuItem, func.count().over().label("total_count"))
        .where(*conditions).order_by(MenuItem.name).limit(limit).offset(offset)
    )
    rows = result.all()
    items = [row[0] for row in rows]
    total = rows[0][1] if rows else 0
    return {
        "status": "success",
        "total": total,
        "items": [
            {
                "id": m.id,
                "slug": m.slug,
                "name": m.name,
                "price": float(m.price),
                "unit": m.unit,
                "category": m.category,
                "sku": m.sku,
                "image_url": m.image_url,
                "is_active": m.is_active,
                "stock_quantity": m.stock_quantity,
                "low_stock_threshold": m.low_stock_threshold,
            }
            for m in items
        ],
    }


# Simple Action to Add Menu Item (Form Post)
@router.post("/admin/menu/add")
@router.post("/menu/add")
async def add_menu_item(
    image: UploadFile = File(None),
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
    data: MenuItemCreate = Depends(MenuItemCreate.as_form)
):
    # Use target shop ID
    target_shop_id = data.shop_id or current_user.shop_id
    if not target_shop_id:
        raise HTTPException(status_code=400, detail="No shop assigned")

    image_url = await save_uploaded_image(image, max_size=(300, 300), prefix="menu")

    # Auto-generate SKU for new item
    shop_res = await db.execute(select(Shop).where(Shop.id == target_shop_id))
    shop = shop_res.scalars().first()
    shop_name = shop.name if shop else "SHOP"
    if data.sku and data.sku.strip():
        final_sku = data.sku.upper().strip()
    else:
        final_sku = await generate_sku(
            shop_name=shop_name,
            category=data.category,
            shop_id=target_shop_id,
            db=db,
        )

    new_item = MenuItem(
        name=data.name,
        price=data.price,
        category=data.category,
        image_url=image_url,
        shop_id=target_shop_id,
        unit=data.unit,
        sku=final_sku,
        low_stock_threshold=data.low_stock_threshold,
        tax_rate=data.tax_rate,
    )
    db.add(new_item)
    await db.commit()
    return RedirectResponse(url=f"/admin/?shop_id={target_shop_id}&tab=menu", status_code=303)


@router.get("/menu/edit/{item_slug}")
async def menu_edit_page(
    request: Request,
    item_slug: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Display edit form for a menu item."""
    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    
    # Fetch the menu item
    query = select(MenuItem).where(MenuItem.slug == item_slug)
    if current_user.role == "owner":
        query = query.where(MenuItem.shop_id == current_user.shop_id)
    
    result = await db.execute(query)
    item = result.scalars().first()
    
    if not item:
        return RedirectResponse(url="/admin/", status_code=303)
    
    # Get shop for currency symbol
    shop = None
    if item and item.shop_id:
        shop_res = await db.execute(select(Shop).where(Shop.id == item.shop_id))
        shop = shop_res.scalars().first()
    elif current_user.shop_id:
         shop_res = await db.execute(select(Shop).where(Shop.id == current_user.shop_id))
         shop = shop_res.scalars().first()
    
    return templates.TemplateResponse(request, "menu_edit.html", {
        "item": item,
        "shop": shop,
        "user": current_user
    })


@router.post("/menu/update/{item_slug}")
async def update_menu_item(
    item_slug: str,
    image: UploadFile = File(None),
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
    data: MenuItemCreate = Depends(MenuItemCreate.as_form)
):
    target_shop_id = data.shop_id or current_user.shop_id
    
    # Only update items from user's shop
    query = select(MenuItem).where(MenuItem.slug == item_slug)
    if current_user.role != "superadmin":
        query = query.where(MenuItem.shop_id == current_user.shop_id)
    
    result = await db.execute(query)
    item = result.scalars().first()
    
    effective_shop_id = item.shop_id if item else target_shop_id
    
    if item:
        item.name = data.name
        item.category = data.category
        item.price = data.price
        item.unit = data.unit
        item.low_stock_threshold = data.low_stock_threshold
        item.tax_rate = data.tax_rate
        # Only update SKU if owner explicitly provides one (override)
        if data.sku and data.sku.strip():
            item.sku = data.sku.upper().strip()

        image_url = await save_uploaded_image(image, max_size=(300, 300), prefix="menu")
        if image_url:
            item.image_url = image_url

        await db.commit()

    return RedirectResponse(url=f"/admin/?shop_id={effective_shop_id}&tab=menu", status_code=303)


@router.post("/menu/delete/{item_slug}")
async def delete_menu_item(
    item_slug: str,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db)
):
    
    target_shop_id = current_user.shop_id
    
    # Only allow deleting items from user's shop
    if current_user.role != "superadmin":
        await db.execute(
            delete(MenuItem).where(
                MenuItem.slug == item_slug,
                MenuItem.shop_id == target_shop_id
            )
        )
    else:
        # Superadmin or no specific shop constraint
        await db.execute(delete(MenuItem).where(MenuItem.slug == item_slug))
    
    await db.commit()
    redirect_url = f"/admin/?tab=menu"
    if target_shop_id:
        redirect_url += f"&shop_id={target_shop_id}"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/settings")
async def update_settings(
    logo: UploadFile = File(None),
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
    data: ShopCreate = Depends(ShopCreate.as_form)
):
    # Use shop_id from form data if provided (superadmin), else use current_user's shop_id
    target_shop_id = getattr(data, "shop_id", None) or current_user.shop_id
    
    if not target_shop_id:
        raise HTTPException(status_code=400, detail="No shop assigned")
    
    res = await db.execute(select(Shop).where(Shop.id == target_shop_id))
    shop = res.scalars().first()
    
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    
    shop.name = data.name
    shop.address = data.address
    shop.currency_symbol = data.currency_symbol
    shop.font_color = data.font_color
    shop.background_color = data.background_color
    shop.header_color = data.header_color
    shop.logo_size = data.logo_size
    shop.watermark_opacity = data.watermark_opacity
    shop.card_bg_color = data.card_bg_color
    shop.sidebar_bg_color = data.sidebar_bg_color
    shop.accent_color = data.accent_color
    shop.border_color = data.border_color
    shop.price_card_bg = data.price_card_bg
    shop.header_text_color = data.header_text_color
    shop.cart_bg_color = data.cart_bg_color
    shop.upi_id = data.upi_id
    shop.block_over_credit_limit = data.block_over_credit_limit
    shop.receipt_footer = data.receipt_footer
    shop.printer_paper_width = data.printer_paper_width
    shop.printer_alignment = data.printer_alignment
    shop.timezone = data.timezone  # IANA zone chosen in settings; controls bill/receipt display time
    
    logo_url = await save_uploaded_image(logo, max_size=(150, 150), prefix="logo")
    if logo_url:
        shop.logo_url = logo_url
            
    await db.commit()
    return RedirectResponse(url=f"/admin/?shop_slug={shop.slug}&tab=printer_settings", status_code=303)


# ============================================================================
# CUSTOMER MANAGEMENT
# ============================================================================

@router.post("/customer/add")
async def add_customer(
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
    data: CustomerCreate = Depends(CustomerCreate.as_form)
):
    
    target_shop_id = data.shop_id or current_user.shop_id
    if not target_shop_id:
        return {"error": "No shop assigned"}

    # Fetch shop to get its default country_code if not provided
    shop_res = await db.execute(select(Shop).where(Shop.id == target_shop_id))
    shop = shop_res.scalars().first()

    phone_number = re.sub(r'\D', '', data.phone_number)
    from app.core.config import settings
    country_code = data.country_code or (shop.country_code if shop else None) or settings.DEFAULT_COUNTRY_CODE

    new_customer = Customer(
        name=data.name,
        phone_number=phone_number,
        country_code=country_code,
        shop_id=target_shop_id,
        is_credit_customer=data.is_credit_customer,
        credit_limit=data.credit_limit,
        payment_term_type=data.payment_term_type,
        payment_term_value=data.payment_term_value
    )
    db.add(new_customer)
    await db.commit()
    return RedirectResponse(url=f"/admin/?shop_id={target_shop_id}&tab=customers", status_code=303)


@router.get("/customer/edit/{customer_slug}")
async def customer_edit_page(
    request: Request,
    customer_slug: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Display edit form for a customer."""
    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    
    # Fetch the customer
    query = select(Customer).where(Customer.slug == customer_slug)
    if current_user.role != "superadmin":
        query = query.where(Customer.shop_id == current_user.shop_id)
    
    result = await db.execute(query)
    customer = result.scalars().first()
    
    if not customer:
        return RedirectResponse(url="/admin/", status_code=303)
    
    # Get shop for context (subscription eagerly loaded — needed for the
    # credit_billing feature check below, FeatureService reads it)
    from sqlalchemy.orm import selectinload
    shop = None
    if customer and customer.shop_id:
        shop_res = await db.execute(
            select(Shop).where(Shop.id == customer.shop_id).options(selectinload(Shop.subscription))
        )
        shop = shop_res.scalars().first()
    elif current_user.shop_id:
        shop_res = await db.execute(
            select(Shop).where(Shop.id == current_user.shop_id).options(selectinload(Shop.subscription))
        )
        shop = shop_res.scalars().first()

    from app.core.config import settings
    default_country_code = customer.country_code or (shop.country_code if shop else None) or settings.DEFAULT_COUNTRY_CODE

    # UX-only gate (FIX-11): hides the credit-terms form for shops without the
    # entitlement so they don't see an option that the backend will reject
    # anyway (CustomerService.set_credit_terms is the authoritative check).
    # This page has no Alpine "$store.features" wiring (that store is only
    # initialized by pos-app.js on the POS page), so the gate is applied
    # server-side with the same FeatureService the backend check uses.
    from app.domains.features.service import FeatureService
    credit_billing_enabled = await FeatureService(db=db).is_feature_enabled(shop, "credit_billing")

    return templates.TemplateResponse(request, "customer_edit.html", {
        "customer": customer,
        "shop": shop,
        "user": current_user,
        "default_country_code": default_country_code,
        "credit_billing_enabled": credit_billing_enabled,
    })


@router.post("/customer/update/{customer_slug}")
async def update_customer(
    customer_slug: str,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
    data: CustomerCreate = Depends(CustomerCreate.as_form)
):
    
    target_shop_id = data.shop_id or current_user.shop_id
    
    query = select(Customer).where(Customer.slug == customer_slug)
    if current_user.role != "superadmin":
        query = query.where(Customer.shop_id == current_user.shop_id)
    
    result = await db.execute(query)
    customer = result.scalars().first()
    
    effective_shop_id = customer.shop_id if customer else target_shop_id
    
    if customer:
        customer.name = data.name
        customer.phone_number = re.sub(r'\D', '', data.phone_number)
        if data.country_code:
            customer.country_code = data.country_code
        # Credit terms (is_credit_customer/credit_limit/payment_term_type/value)
        # are owned exclusively by POST /admin/customers/{slug}/credit-terms
        # (CustomerService.set_credit_terms) now — not touched here, so this
        # name/phone-only form never silently resets them to CustomerCreate's
        # defaults.
        await db.commit()
    
    return RedirectResponse(url=f"/admin/?shop_id={effective_shop_id}&tab=customers", status_code=303)


@router.post("/customer/delete/{customer_slug}")
async def delete_customer(
    customer_slug: str,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db)
):
    
    target_shop_id = current_user.shop_id
    
    query = select(Customer).where(Customer.slug == customer_slug)
    if current_user.role != "superadmin":
        query = query.where(Customer.shop_id == target_shop_id)
    
    result = await db.execute(query)
    customer = result.scalars().first()
    
    effective_shop_id = customer.shop_id if customer else target_shop_id
    
    if customer:
        await db.delete(customer)
        await db.commit()
    
    return RedirectResponse(url=f"/admin/?shop_id={effective_shop_id}&tab=customers", status_code=303)


# ============================================================================
# BILL DETAILS & WHATSAPP
# ============================================================================

@router.get("/bill/{bill_slug}")
async def get_bill_detail(
    bill_slug: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy.orm import selectinload
    
    if current_user.role not in ["owner", "superadmin", "cashier"]:
         return JSONResponse({"error": "Unauthorized"}, status_code=403)
    
    target_shop_id = current_user.shop_id
    
    query = select(Bill).where(Bill.slug == bill_slug).options(selectinload(Bill.customer))
    if current_user.role != "superadmin":
        query = query.where(Bill.shop_id == target_shop_id)
    
    result = await db.execute(query)
    bill = result.scalars().first()
    
    if not bill:
        return JSONResponse({"error": "Bill not found"}, status_code=404)
    
    customer_data = None
    if bill.customer:
        customer_data = {
            "name": bill.customer.name,
            "phone_number": bill.customer.phone_number
        }
    
    shop_res = await db.execute(select(Shop).where(Shop.id == bill.shop_id))
    shop = shop_res.scalars().first()
    
    local_ts = shop_local(bill.timestamp, shop.timezone if shop else "UTC")
    return JSONResponse({
        "bill_number": bill.bill_number,
        "timestamp": local_ts.strftime("%Y-%m-%d %H:%M") if local_ts else "",
        "total_amount": float(bill.total_amount),
        "payment_method": getattr(bill, 'payment_method', 'Cash'),
        "items_snapshot": bill.items_snapshot,
        "customer": customer_data
    })


@router.get("/bill/{bill_slug}/whatsapp-redirect", response_class=HTMLResponse)
async def whatsapp_redirect_page(
    bill_slug: str,
    phone: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Serves a lightweight redirect page. This acts as a bridge to allow
    window.open() to work synchronously in the frontend, while the backend
    logic (fetching bill, formatting message) happens here during page load.
    """
    from sqlalchemy.orm import selectinload
    from app.infrastructure.integrations.whatsapp import whatsapp_service
    from app.domains.billing.service import BillingService

    if current_user.role not in ["owner", "superadmin", "cashier"]:
        return HTMLResponse("<h1>Error: Unauthorized</h1>", status_code=403)

    query = select(Bill).where(Bill.slug == bill_slug).options(selectinload(Bill.customer))
    if current_user.role != "superadmin":
        query = query.where(Bill.shop_id == current_user.shop_id)
    result = await db.execute(query)
    bill = result.scalars().first()

    if not bill:
         return HTMLResponse("<h1>Error: Bill not found</h1>", status_code=404)

    current_bill_shop_id = bill.shop_id
    shop_res = await db.execute(select(Shop).where(Shop.id == current_bill_shop_id))
    shop = shop_res.scalars().first()

    message = BillingService.format_whatsapp_bill_message(
        bill_number=bill.bill_number,
        items_snapshot=bill.items_snapshot or [],
        total_amount=float(bill.total_amount),
        shop_name=shop.name if shop else "Restaurant",
        currency=shop.currency_symbol if shop else "₹",
        bill_date=shop_local(bill.timestamp, shop.timezone if shop else "UTC").strftime("%d-%b-%Y %I:%M %p") if bill.timestamp else "",
        footer_message=shop.receipt_footer if shop and hasattr(shop, 'receipt_footer') else "Thank you for your order!",
        due_date=shop_local(bill.due_date, shop.timezone if shop else "UTC").strftime("%d-%b-%Y") if bill.due_date else "",
        payment_status=bill.payment_status or "",
        subtotal_amount=float(bill.subtotal_amount) if bill.subtotal_amount is not None else None,
        tax_amount=float(bill.tax_amount) if bill.tax_amount is not None else None,
        delivery_charge=float(bill.delivery_charge or 0),
        upi_id=shop.upi_id if shop else None,
        amount_paid=float(bill.amount_paid or 0),
    )
    from app.core.config import settings
    customer_country_code = (bill.customer.country_code if bill.customer else None) or (shop.country_code if shop else None) or settings.DEFAULT_COUNTRY_CODE
    whatsapp_url = whatsapp_service.generate_wa_link(phone, message, customer_country_code)

    # S2 FIX: Escape all user-controllable data to prevent XSS
    safe_url = html_mod.escape(whatsapp_url, quote=True)
    
    # Return an auto-redirecting HTML page with escaped values
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Redirecting to WhatsApp...</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: sans-serif; background: #111; color: #fff; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
            .loader {{ border: 4px solid #333; border-top: 4px solid #25D366; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin-bottom: 20px; }}
            @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
            a {{ color: #25D366; text-decoration: none; border: 1px solid #25D366; padding: 10px 20px; border-radius: 20px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="loader"></div>
        <h2>Opening WhatsApp...</h2>
        <p>If it doesn't open automatically, <a href="{safe_url}">Click Here</a></p>
        <script>
            // Attempt instant redirect
            window.location.href = "{safe_url}";
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# ============================================================================
# FEATURE CHECK (Debug Endpoint)
# ============================================================================

@router.get("/features")
async def check_features(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Debug endpoint to check which features are enabled for the current shop.
    Useful for verifying subscription-based feature gating.
    """
    from sqlalchemy.orm import selectinload
    from app.domains.features.service import FeatureService

    if not current_user.shop_id:
        return JSONResponse({
            "error": "No shop assigned to this user",
            "user_role": current_user.role,
        })

    # Get shop with subscription
    shop_res = await db.execute(
        select(Shop).where(Shop.id == current_user.shop_id).options(selectinload(Shop.subscription))
    )
    shop = shop_res.scalars().first()

    if not shop:
        return JSONResponse({"error": "Shop not found"})

    # Get feature status (authoritative source: FeatureService — M2M catalog)
    shop_features = await FeatureService(db=db).get_all_features_for_shop(shop)

    # Separate enabled and disabled features
    enabled = [k for k, v in shop_features.items() if v]
    disabled = [k for k, v in shop_features.items() if not v]

    return JSONResponse({
        "shop_name": shop.name,
        "subscription": shop.subscription.name if shop.subscription else "None (Free tier)",
        "user_role": current_user.role,
        "enabled_features": enabled,
        "disabled_features": disabled,
        "feature_details": shop_features
    })
