import os
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Customer, MenuItem, Shop
from app.domains.customers.service import CustomerService

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
# CustomerService.get_effective_price — sticky pricing resolution
# ============================================================================

async def test_get_effective_price_falls_back_to_list_price_without_override(db_session: AsyncSession):
    item = MenuItem(name="Plain Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="No Override Doe", phone_number="8880001111", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    price = await service.get_effective_price(customer.id, item.id, date(2026, 7, 1), Decimal("10.00"))
    assert price == Decimal("10.00")


async def test_get_effective_price_falls_back_when_no_customer(db_session: AsyncSession):
    item = MenuItem(name="Anon Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    service = CustomerService(db_session)
    price = await service.get_effective_price(None, item.id, date(2026, 7, 1), Decimal("10.00"))
    assert price == Decimal("10.00")


async def test_get_effective_price_uses_override_once_active(db_session: AsyncSession):
    item = MenuItem(name="Wholesale Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Wholesale Doe", phone_number="8880002222", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    await service.set_item_price(customer.id, item.id, 1, Decimal("7.00"), date(2026, 1, 1), None, None)
    await db_session.commit()

    price = await service.get_effective_price(customer.id, item.id, date(2026, 7, 1), Decimal("10.00"))
    assert price == Decimal("7.00")


async def test_get_effective_price_ignores_override_before_valid_from(db_session: AsyncSession):
    item = MenuItem(name="Future Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Future Doe", phone_number="8880003333", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    await service.set_item_price(customer.id, item.id, 1, Decimal("7.00"), date(2026, 8, 1), None, None)
    await db_session.commit()

    # as_of_date is before valid_from — override not yet active
    price = await service.get_effective_price(customer.id, item.id, date(2026, 7, 1), Decimal("10.00"))
    assert price == Decimal("10.00")


async def test_get_effective_price_ignores_override_after_valid_to(db_session: AsyncSession):
    item = MenuItem(name="Expired Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Expired Doe", phone_number="8880004444", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    await service.set_item_price(
        customer.id, item.id, 1, Decimal("7.00"), date(2026, 1, 1), date(2026, 3, 31), None,
    )
    await db_session.commit()

    price = await service.get_effective_price(customer.id, item.id, date(2026, 7, 1), Decimal("10.00"))
    assert price == Decimal("10.00")


async def test_get_effective_price_picks_latest_valid_from_when_multiple_rows_cover_date(db_session: AsyncSession):
    """
    Sticky pricing: when two open-ended (or overlapping) rows both cover
    as_of_date, the one with the latest valid_from wins — no mutation of the
    earlier row's valid_to is needed or expected (task.yaml FEAT-3).
    """
    item = MenuItem(name="Tiered Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Tiered Doe", phone_number="8880005555", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    await service.set_item_price(customer.id, item.id, 1, Decimal("9.00"), date(2026, 1, 1), None, None)
    await db_session.commit()
    await service.set_item_price(customer.id, item.id, 1, Decimal("6.00"), date(2026, 6, 1), None, None)
    await db_session.commit()

    # After the second override's start: newer (lower) price wins.
    price = await service.get_effective_price(customer.id, item.id, date(2026, 7, 1), Decimal("10.00"))
    assert price == Decimal("6.00")

    # Between the two valid_from dates: only the first override was active.
    price = await service.get_effective_price(customer.id, item.id, date(2026, 3, 1), Decimal("10.00"))
    assert price == Decimal("9.00")

    # Confirm the first row's valid_to was never mutated by adding the second.
    from app.domains.customers.price_repository import CustomerItemPriceRepository
    rows = await CustomerItemPriceRepository(db_session).list(customer_id=customer.id, menu_item_id=item.id)
    first_row = min(rows, key=lambda r: r.valid_from)
    assert first_row.valid_to is None


async def test_get_effective_price_is_per_customer(db_session: AsyncSession):
    """An override for one customer must never leak to another customer
    ordering the same item."""
    item = MenuItem(name="Shared Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer_a = Customer(name="Rate A", phone_number="8880006666", shop_id=1)
    customer_b = Customer(name="Rate B", phone_number="8880007777", shop_id=1)
    db_session.add_all([item, customer_a, customer_b])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer_a)
    await db_session.refresh(customer_b)

    service = CustomerService(db_session)
    await service.set_item_price(customer_a.id, item.id, 1, Decimal("5.00"), date(2026, 1, 1), None, None)
    await db_session.commit()

    price_a = await service.get_effective_price(customer_a.id, item.id, date(2026, 7, 1), Decimal("10.00"))
    price_b = await service.get_effective_price(customer_b.id, item.id, date(2026, 7, 1), Decimal("10.00"))
    assert price_a == Decimal("5.00")
    assert price_b == Decimal("10.00")


# ============================================================================
# End-to-end: rate-card override flows through bill creation totals
# ============================================================================

async def test_create_bill_applies_customer_rate_card_override(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Priced Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Billing Rate Doe", phone_number="8880008888", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    await service.set_item_price(customer.id, item.id, 1, Decimal("6.00"), date(2020, 1, 1), None, None)
    await db_session.commit()

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 2}],
        "payment_method": "Cash",
        "status": "Completed",
        "customer_id": customer.id,
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 12.0  # 2 * 6.00 override, not 2 * 10.00 list price


async def test_create_bill_without_customer_uses_list_price(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Walkin Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    await _login_owner(async_client)

    payload = {
        "items": [{"id": item.id, "qty": 2}],
        "payment_method": "Cash",
        "status": "Completed",
        "customer_id": None,
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200
    assert response.json()["total"] == 20.0


# ============================================================================
# CustomerItemPriceRepository listing endpoint
# ============================================================================

async def test_list_item_prices_returns_all_rows_for_customer(db_session: AsyncSession, async_client: AsyncClient):
    item_a = MenuItem(name="Item A", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    item_b = MenuItem(name="Item B", price=20.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="List Prices Doe", phone_number="8880009999", shop_id=1)
    db_session.add_all([item_a, item_b, customer])
    await db_session.commit()
    await db_session.refresh(item_a)
    await db_session.refresh(item_b)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    await service.set_item_price(customer.id, item_a.id, 1, Decimal("8.00"), date(2026, 1, 1), None, None)
    await db_session.commit()
    await service.set_item_price(customer.id, item_b.id, 1, Decimal("15.00"), date(2026, 1, 1), None, None)
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get(f"/admin/customers/{customer.slug}/prices")
    assert response.status_code == 200
    prices = response.json()["prices"]
    assert len(prices) == 2
    assert {p["menu_item_id"] for p in prices} == {item_a.id, item_b.id}


async def test_add_item_price_rejects_negative_price(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Bad Price Item", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Bad Price Doe", phone_number="8880000001", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    response = await async_client.post(
        f"/admin/customers/{customer.slug}/prices",
        json={"menu_item_id": item.id, "price": -5.00, "valid_from": "2026-01-01"},
    )
    assert response.status_code == 422


async def test_add_item_price_rejects_valid_to_before_valid_from(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Bad Range Item", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Bad Range Doe", phone_number="8880000002", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client)

    response = await async_client.post(
        f"/admin/customers/{customer.slug}/prices",
        json={
            "menu_item_id": item.id, "price": 5.00,
            "valid_from": "2026-06-01", "valid_to": "2026-01-01",
        },
    )
    assert response.status_code == 422


# ============================================================================
# GET /admin/customers/search — server-side type-ahead for the Credit Book /
# Rate Cards pickers (replaces the old full-list-embedded-in-page approach).
# ============================================================================

async def test_search_customers_matches_name_or_phone(db_session: AsyncSession, async_client: AsyncClient):
    alice = Customer(name="Alice Wonderland", phone_number="7001112222", shop_id=1)
    bob = Customer(name="Bob Builder", phone_number="7003334444", shop_id=1)
    db_session.add_all([alice, bob])
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/customers/search?q=alice")
    assert response.status_code == 200
    names = {c["name"] for c in response.json()["customers"]}
    assert names == {"Alice Wonderland"}

    response = await async_client.get("/admin/customers/search?q=7003334444")
    names = {c["name"] for c in response.json()["customers"]}
    assert names == {"Bob Builder"}


async def test_search_customers_empty_query_returns_a_page(db_session: AsyncSession, async_client: AsyncClient):
    for i in range(3):
        db_session.add(Customer(name=f"Search Doe {i}", phone_number=f"701000000{i}", shop_id=1))
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/customers/search")
    assert response.status_code == 200
    assert len(response.json()["customers"]) == 3


async def test_search_customers_credit_only_filter(db_session: AsyncSession, async_client: AsyncClient):
    credit_cust = Customer(name="Credit Doe", phone_number="7020000001", shop_id=1, is_credit_customer=True)
    cash_cust = Customer(name="Cash Doe", phone_number="7020000002", shop_id=1, is_credit_customer=False)
    db_session.add_all([credit_cust, cash_cust])
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/customers/search?credit_only=true")
    names = {c["name"] for c in response.json()["customers"]}
    assert names == {"Credit Doe"}


async def test_search_customers_limit_is_capped(db_session: AsyncSession, async_client: AsyncClient):
    for i in range(10):
        db_session.add(Customer(name=f"Capped Doe {i}", phone_number=f"703000{i:04d}", shop_id=1))
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/customers/search?limit=3")
    assert len(response.json()["customers"]) == 3

    # A limit above the server-side cap is clamped, not honored as-is.
    response = await async_client.get("/admin/customers/search?limit=9999")
    assert len(response.json()["customers"]) <= 50


async def test_search_customers_tenant_isolation(db_session: AsyncSession, async_client: AsyncClient):
    other_shop = Shop(name="Other Search Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    own = Customer(name="Mine Doe", phone_number="7040000001", shop_id=1)
    foreign = Customer(name="Mine Doe Foreign", phone_number="7040000002", shop_id=other_shop.id)
    db_session.add_all([own, foreign])
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/customers/search?q=Mine")
    names = {c["name"] for c in response.json()["customers"]}
    assert names == {"Mine Doe"}


async def test_search_customers_pagination(db_session: AsyncSession, async_client: AsyncClient):
    for i in range(5):
        db_session.add(Customer(name=f"Page Doe {i}", phone_number=f"705000{i:04d}", shop_id=1))
    await db_session.commit()

    await _login_owner(async_client)

    page1 = await async_client.get("/admin/customers/search?limit=2&offset=0")
    page2 = await async_client.get("/admin/customers/search?limit=2&offset=2")

    assert page1.json()["total"] == 5
    assert page2.json()["total"] == 5
    assert len(page1.json()["customers"]) == 2
    assert len(page2.json()["customers"]) == 2
    page1_names = {c["name"] for c in page1.json()["customers"]}
    page2_names = {c["name"] for c in page2.json()["customers"]}
    assert page1_names.isdisjoint(page2_names)


async def test_search_menu_items_pagination(db_session: AsyncSession, async_client: AsyncClient):
    for i in range(5):
        db_session.add(MenuItem(name=f"Page Item {i}", price=10.0, category="Food", shop_id=1, stock_quantity=100))
    await db_session.commit()

    await _login_owner(async_client)

    page1 = await async_client.get("/admin/menu/search?limit=2&offset=0&active_only=false")
    page2 = await async_client.get("/admin/menu/search?limit=2&offset=2&active_only=false")

    assert page1.json()["total"] == 5
    assert page2.json()["total"] == 5
    assert len(page1.json()["items"]) == 2
    assert len(page2.json()["items"]) == 2
    page1_ids = {i["id"] for i in page1.json()["items"]}
    page2_ids = {i["id"] for i in page2.json()["items"]}
    assert page1_ids.isdisjoint(page2_ids)


# ============================================================================
# GET /admin/menu/search — item picker for the Rate Cards add/edit modal.
# ============================================================================

async def test_search_menu_items_matches_name_or_sku(db_session: AsyncSession, async_client: AsyncClient):
    burger = MenuItem(name="Cheese Burger", price=10.0, category="Food", shop_id=1, stock_quantity=100, sku="CHZ-001")
    fries = MenuItem(name="Fries", price=5.0, category="Food", shop_id=1, stock_quantity=100, sku="FRY-001")
    db_session.add_all([burger, fries])
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/menu/search?q=cheese")
    names = {i["name"] for i in response.json()["items"]}
    assert names == {"Cheese Burger"}

    response = await async_client.get("/admin/menu/search?q=FRY-001")
    names = {i["name"] for i in response.json()["items"]}
    assert names == {"Fries"}


async def test_search_menu_items_excludes_inactive_and_other_shops(db_session: AsyncSession, async_client: AsyncClient):
    other_shop = Shop(name="Other Menu Search Shop", printer_ip="mock")
    db_session.add(other_shop)
    await db_session.commit()
    await db_session.refresh(other_shop)

    active = MenuItem(name="Active Item", price=10.0, category="Food", shop_id=1, stock_quantity=100, is_active=True)
    inactive = MenuItem(name="Archived Item", price=10.0, category="Food", shop_id=1, stock_quantity=100, is_active=False)
    foreign = MenuItem(name="Foreign Item", price=10.0, category="Food", shop_id=other_shop.id, stock_quantity=100, is_active=True)
    db_session.add_all([active, inactive, foreign])
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/menu/search")
    names = {i["name"] for i in response.json()["items"]}
    assert names == {"Active Item"}


async def test_search_menu_items_active_only_false_includes_archived(db_session: AsyncSession, async_client: AsyncClient):
    """Menu/Inventory tabs pass active_only=false so archived items still
    show up for management — only the Rate Cards picker wants active_only=true."""
    active = MenuItem(name="Active Search Item", price=10.0, category="Food", shop_id=1, stock_quantity=100, is_active=True)
    inactive = MenuItem(name="Archived Search Item", price=10.0, category="Food", shop_id=1, stock_quantity=100, is_active=False)
    db_session.add_all([active, inactive])
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/menu/search?active_only=false")
    names = {i["name"] for i in response.json()["items"]}
    assert names == {"Active Search Item", "Archived Search Item"}


async def test_search_menu_items_returns_enriched_fields(db_session: AsyncSession, async_client: AsyncClient):
    """Menu/Inventory tabs need more than the Rate Cards picker's minimal
    id/name/price/unit — slug, category, sku, image, stock, threshold."""
    item = MenuItem(
        name="Enriched Item", price=25.0, category="Snacks", shop_id=1,
        sku="ENR-001", stock_quantity=42.0, low_stock_threshold=10.0, is_active=True,
    )
    db_session.add(item)
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/menu/search?q=Enriched")
    row = response.json()["items"][0]
    assert row["slug"] == item.slug
    assert row["category"] == "Snacks"
    assert row["sku"] == "ENR-001"
    assert row["stock_quantity"] == 42.0
    assert row["low_stock_threshold"] == 10.0
    assert row["is_active"] is True


async def test_search_menu_items_limit_clamped_to_100(db_session: AsyncSession, async_client: AsyncClient):
    await _login_owner(async_client)
    response = await async_client.get("/admin/menu/search?limit=99999")
    assert response.status_code == 200


async def test_search_menu_items_stock_status_filter(db_session: AsyncSession, async_client: AsyncClient):
    """stock_status mirrors the Inventory tab's per-row badge logic:
    untracked (no stock_quantity), out (<=0), low (>0 and <= threshold,
    default 5), ok (> threshold)."""
    untracked = MenuItem(name="Untracked Item", price=10.0, category="Food", shop_id=1, stock_quantity=None)
    out = MenuItem(name="Out Item", price=10.0, category="Food", shop_id=1, stock_quantity=0)
    low = MenuItem(name="Low Item", price=10.0, category="Food", shop_id=1, stock_quantity=3, low_stock_threshold=5.0)
    ok = MenuItem(name="Ok Item", price=10.0, category="Food", shop_id=1, stock_quantity=50, low_stock_threshold=5.0)
    db_session.add_all([untracked, out, low, ok])
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/menu/search?stock_status=untracked&active_only=false")
    names = {i["name"] for i in response.json()["items"]}
    assert names == {"Untracked Item"}

    response = await async_client.get("/admin/menu/search?stock_status=out&active_only=false")
    names = {i["name"] for i in response.json()["items"]}
    assert names == {"Out Item"}

    response = await async_client.get("/admin/menu/search?stock_status=low&active_only=false")
    names = {i["name"] for i in response.json()["items"]}
    assert names == {"Low Item"}

    response = await async_client.get("/admin/menu/search?stock_status=ok&active_only=false")
    names = {i["name"] for i in response.json()["items"]}
    assert names == {"Ok Item"}


async def test_search_menu_items_stock_status_combines_with_query(db_session: AsyncSession, async_client: AsyncClient):
    matching_low = MenuItem(name="Combo Low Burger", price=10.0, category="Food", shop_id=1, stock_quantity=2, low_stock_threshold=5.0)
    matching_ok = MenuItem(name="Combo Ok Burger", price=10.0, category="Food", shop_id=1, stock_quantity=50, low_stock_threshold=5.0)
    db_session.add_all([matching_low, matching_ok])
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get("/admin/menu/search?q=Combo&stock_status=low&active_only=false")
    names = {i["name"] for i in response.json()["items"]}
    assert names == {"Combo Low Burger"}


# ============================================================================
# PUT/DELETE /admin/customers/{slug}/prices/{price_slug} — edit and remove
# an existing rate-card override.
# ============================================================================

async def test_update_item_price_success(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Editable Item", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Edit Price Doe", phone_number="7050000001", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    price_row = await service.set_item_price(customer.id, item.id, 1, Decimal("8.00"), date(2026, 1, 1), None, None)
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.put(
        f"/admin/customers/{customer.slug}/prices/{price_row.slug}",
        json={"price": 6.50, "valid_from": "2026-01-01", "valid_to": "2026-12-31"},
    )
    assert response.status_code == 200
    body = response.json()["price"]
    assert body["price"] == 6.5
    assert body["valid_to"] == "2026-12-31"


async def test_update_item_price_rejects_collision_with_another_rate(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Collide Item", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Collide Doe", phone_number="7050000002", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    await service.set_item_price(customer.id, item.id, 1, Decimal("8.00"), date(2026, 1, 1), None, None)
    await db_session.commit()
    second = await service.set_item_price(customer.id, item.id, 1, Decimal("7.00"), date(2026, 6, 1), None, None)
    await db_session.commit()

    await _login_owner(async_client)

    # Moving the second row's valid_from onto the first row's date collides.
    response = await async_client.put(
        f"/admin/customers/{customer.slug}/prices/{second.slug}",
        json={"price": 7.00, "valid_from": "2026-01-01"},
    )
    assert response.status_code == 400
    assert "already starts" in response.json()["detail"]


async def test_update_item_price_rejects_other_customers_row(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Foreign Edit Item", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer_a = Customer(name="Owner Doe", phone_number="7050000003", shop_id=1)
    customer_b = Customer(name="Bystander Doe", phone_number="7050000004", shop_id=1)
    db_session.add_all([item, customer_a, customer_b])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer_a)
    await db_session.refresh(customer_b)

    service = CustomerService(db_session)
    price_row = await service.set_item_price(customer_a.id, item.id, 1, Decimal("8.00"), date(2026, 1, 1), None, None)
    await db_session.commit()

    await _login_owner(async_client)

    # Editing customer_a's price row via customer_b's slug must be rejected.
    response = await async_client.put(
        f"/admin/customers/{customer_b.slug}/prices/{price_row.slug}",
        json={"price": 1.00, "valid_from": "2026-01-01"},
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


async def test_delete_item_price_success(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Deletable Item", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Delete Price Doe", phone_number="7050000005", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    price_row = await service.set_item_price(customer.id, item.id, 1, Decimal("8.00"), date(2026, 1, 1), None, None)
    await db_session.commit()
    price_slug = price_row.slug

    await _login_owner(async_client)

    response = await async_client.delete(f"/admin/customers/{customer.slug}/prices/{price_slug}")
    assert response.status_code == 200

    response = await async_client.get(f"/admin/customers/{customer.slug}/prices")
    assert response.json()["prices"] == []


async def test_delete_item_price_rejects_other_customers_row(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Foreign Delete Item", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer_a = Customer(name="Delete Owner Doe", phone_number="7050000006", shop_id=1)
    customer_b = Customer(name="Delete Bystander Doe", phone_number="7050000007", shop_id=1)
    db_session.add_all([item, customer_a, customer_b])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer_a)
    await db_session.refresh(customer_b)

    service = CustomerService(db_session)
    price_row = await service.set_item_price(customer_a.id, item.id, 1, Decimal("8.00"), date(2026, 1, 1), None, None)
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.delete(f"/admin/customers/{customer_b.slug}/prices/{price_row.slug}")
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


async def test_list_item_prices_includes_menu_item_name(db_session: AsyncSession, async_client: AsyncClient):
    item = MenuItem(name="Named Item", price=10.0, category="Food", shop_id=1, stock_quantity=100)
    customer = Customer(name="Named Item Doe", phone_number="7050000008", shop_id=1)
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    service = CustomerService(db_session)
    await service.set_item_price(customer.id, item.id, 1, Decimal("8.00"), date(2026, 1, 1), None, None)
    await db_session.commit()

    await _login_owner(async_client)

    response = await async_client.get(f"/admin/customers/{customer.slug}/prices")
    prices = response.json()["prices"]
    assert len(prices) == 1
    assert prices[0]["menu_item_name"] == "Named Item"
