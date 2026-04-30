import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.base import Base
from database.session import get_db
from main import app, get_password_hash
from database.models import User, Shop

# TEST DATABASE
# Use in-memory SQLite for speed and isolation
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False}, 
    poolclass=StaticPool, # Needed for in-memory to share across connections
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
        hashed_pw_super = get_password_hash("superadmin")
        superadmin = User(username="superadmin", hashed_password=hashed_pw_super, role="superadmin")
        session.add(superadmin)

        # Seed Owner
        hashed_pw = get_password_hash("admin")
        admin_user = User(username="admin", hashed_password=hashed_pw, role="owner")
        shop = Shop(name="Test Shop", printer_ip="mock")
        session.add(admin_user)
        session.add(shop)
        await session.commit()
        await session.refresh(shop)
        
        # Link user to shop
        admin_user.shop_id = shop.id
        session.add(admin_user)
        await session.commit()
        
        yield session
        
    # Teardown
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture()
async def async_client(db_session):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
