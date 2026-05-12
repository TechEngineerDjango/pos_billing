import os
import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from app.main import app

load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")

@pytest.mark.asyncio
async def test_csrf_enforcement_missing_token(db_session):
    """Verify that unsafe methods fail when CSRF token is missing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No CSRF header or cookie
        response = await client.post("/auth/login", data={"username": "owner", "password": "wrongpassword"})
        # Should return 403 Forbidden because CSRF is required for POST
        assert response.status_code == 403
        assert "CSRF" in response.text

@pytest.mark.asyncio
async def test_csrf_enforcement_invalid_token(db_session):
    """Verify that unsafe methods fail when CSRF token is invalid."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("csrf_token", "valid-cookie-token")
        client.headers.update({"x-csrf-token": "mismatching-token"})
        response = await client.post("/auth/login", data={"username": "owner", "password": "wrongpassword"})
        assert response.status_code == 403

@pytest.mark.asyncio
async def test_pydantic_validation_staff_add(async_client):
    """Verify that Pydantic validation works for adding staff."""
    # Login as admin (fixture already seeds it)
    await async_client.post("/auth/login", data={"username": "owner", "password": OWNER_PASSWORD})
    
    # Try adding staff with missing required fields (data is empty)
    response = await async_client.post("/admin/staff/add", data={})
    # FastAPI/Pydantic should return 422 Unprocessable Entity
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_pydantic_validation_menu_item_invalid_price(async_client):
    """Verify that Pydantic validation fails for invalid data types."""
    # Login as admin
    await async_client.post("/auth/login", data={"username": "owner", "password": OWNER_PASSWORD})
    
    # Try adding menu item with string price instead of number
    response = await async_client.post("/admin/menu/add", data={
        "name": "Invalid Burger",
        "price": "not-a-number",
        "category": "Main",
        "shop_id": 1
    })
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_hardening_workflow_success(async_client):
    """Verify that correct tokens and valid data work as expected."""
    # Login
    await async_client.post("/auth/login", data={"username": "owner", "password": OWNER_PASSWORD})
    
    # Add staff with valid data
    response = await async_client.post("/admin/staff/add", data={
        "username": "newstaff",
        "password": "SecureP@ss123",
        "shop_id": 1
    })
    # Should redirect on success
    assert response.status_code == 303
    assert "/admin/" in response.headers["location"]
