import datetime as dt
import logging
import uuid
from datetime import timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Bill, Customer, CreditStatement, CreditPayment, Shop
from app.shared.sequence import next_sequence_number
from app.shared.time_utils import shop_local
from app.domains.credit.strategies.factory import get_payment_term_strategy
from app.domains.customers.repository import CustomerRepository
from app.domains.credit.repository import CreditStatementRepository, CreditPaymentRepository

logger = logging.getLogger(__name__)


class CreditLimitExceededError(ValueError):
    """Raised when a Completed credit bill would push a customer over their
    credit_limit. Subclasses ValueError so existing ValueError->HTTP 400
    mapping in callers (BillingService._finalize_payment_method) still works."""


class CreditService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.customer_repo = CustomerRepository(db)
        self.statement_repo = CreditStatementRepository(db)
        self.payment_repo = CreditPaymentRepository(db)

    async def check_eligible(self, customer_id: int, *, locked: bool = False) -> Customer:
        customer = (
            await self.customer_repo.get_locked(customer_id)
            if locked else await self.customer_repo.get_by_id(customer_id)
        )
        if not customer:
            raise ValueError("Customer not found")
        if not customer.is_credit_customer:
            raise ValueError("Customer is not eligible for credit")
        return customer

    async def would_exceed_limit(self, customer_id: int, amount: Decimal) -> bool:
        """Read-only preview, no lock. Used for the Held-bill early warning —
        not the authoritative check (see task.yaml contract_standards #5)."""
        customer = await self.check_eligible(customer_id)
        if customer.credit_limit is None:
            return False
        current = customer.credit_balance or Decimal("0.00")
        return (current + amount) > customer.credit_limit

    async def reserve_credit(
        self, customer_id: int, amount: Decimal, bill: Optional[Bill] = None, shop_tz: str = "UTC",
        block_over_limit: bool = False,
    ) -> Optional[str]:
        """The ONE place Customer.credit_balance is incremented. Locks the
        Customer row BEFORE reading credit_balance (same ordering that fixed
        the prior stock-locking bug). Only ever called for a bill that is
        actually Completed — a Held credit bill must not reach here.

        Crossing the customer's credit_limit is a warning by default (the
        bill still goes through, balance still moves) — pass
        block_over_limit=True (shop.block_over_credit_limit) to reject it
        instead, the old hard-block behavior. Returns the warning message,
        or None if under the limit / no limit set."""
        customer = await self.check_eligible(customer_id, locked=True)
        current = customer.credit_balance or Decimal("0.00")
        would_exceed = customer.credit_limit is not None and (current + amount) > customer.credit_limit
        if would_exceed and block_over_limit:
            raise CreditLimitExceededError(
                f"Credit limit exceeded. Current balance: {current}, Limit: {customer.credit_limit}"
            )
        customer.credit_balance = current + amount

        if bill is not None:
            bill.payment_status = "Unpaid"
            if customer.payment_term_type and customer.payment_term_value is not None:
                strategy = get_payment_term_strategy(customer.payment_term_type, customer.payment_term_value)
                # Shop-local day, not raw UTC — same day-boundary fix used for
                # bill numbering in billing/service.py (avoids an off-by-one
                # due date for a bill made near midnight in a non-UTC shop).
                anchor = bill.timestamp if bill.timestamp else dt.datetime.now(timezone.utc)
                from_date = shop_local(anchor, shop_tz).date()
                due = strategy.compute_due_date(from_date)
                bill.due_date = dt.datetime.combine(due, dt.time.min, tzinfo=timezone.utc)

        if would_exceed:
            return (
                f"{customer.name} is now over their credit limit "
                f"(balance {customer.credit_balance}, limit {customer.credit_limit})."
            )
        return None

    async def release_credit(self, customer_id: int, amount: Decimal, _customer: Optional[Customer] = None) -> None:
        """Symmetric decrement to reserve_credit, called on payment collection."""
        customer = _customer or await self.customer_repo.get_locked(customer_id)
        if not customer:
            raise ValueError("Customer not found")
        customer.credit_balance = max(Decimal("0.00"), (customer.credit_balance or Decimal("0.00")) - amount)

    async def record_payment(
        self,
        shop_id: int,
        amount: Decimal,
        idempotency_key: str,
        payment_method: str,
        statement_slug: Optional[str] = None,
        bill_slug: Optional[str] = None,
        recorded_by_user_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> CreditPayment:
        """Idempotent: a repeated idempotency_key returns the original result
        unchanged rather than re-applying (task.yaml contract_standards #4).
        Pays down exactly one of: a CreditStatement (statement_slug — the
        primary collection unit once a bill has been rolled up), or a single
        Completed Credit bill not yet rolled into any statement (bill_slug —
        lets a customer pay off a bill before the next statement run). Rejects
        overpayment (400) rather than silently capping. shop_id scopes every
        lookup so a caller can never read or mutate another shop's ledger."""
        if bool(statement_slug) == bool(bill_slug):
            raise ValueError("Provide exactly one of statement_slug or bill_slug")

        existing = await self.payment_repo.get_by_idempotency_key(idempotency_key)
        if existing:
            if existing.shop_id != shop_id:
                raise ValueError("Not found")
            return existing

        if amount is None or amount <= 0:
            raise ValueError("Payment amount must be greater than zero")

        if statement_slug:
            return await self._record_statement_payment(
                shop_id, statement_slug, amount, idempotency_key, payment_method, recorded_by_user_id, note,
            )
        return await self._record_bill_payment(
            shop_id, bill_slug, amount, idempotency_key, payment_method, recorded_by_user_id, note,
        )

    async def _record_statement_payment(
        self, shop_id, statement_slug, amount, idempotency_key, payment_method, recorded_by_user_id, note,
    ) -> CreditPayment:
        statement = await self.statement_repo.get_locked_by_slug(statement_slug)
        if not statement or statement.shop_id != shop_id:
            raise ValueError("Statement not found")

        customer = await self.customer_repo.get_locked(statement.customer_id)
        if not customer:
            raise ValueError("Customer not found")

        already_paid = statement.amount_paid or Decimal("0.00")
        if already_paid + amount > statement.total_amount:
            raise ValueError(
                f"Payment exceeds outstanding balance. Outstanding: "
                f"{statement.total_amount - already_paid}"
            )

        statement.amount_paid = already_paid + amount
        statement.status = "Paid" if statement.amount_paid >= statement.total_amount else "PartiallyPaid"

        if statement.status == "Paid":
            # The statement is the primary collection unit — payment isn't
            # tracked per-bill while a statement is open, so once it's fully
            # settled every bill rolled into it is settled too.
            bills_result = await self.db.execute(
                select(Bill).where(Bill.credit_statement_id == statement.id)
            )
            for bill in bills_result.scalars().all():
                bill.payment_status = "Paid"
                bill.amount_paid = bill.total_amount

        await self.release_credit(customer.id, amount, _customer=customer)

        payment = CreditPayment(
            shop_id=customer.shop_id,
            customer_id=customer.id,
            credit_statement_id=statement.id,
            amount=amount,
            payment_method=payment_method,
            paid_at=dt.datetime.now(timezone.utc),
            recorded_by_user_id=recorded_by_user_id,
            note=note,
            idempotency_key=idempotency_key,
        )
        return await self.payment_repo.add(payment)

    async def _record_bill_payment(
        self, shop_id, bill_slug, amount, idempotency_key, payment_method, recorded_by_user_id, note,
    ) -> CreditPayment:
        bill = await self.statement_repo.get_locked_bill_by_slug(shop_id, bill_slug)
        if not bill:
            raise ValueError("Bill not found")
        if bill.payment_method != "Credit" or bill.status != "Completed":
            raise ValueError("Only a Completed Credit bill can be paid directly")
        if bill.credit_statement_id is not None:
            raise ValueError("This bill is already part of a statement — pay via the statement instead")

        customer = await self.customer_repo.get_locked(bill.customer_id)
        if not customer:
            raise ValueError("Customer not found")

        already_paid = bill.amount_paid or Decimal("0.00")
        if already_paid + amount > bill.total_amount:
            raise ValueError(
                f"Payment exceeds outstanding balance. Outstanding: "
                f"{bill.total_amount - already_paid}"
            )

        bill.amount_paid = already_paid + amount
        bill.payment_status = "Paid" if bill.amount_paid >= bill.total_amount else "PartiallyPaid"

        await self.release_credit(customer.id, amount, _customer=customer)

        payment = CreditPayment(
            shop_id=customer.shop_id,
            customer_id=customer.id,
            bill_id=bill.id,
            amount=amount,
            payment_method=payment_method,
            paid_at=dt.datetime.now(timezone.utc),
            recorded_by_user_id=recorded_by_user_id,
            note=note,
            idempotency_key=idempotency_key,
        )
        return await self.payment_repo.add(payment)

    async def settle_full_balance(
        self, shop_id: int, customer_id: int, payment_method: str,
        note: Optional[str], recorded_by_user_id: Optional[int], idempotency_key: str,
    ) -> list[CreditPayment]:
        """Pays off every open statement and every un-statemented unpaid
        Credit bill for a customer in one action. Idempotency comes from
        state, not a lookup table: the customer row is locked for the whole
        operation, so a concurrent/retried call either blocks until this one
        commits (then finds nothing left outstanding and raises) or never
        overlaps it — either way nothing is settled twice."""
        customer = await self.customer_repo.get_locked(customer_id)
        if not customer or customer.shop_id != shop_id:
            raise ValueError("Customer not found")

        statements = await self.statement_repo.list_open_locked_by_customer(customer_id)
        bills = await self.statement_repo.list_unbilled_completed_credit_bills(shop_id, customer_id)

        total_settled = Decimal("0.00")
        payments: list[CreditPayment] = []
        now = dt.datetime.now(timezone.utc)

        for statement in statements:
            outstanding = statement.total_amount - (statement.amount_paid or Decimal("0.00"))
            if outstanding <= 0:
                continue
            statement.amount_paid = statement.total_amount
            statement.status = "Paid"
            bills_result = await self.db.execute(
                select(Bill).where(Bill.credit_statement_id == statement.id)
            )
            for bill in bills_result.scalars().all():
                bill.payment_status = "Paid"
                bill.amount_paid = bill.total_amount
            # idempotency_key is String(36) — a bare UUID. Concatenating a
            # suffix here overflowed that column the moment a customer had
            # more than one outstanding item (StringDataRightTruncationError).
            # uuid5 derives a deterministic, always-36-char key instead.
            key = idempotency_key if not payments else str(uuid.uuid5(uuid.NAMESPACE_OID, f"{idempotency_key}:stmt:{statement.id}"))
            payment = CreditPayment(
                shop_id=shop_id, customer_id=customer_id, credit_statement_id=statement.id,
                amount=outstanding, payment_method=payment_method, paid_at=now,
                recorded_by_user_id=recorded_by_user_id, note=note, idempotency_key=key,
            )
            payments.append(await self.payment_repo.add(payment))
            total_settled += outstanding

        for bill in bills:
            outstanding = bill.total_amount - (bill.amount_paid or Decimal("0.00"))
            if outstanding <= 0:
                continue
            bill.amount_paid = bill.total_amount
            bill.payment_status = "Paid"
            key = idempotency_key if not payments else str(uuid.uuid5(uuid.NAMESPACE_OID, f"{idempotency_key}:bill:{bill.id}"))
            payment = CreditPayment(
                shop_id=shop_id, customer_id=customer_id, bill_id=bill.id,
                amount=outstanding, payment_method=payment_method, paid_at=now,
                recorded_by_user_id=recorded_by_user_id, note=note, idempotency_key=key,
            )
            payments.append(await self.payment_repo.add(payment))
            total_settled += outstanding

        if not payments:
            raise ValueError("No outstanding balance to settle")

        await self.release_credit(customer.id, total_settled, _customer=customer)
        return payments

    async def list_accounts(
        self, shop_id: int, *, q: str = "", sort_by: str = "urgency", order: str = "desc",
        overdue_only: bool = False, payment_term_type: Optional[str] = None,
        due_from: Optional[dt.date] = None, due_to: Optional[dt.date] = None,
        page_limit: int = 10, page_offset: int = 0,
    ) -> dict:
        """Credit Book overview: every credit account for the shop, ranked
        by urgency by default, with search/sort/filter applied. Stats are
        computed over the full unfiltered set so the summary cards stay
        stable while the list below is searched/filtered. Pagination is a
        plain slice of the already-sorted list (not a DB LIMIT/OFFSET)
        because "urgency" is a derived Python sort key (days_overdue +
        utilization %, not a single column) that needs the full filtered
        set in memory to rank — acceptable since credit-enabled customers
        are a bounded subset of the shop's full customer list."""
        shop_res = await self.db.execute(select(Shop.timezone).where(Shop.id == shop_id))
        shop_tz = shop_res.scalar_one_or_none() or "UTC"
        today = shop_local(dt.datetime.now(timezone.utc), shop_tz).date()

        rows = await self.statement_repo.list_credit_accounts(
            shop_id, today, q=q, overdue_only=overdue_only,
            payment_term_type=payment_term_type, due_from=due_from, due_to=due_to,
        )

        def utilization(row: dict) -> float:
            limit = row["customer"].credit_limit or Decimal("0.00")
            if not limit:
                return 0.0
            return float((row["customer"].credit_balance or Decimal("0.00")) / limit)

        sort_keys = {
            "urgency": lambda r: (r["days_overdue"], utilization(r)),
            "balance": lambda r: float(r["customer"].credit_balance or 0),
            "overdue_days": lambda r: r["days_overdue"],
            "name": lambda r: r["customer"].name.lower(),
        }
        key_fn = sort_keys.get(sort_by, sort_keys["urgency"])
        rows.sort(key=key_fn, reverse=(order != "asc"))

        accounts = []
        for row in rows:
            customer = row["customer"]
            limit = customer.credit_limit
            balance = customer.credit_balance or Decimal("0.00")
            accounts.append({
                "id": customer.id,
                "slug": customer.slug,
                "name": customer.name,
                "phone_number": customer.phone_number,
                "country_code": customer.country_code,
                "credit_balance": balance,
                "credit_limit": limit,
                "utilization_pct": round(float(balance / limit) * 100, 1) if limit else None,
                "payment_term_type": customer.payment_term_type,
                "due_date": row["earliest_due_date"],
                "days_overdue": row["days_overdue"],
            })

        accounts_total = len(accounts)
        accounts = accounts[page_offset:page_offset + page_limit]

        all_rows = await self.statement_repo.list_credit_accounts(shop_id, today)
        overdue_rows = [r for r in all_rows if r["days_overdue"] > 0]
        return {
            "accounts": accounts,
            "accounts_total": accounts_total,
            "stats": {
                "total_outstanding": sum((r["customer"].credit_balance or Decimal("0.00")) for r in all_rows),
                "overdue_amount": sum((r["customer"].credit_balance or Decimal("0.00")) for r in overdue_rows),
                "overdue_count": len(overdue_rows),
                "active_accounts": len(all_rows),
            },
        }

    async def get_ledger(self, customer_id: int) -> dict:
        customer = await self.customer_repo.get_by_id(customer_id)
        if not customer:
            raise ValueError("Customer not found")

        unpaid_bills = await self.statement_repo.list_unpaid_bills(customer_id)
        statements = await self.statement_repo.list_by_customer(customer_id)

        return {
            "customer_id": customer.id,
            "credit_balance": customer.credit_balance or Decimal("0.00"),
            "credit_limit": customer.credit_limit,
            "payment_term_type": customer.payment_term_type,
            "payment_term_value": customer.payment_term_value,
            "unpaid_bills": unpaid_bills,
            "statements": statements,
        }

    async def generate_statements_for_shop(self, shop_id: int) -> list[CreditStatement]:
        """Mirrors billing_cron.py's generate_due_invoices() shape. Naturally
        idempotent: bills already rolled into a statement have
        credit_statement_id set, so calling this twice in a row for the same
        shop finds zero remaining unbilled bills on the second call and
        generates nothing (task.yaml FEAT-7 fix_direction)."""
        now = dt.datetime.now(timezone.utc)
        shop_res = await self.db.execute(select(Shop.timezone).where(Shop.id == shop_id))
        shop_tz = shop_res.scalar_one_or_none() or "UTC"
        now_local = shop_local(now, shop_tz)

        due_customers = await self.customer_repo.list_credit_customers_due(now)
        generated: list[CreditStatement] = []

        for customer in due_customers:
            if customer.shop_id != shop_id:
                continue

            if not (customer.payment_term_type and customer.payment_term_value is not None):
                logger.warning(
                    f"Skipping statement generation for customer {customer.id}: "
                    f"is_credit_customer but no payment_term_type/value set"
                )
                continue

            bills = await self.statement_repo.list_unbilled_completed_credit_bills(shop_id, customer.id)
            if not bills:
                continue

            total_amount = sum((b.total_amount for b in bills), Decimal("0.00"))
            # Shop-local day, not raw UTC — same day-boundary fix as reserve_credit.
            period_start = min(shop_local(b.timestamp, shop_tz).date() for b in bills if b.timestamp)
            period_end = now_local.date()

            strategy = get_payment_term_strategy(customer.payment_term_type, customer.payment_term_value)
            due_date = strategy.compute_due_date(period_end)

            statement_number = await next_sequence_number(
                self.db,
                model=CreditStatement,
                sequence_column=CreditStatement.statement_number,
                scope_filters={"customer_id": customer.id},
                prefix=f"CR{shop_id}C{customer.id}",
                date_key=period_end.strftime("%Y%m"),
            )

            statement = CreditStatement(
                shop_id=shop_id,
                customer_id=customer.id,
                statement_number=statement_number,
                period_start=period_start,
                period_end=period_end,
                total_amount=total_amount,
                amount_paid=Decimal("0.00"),
                status="Open",
                due_date=due_date,
                generated_at=now,
            )
            statement = await self.statement_repo.add(statement)
            await self.db.flush()

            for bill in bills:
                bill.credit_statement_id = statement.id

            customer.next_statement_date = strategy.compute_next_statement_date(period_end)

            generated.append(statement)

        return generated
