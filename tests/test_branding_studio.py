import os
import pytest
from dotenv import load_dotenv

load_dotenv()
SUPERADMIN_PASSWORD = os.getenv("TEST_SUPERADMIN_PASSWORD")
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
from httpx import AsyncClient
from app.shared.models import User, Shop
from sqlalchemy import select
from app.domains.auth.router import get_password_hash

@pytest.mark.asyncio
async def test_superadmin_branding_studio_persistence(async_client: AsyncClient, db_session):
    """Test that Superadmin Branding Studio correctly persists all layout options"""
    # 1. Setup Superadmin
    # 2. Get Seeded Shop
    shop_res = await db_session.execute(select(Shop).limit(1))
    shop = shop_res.scalars().first()
    assert shop is not None

    # 3. Login as Superadmin
    await async_client.post("/auth/login", data={"username": "superadmin", "password": SUPERADMIN_PASSWORD}, follow_redirects=True)
    
    # 4. Simulate saving design choices from the Branding Studio
    design_data = {
        "name": "Branded Burger Hub",
        "accent_color": "#ff0000",
        "header_color": "#112233",
        "cart_bg_color": "#445566",
        "background_color": "#778899",
        "card_bg_color": "#aabbcc",
        "logo_size": 120,
        "watermark_opacity": 0.5,
        "currency_symbol": "USD"
    }
    
    # Use the superadmin update endpoint
    response = await async_client.post(f"/superadmin/shops/update/{shop.id}", data=design_data, follow_redirects=True)
    assert response.status_code == 200
    
    # 5. Verify Database Persistence
    await db_session.refresh(shop)
    assert shop.name == "Branded Burger Hub"
    assert shop.accent_color == "#ff0000"
    assert shop.header_color == "#112233"
    assert shop.cart_bg_color == "#445566"
    assert shop.background_color == "#778899"
    assert shop.card_bg_color == "#aabbcc"
    assert shop.logo_size == 120
    assert shop.watermark_opacity == 0.5
    assert shop.currency_symbol == "USD"

    # 6. Verify POS Page Layout reflect these changes
    # Login as shop user (or just check as admin if authenticated)
    pos_response = await async_client.get(f"/billing/?shop_id={shop.id}")
    html = pos_response.text
    
    # Check if CSS variables are correctly injected into the POS page
    assert "Branded Burger Hub" in html
    assert "#ff0000" in html # accent
    assert "0.5" in html # watermark opacity
    assert "120px" in html # logo size
