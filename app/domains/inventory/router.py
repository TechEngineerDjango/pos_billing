"""
Inventory Management Router
/admin/inventory — owner/superadmin only, Pro-tier feature gated.

Endpoints:
  GET  /                        → Inventory list with stock levels
  GET  /alerts                  → JSON list of low-stock items
  GET  /by-sku/{sku}            → Lookup item by SKU (for scanner, any staff)
  GET  /{item_id}/movements     → Stock movement history
  POST /{item_id}/restock       → Add stock (scanner or manual)
  POST /{item_id}/update-sku    → Override SKU
  GET  /{item_id}/qr            → PNG QR code for item SKU

Security: CSRF + RBAC (owner/superadmin) + feature gate (inventory_management)
          + tenant isolation (all queries filter by shop_id)
"""
import io
import logging
import datetime as dt
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies.csrf import verify_csrf
from app.core.dependencies.features import require_feature
from app.domains.auth.services import require_owner_or_above, require_any_staff
from app.domains.auth.router import get_current_user
from app.shared.models import MenuItem, StockMovement, User, Shop, DEFAULT_LOW_STOCK_THRESHOLD
from app.shared.schemas import StockRestockRequest, SkuUpdateRequest
from app.domains.inventory.sku import generate_sku, validate_sku_format
from app.infrastructure.integrations.notifications import get_notification_service
from app.domains.expenses.service import ExpenseService
from app.shared.time_utils import shop_local

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/inventory",
    tags=["Inventory"],
    dependencies=[
        Depends(verify_csrf),
        Depends(require_feature("inventory_management")),
    ],
)


# ---------------------------------------------------------------------------
# Helper: resolve shop_id for current user (owner → their shop, superadmin → param or error)
# ---------------------------------------------------------------------------

async def _resolve_shop_id(current_user: User, shop_id: Optional[int], db: AsyncSession) -> int:
    if current_user.role == "superadmin":
        if not shop_id:
            # Default to first active shop
            res = await db.execute(select(Shop).where(Shop.is_active == True).limit(1))
            shop = res.scalars().first()
            if not shop:
                raise HTTPException(status_code=404, detail="No active shop found")
            return shop.id
        return shop_id
    if not current_user.shop_id:
        raise HTTPException(status_code=403, detail="No shop assigned to this account")
    return current_user.shop_id


# ---------------------------------------------------------------------------
# GET /admin/inventory/ — full inventory list
# ---------------------------------------------------------------------------

@router.get("/")
async def list_inventory(
    request: Request,
    shop_id: Optional[int] = None,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all menu items for the shop with current stock levels.
    Items with stock_quantity <= low_stock_threshold are flagged as low.
    """
    target_shop_id = await _resolve_shop_id(current_user, shop_id, db)

    result = await db.execute(
        select(MenuItem)
        .where(MenuItem.shop_id == target_shop_id, MenuItem.is_active == True)
        .order_by(MenuItem.category, MenuItem.name)
    )
    items = result.scalars().all()

    data = []
    low_stock_count = 0
    for item in items:
        threshold = item.low_stock_threshold or DEFAULT_LOW_STOCK_THRESHOLD
        is_tracked = item.stock_quantity is not None
        is_low = is_tracked and item.stock_quantity <= threshold
        is_out = is_tracked and item.stock_quantity <= 0
        if is_low:
            low_stock_count += 1
        data.append({
            **item.to_dict(),
            "is_tracked": is_tracked,
            "is_low_stock": is_low,
            "is_out_of_stock": is_out,
            "status": "out" if is_out else ("low" if is_low else ("ok" if is_tracked else "untracked")),
        })

    return JSONResponse({
        "items": data,
        "low_stock_count": low_stock_count,
        "total": len(data),
    })


# ---------------------------------------------------------------------------
# GET /admin/inventory/alerts — low-stock items only
# ---------------------------------------------------------------------------

@router.get("/alerts")
async def get_low_stock_alerts(
    shop_id: Optional[int] = None,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
):
    """Returns only items that are at or below their low_stock_threshold."""
    target_shop_id = await _resolve_shop_id(current_user, shop_id, db)

    result = await db.execute(
        select(MenuItem)
        .where(
            MenuItem.shop_id == target_shop_id,
            MenuItem.is_active == True,
            MenuItem.stock_quantity.isnot(None),
        )
        .order_by(MenuItem.stock_quantity)
    )
    items = result.scalars().all()

    alerts = []
    for item in items:
        threshold = item.low_stock_threshold or DEFAULT_LOW_STOCK_THRESHOLD
        if item.stock_quantity <= threshold:
            alerts.append({
                **item.to_dict(),
                "is_out_of_stock": item.stock_quantity <= 0,
            })

    return JSONResponse({"alerts": alerts, "count": len(alerts)})


# ---------------------------------------------------------------------------
# GET /admin/inventory/by-sku/{sku} — scanner lookup (any authenticated staff)
# ---------------------------------------------------------------------------

@router.get("/by-sku/{sku}")
async def get_item_by_sku(
    sku: str,
    shop_id: Optional[int] = None,
    current_user: User = Depends(require_any_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    Look up a menu item by SKU — used by QR/barcode scanner on both POS and inventory tab.
    Accessible by all authenticated staff (cashier, owner, superadmin).
    Result is always scoped to the requesting user's shop.
    """
    target_shop_id = await _resolve_shop_id(current_user, shop_id, db)

    result = await db.execute(
        select(MenuItem).where(
            MenuItem.shop_id == target_shop_id,
            MenuItem.sku == sku.upper().strip(),
            MenuItem.is_active == True,
        )
    )
    item = result.scalars().first()

    if not item:
        raise HTTPException(
            status_code=404,
            detail=f"No active item found with SKU '{sku}' in this shop",
        )

    threshold = item.low_stock_threshold or DEFAULT_LOW_STOCK_THRESHOLD
    is_tracked = item.stock_quantity is not None
    return JSONResponse({
        **item.to_dict(),
        "is_tracked": is_tracked,
        "is_low_stock": is_tracked and item.stock_quantity <= threshold,
        "is_out_of_stock": is_tracked and item.stock_quantity <= 0,
    })


# ---------------------------------------------------------------------------
# GET /admin/inventory/{item_id}/movements — audit history
# ---------------------------------------------------------------------------

@router.get("/{item_id}/movements")
async def get_stock_movements(
    item_id: int,
    shop_id: Optional[int] = None,
    limit: int = 50,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
):
    """Returns stock movement history for an item. Tenant-isolated by shop_id."""
    target_shop_id = await _resolve_shop_id(current_user, shop_id, db)

    # Verify item belongs to this shop
    item_res = await db.execute(
        select(MenuItem).where(
            MenuItem.id == item_id,
            MenuItem.shop_id == target_shop_id,
        )
    )
    item = item_res.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found in this shop")

    movements_res = await db.execute(
        select(StockMovement)
        .where(
            StockMovement.menu_item_id == item_id,
            StockMovement.shop_id == target_shop_id,
        )
        .options(selectinload(StockMovement.created_by))
        .order_by(StockMovement.created_at.desc())
        .limit(limit)
    )
    movements = movements_res.scalars().all()

    return JSONResponse({
        "item_id": item_id,
        "item_name": item.name,
        "sku": item.sku,
        "current_stock": item.stock_quantity,
        "movements": [m.to_dict() for m in movements],
    })


# ---------------------------------------------------------------------------
# POST /admin/inventory/{item_id}/restock — add stock
# ---------------------------------------------------------------------------

@router.post("/{item_id}/restock")
async def restock_item(
    item_id: int,
    data: StockRestockRequest = Depends(StockRestockRequest.as_form),
    shop_id: Optional[int] = None,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
):
    """
    Add stock to an item. Called from inventory tab (manual form or scanner).
    Creates a StockMovement record for full audit trail.
    """
    target_shop_id = await _resolve_shop_id(current_user, shop_id, db)

    item_res = await db.execute(
        select(MenuItem).where(
            MenuItem.id == item_id,
            MenuItem.shop_id == target_shop_id,
        )
    )
    item = item_res.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found in this shop")

    # Initialise stock if not yet tracked
    if item.stock_quantity is None:
        item.stock_quantity = 0.0

    item.stock_quantity += data.qty

    reason = "scanner_restock" if data.source == "scanner" else "restock"
    movement = StockMovement(
        menu_item_id=item.id,
        shop_id=target_shop_id,
        change_qty=data.qty,
        reason=reason,
        note=data.note or f"Stock added via {data.source}",
        created_by_user_id=current_user.id,
        created_at=dt.datetime.now(timezone.utc),
    )
    db.add(movement)

    # Auto-log an "Inventory Purchase" expense when a cost was given — same
    # transaction as the stock movement so a restock and its expense entry
    # are always created together or not at all.
    if data.unit_cost is not None and data.unit_cost > 0:
        shop_res = await db.execute(select(Shop.timezone).where(Shop.id == target_shop_id))
        shop_tz = shop_res.scalar_one_or_none() or "UTC"
        expense_date = shop_local(dt.datetime.now(timezone.utc), shop_tz).date()
        amount = (data.unit_cost * Decimal(str(data.qty))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        await ExpenseService(db).create_expense(
            shop_id=target_shop_id,
            category="Inventory Purchase",
            description=f"Restock: {item.name} x{data.qty}",
            amount=amount,
            tax_amount=Decimal("0.00"),
            vendor_name=None,
            expense_date=expense_date,
            payment_method=data.payment_method,
            created_by_user_id=current_user.id,
        )

    try:
        await db.commit()
        await db.refresh(item)
    except Exception as exc:
        await db.rollback()
        logger.error(f"Failed to restock item {item_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update stock. Please try again.")

    return RedirectResponse(url=f"/admin/?shop_id={target_shop_id}&tab=inventory", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# POST /admin/inventory/{item_id}/update-sku — override SKU
# ---------------------------------------------------------------------------

@router.post("/{item_id}/update-sku")
async def update_item_sku(
    item_id: int,
    data: SkuUpdateRequest = Depends(SkuUpdateRequest.as_form),
    shop_id: Optional[int] = None,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
):
    """Owner can override the auto-generated SKU (e.g. to match existing printed labels)."""
    target_shop_id = await _resolve_shop_id(current_user, shop_id, db)

    sku_clean = data.sku.upper().strip()
    if not validate_sku_format(sku_clean):
        raise HTTPException(
            status_code=422,
            detail="Invalid SKU format. Use uppercase letters, numbers, and hyphens only (3–64 chars).",
        )

    # Check uniqueness within shop
    existing_res = await db.execute(
        select(MenuItem).where(
            MenuItem.shop_id == target_shop_id,
            MenuItem.sku == sku_clean,
            MenuItem.id != item_id,
        )
    )
    if existing_res.scalars().first():
        raise HTTPException(status_code=409, detail=f"SKU '{sku_clean}' is already used by another item in this shop")

    item_res = await db.execute(
        select(MenuItem).where(
            MenuItem.id == item_id,
            MenuItem.shop_id == target_shop_id,
        )
    )
    item = item_res.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found in this shop")

    item.sku = sku_clean
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update SKU.")

    return JSONResponse({"status": "success", "item_id": item.id, "sku": item.sku})


# ---------------------------------------------------------------------------
# GET /admin/inventory/{item_id}/qr — PNG QR code for item SKU
# ---------------------------------------------------------------------------

@router.get("/{item_id}/qr")
async def get_item_qr_code(
    item_id: int,
    shop_id: Optional[int] = None,
    current_user: User = Depends(require_owner_or_above),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a PNG QR code image encoding the item's SKU.
    Used for printing SKU labels that can be scanned with the mobile camera.
    """
    import qrcode  # already in requirements.txt

    target_shop_id = await _resolve_shop_id(current_user, shop_id, db)

    item_res = await db.execute(
        select(MenuItem).where(
            MenuItem.id == item_id,
            MenuItem.shop_id == target_shop_id,
        )
    )
    item = item_res.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not item.sku:
        raise HTTPException(status_code=400, detail="This item has no SKU assigned yet")

    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(item.sku)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return Response(
        content=buf.read(),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{item.sku}.png"'},
    )
