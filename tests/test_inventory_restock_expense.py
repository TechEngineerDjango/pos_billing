"""
Restocking an inventory item with a unit_cost auto-creates a matching
"Inventory Purchase" expense entry, in the same transaction as the stock
movement. Omitting unit_cost restocks without touching the expenses ledger
(existing manual/scanner restock flows that don't supply a cost keep working
unchanged).
"""
import os
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.shared.models import Expense, Feature, MenuItem, PlanFeature, Shop, StockMovement, Subscription, User
from app.domains.auth.router import get_password_hash

pytestmark = pytest.mark.asyncio

from dotenv import load_dotenv
load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")


async def _create_shop_with_inventory_feature(db_session: AsyncSession, *, username: str) -> Shop:
    """Isolated Shop + Subscription (M2M-linked to inventory_management and
    pos_basic) + owner User — the restock endpoint is feature-gated and
    shop_id=1's default fixture doesn't grant inventory_management.
    conftest.py's Feature catalog seed doesn't include inventory_management
    either, so create it here if it isn't already present."""
    feat_res = await db_session.execute(
        select(Feature).where(Feature.key.in_(["inventory_management", "pos_basic"]))
    )
    features = list(feat_res.scalars().all())
    existing_keys = {f.key for f in features}
    for key in ["inventory_management", "pos_basic"]:
        if key not in existing_keys:
            new_feature = Feature(key=key, name=key)
            db_session.add(new_feature)
            await db_session.commit()
            await db_session.refresh(new_feature)
            features.append(new_feature)

    sub = Subscription(name=f"Sub for {username}")
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    db_session.add_all([PlanFeature(plan_id=sub.id, feature_id=f.id) for f in features])
    await db_session.commit()

    shop = Shop(name=f"Shop for {username}", printer_ip="mock", subscription_id=sub.id)
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)

    owner = User(
        username=username, hashed_password=get_password_hash(OWNER_PASSWORD),
        role="owner", shop_id=shop.id,
    )
    db_session.add(owner)
    await db_session.commit()

    return shop


async def _login(async_client: AsyncClient, username: str):
    login_res = await async_client.post(
        "/auth/login", data={"username": username, "password": OWNER_PASSWORD}, follow_redirects=False,
    )
    assert login_res.status_code == 303


async def test_restock_with_unit_cost_creates_inventory_purchase_expense(
    db_session: AsyncSession, async_client: AsyncClient,
):
    shop = await _create_shop_with_inventory_feature(db_session, username="restock_owner_1")
    item = MenuItem(name="Flour Sack", price=50.0, category="Ingredients", shop_id=shop.id, stock_quantity=10)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login(async_client, "restock_owner_1")

    response = await async_client.post(
        f"/admin/inventory/{item.id}/restock",
        data={"qty": "5", "unit_cost": "120.00", "payment_method": "UPI", "note": "delivery"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    await db_session.refresh(item)
    assert item.stock_quantity == 15  # 10 + 5

    exp_res = await db_session.execute(select(Expense).where(Expense.shop_id == shop.id))
    expense = exp_res.scalars().first()
    assert expense is not None
    assert expense.category == "Inventory Purchase"
    assert expense.amount == Decimal("600.00")  # 5 * 120.00
    assert expense.payment_method == "UPI"
    assert expense.is_voided is False
    assert "Flour Sack" in expense.description


async def test_restock_without_unit_cost_creates_no_expense(
    db_session: AsyncSession, async_client: AsyncClient,
):
    shop = await _create_shop_with_inventory_feature(db_session, username="restock_owner_2")
    item = MenuItem(name="Sugar Bag", price=40.0, category="Ingredients", shop_id=shop.id, stock_quantity=0)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login(async_client, "restock_owner_2")

    response = await async_client.post(
        f"/admin/inventory/{item.id}/restock",
        data={"qty": "8", "note": "no cost given"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    await db_session.refresh(item)
    assert item.stock_quantity == 8

    exp_res = await db_session.execute(select(Expense).where(Expense.shop_id == shop.id))
    assert exp_res.scalars().first() is None

    # Stock movement is still recorded regardless of cost being provided.
    mv_res = await db_session.execute(select(StockMovement).where(StockMovement.menu_item_id == item.id))
    assert mv_res.scalars().first() is not None


async def test_restock_expense_amount_rounds_to_two_decimals(
    db_session: AsyncSession, async_client: AsyncClient,
):
    shop = await _create_shop_with_inventory_feature(db_session, username="restock_owner_3")
    item = MenuItem(name="Rice Bag", price=30.0, category="Ingredients", shop_id=shop.id, stock_quantity=0)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login(async_client, "restock_owner_3")

    response = await async_client.post(
        f"/admin/inventory/{item.id}/restock",
        data={"qty": "3", "unit_cost": "10.005"},  # 3 * 10.005 = 30.015 -> rounds to 30.02 (ROUND_HALF_UP)
        follow_redirects=False,
    )
    assert response.status_code == 303

    exp_res = await db_session.execute(select(Expense).where(Expense.shop_id == shop.id))
    expense = exp_res.scalars().first()
    assert expense.amount == Decimal("30.02")
    assert expense.payment_method == "Cash"  # default when not specified


async def test_restock_expense_captures_tax_amount(
    db_session: AsyncSession, async_client: AsyncClient,
):
    shop = await _create_shop_with_inventory_feature(db_session, username="restock_owner_4")
    item = MenuItem(name="Oil Can", price=200.0, category="Ingredients", shop_id=shop.id, stock_quantity=0)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login(async_client, "restock_owner_4")

    response = await async_client.post(
        f"/admin/inventory/{item.id}/restock",
        data={"qty": "2", "unit_cost": "150.00", "tax_amount": "27.00", "payment_method": "Cash"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    exp_res = await db_session.execute(select(Expense).where(Expense.shop_id == shop.id))
    expense = exp_res.scalars().first()
    assert expense is not None
    assert expense.amount == Decimal("300.00")  # 2 * 150.00, tax not multiplied by qty
    assert expense.tax_amount == Decimal("27.00")


async def test_restock_expense_tax_defaults_to_zero_when_omitted(
    db_session: AsyncSession, async_client: AsyncClient,
):
    shop = await _create_shop_with_inventory_feature(db_session, username="restock_owner_5")
    item = MenuItem(name="Salt Bag", price=15.0, category="Ingredients", shop_id=shop.id, stock_quantity=0)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login(async_client, "restock_owner_5")

    response = await async_client.post(
        f"/admin/inventory/{item.id}/restock",
        data={"qty": "4", "unit_cost": "15.00"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    exp_res = await db_session.execute(select(Expense).where(Expense.shop_id == shop.id))
    expense = exp_res.scalars().first()
    assert expense.tax_amount == Decimal("0.00")


async def test_restock_blocked_without_inventory_feature(
    db_session: AsyncSession, async_client: AsyncClient,
):
    """Regression guard: shop_id=1's default fixture has no
    inventory_management grant — the endpoint's feature gate must still
    reject it (proves the gate wasn't accidentally bypassed by this change)."""
    item = MenuItem(name="Gate Check Item", price=20.0, category="Food", shop_id=1, stock_quantity=0)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    login_res = await async_client.post(
        "/auth/login", data={"username": "owner", "password": OWNER_PASSWORD}, follow_redirects=False,
    )
    assert login_res.status_code == 303

    response = await async_client.post(
        f"/admin/inventory/{item.id}/restock",
        data={"qty": "1", "unit_cost": "5.00"},
        follow_redirects=False,
    )
    assert response.status_code == 403
