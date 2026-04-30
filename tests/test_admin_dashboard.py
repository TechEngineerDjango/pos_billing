import pytest
from httpx import AsyncClient
from database.models import MenuItem, Shop

@pytest.mark.asyncio
async def test_admin_dashboard_render(async_client: AsyncClient):
    """Test that admin dashboard renders successfully"""
    # Login and follow redirect
    response = await async_client.post(
        "/auth/login", 
        data={"username": "admin", "password": "admin"},
        follow_redirects=True
    )
    
    # Should end up at dashboard with 200 OK
    assert response.status_code == 200
    
    # Check for key dashboard content (not specific to old UI)
    text_lower = response.text.lower()
    assert any(word in text_lower for word in ["dashboard", "admin", "burger", "menu"]), \
        f"Dashboard missing expected content"
    
    # Should NOT have errors
    assert "Internal Server Error" not in response.text
    assert "TemplateSyntaxError" not in response.text


@pytest.mark.asyncio
async def test_admin_dashboard_image_rendering(async_client: AsyncClient, db_session):
    """Test that menu items with images don't cause errors"""
    from sqlalchemy import select
    
    # Get or create a shop
    shop_result = await db_session.execute(select(Shop).limit(1))
    shop = shop_result.scalars().first()
    
    if not shop:
        shop = Shop(name="Test Shop")
        db_session.add(shop)
        await db_session.commit()
        await db_session.refresh(shop)
    
    shop_id = shop.id
    
    item = MenuItem(
        name="BurgerWithImage", 
        price=12.0, 
        category="Test", 
        image_url="/static/uploads/test.jpg",
        shop_id=shop_id
    )
    db_session.add(item)
    await db_session.commit()
    
    # Login and access dashboard
    response = await async_client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=True
    )
    
    # Main test: dashboard should load without errors even with image URLs
    assert response.status_code == 200
    assert "Internal Server Error" not in response.text


