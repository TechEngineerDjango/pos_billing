import json
import logging
import secrets
import uvicorn
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from contextlib import asynccontextmanager
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import engine, AsyncSessionLocal, get_db
from app.core.base import Base
from app.shared.models import User, Shop, Subscription, Feature, PlanFeature, TenantFeatureOverride, LoginAttempt
from app.core.redis import close_redis
from app.domains.billing import router as billing
from app.domains.auth import router as auth
from app.domains.billing import admin_router as admin
from app.domains.tenancy import router as superadmin
from app.core.config import settings
from app.domains.auth.router import get_password_hash
from app.core.middleware.request_id import RequestIDMiddleware
from app.core.middleware.csrf import CSRFMiddleware


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


if settings.IS_PRODUCTION:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

# Startup/Shutdown Events
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables (dev) or rely on Alembic (prod)
    logger.info("Connecting to Database...")
    async with engine.begin() as conn:
        if not settings.IS_PRODUCTION:
            await conn.run_sync(Base.metadata.create_all)

    # Minimal Startup Seed — Only bootstrap the platform superadmin account.
    # Features, plans, shops, and all other data are created via the Superadmin UI.
    # Zero-hardcode policy: no feature keys or plan names live in code.
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == "superadmin"))
        if not result.scalars().first():
            hashed_pw = get_password_hash(settings.SUPERADMIN_PASSWORD)
            db.add(User(username="superadmin", hashed_password=hashed_pw, role="superadmin"))
            await db.commit()
            logger.info("Superadmin user created. Visit /superadmin/ to configure features and plans.")

    yield

    # Shutdown
    logger.info("Closing Database Connection...")
    await close_redis()
    logger.info("Redis connection closed.")


app = FastAPI(
    title="BurgerPOS",
    description="Zero-Gap POS & Billing System",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# S4: CORS Middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CSRFMiddleware)

# Static Files (CSS/Images)
app.mount("/static", StaticFiles(directory="app/frontend/static"), name="static")

# Routers
app.include_router(auth.router)
app.include_router(billing.router)
app.include_router(admin.router)
app.include_router(superadmin.router)


@app.get("/")
def home():
    return RedirectResponse(url="/billing/", status_code=303)


# ---------------------------------------------------------------------------
# R5: Health-check endpoint
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception:
        return JSONResponse({"status": "unhealthy", "database": "disconnected"}, status_code=503)


@app.exception_handler(401)
async def unauthorized_redirect_handler(request: Request, exc):
    if request.method == "GET":
        return RedirectResponse(url="/auth/login", status_code=303)
    return JSONResponse(
        status_code=401,
        content={"detail": "Not authenticated"},
    )

from pydantic import ValidationError

@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    """
    Globally catch Pydantic validation errors raised inside custom dependencies (like .as_form).
    This prevents the application from crashing with a 500 Internal Server Error
    and instead returns a standard 422 response that the frontend fetch API can handle.
    """
    errors = exc.errors()
    msg = errors[0].get("msg", "Invalid data submitted") if errors else "Invalid data submitted"
    return JSONResponse(
        status_code=422,
        content={"detail": msg},
    )


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
