"""
Full functional coverage for the Credit Book: due-date boundary conditions
for every payment-term strategy (weekly/monthly/net_days), and full vs
partial settlement both before and after the due date.

tests/test_credit_bills.py already covers bill-creation credit gating,
record_payment's idempotency/overpayment/cross-shop guards, and statement
generation/rollup. This file fills the gaps it doesn't touch:
  - CreditTerms strategy boundary conditions (pure unit tests)
  - CreditService.settle_full_balance (never exercised anywhere else)
  - Partial payment leaving a statement/bill PartiallyPaid, then completed
  - days_overdue / overdue_only boundary exactly at the due date
Payment success/failure is due-date-agnostic by design (nothing in the
service layer blocks or special-cases an overdue payment) — several tests
below assert that explicitly, since it's the kind of implicit assumption
a due-date feature could easily get wrong.
"""
import os
import uuid
import datetime as dt
from datetime import date, timedelta
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Bill, Customer, CreditStatement
from app.domains.credit.service import CreditService
from app.domains.credit.repository import CreditStatementRepository
from app.domains.credit.strategies.weekly_credit_terms import WeeklyCreditTerms
from app.domains.credit.strategies.monthly_credit_terms import MonthlyCreditTerms
from app.domains.credit.strategies.net_days_credit_terms import NetDaysCreditTerms
from app.domains.credit.strategies.factory import get_payment_term_strategy

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


# ============================================================================
# Payment term strategy boundary conditions — pure unit tests, no DB
# ============================================================================

class TestWeeklyCreditTermsBoundaries:
    def test_from_date_is_target_weekday_rolls_to_next_week_not_same_day(self):
        """The strategy's `if days_ahead <= 0: += 7` branch — if a bill lands
        exactly on the target weekday, the due date must NOT be today, it
        must be 7 days out. This is the boundary most likely to regress into
        an off-by-zero (a same-day 'due date' that's already overdue by the
        time the statement is sent)."""
        monday = date(2026, 3, 2)  # a Monday
        terms = WeeklyCreditTerms(day_of_week=0)  # Monday
        assert terms.compute_due_date(monday) == monday + timedelta(days=7)

    def test_from_date_one_day_before_target(self):
        sunday = date(2026, 3, 1)
        terms = WeeklyCreditTerms(day_of_week=0)  # next Monday
        assert terms.compute_due_date(sunday) == date(2026, 3, 2)

    def test_from_date_one_day_after_target(self):
        tuesday = date(2026, 3, 3)
        terms = WeeklyCreditTerms(day_of_week=0)  # Monday just missed
        assert terms.compute_due_date(tuesday) == date(2026, 3, 9)

    def test_wraps_from_sunday_to_monday_target(self):
        sunday = date(2026, 3, 8)  # weekday() == 6
        terms = WeeklyCreditTerms(day_of_week=0)
        assert terms.compute_due_date(sunday) == date(2026, 3, 9)

    def test_next_statement_date_matches_due_date(self):
        terms = WeeklyCreditTerms(day_of_week=3)
        d = date(2026, 3, 2)
        assert terms.compute_next_statement_date(d) == terms.compute_due_date(d)


class TestMonthlyCreditTermsBoundaries:
    def test_always_next_month_even_if_target_day_not_yet_passed(self):
        """day_of_month=20, from_date day=5 — a naive implementation might
        return the 20th of the SAME month since it hasn't passed yet, but
        this strategy always rolls to next month unconditionally."""
        terms = MonthlyCreditTerms(day_of_month=20)
        assert terms.compute_due_date(date(2026, 3, 5)) == date(2026, 4, 20)

    def test_clamps_to_shorter_month_length(self):
        """day_of_month=31 from a January date rolls into February, which
        has no 31st — must clamp to the last real day, not overflow into
        March or raise."""
        terms = MonthlyCreditTerms(day_of_month=31)
        assert terms.compute_due_date(date(2026, 1, 15)) == date(2026, 2, 28)

    def test_clamps_to_feb_29_in_leap_year(self):
        terms = MonthlyCreditTerms(day_of_month=31)
        assert terms.compute_due_date(date(2028, 1, 15)) == date(2028, 2, 29)  # 2028 is a leap year

    def test_clamps_to_feb_28_in_non_leap_year(self):
        terms = MonthlyCreditTerms(day_of_month=30)
        assert terms.compute_due_date(date(2026, 1, 15)) == date(2026, 2, 28)  # 2026 is not a leap year

    def test_december_rolls_to_january_next_year(self):
        terms = MonthlyCreditTerms(day_of_month=10)
        assert terms.compute_due_date(date(2026, 12, 15)) == date(2027, 1, 10)


class TestNetDaysCreditTermsBoundaries:
    def test_zero_days_due_same_day(self):
        terms = NetDaysCreditTerms(days=0)
        d = date(2026, 3, 15)
        assert terms.compute_due_date(d) == d

    def test_one_day(self):
        terms = NetDaysCreditTerms(days=1)
        assert terms.compute_due_date(date(2026, 3, 15)) == date(2026, 3, 16)

    def test_crosses_month_and_year_boundary(self):
        terms = NetDaysCreditTerms(days=15)
        assert terms.compute_due_date(date(2026, 12, 20)) == date(2027, 1, 4)

    # Realistic B2B terms (30/45/60/90 days) spanning multiple months in one
    # jump — the implementation is a plain timedelta add so stdlib date
    # arithmetic guarantees these are correct, but that's an implementation
    # detail: these pin the actual business-facing behavior so a future
    # refactor (e.g. a business-days-only variant) can't silently break a
    # multi-month span while the small-N tests above keep passing.
    def test_45_days_from_mid_january_spans_into_march(self):
        terms = NetDaysCreditTerms(days=45)
        assert terms.compute_due_date(date(2026, 1, 15)) == date(2026, 3, 1)

    def test_30_days_from_january_31_spans_into_march(self):
        terms = NetDaysCreditTerms(days=30)
        assert terms.compute_due_date(date(2026, 1, 31)) == date(2026, 3, 2)

    def test_60_days_from_january_1(self):
        terms = NetDaysCreditTerms(days=60)
        assert terms.compute_due_date(date(2026, 1, 1)) == date(2026, 3, 2)

    def test_90_days_from_january_1_spans_three_months(self):
        terms = NetDaysCreditTerms(days=90)
        assert terms.compute_due_date(date(2026, 1, 1)) == date(2026, 4, 1)

    def test_45_days_spanning_a_leap_year_february(self):
        terms = NetDaysCreditTerms(days=45)
        assert terms.compute_due_date(date(2028, 1, 20)) == date(2028, 3, 5)  # 2028 is a leap year

    def test_45_days_from_november_crosses_year_boundary(self):
        terms = NetDaysCreditTerms(days=45)
        assert terms.compute_due_date(date(2026, 11, 20)) == date(2027, 1, 4)


class TestPaymentTermStrategyFactory:
    def test_unrecognized_term_type_raises(self):
        with pytest.raises(ValueError, match="Unrecognized payment term type"):
            get_payment_term_strategy("fortnightly", 1)

    def test_returns_correct_strategy_class(self):
        assert isinstance(get_payment_term_strategy("weekly", 1), WeeklyCreditTerms)
        assert isinstance(get_payment_term_strategy("monthly", 1), MonthlyCreditTerms)
        assert isinstance(get_payment_term_strategy("net_days", 1), NetDaysCreditTerms)


# ============================================================================
# Helpers
# ============================================================================

async def _make_customer(db_session, *, name, phone, limit, balance, term_type="net_days", term_value=15):
    customer = Customer(
        name=name, phone_number=phone, shop_id=1, is_credit_customer=True,
        credit_limit=limit, credit_balance=balance,
        payment_term_type=term_type, payment_term_value=term_value,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)
    return customer


async def _make_bill(db_session, *, customer, number, total, statement_id=None, paid=Decimal("0.00")):
    bill = Bill(
        bill_number=number, total_amount=Decimal(str(total)), payment_method="Credit",
        status="Completed", payment_status="Paid" if paid >= Decimal(str(total)) else ("PartiallyPaid" if paid > 0 else "Unpaid"),
        amount_paid=paid, items_snapshot=[], shop_id=1, customer_id=customer.id,
        credit_statement_id=statement_id, timestamp=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(bill)
    await db_session.commit()
    await db_session.refresh(bill)
    return bill


async def _make_statement(db_session, *, customer, number, total, due_date, paid=Decimal("0.00"), status=None):
    statement = CreditStatement(
        shop_id=1, customer_id=customer.id, statement_number=number,
        period_start=date.today() - timedelta(days=30), period_end=date.today(),
        total_amount=Decimal(str(total)), amount_paid=paid,
        status=status or ("PartiallyPaid" if paid > 0 else "Open"),
        due_date=due_date,
    )
    db_session.add(statement)
    await db_session.commit()
    await db_session.refresh(statement)
    return statement


# ============================================================================
# CreditService.settle_full_balance — full settlement, before/after due date
# ============================================================================

async def test_settle_full_balance_before_due_date_single_statement(db_session: AsyncSession):
    customer = await _make_customer(db_session, name="BeforeDue Doe", phone="8880001111", limit=500, balance=100)
    statement = await _make_statement(
        db_session, customer=customer, number="CR1C-BD-0001", total=100,
        due_date=date.today() + timedelta(days=5),  # not yet due
    )

    service = CreditService(db_session)
    payments = await service.settle_full_balance(1, customer.id, "Cash", None, None, str(uuid.uuid4()))
    await db_session.commit()

    assert len(payments) == 1
    assert payments[0].amount == Decimal("100.00")

    await db_session.refresh(statement)
    await db_session.refresh(customer)
    assert statement.status == "Paid"
    assert statement.amount_paid == Decimal("100.00")
    assert customer.credit_balance == Decimal("0.00")


async def test_settle_full_balance_after_due_date_overdue_still_succeeds(db_session: AsyncSession):
    """Payment success must not be due-date-gated — an overdue statement is
    just as payable as one that isn't due yet."""
    customer = await _make_customer(db_session, name="AfterDue Doe", phone="8880002222", limit=500, balance=100)
    statement = await _make_statement(
        db_session, customer=customer, number="CR1C-AD-0001", total=100,
        due_date=date.today() - timedelta(days=10),  # 10 days overdue
    )

    service = CreditService(db_session)
    payments = await service.settle_full_balance(1, customer.id, "Cash", None, None, str(uuid.uuid4()))
    await db_session.commit()

    assert len(payments) == 1
    await db_session.refresh(statement)
    await db_session.refresh(customer)
    assert statement.status == "Paid"
    assert customer.credit_balance == Decimal("0.00")


async def test_settle_full_balance_combines_multiple_statements_and_unbilled_bills(db_session: AsyncSession):
    """One call settles every open statement AND every un-statemented
    Completed Credit bill for the customer, in a single action."""
    customer = await _make_customer(db_session, name="Combo Doe", phone="8880003333", limit=1000, balance=300)
    stmt_a = await _make_statement(
        db_session, customer=customer, number="CR1C-CB-0001", total=120,
        due_date=date.today() + timedelta(days=3),
    )
    stmt_b = await _make_statement(
        db_session, customer=customer, number="CR1C-CB-0002", total=80,
        due_date=date.today() - timedelta(days=2),  # overdue
    )
    unbilled = await _make_bill(db_session, customer=customer, number="CB-0001", total=100)

    service = CreditService(db_session)
    payments = await service.settle_full_balance(1, customer.id, "UPI", "full settlement", None, str(uuid.uuid4()))
    await db_session.commit()

    assert len(payments) == 3  # 2 statements + 1 unbilled bill
    total_paid = sum(p.amount for p in payments)
    assert total_paid == Decimal("300.00")

    await db_session.refresh(stmt_a)
    await db_session.refresh(stmt_b)
    await db_session.refresh(unbilled)
    await db_session.refresh(customer)
    assert stmt_a.status == "Paid"
    assert stmt_b.status == "Paid"
    assert unbilled.payment_status == "Paid"
    assert customer.credit_balance == Decimal("0.00")

    # Every CreditPayment got a distinct, valid idempotency_key (uuid5 suffix
    # derivation for the 2nd/3rd payment — the bug this guards against
    # overflowed the String(36) column when a plain string suffix was used).
    keys = {p.idempotency_key for p in payments}
    assert len(keys) == 3
    assert all(len(k) == 36 for k in keys)


async def test_settle_full_balance_raises_when_nothing_outstanding(db_session: AsyncSession):
    customer = await _make_customer(db_session, name="Nothing Doe", phone="8880004444", limit=500, balance=0)

    service = CreditService(db_session)
    with pytest.raises(ValueError, match="No outstanding balance to settle"):
        await service.settle_full_balance(1, customer.id, "Cash", None, None, str(uuid.uuid4()))


async def test_settle_full_balance_after_prior_partial_payment_settles_only_remainder(db_session: AsyncSession):
    """A statement that already received a partial payment must only have
    its remaining outstanding amount settled, not its full original total
    (double-charging the already-paid portion)."""
    customer = await _make_customer(db_session, name="Remainder Doe", phone="8880005555", limit=500, balance=60)
    statement = await _make_statement(
        db_session, customer=customer, number="CR1C-RM-0001", total=100,
        due_date=date.today() + timedelta(days=1), paid=Decimal("40.00"),
    )

    service = CreditService(db_session)
    payments = await service.settle_full_balance(1, customer.id, "Cash", None, None, str(uuid.uuid4()))
    await db_session.commit()

    assert len(payments) == 1
    assert payments[0].amount == Decimal("60.00")  # 100 - 40 already paid

    await db_session.refresh(statement)
    await db_session.refresh(customer)
    assert statement.status == "Paid"
    assert statement.amount_paid == Decimal("100.00")
    assert customer.credit_balance == Decimal("0.00")


# ============================================================================
# Partial settlement — before/after due date
# ============================================================================

async def test_partial_bill_payment_before_due_date_leaves_partially_paid(db_session: AsyncSession):
    customer = await _make_customer(db_session, name="PartialBefore Doe", phone="8880006666", limit=500, balance=100)
    bill = await _make_bill(db_session, customer=customer, number="PB-0001", total=100)
    bill.due_date = dt.datetime.combine(date.today() + timedelta(days=5), dt.time.min, tzinfo=dt.timezone.utc)
    await db_session.commit()

    service = CreditService(db_session)
    payment = await service.record_payment(1, Decimal("40.00"), str(uuid.uuid4()), "Cash", bill_slug=bill.slug)
    await db_session.commit()

    assert payment.amount == Decimal("40.00")
    await db_session.refresh(bill)
    await db_session.refresh(customer)
    assert bill.payment_status == "PartiallyPaid"
    assert bill.amount_paid == Decimal("40.00")
    assert customer.credit_balance == Decimal("60.00")  # 100 - 40 released


async def test_partial_bill_payment_after_due_date_still_allowed(db_session: AsyncSession):
    """An overdue bill can still receive (and be correctly accounted for by)
    a partial payment — due date affects reporting/urgency only, never
    payment eligibility."""
    customer = await _make_customer(db_session, name="PartialAfter Doe", phone="8880007777", limit=500, balance=100)
    bill = await _make_bill(db_session, customer=customer, number="PA-0001", total=100)
    bill.due_date = dt.datetime.combine(date.today() - timedelta(days=7), dt.time.min, tzinfo=dt.timezone.utc)
    await db_session.commit()

    service = CreditService(db_session)
    payment = await service.record_payment(1, Decimal("30.00"), str(uuid.uuid4()), "Cash", bill_slug=bill.slug)
    await db_session.commit()

    assert payment.amount == Decimal("30.00")
    await db_session.refresh(bill)
    assert bill.payment_status == "PartiallyPaid"
    assert bill.amount_paid == Decimal("30.00")


async def test_two_partial_statement_payments_complete_it_and_sync_bills(db_session: AsyncSession):
    """First partial payment leaves the statement (and its bills) open;
    the second partial payment that reaches the full total flips the
    statement to Paid AND syncs every rolled-up bill to Paid."""
    customer = await _make_customer(db_session, name="TwoPart Doe", phone="8880008888", limit=500, balance=150)
    statement = await _make_statement(
        db_session, customer=customer, number="CR1C-TP-0001", total=150,
        due_date=date.today() - timedelta(days=1),  # overdue — must not block payment
    )
    bill = await _make_bill(db_session, customer=customer, number="TP-0001", total=150, statement_id=statement.id)

    service = CreditService(db_session)

    first = await service.record_payment(1, Decimal("90.00"), str(uuid.uuid4()), "Cash", statement_slug=statement.slug)
    await db_session.commit()
    assert first.amount == Decimal("90.00")

    await db_session.refresh(statement)
    await db_session.refresh(bill)
    assert statement.status == "PartiallyPaid"
    assert bill.payment_status == "Unpaid"  # not synced yet — statement isn't fully paid

    second = await service.record_payment(1, Decimal("60.00"), str(uuid.uuid4()), "Cash", statement_slug=statement.slug)
    await db_session.commit()
    assert second.amount == Decimal("60.00")

    await db_session.refresh(statement)
    await db_session.refresh(bill)
    await db_session.refresh(customer)
    assert statement.status == "Paid"
    assert statement.amount_paid == Decimal("150.00")
    assert bill.payment_status == "Paid"  # synced now that the statement is fully settled
    assert bill.amount_paid == Decimal("150.00")
    assert customer.credit_balance == Decimal("0.00")


# ============================================================================
# days_overdue / overdue_only — boundary exactly at the due date
# ============================================================================

async def test_days_overdue_is_zero_when_due_exactly_today(db_session: AsyncSession):
    customer = await _make_customer(db_session, name="DueToday Doe", phone="8880009999", limit=500, balance=50)
    await _make_statement(
        db_session, customer=customer, number="CR1C-DT-0001", total=50, due_date=date.today(),
    )

    repo = CreditStatementRepository(db_session)
    rows = await repo.list_credit_accounts(1, date.today())
    row = next(r for r in rows if r["customer"].id == customer.id)
    assert row["earliest_due_date"] == date.today()
    assert row["days_overdue"] == 0  # due today is NOT overdue


async def test_days_overdue_is_one_when_due_yesterday(db_session: AsyncSession):
    customer = await _make_customer(db_session, name="DueYesterday Doe", phone="8880010000", limit=500, balance=50)
    await _make_statement(
        db_session, customer=customer, number="CR1C-DY-0001", total=50,
        due_date=date.today() - timedelta(days=1),
    )

    repo = CreditStatementRepository(db_session)
    rows = await repo.list_credit_accounts(1, date.today())
    row = next(r for r in rows if r["customer"].id == customer.id)
    assert row["days_overdue"] == 1


async def test_overdue_only_filter_excludes_due_today_includes_due_yesterday(db_session: AsyncSession):
    today_customer = await _make_customer(db_session, name="FilterToday Doe", phone="8880011111", limit=500, balance=50)
    await _make_statement(
        db_session, customer=today_customer, number="CR1C-FT-0001", total=50, due_date=date.today(),
    )
    overdue_customer = await _make_customer(db_session, name="FilterOverdue Doe", phone="8880012222", limit=500, balance=50)
    await _make_statement(
        db_session, customer=overdue_customer, number="CR1C-FO-0001", total=50,
        due_date=date.today() - timedelta(days=1),
    )

    repo = CreditStatementRepository(db_session)
    rows = await repo.list_credit_accounts(1, date.today(), overdue_only=True)
    ids = {r["customer"].id for r in rows}
    assert overdue_customer.id in ids
    assert today_customer.id not in ids


# ============================================================================
# GET /admin/customers/search?include_due_date=true — Customers tab table
# ============================================================================

async def test_customer_search_include_due_date_returns_earliest_due_date(
    db_session: AsyncSession, async_client: AsyncClient,
):
    """The opt-in include_due_date flag must return each credit customer's
    earliest outstanding due date (same rule as the Credit Book tab), and
    null for a customer with no outstanding statement/bill."""
    customer = await _make_customer(db_session, name="TableView Doe", phone="8880013333", limit=500, balance=80)
    due = date.today() + timedelta(days=4)
    await _make_statement(db_session, customer=customer, number="CR1C-TV-0001", total=80, due_date=due)

    no_dues_customer = await _make_customer(db_session, name="NoDues Doe", phone="8880014444", limit=500, balance=0)

    await _login_owner(async_client)
    response = await async_client.get("/admin/customers/search?include_due_date=true&limit=100")
    assert response.status_code == 200
    data = response.json()

    by_id = {c["id"]: c for c in data["customers"]}
    assert by_id[customer.id]["next_due_date"] == due.isoformat()
    assert by_id[no_dues_customer.id]["next_due_date"] is None


async def test_customer_search_without_include_due_date_omits_the_field(
    db_session: AsyncSession, async_client: AsyncClient,
):
    """Callers that don't ask for it (Rate Cards / Credit Book pickers) must
    not pay for or receive the extra due-date query."""
    customer = await _make_customer(db_session, name="NoFlag Doe", phone="8880015555", limit=500, balance=50)
    await _make_statement(db_session, customer=customer, number="CR1C-NF-0001", total=50, due_date=date.today())

    await _login_owner(async_client)
    response = await async_client.get("/admin/customers/search?limit=100")
    assert response.status_code == 200
    data = response.json()

    matched = next(c for c in data["customers"] if c["id"] == customer.id)
    assert "next_due_date" not in matched
