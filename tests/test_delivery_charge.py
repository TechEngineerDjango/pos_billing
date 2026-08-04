"""
Manual delivery charge (POS-entered, not tied to any menu item) is added to
the bill's grand total server-side — never taxed, never part of any line
item. These tests cover create_bill, update_bill (editing a Held bill), the
recent-bills listing (used to restore it when resuming a Held bill), and the
negative-value rejection at the schema boundary.
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
from httpx import AsyncClient
from sqlalchemy import select
from app.shared.models import MenuItem, Bill


async def _login(async_client: AsyncClient):
    res = await async_client.post(
        "/auth/login",
        data={"username": "owner", "password": OWNER_PASSWORD},
        follow_redirects=False,
    )
    assert res.status_code == 303


@pytest.mark.asyncio
async def test_create_bill_with_delivery_charge_adds_to_total(async_client: AsyncClient, db_session):
    await _login(async_client)

    item = MenuItem(name="Cheese Burger", price=5.0, category="Food", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    payload = {
        "items": [{"id": item.id, "qty": 2}],
        "payment_method": "Cash",
        "delivery_charge": 25.0,
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 35.0  # 2 * 5.0 items + 25.0 delivery

    bill_res = await db_session.execute(select(Bill).where(Bill.bill_number == data["bill_number"]))
    bill = bill_res.scalars().first()
    assert float(bill.delivery_charge) == 25.0
    assert float(bill.total_amount) == 35.0
    assert float(bill.subtotal_amount) == 10.0  # unaffected by delivery


@pytest.mark.asyncio
async def test_create_bill_without_delivery_charge_defaults_to_zero(async_client: AsyncClient, db_session):
    await _login(async_client)

    item = MenuItem(name="Fries", price=3.0, category="Food", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    payload = {"items": [{"id": item.id, "qty": 1}], "payment_method": "Cash"}
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3.0

    bill_res = await db_session.execute(select(Bill).where(Bill.bill_number == data["bill_number"]))
    bill = bill_res.scalars().first()
    assert float(bill.delivery_charge) == 0.0


@pytest.mark.asyncio
async def test_negative_delivery_charge_rejected(async_client: AsyncClient, db_session):
    await _login(async_client)

    item = MenuItem(name="Soda", price=2.0, category="Food", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Cash",
        "delivery_charge": -10.0,
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_bill_changes_delivery_charge(async_client: AsyncClient, db_session):
    await _login(async_client)

    item = MenuItem(name="Pizza", price=20.0, category="Food", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    create_payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Cash",
        "status": "Held",
        "delivery_charge": 10.0,
    }
    create_res = await async_client.post("/billing/create", json=create_payload)
    assert create_res.status_code == 200
    bill_slug = create_res.json()["bill_id"]

    update_payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Cash",
        "status": "Completed",
        "delivery_charge": 50.0,
    }
    update_res = await async_client.post(f"/billing/update/{bill_slug}", json=update_payload)
    assert update_res.status_code == 200
    update_data = update_res.json()
    assert update_data["total"] == 70.0  # 20.0 item + 50.0 delivery

    bill_res = await db_session.execute(select(Bill).where(Bill.slug == bill_slug))
    bill = bill_res.scalars().first()
    assert float(bill.delivery_charge) == 50.0
    assert float(bill.total_amount) == 70.0


@pytest.mark.asyncio
async def test_recent_bills_includes_delivery_charge_for_resuming_held_bills(async_client: AsyncClient, db_session):
    await _login(async_client)

    item = MenuItem(name="Wrap", price=8.0, category="Food", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Cash",
        "status": "Held",
        "delivery_charge": 15.0,
    }
    create_res = await async_client.post("/billing/create", json=payload)
    assert create_res.status_code == 200
    bill_slug = create_res.json()["bill_id"]

    recent_res = await async_client.get("/billing/recent-bills")
    assert recent_res.status_code == 200
    bills = recent_res.json()["bills"]
    held_bill = next(b for b in bills if b["id"] == bill_slug)
    assert held_bill["delivery_charge"] == 15.0
