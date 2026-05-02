"""Reset the owner password and clear login attempts for clean test runs."""
import asyncio
from passlib.context import CryptContext
from sqlalchemy import text
from database.session import AsyncSessionLocal

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def reset():
    async with AsyncSessionLocal() as db:
        # Reset to 'owner' — this is what the shared _login_owner() helper expects
        new_hash = pwd_context.hash("owner")
        await db.execute(
            text("UPDATE users SET hashed_password = :pw WHERE username = 'owner'"),
            {"pw": new_hash}
        )
        await db.execute(text("DELETE FROM login_attempts"))
        await db.commit()
        print("✅ Owner password reset to 'owner' and login attempts cleared")

asyncio.run(reset())
