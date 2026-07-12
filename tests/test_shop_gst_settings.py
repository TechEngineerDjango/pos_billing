import pytest
from httpx import AsyncClient
from app.shared.models import Shop
from app.core.base import Base

@pytest.mark.asyncio
async def test_shop_gst_fields(db_session):
    # Ensure tables are created for tests
    async with db_session.bind.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    # This should fail because gst_number and gst_registered don't exist yet
    shop = Shop(name="Test Shop", gst_registered=True, gst_number="22AAAAA0000A1Z5")
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)
    
    assert shop.gst_registered is True
    assert shop.gst_number == "22AAAAA0000A1Z5"
