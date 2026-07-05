"""
Regression coverage for the held-bill stock reservation feature
(app/domains/billing/service.py), added per task.yaml TEST-1.

Covers:
  - Creating a Held bill reserves the correct `reserved_quantity` (setup check
    for BUG-2) without touching raw `stock_quantity`.
  - A second bill that would exceed available stock (stock_quantity -
    reserved_quantity) is rejected with HTTP 400 (regression for BUG-2).
  - Resuming a Held bill and completing it via /billing/update/{slug}
    succeeds without a 500 and deducts stock correctly (regression for BUG-1,
    which crashed with a TypeError in _dispatch_low_stock_alerts).
  - Cancelling a Held bill via /billing/cancel/{slug} releases its
    reservation.

Follows the same async_client + db_session pattern used in tests/test_core.py
and tests/test_menu_management.py.
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")

from httpx import AsyncClient
from app.shared.models import MenuItem


async def _login_owner(async_client: AsyncClient):
    login_res = await async_client.post(
        "/auth/login",
        data={"username": "owner", "password": OWNER_PASSWORD},
        follow_redirects=False,
    )
    assert login_res.status_code == 303


@pytest.mark.asyncio
async def test_create_held_bill_reserves_quantity(async_client: AsyncClient, db_session):
    """Holding a bill reserves stock via reserved_quantity, leaving stock_quantity untouched."""
    item = MenuItem(name="Reserve Burger", price=5.0, category="Food", shop_id=1, stock_quantity=10)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login_owner(async_client)

    payload = {"items": [{"id": item.id, "qty": 6}], "payment_method": "Cash", "status": "Held"}
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    updated = {u["id"]: u["available_stock"] for u in data["updated_items"]}
    assert updated[item.id] == 4.0  # 10 stock - 6 reserved

    await db_session.refresh(item)
    assert item.reserved_quantity == 6.0
    assert item.stock_quantity == 10.0  # raw stock is untouched by a Hold


@pytest.mark.asyncio
async def test_create_bill_rejects_oversell_against_reserved_stock(async_client: AsyncClient, db_session):
    """Regression for BUG-2: create_bill must validate against available stock
    (stock_quantity - reserved_quantity), not raw stock_quantity, so a second
    bill cannot oversell stock already reserved by a Held bill."""
    item = MenuItem(name="Oversell Burger", price=5.0, category="Food", shop_id=1, stock_quantity=10)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login_owner(async_client)

    # Bill A holds 6 units, leaving 4 available (10 - 6).
    hold_payload = {"items": [{"id": item.id, "qty": 6}], "payment_method": "Cash", "status": "Held"}
    hold_res = await async_client.post("/billing/create", json=hold_payload)
    assert hold_res.status_code == 200

    # Bill B tries to complete 5 units, which exceeds the 4 remaining available.
    oversell_payload = {"items": [{"id": item.id, "qty": 5}], "payment_method": "Cash", "status": "Completed"}
    oversell_res = await async_client.post("/billing/create", json=oversell_payload)
    assert oversell_res.status_code == 400
    assert "Insufficient stock" in oversell_res.json()["detail"]

    # Stock/reservation must be unchanged after the rejected attempt.
    await db_session.refresh(item)
    assert item.reserved_quantity == 6.0
    assert item.stock_quantity == 10.0


@pytest.mark.asyncio
async def test_resume_and_complete_held_bill_succeeds(async_client: AsyncClient, db_session):
    """Regression for BUG-1: completing a resumed Held bill via /billing/update/{slug}
    must not raise a TypeError from _dispatch_low_stock_alerts, and must correctly
    release the reservation while deducting real stock."""
    item = MenuItem(
        name="Resume Burger", price=5.0, category="Food", shop_id=1,
        stock_quantity=10, low_stock_threshold=2,
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login_owner(async_client)

    hold_payload = {"items": [{"id": item.id, "qty": 3}], "payment_method": "Cash", "status": "Held"}
    hold_res = await async_client.post("/billing/create", json=hold_payload)
    assert hold_res.status_code == 200
    bill_slug = hold_res.json()["bill_id"]

    await db_session.refresh(item)
    assert item.reserved_quantity == 3.0

    # Resume the Held bill and complete it (this is the exact call that raised
    # TypeError: tuple indices must be integers or slices, not str, before BUG-1 was fixed).
    complete_payload = {"items": [{"id": item.id, "qty": 3}], "payment_method": "Cash", "status": "Completed"}
    complete_res = await async_client.post(f"/billing/update/{bill_slug}", json=complete_payload)
    assert complete_res.status_code == 200, complete_res.text
    data = complete_res.json()
    assert data["status"] == "success"

    updated = {u["id"]: u["available_stock"] for u in data["updated_items"]}
    assert updated[item.id] == 7.0  # 10 - 3 sold, reservation released

    await db_session.refresh(item)
    assert item.stock_quantity == 7.0
    assert item.reserved_quantity == 0.0


@pytest.mark.asyncio
async def test_cancel_held_bill_releases_reservation(async_client: AsyncClient, db_session):
    """Cancelling a Held bill releases its reservation back to available stock."""
    item = MenuItem(name="Cancel Burger", price=5.0, category="Food", shop_id=1, stock_quantity=10)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login_owner(async_client)

    hold_payload = {"items": [{"id": item.id, "qty": 4}], "payment_method": "Cash", "status": "Held"}
    hold_res = await async_client.post("/billing/create", json=hold_payload)
    assert hold_res.status_code == 200
    bill_slug = hold_res.json()["bill_id"]

    await db_session.refresh(item)
    assert item.reserved_quantity == 4.0

    cancel_res = await async_client.post(f"/billing/cancel/{bill_slug}")
    assert cancel_res.status_code == 200, cancel_res.text
    data = cancel_res.json()
    assert data["status"] == "success"

    updated = {u["id"]: u["available_stock"] for u in data["updated_items"]}
    assert updated[item.id] == 10.0  # reservation fully released

    await db_session.refresh(item)
    assert item.reserved_quantity == 0.0
    assert item.stock_quantity == 10.0
