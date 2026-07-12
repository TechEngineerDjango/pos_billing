from decimal import Decimal
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.domains.reports.strategies.tax_calculation import TaxCalculation

class GstTax(TaxCalculation):
    async def calculate_tax_collected(self, db: AsyncSession, shop_id: int, period_start: datetime, period_end: datetime) -> Decimal:
        from app.shared.models import Bill
        query = select(func.sum(Bill.tax_amount)).where(
            and_(
                Bill.shop_id == shop_id,
                Bill.status == "Completed",
                Bill.timestamp >= period_start,
                Bill.timestamp <= period_end
            )
        )
        result = await db.execute(query)
        val = result.scalar_one_or_none()
        return Decimal(val) if val is not None else Decimal("0.00")

    async def calculate_tax_paid(self, db: AsyncSession, shop_id: int, period_start: datetime, period_end: datetime) -> Decimal:
        # TODO: Implement in Phase 4 (Expense Tracking). 
        # Currently, there are no expenses tracked in the system.
        return Decimal("0.00")
