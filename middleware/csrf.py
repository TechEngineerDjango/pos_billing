import secrets
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from typing import Callable, Awaitable

class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            # For safe methods, ensure the client gets a CSRF token cookie if they don't have one
            csrf_cookie = request.cookies.get("csrf_token")
            if not csrf_cookie:
                csrf_token = secrets.token_hex(32)
                request.state.csrf_token = csrf_token
            else:
                request.state.csrf_token = csrf_cookie

            response = await call_next(request)

            # If we generated a new token, attach it to the cookie
            if not csrf_cookie and request.state.csrf_token:
                from config import settings
                response.set_cookie(
                    key="csrf_token",
                    value=request.state.csrf_token,
                    httponly=True,
                    samesite="lax",
                    secure=settings.IS_PRODUCTION
                )
            return response
        else:
            # Unsafe methods, validate CSRF
            csrf_cookie = request.cookies.get("csrf_token")
            if not csrf_cookie:
                return JSONResponse(
                    {"detail": "CSRF token missing from cookies"},
                    status_code=status.HTTP_403_FORBIDDEN
                )

            # Check Headers first
            csrf_header = request.headers.get("x-csrf-token")
            if csrf_header and secrets.compare_digest(csrf_cookie, csrf_header):
                request.state.csrf_token = csrf_cookie
                return await call_next(request)

            # Check Form data
            content_type = request.headers.get("content-type", "")
            if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
                form = await request.form()
                csrf_form = form.get("csrf_token")
                if csrf_form and isinstance(csrf_form, str) and secrets.compare_digest(csrf_cookie, csrf_form):
                    request.state.csrf_token = csrf_cookie
                    return await call_next(request)

            return JSONResponse(
                {"detail": "CSRF token validation failed"},
                status_code=status.HTTP_403_FORBIDDEN
            )
