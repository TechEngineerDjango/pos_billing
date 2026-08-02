import datetime as dt
from datetime import timezone, date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Customer, Shop, Bill
from app.domains.credit.service import CreditService

pytestmark = pytest.mark.asyncio


# ============================================================================
# CreditService.generate_statements_for_shop — edge cases beyond the basic
# rollup/skip-missing-payment-term coverage in test_credit_bills.py.
# ============================================================================

async def test_generate_statements_for_shop_only_includes_own_shop(db_session: AsyncSession):
    other_shop = Shop(name="Other Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    own_customer = Customer(
        name="Own Shop Doe", phone_number="6660001111", shop_id=1,
        is_credit_customer=True, credit_limit=500.0, credit_balance=0.0,
        payment_term_type="net_days", payment_term_value=15,
    )
    foreign_customer = Customer(
        name="Foreign Shop Doe", phone_number="6660002222", shop_id=other_shop.id,
        is_credit_customer=True, credit_limit=500.0, credit_balance=0.0,
        payment_term_type="net_days", payment_term_value=15,
    )
    db_session.add_all([own_customer, foreign_customer])
    await db_session.commit()
    await db_session.refresh(own_customer)
    await db_session.refresh(foreign_customer)

    own_bill = Bill(
        bill_number="OWN-0001", total_amount=Decimal("30.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=own_customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    foreign_bill = Bill(
        bill_number="FGN-0001", total_amount=Decimal("40.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=other_shop.id, customer_id=foreign_customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    db_session.add_all([own_bill, foreign_bill])
    await db_session.commit()

    service = CreditService(db_session)
    generated = await service.generate_statements_for_shop(1)
    await db_session.commit()

    assert len(generated) == 1
    assert generated[0].customer_id == own_customer.id

    await db_session.refresh(foreign_bill)
    assert foreign_bill.credit_statement_id is None  # untouched by shop 1's run


async def test_generate_statements_for_shop_sums_multiple_bills(db_session: AsyncSession):
    customer = Customer(
        name="Multi Bill Doe", phone_number="6660003333", shop_id=1,
        is_credit_customer=True, credit_limit=500.0, credit_balance=90.0,
        payment_term_type="net_days", payment_term_value=15,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill_a = Bill(
        bill_number="MULTI-0001", total_amount=Decimal("30.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    bill_b = Bill(
        bill_number="MULTI-0002", total_amount=Decimal("60.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    db_session.add_all([bill_a, bill_b])
    await db_session.commit()

    service = CreditService(db_session)
    generated = await service.generate_statements_for_shop(1)
    await db_session.commit()

    assert len(generated) == 1
    assert generated[0].total_amount == Decimal("90.00")


async def test_generate_statements_for_shop_due_date_matches_payment_term(db_session: AsyncSession):
    customer = Customer(
        name="Due Date Doe", phone_number="6660004444", shop_id=1,
        is_credit_customer=True, credit_limit=500.0, credit_balance=0.0,
        payment_term_type="net_days", payment_term_value=15,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill = Bill(
        bill_number="DUE-0001", total_amount=Decimal("30.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    db_session.add(bill)
    await db_session.commit()

    service = CreditService(db_session)
    generated = await service.generate_statements_for_shop(1)
    await db_session.commit()

    statement = generated[0]
    assert statement.due_date == statement.period_end + timedelta(days=15)


async def test_generate_statements_for_shop_sets_next_statement_date_and_gates_reruns(db_session: AsyncSession):
    """
    Multi-cycle boundary: once a customer is billed, next_statement_date is
    pushed into the future, so a bill created before that date is NOT rolled
    into a new statement on an immediate rerun — it waits for the next cycle.
    """
    customer = Customer(
        name="Cycle Doe", phone_number="6660005555", shop_id=1,
        is_credit_customer=True, credit_limit=500.0, credit_balance=0.0,
        payment_term_type="net_days", payment_term_value=15,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill_1 = Bill(
        bill_number="CYCLE-0001", total_amount=Decimal("30.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    db_session.add(bill_1)
    await db_session.commit()

    service = CreditService(db_session)
    first_run = await service.generate_statements_for_shop(1)
    await db_session.commit()
    assert len(first_run) == 1

    await db_session.refresh(customer)
    assert customer.next_statement_date is not None
    assert customer.next_statement_date > date.today()

    # A new bill arrives before the next cycle is due.
    bill_2 = Bill(
        bill_number="CYCLE-0002", total_amount=Decimal("20.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    db_session.add(bill_2)
    await db_session.commit()

    second_run = await service.generate_statements_for_shop(1)
    await db_session.commit()
    assert second_run == []  # not due yet — bill_2 stays unbilled

    await db_session.refresh(bill_2)
    assert bill_2.credit_statement_id is None

    # Simulate the cycle passing.
    customer.next_statement_date = date.today() - timedelta(days=1)
    await db_session.commit()

    third_run = await service.generate_statements_for_shop(1)
    await db_session.commit()
    assert len(third_run) == 1
    assert third_run[0].total_amount == Decimal("20.00")

    await db_session.refresh(bill_2)
    assert bill_2.credit_statement_id == third_run[0].id


async def test_generate_statements_for_shop_returns_empty_when_no_due_customers(db_session: AsyncSession):
    customer = Customer(
        name="Not Due Doe", phone_number="6660006666", shop_id=1,
        is_credit_customer=True, credit_limit=500.0, credit_balance=0.0,
        payment_term_type="net_days", payment_term_value=15,
        next_statement_date=date.today() + timedelta(days=10),
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    bill = Bill(
        bill_number="NOTDUE-0001", total_amount=Decimal("30.00"), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc),
    )
    db_session.add(bill)
    await db_session.commit()

    service = CreditService(db_session)
    generated = await service.generate_statements_for_shop(1)
    assert generated == []

    await db_session.refresh(bill)
    assert bill.credit_statement_id is None
