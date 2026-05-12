"""Reset the owner password and clear login attempts for clean test runs."""
import asyncio
import os
from dotenv import load_dotenv
from passlib.context import CryptContext
from sqlalchemy import text
from app.core.database import AsyncSessionLocal

load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
SUPERADMIN_PASSWORD = os.getenv("TEST_SUPERADMIN_PASSWORD")
CASHIER_PASSWORD = os.getenv("TEST_CASHIER_PASSWORD")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def ensure_db_exists():
    """Ensure the target database exists; create it if missing."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.config import settings
    from app.core.base import Base
    import app.shared.models  # Required to populate Base.metadata
    from urllib.parse import quote_plus
    
    # Use settings directly for consistency
    db_user = settings.DB_USER
    db_pass = quote_plus(settings.DB_PASSWORD)
    db_host = settings.DB_HOST
    db_port = settings.DB_PORT
    db_name = os.getenv("DB_NAME", settings.DB_NAME)
    
    print(f"🔍 Bootstrapping check: Host={db_host}, Port={db_port}, TargetDB={db_name}")

    if db_name != "pos":
        # 1. Create the Database if missing
        admin_url = f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/postgres"
        admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        
        try:
            async with admin_engine.connect() as conn:
                result = await conn.execute(text(f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"))
                if not result.scalar():
                    print(f"🛠️  Target database '{db_name}' not found. Creating...")
                    await conn.execute(text(f"CREATE DATABASE {db_name}"))
                    print(f"✅ Database '{db_name}' created.")
        finally:
            await admin_engine.dispose()

        # 2. Ensure Tables exist
        target_url = f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
        target_engine = create_async_engine(target_url)
        try:
            async with target_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                print(f"📋 Schema synchronized in '{db_name}'.")
        finally:
            await target_engine.dispose()

async def reset():
    # 1. Bootstrapping: Ensure DB exists before trying to connect via AsyncSessionLocal
    await ensure_db_exists()

    # 2. Reset logic
    async with AsyncSessionLocal() as db:
        # Reset Owner
        new_hash_owner = pwd_context.hash(OWNER_PASSWORD)
        await db.execute(
            text("UPDATE users SET hashed_password = :pw WHERE username = 'owner'"),
            {"pw": new_hash_owner}
        )
        
        # Reset Superadmin
        new_hash_super = pwd_context.hash(SUPERADMIN_PASSWORD)
        await db.execute(
            text("UPDATE users SET hashed_password = :pw WHERE username = 'superadmin'"),
            {"pw": new_hash_super}
        )

        # Reset Cashier
        new_hash_cashier = pwd_context.hash(CASHIER_PASSWORD)
        await db.execute(
            text("UPDATE users SET hashed_password = :pw WHERE username = 'cashier'"),
            {"pw": new_hash_cashier}
        )
        
        await db.execute(text("DELETE FROM login_attempts"))
        await db.commit()
        print(f"✅ Passwords reset (Owner/Superadmin/Cashier) and login attempts cleared")


if __name__ == "__main__":
    asyncio.run(reset())


