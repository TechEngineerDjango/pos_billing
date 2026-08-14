# router.py

**Module:** `app.domains.auth.router`

## Functionality

Owns the entire authentication surface of the app: password hashing, JWT
issuance/decoding, the `get_current_user`/`get_optional_current_user`
dependencies every protected route in the app relies on (directly or via
`app.domains.auth.services.require_role`), and the HTTP routes for
login/logout/change-password. Also owns idle-session enforcement: on every
authenticated request it checks/updates a `UserSession` tracking row tied to
the current JWT, and force-logs-out (401) sessions that have been idle past
`settings.SESSION_IDLE_TIMEOUT_MINUTES`.

Router is mounted with `prefix="/auth"` and a router-level
`Depends(verify_csrf)` applied to every route in this file.

## Module-level objects

- `router` — `APIRouter(prefix="/auth", tags=["Authentication"], dependencies=[Depends(verify_csrf)])`.
- `templates` — `Jinja2Templates` pointed at `app/frontend/templates`.
- `pwd_context` — `CryptContext(schemes=["bcrypt"])`, used for all password hash/verify calls in this file.
- `oauth2_scheme` — `OAuth2PasswordBearer(tokenUrl="auth/token")` (declared, not actually used as a route dependency anywhere in this file — login reads the form body directly via `OAuth2PasswordRequestForm`).

## Functions

### `get_device_name(user_agent: str) -> str`
**Input:** `user_agent` — raw `User-Agent` header string.
**Output:** a short human-readable device/browser label: `"Apple iOS Device"`,
`"Android Device"`, `"Google Chrome"`, `"Mozilla Firefox"`, `"Apple Safari"`,
`"Microsoft Edge"`, or `"Desktop Web Browser"` as a fallback. Matches are
checked in that fixed order (case-insensitive substring match on the
lowercased header), so e.g. a Chrome-on-iPhone UA matches `"iphone"` first
and returns `"Apple iOS Device"`, never reaching the Chrome check.

### `verify_password(plain_password, hashed_password) -> bool`
**Input:** `plain_password` (str, user-supplied), `hashed_password` (str, bcrypt hash from `User.hashed_password`).
**Output:** `True`/`False` — delegates to `pwd_context.verify`.

### `get_password_hash(password) -> str`
**Input:** `password` (str, plaintext).
**Output:** bcrypt hash string — delegates to `pwd_context.hash`.

### `create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str`
**Input:** `data` — dict to encode as the JWT payload (in practice always
`{"sub": username, "role": user_role}`); `expires_delta` — optional
`timedelta`; if omitted, defaults to 15 minutes (in practice the caller
`login()` always supplies `ACCESS_TOKEN_EXPIRE_MINUTES`, so this default
is never actually exercised in this codebase).
**Output:** signed JWT string (`jwt.encode` with `settings.SECRET_KEY` /
`settings.ALGORITHM`), with an `exp` claim set to now + the delta.

### `_extract_token(request: Request) -> Optional[str]`
**Input:** `request` — the incoming `Request`.
**Output:** the JWT string, or `None`. Reads the `access_token` cookie
first; if absent, falls back to an `Authorization: Bearer <token>` header.

### `async _resolve_user(token: str, db: AsyncSession) -> Optional[User]`
**Input:** `token` — JWT string; `db` — active `AsyncSession`.
**Output:** the matching `User` row, or `None` if the JWT fails to decode
(`JWTError`), has no `sub` claim, or the username doesn't resolve to a user.
Never raises — all failure paths return `None`.

### `async get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User`
**Input:** `request`; `db` (injected).
**Output:** the authenticated `User`, or raises `HTTPException(401)`.
The primary auth dependency used across the app. Steps:
1. Extract token via `_extract_token`; no token → 401 `"Not authenticated"`.
2. Resolve user via `_resolve_user`; failure → 401 `"Invalid token"`.
3. Look up `UserSession` by `(user_id, session_token == token[-50:])`
   (matched by token value alone, not filtered by `is_active`).
4. **If found and `is_active=True`:** compute idle minutes since
   `last_activity`. If over `settings.SESSION_IDLE_TIMEOUT_MINUTES`: set
   `is_active=False`, write a `SESSION_IDLE_TIMEOUT` `SecurityLog` row,
   commit, raise 401 `"Session expired due to inactivity"`. Else if idle
   ≥5 minutes: update `last_activity` to now and commit (heartbeat,
   throttled to at most once per 5 minutes). Else: no DB write.
5. **If found and `is_active=False`:** raise 401 `"Session expired due to
   inactivity"` unconditionally — no reactivation. (Row was previously
   deactivated either by step 4's own idle-check on an earlier request, or
   by the background sweep in `app.workers.session_cron`.)
6. **If no row found at all:** insert a new `UserSession`
   (`is_active=True`, `login_count=1`, `ip_address` from `request.client.host`,
   `user_agent` from `get_device_name(request.headers["user-agent"])`),
   commit. Request proceeds (no 401).
7. Returns `user`.

### `async get_optional_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> Optional[User]`
**Input:** `request`; `db` (injected).
**Output:** `User` if a valid token is present and resolves, else `None`.
Never raises 401. Only actual caller in this file is `login_page` (to
redirect an already-logged-in visitor away from the login form). Does
**not** perform any idle-session check — a caller relying on this function
alone would not be subject to idle-timeout enforcement.

### `GET /auth/login` → `login_page(request, db)`
**Input:** `request`; `db` (injected). No query/body params.
**Output:** `RedirectResponse` to `/` (303) if already authenticated
(via `get_optional_current_user`), else renders `login.html` with
`{"user": None, "shop": None}`.

### `POST /auth/login` → `login(request, response, form_data, db)`
**Input:** `form_data` (`OAuth2PasswordRequestForm`, i.e. `username` +
`password` from a form body); `request`, `response` (unused directly —
cookie is set on a separately-constructed `redirect_response`, not the
injected `response` param), `db` (injected).
**Output:** on failure, raises `HTTPException` (429 if IP-rate-limited,
401 for bad credentials or a deactivated account); on success, a
`RedirectResponse` (303) to a role-based URL (`/superadmin/` for
`superadmin`, `/admin/` for `owner`, `/billing/` for anything else i.e.
`cashier`) with an `access_token` cookie set (`httponly=True`,
`samesite="lax"`, `secure=settings.IS_PRODUCTION`,
`max_age=ACCESS_TOKEN_EXPIRE_MINUTES*60`).
Behavior:
1. Rate limiting: looks up `LoginAttempt` by client IP. If
   `attempt_count >= 5` and the last attempt was under 15 minutes ago, logs
   a `RATE_LIMIT_EXCEEDED` `SecurityLog` (severity `critical`) and raises
   429. If the 15-minute window has elapsed, resets the counter instead.
2. Looks up `User` by username. If missing or password verification fails:
   increments/creates the `LoginAttempt` row, logs `LOGIN_FAILED`
   (severity `warning`), raises 401 `"Incorrect username or password"`.
3. If found and `user.is_active` is falsy: logs
   `DEACTIVATED_ACCESS_ATTEMPT` (severity `warning`), raises 401
   `"User account is deactivated"`.
4. On success: deletes the `LoginAttempt` row if one existed, mints a JWT
   via `create_access_token` with `{"sub": username, "role": user_role}`,
   **always inserts a new `UserSession` row** (does not look up or reuse
   any existing row for this user/ip/device — see inline comment at
   lines 281-287 explaining this is deliberate, to avoid orphaning another
   tab's still-valid token), logs `SUCCESSFUL_LOGIN`, commits, and returns
   the role-based redirect with the cookie set.

### `GET|POST /auth/logout` → `logout(request, db)`
**Input:** `request`; `db` (injected).
**Output:** always a `RedirectResponse` (303) to `/auth/login` with the
`access_token` cookie deleted (`path="/"`), regardless of whether anything
inside the `try` block succeeds.
Behavior: wrapped in a bare `try/except Exception: pass` — if a token is
present and resolves to a user, finds that user's **active** `UserSession`
row for this exact token (`is_active == True` filter, unlike
`get_current_user`'s lookup) and sets `is_active=False`, then logs a
`SUCCESSFUL_LOGOUT` `SecurityLog` and commits. Any exception in this block
is silently swallowed — the redirect+cookie-delete always happens either way.

### `GET /auth/change-password` → `change_password_page(request, current_user)`
**Input:** `current_user` — injected via `Depends(get_current_user)` (so
this route is subject to full idle-session enforcement).
**Output:** renders `change_password.html` with `{"request", "user": current_user}`.

### `POST /auth/change-password` → `change_password(request, current_user, db)`
**Input:** `current_user` (injected); `db` (injected); form body read
manually via `await request.form()` into `current_password`/`new_password`.
**Output:** on validation failure (raised inside `PasswordChangeRequest`'s
own validation, caught as `ValueError`) or wrong current password: re-renders
`change_password.html` with an `error` string, HTTP 200. On success: updates
`current_user.hashed_password` via `get_password_hash`, commits, and returns
a `RedirectResponse` (303) to `/superadmin/` (if the user's role is
`superadmin`) or `/admin/` (every other role, including `cashier` — no
`/billing/` case here, unlike the login redirect).
