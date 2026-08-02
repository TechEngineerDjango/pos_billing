import datetime as dt
from datetime import timezone, date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Customer, Shop, Bill, CreditStatement
from app.domains.credit.service import CreditService

pytestmark = pytest.mark.asyncio


# ============================================================================
# CreditService.list_accounts — search, sort, filter, stats for the Credit
# Book overview (replaces the old picker+detail flow per the approved
# urgency-sorted list-view design).
# ============================================================================

async def _make_credit_customer(db_session, *, name, phone, balance, limit, term_type="monthly", term_value=1):
    customer = Customer(
        name=name, phone_number=phone, shop_id=1,
        is_credit_customer=True, credit_limit=limit, credit_balance=balance,
        payment_term_type=term_type, payment_term_value=term_value,
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)
    return customer


async def _make_unpaid_bill(db_session, customer, *, amount, due_date, bill_number):
    bill = Bill(
        bill_number=bill_number, total_amount=Decimal(str(amount)), payment_method="Credit",
        status="Completed", payment_status="Unpaid", amount_paid=Decimal("0.00"),
        items_snapshot=[], shop_id=1, customer_id=customer.id,
        timestamp=dt.datetime.now(timezone.utc),
        due_date=dt.datetime.combine(due_date, dt.time.min, tzinfo=timezone.utc),
    )
    db_session.add(bill)
    await db_session.commit()
    return bill


async def test_list_accounts_search_matches_name_or_phone(db_session: AsyncSession):
    await _make_credit_customer(db_session, name="Raghav Traders", phone="9876543210", balance=1000, limit=5000)
    await _make_credit_customer(db_session, name="Meera Stores", phone="9111122223", balance=500, limit=2000)

    service = CreditService(db_session)
    result = await service.list_accounts(1, q="Raghav")
    assert [a["name"] for a in result["accounts"]] == ["Raghav Traders"]

    result = await service.list_accounts(1, q="9111122223")
    assert [a["name"] for a in result["accounts"]] == ["Meera Stores"]


async def test_list_accounts_excludes_non_credit_customers(db_session: AsyncSession):
    await _make_credit_customer(db_session, name="Credit Cust", phone="9000000001", balance=100, limit=1000)
    non_credit = Customer(name="Cash Cust", phone_number="9000000002", shop_id=1, is_credit_customer=False)
    db_session.add(non_credit)
    await db_session.commit()

    service = CreditService(db_session)
    result = await service.list_accounts(1)
    names = [a["name"] for a in result["accounts"]]
    assert "Credit Cust" in names
    assert "Cash Cust" not in names


async def test_list_accounts_tenant_isolation(db_session: AsyncSession):
    other_shop = Shop(name="Other Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    await _make_credit_customer(db_session, name="Own Shop Cust", phone="9000000003", balance=100, limit=1000)
    foreign = Customer(
        name="Foreign Shop Cust", phone_number="9000000004", shop_id=other_shop.id,
        is_credit_customer=True, credit_limit=1000, credit_balance=100,
    )
    db_session.add(foreign)
    await db_session.commit()

    service = CreditService(db_session)
    result = await service.list_accounts(1)
    names = [a["name"] for a in result["accounts"]]
    assert "Own Shop Cust" in names
    assert "Foreign Shop Cust" not in names


async def test_list_accounts_overdue_only_filter(db_session: AsyncSession):
    overdue_cust = await _make_credit_customer(db_session, name="Overdue Cust", phone="9000000005", balance=300, limit=1000)
    current_cust = await _make_credit_customer(db_session, name="Current Cust", phone="9000000006", balance=300, limit=1000)

    await _make_unpaid_bill(db_session, overdue_cust, amount=300, due_date=date.today() - timedelta(days=5), bill_number="OD-0001")
    await _make_unpaid_bill(db_session, current_cust, amount=300, due_date=date.today() + timedelta(days=5), bill_number="CU-0001")

    service = CreditService(db_session)
    result = await service.list_accounts(1, overdue_only=True)
    names = [a["name"] for a in result["accounts"]]
    assert "Overdue Cust" in names
    assert "Current Cust" not in names

    overdue_row = next(a for a in result["accounts"] if a["name"] == "Overdue Cust")
    assert overdue_row["days_overdue"] == 5


async def test_list_accounts_payment_term_type_filter(db_session: AsyncSession):
    await _make_credit_customer(db_session, name="Weekly Cust", phone="9000000007", balance=100, limit=1000, term_type="weekly", term_value=1)
    await _make_credit_customer(db_session, name="Monthly Cust", phone="9000000008", balance=100, limit=1000, term_type="monthly", term_value=1)

    service = CreditService(db_session)
    result = await service.list_accounts(1, payment_term_type="weekly")
    names = [a["name"] for a in result["accounts"]]
    assert names == ["Weekly Cust"]


async def test_list_accounts_due_date_range_filter(db_session: AsyncSession):
    near_cust = await _make_credit_customer(db_session, name="Near Due Cust", phone="9000000009", balance=200, limit=1000)
    far_cust = await _make_credit_customer(db_session, name="Far Due Cust", phone="9000000010", balance=200, limit=1000)

    await _make_unpaid_bill(db_session, near_cust, amount=200, due_date=date.today() + timedelta(days=2), bill_number="NR-0001")
    await _make_unpaid_bill(db_session, far_cust, amount=200, due_date=date.today() + timedelta(days=40), bill_number="FR-0001")

    service = CreditService(db_session)
    result = await service.list_accounts(1, due_from=date.today(), due_to=date.today() + timedelta(days=10))
    names = [a["name"] for a in result["accounts"]]
    assert names == ["Near Due Cust"]


async def test_list_accounts_sort_by_balance_ascending_and_descending(db_session: AsyncSession):
    await _make_credit_customer(db_session, name="Low Balance", phone="9000000011", balance=100, limit=1000)
    await _make_credit_customer(db_session, name="High Balance", phone="9000000012", balance=900, limit=1000)

    service = CreditService(db_session)

    result = await service.list_accounts(1, sort_by="balance", order="desc")
    assert [a["name"] for a in result["accounts"]] == ["High Balance", "Low Balance"]

    result = await service.list_accounts(1, sort_by="balance", order="asc")
    assert [a["name"] for a in result["accounts"]] == ["Low Balance", "High Balance"]


async def test_list_accounts_sort_by_name(db_session: AsyncSession):
    await _make_credit_customer(db_session, name="Zebra Traders", phone="9000000013", balance=100, limit=1000)
    await _make_credit_customer(db_session, name="Alpha Traders", phone="9000000014", balance=100, limit=1000)

    service = CreditService(db_session)
    result = await service.list_accounts(1, sort_by="name", order="asc")
    assert [a["name"] for a in result["accounts"]] == ["Alpha Traders", "Zebra Traders"]


async def test_list_accounts_sort_by_overdue_days(db_session: AsyncSession):
    a = await _make_credit_customer(db_session, name="Slightly Overdue", phone="9000000015", balance=100, limit=1000)
    b = await _make_credit_customer(db_session, name="Very Overdue", phone="9000000016", balance=100, limit=1000)

    await _make_unpaid_bill(db_session, a, amount=100, due_date=date.today() - timedelta(days=2), bill_number="SO-0001")
    await _make_unpaid_bill(db_session, b, amount=100, due_date=date.today() - timedelta(days=20), bill_number="VO-0001")

    service = CreditService(db_session)
    result = await service.list_accounts(1, sort_by="overdue_days", order="desc")
    assert [a["name"] for a in result["accounts"]] == ["Very Overdue", "Slightly Overdue"]


async def test_list_accounts_utilization_pct_computed(db_session: AsyncSession):
    await _make_credit_customer(db_session, name="Half Used", phone="9000000017", balance=500, limit=1000)

    service = CreditService(db_session)
    result = await service.list_accounts(1)
    row = result["accounts"][0]
    assert row["utilization_pct"] == 50.0


async def test_list_accounts_no_limit_gives_null_utilization(db_session: AsyncSession):
    await _make_credit_customer(db_session, name="No Limit Cust", phone="9000000018", balance=500, limit=None)

    service = CreditService(db_session)
    result = await service.list_accounts(1)
    row = result["accounts"][0]
    assert row["utilization_pct"] is None


async def test_list_accounts_stats_are_shop_wide_not_filtered(db_session: AsyncSession):
    overdue_cust = await _make_credit_customer(db_session, name="Stats Overdue", phone="9000000019", balance=300, limit=1000)
    current_cust = await _make_credit_customer(db_session, name="Stats Current", phone="9000000020", balance=200, limit=1000)
    await _make_unpaid_bill(db_session, overdue_cust, amount=300, due_date=date.today() - timedelta(days=3), bill_number="ST-0001")

    service = CreditService(db_session)
    # Search filters the list, but stats must still reflect all accounts.
    result = await service.list_accounts(1, q="Stats Overdue")
    assert len(result["accounts"]) == 1
    assert result["stats"]["active_accounts"] == 2
    assert result["stats"]["overdue_count"] == 1
    assert result["stats"]["total_outstanding"] == Decimal("500.00")
    assert result["stats"]["overdue_amount"] == Decimal("300.00")


async def test_list_accounts_earliest_due_date_prefers_statement_over_bill(db_session: AsyncSession):
    customer = await _make_credit_customer(db_session, name="Statement Priority Cust", phone="9000000021", balance=400, limit=1000)
    statement = CreditStatement(
        shop_id=1, customer_id=customer.id, statement_number="STMT-0001",
        period_start=date.today() - timedelta(days=30), period_end=date.today() - timedelta(days=1),
        total_amount=Decimal("300.00"), amount_paid=Decimal("0.00"), status="Open",
        due_date=date.today() - timedelta(days=1),
    )
    db_session.add(statement)
    await db_session.commit()
    await _make_unpaid_bill(db_session, customer, amount=100, due_date=date.today() + timedelta(days=30), bill_number="SP-0001")

    service = CreditService(db_session)
    result = await service.list_accounts(1)
    row = next(a for a in result["accounts"] if a["name"] == "Statement Priority Cust")
    assert row["due_date"] == date.today() - timedelta(days=1)
    assert row["days_overdue"] == 1
