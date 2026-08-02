from typing import Sequence, Optional
from decimal import Decimal
from datetime import date
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.domains.customers.repository import CustomerRepository
from app.domains.customers.price_repository import CustomerItemPriceRepository
from app.shared.schemas import CustomerCreate
from app.shared.models import Customer, CustomerItemPrice, MenuItem, Shop
from app.domains.features.service import FeatureService

class CustomerService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = CustomerRepository(db)
        self.price_repository = CustomerItemPriceRepository(db)

    async def create_customer(self, schema: CustomerCreate, shop_id: int) -> Customer:
        existing = await self.repository.get_by_phone(schema.phone_number, shop_id)
        if existing:
            raise ValueError(f"Customer with phone {schema.phone_number} already exists.")

        customer = Customer(
            name=schema.name,
            phone_number=schema.phone_number,
            country_code=schema.country_code,
            shop_id=shop_id,
            is_credit_customer=schema.is_credit_customer,
            credit_limit=schema.credit_limit,
            credit_balance=Decimal("0.00"),
            payment_term_type=schema.payment_term_type,
            payment_term_value=schema.payment_term_value,
        )
        return await self.repository.add(customer)

    async def get_customer(self, customer_id: int, shop_id: int) -> Optional[Customer]:
        customer = await self.repository.get_by_id(customer_id)
        if customer and customer.shop_id == shop_id:
            return customer
        return None

    async def list_customers(self, shop_id: int, skip: int = 0, limit: int = 100) -> Sequence[Customer]:
        customers = await self.repository.list(shop_id=shop_id)
        return customers[skip:skip + limit]

    async def set_credit_terms(
        self,
        customer_id: int,
        shop_id: int,
        is_credit_customer: bool,
        credit_limit: Optional[Decimal],
        payment_term_type: Optional[str],
        payment_term_value: Optional[int],
    ) -> Customer:
        customer = await self.get_customer(customer_id, shop_id)
        if not customer:
            raise ValueError("Customer not found")

        if is_credit_customer:
            # Authoritative entitlement gate (contract_standards #5 — the
            # frontend's x-show is UX-only). A shop can only START flagging
            # customers as credit-eligible if its subscription includes
            # "credit_billing"; turning it back OFF is always allowed.
            shop_res = await self.db.execute(
                select(Shop).where(Shop.id == shop_id).options(selectinload(Shop.subscription))
            )
            shop = shop_res.scalar_one_or_none()
            if not await FeatureService(db=self.db).is_feature_enabled(shop, "credit_billing"):
                raise ValueError(
                    "Your subscription plan does not include Credit Billing. "
                    "Please upgrade your plan to enable credit customers."
                )

        customer.is_credit_customer = is_credit_customer
        customer.credit_limit = credit_limit
        customer.payment_term_type = payment_term_type
        customer.payment_term_value = payment_term_value
        return customer

    async def get_credit_summary(self, customer_id: int) -> dict:
        customer = await self.repository.get_by_id(customer_id)
        if not customer:
            raise ValueError("Customer not found")
        return {
            "is_credit_customer": customer.is_credit_customer,
            "credit_limit": float(customer.credit_limit) if customer.credit_limit is not None else None,
            "credit_balance": float(customer.credit_balance or 0),
            "payment_term_type": customer.payment_term_type,
            "payment_term_value": customer.payment_term_value,
        }

    async def set_item_price(
        self,
        customer_id: int,
        menu_item_id: int,
        shop_id: int,
        price: Decimal,
        valid_from: date,
        valid_to: Optional[date],
        created_by_user_id: Optional[int],
    ) -> CustomerItemPrice:
        menu_item_res = await self.db.execute(
            select(MenuItem.id).where(MenuItem.id == menu_item_id, MenuItem.shop_id == shop_id)
        )
        if menu_item_res.scalar_one_or_none() is None:
            raise ValueError("Menu item not found in this shop")

        # Only an exact valid_from match for the same (customer, item) is a real
        # conflict — a bounded (temporary) row is EXPECTED to overlap an
        # open-ended row that started earlier (see task.yaml FEAT-3 reviewer
        # correction). Do not reject on range overlap in general.
        duplicates = await self.price_repository.list_by_start_date(customer_id, menu_item_id, valid_from)
        if duplicates:
            raise ValueError("A rate already starts on this exact date for this customer and item")

        price_row = CustomerItemPrice(
            shop_id=shop_id,
            customer_id=customer_id,
            menu_item_id=menu_item_id,
            price=price,
            valid_from=valid_from,
            valid_to=valid_to,
            is_active=True,
            created_by_user_id=created_by_user_id,
        )
        try:
            return await self.price_repository.add(price_row)
        except IntegrityError:
            # Belt-and-suspenders for the check above: a concurrent request
            # can win the race between the SELECT and this INSERT, so the DB's
            # unique constraint (customer_id, menu_item_id, valid_from) is the
            # real guarantee — this just turns that into the same clean error.
            await self.price_repository.db.rollback()
            raise ValueError("A rate already starts on this exact date for this customer and item")

    async def update_item_price(
        self,
        price_slug: str,
        customer_id: int,
        shop_id: int,
        price: Decimal,
        valid_from: date,
        valid_to: Optional[date],
    ) -> CustomerItemPrice:
        price_row = await self.price_repository.get_by_slug(price_slug)
        if not price_row or price_row.customer_id != customer_id or price_row.shop_id != shop_id:
            raise ValueError("Price override not found")

        if valid_from != price_row.valid_from:
            duplicates = await self.price_repository.list_by_start_date(customer_id, price_row.menu_item_id, valid_from)
            if duplicates:
                raise ValueError("A rate already starts on this exact date for this customer and item")

        price_row.price = price
        price_row.valid_from = valid_from
        price_row.valid_to = valid_to
        try:
            await self.price_repository.db.flush()
        except IntegrityError:
            await self.price_repository.db.rollback()
            raise ValueError("A rate already starts on this exact date for this customer and item")
        return price_row

    async def delete_item_price(self, price_slug: str, customer_id: int, shop_id: int) -> None:
        price_row = await self.price_repository.get_by_slug(price_slug)
        if not price_row or price_row.customer_id != customer_id or price_row.shop_id != shop_id:
            raise ValueError("Price override not found")
        await self.price_repository.delete(price_row)

    async def get_effective_price(self, customer_id: Optional[int], menu_item_id: int, as_of_date: date, list_price: Decimal) -> Decimal:
        """Rate-card price if an override covers as_of_date, else MenuItem.price.
        Deliberately NOT a Strategy pattern — single fallback lookup, see
        task.yaml design_patterns.explicitly_not_strategy_pattern."""
        if not customer_id:
            return list_price
        override = await self.price_repository.get_active_for_date(customer_id, menu_item_id, as_of_date)
        return override.price if override else list_price
