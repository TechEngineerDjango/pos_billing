from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Bill
from app.domains.credit.service import CreditService


class PaymentMethodHandler:
    """Extension point for payment-method-specific side effects on bill finalize."""

    async def on_finalize(
        self, db: AsyncSession, bill: Bill, customer_id: int | None,
        total_amount: Decimal, status: str, shop_tz: str = "UTC",
    ) -> None:
        pass


class CreditHandler(PaymentMethodHandler):
    async def on_finalize(self, db, bill, customer_id, total_amount, status, shop_tz="UTC"):
        if not customer_id:
            raise ValueError("Credit billing requires a registered customer")
        credit_service = CreditService(db)
        if status == "Completed":
            # The ONE place credit_balance is incremented — mirrors the
            # existing reserved_quantity/stock_quantity Held-vs-Completed
            # split (task.yaml FEAT-4 correctness rule).
            await credit_service.reserve_credit(customer_id, total_amount, bill, shop_tz)
        else:
            # Held: eligibility must still hold, but over-limit is a
            # read-only preview only — no commitment, no rejection here.
            await credit_service.check_eligible(customer_id)
            bill.payment_status = "Unpaid"


_PAYMENT_METHOD_REGISTRY = {
    "Credit": CreditHandler,
}


def get_payment_method_handler(payment_method: str) -> PaymentMethodHandler:
    handler_class = _PAYMENT_METHOD_REGISTRY.get(payment_method, PaymentMethodHandler)
    return handler_class()
