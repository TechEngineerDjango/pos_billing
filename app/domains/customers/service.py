from typing import Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from app.domains.customers.repository import CustomerRepository
from app.shared.schemas import CustomerCreate
from app.shared.models import Customer

class CustomerService:
    def __init__(self, session: AsyncSession):
        self.repository = CustomerRepository(session)

    async def create_customer(self, schema: CustomerCreate, shop_id: int) -> Customer:
        existing = await self.repository.get_by_phone(schema.phone_number, shop_id)
        if existing:
            raise ValueError(f"Customer with phone {schema.phone_number} already exists.")
        
        customer = Customer(
            name=schema.name,
            phone_number=schema.phone_number,
            country_code=schema.country_code,
            shop_id=shop_id,
            credit_limit=schema.credit_limit,
            credit_balance=0.00,
            payment_term_type=schema.payment_term_type,
            payment_term_value=schema.payment_term_value
        )
        return await self.repository.add(customer)

    async def get_customer(self, customer_id: int, shop_id: int) -> Customer:
        customer = await self.repository.get_by_id(customer_id)
        if customer and customer.shop_id == shop_id:
            return customer
        return None

    async def list_customers(self, shop_id: int, skip: int = 0, limit: int = 100) -> Sequence[Customer]:
        return await self.repository.list(skip=skip, limit=limit, shop_id=shop_id)
