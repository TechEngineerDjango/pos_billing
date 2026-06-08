from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Form
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings
from app.core.database import get_db
from app.shared.models import User, LoginAttempt, UserSession, SecurityLog
from app.shared.schemas import PasswordChangeRequest
from app.core.dependencies.csrf import verify_csrf

def get_device_name(user_agent: str) -> str:
    ua = user_agent.lower()
    if "iphone" in ua or "ipad" in ua:
        return "Apple iOS Device"
    elif "android" in ua:
        return "Android Device"
    elif "chrome" in ua:
        return "Google Chrome"
    elif "firefox" in ua:
        return "Mozilla Firefox"
    elif "safari" in ua:
        return "Apple Safari"
    elif "edge" in ua:
        return "Microsoft Edge"
    else:
        return "Desktop Web Browser"

from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/auth", tags=["Authentication"], dependencies=[Depends(verify_csrf)])
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "frontend" / "templates"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def _extract_token(request: Request) -> Optional[str]:
    """Extract JWT token from cookies or Authorization header."""
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    return token


async def _resolve_user(token: str, db: AsyncSession) -> Optional[User]:
    """Decode JWT and fetch the corresponding user. Returns None on any failure."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            return None
    except JWTError:
        return None

    result = await db.execute(select(User).where(User.username == username))
    return result.scalars().first()


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await _resolve_user(token, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user

async def get_optional_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    """Returns User if authenticated, else None. Does NOT raise 401."""
    token = _extract_token(request)
    if not token:
        return None
    return await _resolve_user(token, db)

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_current_user(request, db)
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"user": None, "shop": None})

@router.post("/login")
async def login(request: Request, response: Response, form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    client_ip = request.client.host
    now = datetime.now(timezone.utc)

    # R1: Database-backed rate limiting
    result = await db.execute(
        select(LoginAttempt).where(LoginAttempt.ip_address == client_ip)
    )
    attempt_record = result.scalars().first()

    if attempt_record and attempt_record.attempt_count >= 5:
        if now - attempt_record.last_attempt.replace(tzinfo=timezone.utc) < timedelta(minutes=15):
            wait_time = int(
                (timedelta(minutes=15) - (now - attempt_record.last_attempt.replace(tzinfo=timezone.utc))).total_seconds() / 60
            )
            # Log critical rate limit breach
            lockout_log = SecurityLog(
                event_type="RATE_LIMIT_EXCEEDED",
                severity="critical",
                ip_address=client_ip,
                details=f"IP locked out due to multiple failed login attempts. Attempted username: {form_data.username}"
            )
            db.add(lockout_log)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed login attempts. Please wait {wait_time} minutes.",
            )
        else:
            # Reset if lockout period has passed
            attempt_record.attempt_count = 0
            attempt_record.last_attempt = now
            await db.commit()

    # Find user
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalars().first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        # Update failed attempts in DB
        if not attempt_record:
            attempt_record = LoginAttempt(
                ip_address=client_ip, attempt_count=1, last_attempt=now
            )
            db.add(attempt_record)
        else:
            attempt_record.attempt_count += 1
            attempt_record.last_attempt = now
        
        # Log failed login attempt
        failed_log = SecurityLog(
            event_type="LOGIN_FAILED",
            severity="warning",
            ip_address=client_ip,
            details=f"Failed login attempt for username: {form_data.username}",
            user_id=user.id if user else None
        )
        db.add(failed_log)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract user attributes to prevent greenlet/lazy-load exceptions during DB transactions
    user_id = user.id
    username = user.username
    user_role = user.role

    # Clear failed attempts on success
    if attempt_record:
        await db.delete(attempt_record)

    # Check if user is active
    if hasattr(user, 'is_active') and not user.is_active:
        # Log deactivated access attempt
        deactivated_log = SecurityLog(
            event_type="DEACTIVATED_ACCESS_ATTEMPT",
            severity="warning",
            ip_address=client_ip,
            details=f"Deactivated user {username} attempted to access account.",
            user_id=user_id
        )
        db.add(deactivated_log)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
        )
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": username, "role": user_role}, expires_delta=access_token_expires
    )
    
    # Record Successful Session and Security Log
    user_agent_str = request.headers.get("user-agent", "Unknown Device")
    device_name = get_device_name(user_agent_str)

    stmt = select(UserSession).where(
        UserSession.user_id == user_id,
        UserSession.ip_address == client_ip,
        UserSession.user_agent == device_name,
        UserSession.is_active == True
    )
    session_res = await db.execute(stmt)
    existing_session = session_res.scalars().first()

    if existing_session:
        existing_session.login_count += 1
        existing_session.last_activity = now
        existing_session.session_token = access_token[-50:]
    else:
        new_session = UserSession(
            user_id=user_id,
            session_token=access_token[-50:],
            ip_address=client_ip,
            user_agent=device_name,
            login_count=1,
            is_active=True
        )
        db.add(new_session)

    # Log successful login
    success_log = SecurityLog(
        event_type="SUCCESSFUL_LOGIN",
        severity="info",
        ip_address=client_ip,
        details=f"User {username} logged in successfully from {device_name}",
        user_id=user_id
    )
    db.add(success_log)
    await db.commit()
    
    # Determine redirect URL based on role
    if user_role == "superadmin":
        redirect_url = "/superadmin/"
    elif user_role == "owner":
        redirect_url = "/admin/"
    else:  # cashier
        redirect_url = "/billing/"
    
    # Create redirect response with cookie
    redirect_response = RedirectResponse(url=redirect_url, status_code=303)
    redirect_response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=settings.IS_PRODUCTION,
    )
    
    return redirect_response

@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    # Clean up database active session
    try:
        token = _extract_token(request)
        if token:
            suffix = token[-50:]
            user = await _resolve_user(token, db)
            if user:
                stmt = select(UserSession).where(
                    UserSession.user_id == user.id,
                    UserSession.session_token == suffix,
                    UserSession.is_active == True
                )
                session_res = await db.execute(stmt)
                active_sess = session_res.scalars().first()
                if active_sess:
                    active_sess.is_active = False
                
                logout_log = SecurityLog(
                    event_type="SUCCESSFUL_LOGOUT",
                    ip_address=request.client.host if request.client else None,
                    details=f"User {user.username} logged out.",
                    user_id=user.id
                )
                db.add(logout_log)
                await db.commit()
    except Exception as e:
        pass

    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie(key="access_token", path="/")
    return response

@router.get("/change-password")
async def change_password_page(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    return templates.TemplateResponse(
        "change_password.html", 
        {"request": request, "user": current_user}
    )

@router.post("/change-password")
async def change_password(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    form_data = await request.form()
    
    try:
        request_data = PasswordChangeRequest(
            current_password=form_data.get("current_password", ""),
            new_password=form_data.get("new_password", "")
        )
    except ValueError as e:
        return templates.TemplateResponse(
            "change_password.html",
            {"request": request, "user": current_user, "error": str(e)}
        )

    # Verify current password
    if not verify_password(request_data.current_password, current_user.hashed_password):
        return templates.TemplateResponse(
            "change_password.html",
            {"request": request, "user": current_user, "error": "Incorrect current password"}
        )

    # Update password
    current_user.hashed_password = get_password_hash(request_data.new_password)
    db.add(current_user)
    await db.commit()
    
    # Standard Redirect with success indicator
    response = RedirectResponse(
        url="/admin/" if current_user.role != "superadmin" else "/superadmin/",
        status_code=status.HTTP_303_SEE_OTHER
    )
    return response
