from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Expense


async def compute_profit_loss(
    db: AsyncSession,
    shop_id: int,
    period_net_revenue: float,
    period_uncollected: float,
    period_start,
    today,
) -> dict:
    """Shop-wide Profit & Loss for the same period window admin_router.py
    already computes revenue over. period_net_revenue/period_uncollected are
    accumulated by the caller while it loops `bills` for its own analytics —
    passed in rather than re-queried here to avoid a second pass over bills.

    COGS is Expense rows tagged category="Inventory Purchase" (auto-created
    by inventory/router.py's restock endpoint when a unit_cost is given —
    that field is optional, so COGS is only as complete as what's entered at
    restock time). Every other category is Operating Expenses.

    Expense.expense_date is a shop-local calendar day (see
    inventory/router.py's shop_local() call when creating it), while
    period_start/today here are UTC-midnight boundaries — the same
    imprecision bills already have at their period boundaries, extended
    consistently to expenses rather than introducing a second, differently
    aligned boundary. A proper fix would apply shop_day_range_utc()
    (shared/time_utils.py) to both sides together; out of scope here.
    """
    cogs_amount = Expense.amount + func.coalesce(Expense.tax_amount, 0)
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(case((Expense.category == "Inventory Purchase", cogs_amount), else_=0)), 0
            ).label("cogs"),
            func.coalesce(
                func.sum(case((Expense.category != "Inventory Purchase", cogs_amount), else_=0)), 0
            ).label("operating_expenses"),
        ).where(
            Expense.shop_id == shop_id,
            Expense.is_voided.is_(False),
            Expense.expense_date >= period_start.date(),
            Expense.expense_date <= today.date(),
        )
    )
    cogs, operating_expenses = (float(v) for v in result.one())

    gross_profit = period_net_revenue - cogs
    net_profit = gross_profit - operating_expenses
    profit_margin = round((net_profit / period_net_revenue * 100), 1) if period_net_revenue > 0 else 0

    return {
        "net_revenue": round(period_net_revenue, 2),
        "cogs": round(cogs, 2),
        "operating_expenses": round(operating_expenses, 2),
        "gross_profit": round(gross_profit, 2),
        "net_profit": round(net_profit, 2),
        "profit_margin": profit_margin,
        "uncollected_credit": round(period_uncollected, 2),
    }
