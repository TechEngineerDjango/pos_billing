import logging
import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from contextlib import asynccontextmanager

from database.session import engine, AsyncSessionLocal
from database.base import Base
from database.models import User, ShopProfile, Shop, Subscription, Feature
from routers import billing, auth, admin, superadmin
from config import settings
from routers.auth import get_password_hash
from sqlalchemy import select

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Startup/Shutdown Events
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create Config, Connect DB
    logger.info("Connecting to Database...")
    async with engine.begin() as conn:
        # Create Tables
        await conn.run_sync(Base.metadata.create_all)
    
    # Seed Initial Data
    async with AsyncSessionLocal() as db:
        # Check for Superadmin
        result = await db.execute(select(User).where(User.username == "superadmin"))
        superadmin_user = result.scalars().first()
        if not superadmin_user:
            logger.info("Creating default superadmin user (superadmin/superadmin)")
            hashed_pw = get_password_hash("superadmin")
            new_superadmin = User(username="superadmin", hashed_password=hashed_pw, role="superadmin")
            db.add(new_superadmin)
            
            # Create default subscription plans
            logger.info("Creating default subscription plans")
            free_plan = Subscription(
                name="Free", 
                price=0.0, 
                enabled_features=["billing"]
            )
            basic_plan = Subscription(
                name="Basic", 
                price=499.0, 
                enabled_features=["billing", "dashboard", "sale_report"]
            )
            pro_plan = Subscription(
                name="Pro", 
                price=999.0, 
                enabled_features=["billing", "dashboard", "sale_report", "inventory", "stock_count", "customer_management", "whatsapp_bill"]
            )
            db.add_all([free_plan, basic_plan, pro_plan])
            
            # Create default features
            logger.info("Creating default features")
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
            
            # Refresh to get IDs
            await db.refresh(basic_plan)
            
            # Create demo shop
            logger.info("Creating demo shop with sample menu items")
            demo_shop = Shop(
                name="Demo Burger Shop",
                address="123 Food Street",
                currency_symbol="₹",
                subscription_id=basic_plan.id
            )
            db.add(demo_shop)
            await db.commit()
            await db.refresh(demo_shop)
            
            # Create owner for demo shop
            logger.info("Creating demo owner user (owner/owner)")
            owner_user = User(
                username="owner",
                hashed_password=get_password_hash("owner"),
                role="owner",
                shop_id=demo_shop.id
            )
            db.add(owner_user)
            
            # Create cashier for demo shop
            logger.info("Creating demo cashier user (cashier/cashier)")
            cashier_user = User(
                username="cashier",
                hashed_password=get_password_hash("cashier"),
                role="cashier",
                shop_id=demo_shop.id
            )
            db.add(cashier_user)
            
            # Add sample menu items for demo shop
            from database.models import MenuItem
            sample_items = [
                MenuItem(name="Classic Burger", price=150.0, category="Burger", shop_id=demo_shop.id),
                MenuItem(name="Cheese Burger", price=180.0, category="Burger", shop_id=demo_shop.id),
                MenuItem(name="Chicken Burger", price=200.0, category="Burger", shop_id=demo_shop.id),
                MenuItem(name="French Fries", price=80.0, category="Sides", shop_id=demo_shop.id),
                MenuItem(name="Cold Drink", price=50.0, category="Beverages", shop_id=demo_shop.id),
            ]
            db.add_all(sample_items)
            
            await db.commit()
        
        # Check for legacy admin (will be migrated to owner role later)
        result = await db.execute(select(User).where(User.username == "admin"))
        admin_user = result.scalars().first()
        if admin_user and admin_user.role == "admin":
            # Migrate old admin to owner role
            admin_user.role = "owner"
            await db.commit()
            logger.info("Migrated legacy admin user to owner role")
            
    yield
    # Shutdown
    logger.info("Closing Database Connection...")

app = FastAPI(
    title="BurgerPOS", 
    description="Zero-Gap POS & Billing System",
    lifespan=lifespan
)

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


@app.exception_handler(401)
async def unauthorized_redirect_handler(request: Request, exc):
    if request.method == "GET":
        return RedirectResponse(url="/auth/login", status_code=303)
    return JSONResponse(
        status_code=401,
        content={"detail": "Not authenticated"}
    )

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
