import pytest
from decimal import Decimal
from app.shared.models import Customer, Shop
from app.core.base import Base

@pytest.mark.asyncio
async def test_customer_credit_fields(db_session):
    # Ensure tables are created for tests
    async with db_session.bind.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    shop = Shop(name="Test Shop")
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)
    
    # This should fail because credit fields don't exist yet
    customer = Customer(
        name="John Doe", 
        phone_number="1234567890",
        shop_id=shop.id,
        credit_limit=Decimal("5000.00"),
        credit_balance=Decimal("0.00"),
        payment_term_type="monthly",
        payment_term_value=31
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)
    
    assert customer.credit_limit == Decimal("5000.00")
    assert customer.credit_balance == Decimal("0.00")
    assert customer.payment_term_type == "monthly"
    assert customer.payment_term_value == 31
