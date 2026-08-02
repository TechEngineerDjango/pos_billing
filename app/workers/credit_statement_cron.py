import asyncio
import logging

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.domains.credit.service import CreditService
from app.shared.models import Shop

logger = logging.getLogger(__name__)


async def generate_due_statements():
    """Rolls up each due credit customer's unpaid Completed Credit bills into
    a CreditStatement. Mirrors billing_cron.py's generate_due_invoices()
    structure (task.yaml FEAT-7)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Shop.id).where(Shop.is_active.is_(True)))
        shop_ids = [row[0] for row in result.all()]

        service = CreditService(db)
        for shop_id in shop_ids:
            try:
                generated = await service.generate_statements_for_shop(shop_id)
                if generated:
                    logger.info(f"Generated {len(generated)} credit statement(s) for Shop {shop_id}")
                await db.commit()
            except Exception as e:
                # Roll back before the next shop — otherwise one shop's DB
                # error leaves the shared session's transaction unusable and
                # cascades failures to every subsequent shop in this run.
                await db.rollback()
                logger.error(f"Error generating credit statements for Shop {shop_id}: {e}")


async def credit_statement_worker_loop():
    """Runs continuously in the background to roll up due credit statements."""
    logger.info("Credit statement cron started.")
    while True:
        try:
            await generate_due_statements()
        except Exception as e:
            logger.error(f"Error in credit statement loop: {e}")

        # Sleep for 1 hour
        await asyncio.sleep(3600)
