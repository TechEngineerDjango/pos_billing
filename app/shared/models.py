import datetime as dt
from datetime import timezone

from sqlalchemy import (
    Column, Integer, String, Float, Numeric, Boolean, DateTime, Date,
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

    shops = relationship("Shop", back_populates="subscription")
    plan_feature_links = relationship("PlanFeature", back_populates="plan", cascade="all, delete-orphan")

    def to_dict(self):
        feature_keys = [lnk.feature.key for lnk in self.plan_feature_links if lnk.feature]
        return {
            "id": self.id, "name": self.name, "price": self.price,
            "billing_cycle": self.billing_cycle,
            "enabled_features": feature_keys
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
    
    # GST / Tax Settings
    gst_registered = Column(Boolean, default=False)
    gst_number = Column(String(15), nullable=True)
    
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
            "gst_registered": self.gst_registered,
            "gst_number": self.gst_number,
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

    # Credit System Fields
    is_credit_customer = Column(Boolean, default=False)
    credit_limit = Column(Numeric(10, 2), default=0.00)
    credit_balance = Column(Numeric(10, 2), default=0.00)
    payment_term_type = Column(String, nullable=True) # 'weekly', 'monthly', 'net_days'
    payment_term_value = Column(Integer, nullable=True) # Int representing the day/offset
    next_statement_date = Column(Date, nullable=True, index=True)  # FEAT-7 cron worker filters on this

    # B2B Fields
    company_name = Column(String, nullable=True)
    gst_number = Column(String(15), nullable=True)

    # Relationships
    bills = relationship("Bill", back_populates="customer")

    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "phone_number": self.phone_number,
            "country_code": self.country_code,
            "shop_id": self.shop_id,
            "credit_limit": float(self.credit_limit) if self.credit_limit else 0.0,
            "credit_balance": float(self.credit_balance) if self.credit_balance else 0.0,
            "is_credit_customer": self.is_credit_customer,
            "payment_term_type": self.payment_term_type,
            "payment_term_value": self.payment_term_value,
            "next_statement_date": self.next_statement_date.isoformat() if self.next_statement_date else None,
            "company_name": self.company_name,
            "gst_number": self.gst_number,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


# ============================================================================
# CUSTOMER ITEM PRICE (per-customer negotiated rate card — FEAT-3)
# ============================================================================

class CustomerItemPrice(Base):
    """A negotiated price for one (customer, menu item) pair. Sticky by
    default (valid_to=NULL = open-ended); an optional valid_to marks a
    temporary/promotional rate. See task.yaml FEAT-3 for the resolution
    rule: latest valid_from that covers as_of_date wins."""
    __tablename__ = "customer_item_prices"
    __table_args__ = (
        Index("ix_cust_item_price_customer_item", "customer_id", "menu_item_id"),
        UniqueConstraint(
            "customer_id", "menu_item_id", "valid_from",
            name="uq_customer_item_price_customer_item_valid_from",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True)
    price = Column(Numeric(10, 2), nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    customer = relationship("Customer")
    menu_item = relationship("MenuItem")

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "customer_id": self.customer_id,
            "menu_item_id": self.menu_item_id,
            "price": float(self.price),
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "is_active": self.is_active,
        }


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

#: Single source of truth for the low-stock fallback used everywhere an
#: item's own low_stock_threshold is NULL — the column default below, the
#: schema default, and every runtime "item.low_stock_threshold or ..."
#: fallback all import this instead of re-typing the literal.
DEFAULT_LOW_STOCK_THRESHOLD = 5.0


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
    low_stock_threshold = Column(Float, nullable=True, default=DEFAULT_LOW_STOCK_THRESHOLD)

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
    delivery_charge = Column(Numeric(10, 2), nullable=False, default=0.00, server_default="0.00")
    total_amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String, default="Cash")  # Cash, UPI, Credit
    status = Column(String, default="Completed")     # Completed, Held, Cancelled
    
    # Phase 2: Credit Bill enhancements
    payment_status = Column(String, default="Paid")  # Paid, Unpaid, PartiallyPaid
    amount_paid = Column(Numeric(10, 2), default=0.00)
    due_date = Column(DateTime(timezone=True), nullable=True)
    credit_statement_id = Column(Integer, ForeignKey("credit_statements.id", ondelete="SET NULL"), nullable=True, index=True)
    
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
            "delivery_charge": float(self.delivery_charge) if self.delivery_charge is not None else 0.0,
            "total_amount": float(self.total_amount),
            "payment_method": self.payment_method,
            "status": self.status,
            "payment_status": self.payment_status,
            "amount_paid": float(self.amount_paid) if self.amount_paid is not None else 0.0,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "items_snapshot": self.items_snapshot,
            "customer_id": self.customer_id,
            "shop_id": self.shop_id,
            "credit_statement_id": self.credit_statement_id,
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
# CREDIT STATEMENT + CREDIT PAYMENT (FEAT-5 — periodic consolidated billing
# and repayment collection; never hard-deleted, financial ledger records)
# ============================================================================

class CreditStatement(Base):
    """Periodic (weekly/monthly) rollup of a customer's Completed Unpaid
    Credit bills — the PRIMARY collection workflow (see task.yaml FEAT-5)."""
    __tablename__ = "credit_statements"
    __table_args__ = (
        Index("ix_credit_statements_customer_status", "customer_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    statement_number = Column(String, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    total_amount = Column(Numeric(10, 2), nullable=False)
    amount_paid = Column(Numeric(10, 2), default=0.00)
    status = Column(String, default="Open", index=True)  # Open/Sent/PartiallyPaid/Paid/Overdue
    due_date = Column(Date, nullable=True)
    generated_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))

    customer = relationship("Customer")

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "customer_id": self.customer_id,
            "statement_number": self.statement_number,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "total_amount": float(self.total_amount),
            "amount_paid": float(self.amount_paid or 0),
            "status": self.status,
            "due_date": self.due_date.isoformat() if self.due_date else None,
        }


class CreditPayment(Base):
    """Immutable record of a repayment against a CreditStatement or, directly,
    a single Bill. idempotency_key prevents a retried/double-tapped request on
    a flaky connection from double-applying (see task.yaml contract_standards #4)."""
    __tablename__ = "credit_payments"
    __table_args__ = (
        Index("ix_credit_payments_customer_created", "customer_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    credit_statement_id = Column(Integer, ForeignKey("credit_statements.id", ondelete="SET NULL"), nullable=True, index=True)
    bill_id = Column(Integer, ForeignKey("bills.id", ondelete="SET NULL"), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String(16), nullable=False)  # 'Cash' | 'UPI' | 'Card'
    paid_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    recorded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note = Column(String, nullable=True)
    idempotency_key = Column(String(36), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))

    customer = relationship("Customer")
    recorded_by = relationship("User")

    def to_dict(self):
        # Deliberately does NOT touch self.recorded_by / self.customer — those
        # relationships are never eagerly loaded by the credit repositories,
        # and accessing them here would trigger an async lazy-load outside an
        # awaited context (MissingGreenlet) on every payment response.
        return {
            "id": self.id,
            "slug": self.slug,
            "customer_id": self.customer_id,
            "credit_statement_id": self.credit_statement_id,
            "bill_id": self.bill_id,
            "amount": float(self.amount),
            "payment_method": self.payment_method,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "note": self.note,
            "recorded_by_user_id": self.recorded_by_user_id,
        }


# ============================================================================
# EXPENSE (FEAT-8 — business expense tracking; never hard-deleted, financial
# ledger record feeding the future GST report's input-tax-credit side)
# ============================================================================

class Expense(Base):
    """A single business expense entry. is_voided is the soft-delete — never
    hard-DELETE a financial record (same principle as the append-only
    StockMovement log elsewhere in this codebase)."""
    __tablename__ = "expenses"
    __table_args__ = (
        Index("ix_expenses_shop_category", "shop_id", "category"),
        Index("ix_expenses_shop_date", "shop_id", "expense_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(36), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    category = Column(String, nullable=False)
    description = Column(String, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    tax_amount = Column(Numeric(10, 2), default=0.00)
    vendor_name = Column(String, nullable=True)
    expense_date = Column(Date, nullable=False)
    payment_method = Column(String, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    is_voided = Column(Boolean, default=False)

    created_by = relationship("User")

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "category": self.category,
            "description": self.description,
            "amount": float(self.amount),
            "tax_amount": float(self.tax_amount or 0),
            "vendor_name": self.vendor_name,
            "expense_date": self.expense_date.isoformat() if self.expense_date else None,
            "payment_method": self.payment_method,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_voided": self.is_voided,
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
