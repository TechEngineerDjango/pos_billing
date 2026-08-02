from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.dependencies.csrf import verify_csrf
from app.shared.schemas import CustomerCreate, CustomerCreditTermsUpdate, CustomerItemPriceCreate, CustomerItemPriceUpdate
from app.domains.customers.service import CustomerService
from app.domains.auth.router import get_current_user

router = APIRouter(prefix="/admin/customers", tags=["customers"], dependencies=[Depends(verify_csrf)])


def _require_owner_or_above(current_user):
    if current_user.role not in ("owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/{customer_slug}/credit-terms")
async def update_credit_terms(
    customer_slug: str,
    payload: CustomerCreditTermsUpdate = Depends(CustomerCreditTermsUpdate.as_form),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_owner_or_above(current_user)
    service = CustomerService(db)

    from app.domains.customers.repository import CustomerRepository
    customer = await CustomerRepository(db).get_by_slug(customer_slug)
    if not customer or customer.shop_id != current_user.shop_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    try:
        await service.set_credit_terms(
            customer.id, current_user.shop_id, payload.is_credit_customer,
            payload.credit_limit, payload.payment_term_type, payload.payment_term_value,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url=f"/admin/customer/edit/{customer_slug}", status_code=303)


@router.get("/{customer_slug}/prices")
async def list_item_prices(
    customer_slug: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.domains.customers.repository import CustomerRepository
    from app.shared.models import MenuItem
    from sqlalchemy import select

    customer = await CustomerRepository(db).get_by_slug(customer_slug)
    if not customer or customer.shop_id != current_user.shop_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    service = CustomerService(db)
    prices = await service.price_repository.list(customer_id=customer.id)

    # Resolve item names via one bulk lookup — the page never needs the
    # shop's full menu catalog just to label a customer's own price rows.
    item_ids = {p.menu_item_id for p in prices}
    names_by_id = {}
    if item_ids:
        result = await db.execute(select(MenuItem.id, MenuItem.name).where(MenuItem.id.in_(item_ids)))
        names_by_id = {row.id: row.name for row in result}

    payload = []
    for p in prices:
        d = p.to_dict()
        d["menu_item_name"] = names_by_id.get(p.menu_item_id, f"Item #{p.menu_item_id}")
        payload.append(d)

    return {"status": "success", "prices": payload}


@router.post("/{customer_slug}/prices")
async def add_item_price(
    customer_slug: str,
    payload: CustomerItemPriceCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_owner_or_above(current_user)
    from app.domains.customers.repository import CustomerRepository
    customer = await CustomerRepository(db).get_by_slug(customer_slug)
    if not customer or customer.shop_id != current_user.shop_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    service = CustomerService(db)
    try:
        price = await service.set_item_price(
            customer.id, payload.menu_item_id, current_user.shop_id,
            payload.price, payload.valid_from, payload.valid_to, current_user.id,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "success", "price": price.to_dict()}


@router.put("/{customer_slug}/prices/{price_slug}")
async def update_item_price(
    customer_slug: str,
    price_slug: str,
    payload: CustomerItemPriceUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_owner_or_above(current_user)
    from app.domains.customers.repository import CustomerRepository
    customer = await CustomerRepository(db).get_by_slug(customer_slug)
    if not customer or customer.shop_id != current_user.shop_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    service = CustomerService(db)
    try:
        price = await service.update_item_price(
            price_slug, customer.id, current_user.shop_id,
            payload.price, payload.valid_from, payload.valid_to,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "success", "price": price.to_dict()}


@router.delete("/{customer_slug}/prices/{price_slug}")
async def delete_item_price(
    customer_slug: str,
    price_slug: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_owner_or_above(current_user)
    from app.domains.customers.repository import CustomerRepository
    customer = await CustomerRepository(db).get_by_slug(customer_slug)
    if not customer or customer.shop_id != current_user.shop_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    service = CustomerService(db)
    try:
        await service.delete_item_price(price_slug, customer.id, current_user.shop_id)
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "success"}


@router.post("/")
async def create_customer(
    schema: CustomerCreate = Depends(CustomerCreate.as_form),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    service = CustomerService(db)
    try:
        customer = await service.create_customer(schema, current_user.shop_id)
        await db.commit()
        return {"status": "success", "customer": customer.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/")
async def list_customers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    service = CustomerService(db)
    customers = await service.list_customers(current_user.shop_id, skip, limit)
    return {"status": "success", "customers": [c.to_dict() for c in customers]}


@router.get("/search")
async def search_customers(
    q: str = "",
    credit_only: bool = False,
    include_due_date: bool = False,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Type-ahead customer search for the Credit Book / Rate Cards / Customers
    tab — server-side, capped, and paginated, so these pages never need to
    load the shop's full customer list client-side.

    include_due_date is opt-in (extra query) since only the Customers tab
    table needs "next due date" — the Rate Cards / Credit Book pickers that
    also call this endpoint don't."""
    from app.domains.customers.repository import CustomerRepository
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    customers, total = await CustomerRepository(db).search(
        current_user.shop_id, q, credit_only=credit_only, limit=limit, offset=offset,
    )

    due_dates = {}
    if include_due_date:
        from app.domains.credit.repository import CreditStatementRepository
        credit_ids = [c.id for c in customers if c.is_credit_customer]
        due_dates = await CreditStatementRepository(db).get_earliest_due_dates(current_user.shop_id, credit_ids)

    customer_dicts = []
    for c in customers:
        d = c.to_dict()
        if include_due_date:
            due = due_dates.get(c.id)
            d["next_due_date"] = due.isoformat() if due else None
        customer_dicts.append(d)

    return {"status": "success", "customers": customer_dicts, "total": total}
