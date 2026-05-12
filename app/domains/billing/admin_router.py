import html as html_mod
import re
import urllib.parse
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, cast, String, func

from app.core.database import get_db
from app.shared.models import User, MenuItem, Bill, Shop, Customer
from app.domains.auth.router import get_current_user, get_optional_current_user
from app.shared.schemas import UserCreate, MenuItemCreate, CustomerCreate, ShopCreate
from app.infrastructure.integrations.image import save_uploaded_image
from app.domains.auth.services import require_owner_or_above, require_any_staff
from app.core.dependencies.csrf import verify_csrf

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(verify_csrf)])
templates = Jinja2Templates(directory="app/frontend/templates")


@router.get("/")
async def admin_overview(
    request: Request,
    shop_id: Optional[int] = None,
    period: Optional[str] = "7d",  # 7d, 1m, 6m, 1y
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy.orm import selectinload
    from app.shared.features import get_shop_features
    
    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    
    # Get target shop ID
    target_shop_id = shop_id or current_user.shop_id
    
    # If superadmin and no shop_id provided, default to first active shop
    if current_user.role == "superadmin" and not shop_id:
        shop_res = await db.execute(select(Shop).where(Shop.is_active == True).limit(1))
        target_shop_id = getattr(shop_res.scalars().first(), "id", None)

    # Get user's shop
    shop = None
    menu_items = []
    customers = []
    bills = []
    staff = []
    stats = {"total_sales": 0.0, "customer_count": 0, "order_count": 0, "menu_count": 0, "staff_count": 0}
    analytics = {}
    
    if target_shop_id:
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
            
            # Bills for Reports
            bills_res = await db.execute(
                select(Bill).where(Bill.shop_id == shop.id).order_by(Bill.timestamp.desc())
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
            
            for bill in bills:
                bill_date = bill.timestamp
                if not bill_date:
                    continue
                
                # Ensure bill_date is aware (SQLite often returns naive even if timezone=True)
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
    
    # Get shop features based on subscription
    shop_features = get_shop_features(shop) if shop else {}
    
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
        "active_page": "overview"
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


@router.post("/staff/delete/{staff_id}")
async def delete_staff(
    staff_id: int,
    shop_id: Optional[int] = Form(None),
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db)
):
    
    target_shop_id = shop_id or current_user.shop_id
    
    query = select(User).where(User.id == staff_id, User.role == "cashier")
    if target_shop_id:
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

    new_item = MenuItem(
        name=data.name, 
        price=data.price, 
        category=data.category, 
        image_url=image_url,
        shop_id=target_shop_id,
        unit=data.unit
    )
    db.add(new_item)
    await db.commit()
    return RedirectResponse(url=f"/admin/?shop_id={target_shop_id}&tab=menu", status_code=303)


@router.get("/menu/edit/{item_id}")
async def menu_edit_page(
    request: Request,
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Display edit form for a menu item."""
    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    
    # Fetch the menu item
    query = select(MenuItem).where(MenuItem.id == item_id)
    if current_user.shop_id:
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


@router.post("/menu/update/{item_id}")
async def update_menu_item(
    item_id: int,
    image: UploadFile = File(None),
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
    data: MenuItemCreate = Depends(MenuItemCreate.as_form)
):
    target_shop_id = data.shop_id or current_user.shop_id
    
    # Only update items from user's shop
    query = select(MenuItem).where(MenuItem.id == item_id)
    if target_shop_id and current_user.role != "superadmin":
        query = query.where(MenuItem.shop_id == target_shop_id)
    
    result = await db.execute(query)
    item = result.scalars().first()
    
    effective_shop_id = item.shop_id if item else target_shop_id
    
    if item:
        item.name = data.name
        item.category = data.category
        item.price = data.price
        item.unit = data.unit
        
        image_url = await save_uploaded_image(image, max_size=(300, 300), prefix="menu")
        if image_url:
            item.image_url = image_url
        
        await db.commit()
    
    return RedirectResponse(url=f"/admin/?shop_id={effective_shop_id}&tab=menu", status_code=303)


@router.post("/menu/delete/{item_id}")
async def delete_menu_item(
    item_id: int,
    shop_id: Optional[int] = Form(None),
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db)
):
    
    target_shop_id = shop_id or current_user.shop_id
    
    # Only allow deleting items from user's shop
    if target_shop_id and current_user.role != "superadmin":
        await db.execute(
            delete(MenuItem).where(
                MenuItem.id == item_id,
                MenuItem.shop_id == target_shop_id
            )
        )
    else:
        # Superadmin or no specific shop constraint
        await db.execute(delete(MenuItem).where(MenuItem.id == item_id))
    
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
    
    logo_url = await save_uploaded_image(logo, max_size=(150, 150), prefix="logo")
    if logo_url:
        shop.logo_url = logo_url
            
    await db.commit()
    return RedirectResponse(url=f"/admin/?shop_id={target_shop_id}&tab=branding", status_code=303)


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
    
    phone_number = re.sub(r'\D', '', data.phone_number)
    
    new_customer = Customer(
        name=data.name,
        phone_number=phone_number,
        shop_id=target_shop_id
    )
    db.add(new_customer)
    await db.commit()
    return RedirectResponse(url=f"/admin/?shop_id={target_shop_id}&tab=customers", status_code=303)


@router.get("/customer/edit/{customer_id}")
async def customer_edit_page(
    request: Request,
    customer_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Display edit form for a customer."""
    if current_user.role not in ["owner", "superadmin"]:
        return RedirectResponse(url="/auth/login")
    
    # Fetch the customer
    query = select(Customer).where(Customer.id == customer_id)
    if current_user.shop_id:
        query = query.where(Customer.shop_id == current_user.shop_id)
    
    result = await db.execute(query)
    customer = result.scalars().first()
    
    if not customer:
        return RedirectResponse(url="/admin/", status_code=303)
    
    # Get shop for context
    shop = None
    if customer and customer.shop_id:
        shop_res = await db.execute(select(Shop).where(Shop.id == customer.shop_id))
        shop = shop_res.scalars().first()
    elif current_user.shop_id:
        shop_res = await db.execute(select(Shop).where(Shop.id == current_user.shop_id))
        shop = shop_res.scalars().first()
    
    return templates.TemplateResponse(request, "customer_edit.html", {
        "customer": customer,
        "shop": shop,
        "user": current_user
    })


@router.post("/customer/update/{customer_id}")
async def update_customer(
    customer_id: int,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
    data: CustomerCreate = Depends(CustomerCreate.as_form)
):
    
    target_shop_id = data.shop_id or current_user.shop_id
    
    query = select(Customer).where(Customer.id == customer_id)
    if target_shop_id and current_user.role != "superadmin":
        query = query.where(Customer.shop_id == target_shop_id)
    
    result = await db.execute(query)
    customer = result.scalars().first()
    
    effective_shop_id = customer.shop_id if customer else target_shop_id
    
    if customer:
        customer.name = data.name
        customer.phone_number = re.sub(r'\D', '', data.phone_number)
        await db.commit()
    
    return RedirectResponse(url=f"/admin/?shop_id={effective_shop_id}&tab=customers", status_code=303)


@router.post("/customer/delete/{customer_id}")
async def delete_customer(
    customer_id: int,
    shop_id: Optional[int] = Form(None),
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db)
):
    
    target_shop_id = shop_id or current_user.shop_id
    
    query = select(Customer).where(Customer.id == customer_id)
    if target_shop_id and current_user.role != "superadmin":
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

@router.get("/bill/{bill_id}")
async def get_bill_detail(
    bill_id: int,
    shop_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy.orm import selectinload
    
    if current_user.role not in ["owner", "superadmin", "cashier"]:
         return JSONResponse({"error": "Unauthorized"}, status_code=403)
    
    target_shop_id = shop_id or current_user.shop_id
    
    query = select(Bill).where(Bill.id == bill_id).options(selectinload(Bill.customer))
    if target_shop_id and current_user.role != "superadmin":
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
    
    return JSONResponse({
        "bill_number": bill.bill_number,
        "timestamp": bill.timestamp.strftime("%Y-%m-%d %H:%M"),
        "total_amount": float(bill.total_amount),
        "payment_method": getattr(bill, 'payment_method', 'Cash'),
        "items_snapshot": bill.items_snapshot,
        "customer": customer_data
    })


@router.post("/bill/{bill_id}/send-whatsapp")
async def send_bill_whatsapp(
    bill_id: int,
    phone_number: str = Form(None),
    shop_id: Optional[int] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy.orm import selectinload
    
    if current_user.role not in ["owner", "superadmin", "cashier"]:
         return JSONResponse({"error": "Unauthorized"}, status_code=403)
    
    target_shop_id = shop_id or current_user.shop_id
    
    query = select(Bill).where(Bill.id == bill_id).options(selectinload(Bill.customer))
    if target_shop_id and current_user.role != "superadmin":
        query = query.where(Bill.shop_id == target_shop_id)
    
    result = await db.execute(query)
    bill = result.scalars().first()
    
    if not bill:
        return JSONResponse({"error": "Bill not found"}, status_code=404)
    
    if not phone_number and bill.customer:
        phone_number = bill.customer.phone_number
    
    if not phone_number:
        return JSONResponse({"error": "No phone number provided"}, status_code=400)
    
    phone_number = re.sub(r'\D', '', phone_number)
    if len(phone_number) == 10:
        phone_number = "91" + phone_number
    
    current_bill_shop_id = bill.shop_id or target_shop_id
    shop_res = await db.execute(select(Shop).where(Shop.id == current_bill_shop_id))
    shop = shop_res.scalars().first()
    shop_name = shop.name if shop else "Restaurant"
    currency = shop.currency_symbol if shop else "₹"
    
    items_text = "\n".join([
        f"{item['qty']}x {item['name']} - {currency}{item['line_total']:.2f}"
        for item in bill.items_snapshot
    ])
    
    message = f"""*{shop_name}*
Bill #{bill.bill_number}

{items_text}

*Total: {currency}{bill.total_amount:.2f}*

Thank you for your order! \U0001f354"""
    
    whatsapp_url = f"https://wa.me/{phone_number}?text={urllib.parse.quote(message.strip(), encoding='utf-8')}"
    return JSONResponse({
        "success": True,
        "whatsapp_url": whatsapp_url,
        "message": "Bill formatted for WhatsApp"
    })

@router.get("/bill/{bill_id}/whatsapp-redirect", response_class=HTMLResponse)
async def whatsapp_redirect_page(
    bill_id: int,
    phone: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Serves a lightweight redirect page. This acts as a bridge to allow
    window.open() to work synchronously in the frontend, while the backend
    logic (fetching bill, formatting message) happens here during page load.
    """
    from sqlalchemy.orm import selectinload
    import urllib.parse
    
    query = select(Bill).where(Bill.id == bill_id).options(selectinload(Bill.customer))
    result = await db.execute(query)
    bill = result.scalars().first()
    
    if not bill:
         return HTMLResponse("<h1>Error: Bill not found</h1>", status_code=404)

    # Use provided phone
    phone_number = phone
    phone_number = re.sub(r'\D', '', phone_number)
    if len(phone_number) == 10:
        phone_number = "91" + phone_number
        
    current_bill_shop_id = bill.shop_id
    shop_res = await db.execute(select(Shop).where(Shop.id == current_bill_shop_id))
    shop = shop_res.scalars().first()
    shop_name = shop.name if shop else "Restaurant"
    currency = shop.currency_symbol if shop else "₹"
    
    # Manually format message (same as send_bill_whatsapp)
    items_text = "\n".join([
        f"{item['qty']}x {item['name']} - {currency}{item['line_total']:.2f}"
        for item in bill.items_snapshot or []
    ])
    
    message = f"""*{shop_name}*
Bill #{bill.bill_number}

{items_text}

*Total: {currency}{bill.total_amount:.2f}*

Thank you for your order! \U0001f354"""

    # Generate the actual WhatsApp URL
    whatsapp_url = f"https://wa.me/{phone_number}?text={urllib.parse.quote(message.strip(), encoding='utf-8')}"
    
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
    from app.shared.features import get_shop_features, FEATURES
    
    if not current_user.shop_id:
        return JSONResponse({
            "error": "No shop assigned to this user",
            "user_role": current_user.role,
            "available_features": list(FEATURES.keys())
        })
    
    # Get shop with subscription
    shop_res = await db.execute(
        select(Shop).where(Shop.id == current_user.shop_id).options(selectinload(Shop.subscription))
    )
    shop = shop_res.scalars().first()
    
    if not shop:
        return JSONResponse({"error": "Shop not found"})
    
    # Get feature status
    shop_features = get_shop_features(shop)
    
    # Separate enabled and disabled features
    enabled = [k for k, v in shop_features.items() if v.get("enabled")]
    disabled = [k for k, v in shop_features.items() if not v.get("enabled")]
    
    return JSONResponse({
        "shop_name": shop.name,
        "subscription": shop.subscription.name if shop.subscription else "None (Free tier)",
        "subscription_features": shop.subscription.enabled_features if shop.subscription else [],
        "user_role": current_user.role,
        "enabled_features": enabled,
        "disabled_features": disabled,
        "feature_details": shop_features
    })
