"""
Pluggable Notification Service for low-stock alerts.

Architecture:
  - NotificationService (ABC) — defines the contract
  - InAppNotificationService — active implementation, writes to notification_log table
  - Future: WhatsAppNotificationService, SMSNotificationService, EmailNotificationService
    all implement NotificationService without changing any caller code.

Usage:
    svc = InAppNotificationService(db=db)
    await svc.send_low_stock_alert(shop_id=1, item_name="Burger", current_qty=2.0, threshold=5.0)
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional
import datetime as dt
from datetime import timezone

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class NotificationService(ABC):
    """Abstract base — all notification channels implement this interface."""

    @abstractmethod
    async def send_low_stock_alert(
        self,
        shop_id: int,
        item_name: str,
        current_qty: float,
        threshold: float,
        item_id: Optional[int] = None,
    ) -> None:
        """Send a low-stock alert for a specific item."""
        ...


class InAppNotificationService(NotificationService):
    """
    Writes low-stock alerts to the notification_log table.
    These are read at dashboard load time and displayed as in-app banners.
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    async def send_low_stock_alert(
        self,
        shop_id: int,
        item_name: str,
        current_qty: float,
        threshold: float,
        item_id: Optional[int] = None,
    ) -> None:
        from app.shared.models import NotificationLog

        message = (
            f"Low stock alert: '{item_name}' has {current_qty} units remaining "
            f"(threshold: {threshold})."
        )
        try:
            log = NotificationLog(
                shop_id=shop_id,
                channel="in_app",
                message=message,
                status="sent",
                sent_at=dt.datetime.now(timezone.utc),
            )
            self._db.add(log)
            # NOTE: Caller is responsible for commit — this runs inside the bill transaction.
            logger.info(f"[InApp] {message}")
        except Exception as exc:
            # Notification failure must never abort the bill transaction
            logger.warning(f"Failed to write in-app notification: {exc}")


# ---------------------------------------------------------------------------
# Future channel stubs (not activated — implement and register when needed)
# ---------------------------------------------------------------------------

# class WhatsAppNotificationService(NotificationService):
#     """Send low-stock alerts via WhatsApp API."""
#     async def send_low_stock_alert(self, shop_id, item_name, current_qty, threshold, item_id=None):
#         ...

# class SMSNotificationService(NotificationService):
#     """Send low-stock alerts via SMS gateway."""
#     async def send_low_stock_alert(self, shop_id, item_name, current_qty, threshold, item_id=None):
#         ...

# class EmailNotificationService(NotificationService):
#     """Send low-stock alerts via SMTP."""
#     async def send_low_stock_alert(self, shop_id, item_name, current_qty, threshold, item_id=None):
#         ...


def get_notification_service(db: AsyncSession) -> NotificationService:
    """
    Factory: returns the active notification service.
    Swap InAppNotificationService for another class here to change the channel globally.
    """
    return InAppNotificationService(db=db)
