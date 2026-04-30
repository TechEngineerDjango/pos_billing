import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_logout_functionality(async_client: AsyncClient):
    """Test that logout correctly clears the access token and redirects"""
    
    # 1. Login first to get a cookie
    login_res = await async_client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False
    )
    assert login_res.status_code == 303
    assert login_res.cookies.get("access_token") is not None
    
    # 2. Perform Logout (POST)
    logout_res = await async_client.post("/auth/logout", follow_redirects=False)
    assert logout_res.status_code == 303
    # Verify cookie is cleared (FastAPI sets it to empty string/expires it)
    # Check if the response includes a Set-Cookie header that clears it
    has_clear_cookie = any(
        "access_token=;" in header or "access_token=\"\"" in header 
        for header in logout_res.headers.get_list("set-cookie")
    )
    assert has_clear_cookie, "Logout should clear the access_token cookie"

@pytest.mark.asyncio
async def test_logout_get_request(async_client: AsyncClient):
    """Test that logout also works via GET request for ease of use"""
    response = await async_client.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]
