import os
import pytest
from dotenv import load_dotenv

load_dotenv()
SUPERADMIN_PASSWORD = os.getenv("TEST_SUPERADMIN_PASSWORD")
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
CASHIER_PASSWORD = os.getenv("TEST_CASHIER_PASSWORD")
from httpx import AsyncClient
from app.shared.models import User, Shop, MenuItem, Bill
from sqlalchemy import select
from app.domains.auth.router import get_password_hash

@pytest.mark.asyncio
async def test_full_platform_lifecycle_e2e(async_client: AsyncClient, db_session):
    """
    Production Readiness Test: Comprehensive end-to-end platform flow.
    1. Superadmin configuration (fleet management)
    2. Shop Owner onboarding
    3. Operational setup (menu management)
    4. Business execution (POS billing)
    5. Reporting verification
    """
    
    # --- PHASE 1: SUPERADMIN CONTROL ---
    # Login as Superadmin
    await async_client.post("/auth/login", data={"username": "superadmin", "password": SUPERADMIN_PASSWORD}, follow_redirects=True)
    
    # Create a new shop via API
    shop_data = {
        "name": "E2E Burger Galaxy",
        "address": "Quantum Street 42",
        "contact": "9998887776",
        "currency_symbol": "Q"
    }
    await async_client.post("/superadmin/shops/create", data=shop_data, follow_redirects=True)
    
    # Verify shop exists
    result = await db_session.execute(select(Shop).filter_by(name="E2E Burger Galaxy"))
    new_shop = result.scalars().first()
    assert new_shop is not None
    assert new_shop.currency_symbol == "Q"

    # Create Owner for this shop
    owner_data = {
        "username": "galaxy_owner",
        "password": "Galaxy@Pass123!",
        "role": "owner",
        "shop_id": new_shop.id
    }
    await async_client.post("/superadmin/users/create", data=owner_data, follow_redirects=True)
    
    # Logout Superadmin
    await async_client.get("/auth/logout", follow_redirects=True)

    # --- PHASE 2: SHOP OWNER OPERATIONS ---
    # Login as New Owner
    login_resp = await async_client.post("/auth/login", data={"username": "galaxy_owner", "password": "Galaxy@Pass123!"}, follow_redirects=True)
    assert login_resp.status_code == 200
    assert "Catalog Control" in login_resp.text # Landing page for owner
    
    # Add a menu item
    menu_item_data = {
        "name": "Quantum Burger",
        "price": 100.0
    }
    await async_client.post("/admin/menu/add", data=menu_item_data, follow_redirects=True)
    
    # Verify menu item exists
    result = await db_session.execute(select(MenuItem).filter_by(shop_id=new_shop.id))
    items = result.scalars().all()
    assert len(items) == 1
    assert items[0].name == "Quantum Burger"

    # --- PHASE 3: BUSINESS EXECUTION (POS) ---
    # Access POS
    pos_resp = await async_client.get("/billing/", follow_redirects=True)
    assert "Quantum Burger" in pos_resp.text
    assert "Q100.0" in pos_resp.text # Specialized currency injected

    # Create a bill
    bill_payload = {
        "items": [{"id": items[0].id, "qty": 2}],
        "payment_method": "Cash",
        "customer_phone": "1234567890",
        "customer_name": "Test Customer"
    }
    bill_resp = await async_client.post("/billing/create", json=bill_payload)
    assert bill_resp.status_code == 200
    bill_data = bill_resp.json()
    assert "bill_number" in bill_data
    
    # --- PHASE 4: REPORTING ---
    # Check Dashboard Sales History
    dash_resp = await async_client.get("/admin/", follow_redirects=True)
    assert bill_data["bill_number"] in dash_resp.text
    assert "Q200.0" in dash_resp.text # Total for 2 burgers

    # Verify Database record
    result = await db_session.execute(select(Bill).filter_by(bill_number=bill_data["bill_number"]))
    final_bill = result.scalars().first()
    assert final_bill is not None
    assert float(final_bill.total_amount) == 200.0
    assert final_bill.shop_id == new_shop.id

    print("\n✅ Platform Lifecycle E2E Test Passed: Platform is functional and ready.")
