"""
Redis connection pool for feature caching and rate limiting.
Designed to degrade gracefully — if Redis is unavailable, the app
falls back to database-only mode without crashing.
"""
import logging
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

# Lazy-initialized connection pool
_redis_pool = None


async def get_redis():
    """
    FastAPI dependency: yields a Redis client from the pool.
    Returns None if Redis is not configured or unavailable.
    Callers must handle None to support degraded mode.
    """
    global _redis_pool
    try:
        import redis.asyncio as aioredis  # type: ignore
        if _redis_pool is None:
            _redis_pool = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        yield _redis_pool
    except ImportError:
        logger.warning("redis[asyncio] not installed — feature caching disabled.")
        yield None
    except Exception as e:
        logger.warning(f"Redis unavailable ({e}) — falling back to DB-only mode.")
        yield None


async def close_redis():
    """Call on app shutdown to gracefully close the pool."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None
