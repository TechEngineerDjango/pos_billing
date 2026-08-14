import os
import datetime as dt
from datetime import timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from dotenv import load_dotenv

from app.shared.models import Bill, Expense, Shop
from app.domains.billing.profit_loss import compute_profit_loss

load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")


async def _get_shop_id(db_session):
    result = await db_session.execute(select(Shop).limit(1))
    shop = result.scalars().first()
    if not shop:
        shop = Shop(name="Test Shop")
        db_session.add(shop)
        await db_session.commit()
        await db_session.refresh(shop)
    return shop.id


@pytest.mark.asyncio
async def test_compute_profit_loss_full_scenario(db_session):
    """Completed bill w/ tax counted, Held/Cancelled excluded (via the
    caller's pre-filtered period_net_revenue — this function trusts what it's
    given for revenue), COGS vs OpEx split, voided expense excluded,
    uncollected credit for a PartiallyPaid bill."""
    shop_id = await _get_shop_id(db_session)
    today = dt.datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = today - dt.timedelta(days=7)
    # UTC date, matching compute_profit_loss's own boundary — not
    # date.today() (system-local), which can legitimately differ from the
    # UTC date by up to a day and would make this test flaky depending on
    # the machine's timezone (exactly the shop-local-vs-UTC edge case
    # documented in profit_loss.py).
    expense_date = today.date()

    db_session.add_all([
        Expense(shop_id=shop_id, category="Inventory Purchase", description="Stock",
                amount=Decimal("40.00"), tax_amount=Decimal("4.00"), expense_date=expense_date,
                payment_method="Cash", is_voided=False),
        Expense(shop_id=shop_id, category="Rent", description="Rent",
                amount=Decimal("20.00"), tax_amount=Decimal("0.00"), expense_date=expense_date,
                payment_method="Cash", is_voided=False),
        Expense(shop_id=shop_id, category="Rent", description="Voided rent — must not count",
                amount=Decimal("999.00"), tax_amount=Decimal("0.00"), expense_date=expense_date,
                payment_method="Cash", is_voided=True),
    ])
    await db_session.commit()

    # Caller (admin_router.py) is responsible for excluding Held/Cancelled
    # and computing net-of-tax revenue + uncollected before calling this —
    # simulate that pre-computation directly here.
    # Completed bill: total 110, tax 10 -> net revenue 100
    # PartiallyPaid completed bill: total 80, paid 30 -> uncollected 50
    period_net_revenue = 100.0 + 80.0
    period_uncollected = 50.0

    result = await compute_profit_loss(
        db_session, shop_id, period_net_revenue, period_uncollected, period_start, today
    )

    assert result["net_revenue"] == 180.0
    assert result["cogs"] == 44.0  # 40 + 4 tax, voided excluded
    assert result["operating_expenses"] == 20.0  # Rent only, voided excluded
    assert result["gross_profit"] == 136.0  # 180 - 44
    assert result["net_profit"] == 116.0  # 136 - 20
    assert result["uncollected_credit"] == 50.0
    assert result["profit_margin"] == round(116.0 / 180.0 * 100, 1)


@pytest.mark.asyncio
async def test_compute_profit_loss_zero_revenue_no_divide_by_zero(db_session):
    shop_id = await _get_shop_id(db_session)
    today = dt.datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = today - dt.timedelta(days=7)

    result = await compute_profit_loss(db_session, shop_id, 0.0, 0.0, period_start, today)

    assert result["net_revenue"] == 0.0
    assert result["cogs"] == 0.0
    assert result["operating_expenses"] == 0.0
    assert result["net_profit"] == 0.0
    assert result["profit_margin"] == 0


@pytest.mark.asyncio
async def test_admin_dashboard_renders_profit_loss_card(async_client: AsyncClient, db_session):
    shop_id = await _get_shop_id(db_session)
    now = dt.datetime.now(timezone.utc)

    db_session.add_all([
        Bill(bill_number="PL-1", total_amount=Decimal("110.00"), tax_amount=Decimal("10.00"),
             status="Completed", payment_status="Paid", amount_paid=Decimal("110.00"),
             items_snapshot=[], shop_id=shop_id, timestamp=now),
        Bill(bill_number="PL-2", total_amount=Decimal("50.00"), tax_amount=Decimal("0.00"),
             status="Held", payment_status="Unpaid", amount_paid=Decimal("0.00"),
             items_snapshot=[], shop_id=shop_id, timestamp=now),
        Bill(bill_number="PL-3", total_amount=Decimal("200.00"), tax_amount=Decimal("0.00"),
             status="Cancelled", payment_status="Unpaid", amount_paid=Decimal("0.00"),
             items_snapshot=[], shop_id=shop_id, timestamp=now),
    ])
    await db_session.commit()

    await async_client.post(
        "/auth/login", data={"username": "owner", "password": OWNER_PASSWORD}, follow_redirects=True
    )

    response = await async_client.get("/admin/?tab=reports&period=7d")
    assert response.status_code == 200
    assert "Internal Server Error" not in response.text
    assert "TemplateSyntaxError" not in response.text
    assert "Profit &amp; Loss" in response.text or "Profit & Loss" in response.text
