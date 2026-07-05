import datetime as dt
from datetime import timezone
from zoneinfo import ZoneInfo


def _as_utc(value: dt.datetime) -> dt.datetime:
    """asyncpg sometimes returns naive datetimes for TIMESTAMPTZ columns.

    A naive datetime is actually stored as UTC (see model defaults), so attach
    that offset explicitly rather than leaving it ambiguous.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def utc_iso(value: dt.datetime | None) -> str | None:
    """isoformat() with a guaranteed UTC offset, for JSON/JS consumers."""
    if value is None:
        return None
    return _as_utc(value).isoformat()


def shop_local(value: dt.datetime | None, shop=None) -> dt.datetime | None:
    """Convert a UTC timestamp to the given shop's configured display timezone.

    Falls back to UTC if the shop has no timezone set or the name is invalid.
    Returns a tz-aware datetime — callers format it with their own .strftime().
    """
    if value is None:
        return None
    tz_name = getattr(shop, "timezone", None) or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return _as_utc(value).astimezone(tz)
