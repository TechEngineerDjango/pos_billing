from datetime import date
from decimal import Decimal
from typing import Optional
import html as html_mod

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies.csrf import verify_csrf
from app.domains.auth.router import get_current_user
from app.domains.credit.service import CreditService
from app.domains.credit.repository import CreditStatementRepository
from app.domains.customers.repository import CustomerRepository
from app.shared.models import Shop
from app.shared.schemas import CreditLedgerResponse, CreditPaymentCreate, CreditPaymentResponse, CreditFullSettlementCreate

router = APIRouter(prefix="/admin/credit", tags=["credit"], dependencies=[Depends(verify_csrf)])


def _require_owner_or_above(current_user):
    if current_user.role not in ("owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/accounts")
async def list_credit_accounts(
    q: str = "",
    sort_by: str = "urgency",
    order: str = "desc",
    overdue_only: bool = False,
    payment_term_type: Optional[str] = None,
    due_from: Optional[date] = None,
    due_to: Optional[date] = None,
    limit: int = 10,
    offset: int = 0,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Credit Book overview — every credit account for the shop, searchable,
    sortable (urgency/balance/overdue_days/name, asc or desc), and filterable
    by overdue status, payment term, and due-date range. Paginated (a plain
    slice of the sorted list — see CreditService.list_accounts for why)."""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    service = CreditService(db)
    result = await service.list_accounts(
        current_user.shop_id, q=q, sort_by=sort_by, order=order,
        overdue_only=overdue_only, payment_term_type=payment_term_type,
        due_from=due_from, due_to=due_to, page_limit=limit, page_offset=offset,
    )
    return {
        "status": "success",
        "accounts": [
            {
                **a,
                "credit_balance": float(a["credit_balance"]),
                "credit_limit": float(a["credit_limit"]) if a["credit_limit"] is not None else None,
                "due_date": a["due_date"].isoformat() if a["due_date"] else None,
            }
            for a in result["accounts"]
        ],
        "accounts_total": result["accounts_total"],
        "stats": {
            "total_outstanding": float(result["stats"]["total_outstanding"]),
            "overdue_amount": float(result["stats"]["overdue_amount"]),
            "overdue_count": result["stats"]["overdue_count"],
            "active_accounts": result["stats"]["active_accounts"],
        },
    }


@router.get("/customers/{customer_slug}/whatsapp-reminder", response_class=HTMLResponse)
async def whatsapp_reminder_redirect(
    customer_slug: str,
    phone: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mirrors billing/admin_router.py's whatsapp_redirect_page pattern —
    server formats the message, frontend just window.open()s this URL."""
    from app.infrastructure.integrations.whatsapp import whatsapp_service, build_redirect_page

    if current_user.role not in ("owner", "superadmin", "cashier"):
        return HTMLResponse("<h1>Error: Unauthorized</h1>", status_code=403)

    customer = await CustomerRepository(db).get_by_slug(customer_slug)
    if not customer or customer.shop_id != current_user.shop_id:
        return HTMLResponse("<h1>Error: Customer not found</h1>", status_code=404)

    shop_res = await db.execute(select(Shop).where(Shop.id == current_user.shop_id))
    shop = shop_res.scalars().first()

    balance = customer.credit_balance or Decimal("0.00")
    currency = shop.currency_symbol if shop else "₹"
    shop_name = shop.name if shop else "us"
    message = (
        f"Hi {customer.name}, this is a reminder that you have an outstanding "
        f"balance of {currency}{balance:.2f} with {shop_name}. "
        f"Please settle at your earliest convenience. Thank you!"
    )
    country_code = customer.country_code or (shop.country_code if shop else None) or settings.DEFAULT_COUNTRY_CODE
    whatsapp_url = whatsapp_service.generate_wa_link(phone, message, country_code)
    safe_url = html_mod.escape(whatsapp_url, quote=True)

    return HTMLResponse(content=build_redirect_page(safe_url))


@router.get("/customers/{customer_slug}/ledger", response_model=CreditLedgerResponse)
async def get_customer_ledger(
    customer_slug: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    customer = await CustomerRepository(db).get_by_slug(customer_slug)
    if not customer or customer.shop_id != current_user.shop_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    service = CreditService(db)
    ledger = await service.get_ledger(customer.id)
    return {
        "customer_id": ledger["customer_id"],
        "credit_balance": ledger["credit_balance"],
        "credit_limit": ledger["credit_limit"],
        "payment_term_type": ledger["payment_term_type"],
        "payment_term_value": ledger["payment_term_value"],
        "unpaid_bills": [b.to_dict() for b in ledger["unpaid_bills"]],
        "statements": [s.to_dict() for s in ledger["statements"]],
    }


@router.get("/customers/{customer_slug}/bills")
async def list_customer_bills(
    customer_slug: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    status: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full Credit-bill history for the Credit Book detail page (paid and
    unpaid), filterable by date range, amount range, and status. Paginated
    — a long-standing credit customer can accumulate hundreds of bills."""
    customer = await CustomerRepository(db).get_by_slug(customer_slug)
    if not customer or customer.shop_id != current_user.shop_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    repo = CreditStatementRepository(db)
    bills, total = await repo.list_bills_for_customer(
        customer.id, date_from=date_from, date_to=date_to,
        min_amount=min_amount, max_amount=max_amount, status=status,
        limit=limit, offset=offset,
    )
    return {"status": "success", "bills": [b.to_dict() for b in bills], "total": total}


@router.get("/statements")
async def list_statements(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CreditStatementRepository(db)
    statements = await repo.list(shop_id=current_user.shop_id)
    return {"status": "success", "statements": [s.to_dict() for s in statements]}


@router.get("/statements/{statement_slug}")
async def get_statement(
    statement_slug: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CreditStatementRepository(db)
    statement = await repo.get_by_slug(statement_slug)
    if not statement or statement.shop_id != current_user.shop_id:
        raise HTTPException(status_code=404, detail="Statement not found")
    return {"status": "success", "statement": statement.to_dict()}


@router.post("/payments", response_model=CreditPaymentResponse)
async def record_payment(
    payload: CreditPaymentCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_owner_or_above(current_user)
    service = CreditService(db)
    try:
        payment = await service.record_payment(
            current_user.shop_id, payload.amount, payload.idempotency_key, payload.payment_method,
            statement_slug=payload.statement_slug, bill_slug=payload.bill_slug,
            recorded_by_user_id=current_user.id, note=payload.note,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return payment.to_dict()


@router.post("/customers/{customer_slug}/settle-full-balance")
async def settle_full_balance(
    customer_slug: str,
    payload: CreditFullSettlementCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_owner_or_above(current_user)
    customer = await CustomerRepository(db).get_by_slug(customer_slug)
    if not customer or customer.shop_id != current_user.shop_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    service = CreditService(db)
    try:
        payments = await service.settle_full_balance(
            current_user.shop_id, customer.id, payload.payment_method,
            payload.note, current_user.id, payload.idempotency_key,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "success",
        "settled_amount": float(sum(p.amount for p in payments)),
        "payments_count": len(payments),
    }


@router.post("/statements/generate")
async def generate_statements(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manual trigger/override for the periodic cron job. Naturally
    idempotent — bills already rolled into a statement are excluded from the
    next call's pool, so calling this twice in a row generates nothing the
    second time (task.yaml FEAT-7)."""
    _require_owner_or_above(current_user)
    service = CreditService(db)
    statements = await service.generate_statements_for_shop(current_user.shop_id)
    await db.commit()
    return {"status": "success", "generated": len(statements), "statements": [s.to_dict() for s in statements]}
