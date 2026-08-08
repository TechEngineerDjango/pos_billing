import os
import uuid
import datetime as dt
from datetime import timezone, date
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.shared.models import Customer, Shop, MenuItem, Bill, CreditStatement
from app.domains.credit.service import CreditService
from app.domains.customers.service import CustomerService

pytestmark = pytest.mark.asyncio

# Get owner password from env like test_billing_reservation.py
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


# ============================================================================
# CreditHandler.on_finalize — Held-vs-Completed credit_balance correctness
# ============================================================================

async def test_completed_credit_bill_increments_balance_once(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Completed credit bill under limit succeeds and increments credit_balance exactly once.
    """
    item = MenuItem(name="Credit Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="John Doe", phone_number="1234567890", shop_id=1, is_credit_customer=True, credit_limit=100.0, credit_balance=0.0, payment_term_type="weekly", payment_term_value=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 2}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200

    await db_session.refresh(customer)
    assert customer.credit_balance == 20.0  # 2 * 10.0

    result = await db_session.execute(select(Bill).where(Bill.customer_id == customer.id))
    bill = result.scalars().first()
    assert bill is not None
    assert bill.payment_status == "Unpaid"
    assert bill.due_date is not None


async def test_held_credit_bill_does_not_change_balance(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    A Held credit bill does NOT change credit_balance (only a preview warning).
    """
    item = MenuItem(name="Held Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Jane Doe", phone_number="0987654321", shop_id=1, is_credit_customer=True, credit_limit=100.0, credit_balance=0.0)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 3}],
        "payment_method": "Credit",
        "status": "Held",
        "customer_id": customer.id
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200

    await db_session.refresh(customer)
    assert customer.credit_balance == 0.0


async def test_held_to_completed_transition_increments_balance_once(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Held->Completed transition via update_bill increments credit_balance exactly once at that transition, not twice.
    """
    item = MenuItem(name="Transition Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Trans Doe", phone_number="1112223334", shop_id=1, is_credit_customer=True, credit_limit=100.0, credit_balance=0.0)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    hold_payload = {
        "items": [{"id": item.id, "qty": 3}],
        "payment_method": "Credit",
        "status": "Held",
        "customer_id": customer.id
    }
    hold_res = await async_client.post("/billing/create", json=hold_payload)
    assert hold_res.status_code == 200
    bill_slug = hold_res.json()["bill_id"]

    await db_session.refresh(customer)
    assert customer.credit_balance == 0.0

    complete_payload = {
        "items": [{"id": item.id, "qty": 3}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id
    }
    complete_res = await async_client.post(f"/billing/update/{bill_slug}", json=complete_payload)
    assert complete_res.status_code == 200

    await db_session.refresh(customer)
    assert customer.credit_balance == 30.0  # Exactly once


async def test_credit_bill_over_limit_rejected_400_when_shop_blocks(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Over-limit rejected 400 — but only when the shop has opted into
    block_over_credit_limit (the old hard-block behavior, no longer the
    default). See test_credit_bill_over_limit_warns_by_default below for the
    new default.
    """
    shop_res = await db_session.execute(select(Shop).where(Shop.id == 1))
    shop = shop_res.scalars().first()
    shop.block_over_credit_limit = True
    await db_session.commit()

    item = MenuItem(name="Limit Burger", price=100.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Limit Doe", phone_number="2223334445", shop_id=1, is_credit_customer=True, credit_limit=50.0, credit_balance=0.0)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 400
    assert "Credit limit exceeded" in response.json()["detail"]

    await db_session.refresh(customer)
    assert customer.credit_balance == 0.0


async def test_credit_bill_over_limit_warns_by_default(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Over-limit succeeds with a warning by default (shop.block_over_credit_limit
    is False unless the shop owner opts in) — the bill still completes and
    credit_balance still moves, so the customer's real outstanding balance
    stays accurate even though it's over their nominal limit.
    """
    item = MenuItem(name="Warn Limit Burger", price=100.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Warn Limit Doe", phone_number="2223334446", shop_id=1, is_credit_customer=True, credit_limit=50.0, credit_balance=0.0)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["warning"] and "credit limit" in data["warning"]

    await db_session.refresh(customer)
    assert customer.credit_balance == 100.0


async def test_anonymous_customer_credit_rejected_400(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Anonymous customer + Credit rejected 400.
    """
    item = MenuItem(name="Anon Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": None
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 400
    assert "Pay Later requires a registered customer" in response.json()["detail"]


async def test_non_credit_customer_can_pay_later(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Pay Later (payment_method "Credit") is available to any registered
    customer, not just is_credit_customer accounts — it's just an unpaid
    bill to collect on later via the Orders view, no limit/balance tracking.
    """
    item = MenuItem(name="NonCredit Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="NonCredit Doe", phone_number="5556667778", shop_id=1, is_credit_customer=False)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["warning"] is None

    await db_session.refresh(customer)
    assert customer.credit_balance == 0.0  # No credit account — no balance movement

    bill_res = await db_session.execute(select(Bill).where(Bill.slug == data["bill_id"]))
    bill = bill_res.scalars().first()
    assert bill.payment_status == "Unpaid"
    assert bill.delivery_status == "Pending"
    assert bill.due_date is None  # No payment terms configured for a non-credit customer


async def test_create_bill_rejects_customer_id_from_another_shop(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Regression test: create_bill must validate a client-supplied customer_id
    belongs to the bill's own shop before using it for pricing/credit —
    otherwise a forged/guessed cross-shop customer_id lets a bill mutate
    another shop's customer.
    """
    item = MenuItem(name="Tenant Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    other_shop = Shop(name="Other Shop", printer_ip="mock")
    db_session.add_all([item, other_shop])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(other_shop)

    foreign_customer = Customer(
        name="Foreign Doe", phone_number="9990001111", shop_id=other_shop.id,
        is_credit_customer=True, credit_limit=1000.0, credit_balance=0.0,
    )
    db_session.add(foreign_customer)
    await db_session.commit()
    await db_session.refresh(foreign_customer)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Cash",
        "status": "Completed",
        "customer_id": foreign_customer.id,
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 400
    assert "Customer not found" in response.json()["detail"]

    await db_session.refresh(foreign_customer)
    assert foreign_customer.credit_balance == 0.0


async def test_get_billing_customer_search_response(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    GET /billing/customer/search response includes B2B and Credit fields.
    """
    customer = Customer(
        name="Search Doe",
        phone_number="9998887776",
        shop_id=1,
        is_credit_customer=True,
        credit_limit=1000.0,
        credit_balance=250.0,
        payment_term_type="weekly",
        payment_term_value=1,
        company_name="Acme Corp",
        gst_number="22AAAAA0000A1Z5"
    )
    db_session.add(customer)
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/billing/customer/search?query=999888")
    assert response.status_code == 200
    data = response.json()
    assert data["found"] is True

    assert data["is_credit_customer"] is True
    assert data["credit_limit"] == 1000.0
    assert data["credit_balance"] == 250.0
    assert data["payment_term_type"] == "weekly"
    assert data["company_name"] == "Acme Corp"
    assert data["gst_number"] == "22AAAAA0000A1Z5"


async def test_update_bill_credit_limit_exceeded_returns_400_not_500(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Regression test for the update_bill exception-handling bug: previously
    update_bill's generic `except Exception` caught the HTTPException(400)
    raised by the Credit payment handler and replaced it with a 500, hiding
    the real "Credit limit exceeded" message from the cashier. Only
    reproducible when the shop has opted into block_over_credit_limit — it's
    no longer the default (see test_update_bill_credit_limit_warns_by_default).
    """
    shop_res = await db_session.execute(select(Shop).where(Shop.id == 1))
    shop = shop_res.scalars().first()
    shop.block_over_credit_limit = True
    await db_session.commit()

    item = MenuItem(name="Update Limit Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(
        name="Update Limit Doe", phone_number="6667778880", shop_id=1,
        is_credit_customer=True, credit_limit=50.0, credit_balance=0.0,
    )
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    hold_payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Held",
        "customer_id": customer.id,
    }
    hold_res = await async_client.post("/billing/create", json=hold_payload)
    assert hold_res.status_code == 200
    bill_slug = hold_res.json()["bill_id"]

    complete_payload = {
        "items": [{"id": item.id, "qty": 10}],  # 10 * 10.0 = 100.0 > limit 50.0
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id,
    }
    complete_res = await async_client.post(f"/billing/update/{bill_slug}", json=complete_payload)

    assert complete_res.status_code == 400
    assert "Credit limit exceeded" in complete_res.json()["detail"]

    await db_session.refresh(customer)
    assert customer.credit_balance == 0.0


async def test_update_bill_credit_limit_warns_by_default(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Same over-limit scenario as test_update_bill_credit_limit_exceeded_returns_400_not_500,
    but with the shop left at the default block_over_credit_limit=False —
    the update succeeds and returns a warning instead of a 400.
    """
    item = MenuItem(name="Update Warn Limit Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(
        name="Update Warn Limit Doe", phone_number="6667778881", shop_id=1,
        is_credit_customer=True, credit_limit=50.0, credit_balance=0.0,
    )
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    hold_payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Held",
        "customer_id": customer.id,
    }
    hold_res = await async_client.post("/billing/create", json=hold_payload)
    assert hold_res.status_code == 200
    bill_slug = hold_res.json()["bill_id"]

    complete_payload = {
        "items": [{"id": item.id, "qty": 10}],  # 10 * 10.0 = 100.0 > limit 50.0
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id,
    }
    complete_res = await async_client.post(f"/billing/update/{bill_slug}", json=complete_payload)

    assert complete_res.status_code == 200
    data = complete_res.json()
    assert data["warning"] and "credit limit" in data["warning"]

    await db_session.refresh(customer)
    assert customer.credit_balance == 100.0


# ============================================================================
# CreditService.record_payment — statement-level and direct bill-level
# ============================================================================

async def test_record_bill_payment_direct_pays_off_unbilled_bill(db_session: AsyncSession):
    """
    A Completed Credit bill not yet rolled into a statement can be paid off
    directly via bill_slug (task.yaml FEAT-5/6 gap fix).
    """
    customer = Customer(
        name="Direct Pay Doe", phone_number="7770001112", shop_id=1,
        is_credit_customer=True, credit_limit=200.0, credit_balance=50.0,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill = Bill(
        bill_number="DP-0001", total_amount=Decimal("50.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
    )
    db_session.add(bill)
    await db_session.commit()
    await db_session.refresh(bill)

    service = CreditService(db_session)
    payment = await service.record_payment(
        1, Decimal("50.00"), str(uuid.uuid4()), "Cash", bill_slug=bill.slug,
    )
    await db_session.commit()

    assert payment.bill_id == bill.id
    assert payment.credit_statement_id is None

    await db_session.refresh(bill)
    await db_session.refresh(customer)
    assert bill.payment_status == "Paid"
    assert bill.amount_paid == Decimal("50.00")
    assert customer.credit_balance == Decimal("0.00")


async def test_record_statement_payment_marks_all_bills_paid_when_settled(db_session: AsyncSession):
    """
    Once a CreditStatement is fully paid, every Bill rolled into it is
    also marked Paid (previously this sync never happened, so the ledger's
    unpaid_bills view kept showing settled bills as outstanding forever).
    """
    customer = Customer(
        name="Statement Pay Doe", phone_number="7770002223", shop_id=1,
        is_credit_customer=True, credit_limit=500.0, credit_balance=150.0,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill_a = Bill(
        bill_number="SP-0001", total_amount=Decimal("70.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
    )
    bill_b = Bill(
        bill_number="SP-0002", total_amount=Decimal("80.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
    )
    db_session.add_all([bill_a, bill_b])
    await db_session.commit()
    await db_session.refresh(bill_a)
    await db_session.refresh(bill_b)

    statement = CreditStatement(
        shop_id=1, customer_id=customer.id, statement_number="CR1C1-202607-0001",
        period_start=date.today(), period_end=date.today(),
        total_amount=Decimal("150.00"), amount_paid=Decimal("0.00"), status="Open",
    )
    db_session.add(statement)
    await db_session.commit()
    await db_session.refresh(statement)

    bill_a.credit_statement_id = statement.id
    bill_b.credit_statement_id = statement.id
    await db_session.commit()

    service = CreditService(db_session)
    await service.record_payment(
        1, Decimal("150.00"), str(uuid.uuid4()), "Cash", statement_slug=statement.slug,
    )
    await db_session.commit()

    await db_session.refresh(statement)
    await db_session.refresh(bill_a)
    await db_session.refresh(bill_b)
    await db_session.refresh(customer)

    assert statement.status == "Paid"
    assert bill_a.payment_status == "Paid"
    assert bill_a.amount_paid == Decimal("70.00")
    assert bill_b.payment_status == "Paid"
    assert bill_b.amount_paid == Decimal("80.00")
    assert customer.credit_balance == Decimal("0.00")


async def test_record_payment_is_idempotent(db_session: AsyncSession):
    """
    A repeated idempotency_key returns the original payment unchanged
    instead of double-applying it against the balance.
    """
    customer = Customer(
        name="Idempotent Doe", phone_number="7770003334", shop_id=1,
        is_credit_customer=True, credit_limit=200.0, credit_balance=50.0,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill = Bill(
        bill_number="IDEM-0001", total_amount=Decimal("50.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
    )
    db_session.add(bill)
    await db_session.commit()
    await db_session.refresh(bill)

    key = str(uuid.uuid4())
    service = CreditService(db_session)
    first = await service.record_payment(1, Decimal("50.00"), key, "Cash", bill_slug=bill.slug)
    await db_session.commit()

    second = await service.record_payment(1, Decimal("50.00"), key, "Cash", bill_slug=bill.slug)
    await db_session.commit()

    assert first.id == second.id

    await db_session.refresh(customer)
    assert customer.credit_balance == Decimal("0.00")  # not double-decremented


async def test_record_payment_rejects_overpayment(db_session: AsyncSession):
    customer = Customer(
        name="Overpay Doe", phone_number="7770004445", shop_id=1,
        is_credit_customer=True, credit_limit=200.0, credit_balance=30.0,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill = Bill(
        bill_number="OVER-0001", total_amount=Decimal("30.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
    )
    db_session.add(bill)
    await db_session.commit()
    await db_session.refresh(bill)

    service = CreditService(db_session)
    with pytest.raises(ValueError, match="exceeds outstanding balance"):
        await service.record_payment(1, Decimal("999.00"), str(uuid.uuid4()), "Cash", bill_slug=bill.slug)


async def test_record_payment_requires_exactly_one_target(db_session: AsyncSession):
    service = CreditService(db_session)
    with pytest.raises(ValueError, match="exactly one"):
        await service.record_payment(1, Decimal("10.00"), str(uuid.uuid4()), "Cash")
    with pytest.raises(ValueError, match="exactly one"):
        await service.record_payment(
            1, Decimal("10.00"), str(uuid.uuid4()), "Cash",
            statement_slug="fake-statement", bill_slug="fake-bill",
        )


async def test_record_payment_rejects_statement_from_another_shop(db_session: AsyncSession):
    """
    Regression test: record_payment must scope the statement lookup to the
    caller's shop_id — previously a slug from any shop was accepted, letting
    one shop pay down (and mutate the credit balance of) another shop's
    customer.
    """
    other_shop = Shop(name="Cross Tenant Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    foreign_customer = Customer(
        name="Foreign Statement Doe", phone_number="7770005556", shop_id=other_shop.id,
        is_credit_customer=True, credit_limit=200.0, credit_balance=40.0,
    )
    db_session.add(foreign_customer)
    await db_session.commit()
    await db_session.refresh(foreign_customer)

    statement = CreditStatement(
        shop_id=other_shop.id, customer_id=foreign_customer.id,
        statement_number="CR2C1-202607-0001", period_start=date.today(), period_end=date.today(),
        total_amount=Decimal("40.00"), amount_paid=Decimal("0.00"), status="Open",
    )
    db_session.add(statement)
    await db_session.commit()
    await db_session.refresh(statement)

    service = CreditService(db_session)
    with pytest.raises(ValueError, match="Statement not found"):
        await service.record_payment(
            1, Decimal("40.00"), str(uuid.uuid4()), "Cash", statement_slug=statement.slug,
        )

    await db_session.refresh(foreign_customer)
    assert foreign_customer.credit_balance == Decimal("40.00")  # untouched


# ============================================================================
# CreditService.generate_statements_for_shop
# ============================================================================

async def test_generate_statements_for_shop_rolls_up_unbilled_bills(db_session: AsyncSession):
    customer = Customer(
        name="Rollup Doe", phone_number="7770006667", shop_id=1,
        is_credit_customer=True, credit_limit=500.0, credit_balance=45.0,
        payment_term_type="net_days", payment_term_value=15,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill = Bill(
        bill_number="ROLL-0001", total_amount=Decimal("45.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    db_session.add(bill)
    await db_session.commit()
    await db_session.refresh(bill)

    service = CreditService(db_session)
    generated = await service.generate_statements_for_shop(1)
    await db_session.commit()

    assert len(generated) == 1
    statement = generated[0]
    assert statement.total_amount == Decimal("45.00")

    await db_session.refresh(bill)
    assert bill.credit_statement_id == statement.id

    # Idempotent: calling again finds no remaining unbilled bills.
    second_run = await service.generate_statements_for_shop(1)
    assert second_run == []


async def test_generate_statements_for_shop_skips_customer_missing_payment_term(db_session: AsyncSession):
    """
    Regression test: a credit customer with is_credit_customer=True but no
    payment_term_type/value set (pre-existing bad data, or created before the
    schema validation existed) must be skipped, not crash the whole run.
    """
    customer = Customer(
        name="No Terms Doe", phone_number="7770007778", shop_id=1,
        is_credit_customer=True, credit_limit=200.0, credit_balance=25.0,
        payment_term_type=None, payment_term_value=None,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill = Bill(
        bill_number="NOTERM-0001", total_amount=Decimal("25.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    db_session.add(bill)
    await db_session.commit()

    service = CreditService(db_session)
    generated = await service.generate_statements_for_shop(1)  # must not raise
    await db_session.commit()

    assert generated == []


# ============================================================================
# CustomerCreditTermsUpdate schema — cross-field validation
# ============================================================================

async def test_credit_terms_requires_payment_term_type_when_credit_customer(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    customer = Customer(name="Schema Doe", phone_number="7770008889", shop_id=1)
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    await _login_owner(async_client)

    response = await async_client.post(
        f"/admin/customers/{customer.slug}/credit-terms",
        data={"is_credit_customer": "true", "credit_limit": "100.00"},
    )
    assert response.status_code == 422


# ============================================================================
# Rate-card pricing (FEAT-3) — race condition and tenant isolation
# ============================================================================

async def test_set_item_price_duplicate_valid_from_rejected(db_session: AsyncSession):
    item = MenuItem(name="Rate Card Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Rate Card Doe", phone_number="7770009990", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    await service.set_item_price(customer.id, item.id, 1, Decimal("8.00"), date(2026, 1, 1), None, None)
    await db_session.commit()

    with pytest.raises(ValueError, match="already starts on this exact date"):
        await service.set_item_price(customer.id, item.id, 1, Decimal("7.50"), date(2026, 1, 1), None, None)


async def test_add_item_price_rejects_menu_item_from_another_shop(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    Regression test: add_item_price must validate menu_item_id belongs to
    the caller's own shop before attaching a rate-card row to it.
    """
    other_shop = Shop(name="Other Menu Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    foreign_item = MenuItem(name="Foreign Item", price=10.0, category="Food", shop_id=other_shop.id, stock_quantity=100)
    customer = Customer(name="Cross Shop Rate Doe", phone_number="7770001119", shop_id=1)
    db_session.add_all([foreign_item, customer])
    await db_session.commit()
    await db_session.refresh(foreign_item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    response = await async_client.post(
        f"/admin/customers/{customer.slug}/prices",
        json={
            "menu_item_id": foreign_item.id,
            "price": 5.00,
            "valid_from": "2026-01-01",
        },
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()
