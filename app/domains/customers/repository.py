from app.shared.repository import BaseRepository
from app.shared.models import Customer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from datetime import datetime
from typing import Optional, List, Tuple

class CustomerRepository(BaseRepository[Customer]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, Customer)

    async def get_by_phone(self, phone_number: str, shop_id: int) -> Optional[Customer]:
        query = select(self.model).where(
            self.model.phone_number == phone_number,
            self.model.shop_id == shop_id
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_locked(self, customer_id: int) -> Optional[Customer]:
        """Row-locks the Customer for a credit-balance mutation. Caller must
        acquire this BEFORE reading credit_balance (same ordering that fixed
        the prior stock-locking bug — see task.yaml reviewer_agent notes)."""
        query = select(self.model).where(self.model.id == customer_id).with_for_update()
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def search(
        self, shop_id: int, query: str, *, credit_only: bool = False, limit: int = 20, offset: int = 0,
    ) -> Tuple[List[Customer], int]:
        """Server-side type-ahead search — name/phone match, capped and
        paginated. Backs the Credit Book / Rate Cards / Customers-tab
        pickers so the page never has to embed the shop's full customer
        list client-side."""
        conditions = [self.model.shop_id == shop_id]
        query_clean = query.strip()
        if query_clean:
            conditions.append(or_(
                self.model.name.ilike(f"%{query_clean}%"),
                self.model.phone_number.ilike(f"%{query_clean}%"),
            ))
        if credit_only:
            conditions.append(self.model.is_credit_customer.is_(True))

        # Single round trip via count(*) over() instead of a separate
        # COUNT(*) query — see app/domains/billing/admin_router.py's
        # search_menu_items for the same pattern and its offset-past-total
        # caveat (total falls back to 0, not the true count, if offset
        # skips past every matching row).
        result = await self.db.execute(
            select(self.model, func.count().over().label("total_count"))
            .where(*conditions).order_by(self.model.name).limit(limit).offset(offset)
        )
        rows = result.all()
        customers = [row[0] for row in rows]
        total = rows[0][1] if rows else 0
        return customers, total

    async def list_credit_customers_due(self, now: datetime) -> List[Customer]:
        query = select(self.model).where(
            self.model.is_credit_customer.is_(True),
            or_(
                self.model.next_statement_date.is_(None),
                self.model.next_statement_date <= now,
            ),
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())
