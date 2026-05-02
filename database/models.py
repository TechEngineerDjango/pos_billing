import datetime as dt
from datetime import timezone

from sqlalchemy import (
    Column, Integer, String, Float, Numeric, Boolean, DateTime,
    JSON, ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from database.base import Base


# ============================================================================
# SUBSCRIPTION & FEATURES (Platform-level)
# ============================================================================

class Feature(Base):
    """Toggleable features that can be enabled per subscription plan."""
    __tablename__ = "features"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)  # e.g. "dashboard", "whatsapp_bill"
    name = Column(String, nullable=False)  # Human-readable name
    description = Column(String, nullable=True)

    def to_dict(self):
        return {"id": self.id, "key": self.key, "name": self.name, "description": self.description}



class Subscription(Base):
    """Subscription plans (Free, Basic, Pro, etc.)"""
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)  # Free, Basic, Pro
    price = Column(Numeric(10, 2), default=0.00)
    # JSON list of feature keys enabled for this plan
    enabled_features = Column(JSON, default=list)  # ["dashboard", "sale_report"]
    
    shops = relationship("Shop", back_populates="subscription")

    def to_dict(self):
        return {"id": self.id, "name": self.name, "price": self.price, "enabled_features": self.enabled_features}



# ============================================================================
# SHOP (Multi-tenant)
# ============================================================================

class Shop(Base):
    """A shop/business entity. Each shop has its own users, menu, bills."""
    __tablename__ = "shops"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=True)
    contact = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)
    currency_symbol = Column(String, default="₹")
    theme = Column(String, default="dark")  # dark, light
    printer_ip = Column(String, nullable=True)
    menu_icon_url = Column(String, nullable=True) # New configurable menu icon

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

    # Billing Styles
    billing_font_color = Column(String, default="#ffffff")
    billing_card_bg_color = Column(String, default="#1e293b")
    billing_card_font_color = Column(String, default="#ffffff")

    # Button Colors
    inc_dec_button_color = Column(String, default="#f97316")
    cash_upi_option_color = Column(String, default="#1e293b")
    cash_upi_font_color = Column(String, default="#ffffff")
    
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=True)
    subscription = relationship("Subscription", back_populates="shops")
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc))
    
    # Relationships
    users = relationship("User", back_populates="shop")
    menu_items = relationship("MenuItem", back_populates="shop")
    bills = relationship("Bill", back_populates="shop")
    customers = relationship("Customer", back_populates="shop")

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
            "subscription_id": self.subscription_id,
            "is_active": self.is_active,
            "subscription_name": self.subscription.name if self.subscription else None
        }


# ============================================================================
# CUSTOMER (Customer information)
# ============================================================================

class Customer(Base):
    """Customer information for better tracking and communication."""
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone_number = Column(String, nullable=False, index=True)
    
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
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    category = Column(String, index=True, default="General")
    image_url = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    
    # Multi-tenancy: Each menu item belongs to a shop
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=True)
    shop = relationship("Shop", back_populates="menu_items")

    def to_dict(self):
        return {"id": self.id, "name": self.name, "price": self.price, "category": self.category, "image_url": self.image_url, "is_active": self.is_active, "shop_id": self.shop_id}



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
    bill_number = Column(String, index=True, nullable=False)
    total_amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String, default="Cash")  # Cash, UPI
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
