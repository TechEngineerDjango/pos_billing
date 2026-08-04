from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.expenses.repository import ExpenseRepository
from app.shared.models import Expense

CATEGORIES = (
    "Rent", "Utilities", "Salaries", "Inventory Purchase",
    "Maintenance", "Marketing", "Transport", "Other",
)


class ExpenseService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = ExpenseRepository(db)

    async def create_expense(
        self,
        shop_id: int,
        category: str,
        description: str,
        amount: Decimal,
        tax_amount: Decimal,
        vendor_name: Optional[str],
        expense_date: date,
        payment_method: str,
        created_by_user_id: Optional[int],
    ) -> Expense:
        expense = Expense(
            shop_id=shop_id,
            category=category,
            description=description,
            amount=amount,
            tax_amount=tax_amount,
            vendor_name=vendor_name,
            expense_date=expense_date,
            payment_method=payment_method,
            created_by_user_id=created_by_user_id,
        )
        return await self.repository.add(expense)

    async def update_expense(
        self,
        expense_slug: str,
        shop_id: int,
        category: str,
        description: str,
        amount: Decimal,
        tax_amount: Decimal,
        vendor_name: Optional[str],
        expense_date: date,
        payment_method: str,
    ) -> Expense:
        expense = await self.repository.get_by_slug(expense_slug)
        if not expense or expense.shop_id != shop_id:
            raise ValueError("Expense not found")
        if expense.is_voided:
            raise ValueError("Cannot edit a voided expense")
        expense.category = category
        expense.description = description
        expense.amount = amount
        expense.tax_amount = tax_amount
        expense.vendor_name = vendor_name
        expense.expense_date = expense_date
        expense.payment_method = payment_method
        return expense

    async def void_expense(self, expense_slug: str, shop_id: int) -> Expense:
        # Financial ledger record — never hard-deleted (task.yaml FEAT-8
        # CORRECTNESS RULE), only soft-voided.
        expense = await self.repository.get_by_slug(expense_slug)
        if not expense or expense.shop_id != shop_id:
            raise ValueError("Expense not found")
        expense.is_voided = True
        return expense

    async def list_expenses(
        self,
        shop_id: int,
        *,
        q: str = "",
        category: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        include_voided: bool = False,
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[Expense], int, Decimal]:
        return await self.repository.list_filtered(
            shop_id, q=q, category=category, date_from=date_from, date_to=date_to,
            include_voided=include_voided, limit=limit, offset=offset,
        )
