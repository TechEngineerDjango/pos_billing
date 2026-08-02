import datetime as dt
from typing import List, Optional

from sqlalchemy import select, or_, union_all, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.repository import BaseRepository
from app.shared.models import Bill, CreditPayment, CreditStatement, Customer

OPEN_STATEMENT_STATUSES = ("Open", "Sent", "PartiallyPaid", "Overdue")


class CreditStatementRepository(BaseRepository[CreditStatement]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, CreditStatement)

    async def list_credit_accounts(
        self, shop_id: int, today: dt.date, *, q: str = "", overdue_only: bool = False,
        payment_term_type: Optional[str] = None,
        due_from: Optional[dt.date] = None, due_to: Optional[dt.date] = None,
    ) -> List[dict]:
        """One row per credit-enabled customer, joined to their earliest
        outstanding due date (from either an un-statemented Completed Credit
        bill or an open CreditStatement — whichever is sooner). No
        pagination: credit accounts are a bounded subset of a shop's
        customers, unlike the full customer/menu lists this session already
        moved to server-side search."""
        bill_due = (
            select(Bill.customer_id, func.min(cast(Bill.due_date, Date)).label("due_date"))
            .where(
                Bill.shop_id == shop_id,
                Bill.payment_method == "Credit",
                Bill.payment_status != "Paid",
                Bill.credit_statement_id.is_(None),
                Bill.status == "Completed",
                Bill.due_date.isnot(None),
            )
            .group_by(Bill.customer_id)
        )
        statement_due = (
            select(CreditStatement.customer_id, func.min(CreditStatement.due_date).label("due_date"))
            .where(
                CreditStatement.shop_id == shop_id,
                CreditStatement.status.in_(OPEN_STATEMENT_STATUSES),
                CreditStatement.due_date.isnot(None),
            )
            .group_by(CreditStatement.customer_id)
        )
        combined = union_all(bill_due, statement_due).subquery()
        earliest_due = (
            select(combined.c.customer_id, func.min(combined.c.due_date).label("earliest_due_date"))
            .group_by(combined.c.customer_id)
        ).subquery()

        query = (
            select(Customer, earliest_due.c.earliest_due_date)
            .outerjoin(earliest_due, earliest_due.c.customer_id == Customer.id)
            .where(Customer.shop_id == shop_id, Customer.is_credit_customer.is_(True))
        )
        query_clean = q.strip()
        if query_clean:
            query = query.where(or_(
                Customer.name.ilike(f"%{query_clean}%"),
                Customer.phone_number.ilike(f"%{query_clean}%"),
            ))
        if payment_term_type:
            query = query.where(Customer.payment_term_type == payment_term_type)
        if due_from:
            query = query.where(earliest_due.c.earliest_due_date >= due_from)
        if due_to:
            query = query.where(earliest_due.c.earliest_due_date <= due_to)
        if overdue_only:
            query = query.where(earliest_due.c.earliest_due_date < today)

        result = await self.db.execute(query)
        rows = []
        for customer, earliest_due_date in result.all():
            days_overdue = (today - earliest_due_date).days if earliest_due_date and earliest_due_date < today else 0
            rows.append({
                "customer": customer,
                "earliest_due_date": earliest_due_date,
                "days_overdue": days_overdue,
            })
        return rows

    async def get_earliest_due_dates(self, shop_id: int, customer_ids: List[int]) -> dict:
        """{customer_id: earliest_due_date} for a specific page of customer
        ids — same "earliest of any un-statemented bill or open statement"
        rule as list_credit_accounts, but scoped to a page of ids instead of
        the whole shop, for callers (like the Customers tab) that already
        paginated their own customer list and just need due dates merged in."""
        if not customer_ids:
            return {}

        bill_due = (
            select(Bill.customer_id, func.min(cast(Bill.due_date, Date)).label("due_date"))
            .where(
                Bill.shop_id == shop_id,
                Bill.customer_id.in_(customer_ids),
                Bill.payment_method == "Credit",
                Bill.payment_status != "Paid",
                Bill.credit_statement_id.is_(None),
                Bill.status == "Completed",
                Bill.due_date.isnot(None),
            )
            .group_by(Bill.customer_id)
        )
        statement_due = (
            select(CreditStatement.customer_id, func.min(CreditStatement.due_date).label("due_date"))
            .where(
                CreditStatement.shop_id == shop_id,
                CreditStatement.customer_id.in_(customer_ids),
                CreditStatement.status.in_(OPEN_STATEMENT_STATUSES),
                CreditStatement.due_date.isnot(None),
            )
            .group_by(CreditStatement.customer_id)
        )
        combined = union_all(bill_due, statement_due).subquery()
        result = await self.db.execute(
            select(combined.c.customer_id, func.min(combined.c.due_date))
            .group_by(combined.c.customer_id)
        )
        return {customer_id: due_date for customer_id, due_date in result.all()}

    async def get_locked_by_slug(self, slug: str) -> Optional[CreditStatement]:
        """Locks the statement row before a payment mutates amount_paid/status
        (same ordering discipline as CustomerRepository.get_locked)."""
        query = select(self.model).where(self.model.slug == slug).with_for_update()
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_by_customer(self, customer_id: int) -> List[CreditStatement]:
        query = (
            select(self.model)
            .where(self.model.customer_id == customer_id)
            .order_by(self.model.period_start.desc())
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_open_locked_by_customer(self, customer_id: int) -> List[CreditStatement]:
        """Open/PartiallyPaid/Overdue statements for a customer, locked —
        used by full-balance settlement so a concurrent generate/pay call
        can't read the same statement twice."""
        query = (
            select(self.model)
            .where(self.model.customer_id == customer_id, self.model.status.in_(OPEN_STATEMENT_STATUSES))
            .order_by(self.model.period_start)
            .with_for_update()
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_unbilled_completed_credit_bills(self, shop_id: int, customer_id: int) -> List[Bill]:
        """Completed Credit bills not yet rolled into a statement — the pool
        FEAT-7's generator sums into a new CreditStatement. Locked so a
        concurrent generation run (cron overlapping a manual trigger) blocks
        on these rows instead of reading the same unbilled set twice and
        creating two statements for the same bills."""
        query = (
            select(Bill)
            .where(
                Bill.shop_id == shop_id,
                Bill.customer_id == customer_id,
                Bill.payment_method == "Credit",
                Bill.payment_status != "Paid",
                Bill.credit_statement_id.is_(None),
                Bill.status == "Completed",
            )
            .order_by(Bill.timestamp)
            .with_for_update()
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_locked_bill_by_slug(self, shop_id: int, slug: str) -> Optional[Bill]:
        """Locks a single Bill row for a direct, pre-statement credit payment."""
        query = select(Bill).where(Bill.slug == slug, Bill.shop_id == shop_id).with_for_update()
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_unpaid_bills(self, customer_id: int) -> List[Bill]:
        """Credit bills for the ledger view — payment_status != Paid,
        regardless of whether they've been statemented yet."""
        query = (
            select(Bill)
            .where(
                Bill.customer_id == customer_id,
                Bill.payment_method == "Credit",
                Bill.payment_status != "Paid",
            )
            .order_by(Bill.timestamp.desc())
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_bills_for_customer(
        self, customer_id: int, *, date_from: Optional[dt.date] = None,
        date_to: Optional[dt.date] = None, min_amount: Optional[float] = None,
        max_amount: Optional[float] = None, status: Optional[str] = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[List[Bill], int]:
        """Full bill history (paid and unpaid) for a customer's Credit Book
        detail page, filterable by date/amount/status. Paginated — a
        long-standing credit customer can accumulate hundreds of bills."""
        conditions = [Bill.customer_id == customer_id, Bill.payment_method == "Credit"]
        if date_from:
            conditions.append(cast(Bill.timestamp, Date) >= date_from)
        if date_to:
            conditions.append(cast(Bill.timestamp, Date) <= date_to)
        if min_amount is not None:
            conditions.append(Bill.total_amount >= min_amount)
        if max_amount is not None:
            conditions.append(Bill.total_amount <= max_amount)
        if status:
            conditions.append(Bill.payment_status == status)

        # Single round trip via count(*) over() — see
        # app/domains/billing/admin_router.py's search_menu_items for the
        # same pattern and its offset-past-total caveat.
        result = await self.db.execute(
            select(Bill, func.count().over().label("total_count"))
            .where(*conditions).order_by(Bill.timestamp.desc()).limit(limit).offset(offset)
        )
        rows = result.all()
        bills = [row[0] for row in rows]
        total = rows[0][1] if rows else 0
        return bills, total


class CreditPaymentRepository(BaseRepository[CreditPayment]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, CreditPayment)

    async def get_by_idempotency_key(self, key: str) -> Optional[CreditPayment]:
        query = select(self.model).where(self.model.idempotency_key == key)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_by_customer(self, customer_id: int) -> List[CreditPayment]:
        query = (
            select(self.model)
            .where(self.model.customer_id == customer_id)
            .order_by(self.model.paid_at.desc())
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())
