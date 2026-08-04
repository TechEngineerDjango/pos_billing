from pydantic import BaseModel, Field, validator
from typing import List, Optional, Type
from fastapi import Form
from decimal import Decimal
from datetime import date
import inspect

from app.shared.models import DEFAULT_LOW_STOCK_THRESHOLD

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

class CustomerSearchResponse(BaseModel):
    found: bool
    id: Optional[int] = None
    name: Optional[str] = None
    phone_number: Optional[str] = None
    company_name: Optional[str] = None
    gst_number: Optional[str] = None
    is_credit_customer: Optional[bool] = None
    credit_limit: Optional[float] = None
    credit_balance: Optional[float] = None
    payment_term_type: Optional[str] = None
    payment_term_value: Optional[int] = None

class BillCreate(BaseModel):
    items: List[CartItem]
    payment_method: str = "Cash"
    status: str = "Completed"
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_country_code: Optional[str] = None
    delivery_charge: float = Field(default=0, ge=0)

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
    low_stock_threshold: Optional[float] = Field(default=DEFAULT_LOW_STOCK_THRESHOLD, ge=0)
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
    
    # B2B Fields
    company_name: Optional[str] = None
    gst_number: Optional[str] = None
    
    # Credit Fields
    is_credit_customer: bool = False
    credit_limit: Optional[Decimal] = Field(default=Decimal("0.00"), ge=0)
    payment_term_type: Optional[str] = None # 'weekly', 'monthly', 'net_days'
    payment_term_value: Optional[int] = None


@as_form
class CustomerCreditTermsUpdate(BaseModel):
    is_credit_customer: bool = False
    credit_limit: Optional[Decimal] = Field(default=None, ge=0)
    payment_term_type: Optional[str] = None
    payment_term_value: Optional[int] = None

    @validator("payment_term_type")
    def require_payment_term_type_for_credit(cls, v, values):
        if values.get("is_credit_customer") and not v:
            raise ValueError("payment_term_type is required when is_credit_customer is true")
        return v

    @validator("payment_term_value")
    def require_payment_term_value_for_credit(cls, v, values):
        if values.get("is_credit_customer") and v is None:
            raise ValueError("payment_term_value is required when is_credit_customer is true")
        return v


class CustomerItemPriceCreate(BaseModel):
    menu_item_id: int
    price: Decimal = Field(..., ge=0)
    valid_from: date
    valid_to: Optional[date] = None

    @validator("valid_to")
    def valid_to_after_valid_from(cls, v, values):
        if v is not None and "valid_from" in values and v < values["valid_from"]:
            raise ValueError("valid_to must be on or after valid_from")
        return v


class CustomerItemPriceUpdate(BaseModel):
    price: Decimal = Field(..., ge=0)
    valid_from: date
    valid_to: Optional[date] = None

    @validator("valid_to")
    def valid_to_after_valid_from(cls, v, values):
        if v is not None and "valid_from" in values and v < values["valid_from"]:
            raise ValueError("valid_to must be on or after valid_from")
        return v


class CreditBillSummary(BaseModel):
    id: int
    slug: str
    bill_number: str
    total_amount: Decimal
    payment_status: str
    amount_paid: Decimal
    due_date: Optional[str]
    timestamp: Optional[str]
    credit_statement_id: Optional[int]


class CreditStatementSummary(BaseModel):
    id: int
    slug: str
    customer_id: int
    statement_number: str
    period_start: Optional[str]
    period_end: Optional[str]
    total_amount: Decimal
    amount_paid: Decimal
    status: str
    due_date: Optional[str]


class CreditLedgerResponse(BaseModel):
    customer_id: int
    credit_balance: Decimal
    credit_limit: Optional[Decimal]
    payment_term_type: Optional[str]
    payment_term_value: Optional[int]
    unpaid_bills: List[CreditBillSummary]
    statements: List[CreditStatementSummary]


class CreditPaymentCreate(BaseModel):
    """Exactly one of statement_slug (pay down a rolled-up statement) or
    bill_slug (pay off a single Completed Credit bill directly, before it's
    been rolled into any statement) must be provided."""
    statement_slug: Optional[str] = None
    bill_slug: Optional[str] = None
    amount: Decimal = Field(..., gt=0)
    idempotency_key: str
    payment_method: str
    note: Optional[str] = None

    @validator("bill_slug")
    def exactly_one_target(cls, v, values):
        statement_slug = values.get("statement_slug")
        if bool(statement_slug) == bool(v):
            raise ValueError("Provide exactly one of statement_slug or bill_slug")
        return v


class CreditFullSettlementCreate(BaseModel):
    """Pays off a customer's entire outstanding credit balance in one action
    — every open statement and every un-statemented unpaid Credit bill.
    No amount field: it's defined as paying exactly what's currently owed."""
    idempotency_key: str
    payment_method: str
    note: Optional[str] = None


class CreditPaymentResponse(BaseModel):
    id: int
    slug: str
    customer_id: int
    credit_statement_id: Optional[int]
    bill_id: Optional[int]
    amount: Decimal
    payment_method: str
    paid_at: Optional[str]
    note: Optional[str]
    recorded_by_user_id: Optional[int]


class ExpenseCreate(BaseModel):
    category: str
    description: str
    amount: Decimal = Field(..., gt=0)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    vendor_name: Optional[str] = None
    expense_date: date
    payment_method: str


class ExpenseUpdate(ExpenseCreate):
    pass


class ExpenseResponse(BaseModel):
    id: int
    slug: str
    category: str
    description: str
    amount: Decimal
    tax_amount: Decimal
    vendor_name: Optional[str]
    expense_date: date
    payment_method: str
    created_at: Optional[str]
    is_voided: bool


class ExpenseListResponse(BaseModel):
    status: str
    expenses: List[ExpenseResponse]
    total: int = 0
    total_amount: float = 0.0


@as_form
class SubscriptionCreate(BaseModel):
    name: str
    price: Decimal
    feature_keys: List[str] = []

@as_form
class ShopCreate(BaseModel):
    name: str
    address: str = ""
    contact: str = ""
    currency_symbol: str = "₹"
    country_code: Optional[str] = None  # Phone country code (e.g., "91" for India); defaults to system DEFAULT_COUNTRY_CODE
    subscription_id: Optional[int] = None
    
    # GST / Tax Settings
    gst_registered: bool = False
    gst_number: Optional[str] = None

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
    unit_cost: Optional[Decimal] = Field(
        default=None, ge=0,
        description="Cost paid per unit for this restock. If provided (>0), "
                     "auto-creates an 'Inventory Purchase' expense entry for "
                     "qty * unit_cost — omit to restock without logging an expense."
    )
    tax_amount: Optional[Decimal] = Field(
        default=None, ge=0,
        description="Total tax paid on this restock (flat amount, not per-unit). "
                     "Only used when unit_cost is also provided."
    )
    payment_method: str = Field(default="Cash", description="Only used if unit_cost is provided")


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
