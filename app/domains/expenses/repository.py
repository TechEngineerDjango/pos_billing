from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.repository import BaseRepository
from app.shared.models import Expense


class ExpenseRepository(BaseRepository[Expense]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, Expense)

    async def list_filtered(
        self,
        shop_id: int,
        *,
        q: str = "",
        category: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        include_voided: bool = False,
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[Expense], int, Decimal]:
        # total_count/total_amount_sum are window functions computed over the
        # full filtered set (not just this page) in the same round trip, so
        # pagination doesn't break the "Total (filtered)" summary card — the
        # FILTER clause excludes voided rows from the sum even when
        # include_voided=True widens which rows the page itself shows.
        query = select(
            self.model,
            func.count().over().label("total_count"),
            func.coalesce(
                func.sum(self.model.amount).filter(self.model.is_voided.is_(False)).over(), 0
            ).label("total_amount_sum"),
        ).where(self.model.shop_id == shop_id)
        if not include_voided:
            query = query.where(self.model.is_voided.is_(False))
        if category:
            query = query.where(self.model.category == category)
        if date_from:
            query = query.where(self.model.expense_date >= date_from)
        if date_to:
            query = query.where(self.model.expense_date <= date_to)
        query_clean = q.strip()
        if query_clean:
            query = query.where(or_(
                self.model.description.ilike(f"%{query_clean}%"),
                self.model.vendor_name.ilike(f"%{query_clean}%"),
            ))
        query = query.order_by(self.model.expense_date.desc(), self.model.id.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        rows = result.all()
        expenses = [row[0] for row in rows]
        total = rows[0][1] if rows else 0
        total_amount_sum = rows[0][2] if rows else Decimal("0.00")
        return expenses, total, total_amount_sum
