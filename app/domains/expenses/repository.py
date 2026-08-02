from datetime import date
from typing import List, Optional

from sqlalchemy import select
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
        category: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        include_voided: bool = False,
    ) -> List[Expense]:
        query = select(self.model).where(self.model.shop_id == shop_id)
        if not include_voided:
            query = query.where(self.model.is_voided.is_(False))
        if category:
            query = query.where(self.model.category == category)
        if date_from:
            query = query.where(self.model.expense_date >= date_from)
        if date_to:
            query = query.where(self.model.expense_date <= date_to)
        query = query.order_by(self.model.expense_date.desc(), self.model.id.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())
