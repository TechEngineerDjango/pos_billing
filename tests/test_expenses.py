import os
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Expense, Shop

pytestmark = pytest.mark.asyncio

from dotenv import load_dotenv
load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
CASHIER_PASSWORD = os.getenv("TEST_CASHIER_PASSWORD")


async def _login(async_client: AsyncClient, username: str, password: str):
    login_res = await async_client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert login_res.status_code == 303


# ============================================================================
# ExpenseService.create_expense / router
# ============================================================================

async def test_create_expense_success(db_session: AsyncSession, async_client: AsyncClient):
    await _login(async_client, "owner", OWNER_PASSWORD)

    payload = {
        "category": "Rent",
        "description": "July rent",
        "amount": "500.00",
        "tax_amount": "0.00",
        "vendor_name": "Landlord Co",
        "expense_date": "2026-07-01",
        "payment_method": "Cash",
    }
    response = await async_client.post("/admin/expenses/add", json=payload)
    assert response.status_code == 200
    data = response.json()["expense"]
    assert data["category"] == "Rent"
    assert data["amount"] == 500.0
    assert data["is_voided"] is False


async def test_create_expense_requires_owner_role(db_session: AsyncSession, async_client: AsyncClient):
    """
    Regression guard: a cashier must not be able to create expense records
    (financial ledger, owner/superadmin only per _require_owner_or_above).
    """
    await _login(async_client, "cashier", CASHIER_PASSWORD)

    payload = {
        "category": "Rent",
        "description": "Attempted by cashier",
        "amount": "10.00",
        "tax_amount": "0.00",
        "expense_date": "2026-07-01",
        "payment_method": "Cash",
    }
    response = await async_client.post("/admin/expenses/add", json=payload)
    assert response.status_code == 403


async def test_create_expense_rejects_non_positive_amount(db_session: AsyncSession, async_client: AsyncClient):
    await _login(async_client, "owner", OWNER_PASSWORD)

    payload = {
        "category": "Rent",
        "description": "Bad amount",
        "amount": "0.00",
        "tax_amount": "0.00",
        "expense_date": "2026-07-01",
        "payment_method": "Cash",
    }
    response = await async_client.post("/admin/expenses/add", json=payload)
    assert response.status_code == 422


# ============================================================================
# ExpenseService.update_expense
# ============================================================================

async def test_update_expense_success(db_session: AsyncSession, async_client: AsyncClient):
    await _login(async_client, "owner", OWNER_PASSWORD)

    expense = Expense(
        shop_id=1, category="Utilities", description="Electricity",
        amount=Decimal("100.00"), tax_amount=Decimal("0.00"),
        expense_date=date(2026, 7, 1), payment_method="Cash",
    )
    db_session.add(expense)
    await db_session.commit()
    await db_session.refresh(expense)

    payload = {
        "category": "Utilities",
        "description": "Electricity (corrected)",
        "amount": "120.00",
        "tax_amount": "0.00",
        "expense_date": "2026-07-01",
        "payment_method": "Cash",
    }
    response = await async_client.post(f"/admin/expenses/update/{expense.slug}", json=payload)
    assert response.status_code == 200
    assert response.json()["expense"]["amount"] == 120.0


async def test_update_voided_expense_rejected(db_session: AsyncSession, async_client: AsyncClient):
    """
    Voided expenses are financial-ledger records — never editable, only
    soft-voided (task.yaml FEAT-8 correctness rule).
    """
    await _login(async_client, "owner", OWNER_PASSWORD)

    expense = Expense(
        shop_id=1, category="Utilities", description="Electricity",
        amount=Decimal("100.00"), tax_amount=Decimal("0.00"),
        expense_date=date(2026, 7, 1), payment_method="Cash", is_voided=True,
    )
    db_session.add(expense)
    await db_session.commit()
    await db_session.refresh(expense)

    payload = {
        "category": "Utilities",
        "description": "Trying to edit a voided record",
        "amount": "1.00",
        "tax_amount": "0.00",
        "expense_date": "2026-07-01",
        "payment_method": "Cash",
    }
    response = await async_client.post(f"/admin/expenses/update/{expense.slug}", json=payload)
    assert response.status_code == 400
    assert "voided" in response.json()["detail"].lower()


# ============================================================================
# ExpenseService.void_expense — soft delete
# ============================================================================

async def test_void_expense_soft_deletes_not_hard_deletes(db_session: AsyncSession, async_client: AsyncClient):
    await _login(async_client, "owner", OWNER_PASSWORD)

    expense = Expense(
        shop_id=1, category="Maintenance", description="AC repair",
        amount=Decimal("50.00"), tax_amount=Decimal("0.00"),
        expense_date=date(2026, 7, 1), payment_method="Cash",
    )
    db_session.add(expense)
    await db_session.commit()
    await db_session.refresh(expense)

    response = await async_client.post(f"/admin/expenses/void/{expense.slug}")
    assert response.status_code == 200
    assert response.json()["expense"]["is_voided"] is True

    await db_session.refresh(expense)
    assert expense.is_voided is True  # row still exists, soft-voided only


async def test_void_expense_not_found_in_other_shop(db_session: AsyncSession, async_client: AsyncClient):
    other_shop = Shop(name="Other Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    foreign_expense = Expense(
        shop_id=other_shop.id, category="Rent", description="Foreign rent",
        amount=Decimal("10.00"), tax_amount=Decimal("0.00"),
        expense_date=date(2026, 7, 1), payment_method="Cash",
    )
    db_session.add(foreign_expense)
    await db_session.commit()
    await db_session.refresh(foreign_expense)

    await _login(async_client, "owner", OWNER_PASSWORD)

    response = await async_client.post(f"/admin/expenses/void/{foreign_expense.slug}")
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()

    await db_session.refresh(foreign_expense)
    assert foreign_expense.is_voided is False  # untouched


# ============================================================================
# ExpenseService.list_expenses — filters, tenant isolation, voided visibility
# ============================================================================

async def test_list_expenses_excludes_voided_by_default(db_session: AsyncSession, async_client: AsyncClient):
    active = Expense(
        shop_id=1, category="Rent", description="Active", amount=Decimal("10.00"),
        tax_amount=Decimal("0.00"), expense_date=date(2026, 7, 1), payment_method="Cash",
    )
    voided = Expense(
        shop_id=1, category="Rent", description="Voided", amount=Decimal("20.00"),
        tax_amount=Decimal("0.00"), expense_date=date(2026, 7, 2), payment_method="Cash",
        is_voided=True,
    )
    db_session.add_all([active, voided])
    await db_session.commit()

    await _login(async_client, "owner", OWNER_PASSWORD)

    response = await async_client.get("/admin/expenses")
    assert response.status_code == 200
    descriptions = {e["description"] for e in response.json()["expenses"]}
    assert descriptions == {"Active"}

    response = await async_client.get("/admin/expenses?include_voided=true")
    descriptions = {e["description"] for e in response.json()["expenses"]}
    assert descriptions == {"Active", "Voided"}


async def test_list_expenses_filters_by_category_and_date_range(db_session: AsyncSession, async_client: AsyncClient):
    rent = Expense(
        shop_id=1, category="Rent", description="July rent", amount=Decimal("500.00"),
        tax_amount=Decimal("0.00"), expense_date=date(2026, 7, 1), payment_method="Cash",
    )
    marketing = Expense(
        shop_id=1, category="Marketing", description="Ads", amount=Decimal("50.00"),
        tax_amount=Decimal("0.00"), expense_date=date(2026, 7, 10), payment_method="Cash",
    )
    old_rent = Expense(
        shop_id=1, category="Rent", description="June rent", amount=Decimal("500.00"),
        tax_amount=Decimal("0.00"), expense_date=date(2026, 6, 1), payment_method="Cash",
    )
    db_session.add_all([rent, marketing, old_rent])
    await db_session.commit()

    await _login(async_client, "owner", OWNER_PASSWORD)

    response = await async_client.get("/admin/expenses?category=Rent")
    descriptions = {e["description"] for e in response.json()["expenses"]}
    assert descriptions == {"July rent", "June rent"}

    response = await async_client.get("/admin/expenses?date_from=2026-07-01&date_to=2026-07-31")
    descriptions = {e["description"] for e in response.json()["expenses"]}
    assert descriptions == {"July rent", "Ads"}

    response = await async_client.get("/admin/expenses?category=Rent&date_from=2026-07-01")
    descriptions = {e["description"] for e in response.json()["expenses"]}
    assert descriptions == {"July rent"}


async def test_list_expenses_tenant_isolation(db_session: AsyncSession, async_client: AsyncClient):
    other_shop = Shop(name="Other Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    own = Expense(
        shop_id=1, category="Rent", description="Mine", amount=Decimal("10.00"),
        tax_amount=Decimal("0.00"), expense_date=date(2026, 7, 1), payment_method="Cash",
    )
    foreign = Expense(
        shop_id=other_shop.id, category="Rent", description="Not mine", amount=Decimal("10.00"),
        tax_amount=Decimal("0.00"), expense_date=date(2026, 7, 1), payment_method="Cash",
    )
    db_session.add_all([own, foreign])
    await db_session.commit()

    await _login(async_client, "owner", OWNER_PASSWORD)

    response = await async_client.get("/admin/expenses")
    descriptions = {e["description"] for e in response.json()["expenses"]}
    assert descriptions == {"Mine"}
