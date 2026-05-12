import asyncio
from sqlalchemy import text
from app.core.database import AsyncSessionLocal

async def list_users():
    async with AsyncSessionLocal() as db:
        result = await db.execute(text("SELECT username, role FROM users;"))
        for row in result.fetchall():
            print(row)

asyncio.run(list_users())
