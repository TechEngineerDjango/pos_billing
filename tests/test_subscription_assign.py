import os
import pytest
from dotenv import load_dotenv
from httpx import AsyncClient
from sqlalchemy import select

from app.shared.models import Shop, Subscription

load_dotenv()
SUPERADMIN_PASSWORD = os.getenv("TEST_SUPERADMIN_PASSWORD")


async def _get_shop(db_session):
    result = await db_session.execute(select(Shop).limit(1))
    return result.scalars().first()


async def _get_or_create_subscription(db_session):
    result = await db_session.execute(select(Subscription).limit(1))
    sub = result.scalars().first()
    if not sub:
        sub = Subscription(name="Test Plan", price=499.00)
        db_session.add(sub)
        await db_session.commit()
        await db_session.refresh(sub)
    return sub


@pytest.mark.asyncio
async def test_assign_real_plan_sets_subscription_id(async_client: AsyncClient, db_session):
    shop = await _get_shop(db_session)
    sub = await _get_or_create_subscription(db_session)
    await async_client.post(
        "/auth/login", data={"username": "superadmin", "password": SUPERADMIN_PASSWORD}, follow_redirects=True
    )

    response = await async_client.post(
        "/superadmin/subscriptions/assign",
        data={"shop_id": shop.id, "subscription_id": sub.id},
        follow_redirects=True,
    )
    assert response.status_code == 200

    await db_session.refresh(shop)
    assert shop.subscription_id == sub.id


@pytest.mark.asyncio
async def test_assign_no_plan_clears_subscription_id(async_client: AsyncClient, db_session):
    """Regression test: the Fleet tab's 'No Plan (Free Tier)' option submits
    subscription_id="" (empty string, not omitted). Optional[int] = Form(None)
    used to reject this with a 422 before the handler ever ran, because
    Pydantic doesn't coerce "" to None for an Optional[int] field — so
    clearing a shop's plan was completely broken. subscription_id is now
    Optional[str], converted manually, specifically to keep this working."""
    shop = await _get_shop(db_session)
    sub = await _get_or_create_subscription(db_session)
    shop.subscription_id = sub.id
    await db_session.commit()

    await async_client.post(
        "/auth/login", data={"username": "superadmin", "password": SUPERADMIN_PASSWORD}, follow_redirects=True
    )

    response = await async_client.post(
        "/superadmin/subscriptions/assign",
        data={"shop_id": shop.id, "subscription_id": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert response.status_code != 422

    await db_session.refresh(shop)
    assert shop.subscription_id is None
