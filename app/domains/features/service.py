"""
FeatureService — the central feature access decision engine.

Responsibility chain (in order):
  1. Check subscription expiry
  2. Redis cache hit  →  instant return
  3. Tenant override  →  overrides always win (grant or deny)
  4. Plan features    →  check M2M plan-feature table
  5. Legacy fallback  →  check JSON enabled_features for old plans
  6. Cache result     →  write back to Redis with TTL

This service NEVER imports from routers. It is called by the
`require_feature()` dependency in app/core/dependencies/features.py.
"""
import datetime as dt
from datetime import timezone
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.shared.models import Shop, Subscription, PlanFeature, Feature
from app.domains.features.repository import FeatureRepository

logger = logging.getLogger(__name__)

# Cache key template — namespaced per tenant per feature
_CACHE_KEY = "feature:{shop_id}:{feature_key}"
_CACHE_ALL_KEY = "features:{shop_id}"


class FeatureService:
    def __init__(self, db: AsyncSession, redis=None):
        self._repo = FeatureRepository(db)
        self._db = db
        self._redis = redis  # Optional — may be None (degraded mode)

    # ------------------------------------------------------------------
    # Primary resolution method
    # ------------------------------------------------------------------

    async def is_feature_enabled(self, shop: Shop, feature_key: str) -> bool:
        """
        Authoritative feature check. Called by require_feature() dependency.
        Returns True only if all conditions pass.
        """
        # 1. Shop must be active
        if not shop or not shop.is_active:
            return False

        # 2. Subscription expiry check (if applicable)
        if not self._is_subscription_valid(shop):
            return False

        # 3. Redis cache hit (fast path)
        cached = await self._cache_get(shop.id, feature_key)
        if cached is not None:
            return cached

        # 4. Override resolution (overrides always win)
        override = await self._repo.get_override(shop.id, feature_key)
        if override and override.is_valid():
            result = override.is_enabled
            await self._cache_set(shop.id, feature_key, result)
            return result

        # 5. Plan feature resolution (M2M first, legacy JSON fallback)
        result = await self._resolve_from_plan(shop, feature_key)

        # 6. Write to cache
        await self._cache_set(shop.id, feature_key, result)
        return result

    async def get_all_features_for_shop(self, shop: Shop) -> dict[str, bool]:
        """
        Returns {feature_key: bool} for ALL features in the catalog.
        Used to build the Jinja2 context island injected into every page.
        Calls is_feature_enabled per feature — results are individually cached.
        """
        all_features = await self._repo.get_all_active_features()
        result = {}
        for feature in all_features:
            result[feature.key] = await self.is_feature_enabled(shop, feature.key)
        return result

    # ------------------------------------------------------------------
    # Cache invalidation — surgical, per-tenant
    # ------------------------------------------------------------------

    async def invalidate_shop_cache(self, shop_id: int) -> None:
        """
        Called when: plan changes, override added/removed, plan updated.
        Deletes all feature cache entries for this shop.
        """
        if self._redis is None:
            return
        try:
            pattern = f"feature:{shop_id}:*"
            async for key in self._redis.scan_iter(match=pattern):
                await self._redis.delete(key)
            logger.info(f"Feature cache invalidated for shop {shop_id}")
        except Exception as e:
            logger.warning(f"Cache invalidation failed for shop {shop_id}: {e}")

    async def invalidate_global_feature_cache(self, feature_key: str) -> None:
        """
        Called when a Feature is globally deactivated by superadmin.
        Clears that key across all tenant caches.
        """
        if self._redis is None:
            return
        try:
            pattern = f"feature:*:{feature_key}"
            async for key in self._redis.scan_iter(match=pattern):
                await self._redis.delete(key)
        except Exception as e:
            logger.warning(f"Global cache invalidation failed for {feature_key}: {e}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_subscription_valid(self, shop: Shop) -> bool:
        """
        Checks if the shop's subscription has not expired.
        Shops without a subscription get Free-tier access only.
        """
        # No expiry field on Subscription model yet — always valid for now.
        # When plan_expires_at is added to Shop, check it here.
        return True

    async def _resolve_from_plan(self, shop: Shop, feature_key: str) -> bool:
        """
        Resolves feature access from the plan.
        Priority: M2M plan_features table → legacy JSON enabled_features.
        Superadmin users always have all features.
        """
        # Superadmin bypass
        if hasattr(shop, '_current_user_role') and shop._current_user_role == "superadmin":
            return True

        if not shop.subscription:
            return False

        # M2M resolution (new system)
        plan_feature_keys = await self._repo.get_plan_feature_keys(shop.subscription.id)
        if plan_feature_keys:
            return feature_key in plan_feature_keys

        # Legacy JSON fallback (old system — during migration window)
        legacy_features = shop.subscription.enabled_features or []
        return feature_key in legacy_features

    async def _cache_get(self, shop_id: int, feature_key: str) -> Optional[bool]:
        if self._redis is None:
            return None
        try:
            key = _CACHE_KEY.format(shop_id=shop_id, feature_key=feature_key)
            val = await self._redis.get(key)
            if val is None:
                return None
            return val == "1"
        except Exception:
            return None

    async def _cache_set(self, shop_id: int, feature_key: str, enabled: bool) -> None:
        if self._redis is None:
            return
        try:
            key = _CACHE_KEY.format(shop_id=shop_id, feature_key=feature_key)
            await self._redis.setex(key, settings.FEATURE_CACHE_TTL, "1" if enabled else "0")
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")
