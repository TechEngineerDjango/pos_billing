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


def shop_local(dt: dt.datetime, tz_name: str) -> dt.datetime:
    """
    Converts a naive or UTC datetime to the specified timezone.
    Always returns a naive datetime representing local time to avoid formatting issues.
    """
    if not dt:
        return dt
        
    try:
        target_tz = ZoneInfo(tz_name)
    except Exception:
        target_tz = ZoneInfo("UTC")
        
    # If dt is naive, assume UTC. Otherwise, keep it aware.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    local_dt = dt.astimezone(target_tz)

    # Strip tzinfo so it renders as 'YYYY-MM-DD HH:MM:SS' consistently
    return local_dt.replace(tzinfo=None)


def shop_day_range_utc(local_date: dt.date, tz_name: str) -> tuple[dt.datetime, dt.datetime]:
    """UTC [start, end) bounds for a shop-local calendar day, for range queries."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    start_local = dt.datetime.combine(local_date, dt.time.min, tzinfo=tz)
    end_local = start_local + dt.timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
