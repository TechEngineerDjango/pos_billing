import datetime as dt
import json
import logging
from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, HTTPException, UploadFile, File
from fastapi.encoders import jsonable_encoder
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from database.session import get_db
from database.models import User, Shop, Subscription, Feature
from features import get_available_features, get_features_by_category, DEFAULT_TIER_FEATURES
from routers.auth import get_current_user, get_password_hash
from schemas.schemas import ShopCreate, SubscriptionCreate, UserCreate
from services.image import save_uploaded_image
from services.auth import require_superadmin
from dependencies.csrf import verify_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/superadmin", tags=["SuperAdmin"], dependencies=[Depends(verify_csrf)])
templates = Jinja2Templates(directory="templates")

def json_serializer(obj):
    if isinstance(obj, list):
        return json.dumps([i.to_dict() if hasattr(i, "to_dict") else jsonable_encoder(i) for i in obj])
    if hasattr(obj, "to_dict"):
        return json.dumps(obj.to_dict())
    return json.dumps(jsonable_encoder(obj))

templates.env.filters["tojson"] = json_serializer




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
    logo: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
    data: ShopCreate = Depends(ShopCreate.as_form)
):
    """Create a new shop."""
    new_shop = Shop(
        name=data.name,
        address=data.address,
        contact=data.contact,
        currency_symbol=data.currency_symbol,
        font_color=data.font_color,
        background_color=data.background_color,
        header_color=data.header_color,
        logo_size=data.logo_size,
        watermark_opacity=data.watermark_opacity,
        card_bg_color=data.card_bg_color,
        sidebar_bg_color=data.sidebar_bg_color,
        accent_color=data.accent_color,
        border_color=data.border_color,
        price_card_bg=data.price_card_bg,
        header_text_color=data.header_text_color,
        cart_bg_color=data.cart_bg_color,
        nav_font_family=data.nav_font_family,
        pos_card_width=data.pos_card_width,
        pos_card_height=data.pos_card_height,
        pos_card_image_width=data.pos_card_image_width,
        pos_card_image_height=data.pos_card_image_height,
        panel_font_color=data.panel_font_color,
        panel_bg_color=data.panel_bg_color,
        billing_font_color=data.billing_font_color,
        billing_card_bg_color=data.billing_card_bg_color,
        billing_card_font_color=data.billing_card_font_color,
        inc_dec_button_color=data.inc_dec_button_color,
        cash_upi_option_color=data.cash_upi_option_color,
        cash_upi_font_color=data.cash_upi_font_color,
        subscription_id=data.subscription_id if data.subscription_id else None
    )
    
    # Handle logo upload
    logo_url = await save_uploaded_image(logo, max_size=(150, 150), prefix="logo")
    if logo_url:
        new_shop.logo_url = logo_url

    db.add(new_shop)
    await db.commit()
    return RedirectResponse(url="/superadmin/?tab=fleet", status_code=303)


@router.post("/shops/update/{shop_id}")
async def update_shop(
    shop_id: int,
    logo: UploadFile = File(None),
    menu_icon: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
    data: ShopCreate = Depends(ShopCreate.as_form)
):
    """Update an existing shop."""
    result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = result.scalars().first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    shop.name = data.name
    shop.address = data.address
    shop.contact = data.contact
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
    
    if data.price_card_bg: shop.price_card_bg = data.price_card_bg
    if data.header_text_color: shop.header_text_color = data.header_text_color
    if data.cart_bg_color: shop.cart_bg_color = data.cart_bg_color
    if data.nav_font_family: shop.nav_font_family = data.nav_font_family
    if data.pos_card_width: shop.pos_card_width = data.pos_card_width
    if data.pos_card_height: shop.pos_card_height = data.pos_card_height
    if data.pos_card_image_width: shop.pos_card_image_width = data.pos_card_image_width
    if data.pos_card_image_height: shop.pos_card_image_height = data.pos_card_image_height
    if data.panel_font_color: shop.panel_font_color = data.panel_font_color
    if data.panel_bg_color: shop.panel_bg_color = data.panel_bg_color
    if data.billing_font_color: shop.billing_font_color = data.billing_font_color
    if data.billing_card_bg_color: shop.billing_card_bg_color = data.billing_card_bg_color
    if data.billing_card_font_color: shop.billing_card_font_color = data.billing_card_font_color
    if data.inc_dec_button_color: shop.inc_dec_button_color = data.inc_dec_button_color
    if data.cash_upi_option_color: shop.cash_upi_option_color = data.cash_upi_option_color
    if data.cash_upi_font_color: shop.cash_upi_font_color = data.cash_upi_font_color
    
    if data.subscription_id:
        shop.subscription_id = data.subscription_id
    
    # Handle logo upload
    logo_url = await save_uploaded_image(logo, max_size=(150, 150), prefix="logo")
    if logo_url:
        shop.logo_url = logo_url

    # Handle menu icon upload
    icon_url = await save_uploaded_image(menu_icon, max_size=(100, 100), prefix="menu_icon")
    if icon_url:
        shop.menu_icon_url = icon_url
    
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
    data: UserCreate = Depends(UserCreate.as_form),
    role: str = Form("owner")
):
    """Provision a new user with platform-wide or shop-specific access."""
    # Check if username taken
    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalars().first():
        return RedirectResponse(url="/superadmin/?tab=users&error=Username already exists", status_code=303)
    
    new_user = User(
        username=data.username,
        hashed_password=get_password_hash(data.password),
        role=role,
        shop_id=data.shop_id
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
    data: SubscriptionCreate = Depends(SubscriptionCreate.as_form)
):
    """Create a new subscription plan."""
    feature_list = [f.strip() for f in (data.features or "").split(",") if f.strip()]
    
    new_sub = Subscription(
        name=data.name,
        price=data.price,
        enabled_features=feature_list
    )
    db.add(new_sub)
    await db.commit()
    return RedirectResponse(url="/superadmin/?tab=plans", status_code=303)


@router.post("/subscriptions/update/{sub_id}")
async def update_subscription(
    sub_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
    data: SubscriptionCreate = Depends(SubscriptionCreate.as_form)
):
    """Update an existing subscription plan."""
    result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
    sub = result.scalars().first()
    
    if not sub:
        return RedirectResponse(url="/superadmin/?tab=plans&error=Plan not found", status_code=303)
    
    # Parse feature list
    feature_list = [f.strip() for f in (data.features or "").split(",") if f.strip()]
    
    sub.name = data.name
    sub.price = data.price
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
