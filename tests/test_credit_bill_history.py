import os
import datetime as dt
from datetime import timezone, date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Customer, Bill
from app.domains.credit.repository import CreditStatementRepository

pytestmark = pytest.mark.asyncio

from dotenv import load_dotenv
load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")


async def _login_owner(async_client: AsyncClient):
    res = await async_client.post(
        "/auth/login", data={"username": "owner", "password": OWNER_PASSWORD}, follow_redirects=False,
    )
    assert res.status_code == 303


async def _make_credit_customer(db_session, *, name, phone):
    customer = Customer(
        name=name, phone_number=phone, shop_id=1,
        is_credit_customer=True, credit_limit=5000, credit_balance=0,
        payment_term_type="monthly", payment_term_value=1,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)
    return customer


async def _make_bill(db_session, customer, *, amount, bill_number, payment_status="Unpaid",
                      amount_paid=0, days_ago=0):
    bill = Bill(
        bill_number=bill_number, total_amount=Decimal(str(amount)), payment_method="Credit",
        status="Completed", payment_status=payment_status, amount_paid=Decimal(str(amount_paid)),
        items_snapshot=[{"name": "Item", "qty": 1, "price": amount}], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db_session.add(bill)
    await db_session.commit()
    await db_session.refresh(bill)
    return bill


# ============================================================================
# CreditStatementRepository.list_bills_for_customer — repository-level filters
# ============================================================================

async def test_list_bills_for_customer_filters_by_date_range(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Date Filter Cust", phone="9300000001")
    recent = await _make_bill(db_session, customer, amount=100, bill_number="BH-RECENT", days_ago=1)
    old = await _make_bill(db_session, customer, amount=100, bill_number="BH-OLD", days_ago=60)

    repo = CreditStatementRepository(db_session)
    bills, total = await repo.list_bills_for_customer(
        customer.id, date_from=date.today() - timedelta(days=5), date_to=date.today(),
    )
    numbers = [b.bill_number for b in bills]
    assert "BH-RECENT" in numbers
    assert "BH-OLD" not in numbers
    assert total == 1


async def test_list_bills_for_customer_filters_by_amount_range(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Amount Filter Cust", phone="9300000002")
    await _make_bill(db_session, customer, amount=50, bill_number="BH-SMALL")
    await _make_bill(db_session, customer, amount=500, bill_number="BH-LARGE")

    repo = CreditStatementRepository(db_session)
    bills, total = await repo.list_bills_for_customer(customer.id, min_amount=100, max_amount=1000)
    numbers = [b.bill_number for b in bills]
    assert numbers == ["BH-LARGE"]
    assert total == 1


async def test_list_bills_for_customer_filters_by_status(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Status Filter Cust", phone="9300000003")
    await _make_bill(db_session, customer, amount=100, bill_number="BH-PAID", payment_status="Paid", amount_paid=100)
    await _make_bill(db_session, customer, amount=100, bill_number="BH-UNPAID", payment_status="Unpaid")

    repo = CreditStatementRepository(db_session)
    bills, total = await repo.list_bills_for_customer(customer.id, status="Paid")
    numbers = [b.bill_number for b in bills]
    assert numbers == ["BH-PAID"]
    assert total == 1


async def test_list_bills_for_customer_pagination(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Paginate Cust", phone="9300000004")
    for i in range(5):
        await _make_bill(db_session, customer, amount=100, bill_number=f"BH-PAGE-{i}", days_ago=i)

    repo = CreditStatementRepository(db_session)
    page1, total = await repo.list_bills_for_customer(customer.id, limit=2, offset=0)
    page2, total2 = await repo.list_bills_for_customer(customer.id, limit=2, offset=2)

    assert len(page1) == 2
    assert len(page2) == 2
    assert total == 5
    assert total2 == 5
    assert {b.id for b in page1}.isdisjoint({b.id for b in page2})


async def test_list_bills_for_customer_excludes_other_customers(db_session: AsyncSession):
    customer_a = await _make_credit_customer(db_session, name="Bills Cust A", phone="9300000005")
    customer_b = await _make_credit_customer(db_session, name="Bills Cust B", phone="9300000006")
    await _make_bill(db_session, customer_a, amount=100, bill_number="BH-CUSTA")
    await _make_bill(db_session, customer_b, amount=100, bill_number="BH-CUSTB")

    repo = CreditStatementRepository(db_session)
    bills, total = await repo.list_bills_for_customer(customer_a.id)
    numbers = [b.bill_number for b in bills]
    assert numbers == ["BH-CUSTA"]
    assert total == 1


# ============================================================================
# GET /admin/credit/customers/{customer_slug}/bills — router-level checks
# ============================================================================

async def test_bills_endpoint_returns_bills_and_total(db_session: AsyncSession, async_client: AsyncClient):
    customer = await _make_credit_customer(db_session, name="HTTP Bills Cust", phone="9300000007")
    await _make_bill(db_session, customer, amount=100, bill_number="BH-HTTP-0001")

    await _login_owner(async_client)
    response = await async_client.get(f"/admin/credit/customers/{customer.slug}/bills")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["total"] == 1
    assert data["bills"][0]["bill_number"] == "BH-HTTP-0001"


async def test_bills_endpoint_rejects_other_shop_customer(db_session: AsyncSession, async_client: AsyncClient):
    from app.shared.models import Shop
    other_shop = Shop(name="Other Bills Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    customer = Customer(
        name="Foreign Bills Cust", phone_number="9300000008", shop_id=other_shop.id,
        is_credit_customer=True, credit_limit=1000, credit_balance=0,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    await _login_owner(async_client)
    response = await async_client.get(f"/admin/credit/customers/{customer.slug}/bills")
    assert response.status_code == 404


async def test_bills_endpoint_clamps_limit(db_session: AsyncSession, async_client: AsyncClient):
    customer = await _make_credit_customer(db_session, name="Clamp Limit Cust", phone="9300000009")
    await _make_bill(db_session, customer, amount=100, bill_number="BH-CLAMP-0001")

    await _login_owner(async_client)
    response = await async_client.get(f"/admin/credit/customers/{customer.slug}/bills?limit=99999")
    assert response.status_code == 200
