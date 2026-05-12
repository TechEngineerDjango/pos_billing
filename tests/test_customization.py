import pytest
from httpx import AsyncClient
from sqlalchemy.future import select
from app.shared.models import Shop

@pytest.mark.asyncio
async def test_customization_fields_persistence(async_client: AsyncClient, db_session):
    """Test that customization fields save and persist correctly"""
    # Ensure we have a shop (admin user should have one)
    shop_result = await db_session.execute(select(Shop).limit(1))
    shop = shop_result.scalars().first()
    
    if not shop:
        # Create shop if doesn't exist
        shop = Shop(
            name="Test Burger Shop",
            currency_symbol="$"
        )
        db_session.add(shop)
        await db_session.commit()
        await db_session.refresh(shop)
    
    # Update the shop with new customization fields
    shop.price_card_bg = "#aabbcc"
    shop.header_text_color = "#ddeeff"
    shop.cart_bg_color = "#123456"
    await db_session.commit()
    
    # Verify the fields persisted
    await db_session.refresh(shop)
    assert shop.price_card_bg == "#aabbcc"
    assert shop.header_text_color == "#ddeeff"
    assert shop.cart_bg_color == "#123456"



