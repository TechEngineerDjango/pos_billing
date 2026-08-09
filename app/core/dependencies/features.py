"""
require_feature() — The central feature-gating dependency factory.

Usage in any router:
    @router.get("/items", dependencies=[Depends(require_feature("INVENTORY"))])
    @router.post("/orders", dependencies=[Depends(require_feature("POS"))])

This is the ONLY place where feature access is enforced on the API layer.
The frontend feature island is for UI rendering only — this is the real gate.
"""
import logging
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.redis import get_redis
from app.shared.models import Shop, Subscription, PlanFeature
from app.domains.auth.router import get_current_user
from app.domains.features.service import FeatureService

logger = logging.getLogger(__name__)


async def get_feature_service(
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> FeatureService:
    """FastAPI dependency that constructs a FeatureService with DI."""
    return FeatureService(db=db, redis=redis)


async def _load_shop_with_subscription(user, db: AsyncSession) -> Shop | None:
    """
    Loads the user's shop with subscription and plan_feature_links eagerly.
    Superadmin users have no fixed shop — returns None (bypass handled in service).
    """
    if user.role == "superadmin":
        return None
    if not user.shop_id:
        return None

    result = await db.execute(
        select(Shop)
        .where(Shop.id == user.shop_id)
        .options(
            selectinload(Shop.subscription)
            .selectinload(Subscription.plan_feature_links)
            .selectinload(PlanFeature.feature)
        )
    )
    return result.scalars().first()


def require_feature(feature_key: str):
    """
    Factory function that returns a FastAPI dependency enforcing feature access.

    Decision chain:
      - Superadmin → always allowed (platform-level access)
      - Shop inactive → 403 Forbidden
      - Feature disabled (via FeatureService) → 403 with upgrade hint
      - Otherwise → passes through

    Args:
        feature_key: The feature string key as stored in the `features` table
                     e.g. "INVENTORY", "GST", "KITCHEN"
    """
    async def _dependency(
        current_user=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        feature_service: FeatureService = Depends(get_feature_service),
    ):
        # Superadmin bypass — platform-level access, unrestricted
        if current_user.role == "superadmin":
            return

        # Load shop with subscription + feature links
        shop = await _load_shop_with_subscription(current_user, db)

        if shop is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No shop associated with this account.",
            )

        if not shop.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This shop has been deactivated. Contact support.",
            )

        is_enabled = await feature_service.is_feature_enabled(shop, feature_key)

        if not is_enabled:
            logger.warning(
                f"Feature '{feature_key}' blocked for shop {shop.id} "
                f"(plan: {shop.subscription.name if shop.subscription else 'none'})"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FEATURE_NOT_AVAILABLE",
                    "feature": feature_key,
                    "message": f"The '{feature_key}' feature is not available on your current plan.",
                    "hint": "Contact your administrator to upgrade your subscription.",
                },
            )

    return _dependency


async def require_active_shop(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Lightweight sibling of require_feature() for routes that must keep working
    for a shop regardless of its feature/plan state (e.g. managing Pay Later
    orders a shop already committed to, even after losing the credit_billing
    feature) but still must not be reachable once the shop itself is
    deactivated/suspended. No subscription/plan_feature_links eager-load —
    only the is_active check, which require_feature() would otherwise bundle
    in in a way that's inseparable from its feature gate.
    """
    if current_user.role == "superadmin":
        return

    if not current_user.shop_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No shop associated with this account.",
        )

    result = await db.execute(select(Shop.is_active).where(Shop.id == current_user.shop_id))
    is_active = result.scalar_one_or_none()

    if is_active is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No shop associated with this account.",
        )
    if not is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This shop has been deactivated. Contact support.",
        )
