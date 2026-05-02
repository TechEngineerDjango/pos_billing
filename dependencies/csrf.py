import secrets
from fastapi import Request, HTTPException, status


async def verify_csrf(request: Request):
    """
    Reusable FastAPI dependency that validates the CSRF token.
    
    For safe methods (GET, HEAD, OPTIONS), validation is skipped.
    For unsafe methods (POST, PUT, DELETE), the token from the form body
    or X-CSRF-Token header is compared against the csrf_token cookie.
    
    Because this runs as a dependency (same execution context as the endpoint),
    request.form() is cached by Starlette — the endpoint receives the same
    parsed data without any body-stream issues.
    """
    # Skip validation for safe HTTP methods
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    csrf_cookie = request.cookies.get("csrf_token")
    if not csrf_cookie:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing from cookies"
        )

    # 1. Check header first (for fetch/AJAX requests like POS billing and login)
    csrf_header = request.headers.get("x-csrf-token")
    if csrf_header and secrets.compare_digest(csrf_cookie, csrf_header):
        request.state.csrf_token = csrf_cookie
        return

    # 2. Check form data (for standard HTML form POST submissions)
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        csrf_form = form.get("csrf_token")
        if csrf_form and isinstance(csrf_form, str) and secrets.compare_digest(csrf_cookie, csrf_form):
            request.state.csrf_token = csrf_cookie
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="CSRF token validation failed"
    )
