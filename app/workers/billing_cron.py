import asyncio
import logging
import datetime as dt
from datetime import timezone
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.shared.models import Shop, ShopInvoice, Subscription

logger = logging.getLogger(__name__)

async def generate_due_invoices():
    """
    Checks for shops whose next_billing_date has passed or is missing (if they have a subscription),
    and generates an invoice for them.
    """
    async with AsyncSessionLocal() as db:
        now = dt.datetime.now(timezone.utc)
        
        # Find shops that have a subscription AND are active
        # where next_billing_date is either NULL or <= now
        query = select(Shop).options(selectinload(Shop.subscription)).where(
            Shop.is_active == True,
            Shop.subscription_id != None,
            or_(
                Shop.next_billing_date == None,
                Shop.next_billing_date <= now
            )
        )
        
        result = await db.execute(query)
        shops_to_bill = result.scalars().all()
        
        for shop in shops_to_bill:
            if not shop.subscription:
                continue
                
            # Determine cycle
            cycle = shop.subscription.billing_cycle or "monthly"
            price = shop.subscription.price
            
            if price <= 0:
                # Free tier, maybe skip or generate 0 invoice
                continue
                
            # Period start is last billing date or today
            period_start = shop.next_billing_date or now
            
            # Next billing date
            if cycle == "yearly":
                # Rough approximation, better to use dateutil.relativedelta but we stick to stdlib if possible
                try:
                    period_end = period_start.replace(year=period_start.year + 1)
                except ValueError: # leap year
                    period_end = period_start + dt.timedelta(days=365)
            else: # monthly
                try:
                    if period_start.month == 12:
                        period_end = period_start.replace(year=period_start.year + 1, month=1)
                    else:
                        period_end = period_start.replace(month=period_start.month + 1)
                except ValueError:
                    # e.g. Jan 31 -> Feb 31 (invalid)
                    period_end = period_start + dt.timedelta(days=30)
            
            # Due date is period_start + 7 days
            due_date = period_start + dt.timedelta(days=7)
            
            # Create Invoice
            invoice = ShopInvoice(
                shop_id=shop.id,
                subscription_id=shop.subscription.id,
                amount=price,
                billing_cycle=cycle,
                period_start=period_start,
                period_end=period_end,
                due_date=due_date,
                status="Pending"
            )
            
            db.add(invoice)
            
            # Update shop's next billing date
            if not shop.billing_start_date:
                shop.billing_start_date = period_start
            shop.next_billing_date = period_end
            
            logger.info(f"Generated {cycle} invoice for Shop {shop.id} - Amount: {price}")
            
        await db.commit()

async def billing_worker_loop():
    """Runs continuously in the background to trigger automated billing."""
    logger.info("Billing cron started.")
    while True:
        try:
            await generate_due_invoices()
        except Exception as e:
            logger.error(f"Error in automated billing loop: {e}")
            
        # Sleep for 1 hour
        await asyncio.sleep(3600)
