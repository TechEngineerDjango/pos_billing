import secrets
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from typing import Callable, Awaitable


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    Middleware for CSRF cookie management only.
    
    Responsibility: Set the csrf_token cookie and request.state.csrf_token
    on GET requests so that templates can render the hidden input.
    
    POST/PUT/DELETE validation is handled by the verify_csrf dependency
    at the router level — this avoids the BaseHTTPMiddleware body-swallowing issue.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            csrf_cookie = request.cookies.get("csrf_token")
            if not csrf_cookie:
                csrf_token = secrets.token_hex(32)
                request.state.csrf_token = csrf_token
            else:
                request.state.csrf_token = csrf_cookie

            response = await call_next(request)

            # If we generated a new token, attach it to the cookie
            if not csrf_cookie and request.state.csrf_token:
                from app.core.config import settings
                response.set_cookie(
                    key="csrf_token",
                    value=request.state.csrf_token,
                    httponly=True,
                    samesite="lax",
                    secure=settings.IS_PRODUCTION
                )
            return response
        else:
            # Unsafe methods: pass through to router-level dependency for validation
            return await call_next(request)
