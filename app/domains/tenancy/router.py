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

from app.core.database import get_db
from app.shared.models import User, Shop, Subscription, Feature, PlanFeature, UserSession, SecurityLog
from app.domains.auth.router import get_current_user, get_password_hash
from app.shared.schemas import ShopCreate, SubscriptionCreate, UserCreate
from app.infrastructure.integrations.image import save_uploaded_image
from app.domains.auth.services import require_superadmin
from app.core.dependencies.csrf import verify_csrf
from app.domains.features.service import FeatureService
from app.core.redis import get_redis
from app.shared.time_utils import utc_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/superadmin", tags=["SuperAdmin"], dependencies=[Depends(verify_csrf)])
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "frontend" / "templates"))
templates.env.filters["utc_iso"] = utc_iso

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
    """Main superadmin dashboard - DB-driven. No hardcoded feature lists."""

    # Fetch all shops with subscription relationship
    shops_res = await db.execute(select(Shop).options(selectinload(Shop.subscription)))
    shops = shops_res.scalars().all()

    # Fetch all users (excluding superadmin) with shop relationship
    users_res = await db.execute(select(User).where(User.role != "superadmin").options(selectinload(User.shop)))
    all_users = users_res.scalars().all()

    # Fetch subscriptions with shops + M2M feature links
    subs_res = await db.execute(
        select(Subscription)
        .options(
            selectinload(Subscription.shops),
            selectinload(Subscription.plan_feature_links).selectinload(PlanFeature.feature)
        )
    )
    subscriptions = subs_res.scalars().all()

    # Fetch ALL features from DB (the authoritative catalog)
    features_res = await db.execute(select(Feature).order_by(Feature.category, Feature.name))
    all_features = features_res.scalars().all()

    # Fetch active user sessions grouped by user
    sessions_res = await db.execute(
        select(UserSession)
        .where(UserSession.is_active == True)
        .options(selectinload(UserSession.user))
        .order_by(UserSession.last_activity.desc())
    )
    active_sessions = sessions_res.scalars().all()
    sessions_grouped = {}
    active_users_count = 0
    for sess in active_sessions:
        if sess.user_id not in sessions_grouped:
            sessions_grouped[sess.user_id] = []
            active_users_count += 1
        sessions_grouped[sess.user_id].append(sess)

    # Fetch recent security logs
    logs_res = await db.execute(
        select(SecurityLog)
        .options(selectinload(SecurityLog.user))
        .order_by(SecurityLog.timestamp.desc())
        .limit(100)
    )
    security_logs = logs_res.scalars().all()

    return templates.TemplateResponse(request, "superadmin_dashboard.html", {
        "user": current_user,
        "shops": shops,
        "users": all_users,
        "subscriptions": subscriptions,
        "all_features": all_features,
        "active_sessions": active_sessions,
        "sessions_grouped": sessions_grouped,
        "active_users_count": active_users_count,
        "security_logs": security_logs,
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
    favicon: UploadFile = File(None),
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
    if data.upi_id is not None: shop.upi_id = data.upi_id
    if data.receipt_footer is not None: shop.receipt_footer = data.receipt_footer
    if data.printer_paper_width is not None: shop.printer_paper_width = data.printer_paper_width
    if data.printer_alignment is not None: shop.printer_alignment = data.printer_alignment
    
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
        
    # Handle favicon upload
    fav_url = await save_uploaded_image(favicon, max_size=(64, 64), prefix="favicon")
    if fav_url:
        shop.favicon_url = fav_url
    
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Provision a new user with platform-wide or shop-specific access."""
    form_data = await request.form()
    
    try:
        from pydantic import ValidationError
        data = UserCreate(
            username=form_data.get("username"),
            password=form_data.get("password"),
            shop_id=int(form_data.get("shop_id")) if form_data.get("shop_id") else None
        )
        role = form_data.get("role", "owner")
    except ValidationError as e:
        # Extract the specific error message (e.g., password strength failure)
        error_msg = e.errors()[0].get("msg", "Validation failed")
        from urllib.parse import quote
        return RedirectResponse(url=f"/superadmin/?tab=users&error={quote(error_msg)}", status_code=303)
    except ValueError:
        return RedirectResponse(url="/superadmin/?tab=users&error=Invalid data format", status_code=303)

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
    # Parse feature list from potential list of strings or comma-separated string
    features_raw = ",".join(data.features) if data.features else ""
    feature_list = [f.strip() for f in features_raw.split(",") if f.strip()]
    
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
    
    # Parse feature list from potential list of strings or comma-separated string
    features_raw = ",".join(data.features) if data.features else ""
    feature_list = [f.strip() for f in features_raw.split(",") if f.strip()]
    
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


# ============================================================================
# FEATURE CATALOG MANAGEMENT (DB-driven — zero hardcode)
# ============================================================================

@router.post("/features/create")
async def create_feature(
    key: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    category: str = Form("billing"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    """Create a new feature in the catalog. Key must be unique and is immutable after creation."""
    key = key.strip().lower().replace(" ", "_")
    existing = await db.execute(select(Feature).where(Feature.key == key))
    if existing.scalars().first():
        return RedirectResponse(url="/superadmin/?tab=features&error=Feature+key+already+exists", status_code=303)
    db.add(Feature(key=key, name=name.strip(), description=description.strip(), category=category.strip()))
    await db.commit()
    return RedirectResponse(url="/superadmin/?tab=features", status_code=303)


@router.post("/features/{feature_id}/toggle")
async def toggle_feature(
    feature_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
    redis=Depends(get_redis),
):
    """Toggle a feature active/inactive globally. Inactive features are hidden from all tenants."""
    res = await db.execute(select(Feature).where(Feature.id == feature_id))
    feature = res.scalars().first()
    if not feature:
        return RedirectResponse(url="/superadmin/?tab=features&error=Not+found", status_code=303)
    feature.is_active = not feature.is_active
    await db.commit()
    # Invalidate Redis cache for this feature key across all tenants
    svc = FeatureService(db=db, redis=redis)
    await svc.invalidate_global_feature_cache(feature.key)
    return RedirectResponse(url="/superadmin/?tab=features", status_code=303)


# ============================================================================
# PLAN → FEATURE M2M ASSIGNMENT (replaces legacy JSON enabled_features)
# ============================================================================

@router.post("/plans/{plan_id}/features")
async def assign_plan_features(
    plan_id: int,
    feature_ids: list[int] = Form(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
    redis=Depends(get_redis),
):
    """
    Set the M2M feature links for a plan.
    Replaces ALL existing links for the plan with the submitted set.
    Also clears the legacy enabled_features JSON column for this plan.
    Invalidates the Redis feature cache for every shop on this plan.
    """
    from sqlalchemy import delete as sa_delete

    # Verify plan exists
    plan_res = await db.execute(select(Subscription).options(selectinload(Subscription.shops)).where(Subscription.id == plan_id))
    plan = plan_res.scalars().first()
    if not plan:
        return RedirectResponse(url="/superadmin/?tab=plans&error=Plan+not+found", status_code=303)

    # Delete existing M2M links for this plan
    await db.execute(sa_delete(PlanFeature).where(PlanFeature.plan_id == plan_id))

    # Insert the new M2M links
    for fid in feature_ids:
        db.add(PlanFeature(plan_id=plan_id, feature_id=fid))

    # Clear legacy JSON column — M2M is now authoritative
    plan.enabled_features = []

    await db.commit()

    # Invalidate Redis feature cache for all shops on this plan
    svc = FeatureService(db=db, redis=redis)
    for shop in plan.shops:
        await svc.invalidate_shop_cache(shop.id)

    return RedirectResponse(url="/superadmin/?tab=plans", status_code=303)
