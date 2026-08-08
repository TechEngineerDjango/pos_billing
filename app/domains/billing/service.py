from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from fastapi import HTTPException
import datetime as dt
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
import re
import logging
import textwrap

from app.shared.models import Bill, MenuItem, Shop, User, Customer, StockMovement, DEFAULT_LOW_STOCK_THRESHOLD
from app.infrastructure.integrations.notifications import get_notification_service
from app.shared.schemas import BillCreate
from app.shared.time_utils import shop_local, shop_day_range_utc
from app.domains.features.service import FeatureService
from app.domains.billing.payment_strategies import get_payment_method_handler
from app.domains.customers.service import CustomerService

logger = logging.getLogger(__name__)

class BillingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notification_svc = get_notification_service(db)
        self.customer_service = CustomerService(db)

    async def _resolve_unit_price(self, menu_item: MenuItem, customer_id, as_of_date):
        """Rate-card price (FEAT-3) if the customer has an active override for
        this item, else MenuItem.price — applies to every payment method."""
        return await self.customer_service.get_effective_price(
            customer_id, menu_item.id, as_of_date, menu_item.price
        )

    @staticmethod
    def _collect_updated_stock(stock_updates: list[tuple]) -> list[dict]:
        """Return backend-authoritative available_stock for affected items.
        Called AFTER commit so values reflect the committed state."""
        return [
            {
                "id": int(item.id),
                "available_stock": (
                    float(item.stock_quantity) - float(item.reserved_quantity)
                    if item.stock_quantity is not None else None
                ),
            }
            for item, _ in stock_updates
        ]

    async def _resolve_customer_id(self, shop: Shop, bill_in: BillCreate) -> Optional[int]:
        """Resolves the customer for a bill: validates a client-supplied
        customer_id belongs to this shop (never trust it blindly — it drives
        rate-card pricing and credit-balance mutation), or looks up/creates
        by phone number, scoped to this shop, same as before."""
        if bill_in.customer_id:
            customer_res = await self.db.execute(
                select(Customer).where(
                    Customer.id == bill_in.customer_id,
                    Customer.shop_id == shop.id,
                )
            )
            customer = customer_res.scalars().first()
            if not customer:
                raise HTTPException(status_code=400, detail="Customer not found in this shop")
            return int(customer.id)

        if bill_in.customer_phone and shop.id:
            phone = re.sub(r'\D', '', bill_in.customer_phone)
            if phone:
                customer_res = await self.db.execute(
                    select(Customer).where(
                        Customer.shop_id == shop.id,
                        Customer.phone_number == phone
                    )
                )
                customer = customer_res.scalars().first()
                if not customer:
                    customer_name = bill_in.customer_name or f"Customer {phone[-4:]}"
                    from app.core.config import settings
                    customer_country_code = bill_in.customer_country_code or shop.country_code or settings.DEFAULT_COUNTRY_CODE
                    customer = Customer(
                        name=customer_name,
                        phone_number=phone,
                        country_code=customer_country_code,
                        shop_id=shop.id
                    )
                    self.db.add(customer)
                    await self.db.flush()
                return int(customer.id)

        return None

    @staticmethod
    async def _finalize_payment_method(db, bill, customer_id, total_amount, status, payment_method, shop_tz="UTC", block_over_limit=False):
        try:
            return await get_payment_method_handler(payment_method).on_finalize(
                db, bill, customer_id, total_amount, status, shop_tz, block_over_limit
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    async def _dispatch_low_stock_alerts(self, shop_id: int, alerts_to_check: list[dict]):
        """Evaluate and send low stock alerts asynchronously to avoid blocking DB locks."""
        for alert in alerts_to_check:
            if alert["stock_quantity"] <= alert["low_stock_threshold"]:
                await self.notification_svc.send_low_stock_alert(
                    shop_id=shop_id,
                    item_name=alert["name"],
                    current_qty=alert["stock_quantity"],
                    threshold=alert["low_stock_threshold"],
                    item_id=alert["item_id"],
                )

    async def create_bill(self, bill_in: BillCreate, current_user: User) -> dict:
        if not bill_in.items:
            raise HTTPException(status_code=400, detail="Cart is empty")

        # Require a resolvable shop
        shop = None
        shop_id = current_user.shop_id
        if shop_id:
            shop_res = await self.db.execute(
                select(Shop).where(Shop.id == shop_id).options(selectinload(Shop.subscription))
            )
            shop = shop_res.scalars().first()

        if not shop:
            raise HTTPException(
                status_code=400,
                detail="No shop associated with this account. Cannot create bill."
            )

        # Defense-in-depth entitlement gate (contract_standards #5): a
        # customer row can already be flagged is_credit_customer=True from
        # before the shop's subscription was downgraded — set_credit_terms's
        # write-time check alone is not enough, so re-check here at
        # bill-creation time, independent of the customer row's own flag.
        if bill_in.payment_method == "Credit" and not await FeatureService(db=self.db).is_feature_enabled(shop, "credit_billing"):
            raise HTTPException(
                status_code=400,
                detail="Your subscription plan does not include Credit Billing.",
            )

        # Bill number format: SHOPCODE-YYYYMMDD-NNNN (shop code + date + a daily counter).
        # The counter resets to 0001 every day, so we need to count "today's" bills
        # for this shop before picking the next number.
        from sqlalchemy import func
        shop_code = re.sub(r"[^A-Z0-9]", "", shop.name.upper())[:4] or "SHOP"

        # Bug fix: "today" must be the SHOP's local day, not the server's UTC day.
        # Bills are stored in UTC, but Postgres was comparing dates using its own
        # session timezone (e.g. IST) while Python computed "today" in UTC. Those
        # two didn't agree near midnight, so the count query sometimes returned 0
        # for a day that already had bills, and two bills got the same sequence
        # number (duplicate bill_number crash). Fix: compute the shop-local day's
        # UTC boundaries in Python and use a range comparison, so "today" always
        # means the same calendar day regardless of DB session timezone.
        shop_tz = shop.timezone or "UTC"
        today = shop_local(dt.datetime.now(timezone.utc), shop_tz).date()
        date_str = today.strftime("%Y%m%d")
        day_start_utc, day_end_utc = shop_day_range_utc(today, shop_tz)

        # Count how many bills this shop already has for today (shop-local day),
        # then the new bill becomes count + 1.
        count_res = await self.db.execute(
            select(func.count(Bill.id)).where(
                Bill.shop_id == shop.id,
                Bill.timestamp >= day_start_utc,
                Bill.timestamp < day_end_utc,
            )
        )
        today_count = count_res.scalar() or 0
        seq = str(today_count + 1).zfill(4)  # 0001, 0002, 0003, ...
        bill_number = f"{shop_code}-{date_str}-{seq}"

        # Handle customer — resolved BEFORE the pricing loop below, since rate
        # -card price resolution (FEAT-3) needs customer_id per line. Also
        # validates a client-supplied customer_id actually belongs to this
        # shop, since it drives credit-balance mutation and rate-card pricing.
        customer_id = await self._resolve_customer_id(shop, bill_in)

        subtotal_amount = Decimal("0.00")
        tax_amount = Decimal("0.00")
        total_amount = Decimal("0.00")
        items_snapshot = []
        stock_updates: list[tuple] = []

        # 1. Bulk Fetch and Lock items to prevent Deadlocks and N+1 queries
        item_ids = [item.id for item in bill_in.items]
        result = await self.db.execute(
            select(MenuItem)
            .where(
                MenuItem.id.in_(item_ids),
                MenuItem.shop_id == shop.id,
                MenuItem.is_active.is_(True),
            )
            .order_by(MenuItem.id)
            .with_for_update()
        )
        menu_items_db = {int(item.id): item for item in result.scalars().all()}  # type: ignore

        # 2. Validate and build snapshot
        for cart_item in bill_in.items:
            menu_item = menu_items_db.get(cart_item.id)

            if not menu_item:
                raise HTTPException(
                    status_code=400,
                    detail=f"Item ID {cart_item.id} not found in this shop's menu"
                )

            # Server-side stock enforcement (accounts for stock already reserved by Held bills)
            available = float(menu_item.stock_quantity) - float(menu_item.reserved_quantity) if menu_item.stock_quantity is not None else None
            if available is not None and cart_item.qty > available:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for '{menu_item.name}'. "
                           f"Available: {available}, Requested: {cart_item.qty}"
                )

            # Rate-card price (FEAT-3) if this customer has an active override
            # for this item, else MenuItem.price — applies to every payment method.
            unit_price = await self._resolve_unit_price(menu_item, customer_id, today)

            TWO_PLACES = Decimal("0.01")
            line_subtotal = (unit_price * Decimal(str(cart_item.qty))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            tax_rate = menu_item.tax_rate if menu_item.tax_rate is not None else Decimal("0.00")
            line_tax = (line_subtotal * (tax_rate / Decimal("100.00"))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            line_total = (line_subtotal + line_tax).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

            subtotal_amount += line_subtotal
            tax_amount += line_tax
            total_amount += line_total

            items_snapshot.append({
                "id": menu_item.id,
                "name": menu_item.name,
                "sku": menu_item.sku,
                "category": menu_item.category,
                "price": float(unit_price),
                "unit": menu_item.unit,
                "qty": cart_item.qty,
                "tax_rate": float(tax_rate),
                "line_subtotal": float(line_subtotal),
                "line_tax": float(line_tax),
                "line_total": float(line_total),
            })
            stock_updates.append((menu_item, cart_item.qty))

        # Manual delivery charge (POS-entered, not tied to any line item) —
        # added to the grand total after item pricing, never taxed.
        delivery_charge = Decimal(str(bill_in.delivery_charge or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amount += delivery_charge

        shop_data = {
            "name": str(shop.name),
            "address": str(shop.address or ""),
            # Bug fix: these columns are nullable with no DB default, so shops that
            # haven't saved the settings form had shop.receipt_footer == None here,
            # and str(None) produced the literal text "None" on printed receipts.
            # Fall back to the same defaults printer.py already uses when a key is
            # missing, so an unconfigured shop gets sensible output instead of "None".
            "receipt_footer": str(shop.receipt_footer) if shop.receipt_footer else "Thank You!\\nVisit Again\\n\\n\\n",
            "printer_paper_width": str(shop.printer_paper_width) if shop.printer_paper_width else "80mm",
            "printer_alignment": str(shop.printer_alignment) if shop.printer_alignment else "center"
        }
        printer_ip = shop.printer_ip

        # Prevent MissingGreenlet on lazy-load post-commit by extracting primitive
        shop_id = int(shop.id)
        shop_tz = getattr(shop, "timezone", None) or "UTC"

        # Atomic bill + stock deduction
        try:
            new_bill = Bill(
                bill_number=bill_number,
                subtotal_amount=subtotal_amount,
                tax_amount=tax_amount,
                delivery_charge=delivery_charge,
                total_amount=total_amount,
                payment_method=bill_in.payment_method,
                items_snapshot=items_snapshot,
                timestamp=dt.datetime.now(timezone.utc),
                shop_id=shop.id,
                customer_id=customer_id,
                status=bill_in.status,
            )

            warning = await self._finalize_payment_method(
                self.db, new_bill, customer_id, total_amount, bill_in.status, bill_in.payment_method, shop_tz,
                bool(shop.block_over_credit_limit),
            )
            
            self.db.add(new_bill)
            await self.db.flush()

            alerts_to_check = []
            if bill_in.status == "Completed":
                for menu_item, qty_sold in stock_updates:
                    if menu_item.stock_quantity is not None:
                        menu_item.stock_quantity -= qty_sold
                        alerts_to_check.append({
                            "item_id": int(menu_item.id),
                            "name": str(menu_item.name),
                            "stock_quantity": float(menu_item.stock_quantity),
                            "low_stock_threshold": float(menu_item.low_stock_threshold or DEFAULT_LOW_STOCK_THRESHOLD)
                        })
                    self.db.add(StockMovement(
                        menu_item_id=menu_item.id,
                        shop_id=shop.id,
                        change_qty=-qty_sold,
                        reason="sale",
                        note=f"Bill #{bill_number}",
                        created_by_user_id=current_user.id,
                    ))
            elif bill_in.status == "Held":
                for menu_item, qty_sold in stock_updates:
                    if menu_item.stock_quantity is not None:
                        menu_item.reserved_quantity += qty_sold
                    self.db.add(StockMovement(
                        menu_item_id=menu_item.id,
                        shop_id=shop.id,
                        change_qty=0.0,
                        reason="held_reserve",
                        note=f"Reserved for Held Bill #{bill_number}",
                        created_by_user_id=current_user.id,
                    ))

            # Extract primitives BEFORE commit
            updated_stock_data = self._collect_updated_stock(stock_updates)

            await self.db.commit()
            await self.db.refresh(new_bill)

        except HTTPException:
            raise
        except Exception as exc:
            await self.db.rollback()
            logger.error(f"Failed to create bill: {str(exc)}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal Server Error: Failed to create bill.")

        # Post-commit async tasks (Notifications)
        if bill_in.status == "Completed":
            await self._dispatch_low_stock_alerts(shop_id, alerts_to_check)

        # Bill is stored in UTC; convert to the shop's configured timezone here so
        # the receipt/response always shows the shop's own local time, not UTC.
        local_ts = shop_local(new_bill.timestamp, shop_tz)
        bill_data = {
            "bill_number": new_bill.bill_number,
            "items_snapshot": items_snapshot,
            "subtotal_amount": float(subtotal_amount),
            "tax_amount": float(tax_amount),
            "delivery_charge": float(delivery_charge),
            "total_amount": float(total_amount),
            "date": local_ts.strftime("%Y-%m-%d %H:%M:%S") if local_ts else "",
        }

        return {
            "status": "success",
            "bill_number": new_bill.bill_number,
            "bill_id": str(new_bill.slug),
            "total": float(total_amount),
            "message": "Bill created and printing",
            "bill_data": bill_data,
            "shop_data": shop_data,
            "printer_ip": str(printer_ip) if printer_ip else None,
            "updated_items": updated_stock_data,
            "warning": warning,
        }

    async def update_bill(self, bill_slug: str, bill_in: BillCreate, current_user: User) -> dict:
        if not bill_in.items:
            raise HTTPException(status_code=400, detail="Cart is empty")

        shop_id = current_user.shop_id
        if not shop_id:
            raise HTTPException(status_code=400, detail="No shop assigned")

        result = await self.db.execute(select(Bill).where(Bill.slug == bill_slug, Bill.shop_id == shop_id))
        bill = result.scalars().first()

        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")

        if bill.status == "Completed":
            raise HTTPException(status_code=400, detail="Cannot edit a completed bill")

        # Needed for rate-card resolution (FEAT-3) below — as_of_date must be
        # the shop's local day, same as create_bill.
        shop_res_for_pricing = await self.db.execute(
            select(Shop).where(Shop.id == shop_id).options(selectinload(Shop.subscription))
        )
        shop_for_pricing = shop_res_for_pricing.scalars().first()
        if not shop_for_pricing:
            raise HTTPException(status_code=400, detail="No shop associated with this account. Cannot update bill.")

        # Same defense-in-depth entitlement gate as create_bill: re-check
        # "credit_billing" at update time too, independent of the customer
        # row's own is_credit_customer flag (shop may have been downgraded
        # since the bill/customer were originally created).
        if bill_in.payment_method == "Credit" and not await FeatureService(db=self.db).is_feature_enabled(shop_for_pricing, "credit_billing"):
            raise HTTPException(
                status_code=400,
                detail="Your subscription plan does not include Credit Billing.",
            )

        today = shop_local(dt.datetime.now(timezone.utc), shop_for_pricing.timezone or "UTC").date()

        # Re-resolve the customer from the edit payload (same as create_bill) —
        # previously this was silently ignored and the bill's original
        # customer_id was used forever, so attaching/changing a customer while
        # editing a Held bill never worked. Falls back to the bill's existing
        # customer_id when the payload doesn't resolve one, so a plain
        # cart-quantity edit that doesn't touch customer fields never clears
        # an existing attachment.
        resolved_customer_id = await self._resolve_customer_id(shop_for_pricing, bill_in)
        customer_id = resolved_customer_id if resolved_customer_id is not None else bill.customer_id

        subtotal_amount = Decimal("0.00")
        tax_amount = Decimal("0.00")
        total_amount = Decimal("0.00")
        items_snapshot = []
        stock_updates: list[tuple] = []

        # Bulk Fetch and Lock items to prevent Deadlocks
        item_ids = [item.id for item in bill_in.items]
        
        # We also need to lock the old items from the snapshot if the bill was Held
        old_items_map = {}
        if bill.status == "Held" and getattr(bill, "items_snapshot", None):
            for old_item in bill.items_snapshot:
                if "id" in old_item:
                    item_ids.append(old_item["id"])
                    old_items_map[old_item["id"]] = old_item.get("qty", 0)
                    
        item_ids = list(set(item_ids)) # Unique IDs
        
        result = await self.db.execute(
            select(MenuItem)
            .where(
                MenuItem.id.in_(item_ids),
                MenuItem.shop_id == shop_id,
                MenuItem.is_active.is_(True)
            )
            .order_by(MenuItem.id)
            .with_for_update()
        )
        menu_items_db = {int(item.id): item for item in result.scalars().all()}  # type: ignore

        # Release old held stock before applying new stock
        if bill.status == "Held":
            for old_id, old_qty in old_items_map.items():
                if old_id in menu_items_db:
                    menu_item = menu_items_db[old_id]
                    if menu_item.stock_quantity is not None:
                        menu_item.reserved_quantity = max(0.0, float(menu_item.reserved_quantity) - float(old_qty))

        for cart_item in bill_in.items:
            menu_item = menu_items_db.get(cart_item.id)

            if not menu_item:
                raise HTTPException(status_code=400, detail=f"Item ID {cart_item.id} not found")

            # Calculate available stock correctly
            available = float(menu_item.stock_quantity) - float(menu_item.reserved_quantity) if menu_item.stock_quantity is not None else None
            
            if available is not None and cart_item.qty > available:
                raise HTTPException(status_code=400, detail=f"Insufficient stock for '{menu_item.name}'")

            # Rate-card price (FEAT-3), same as create_bill.
            unit_price = await self._resolve_unit_price(menu_item, customer_id, today)

            TWO_PLACES = Decimal("0.01")
            line_subtotal = (unit_price * Decimal(str(cart_item.qty))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            tax_rate = menu_item.tax_rate if menu_item.tax_rate is not None else Decimal("0.00")
            line_tax = (line_subtotal * (tax_rate / Decimal("100.00"))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            line_total = (line_subtotal + line_tax).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

            subtotal_amount += line_subtotal
            tax_amount += line_tax
            total_amount += line_total

            items_snapshot.append({
                "id": menu_item.id, "name": menu_item.name, "sku": menu_item.sku,
                "category": menu_item.category, "price": float(unit_price), "unit": menu_item.unit,
                "qty": cart_item.qty, "tax_rate": float(tax_rate),
                "line_subtotal": float(line_subtotal), "line_tax": float(line_tax), "line_total": float(line_total),
            })
            stock_updates.append((menu_item, cart_item.qty))

        # Manual delivery charge (POS-entered, not tied to any line item) —
        # added to the grand total after item pricing, never taxed.
        delivery_charge = Decimal(str(bill_in.delivery_charge or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amount += delivery_charge

        try:
            bill.subtotal_amount = subtotal_amount  # type: ignore
            bill.tax_amount = tax_amount  # type: ignore
            bill.delivery_charge = delivery_charge  # type: ignore
            bill.total_amount = total_amount  # type: ignore
            bill.payment_method = bill_in.payment_method  # type: ignore
            bill.status = bill_in.status  # type: ignore
            bill.items_snapshot = items_snapshot  # type: ignore
            bill.timestamp = dt.datetime.now(timezone.utc)  # type: ignore
            bill.customer_id = customer_id  # type: ignore

            warning = await self._finalize_payment_method(
                self.db, bill, customer_id, total_amount, bill_in.status, bill_in.payment_method,
                shop_for_pricing.timezone or "UTC", bool(shop_for_pricing.block_over_credit_limit),
            )

            alerts_to_check = []
            if bill_in.status == "Completed":
                for menu_item, qty_sold in stock_updates:
                    if menu_item.stock_quantity is not None:
                        menu_item.stock_quantity -= qty_sold
                        alerts_to_check.append({
                            "item_id": int(menu_item.id),
                            "name": str(menu_item.name),
                            "stock_quantity": float(menu_item.stock_quantity),
                            "low_stock_threshold": float(menu_item.low_stock_threshold or DEFAULT_LOW_STOCK_THRESHOLD)
                        })
                        self.db.add(StockMovement(
                            menu_item_id=menu_item.id, shop_id=shop_id, change_qty=-qty_sold,
                            reason="sale", note=f"Bill #{bill.bill_number} (Resumed)", created_by_user_id=current_user.id,
                        ))
            elif bill_in.status == "Held":
                for menu_item, qty_sold in stock_updates:
                    if menu_item.stock_quantity is not None:
                        menu_item.reserved_quantity += qty_sold
                        # StockMovement for hold update is omitted to reduce noise, since it's just a reservation delta

            # Extract primitives BEFORE commit
            updated_stock_data = self._collect_updated_stock(stock_updates)

            await self.db.commit()
            await self.db.refresh(bill)
        except HTTPException:
            raise
        except Exception as exc:
            await self.db.rollback()
            logger.error(f"Failed to update bill: {str(exc)}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal Server Error: Failed to update bill.")

        # Post-commit async tasks (Notifications)
        if bill_in.status == "Completed":
            await self._dispatch_low_stock_alerts(int(shop_id), alerts_to_check)

        shop_res = await self.db.execute(select(Shop).where(Shop.id == shop_id))
        shop = shop_res.scalars().first()
        # Same "None" bug as create_bill's shop_data: guard each nullable field
        # individually (not just "if shop"), otherwise a None field still renders
        # as the literal text "None" on the receipt.
        shop_data = {
            "name": str(shop.name) if shop else "",
            "address": str(shop.address) if shop and shop.address else "",
            "receipt_footer": str(shop.receipt_footer) if shop and shop.receipt_footer else "Thank You!\\nVisit Again\\n\\n\\n",
            "printer_paper_width": str(shop.printer_paper_width) if shop and shop.printer_paper_width else "80mm",
            "printer_alignment": str(shop.printer_alignment) if shop and shop.printer_alignment else "center",
        }

        # Same as create_bill: show the shop's local time, not the raw UTC value.
        # Avoid MissingGreenlet on post-commit timezone lookup
        shop_tz = getattr(shop, "timezone", None) or "UTC" if shop else "UTC"
        local_ts = shop_local(bill.timestamp, shop_tz)
        bill_data = {
            "bill_number": bill.bill_number, "items_snapshot": items_snapshot,
            "subtotal_amount": float(subtotal_amount), "tax_amount": float(tax_amount),
            "delivery_charge": float(delivery_charge), "total_amount": float(total_amount),
            "date": local_ts.strftime("%Y-%m-%d %H:%M:%S") if local_ts else "",
        }

        return {
            "status": "success", "bill_number": bill.bill_number, "bill_id": str(bill.slug), "total": float(total_amount),
            "message": "Bill updated successfully", "bill_data": bill_data, "shop_data": shop_data, "printer_ip": str(shop.printer_ip) if shop else None,
            "updated_items": updated_stock_data,
            "warning": warning,
        }

    async def cancel_bill(self, bill_slug: str, current_user: User) -> dict:
        shop_id = current_user.shop_id
        if not shop_id:
            raise HTTPException(status_code=400, detail="No shop assigned")

        result = await self.db.execute(select(Bill).where(Bill.slug == bill_slug, Bill.shop_id == shop_id))
        bill = result.scalars().first()

        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")

        if bill.status != "Held":
            raise HTTPException(status_code=400, detail="Only Held bills can be cancelled")

        # Fetch items to release reservations
        item_ids = []
        old_items_map = {}
        if getattr(bill, "items_snapshot", None):
            for old_item in bill.items_snapshot:
                if "id" in old_item:
                    item_ids.append(old_item["id"])
                    old_items_map[old_item["id"]] = old_item.get("qty", 0)

        if item_ids:
            item_res = await self.db.execute(
                select(MenuItem)
                .where(MenuItem.id.in_(item_ids), MenuItem.shop_id == shop_id)
                .order_by(MenuItem.id)
                .with_for_update()
            )
            menu_items_db = {int(item.id): item for item in item_res.scalars().all()}
            
            for old_id, old_qty in old_items_map.items():
                if old_id in menu_items_db:
                    menu_item = menu_items_db[old_id]
                    if menu_item.stock_quantity is not None:
                        menu_item.reserved_quantity = max(0.0, float(menu_item.reserved_quantity) - float(old_qty))
                        self.db.add(StockMovement(
                            menu_item_id=menu_item.id, shop_id=shop_id, change_qty=0.0,
                            reason="held_release", note=f"Cancelled Held Bill #{bill.bill_number}", created_by_user_id=current_user.id,
                        ))

        try:
            bill.status = "Cancelled" # type: ignore

            # Build updated_items from the released menu items
            cancel_stock_updates = [
                (menu_items_db[old_id], old_qty)
                for old_id, old_qty in old_items_map.items()
                if old_id in menu_items_db
            ]

            # Extract primitives BEFORE commit
            updated_stock_data = self._collect_updated_stock(cancel_stock_updates)

            await self.db.commit()
        except HTTPException:
            raise
        except Exception as exc:
            await self.db.rollback()
            logger.error(f"Failed to cancel bill: {str(exc)}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal Server Error: Failed to cancel bill.")

        return {
            "status": "success",
            "message": "Held bill cancelled successfully",
            "updated_items": updated_stock_data,
        }

    #: Fixed monospace column widths for the WhatsApp item table — a single
    #: header row (No/Item/Qty/Amt) with every item as one grid row
    #: underneath. Kept deliberately narrow (~28 chars total) because
    #: WhatsApp renders a ``` code block at the recipient's device font size,
    #: which only fits ~28-30 monospace chars on a phone before wrapping —
    #: there is no way to shrink the font from our side (plain text only), so
    #: the layout width is the only lever. Per-item Price and Tax are dropped
    #: from the grid (they'd blow the width budget) and instead surface in
    #: the Subtotal/Tax/Total summary below; the line Amount is what a
    #: customer actually verifies per row.
    #: Columns are joined with a single-space _WA_GAP so a value that exactly
    #: fills its own width still can't run into its neighbour. Long item
    #: names wrap onto continuation lines confined to the Item column (Qty
    #: and Amt blank on those lines).
    _WA_GAP = " "
    _WA_NO_W = 2
    _WA_ITEM_W = 9
    _WA_QTY_W = 6
    _WA_AMT_W = 8
    _WA_LINE_WIDTH = (
        _WA_NO_W + _WA_ITEM_W + _WA_QTY_W + _WA_AMT_W + 3 * len(_WA_GAP)
    )

    #: Short display labels for MenuItem.unit (app/shared/models.py) —
    #: 'liter' shows as 'ltr'; piece/no-unit items show no unit at all.
    _WA_UNIT_LABELS = {
        "kg": "kg",
        "g": "g",
        "liter": "ltr",
        "ml": "ml",
        "piece": "",
        "": "",
        None: "",
    }

    @staticmethod
    def _wa_qty_str(qty) -> str:
        """1.0 -> '1', 0.5 -> '0.5' — avoids a misleading trailing '.0' on
        piece-based items while keeping fractional weights/volumes exact."""
        qty_f = float(qty)
        return str(int(qty_f)) if qty_f == int(qty_f) else str(qty_f)

    @staticmethod
    def format_whatsapp_bill_message(
        bill_number: str,
        items_snapshot: list,
        total_amount: float,
        shop_name: str,
        currency: str = "₹",
        bill_date: str = "",
        footer_message: str = "Thank you for your order! your order will be delivered soon...",
        due_date: str = "",
        payment_status: str = "",
        subtotal_amount: Optional[float] = None,
        tax_amount: Optional[float] = None,
        delivery_charge: float = 0.0,
    ) -> str:
        """Single source of truth for WhatsApp bill receipt message format (Domain Layer).

        Item table is a fixed-width monospace grid wrapped in a ``` code
        fence (the only way WhatsApp preserves column alignment): one
        header row (No/Item/Qty/Amt) and one row per item, kept narrow
        (~28 chars) so it doesn't wrap on a phone. Long item names wrap
        onto continuation lines confined to the Item column so they never
        bleed into the numeric columns. Per-item price/tax are not shown in
        the grid (width budget); Subtotal/Tax/[Delivery]/Total are broken
        out on their own rows below — subtotal_amount/tax_amount default to
        summing the per-line values when the caller doesn't have the
        bill-level columns handy; total_amount is assumed to already
        include delivery_charge (added to the grand total server-side, not
        per line item).
        """
        if tax_amount is None:
            tax_amount = sum(float(it.get('line_tax', 0) or 0) for it in items_snapshot)
        if subtotal_amount is None:
            subtotal_amount = total_amount - tax_amount - delivery_charge
        GAP = BillingService._WA_GAP
        NO_W = BillingService._WA_NO_W
        ITEM_W = BillingService._WA_ITEM_W
        QTY_W = BillingService._WA_QTY_W
        AMT_W = BillingService._WA_AMT_W
        LINE_W = BillingService._WA_LINE_WIDTH
        sep = "-" * LINE_W

        def row(no_s, item_s, qty_s, amt_s):
            return GAP.join([
                f"{no_s:<{NO_W}}", f"{item_s:<{ITEM_W}}",
                f"{qty_s:>{QTY_W}}", f"{amt_s:>{AMT_W}}",
            ])

        table_lines = [row("No", "Item", "Qty", "Amt"), sep]
        for i, item in enumerate(items_snapshot, start=1):
            name = str(item.get('name', 'Item')).strip()
            unit_label = BillingService._WA_UNIT_LABELS.get(item.get('unit'), item.get('unit') or '')
            qty_str = BillingService._wa_qty_str(item.get('qty', 1))
            qty_unit_str = f"{qty_str} {unit_label}".strip()
            amount_str = f"{currency}{float(item.get('line_total', 0)):.2f}"

            wrapped_name = textwrap.wrap(name, width=ITEM_W) or [""]
            table_lines.append(row(str(i), wrapped_name[0], qty_unit_str, amount_str))
            for cont in wrapped_name[1:]:
                table_lines.append(row("", cont, "", ""))
        table_lines.append(sep)
        label_w = NO_W + ITEM_W + QTY_W + 2 * len(GAP)
        subtotal_str = f"{currency}{subtotal_amount:.2f}"
        tax_total_str = f"{currency}{tax_amount:.2f}"
        total_str = f"{currency}{total_amount:.2f}"
        table_lines.append(f"{'SUBTOTAL':<{label_w}}{GAP}{subtotal_str:>{AMT_W}}")
        table_lines.append(f"{'TAX':<{label_w}}{GAP}{tax_total_str:>{AMT_W}}")
        if delivery_charge > 0:
            delivery_str = f"{currency}{delivery_charge:.2f}"
            table_lines.append(f"{'DELIVERY':<{label_w}}{GAP}{delivery_str:>{AMT_W}}")
        table_lines.append(f"{'TOTAL':<{label_w}}{GAP}{total_str:>{AMT_W}}")
        items_table = "```\n" + "\n".join(table_lines) + "\n```"

        date_line = f"Date: {bill_date}\n" if bill_date else ""

        # Credit sales (Unpaid / PartiallyPaid) get an extra line calling out the
        # due date; Cash/UPI bills (payment_status "Paid") are unaffected.
        credit_line = ""
        if payment_status in ("Unpaid", "PartiallyPaid") and due_date:
            credit_line = f"\n*This is a credit sale — Payment Due: {due_date}*\n"

        return f"""*{shop_name}*
*Tax Invoice*
Bill #{bill_number}
{date_line}{items_table}
{credit_line}
{footer_message}"""
