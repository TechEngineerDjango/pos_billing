from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta
from typing import Optional

from config import settings
from database.session import get_db
from database.models import User

from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])
templates = Jinja2Templates(directory="templates")

# In-memory storage for login attempts (IP-based)
# Key: client_ip, Value: {count: int, last_attempt: datetime}
login_attempts = {}

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        # Check Authorization header as fallback if not using cookies purely
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    
    if not token:
         raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

async def get_optional_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    """Returns User if authenticated, else None. Does NOT raise 401."""
    token = request.cookies.get("access_token")
    if not token:
        # Check Header
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    
    if not token:
        return None

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            return None
    except JWTError:
        return None
    
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    return user

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_current_user(request, db)
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"user": None, "shop": None})

@router.post("/login")
async def login(request: Request, response: Response, form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    client_ip = request.client.host
    now = datetime.utcnow()

    # Rate limiting check
    if client_ip in login_attempts:
        attempt_data = login_attempts[client_ip]
        if attempt_data["count"] >= 5:
            if now - attempt_data["last_attempt"] < timedelta(minutes=15):
                wait_time = int((timedelta(minutes=15) - (now - attempt_data["last_attempt"])).total_seconds() / 60)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many failed login attempts. Please wait {wait_time} minutes."
                )
            else:
                # Reset if lockout time has passed
                login_attempts[client_ip] = {"count": 0, "last_attempt": now}

    # Find user
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalars().first()
    
    if not user or not verify_password(form_data.password, user.hashed_password):
        # Update failed attempts on failure
        if client_ip not in login_attempts:
            login_attempts[client_ip] = {"count": 1, "last_attempt": now}
        else:
            login_attempts[client_ip]["count"] += 1
            login_attempts[client_ip]["last_attempt"] = now

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Clear failed attempts on success
    if client_ip in login_attempts:
        del login_attempts[client_ip]

    # Check if user is active
    if hasattr(user, 'is_active') and not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
        )
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role}, expires_delta=access_token_expires
    )
    
    # Determine redirect URL based on role
    if user.role == "superadmin":
        redirect_url = "/superadmin/"
    elif user.role == "owner":
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
        expires=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=False 
    )
    
    return redirect_response

@router.api_route("/logout", methods=["GET", "POST"])
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=303)
    # Explicitly clear the cookie on the response being returned
    response.delete_cookie(key="access_token", path="/")
    return response
