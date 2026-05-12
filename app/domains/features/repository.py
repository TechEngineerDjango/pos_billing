"""
FeatureRepository — pure data access layer.
Single responsibility: fetch and persist feature-related records.
No business logic. No caching. No HTTP concerns.
"""
import datetime as dt
from datetime import timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.shared.models import Feature, Subscription, PlanFeature, TenantFeatureOverride


class FeatureRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    # ------------------------------------------------------------------
    # Feature catalog
    # ------------------------------------------------------------------

    async def get_all_active_features(self) -> list[Feature]:
        result = await self._db.execute(
            select(Feature).where(Feature.is_active == True)
        )
        return result.scalars().all()

    async def get_feature_by_key(self, key: str) -> Optional[Feature]:
        result = await self._db.execute(
            select(Feature).where(Feature.key == key, Feature.is_active == True)
        )
        return result.scalars().first()

    # ------------------------------------------------------------------
    # Plan features (M2M)
    # ------------------------------------------------------------------

    async def get_plan_feature_keys(self, plan_id: int) -> set[str]:
        """
        Returns the set of active feature keys enabled for a given plan.
        Falls back gracefully if no M2M links exist (legacy mode).
        """
        result = await self._db.execute(
            select(Feature.key)
            .join(PlanFeature, PlanFeature.feature_id == Feature.id)
            .where(PlanFeature.plan_id == plan_id, Feature.is_active == True)
        )
        return set(result.scalars().all())

    async def get_plan_with_features(self, plan_id: int) -> Optional[Subscription]:
        result = await self._db.execute(
            select(Subscription)
            .where(Subscription.id == plan_id)
            .options(selectinload(Subscription.plan_feature_links).selectinload(PlanFeature.feature))
        )
        return result.scalars().first()

    async def add_feature_to_plan(self, plan_id: int, feature_id: int) -> None:
        link = PlanFeature(plan_id=plan_id, feature_id=feature_id)
        self._db.add(link)
        await self._db.flush()

    async def remove_feature_from_plan(self, plan_id: int, feature_id: int) -> None:
        result = await self._db.execute(
            select(PlanFeature).where(
                PlanFeature.plan_id == plan_id,
                PlanFeature.feature_id == feature_id
            )
        )
        link = result.scalars().first()
        if link:
            await self._db.delete(link)
            await self._db.flush()

    # ------------------------------------------------------------------
    # Tenant overrides
    # ------------------------------------------------------------------

    async def get_override(self, shop_id: int, feature_key: str) -> Optional[TenantFeatureOverride]:
        result = await self._db.execute(
            select(TenantFeatureOverride).where(
                TenantFeatureOverride.shop_id == shop_id,
                TenantFeatureOverride.feature_key == feature_key,
            )
        )
        return result.scalars().first()

    async def get_all_overrides_for_shop(self, shop_id: int) -> list[TenantFeatureOverride]:
        result = await self._db.execute(
            select(TenantFeatureOverride).where(TenantFeatureOverride.shop_id == shop_id)
        )
        return result.scalars().all()

    async def upsert_override(
        self,
        shop_id: int,
        feature_key: str,
        is_enabled: bool,
        reason: Optional[str] = None,
        expires_at=None,
    ) -> TenantFeatureOverride:
        existing = await self.get_override(shop_id, feature_key)
        if existing:
            existing.is_enabled = is_enabled
            existing.reason = reason
            existing.expires_at = expires_at
            await self._db.flush()
            return existing
        override = TenantFeatureOverride(
            shop_id=shop_id,
            feature_key=feature_key,
            is_enabled=is_enabled,
            reason=reason,
            expires_at=expires_at,
        )
        self._db.add(override)
        await self._db.flush()
        return override

    async def delete_override(self, shop_id: int, feature_key: str) -> bool:
        override = await self.get_override(shop_id, feature_key)
        if override:
            await self._db.delete(override)
            await self._db.flush()
            return True
        return False
