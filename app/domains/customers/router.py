from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.shared.schemas import CustomerCreate
from app.domains.customers.service import CustomerService
from app.domains.auth.router import get_current_user

router = APIRouter(prefix="/customers", tags=["customers"])

@router.post("/")
async def create_customer(
    request: Request,
    schema: CustomerCreate = Depends(CustomerCreate.as_form),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    csrf_token = request.headers.get("X-CSRF-Token")
    expected_token = request.cookies.get("csrf_token")
    if not csrf_token or csrf_token != expected_token:
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
        
    service = CustomerService(db)
    try:
        customer = await service.create_customer(schema, current_user.shop_id)
        return {"status": "success", "customer": customer.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/")
async def list_customers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    service = CustomerService(db)
    customers = await service.list_customers(current_user.shop_id, skip, limit)
    return {"status": "success", "customers": [c.to_dict() for c in customers]}
