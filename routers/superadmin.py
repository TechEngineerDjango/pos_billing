import datetime
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.encoders import jsonable_encoder


from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Optional

from database.session import get_db
from database.models import User, Shop, Subscription, Feature
from routers.auth import get_current_user, get_password_hash

router = APIRouter(prefix="/superadmin", tags=["SuperAdmin"])
import json
import shutil
import os
import uuid
from PIL import Image
from fastapi import UploadFile, File
templates = Jinja2Templates(directory="templates")

def json_serializer(obj):
    if isinstance(obj, list):
        return json.dumps([i.to_dict() if hasattr(i, "to_dict") else jsonable_encoder(i) for i in obj])
    if hasattr(obj, "to_dict"):
        return json.dumps(obj.to_dict())
    return json.dumps(jsonable_encoder(obj))

templates.env.filters["tojson"] = json_serializer



def require_superadmin(current_user: User = Depends(get_current_user)):
    """Dependency to ensure only superadmin can access these routes."""
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return current_user


# ============================================================================
# DASHBOARD
# ============================================================================

@router.get("/", response_class=HTMLResponse)
async def superadmin_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Main superadmin dashboard - overview of all shops and users."""
    from sqlalchemy.orm import selectinload
    from features import get_available_features, get_features_by_category, DEFAULT_TIER_FEATURES
    
    # Fetch all shops with subscription relationship
    shops_res = await db.execute(select(Shop).options(selectinload(Shop.subscription)))
    shops = shops_res.scalars().all()
    
    # Fetch all users (excluding superadmin) with shop relationship
    users_res = await db.execute(select(User).where(User.role != "superadmin").options(selectinload(User.shop)))
    all_users = users_res.scalars().all()
    
    # Fetch subscriptions with shops relationship
    subs_res = await db.execute(select(Subscription).options(selectinload(Subscription.shops)))
    subscriptions = subs_res.scalars().all()
    
    return templates.TemplateResponse(request, "superadmin_dashboard.html", {
        "user": current_user,
        "shops": shops,
        "users": all_users,
        "subscriptions": subscriptions,
        "available_features": get_available_features(),
        "features_by_category": get_features_by_category(),
        "default_tier_features": DEFAULT_TIER_FEATURES,
        "active_page": "overview"
    })


# ============================================================================
# SHOP MANAGEMENT
# ============================================================================

@router.post("/shops/create")
async def create_shop(
    name: str = Form(...),
    address: str = Form(""),
    contact: str = Form(""),
    currency_symbol: str = Form("₹"),
    font_color: str = Form("#ffffff"),
    background_color: str = Form("#0f172a"),
    header_color: str = Form("#1e293b"),
    logo_size: int = Form(40),
    watermark_opacity: float = Form(0.1),
    card_bg_color: str = Form("#1e293b"),
    sidebar_bg_color: str = Form("#0f172a"),
    accent_color: str = Form("#f97316"),
    border_color: str = Form("rgba(255, 255, 255, 0.1)"),
    price_card_bg: str = Form("#1e293b"),
    header_text_color: str = Form("#ffffff"),
    cart_bg_color: str = Form("#0f172a"),
    nav_font_family: str = Form("'Outfit', sans-serif"),
    pos_card_width: str = Form("100%"),
    pos_card_height: str = Form("auto"),
    pos_card_image_width: str = Form("100%"),
    pos_card_image_height: str = Form("8rem"),
    panel_font_color: str = Form("#ffffff"),
    panel_bg_color: str = Form("#1e293b"),
    billing_font_color: str = Form("#ffffff"),
    billing_card_bg_color: str = Form("#1e293b"),
    billing_card_font_color: str = Form("#ffffff"),
    inc_dec_button_color: str = Form("#f97316"),
    cash_upi_option_color: str = Form("#1e293b"),
    cash_upi_font_color: str = Form("#ffffff"),
    logo: UploadFile = File(None),
    subscription_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Create a new shop."""
    new_shop = Shop(
        name=name,
        address=address,
        contact=contact,
        currency_symbol=currency_symbol,
        font_color=font_color,
        background_color=background_color,
        header_color=header_color,
        logo_size=logo_size,
        watermark_opacity=watermark_opacity,
        card_bg_color=card_bg_color,
        sidebar_bg_color=sidebar_bg_color,
        accent_color=accent_color,
        border_color=border_color,
        price_card_bg=price_card_bg,
        header_text_color=header_text_color,
        cart_bg_color=cart_bg_color,
        nav_font_family=nav_font_family,
        pos_card_width=pos_card_width,
        pos_card_height=pos_card_height,
        pos_card_image_width=pos_card_image_width,
        pos_card_image_height=pos_card_image_height,
        panel_font_color=panel_font_color,
        panel_bg_color=panel_bg_color,
        billing_font_color=billing_font_color,
        billing_card_bg_color=billing_card_bg_color,
        billing_card_font_color=billing_card_font_color,
        inc_dec_button_color=inc_dec_button_color,
        cash_upi_option_color=cash_upi_option_color,
        cash_upi_font_color=cash_upi_font_color,
        subscription_id=subscription_id if subscription_id else None
    )
    
    # Handle logo upload
    if logo and logo.filename:
        ext = logo.filename.split(".")[-1]
        filename = f"logo_{uuid.uuid4()}.{ext}"
        upload_dir = "static/uploads"
        os.makedirs(upload_dir, exist_ok=True)
        filepath = os.path.join(upload_dir, filename)
        try:
            with open(filepath, "wb") as buffer:
                shutil.copyfileobj(logo.file, buffer)
            with Image.open(filepath) as img:
                img.thumbnail((150, 150))
                img.save(filepath)
            new_shop.logo_url = f"/static/uploads/{filename}"
        except Exception: pass

    db.add(new_shop)
    await db.commit()
    return RedirectResponse(url="/superadmin/?tab=fleet", status_code=303)


@router.post("/shops/update/{shop_id}")
async def update_shop(
    shop_id: int,
    name: str = Form(...),
    address: str = Form(""),
    contact: str = Form(""),
    currency_symbol: str = Form("₹"),
    font_color: str = Form("#ffffff"),
    background_color: str = Form("#0f172a"),
    header_color: str = Form("#1e293b"),
    logo_size: int = Form(40),
    watermark_opacity: float = Form(0.1),
    card_bg_color: str = Form("#1e293b"),
    sidebar_bg_color: str = Form("#0f172a"),
    accent_color: str = Form("#f97316"),
    border_color: str = Form("rgba(255, 255, 255, 0.1)"),
    price_card_bg: Optional[str] = Form(None),
    header_text_color: Optional[str] = Form(None),
    cart_bg_color: Optional[str] = Form(None),
    nav_font_family: Optional[str] = Form(None),
    pos_card_width: Optional[str] = Form(None),
    pos_card_height: Optional[str] = Form(None),
    pos_card_image_width: Optional[str] = Form(None),
    pos_card_image_height: Optional[str] = Form(None),
    panel_font_color: Optional[str] = Form(None),
    panel_bg_color: Optional[str] = Form(None),
    billing_font_color: Optional[str] = Form(None),
    billing_card_bg_color: Optional[str] = Form(None),
    billing_card_font_color: Optional[str] = Form(None),
    inc_dec_button_color: Optional[str] = Form(None),
    cash_upi_option_color: Optional[str] = Form(None),
    cash_upi_font_color: Optional[str] = Form(None),
    logo: UploadFile = File(None),
    menu_icon: UploadFile = File(None),
    subscription_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Update an existing shop."""
    result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = result.scalars().first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    shop.name = name
    shop.address = address
    shop.contact = contact
    shop.currency_symbol = currency_symbol
    shop.font_color = font_color
    shop.background_color = background_color
    shop.header_color = header_color
    shop.logo_size = logo_size
    shop.watermark_opacity = watermark_opacity
    shop.card_bg_color = card_bg_color
    shop.sidebar_bg_color = sidebar_bg_color
    shop.accent_color = accent_color
    shop.border_color = border_color
    
    if price_card_bg: shop.price_card_bg = price_card_bg
    if header_text_color: shop.header_text_color = header_text_color
    if cart_bg_color: shop.cart_bg_color = cart_bg_color
    if nav_font_family: shop.nav_font_family = nav_font_family
    if pos_card_width: shop.pos_card_width = pos_card_width
    if pos_card_height: shop.pos_card_height = pos_card_height
    if pos_card_image_width: shop.pos_card_image_width = pos_card_image_width
    if pos_card_image_height: shop.pos_card_image_height = pos_card_image_height
    if panel_font_color: shop.panel_font_color = panel_font_color
    if panel_bg_color: shop.panel_bg_color = panel_bg_color
    if billing_font_color: shop.billing_font_color = billing_font_color
    if billing_card_bg_color: shop.billing_card_bg_color = billing_card_bg_color
    if billing_card_font_color: shop.billing_card_font_color = billing_card_font_color
    if inc_dec_button_color: shop.inc_dec_button_color = inc_dec_button_color
    if cash_upi_option_color: shop.cash_upi_option_color = cash_upi_option_color
    if cash_upi_font_color: shop.cash_upi_font_color = cash_upi_font_color
    
    if subscription_id:
        shop.subscription_id = subscription_id
    
    # Handle logo upload
    if logo and logo.filename:
        ext = logo.filename.split(".")[-1]
        filename = f"logo_{uuid.uuid4()}.{ext}"
        upload_dir = "static/uploads"
        os.makedirs(upload_dir, exist_ok=True)
        filepath = os.path.join(upload_dir, filename)
        try:
            with open(filepath, "wb") as buffer:
                shutil.copyfileobj(logo.file, buffer)
            with Image.open(filepath) as img:
                img.thumbnail((150, 150))
                img.save(filepath)
            shop.logo_url = f"/static/uploads/{filename}"
        except Exception: pass

    # Handle menu icon upload
    if menu_icon and menu_icon.filename:
        ext = menu_icon.filename.split(".")[-1]
        filename = f"menu_icon_{uuid.uuid4()}.{ext}"
        upload_dir = "static/uploads"
        os.makedirs(upload_dir, exist_ok=True)
        filepath = os.path.join(upload_dir, filename)
        try:
            with open(filepath, "wb") as buffer:
                shutil.copyfileobj(menu_icon.file, buffer)
            with Image.open(filepath) as img:
                img.thumbnail((100, 100)) # Smaller thumbnail for menu icon
                img.save(filepath)
            shop.menu_icon_url = f"/static/uploads/{filename}"
        except Exception: pass
    
    await db.commit()
    return RedirectResponse(url=f"/superadmin/?tab=design&shop_id={shop_id}", status_code=303)


@router.post("/shops/delete/{shop_id}")
async def delete_shop(
    shop_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Delete a shop (soft delete by deactivating)."""
    result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = result.scalars().first()
    if shop:
        shop.is_active = False
        await db.commit()
    return RedirectResponse(url="/superadmin/?tab=fleet", status_code=303)


@router.post("/shops/toggle/{shop_id}")
async def toggle_shop(
    shop_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Toggle shop active status."""
    result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = result.scalars().first()
    if shop:
        shop.is_active = not shop.is_active
        await db.commit()
    return RedirectResponse(url="/superadmin/?tab=fleet", status_code=303)


# ============================================================================
# USER MANAGEMENT
# ============================================================================

@router.post("/users/create")
async def create_user(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("cashier"),  # owner, cashier
    shop_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Create a new user (owner or cashier) for a shop."""
    # Check if username exists
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Username already exists")
    
    # Validate role
    if role not in ["owner", "cashier"]:
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'owner' or 'cashier'")
    
    hashed_pw = get_password_hash(password)
    new_user = User(
        username=username,
        hashed_password=hashed_pw,
        role=role,
        shop_id=shop_id
    )
    db.add(new_user)
    await db.commit()
    return RedirectResponse(url="/superadmin/?tab=users", status_code=303)


@router.post("/users/delete/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Delete a user (soft delete by deactivating)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if user and user.role != "superadmin":
        user.is_active = False
        await db.commit()
    return RedirectResponse(url="/superadmin/?tab=users", status_code=303)


@router.post("/users/toggle/{user_id}")
async def toggle_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Toggle user active status."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if user and user.role != "superadmin":
        user.is_active = not user.is_active
        await db.commit()
    return RedirectResponse(url="/superadmin/?tab=users", status_code=303)


@router.post("/users/purge/{user_id}")
async def purge_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Hard delete a user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if user and user.role != "superadmin":
        await db.delete(user)
        await db.commit()
    return RedirectResponse(url="/superadmin/?tab=users", status_code=303)


# ============================================================================
# SUBSCRIPTION MANAGEMENT
# ============================================================================

@router.post("/subscriptions/create")
async def create_subscription(
    name: str = Form(...),
    price: float = Form(0.0),
    features: str = Form(""),  # Comma-separated feature keys
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Create a new subscription plan."""
    feature_list = [f.strip() for f in features.split(",") if f.strip()]
    
    new_sub = Subscription(
        name=name,
        price=price,
        enabled_features=feature_list
    )
    db.add(new_sub)
    await db.commit()
    return RedirectResponse(url="/superadmin/?tab=plans", status_code=303)


@router.post("/subscriptions/update/{sub_id}")
async def update_subscription(
    sub_id: int,
    name: str = Form(...),
    price: float = Form(0.0),
    features: str = Form(""),  # Comma-separated feature keys
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Update an existing subscription plan."""
    result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
    sub = result.scalars().first()
    
    if not sub:
        return RedirectResponse(url="/superadmin/?tab=plans&error=Plan not found", status_code=303)
    
    # Parse feature list
    feature_list = [f.strip() for f in features.split(",") if f.strip()]
    
    sub.name = name
    sub.price = price
    sub.enabled_features = feature_list
    
    await db.commit()
    return RedirectResponse(url="/superadmin/?tab=plans", status_code=303)


@router.post("/subscriptions/delete/{sub_id}")
async def delete_subscription(
    sub_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Hard delete a subscription plan."""
    result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
    sub = result.scalars().first()
    if sub:
        await db.delete(sub)
        await db.commit()
    return RedirectResponse(url="/superadmin/?tab=plans", status_code=303)



@router.post("/subscriptions/assign")
async def assign_subscription(
    shop_id: int = Form(...),
    subscription_id: Optional[int] = Form(None),  # Made optional for "No Plan"
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Assign a subscription plan to a shop. Pass empty subscription_id for no plan."""
    result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = result.scalars().first()
    if shop:
        # If subscription_id is empty string or 0, set to None (no plan)
        shop.subscription_id = subscription_id if subscription_id else None
        await db.commit()
        await db.refresh(shop)
    return RedirectResponse(url="/superadmin/?tab=fleet", status_code=303)
