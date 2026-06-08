from pydantic import BaseModel, Field, validator
from typing import List, Optional
from fastapi import Form
from decimal import Decimal

class CartItem(BaseModel):
    id: int
    qty: float = Field(..., gt=0)  # float accepts whole (2) and fractional (1.6 kg) quantities; JSON-serializable

class BillCreate(BaseModel):
    items: List[CartItem]
    payment_method: str = "Cash"
    status: str = "Completed"
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None

import re

def validate_password_strength(password: str) -> str:
    # Single regex for: 8+ chars, uppercase, lowercase, digit, and special character
    pattern = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]).{8,}$"
    if not re.match(pattern, password):
        raise ValueError("Password does not meet the security policy requirements")
    return password

class UserCreate(BaseModel):
    username: str
    password: str
    shop_id: Optional[int] = None

    @validator("password")
    def validate_password(cls, v):
        return validate_password_strength(v)

    @classmethod
    def as_form(
        cls,
        username: str = Form(...),
        password: str = Form(...),
        shop_id: Optional[int] = Form(None)
    ):
        return cls(username=username, password=password, shop_id=shop_id)

class MenuItemCreate(BaseModel):
    name: str
    price: Decimal = Field(..., ge=0)
    category: str = "General"
    shop_id: Optional[int] = None
    unit: Optional[str] = None  # None/'piece' = fixed price; 'kg'/'g'/'liter'/'ml' = rate per unit
    sku: Optional[str] = None   # If provided, overrides auto-generated SKU
    low_stock_threshold: Optional[float] = Field(default=5.0, ge=0)
    tax_rate: Optional[Decimal] = Field(default=Decimal("0.00"), ge=0, le=100)

    @classmethod
    def as_form(
        cls,
        name: str = Form(...),
        price: Decimal = Form(...),
        category: str = Form("General"),
        shop_id: Optional[int] = Form(None),
        unit: Optional[str] = Form(None),
        sku: Optional[str] = Form(None),
        low_stock_threshold: Optional[float] = Form(5.0),
        tax_rate: Optional[Decimal] = Form(0.0),
    ):
        unit = unit if unit and unit.strip() else None
        sku = sku.upper().strip() if sku and sku.strip() else None
        return cls(name=name, price=price, category=category, shop_id=shop_id, unit=unit, sku=sku, low_stock_threshold=low_stock_threshold, tax_rate=tax_rate)

class CustomerCreate(BaseModel):
    name: str
    phone_number: str
    shop_id: Optional[int] = None

    @classmethod
    def as_form(
        cls,
        name: str = Form(...),
        phone_number: str = Form(...),
        shop_id: Optional[int] = Form(None)
    ):
        return cls(name=name, phone_number=phone_number, shop_id=shop_id)

class SubscriptionCreate(BaseModel):
    name: str
    price: Decimal
    features: List[str] = []

    @classmethod
    def as_form(
        cls,
        name: str = Form(...),
        price: Decimal = Form(...),
        features: List[str] = Form([])
    ):
        return cls(name=name, price=price, features=features)

class ShopCreate(BaseModel):
    name: str
    address: str = ""
    contact: str = ""
    currency_symbol: str = "₹"
    subscription_id: Optional[int] = None
    
    # Visual configs
    font_color: str = "#ffffff"
    background_color: str = "#0f172a"
    header_color: str = "#1e293b"
    logo_size: int = 40
    watermark_opacity: float = 0.1
    card_bg_color: str = "#1e293b"
    sidebar_bg_color: str = "#0f172a"
    accent_color: str = "#f97316"
    border_color: str = "rgba(255, 255, 255, 0.1)"
    price_card_bg: str = "#1e293b"
    header_text_color: str = "#ffffff"
    cart_bg_color: str = "#0f172a"
    nav_font_family: str = "'Outfit', sans-serif"
    pos_card_width: str = "100%"
    pos_card_height: str = "auto"
    pos_card_image_width: str = "100%"
    pos_card_image_height: str = "8rem"
    panel_font_color: str = "#ffffff"
    panel_bg_color: str = "#1e293b"
    billing_font_color: str = "#ffffff"
    billing_card_bg_color: str = "#1e293b"
    billing_card_font_color: str = "#ffffff"
    inc_dec_button_color: str = "#f97316"
    cash_upi_option_color: str = "#1e293b"
    cash_upi_font_color: str = "#ffffff"
    upi_id: Optional[str] = None

    @classmethod
    def as_form(
        cls,
        name: str = Form(...),
        address: str = Form(""),
        contact: str = Form(""),
        currency_symbol: str = Form("₹"),
        subscription_id: Optional[int] = Form(None),
        font_color: str = Form("#ffffff"),
        background_color: str = Form("#0f172a"),
        header_color: str = Form("#1e293b"),
        logo_size: int = Form(40),
        watermark_opacity: float = Form(0.1),
        card_bg_color: str = Form("#1e293b"),
        sidebar_bg_color: str = Form("#0f172a"),
        accent_color: str = Form("#f97316"),
        border_color: str = Form("rgba(255, 255, 255, 0.1)"),
        price_card_bg: str = Form("#1e293b"),
        header_text_color: str = Form("#ffffff"),
        cart_bg_color: str = Form("#0f172a"),
        nav_font_family: str = Form("'Outfit', sans-serif"),
        pos_card_width: str = Form("100%"),
        pos_card_height: str = Form("auto"),
        pos_card_image_width: str = Form("100%"),
        pos_card_image_height: str = Form("8rem"),
        panel_font_color: str = Form("#ffffff"),
        panel_bg_color: str = Form("#1e293b"),
        billing_font_color: str = Form("#ffffff"),
        billing_card_bg_color: str = Form("#1e293b"),
        billing_card_font_color: str = Form("#ffffff"),
        inc_dec_button_color: str = Form("#f97316"),
        cash_upi_option_color: str = Form("#1e293b"),
        cash_upi_font_color: str = Form("#ffffff"),
        upi_id: Optional[str] = Form(None),
        target_shop_id: Optional[int] = Form(None)  # Renamed to avoid path param collision
    ):
        return cls(
            name=name, address=address, contact=contact, currency_symbol=currency_symbol,
            subscription_id=subscription_id, font_color=font_color,
            background_color=background_color, header_color=header_color,
            logo_size=logo_size, watermark_opacity=watermark_opacity,
            card_bg_color=card_bg_color, sidebar_bg_color=sidebar_bg_color,
            accent_color=accent_color, border_color=border_color,
            price_card_bg=price_card_bg, header_text_color=header_text_color,
            cart_bg_color=cart_bg_color, nav_font_family=nav_font_family,
            pos_card_width=pos_card_width, pos_card_height=pos_card_height,
            pos_card_image_width=pos_card_image_width,
            pos_card_image_height=pos_card_image_height,
            panel_font_color=panel_font_color, panel_bg_color=panel_bg_color,
            billing_font_color=billing_font_color,
            billing_card_bg_color=billing_card_bg_color,
            billing_card_font_color=billing_card_font_color,
            inc_dec_button_color=inc_dec_button_color,
            cash_upi_option_color=cash_upi_option_color,
            cash_upi_font_color=cash_upi_font_color,
            upi_id=upi_id,
            shop_id=target_shop_id
        )

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

    @validator("new_password")
    def validate_new_password(cls, v):
        return validate_password_strength(v)

    @classmethod
    def as_form(
        cls,
        current_password: str = Form(...),
        new_password: str = Form(...)
    ):
        return cls(current_password=current_password, new_password=new_password)


class StockRestockRequest(BaseModel):
    """Used by owner to add stock — via manual form or QR scanner."""
    qty: float = Field(gt=0, description="Quantity to add (must be positive)")
    note: Optional[str] = Field(None, max_length=255)
    source: str = Field(default="manual", description="'manual' or 'scanner'")

    @classmethod
    def as_form(
        cls,
        qty: float = Form(...),
        note: Optional[str] = Form(None),
        source: str = Form("manual"),
    ):
        return cls(qty=qty, note=note, source=source)


class SkuUpdateRequest(BaseModel):
    """Used by owner to manually override an auto-generated SKU."""
    sku: str = Field(min_length=3, max_length=64, description="Alphanumeric + hyphens only, uppercase")

    @classmethod
    def as_form(cls, sku: str = Form(...)):
        return cls(sku=sku.upper().strip())


class ShopInvoiceResponse(BaseModel):
    id: int
    invoice_number: str
    shop_id: int
    subscription_id: Optional[int]
    amount: Decimal
    billing_cycle: str
    period_start: str
    period_end: str
    due_date: str
    status: str
    paid_at: Optional[str]
    created_at: str
    shop_name: Optional[str]
    subscription_name: Optional[str]

class PaginatedShopInvoiceResponse(BaseModel):
    items: List[ShopInvoiceResponse]
    total: int
    page: int
    size: int
    pages: int
