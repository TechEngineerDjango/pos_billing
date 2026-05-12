import os
import pytest
from dotenv import load_dotenv

load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_admin_dashboard_loads(async_client: AsyncClient):
    """Test that admin dashboard actually loads without template errors"""
    # 1. Login
    login_res = await async_client.post(
        "/auth/login", 
        data={"username": "owner", "password": OWNER_PASSWORD},
        follow_redirects=True  # Follow the redirect
    )
    # After login redirect, should end up at admin dashboard
    assert login_res.status_code == 200, f"Login redirect failed: {login_res.status_code}"
    
    # Check we got the dashboard (NOT an error page)
    assert "Internal Server Error" not in login_res.text
    assert "TemplateSyntaxError" not in login_res.text
    
    # Should have some admin/dashboard content
    text_lower = login_res.text.lower()
    assert any(keyword in text_lower for keyword in ["burger", "dashboard", "owner", "menu"]), \
        f"Dashboard doesn't contain expected content. Got: {login_res.text[:500]}"
