import pytest
from httpx import AsyncClient
from database.models import User, Shop
from sqlalchemy import select
from routers.auth import get_password_hash

@pytest.mark.asyncio
async def test_superadmin_sees_design_tab(async_client: AsyncClient, db_session):
    # Ensure superadmin user exists
    result = await db_session.execute(select(User).filter_by(role="superadmin"))
    admin = result.scalars().first()
    if not admin:
        admin = User(username="superadmin", hashed_password=get_password_hash("admin"), role="superadmin")
        db_session.add(admin)
        await db_session.commit()

    # Login as superadmin
    await async_client.post("/auth/login", data={"username": "superadmin", "password": "superadmin"}, follow_redirects=True)
    
    # Check superadmin control center
    response = await async_client.get("/superadmin/")
    assert response.status_code == 200
    assert "Branding Studio" in response.text
    assert "activeTab === 'design'" in response.text

@pytest.mark.asyncio
async def test_owner_does_not_see_design_tab(async_client: AsyncClient, db_session):
    # Use the existing 'admin' user from conftest which has 'owner' role
    # Login as 'admin'
    login_response = await async_client.post("/auth/login", data={"username": "admin", "password": "admin"}, follow_redirects=True)
    assert login_response.status_code == 200
    
    # Access dashboard
    response = await async_client.get("/admin/")
    assert response.status_code == 200
    # Should NOT see Design tab button or content
    assert "button @click=\"switchTab('design')\"" not in response.text
    # But should see Menu
    assert "Menu Management" in response.text or "Menu" in response.text
