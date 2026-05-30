"""
SKU Generation Service — Industrial Standard (Option A)

Format: {SHOP_CODE}-{CAT_CODE}-{SEQUENCE}
Example: BURG-BRG-001, BURG-DRK-002

Rules:
- SHOP_CODE: first 4 alphanumeric chars of shop name, uppercased
- CAT_CODE: first 3 alphanumeric chars of category, uppercased
- SEQUENCE: auto-incremented per shop, zero-padded to 3 digits
- SKU is unique per shop (enforced at DB level via unique index)
- Owner may override the SKU with any alphanumeric+hyphen string (max 64 chars)
"""
import re
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^A-Z0-9]")


def _slugify(text: str, length: int) -> str:
    """Extract first `length` uppercase alphanumeric chars from text."""
    cleaned = _SLUG_RE.sub("", text.upper())
    return cleaned[:length] if cleaned else "XXX"


def validate_sku_format(sku: str) -> bool:
    """
    Validate a user-supplied SKU.
    Allowed: uppercase letters, digits, hyphens. Length 3–64.
    """
    return bool(re.match(r"^[A-Z0-9\-]{3,64}$", sku.upper()))


async def generate_sku(
    shop_name: str,
    category: str,
    shop_id: int,
    db: AsyncSession,
) -> str:
    """
    Auto-generate a unique SKU for a new menu item.

    Algorithm:
      1. Derive SHOP_CODE (4 chars) and CAT_CODE (3 chars) from name slugs.
      2. Query max existing sequence for this shop+category prefix.
      3. Increment by 1, zero-pad to 3 digits.
      4. Return SKU string.

    This is idempotent: calling twice with same args yields consecutive SKUs.
    """
    from app.shared.models import MenuItem

    shop_code = _slugify(shop_name, 4) or "SHOP"
    cat_code = _slugify(category, 3) or "GEN"
    prefix = f"{shop_code}-{cat_code}-"

    # Find highest existing sequence number for this prefix in this shop
    result = await db.execute(
        select(MenuItem.sku)
        .where(
            MenuItem.shop_id == shop_id,
            MenuItem.sku.like(f"{prefix}%"),
        )
    )
    existing_skus = result.scalars().all()

    max_seq = 0
    for existing in existing_skus:
        if existing and existing.startswith(prefix):
            suffix = existing[len(prefix):]
            if suffix.isdigit():
                max_seq = max(max_seq, int(suffix))

    new_seq = max_seq + 1
    sku = f"{prefix}{str(new_seq).zfill(3)}"
    logger.debug(f"Generated SKU: {sku} for shop_id={shop_id}")
    return sku
