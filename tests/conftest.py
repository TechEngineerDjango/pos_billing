import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure SECRET_KEY is set before importing app/config
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

# Load environment for passwords
from dotenv import load_dotenv
load_dotenv()
SUPERADMIN_PASSWORD = os.getenv("TEST_SUPERADMIN_PASSWORD")
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
CASHIER_PASSWORD = os.getenv("TEST_CASHIER_PASSWORD")

from app.core.base import Base
from app.core.database import get_db
from app.main import app
from app.domains.auth.router import get_password_hash
from app.shared.models import User, Shop

# TEST DATABASE
# Use in-memory SQLite for speed and isolation
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,  # Needed for in-memory to share across connections
)

TestingSessionLocal = sessionmaker(
    class_=AsyncSession, autocommit=False, autoflush=False, bind=engine
)


async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture()
async def db_session():
    # Setup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestingSessionLocal() as session:
        # Seed Superadmin
        hashed_pw_super = get_password_hash(SUPERADMIN_PASSWORD)
        superadmin = User(username="superadmin", hashed_password=hashed_pw_super, role="superadmin")
        session.add(superadmin)

        # Seed Owner
        hashed_pw = get_password_hash(OWNER_PASSWORD)
        admin_user = User(username="owner", hashed_password=hashed_pw, role="owner")
        shop = Shop(name="Test Shop", printer_ip="mock")
        session.add(admin_user)
        session.add(shop)
        await session.commit()
        await session.refresh(shop)

        # Link user to shop
        admin_user.shop_id = shop.id
        session.add(admin_user)

        # Seed Cashier
        hashed_pw_cashier = get_password_hash(CASHIER_PASSWORD)
        cashier_user = User(username="cashier", hashed_password=hashed_pw_cashier, role="cashier", shop_id=shop.id)
        session.add(cashier_user)

        await session.commit()

        yield session

    # Teardown
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture()
async def async_client(db_session):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("csrf_token", "test-csrf-token")
        client.headers.update({"x-csrf-token": "test-csrf-token"})
        yield client
