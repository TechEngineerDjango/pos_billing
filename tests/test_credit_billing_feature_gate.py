"""
FIX-11 — credit_billing feature flag never registered or enforced.

The "credit_billing" feature is defined as a row in the DB-driven `features`
catalog table and granted to a plan via the `plan_features` M2M table
(app/domains/features/service.py's FeatureService — the sole feature-gating
mechanism; there is no legacy JSON fallback).

These tests pin the expected gated behavior described in FIX-11's
fix_direction (task.yaml):
  - CustomerService.set_credit_terms must check
    FeatureService.is_feature_enabled(shop, "credit_billing") before
    allowing is_credit_customer=True — reject (this codebase's
    ValueError->400 convention for this router, see
    app/domains/customers/router.py's other endpoints) if the shop lacks
    the feature.
  - BillingService.create_bill (and update_bill) must re-check the same
    flag when payment_method == "Credit", independent of the customer
    row's own is_credit_customer flag — this covers a shop being
    downgraded after some customers were already flagged credit-eligible.

conftest.py's db_session fixture grants shop_id=1's plan "credit_billing"
via M2M (restored there once FIX-11 landed, since shop_id=1 previously
"had" credit billing unconditionally before any gate existed, and the
pre-existing tests/test_credit_bills.py suite was written against that
assumption predating FIX-11). Because shop_id=1 is no longer a safe
stand-in for "lacks the feature", every test in this file — both the WITH
and WITHOUT cases — builds its own isolated Shop/Subscription/User via
_create_shop_with_feature(feature_enabled=...), rather than relying on the
shared default fixture shop for the negative cases.
"""
import os
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.shared.models import Customer, Feature, MenuItem, PlanFeature, Shop, Subscription, User
from app.domains.auth.router import get_password_hash

pytestmark = pytest.mark.asyncio

from dotenv import load_dotenv
load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")


async def _login_owner(async_client: AsyncClient, username: str = "owner"):
    login_res = await async_client.post(
        "/auth/login",
        data={"username": username, "password": OWNER_PASSWORD},
        follow_redirects=False,
    )
    assert login_res.status_code == 303


async def _create_shop_with_feature(
    db_session: AsyncSession, *, feature_enabled: bool, username: str
) -> Shop:
    """
    Creates an isolated Shop + Subscription + owner User (distinct from the
    default fixture shop_id=1) whose plan either does or does not have
    "credit_billing" linked via the M2M plan_features table.

    FeatureService.is_feature_enabled() (app/domains/features/service.py)
    resolves purely from plan_features M2M links, so this is the mechanism
    FIX-11's fix_direction says the backend gate must key off.
    """
    feature_keys = ["pos_basic", "cash_calculator", "customer_management"]
    if feature_enabled:
        feature_keys.append("credit_billing")

    sub = Subscription(name=f"Sub for {username}")
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    feat_res = await db_session.execute(select(Feature).where(Feature.key.in_(feature_keys)))
    for feature in feat_res.scalars().all():
        db_session.add(PlanFeature(plan_id=sub.id, feature_id=feature.id))
    await db_session.commit()

    shop = Shop(name=f"Shop for {username}", printer_ip="mock", subscription_id=sub.id)
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)

    owner = User(
        username=username,
        hashed_password=get_password_hash(OWNER_PASSWORD),
        role="owner",
        shop_id=shop.id,
    )
    db_session.add(owner)
    await db_session.commit()

    return shop


# ============================================================================
# CustomerService.set_credit_terms — authoritative gate
# ============================================================================

async def test_credit_terms_rejected_without_credit_billing_feature(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    A shop WITHOUT "credit_billing" linked to its plan (M2M)
    cannot set is_credit_customer=True via
    POST /admin/customers/{slug}/credit-terms — must be rejected (400,
    matching this router's existing ValueError->400 convention used by
    every other endpoint in app/domains/customers/router.py), not silently
    accepted (200/303).

    Builds its own isolated shop lacking the feature via
    _create_shop_with_feature(feature_enabled=False) — shop_id=1 (the
    shared default fixture shop) now has "credit_billing" enabled (see
    module docstring), so it can no longer stand in for the "shop lacks
    the feature" case.
    """
    shop = await _create_shop_with_feature(
        db_session, feature_enabled=False, username="owner_credit_terms_gate"
    )

    customer = Customer(name="Gate Doe", phone_number="4440001112", shop_id=shop.id)
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    await _login_owner(async_client, username="owner_credit_terms_gate")

    response = await async_client.post(
        f"/admin/customers/{customer.slug}/credit-terms",
        data={
            "is_credit_customer": "true",
            "credit_limit": "100.00",
            "payment_term_type": "weekly",
            "payment_term_value": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400

    await db_session.refresh(customer)
    assert customer.is_credit_customer is False  # not silently applied


async def test_credit_terms_allowed_with_credit_billing_feature(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    A shop WITH "credit_billing" enabled on its subscription CAN set
    is_credit_customer=True via the same endpoint. Happy-path regression
    guard — expected to pass today too, since it exercises no new
    restriction (the feature isn't wired up yet, so this currently
    passes vacuously; once the gate lands it must keep passing for real).
    """
    shop = await _create_shop_with_feature(
        db_session, feature_enabled=True, username="owner_credit_terms_ok"
    )

    customer = Customer(name="Allowed Doe", phone_number="4440002223", shop_id=shop.id)
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    await _login_owner(async_client, username="owner_credit_terms_ok")

    response = await async_client.post(
        f"/admin/customers/{customer.slug}/credit-terms",
        data={
            "is_credit_customer": "true",
            "credit_limit": "100.00",
            "payment_term_type": "weekly",
            "payment_term_value": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    await db_session.refresh(customer)
    assert customer.is_credit_customer is True
    assert float(customer.credit_limit) == 100.00


# ============================================================================
# BillingService.create_bill — defense-in-depth gate (downgrade-after-the-
# fact scenario: customer row already flagged credit-eligible, shop no
# longer has the feature)
# ============================================================================

async def test_credit_bill_rejected_without_feature_even_if_customer_already_eligible(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    A Credit bill (POST /billing/create, payment_method='Credit') is
    rejected for a shop that lacks "credit_billing" even if the target
    customer row already has is_credit_customer=True — simulated here by
    creating the customer as credit-eligible directly via the DB/fixture,
    bypassing the credit-terms endpoint, to isolate testing the
    create_bill-level gate specifically (the "shop downgraded after the
    fact" scenario from FIX-11's fix_direction: plans change after data
    exists, so create_bill must not rely solely on the customer row's own
    is_credit_customer flag).

    Builds its own isolated shop lacking the feature via
    _create_shop_with_feature(feature_enabled=False) — shop_id=1 (the
    shared default fixture shop) now has "credit_billing" enabled (see
    module docstring), so it can no longer stand in for the "shop lacks
    the feature" case.
    """
    shop = await _create_shop_with_feature(
        db_session, feature_enabled=False, username="owner_credit_bill_gate"
    )

    item = MenuItem(name="Gate Burger", price=10.0, category="Food", shop_id=shop.id, stock_quantity=100)
    customer = Customer(
        name="Already Eligible Doe", phone_number="4440003334", shop_id=shop.id,
        is_credit_customer=True, credit_limit=100.0, credit_balance=0.0,
        payment_term_type="weekly", payment_term_value=1,
    )
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client, username="owner_credit_bill_gate")

    payload = {
        "items": [{"id": item.id, "qty": 1}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id,
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 400

    await db_session.refresh(customer)
    assert customer.credit_balance == 0.0  # bill must not have been created/reserved


async def test_credit_bill_succeeds_with_feature_and_eligible_customer(
    db_session: AsyncSession,
    async_client: AsyncClient,
):
    """
    A shop WITH "credit_billing" and an eligible customer can still create
    a Credit bill successfully — regression guard, run alongside
    tests/test_credit_bills.py. Expected to pass today too (no new
    restriction applies), and must keep passing once the gate lands.
    """
    shop = await _create_shop_with_feature(
        db_session, feature_enabled=True, username="owner_credit_bill_ok"
    )

    item = MenuItem(name="Allowed Burger", price=10.0, category="Food", shop_id=shop.id, stock_quantity=100)
    customer = Customer(
        name="Feature Eligible Doe", phone_number="4440004445", shop_id=shop.id,
        is_credit_customer=True, credit_limit=100.0, credit_balance=0.0,
        payment_term_type="weekly", payment_term_value=1,
    )
    db_session.add_all([item, customer])
    await db_session.commit()
    await db_session.refresh(item)
    await db_session.refresh(customer)

    await _login_owner(async_client, username="owner_credit_bill_ok")

    payload = {
        "items": [{"id": item.id, "qty": 2}],
        "payment_method": "Credit",
        "status": "Completed",
        "customer_id": customer.id,
    }
    response = await async_client.post("/billing/create", json=payload)
    assert response.status_code == 200

    await db_session.refresh(customer)
    assert customer.credit_balance == 20.0  # 2 * 10.0
