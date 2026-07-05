import datetime as dt
from datetime import timezone

from sqlalchemy import (
    Column, Integer, String, Float, Numeric, Boolean, DateTime,
    JSON, ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship
import uuid
from app.core.base import Base


# ============================================================================
# FEATURE CATALOG (DB-driven — zero hardcoded enums)
# ============================================================================

class Feature(Base):
    """
    Master catalog of all platform capabilities.
    Adding a new feature = INSERT into this table. Zero code changes.
    """
    __tablename__ = "features"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), unique=True, index=True, nullable=False)   # 'INVENTORY', 'GST'
    name = Column(String(128), nullable=False)                          # 'Inventory Management'
    description = Column(String, nullable=True)
    category = Column(String(64), nullable=True)                        # 'billing', 'ops', 'analytics'
    is_active = Column(Boolean, default=True)                          # Superadmin can disable globally
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))

    # M2M: which plans include this feature
    plan_links = relationship("PlanFeature", back_populates="feature", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "key": self.key, "name": self.name,
            "description": self.description, "category": self.category,
            "is_active": self.is_active
        }


class PlanFeature(Base):
    """
    M2M association: which features belong to which plan.
    Replacing the JSON enabled_features column — fully relational.
    """
    __tablename__ = "plan_features"
    __table_args__ = (Index("ix_plan_feature", "plan_id", "feature_id"),)

    plan_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True)
    feature_id = Column(Integer, ForeignKey("features.id", ondelete="CASCADE"), primary_key=True)

    plan = relationship("Subscription", back_populates="plan_feature_links")
    feature = relationship("Feature", back_populates="plan_links")


class Subscription(Base):
    """Subscription plans (Free, Basic, Pro, Enterprise)."""
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False)  # 'Free', 'Basic', 'Pro'
    price = Column(Numeric(10, 2), default=0.00)
    billing_cycle = Column(String(16), default="monthly")   # 'monthly', 'annual'
    is_active = Column(Boolean, default=True)
    # Legacy field kept for backward-compat during migration
    enabled_features = Column(JSON, default=list)

    shops = relationship("Shop", back_populates="subscription")
    plan_feature_links = relationship("PlanFeature", back_populates="plan", cascade="all, delete-orphan")

    def to_dict(self):
        # Resolve features from M2M links; fall back to legacy JSON if links empty
        feature_keys = [lnk.feature.key for lnk in self.plan_feature_links if lnk.feature]
        return {
            "id": self.id, "name": self.name, "price": self.price,
            "billing_cycle": self.billing_cycle,
            "enabled_features": feature_keys or (self.enabled_features or [])
        }


class TenantFeatureOverride(Base):
    """
    Per-tenant capability overrides.
    is_enabled=True  → force-grant feature even if not in plan.
    is_enabled=False → force-deny feature even if it IS in plan.
    """
    __tablename__ = "tenant_feature_overrides"
    __table_args__ = (Index("ix_tenant_feature_override", "shop_id", "feature_key"),)

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    feature_key = Column(String(64), nullable=False)          # matches Feature.key
    is_enabled = Column(Boolean, nullable=False)              # True=grant, False=deny
    reason = Column(String, nullable=True)                    # audit note
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))

    shop = relationship("Shop", back_populates="feature_overrides")

    def is_valid(self) -> bool:
        """Returns False if the override has expired."""
        if self.expires_at is None:
            return True
        return dt.datetime.now(timezone.utc) < self.expires_at



# ============================================================================
# SHOP (Multi-tenant)
# ============================================================================

class Shop(Base):
    """A shop/business entity. Each shop has its own users, menu, bills."""
    __tablename__ = "shops"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    name = Column(String, nullable=False)
    address = Column(String, nullable=True)
    contact = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)
    currency_symbol = Column(String, default="₹")
    theme = Column(String, default="dark")  # dark, light
    printer_ip = Column(String, nullable=True)
    menu_icon_url = Column(String, nullable=True) # New configurable menu icon
    favicon_url = Column(String, nullable=True) # Website Favicon

    # Branding / Customization
    font_color = Column(String, default="#ffffff")
    background_color = Column(String, default="#0f172a")  # Slate-900
    header_color = Column(String, default="#1e293b")      # Slate-800
    logo_size = Column(Integer, default=40)               # px (height)
    watermark_opacity = Column(Float, default=0.1)  # 0.0 to 1.0 (not money)
    
    # Advanced Customization
    card_bg_color = Column(String, default="#1e293b")     # Slate-800
    sidebar_bg_color = Column(String, default="#0f172a")  # Slate-900
    accent_color = Column(String, default="#f97316")      # Orange-500
    border_color = Column(String, default="rgba(255, 255, 255, 0.1)") # White/10
    
    # New Customization Fields (Feedback v2)
    price_card_bg = Column(String, default="#1e293b")  # Slate-800
    header_text_color = Column(String, default="#ffffff")
    cart_bg_color = Column(String, default="#0f172a")

    # Font Selection
    nav_font_family = Column(String, default="'Outfit', sans-serif")
    
    # POS Card Dimensions
    pos_card_width = Column(String, default="100%")
    pos_card_height = Column(String, default="auto")
    pos_card_image_width = Column(String, default="100%")
    pos_card_image_height = Column(String, default="8rem") # h-32

    # Panel Colors
    panel_font_color = Column(String, default="#ffffff")
    panel_bg_color = Column(String, default="#1e293b")
    
    # Printer and Receipt Customization
    receipt_footer = Column(String, default="Thank you for your order!")
    printer_paper_width = Column(String, default="80mm") # 58mm or 80mm
    printer_alignment = Column(String, default="center") # left or center

    # Billing Styles
    billing_font_color = Column(String, default="#ffffff")
    billing_card_bg_color = Column(String, default="#1e293b")
    billing_card_font_color = Column(String, default="#ffffff")

    # Button Colors
    inc_dec_button_color = Column(String, default="#f97316")
    cash_upi_option_color = Column(String, default="#1e293b")
    cash_upi_font_color = Column(String, default="#ffffff")
    upi_id = Column(String, nullable=True)  # Added for UPI QR code
    country_code = Column(String(3), nullable=True)  # Phone country code (e.g., "91" for India, "1" for US)
    timezone = Column(String, nullable=False, server_default="Asia/Kolkata")  # IANA name; controls how bill/receipt timestamps are displayed

    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=True)
    subscription = relationship("Subscription", back_populates="shops")
    
    billing_start_date = Column(DateTime(timezone=True), nullable=True)
    next_billing_date = Column(DateTime(timezone=True), nullable=True)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    
    # Relationships
    users = relationship("User", back_populates="shop")
    menu_items = relationship("MenuItem", back_populates="shop")
    bills = relationship("Bill", back_populates="shop")
    customers = relationship("Customer", back_populates="shop")
    feature_overrides = relationship("TenantFeatureOverride", back_populates="shop", cascade="all, delete-orphan")
    invoices = relationship("ShopInvoice", back_populates="shop", cascade="all, delete-orphan")

    def to_dict(self):
        """Convert object to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "contact": self.contact,
            "currency_symbol": self.currency_symbol,
            "logo_url": self.logo_url,
            "font_color": self.font_color,
            "background_color": self.background_color,
            "header_color": self.header_color,
            "logo_size": self.logo_size,
            "watermark_opacity": self.watermark_opacity,
            "card_bg_color": self.card_bg_color,
            "sidebar_bg_color": self.sidebar_bg_color,
            "accent_color": self.accent_color,
            "border_color": self.border_color,
            "price_card_bg": self.price_card_bg,
            "header_text_color": self.header_text_color,
            "cart_bg_color": self.cart_bg_color,
            "nav_font_family": self.nav_font_family,
            "pos_card_width": self.pos_card_width,
            "pos_card_height": self.pos_card_height,
            "pos_card_image_width": self.pos_card_image_width,
            "pos_card_image_height": self.pos_card_image_height,
            "panel_font_color": self.panel_font_color,
            "panel_bg_color": self.panel_bg_color,
            "billing_font_color": self.billing_font_color,
            "billing_card_bg_color": self.billing_card_bg_color,
            "billing_card_font_color": self.billing_card_font_color,
            "inc_dec_button_color": self.inc_dec_button_color,
            "cash_upi_option_color": self.cash_upi_option_color,
            "cash_upi_font_color": self.cash_upi_font_color,
            "upi_id": self.upi_id,
            "subscription_id": self.subscription_id,
            "is_active": self.is_active,
            "subscription_name": self.subscription.name if self.subscription else None,
            "billing_start_date": self.billing_start_date.isoformat() if self.billing_start_date else None,
            "next_billing_date": self.next_billing_date.isoformat() if self.next_billing_date else None
        }

# ============================================================================
# SHOP INVOICE (Platform -> Shop Fleet Billing)
# ============================================================================

class ShopInvoice(Base):
    """Platform bill issued to a shop for their subscription."""
    __tablename__ = "shop_invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(36), unique=True, index=True, default=lambda: f"INV-{uuid.uuid4().hex[:8].upper()}")
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)
    
    amount = Column(Numeric(10, 2), nullable=False)
    billing_cycle = Column(String(16), default="monthly")  # 'monthly', 'annual'
    
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False)
    
    status = Column(String(16), default="Pending") # Pending, Paid, Overdue, Cancelled
    paid_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    
    shop = relationship("Shop", back_populates="invoices")
    subscription = relationship("Subscription")

    def to_dict(self):
        return {
            "id": self.id,
            "invoice_number": self.invoice_number,
            "shop_id": self.shop_id,
            "subscription_id": self.subscription_id,
            "amount": float(self.amount),
            "billing_cycle": self.billing_cycle,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "status": self.status,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "shop_name": self.shop.name if self.shop else None,
            "subscription_name": self.subscription.name if self.subscription else None
        }


# ============================================================================
# CUSTOMER (Customer information)
# ============================================================================

class Customer(Base):
    """Customer information for better tracking and communication."""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    name = Column(String, nullable=False)
    phone_number = Column(String, nullable=False, index=True)
    country_code = Column(String(3), nullable=True)  # Phone country code (if None, use shop's default)

    # Multi-tenancy: Each customer belongs to a shop
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    shop = relationship("Shop", back_populates="customers")

    # Relationships
    bills = relationship("Bill", back_populates="customer")

    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))


# ============================================================================
# USER (Modified for multi-tenancy)
# ============================================================================

class User(Base):
    """Users with roles: superadmin (platform), owner (shop), cashier (shop)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    
    # Role: "superadmin" (platform admin), "owner" (shop owner), "cashier" (billing person)
    role = Column(String, default="cashier")
    
    # Foreign key to shop (nullable for superadmin who doesn't belong to any shop)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=True)
    shop = relationship("Shop", back_populates="users")
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))

    def to_dict(self):
        return {"id": self.id, "username": self.username, "role": self.role, "shop_id": self.shop_id, "is_active": self.is_active}



# ============================================================================
# MENU ITEMS (Modified for multi-tenancy)
# ============================================================================

class MenuItem(Base):
    """Menu items belonging to a specific shop."""
    __tablename__ = "menu_items"
    __table_args__ = (
        Index("ix_menuitems_shop_active", "shop_id", "is_active"),
        Index("ix_menuitems_shop_sku", "shop_id", "sku"),
    )

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    name = Column(String, index=True, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)     # Rate per unit when unit is set
    category = Column(String, index=True, default="General")
    image_url = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)

    # SKU — industrial standard: SHOP-CAT-SEQ, auto-generated, owner-overridable
    sku = Column(String(64), nullable=True, index=True)

    # Inventory tracking — NULL means this item is NOT tracked
    stock_quantity = Column(Float, nullable=True, default=None)
    reserved_quantity = Column(Float, nullable=False, default=0.0)
    low_stock_threshold = Column(Float, nullable=True, default=5.0)

    # Unit-based pricing:
    # None / 'piece' = fixed price (qty is whole numbers)
    # 'kg' / 'g' / 'liter' / 'ml' = price is per-unit; cashier enters actual qty at sale
    unit = Column(String(10), nullable=True, default=None)

    # Tax Configuration
    tax_rate = Column(Numeric(5, 2), nullable=True, default=0.00)

    # Multi-tenancy: Each menu item belongs to a shop
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=True)
    shop = relationship("Shop", back_populates="menu_items")

    # Inventory movements
    stock_movements = relationship("StockMovement", back_populates="menu_item", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "price": float(self.price),
            "category": self.category,
            "image_url": self.image_url,
            "is_active": self.is_active,
            "shop_id": self.shop_id,
            "unit": self.unit,   # None → fixed price; 'kg'/'g'/'liter'/'ml' → rate-based
            "sku": self.sku,
            "stock_quantity": self.stock_quantity,
            "reserved_quantity": self.reserved_quantity,
            "available_stock": self.stock_quantity - self.reserved_quantity if self.stock_quantity is not None else None,
            "low_stock_threshold": self.low_stock_threshold,
            "tax_rate": float(self.tax_rate) if self.tax_rate is not None else 0.0,
        }



# ============================================================================
# BILLS (Modified for multi-tenancy)
# ============================================================================

class Bill(Base):
    """Bills/transactions belonging to a specific shop."""
    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("shop_id", "bill_number", name="uq_shop_bill_number"),
        Index("ix_bills_shop_timestamp", "shop_id", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    bill_number = Column(String, index=True, nullable=False)
    subtotal_amount = Column(Numeric(10, 2), nullable=True)  # Added for tax module
    tax_amount = Column(Numeric(10, 2), nullable=True)       # Added for tax module
    total_amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String, default="Cash")  # Cash, UPI
    status = Column(String, default="Completed")     # Completed, Held, Cancelled
    timestamp = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    
    # CRITICAL: Store exact price/name at moment of sale
    items_snapshot = Column(JSON, nullable=False)
    
    # Customer association (optional)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    customer = relationship("Customer", back_populates="bills")
    
    # Multi-tenancy: Each bill belongs to a shop
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=True)
    shop = relationship("Shop", back_populates="bills") 

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "bill_number": self.bill_number,
            "total_amount": self.total_amount,
            "payment_method": self.payment_method,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "items_snapshot": self.items_snapshot,
            "customer_id": self.customer_id,
            "shop_id": self.shop_id
        }


# ============================================================================
# LOGIN ATTEMPTS (R1: DB-backed rate limiting)
# ============================================================================

class LoginAttempt(Base):
    """Tracks failed login attempts per IP for rate limiting."""
    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String, index=True, nullable=False, unique=True)
    attempt_count = Column(Integer, default=0)
    last_attempt = Column(DateTime(timezone=True), nullable=False)


# ============================================================================
# STOCK MOVEMENT (Inventory audit log)
# ============================================================================

class StockMovement(Base):
    """
    Immutable audit log of every stock change.
    Reasons: 'sale' (auto), 'restock' (owner/scanner), 'adjustment' (manual correction).
    """
    __tablename__ = "stock_movements"
    __table_args__ = (
        Index("ix_stock_movements_shop_created", "shop_id", "created_at"),
        Index("ix_stock_movements_item_created", "menu_item_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    change_qty = Column(Float, nullable=False)   # positive = stock added, negative = stock removed
    # reason: 'sale' | 'restock' | 'adjustment' | 'scanner_restock'
    reason = Column(String(32), nullable=False, default="adjustment")
    note = Column(String, nullable=True)          # e.g. "Bill #BS-1748440000" or "Received delivery"
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))

    menu_item = relationship("MenuItem", back_populates="stock_movements")
    created_by = relationship("User")

    def to_dict(self):
        return {
            "id": self.id,
            "menu_item_id": self.menu_item_id,
            "shop_id": self.shop_id,
            "change_qty": self.change_qty,
            "reason": self.reason,
            "note": self.note,
            "created_by_user_id": self.created_by_user_id,
            "created_by_username": self.created_by.username if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================================
# NOTIFICATION LOG (provision for future SMS/WhatsApp/email alerts)
# ============================================================================

class NotificationLog(Base):
    """
    Records all notifications sent by the system.
    channel: 'in_app' (active) | 'whatsapp' | 'sms' | 'email' (future).
    """
    __tablename__ = "notification_log"
    __table_args__ = (
        Index("ix_notification_log_shop_sent", "shop_id", "sent_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(32), nullable=False, default="in_app")  # in_app | whatsapp | sms | email
    message = Column(String, nullable=False)
    status = Column(String(16), nullable=False, default="sent")     # sent | failed | pending
    sent_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))


# ============================================================================
# ACTIVE USER SESSIONS & SECURITY LOGS
# ============================================================================

class UserSession(Base):
    """Tracks active user login sessions and device instances."""
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_token = Column(String, unique=True, index=True, nullable=False)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    login_time = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    last_activity = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    login_count = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)

    user = relationship("User")

    @property
    def active_duration(self) -> str:
        if not self.login_time:
            return "0s"
        now = dt.datetime.now(timezone.utc)
        diff = now - self.login_time.replace(tzinfo=timezone.utc)
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        days = hours // 24
        if days > 0:
            return f"{days}d {hours % 24}h"
        return f"{hours}h {minutes % 60}m"

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "user_id": self.user_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "login_time": self.login_time.isoformat() if self.login_time else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "login_count": self.login_count,
            "is_active": self.is_active,
            "active_duration": self.active_duration
        }


class SecurityLog(Base):
    """Tracks suspicious events, failed logins, and potential security intrusions."""
    __tablename__ = "security_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(64), nullable=False)      # e.g., 'CSRF_FAILURE', 'LOGIN_FAILED', 'SQL_INJECTION_SUSPICION', 'RATE_LIMIT_EXCEEDED'
    severity = Column(String(16), nullable=False, default="info")  # info, warning, critical
    ip_address = Column(String, nullable=True)
    details = Column(String, nullable=True)               # e.g., JSON or formatted message
    timestamp = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    user = relationship("User")

    def to_dict(self):
        return {
            "id": self.id,
            "event_type": self.event_type,
            "severity": self.severity,
            "ip_address": self.ip_address,
            "details": self.details,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None
        }
