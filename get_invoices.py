import asyncio
from app.core.database import AsyncSessionLocal
from app.shared.models import ShopInvoice
from sqlalchemy import select
from sqlalchemy.orm import selectinload

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(ShopInvoice).options(selectinload(ShopInvoice.shop)))
        invoices = res.scalars().all()
        print(f"Total invoices: {len(invoices)}")

asyncio.run(main())
