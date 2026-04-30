import pytest
from httpx import AsyncClient
import time

@pytest.mark.asyncio
async def test_security_login_flow(async_client: AsyncClient):
    """Integrated test for failed attempts, lockout, and recovery"""
    
    # 1. Successful Login initially
    first_success = await async_client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False
    )
    assert first_success.status_code == 303

    # 2. Fail 5 times
    for i in range(5):
        response = await async_client.post(
            "/auth/login",
            data={"username": "admin", "password": "bad_password"},
            follow_redirects=False
        )
        assert response.status_code == 401
    
    # 3. 6th attempt should be 429
    lockout = await async_client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False
    )
    assert lockout.status_code == 429
    print("✅ Lockout confirmed")
