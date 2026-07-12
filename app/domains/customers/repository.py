from app.shared.repository import BaseRepository
from app.shared.models import Customer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional

class CustomerRepository(BaseRepository[Customer]):
    def __init__(self, session: AsyncSession):
        super().__init__(Customer, session)

    async def get_by_phone(self, phone_number: str, shop_id: int) -> Optional[Customer]:
        query = select(self.model).where(
            self.model.phone_number == phone_number,
            self.model.shop_id == shop_id
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
