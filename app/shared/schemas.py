from pydantic import BaseModel, Field, validator
from typing import List, Optional, Type
from fastapi import Form
from decimal import Decimal
import inspect

def as_form(cls: Type[BaseModel]):
    """
    Decorator to dynamically generate an `as_form` classmethod for FastAPI Form dependencies.
    Prevents repetitive boilerplate.
    """
    new_params = []
    for field_name, model_field in cls.model_fields.items():
        new_params.append(
            inspect.Parameter(
                field_name,
                inspect.Parameter.POSITIONAL_ONLY,
                default=Form(model_field.default) if not model_field.is_required() else Form(...),
                annotation=model_field.annotation,
            )
        )
    
    async def _as_form(**data):
        return cls(**data)
    
    sig = inspect.signature(_as_form)
    sig = sig.replace(parameters=new_params)
    _as_form.__signature__ = sig
    setattr(cls, 'as_form', _as_form)
    return cls

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
    customer_country_code: Optional[str] = None

import re

def validate_password_strength(password: str) -> str:
    # Single regex for: 8+ chars, uppercase, lowercase, digit, and special character
    pattern = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]).{8,}$"
    if not re.match(pattern, password):
        raise ValueError("Password does not meet the security policy requirements")
    return password

@as_form
class UserCreate(BaseModel):
    username: str
    password: str
    shop_id: Optional[int] = None

    @validator("password")
    def validate_password(cls, v):
        return validate_password_strength(v)

@as_form
class MenuItemCreate(BaseModel):
    name: str
    price: Decimal = Field(..., ge=0)
    category: str = "General"
    shop_id: Optional[int] = None
    unit: Optional[str] = None  # None/'piece' = fixed price; 'kg'/'g'/'liter'/'ml' = rate per unit
    sku: Optional[str] = None   # If provided, overrides auto-generated SKU
    low_stock_threshold: Optional[float] = Field(default=5.0, ge=0)
    tax_rate: Optional[Decimal] = Field(default=Decimal("0.00"), ge=0, le=100)

    @validator("unit", pre=True)
    def clean_unit(cls, v):
        return v.strip() if v and v.strip() else None

    @validator("sku", pre=True)
    def clean_sku(cls, v):
        return v.upper().strip() if v and v.strip() else None

@as_form
class CustomerCreate(BaseModel):
    name: str
    phone_number: str
    country_code: Optional[str] = None  # Phone country code (e.g., "91" for India); if None, uses shop's default
    shop_id: Optional[int] = None

@as_form
class SubscriptionCreate(BaseModel):
    name: str
    price: Decimal
    features: List[str] = []

@as_form
class ShopCreate(BaseModel):
    name: str
    address: str = ""
    contact: str = ""
    currency_symbol: str = "₹"
    country_code: Optional[str] = None  # Phone country code (e.g., "91" for India); defaults to system DEFAULT_COUNTRY_CODE
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
    
    receipt_footer: str = "Thank you for your order!"
    printer_paper_width: str = "80mm"
    printer_alignment: str = "center"
    timezone: str = "Asia/Kolkata"
    target_shop_id: Optional[int] = Field(None, alias="shop_id")

@as_form
class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

    @validator("new_password")
    def validate_new_password(cls, v):
        return validate_password_strength(v)


@as_form
class StockRestockRequest(BaseModel):
    """Used by owner to add stock — via manual form or QR scanner."""
    qty: float = Field(gt=0, description="Quantity to add (must be positive)")
    note: Optional[str] = Field(None, max_length=255)
    source: str = Field(default="manual", description="'manual' or 'scanner'")


@as_form
class SkuUpdateRequest(BaseModel):
    """Used by owner to manually override an auto-generated SKU."""
    sku: str = Field(min_length=3, max_length=64, description="Alphanumeric + hyphens only, uppercase")

    @validator("sku", pre=True)
    def clean_sku(cls, v):
        return v.upper().strip() if v else v


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

class StockUpdate(BaseModel):
    """Represents the backend-authoritative available stock for a single item."""
    id: int
    available_stock: Optional[float] = None

class BillActionResponse(BaseModel):
    status: str
    message: str
    bill_number: Optional[str] = None
    bill_id: Optional[str] = None
    total: Optional[float] = None
    updated_items: List[StockUpdate] = []
