import os
import pytest
from dotenv import load_dotenv

load_dotenv()
SUPERADMIN_PASSWORD = os.getenv("TEST_SUPERADMIN_PASSWORD")
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
from httpx import AsyncClient
from sqlalchemy import select
from app.shared.models import User

@pytest.mark.asyncio
async def test_dashboard_link_role_awareness(async_client: AsyncClient, db_session):
    """Verify navbar Dashboard link correctly routes based on user role"""
    
    # 1. Test Superadmin Routing
    await async_client.post("/auth/login", data={"username": "superadmin", "password": SUPERADMIN_PASSWORD}, follow_redirects=True)
    resp = await async_client.get("/billing/") # Start at POS
    # Check if 'Control Center' link exists and points to /superadmin/
    assert 'href="/superadmin/"' in resp.text
    assert 'Control Center' in resp.text
    
    # 2. Test Owner Routing
    await async_client.get("/auth/logout", follow_redirects=True)
    await async_client.post("/auth/login", data={"username": "owner", "password": OWNER_PASSWORD}, follow_redirects=True)
    resp = await async_client.get("/billing/")
    # Check if 'Dashboard' link exists and points to /admin/
    assert 'href="/admin/"' in resp.text
    assert 'Dashboard' in resp.text

@pytest.mark.asyncio
async def test_sticky_tab_navigation(async_client: AsyncClient, db_session):
    """Verify that dashboard tabs are sticky via URL parameters"""
    await async_client.post("/auth/login", data={"username": "owner", "password": OWNER_PASSWORD}, follow_redirects=True)
    
    # Access dashboard with specific tab
    response = await async_client.get("/admin/?tab=reports")
    assert response.status_code == 200
    # The Alpine.js initialization should catch 'reports'
    assert "activeTab: params.get('tab') || 'overview'" in response.text
    
    # Verify that 'Reports' section is logically active (not hidden by default)
    # Note: Logic is handled in browser, but we verify the HTML contains the correct initialization code fix
    assert "params.get('tab')" in response.text

@pytest.mark.asyncio
async def test_unauthorized_access_redirection(async_client: AsyncClient):
    """Verify production-grade security: Guest users cannot access protected routes"""
    protected_routes = ["/admin/", "/superadmin/", "/billing/"]
    
    for route in protected_routes:
        response = await async_client.get(route, follow_redirects=False)
        # Should redirect to login
        assert response.status_code == 303
        assert "/auth/login" in response.headers["location"]

@pytest.mark.asyncio
async def test_superadmin_pos_shop_switching(async_client: AsyncClient, db_session):
    """Verify Superadmin can switch between different shop views in POS"""
    from app.shared.models import Shop
    
    # Create extra shop
    new_shop = Shop(name="Secondary Shop", currency_symbol="£")
    db_session.add(new_shop)
    await db_session.commit()
    await db_session.refresh(new_shop)

    await async_client.post("/auth/login", data={"username": "superadmin", "password": SUPERADMIN_PASSWORD}, follow_redirects=True)
    
    # Access POS with shop_slug
    response = await async_client.get(f"/billing/?shop_slug={new_shop.slug}")
    assert response.status_code == 200
    assert "Secondary Shop" in response.text
    # Ensure currency symbol changes
    assert "£" in response.text
