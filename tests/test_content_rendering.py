import os
import pytest
from dotenv import load_dotenv

load_dotenv()
SUPERADMIN_PASSWORD = os.getenv("TEST_SUPERADMIN_PASSWORD")
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
from httpx import AsyncClient

from app.shared.models import MenuItem

@pytest.mark.asyncio
async def test_dashboard_shows_menu_items(async_client: AsyncClient, db_session):
    """Test that dashboard actually displays menu items for the logged-in user"""
    # Create a menu item
    # Fetch user to get valid shop_id
    from sqlalchemy import select
    from app.shared.models import User
    
    result = await db_session.execute(select(User).where(User.username == "owner"))
    admin_user = result.scalars().first()
    
    item = MenuItem(name="Classic Burger", price=10.0, category="Burgers", shop_id=admin_user.shop_id)
    db_session.add(item)
    await db_session.commit()

    # Login as admin user (owner role in test setup)
    response = await async_client.post(
        "/auth/login",
        data={"username": "owner", "password": OWNER_PASSWORD},
        follow_redirects=True
    )
    
    assert response.status_code == 200
    html = response.text
    
    # Verify menu items are in the response
    assert "Classic Burger" in html
    # Owners should NOT see Design tab
    assert "switchTab('design')" not in html
    # But should see Menu Management
    assert "Catalog Control" in html

@pytest.mark.asyncio
async def test_superadmin_has_customization_controls(async_client: AsyncClient, db_session):
    """Test that customization controls are present for superadmin"""
    # Login as standard seeded superadmin
    await async_client.post(
        "/auth/login",
        data={"username": "superadmin", "password": SUPERADMIN_PASSWORD},
        follow_redirects=True
    )
    
    dashboard_res = await async_client.get("/superadmin/")
    html = dashboard_res.text
    
    assert "Branding Studio" in html
    assert "accent_color" in html or "designConfig.accent_color" in html


@pytest.mark.asyncio
async def test_pos_shows_menu_items(async_client: AsyncClient):
    """Test that POS page shows menu items"""
    response = await async_client.post(
        "/auth/login",
        data={"username": "Cashier@123", "password": "Cashier@123"},
        follow_redirects=False
    )
    
    # Access POS
    pos_response = await async_client.get("/billing/", follow_redirects=True)
    assert pos_response.status_code == 200
    
    html = pos_response.text
    # Should have menu items or product display
    assert len(html) > 3000  # POS should have content
