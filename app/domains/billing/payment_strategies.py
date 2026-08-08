from decimal import Decimal
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Bill
from app.domains.credit.service import CreditService


class PaymentMethodHandler:
    """Extension point for payment-method-specific side effects on bill finalize."""

    async def on_finalize(
        self, db: AsyncSession, bill: Bill, customer_id: int | None,
        total_amount: Decimal, status: str, shop_tz: str = "UTC",
        block_over_limit: bool = False,
    ) -> Optional[str]:
        return None


class CreditHandler(PaymentMethodHandler):
    """"Pay Later" — despite the DB/internal name "Credit", this is available
    to any registered customer, not just is_credit_customer accounts. Only
    actual credit-customers get a credit_limit check (and only they carry a
    credit_balance/due_date) — everyone else is just an unpaid bill to
    collect on later, tracked via the Orders view instead."""

    async def on_finalize(self, db, bill, customer_id, total_amount, status, shop_tz="UTC", block_over_limit=False):
        if not customer_id:
            raise ValueError("Pay Later requires a registered customer")
        credit_service = CreditService(db)
        customer = await credit_service.customer_repo.get_by_id(customer_id)
        if not customer:
            raise ValueError("Customer not found")

        if not customer.is_credit_customer:
            # Ordinary "pay later" customer — no limit, no balance tracking.
            bill.payment_status = "Unpaid"
            return None

        if status == "Completed":
            # The ONE place credit_balance is incremented — mirrors the
            # existing reserved_quantity/stock_quantity Held-vs-Completed
            # split (task.yaml FEAT-4 correctness rule).
            return await credit_service.reserve_credit(customer_id, total_amount, bill, shop_tz, block_over_limit)
        else:
            # Held: eligibility must still hold, but over-limit is a
            # read-only preview only — no commitment, no rejection here.
            await credit_service.check_eligible(customer_id)
            bill.payment_status = "Unpaid"
            return None


_PAYMENT_METHOD_REGISTRY = {
    "Credit": CreditHandler,
}


def get_payment_method_handler(payment_method: str) -> PaymentMethodHandler:
    handler_class = _PAYMENT_METHOD_REGISTRY.get(payment_method, PaymentMethodHandler)
    return handler_class()
