import asyncio
import logging
import datetime as dt
from datetime import timezone
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.config import settings
from app.shared.models import UserSession, SecurityLog

logger = logging.getLogger(__name__)


async def sweep_idle_sessions(db) -> int:
    """Marks is_active=False on any UserSession whose last_activity is past
    SESSION_IDLE_TIMEOUT_MINUTES. Runs on its own timer (see
    session_kickout_worker_loop) so idle accounts get kicked out even if no
    admin happens to be looking at the sessions dashboard, and even if the
    idle user's own browser never sends another request to trigger the
    per-request check in get_current_user. Returns the number swept.
    """
    idle_cutoff = dt.datetime.now(timezone.utc) - dt.timedelta(minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES)
    stale_res = await db.execute(
        select(UserSession)
        .where(UserSession.is_active == True, UserSession.last_activity < idle_cutoff)
        .options(selectinload(UserSession.user))
    )
    stale_sessions = stale_res.scalars().all()
    for sess in stale_sessions:
        sess.is_active = False
        db.add(SecurityLog(
            event_type="SESSION_IDLE_TIMEOUT",
            severity="info",
            ip_address=sess.ip_address,
            details=f"Session for user {sess.user.username} timed out after inactivity (background sweep).",
            user_id=sess.user_id,
        ))
    if stale_sessions:
        await db.commit()
    return len(stale_sessions)


async def session_kickout_worker_loop():
    """Runs continuously in the background to kick out idle sessions.

    Marking is_active=False here doesn't touch the browser directly (no
    push channel exists) — the kickout actually takes effect the next time
    that browser sends a request: get_current_user finds the row already
    inactive and, for a session that had genuinely established activity
    before going idle, forces a real 401 (which clears the auth cookie)
    instead of silently letting it through. This loop is what makes that
    check fire for abandoned sessions that never come back on their own —
    without it, a session was only ever swept opportunistically, whenever
    some other request happened to touch the same row.
    """
    logger.info("Session idle-kickout cron started.")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                count = await sweep_idle_sessions(db)
                if count:
                    logger.info(f"Session kickout sweep: {count} idle session(s) marked inactive.")
        except Exception as e:
            logger.error(f"Error in session kickout loop: {e}")

        # Idle timeout is minutes-granular; check every 5 minutes rather
        # than the hourly cadence used by the billing/statement crons.
        await asyncio.sleep(300)
