"""
Reusable authorization dependencies for FastAPI routers.

Replaces scattered inline `if current_user.role not in [...]` checks
with proper dependency injection that raises HTTP 403. (Fixes S4 + P5)
"""
from fastapi import Depends, HTTPException, status
from app.shared.models import User
from app.domains.auth.router import get_current_user


def require_role(*allowed_roles: str):
    """
    Factory that creates a FastAPI dependency requiring specific roles.

    Usage:
        @router.post("/menu/add")
        async def add_menu(current_user: User = Depends(require_role("owner", "superadmin"))):
            ...
    """
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {' or '.join(allowed_roles)}"
            )
        return current_user
    return _check


# Pre-built convenience dependencies
require_owner_or_above = require_role("owner", "superadmin")
require_superadmin = require_role("superadmin")
require_any_staff = require_role("owner", "superadmin", "cashier")
