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

from database.session import engine, AsyncSessionLocal, get_db
from database.base import Base
from database.models import User, Shop, Subscription, Feature, LoginAttempt
from routers import billing, auth, admin, superadmin
from config import settings
from routers.auth import get_password_hash
from middleware.request_id import RequestIDMiddleware
from middleware.csrf import CSRFMiddleware


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
    # Startup: Create Config, Connect DB
    logger.info("Connecting to Database...")
    async with engine.begin() as conn:
        # In dev mode, auto-create tables. In prod, use Alembic migrations.
        if not settings.IS_PRODUCTION:
            await conn.run_sync(Base.metadata.create_all)
    
    # Seed Initial Data
    async with AsyncSessionLocal() as db:
        # 1. Ensure Plans and Features exist
        result = await db.execute(select(Subscription).where(Subscription.name == "Basic"))
        if not result.scalars().first():
            logger.info("Creating default subscription plans and features")
            free_plan = Subscription(name="Free", price=0.0, enabled_features=["billing"])
            basic_plan = Subscription(name="Basic", price=499.0, enabled_features=["billing", "dashboard", "sale_report"])
            pro_plan = Subscription(name="Pro", price=999.0, enabled_features=["billing", "dashboard", "sale_report", "inventory", "stock_count", "customer_management", "whatsapp_bill"])
            db.add_all([free_plan, basic_plan, pro_plan])
            
            features = [
                Feature(key="billing", name="Billing", description="Basic POS billing"),
                Feature(key="dashboard", name="Dashboard", description="Sales dashboard view"),
                Feature(key="sale_report", name="Sale Reports", description="View and export sale reports"),
                Feature(key="inventory", name="Inventory Management", description="Manage stock and inventory"),
                Feature(key="stock_count", name="Stock Count", description="Physical stock counting"),
                Feature(key="customer_management", name="Customer Management", description="Create and manage customers"),
                Feature(key="whatsapp_bill", name="WhatsApp Bill", description="Send bills via WhatsApp"),
                Feature(key="balance_calculation", name="Balance Calculation", description="Cash payment balance calculation"),
            ]
            db.add_all(features)
            await db.commit()

        # 2. Ensure Superadmin exists
        result = await db.execute(select(User).where(User.username == "superadmin"))
        if not result.scalars().first():
            hashed_pw = get_password_hash(settings.SUPERADMIN_PASSWORD)
            db.add(User(username="superadmin", hashed_password=hashed_pw, role="superadmin"))
            await db.commit()

        # 3. Ensure Demo Shop exists
        result = await db.execute(select(Shop).where(Shop.name == "Demo Burger Shop"))
        demo_shop = result.scalars().first()
        if not demo_shop:
            result = await db.execute(select(Subscription).where(Subscription.name == "Basic"))
            basic_plan = result.scalars().first()
            demo_shop = Shop(name="Demo Burger Shop", address="123 Food Street", currency_symbol="₹", subscription_id=basic_plan.id)
            db.add(demo_shop)
            await db.commit()
            await db.refresh(demo_shop)

        # 4. Ensure Owner exists
        result = await db.execute(select(User).where(User.username == "owner"))
        if not result.scalars().first():
            hashed_pw = get_password_hash(settings.OWNER_PASSWORD)
            db.add(User(username="owner", hashed_password=hashed_pw, role="owner", shop_id=demo_shop.id))
            
            # Add sample items for the new owner
            from database.models import MenuItem
            sample_items = [
                MenuItem(name="Classic Burger", price=150.0, category="Burger", shop_id=demo_shop.id),
                MenuItem(name="French Fries", price=80.0, category="Sides", shop_id=demo_shop.id),
            ]
            db.add_all(sample_items)
            await db.commit()
            
    yield
    # Shutdown
    logger.info("Closing Database Connection...")

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
app.mount("/static", StaticFiles(directory="static"), name="static")

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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
