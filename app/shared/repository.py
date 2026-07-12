from typing import Generic, TypeVar, Type, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

T = TypeVar("T")

class BaseRepository(Generic[T]):
    def __init__(self, db: AsyncSession, model: Type[T]):
        self.db = db
        self.model = model

    async def get_by_id(self, id: int) -> Optional[T]:
        return await self.db.get(self.model, id)

    async def get_by_slug(self, slug: str) -> Optional[T]:
        query = select(self.model).where(self.model.slug == slug)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def add(self, entity: T) -> T:
        self.db.add(entity)
        await self.db.flush()
        return entity

    async def list(self, **filters) -> List[T]:
        query = select(self.model)
        for key, value in filters.items():
            query = query.where(getattr(self.model, key) == value)
        result = await self.db.execute(query)
        return list(result.scalars().all())
