from typing import Optional
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies.csrf import verify_csrf
from app.domains.auth.router import get_current_user
from app.domains.expenses.service import ExpenseService
from app.shared.schemas import ExpenseCreate, ExpenseUpdate, ExpenseListResponse

router = APIRouter(prefix="/admin/expenses", tags=["expenses"], dependencies=[Depends(verify_csrf)])


def _require_owner_or_above(current_user):
    if current_user.role not in ("owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("", response_model=ExpenseListResponse)
async def list_expenses(
    q: str = "",
    category: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    include_voided: bool = False,
    limit: int = 25,
    offset: int = 0,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    service = ExpenseService(db)
    expenses, total, total_amount = await service.list_expenses(
        current_user.shop_id, q=q, category=category, date_from=date_from, date_to=date_to,
        include_voided=include_voided, limit=limit, offset=offset,
    )
    return {
        "status": "success",
        "expenses": [e.to_dict() for e in expenses],
        "total": total,
        "total_amount": float(total_amount),
    }


@router.post("/add")
async def add_expense(
    payload: ExpenseCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_owner_or_above(current_user)
    service = ExpenseService(db)
    expense = await service.create_expense(
        current_user.shop_id, payload.category, payload.description, payload.amount,
        payload.tax_amount, payload.vendor_name, payload.expense_date, payload.payment_method,
        current_user.id,
    )
    await db.commit()
    return {"status": "success", "expense": expense.to_dict()}


@router.post("/update/{expense_slug}")
async def update_expense(
    expense_slug: str,
    payload: ExpenseUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_owner_or_above(current_user)
    service = ExpenseService(db)
    try:
        expense = await service.update_expense(
            expense_slug, current_user.shop_id, payload.category, payload.description,
            payload.amount, payload.tax_amount, payload.vendor_name, payload.expense_date,
            payload.payment_method,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "expense": expense.to_dict()}


@router.post("/void/{expense_slug}")
async def void_expense(
    expense_slug: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_owner_or_above(current_user)
    service = ExpenseService(db)
    try:
        expense = await service.void_expense(expense_slug, current_user.shop_id)
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "expense": expense.to_dict()}
