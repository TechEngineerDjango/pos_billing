import datetime as dt
import uuid
from datetime import timezone, date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Customer, Bill, CreditStatement
from app.domains.credit.service import CreditService

pytestmark = pytest.mark.asyncio

# CreditPayment.idempotency_key is String(36) — a bare UUID, exactly what the
# browser sends via crypto.randomUUID(). Tests must use real 36-char UUIDs
# here, not short literals like "idem-1": a short literal hides column-length
# bugs in any code that concatenates a suffix onto this value (see
# settle_full_balance, which used to do exactly that and overflowed in prod
# the moment a customer had more than one outstanding bill/statement).


# ============================================================================
# CreditService.settle_full_balance — one-shot payoff of every open statement
# and every un-statemented unpaid Credit bill for a customer.
# ============================================================================

async def _make_credit_customer(db_session, *, name, phone, balance, limit=1000):
    customer = Customer(
        name=name, phone_number=phone, shop_id=1,
        is_credit_customer=True, credit_limit=limit, credit_balance=balance,
        payment_term_type="monthly", payment_term_value=1,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)
    return customer


async def _make_unpaid_bill(db_session, customer, *, amount, bill_number, statement_id=None):
    bill = Bill(
        bill_number=bill_number, total_amount=Decimal(str(amount)), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc), credit_statement_id=statement_id,
    )
    db_session.add(bill)
    await db_session.commit()
    await db_session.refresh(bill)
    return bill


async def test_settle_full_balance_pays_off_unstatemented_bills(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Settle Me", phone="9100000001", balance=300)
    bill_a = await _make_unpaid_bill(db_session, customer, amount=100, bill_number="SFB-0001")
    bill_b = await _make_unpaid_bill(db_session, customer, amount=200, bill_number="SFB-0002")

    service = CreditService(db_session)
    payments = await service.settle_full_balance(1, customer.id, "Cash", None, None, str(uuid.uuid4()))
    await db_session.commit()

    assert len(payments) == 2
    assert sum(p.amount for p in payments) == Decimal("300.00")

    await db_session.refresh(customer)
    assert customer.credit_balance == Decimal("0.00")

    await db_session.refresh(bill_a)
    await db_session.refresh(bill_b)
    assert bill_a.payment_status == "Paid"
    assert bill_a.amount_paid == Decimal("100.00")
    assert bill_b.payment_status == "Paid"
    assert bill_b.amount_paid == Decimal("200.00")


async def test_settle_full_balance_pays_off_open_statement_and_its_bills(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Statement Settle", phone="9100000002", balance=150)
    statement = CreditStatement(
        shop_id=1, customer_id=customer.id, statement_number="SFB-STMT-0001",
        period_start=date.today() - timedelta(days=30), period_end=date.today() - timedelta(days=1),
        total_amount=Decimal("150.00"), amount_paid=Decimal("0.00"), status="Open",
        due_date=date.today() + timedelta(days=5),
    )
    db_session.add(statement)
    await db_session.commit()
    await db_session.refresh(statement)
    bill = await _make_unpaid_bill(db_session, customer, amount=150, bill_number="SFB-STMT-BILL-0001", statement_id=statement.id)

    service = CreditService(db_session)
    payments = await service.settle_full_balance(1, customer.id, "UPI", "Paid in full", None, str(uuid.uuid4()))
    await db_session.commit()

    assert len(payments) == 1
    assert payments[0].amount == Decimal("150.00")
    assert payments[0].credit_statement_id == statement.id
    assert payments[0].payment_method == "UPI"
    assert payments[0].note == "Paid in full"

    await db_session.refresh(statement)
    assert statement.status == "Paid"
    assert statement.amount_paid == Decimal("150.00")

    await db_session.refresh(bill)
    assert bill.payment_status == "Paid"

    await db_session.refresh(customer)
    assert customer.credit_balance == Decimal("0.00")


async def test_settle_full_balance_combines_statement_and_direct_bills(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Combo Settle", phone="9100000003", balance=250)
    statement = CreditStatement(
        shop_id=1, customer_id=customer.id, statement_number="SFB-COMBO-0001",
        period_start=date.today() - timedelta(days=30), period_end=date.today() - timedelta(days=1),
        total_amount=Decimal("100.00"), amount_paid=Decimal("0.00"), status="Open",
        due_date=date.today() + timedelta(days=5),
    )
    db_session.add(statement)
    await db_session.commit()
    await db_session.refresh(statement)
    await _make_unpaid_bill(db_session, customer, amount=100, bill_number="SFB-COMBO-STMT-BILL", statement_id=statement.id)
    await _make_unpaid_bill(db_session, customer, amount=150, bill_number="SFB-COMBO-DIRECT-BILL")

    service = CreditService(db_session)
    payments = await service.settle_full_balance(1, customer.id, "Cash", None, None, str(uuid.uuid4()))
    await db_session.commit()

    assert len(payments) == 2
    assert sum(p.amount for p in payments) == Decimal("250.00")

    await db_session.refresh(customer)
    assert customer.credit_balance == Decimal("0.00")


async def test_settle_full_balance_raises_when_nothing_outstanding(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Nothing Owed", phone="9100000004", balance=0)

    service = CreditService(db_session)
    with pytest.raises(ValueError, match="No outstanding balance"):
        await service.settle_full_balance(1, customer.id, "Cash", None, None, str(uuid.uuid4()))


async def test_settle_full_balance_second_call_after_success_finds_nothing_left(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Retry Settle", phone="9100000005", balance=100)
    await _make_unpaid_bill(db_session, customer, amount=100, bill_number="SFB-RETRY-0001")

    service = CreditService(db_session)
    first = await service.settle_full_balance(1, customer.id, "Cash", None, None, str(uuid.uuid4()))
    await db_session.commit()
    assert len(first) == 1

    # A retry of the same logical request (e.g. a double-tapped button) finds
    # nothing left outstanding rather than double-applying the payment.
    with pytest.raises(ValueError, match="No outstanding balance"):
        await service.settle_full_balance(1, customer.id, "Cash", None, None, str(uuid.uuid4()))


async def test_settle_full_balance_rejects_wrong_shop(db_session: AsyncSession):
    from app.shared.models import Shop
    other_shop = Shop(name="Other Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    customer = await _make_credit_customer(db_session, name="Foreign Settle", phone="9100000006", balance=100)

    service = CreditService(db_session)
    with pytest.raises(ValueError, match="Customer not found"):
        await service.settle_full_balance(other_shop.id, customer.id, "Cash", None, None, str(uuid.uuid4()))
