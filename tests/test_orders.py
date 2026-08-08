import os
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.shared.models import Customer, Shop, MenuItem, Bill

pytestmark = pytest.mark.asyncio

from dotenv import load_dotenv
load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")


async def _login_owner(async_client: AsyncClient):
    login_res = await async_client.post(
        "/auth/login",
        data={"username": "owner", "password": OWNER_PASSWORD},
        follow_redirects=False,
    )
    assert login_res.status_code == 303


async def test_delivery_status_defaults_to_pending_and_updatable(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """A new Pay Later bill starts Pending, and each stage is settable
    through POST /billing/orders/{slug}/update."""
    item = MenuItem(name="Order Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Order Doe", phone_number="7778889990", shop_id=1, is_credit_customer=False)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id,
    }
    create_res = await async_client.post("/billing/create", json=payload)
    assert create_res.status_code == 200
    bill_slug = create_res.json()["bill_id"]

    bill_res = await db_session.execute(select(Bill).where(Bill.slug == bill_slug))
    bill = bill_res.scalars().first()
    assert bill.delivery_status == "Pending"

    for stage in ("Out for Delivery", "Delivered"):
        update_res = await async_client.post(
            f"/billing/orders/{bill_slug}/update", json={"delivery_status": stage}
        )
        assert update_res.status_code == 200
        assert update_res.json()["order"]["delivery_status"] == stage

    # mark_paid succeeds for this non-credit customer
    paid_res = await async_client.post(f"/billing/orders/{bill_slug}/update", json={"mark_paid": True})
    assert paid_res.status_code == 200
    assert paid_res.json()["order"]["payment_status"] == "Paid"


async def test_mark_paid_rejected_for_credit_customer(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """A real credit-customer's bill must be paid via /admin/credit/payments
    (keeps the CreditPayment audit trail and credit_balance correct) — the
    lightweight Orders endpoint refuses to mark it paid directly."""
    item = MenuItem(name="Credit Order Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(
        name="Credit Order Doe", phone_number="7778889991", shop_id=1,
        is_credit_customer=True, credit_limit=1000.0, credit_balance=0.0,
    )
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id,
    }
    create_res = await async_client.post("/billing/create", json=payload)
    assert create_res.status_code == 200
    bill_slug = create_res.json()["bill_id"]

    paid_res = await async_client.post(f"/billing/orders/{bill_slug}/update", json={"mark_paid": True})
    assert paid_res.status_code == 400
    assert "Credit Book" in paid_res.json()["detail"]


async def test_orders_list_scoped_to_shop_and_excludes_cancelled(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """GET /billing/orders only returns the caller's own shop's Pay Later
    bills, and excludes Cancelled ones."""
    item = MenuItem(name="Scope Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Scope Doe", phone_number="7778889992", shop_id=1, is_credit_customer=False)
    other_shop = Shop(name="Other Orders Shop", printer_ip="mock")
    db_session.add_all([item, customer, other_shop])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)
    await db_session.refresh(other_shop)

    other_customer = Customer(
        name="Foreign Order Doe", phone_number="7778889993", shop_id=other_shop.id, is_credit_customer=False,
    )
    db_session.add(other_customer)
    await db_session.commit()
    await db_session.refresh(other_customer)

    # Bill belonging to the other shop — inserted directly, never visible to shop 1.
    foreign_bill = Bill(
        bill_number="OTHER-0001", total_amount=25.0, payment_method="Credit", status="Completed",
        payment_status="Unpaid", items_snapshot=[], shop_id=other_shop.id, customer_id=other_customer.id,
    )
    # A Cancelled Pay Later bill on shop 1 — must be excluded even though it's the right shop.
    cancelled_bill = Bill(
        bill_number="SHOP1-CANCELLED", total_amount=15.0, payment_method="Credit", status="Cancelled",
        payment_status="Unpaid", items_snapshot=[], shop_id=1, customer_id=customer.id,
    )
    db_session.add_all([foreign_bill, cancelled_bill])
    await db_session.commit()

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id,
    }
    create_res = await async_client.post("/billing/create", json=payload)
    assert create_res.status_code == 200
    own_bill_number = create_res.json()["bill_number"]

    list_res = await async_client.get("/billing/orders")
    assert list_res.status_code == 200
    bill_numbers = [o["bill_number"] for o in list_res.json()["orders"]]
    assert own_bill_number in bill_numbers
    assert "OTHER-0001" not in bill_numbers
    assert "SHOP1-CANCELLED" not in bill_numbers
