from datetime import date
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.shared.repository import BaseRepository
from app.shared.models import CustomerItemPrice


class CustomerItemPriceRepository(BaseRepository[CustomerItemPrice]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, CustomerItemPrice)

    async def list_by_start_date(self, customer_id: int, menu_item_id: int, valid_from: date) -> List[CustomerItemPrice]:
        """Only an exact valid_from match for the same (customer, item) is a
        real conflict — see task.yaml FEAT-3 reviewer correction."""
        query = select(self.model).where(
            self.model.customer_id == customer_id,
            self.model.menu_item_id == menu_item_id,
            self.model.valid_from == valid_from,
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_active_for_date(self, customer_id: int, menu_item_id: int, as_of_date: date) -> Optional[CustomerItemPrice]:
        """Latest valid_from that covers as_of_date wins — no mutation of a
        prior row's valid_to needed when a newer open-ended row is added."""
        query = (
            select(self.model)
            .where(
                self.model.customer_id == customer_id,
                self.model.menu_item_id == menu_item_id,
                self.model.is_active.is_(True),
                self.model.valid_from <= as_of_date,
                or_(self.model.valid_to.is_(None), self.model.valid_to >= as_of_date),
            )
            .order_by(self.model.valid_from.desc())
            .limit(1)
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
