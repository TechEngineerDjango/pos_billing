# services.py

**Module:** `app.domains.auth.services`

## Functionality

Provides reusable, role-based FastAPI authorization dependencies, replacing
scattered inline `if current_user.role not in [...]` checks across routers
with a single dependency-injected pattern that raises HTTP 403 consistently.
Everything in this file builds on `get_current_user`
(`app.domains.auth.router`), so any route using these dependencies is also
automatically subject to that function's idle-session enforcement.

## Functions

### `require_role(*allowed_roles: str) -> Callable`
**Input:** `allowed_roles` — variadic list of role strings (e.g.
`"owner"`, `"superadmin"`, `"cashier"`).
**Output:** an async dependency function (`_check`) suitable for
`Depends(...)`, which itself depends on `get_current_user`.
Behavior: `_check(current_user: User = Depends(get_current_user)) -> User`
— if `current_user.role` is not in `allowed_roles`, raises
`HTTPException(403, detail=f"Insufficient permissions. Required role: {' or '.join(allowed_roles)}")`;
otherwise returns `current_user` unchanged. This is a dependency **factory**
— calling `require_role(...)` returns a new closure each time, which is why
the three convenience instances below are built once at import time rather
than called inline at every route.

## Module-level objects (pre-built dependencies)

- `require_owner_or_above = require_role("owner", "superadmin")` — used
  throughout `app.domains.billing.admin_router` and other owner-facing
  routers.
- `require_superadmin = require_role("superadmin")` — used by
  `app.domains.tenancy.router` for the superadmin dashboard/API routes.
- `require_any_staff = require_role("owner", "superadmin", "cashier")` —
  permits any authenticated staff role (i.e. functionally "any logged-in
  user," since these three are the only roles in the system per this file).
