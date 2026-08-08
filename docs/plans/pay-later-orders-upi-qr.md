# Pay Later billing + Orders tracking + UPI payment QR

## Context

Today "Credit" in the POS is an opt-in payment method that only appears for
customers already flagged `is_credit_customer=True`, and it's tightly coupled
to the credit-limit/statement ("khata") system in `app/domains/credit/`.

The actual need is broader: the shop owner wants to hand over product without
collecting payment at checkout — for *any* customer, not only pre-registered
credit accounts (e.g. a phone/WhatsApp order they'll deliver and collect cash
for later). Only customers who *are* registered credit accounts should have
their balance checked against a limit; everyone else should just be allowed
through. Once a bill isn't paid at checkout, staff need a place to track it
until it's delivered and paid — that's the new "Orders" view — and the
customer needs an easy way to pay after the fact, hence a UPI QR/pay-link on
the bill and in the WhatsApp receipt.

This plan reuses the existing `payment_method == "Credit"` plumbing (rename
is UI-label-only — the DB value stays `"Credit"` to avoid touching the
credit repository filters, statement generation, and existing production
rows) and the existing Credit Book/statement machinery is confirmed to be
scoped to `is_credit_customer=True` customers only (`app/domains/credit/repository.py:58`
`list_credit_accounts` filters `Customer.is_credit_customer.is_(True)`, and
`generate_statements_for_shop` iterates `list_credit_customers_due`) — so
"Pay Later" bills from ordinary customers will never leak into Credit Book
accounts/statements, they just won't have a `due_date` or `credit_balance`
tracked. Verified via full read of `credit/service.py` and `credit/repository.py`.

---

## 1. POS: relabel "Credit" → "Pay Later", open to all customers, warn not block

**File: `app/frontend/templates/pos.html`** (payment method selector, ~line 352-385)
- Change grid gating from `($store.features['credit_billing'] && foundCustomer?.is_credit_customer) ? 'grid-cols-3' : 'grid-cols-2'` to just `$store.features['credit_billing'] ? 'grid-cols-3' : 'grid-cols-2'`.
- Change the `<template x-if="$store.features['credit_billing'] && foundCustomer?.is_credit_customer">` wrapping the third button to `<template x-if="$store.features['credit_billing']">` — button always available when the shop has the feature, not gated on the customer's flag.
- Button label `<span>🧾</span> Credit` → `<span>🧾</span> Pay Later`.
- The "Available credit" hint (line 380-384) stays conditioned on `foundCustomer?.is_credit_customer` — only actual credit accounts have a limit to show.

**File: `app/frontend/static/js/pos/pos-app.js`**
- `handleCustomerFound` (line 469-475): remove the fallback that resets `paymentMethod` to `'Cash'` when the found/cleared customer isn't `is_credit_customer` — Pay Later is now valid for any customer.
- `validateSubmission` (line 551-570): change the `paymentMethod === 'Credit' && !foundCustomer?.is_credit_customer` block — that combination is no longer an error. Instead require a resolvable customer for Pay Later (phone entered, since `_resolve_customer_id` needs either `customer_id` or `customer_phone` to create/find a Customer — otherwise `CreditHandler` raises "requires a registered customer"): if `paymentMethod === 'Credit' && !customerPhone` → alert "Pay Later requires a customer phone number" and block submit.
- `handleSuccess` (~line 583-615): if the create/update response includes a `warning` field (see §2), `alert()` it after the success flow completes (non-blocking — bill is already created).

## 2. Backend: allow Pay Later for any customer; credit-limit enforcement is a configurable shop setting

Whether crossing a credit customer's limit **warns** (bill still goes through) or **blocks** (bill rejected, current behavior) becomes a per-shop setting, defaulting to warn.

**New shop setting: `block_over_credit_limit`** (boolean, default `False`)
- **Migration** (can be combined with §3's migration or a separate one, same `down_revision` chain): add `shops.block_over_credit_limit` — `sa.Boolean(), nullable=False, server_default=sa.false()`.
- **File: `app/shared/models.py`** — `Shop.block_over_credit_limit = Column(Boolean, default=False)`, included in its `to_dict()`-equivalent if one exists.
- **File: `app/shared/schemas.py`** — add `block_over_credit_limit: bool = False` to `ShopCreate` (~line 319, next to `upi_id`), so it flows through the existing `.as_form()` mechanism.
- **File: `app/domains/billing/admin_router.py`** — `update_settings` (line 630-675, owner-only via `require_owner_or_above`): add `shop.block_over_credit_limit = data.block_over_credit_limit` alongside the other field assignments (~line 664-668).
- **File: `app/frontend/templates/dashboard.html`** — add a control to the existing "Receipt & Printer Settings" section (~line 352-410): a green neon-style toggle switch (no full Bootstrap JS/CSS is loaded in this project — only Bootstrap Icons font, confirmed via `layout.html:17` — so this is a Tailwind-built switch, not a literal Bootstrap component, but matches that look/feel). Standard Tailwind `peer`-checkbox pattern: a visually-hidden checkbox drives a track (gray → green, thumb slides right) plus an "OFF"/"ON" text label that glows when on:
  ```html
  <div class="md:col-span-2 flex items-center justify-between gap-3 bg-gray-50 dark:bg-slate-900 rounded-xl px-4 py-3 border border-gray-200 dark:border-white/10">
      <label for="block_over_credit_limit" class="text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
          <span class="font-medium text-gray-900 dark:text-white block">Block Pay Later bills that exceed a customer's credit limit</span>
          <span class="text-[10px] text-gray-400">Off (default): the bill still goes through, with a warning. On: the bill is rejected until the limit is raised or the balance is paid down.</span>
      </label>
      <label class="relative inline-flex items-center gap-2 cursor-pointer shrink-0">
          <input type="checkbox" id="block_over_credit_limit" name="block_over_credit_limit"
              {% if shop.block_over_credit_limit %}checked{% endif %} class="sr-only peer">
          <span class="relative w-12 h-6 rounded-full bg-gray-300 dark:bg-slate-700 peer-checked:bg-green-500
                       peer-focus-visible:ring-2 peer-focus-visible:ring-green-400/50 transition-colors duration-200
                       after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:h-5 after:w-5
                       after:rounded-full after:bg-white after:shadow after:transition-transform after:duration-200
                       peer-checked:after:translate-x-6"></span>
          <span class="w-8 text-center text-xs font-black uppercase tracking-widest text-gray-400 peer-checked:hidden">Off</span>
          <span class="hidden w-8 text-center text-xs font-black uppercase tracking-widest text-green-400 peer-checked:inline
                       [text-shadow:0_0_6px_rgba(74,222,128,.9),0_0_14px_rgba(74,222,128,.6)]">On</span>
      </label>
  </div>
  ```
  All three post-checkbox elements (track, "Off" label, "On" label) are direct siblings of the `<input class="peer">` within the same `<label>` — required for Tailwind's `peer-checked:` sibling selector to reach them. Submitted with the same settings form that already posts to `/admin/settings`; browsers omit unchecked checkboxes from form data, which FastAPI's `Form(default)` already handles as `False` (same pattern the existing `gst_registered` checkbox relies on).

**File: `app/domains/credit/service.py`**
- `reserve_credit` (line 54-79) takes a new `block_over_limit: bool = False` param. Instead of always raising, branch:
  ```python
  async def reserve_credit(self, customer_id, amount, bill=None, shop_tz="UTC", block_over_limit: bool = False) -> Optional[str]:
      customer = await self.check_eligible(customer_id, locked=True)
      current = customer.credit_balance or Decimal("0.00")
      would_exceed = customer.credit_limit is not None and (current + amount) > customer.credit_limit
      if would_exceed and block_over_limit:
          raise CreditLimitExceededError(
              f"Credit limit exceeded. Current balance: {current}, Limit: {customer.credit_limit}"
          )
      customer.credit_balance = current + amount
      if bill is not None:
          bill.payment_status = "Unpaid"
          # ...unchanged due_date logic...
      return (f"{customer.name} is now over their credit limit "
              f"(balance {current + amount}, limit {customer.credit_limit}).") if would_exceed else None
  ```
  `block_over_limit=True` preserves exactly today's behavior (hard 400 via the existing `CreditLimitExceededError` → `ValueError` → HTTP 400 mapping); `False` (the new default) commits the balance over the limit and surfaces a warning instead.

**File: `app/domains/billing/payment_strategies.py`**
- `CreditHandler.on_finalize` needs the shop's `block_over_credit_limit` flag and the customer's `is_credit_customer` flag, and returns an `Optional[str]` warning (change base class signature `-> None` to `-> Optional[str]`):
  ```python
  async def on_finalize(self, db, bill, customer_id, total_amount, status, shop_tz="UTC", block_over_limit: bool = False):
      if not customer_id:
          raise ValueError("Pay Later requires a registered customer")
      credit_service = CreditService(db)
      customer = await credit_service.customer_repo.get_by_id(customer_id)
      if not customer:
          raise ValueError("Customer not found")

      if not customer.is_credit_customer:
          # Ordinary "pay later" customer — no limit, no balance tracking.
          bill.payment_status = "Unpaid"
          return None

      if status == "Completed":
          return await credit_service.reserve_credit(customer_id, total_amount, bill, shop_tz, block_over_limit)
      else:
          await credit_service.check_eligible(customer_id)
          bill.payment_status = "Unpaid"
          return None
  ```

**File: `app/domains/billing/service.py`**
- `_finalize_payment_method` (line 95-101, static helper) currently discards the handler's return value and doesn't receive `shop`. Change it to: (a) accept `block_over_limit: bool = False` and pass it through to `on_finalize`, (b) return whatever `on_finalize` returns. Thread it through `create_bill` (call site ~line 290, `shop` already in scope → pass `bool(shop.block_over_credit_limit)`) and `update_bill` (call site ~line 507, same) into their response dicts as `"warning": warning_or_none`.
- The existing `credit_billing` feature-flag gate (line 139 and 401, `payment_method == "Credit"` → require `is_feature_enabled(shop, "credit_billing")`) is unchanged — it already applies regardless of customer type.

**File: `app/domains/billing/router.py`** — `create_bill`/`update_bill` response building: pass through the new `warning` key from the service dict (currently these routes just forward `result` fields; confirm `warning` rides along without extra work, or add it explicitly to the returned dict).

## 3. `delivery_status` column + migration

**New file: `alembic/versions/<hash>_add_delivery_status_to_bill.py`**, `down_revision = 'a7c3e9f10b21'` (current head), following the exact template of `a7c3e9f10b21_add_delivery_charge_to_bill.py`. Bundles both new columns from this plan (`bills.delivery_status` and `shops.block_over_credit_limit`, §2) into one migration since they land together:
```python
def upgrade() -> None:
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delivery_status', sa.String(), nullable=False, server_default='Pending'))
    with op.batch_alter_table('shops', schema=None) as batch_op:
        batch_op.add_column(sa.Column('block_over_credit_limit', sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade() -> None:
    with op.batch_alter_table('shops', schema=None) as batch_op:
        batch_op.drop_column('block_over_credit_limit')
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.drop_column('delivery_status')
```

**File: `app/shared/models.py`** — add `delivery_status = Column(String, default="Pending")` next to `payment_status` (~line 510) on `Bill`, values `Pending` / `Out for Delivery` / `Delivered`; include it in `Bill.to_dict()` (~line 528-544).

Only bills with `payment_method == "Credit"` (i.e. Pay Later orders) are meaningfully tracked through this field — cash/UPI bills keep the default and are simply not surfaced in the Orders view.

## 4. Orders API

**File: `app/domains/billing/router.py`** (same home as `/billing/recent-bills`, `/billing/cancel/{slug}`):

- `GET /billing/orders` — list Pay Later orders for the shop, mirroring `list_customer_bills` in `credit/router.py:135-163` (same filter params: `date_from`, `date_to`, `min_amount`, `max_amount`, `payment_status`, `delivery_status`, `limit`, `offset`, capped `limit<=100`). Query: `Bill.shop_id == current_user.shop_id, Bill.payment_method == "Credit", Bill.status != "Cancelled"`, `selectinload(Bill.customer)`, ordered by `timestamp desc`. Response includes each bill's `to_dict()` plus `customer.is_credit_customer` (needed by the frontend to decide which "mark paid" action to call — §5). Used by both the POS shortcut modal (small default limit, no filters) and the Dashboard Orders tab (full filters, paginated via the existing `fetchPaginated` JS helper).

- `POST /billing/orders/{bill_slug}/update` — body `{delivery_status?: str, mark_paid?: bool}`. Auth matches existing `cancel_bill`/`update_bill` style (logged-in shop staff, no extra role gate — cashiers need to update deliveries). Looks up the bill by slug + shop, then:
  - `delivery_status` (if present): validate it's one of `Pending`/`Out for Delivery`/`Delivered`, set directly — no side effects.
  - `mark_paid` (if `True`): **only valid when the bill's customer is NOT `is_credit_customer`** (real credit-customer payments must go through the existing `/admin/credit/payments` endpoint so `CreditPayment` audit rows and `credit_balance` stay correct — see §5). Set `bill.payment_status = "Paid"`, `bill.amount_paid = bill.total_amount`. If the bill *does* belong to a credit customer, return 400 telling the caller to use the credit payment flow instead.

## 5. Orders UI

**POS shortcut** (`app/frontend/templates/pos.html`, next to the existing "Recent" button ~line 86-89): add a sibling button (`🚚 Orders` or similar) that opens a new `ordersModal`, built the same way as `recentBillsModal` (`app/frontend/static/js/pos/pos-app.js:136, 627-645` and the modal markup at `pos.html:556-616`):
- State: `ordersModal: { open: false, loading: false, orders: [] }`.
- New `BillingApi.fetchOrders()` → `GET /billing/orders` (no filters, small limit e.g. 30), following the existing `BillingApi` object shape (`pos-app.js:19-41`).
- Per-row: customer name/phone, amount, a delivery-status stepper/select (Pending → Out for Delivery → Delivered) calling the new update endpoint, and a "Mark Paid" button — for `is_credit_customer` bills this button instead deep-links to (or just informs the cashier to use) the Dashboard Credit Book, since limit-aware payment recording belongs there.

**Dashboard tab** (`app/frontend/templates/dashboard.html`): add a sibling `orders` tab following the `credit_book` pattern exactly —
- Sidebar nav button (~line 113-124) and top/mobile tab-switcher entries (~line 12-129, ~245), gated the same way as Credit Book's own flag (confirm exact flag name when implementing — the Explore pass found the wrapping `{% if features.<flag> %}` around the Credit Book button; reuse `credit_billing` since Pay Later requires it).
- Content panel: new `<div x-show="activeTab === 'orders'" class="h-full overflow-y-auto p-4 pb-24 sm:p-6 lg:p-10" x-cloak>` sibling to the `credit_book` block (~line 1267), using the **mobile-card / desktop-table dual layout** already established for the Recent Transactions section (this session's earlier work) so it doesn't need a separate mobile-scroll fix later.
- Data: reuse `fetchPaginated` (`dashboard-app.js:8-13`) against `GET /billing/orders` with the full filter set, same pattern as the existing Bill History sub-tab (`dashboard-app.js:696`, `dashboard.html:1580-1600`).
- Row actions: delivery-status `<select>` and a "Mark Paid" button. For rows where `customer.is_credit_customer` is true, "Mark Paid" calls the **existing** `/admin/credit/payments` endpoint with `bill_slug` + `amount = total_amount - amount_paid` and a client-generated idempotency key (reuse `_idempotencyKey()`, `dashboard-app.js:769-774`) — exactly what the Credit Book's own "Record Payment" panel already does. For non-credit rows, it calls the new `POST /billing/orders/{slug}/update` with `mark_paid: true`.

## 6. UPI payment QR — bill page + WhatsApp

**File: `app/frontend/templates/bill_detail.html`** — insert a new block after the Total Section (~line 96, before Action Buttons ~line 99), shown when `bill.payment_status in ('Unpaid', 'PartiallyPaid') and shop.upi_id`:
```html
<div class="mt-4 p-4 bg-white/5 rounded-2xl text-center no-print">
  <img src="https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={{ ('upi://pay?pa=' ~ shop.upi_id ~ '&pn=' ~ (shop.name|urlencode) ~ '&am=' ~ '%.2f'|format(bill.total_amount - (bill.amount_paid or 0)) ~ '&cu=INR')|urlencode }}"
       alt="UPI QR Code" class="w-40 h-40 mx-auto rounded-xl bg-white p-2 shadow-lg">
  <p class="mt-2 text-sm font-bold">Scan to pay {{ shop.currency_symbol }}{{ "%.2f"|format(bill.total_amount - (bill.amount_paid or 0)) }}</p>
</div>
```
Same `upi://pay?pa=...&pn=...&am=...&cu=INR` + `api.qrserver.com` pattern already used in `dashboard-app.js:761-766` and `pos.html:392` — just amount is the outstanding balance, not the full total. Guard on `shop.upi_id` since the fallback `shop` object in `router.py:399-404` has no `upi_id` attribute (use `shop.upi_id if hasattr... ` or ensure the fallback object gets the attr with `None` default).

**File: `app/domains/billing/service.py`** — `format_whatsapp_bill_message` (line 691-777): add optional params `upi_id: Optional[str] = None`, `pay_url: Optional[str] = None`. After `credit_line`, append a pay-link line when `payment_status in ("Unpaid", "PartiallyPaid") and pay_url`:
```python
pay_line = f"\n💳 Pay now: {pay_url}\n" if (payment_status in ("Unpaid", "PartiallyPaid") and pay_url) else ""
```
insert into the returned template.

**File: `app/domains/billing/admin_router.py`** — `whatsapp_redirect_page` (~line 882-935): already has `shop` and `bill` loaded before calling `format_whatsapp_bill_message` (line 914); pass `pay_url=f"{request.base_url}billing/bill/{bill.id}"` (the existing public, no-auth bill page from `router.py:369`, which now carries the QR block above) so the same link works for both "staff-facing" (shown in Orders view via the bill link that already exists) and "customer-facing" (WhatsApp message) per your answer — no separate customer-only page needed.

## Tests to update / add

- `tests/test_credit_bills.py::test_credit_bill_over_limit_rejected_400` and `::test_update_bill_credit_limit_exceeded_returns_400_not_500` — currently assert a 400 with "Credit limit exceeded" under implicit default behavior. Update both to explicitly set `shop.block_over_credit_limit = True` in the fixture, keeping them as regression tests for the block path (still 400, same message).
- Add new tests: same over-limit scenario with `shop.block_over_credit_limit` left at the default (`False`) — bill succeeds (200/201), `customer.credit_balance` is incremented past the limit, response `warning` field contains the over-limit message. Pay Later bill for a non-`is_credit_customer` customer succeeds regardless of the setting (no limit check, no `credit_balance` mutation). `delivery_status` defaults to `Pending` and is updatable via the new endpoint. New `/billing/orders` list endpoint scoping (shop-scoped, excludes Cancelled).

## Verification

1. `alembic upgrade head` locally, confirm `bills.delivery_status` exists with default `Pending`.
2. `pytest tests/test_credit_bills.py tests/test_credit_billing_feature_gate.py tests/test_credit_accounts.py -q` after updating the two tests above — full pass.
3. Rebuild Tailwind (`npm run build`) after the `pos.html`/`dashboard.html` markup changes (new classes for the Orders tab reuse existing utility classes from the mobile-card pattern, but re-run to be safe).
4. Manual/Playwright pass against the running dev server: create a bill for a brand-new walk-in customer (name+phone only) with "Pay Later" selected → confirm it succeeds with no `is_credit_customer` requirement; create one for an existing credit customer with an amount that exceeds their limit → confirm the bill still completes and a warning alert fires; open the POS Orders shortcut and the Dashboard Orders tab → confirm the bill appears, delivery status is updatable through all three stages, and "Mark Paid" works for both a credit and a non-credit order via their respective code paths; open the bill's public `/billing/bill/{id}` page while Unpaid → confirm the UPI QR renders with the correct outstanding amount; trigger the WhatsApp send → confirm the message includes the pay link.
