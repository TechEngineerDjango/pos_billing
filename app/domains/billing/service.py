from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException
import datetime as dt
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP
import re
import logging

from app.shared.models import Bill, MenuItem, Shop, User, Customer, StockMovement
from app.infrastructure.integrations.notifications import get_notification_service
from app.shared.schemas import BillCreate
from app.shared.time_utils import shop_local

logger = logging.getLogger(__name__)

DEFAULT_LOW_STOCK_THRESHOLD = 5.0

class BillingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notification_svc = get_notification_service(db)

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
            shop_res = await self.db.execute(select(Shop).where(Shop.id == shop_id))
            shop = shop_res.scalars().first()

        if not shop:
            raise HTTPException(
                status_code=400,
                detail="No shop associated with this account. Cannot create bill."
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
        # number (duplicate bill_number crash). Fix: convert both sides to the
        # shop's configured timezone (shop.timezone) before comparing dates, so
        # "today" always means the same calendar day on both sides of the query.
        today = shop_local(dt.datetime.now(timezone.utc), shop).date()
        date_str = today.strftime("%Y%m%d")
        shop_tz = shop.timezone or "UTC"

        # Count how many bills this shop already has for today (shop-local day),
        # then the new bill becomes count + 1.
        count_res = await self.db.execute(
            select(func.count(Bill.id)).where(
                Bill.shop_id == shop.id,
                func.date(func.timezone(shop_tz, Bill.timestamp)) == today
            )
        )
        today_count = count_res.scalar() or 0
        seq = str(today_count + 1).zfill(4)  # 0001, 0002, 0003, ...
        bill_number = f"{shop_code}-{date_str}-{seq}"

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

            TWO_PLACES = Decimal("0.01")
            line_subtotal = (menu_item.price * Decimal(str(cart_item.qty))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
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
                "price": float(menu_item.price),
                "unit": menu_item.unit,
                "qty": cart_item.qty,
                "tax_rate": float(tax_rate),
                "line_subtotal": float(line_subtotal),
                "line_tax": float(line_tax),
                "line_total": float(line_total),
            })
            stock_updates.append((menu_item, cart_item.qty))

        # Handle customer
        customer_id = None
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
                customer_id = int(customer.id)

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

        # Atomic bill + stock deduction
        try:
            new_bill = Bill(
                bill_number=bill_number,
                subtotal_amount=subtotal_amount,
                tax_amount=tax_amount,
                total_amount=total_amount,
                payment_method=bill_in.payment_method,
                items_snapshot=items_snapshot,
                timestamp=dt.datetime.now(timezone.utc),
                shop_id=shop.id,
                customer_id=customer_id,
                status=bill_in.status,
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
        local_ts = shop_local(new_bill.timestamp, shop)
        bill_data = {
            "bill_number": new_bill.bill_number,
            "items_snapshot": items_snapshot,
            "subtotal_amount": float(subtotal_amount),
            "tax_amount": float(tax_amount),
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

            TWO_PLACES = Decimal("0.01")
            line_subtotal = (menu_item.price * Decimal(str(cart_item.qty))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            tax_rate = menu_item.tax_rate if menu_item.tax_rate is not None else Decimal("0.00")
            line_tax = (line_subtotal * (tax_rate / Decimal("100.00"))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            line_total = (line_subtotal + line_tax).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            
            subtotal_amount += line_subtotal
            tax_amount += line_tax
            total_amount += line_total

            items_snapshot.append({
                "id": menu_item.id, "name": menu_item.name, "sku": menu_item.sku,
                "category": menu_item.category, "price": float(menu_item.price), "unit": menu_item.unit,
                "qty": cart_item.qty, "tax_rate": float(tax_rate),
                "line_subtotal": float(line_subtotal), "line_tax": float(line_tax), "line_total": float(line_total),
            })
            stock_updates.append((menu_item, cart_item.qty))

        try:
            bill.subtotal_amount = subtotal_amount  # type: ignore
            bill.tax_amount = tax_amount  # type: ignore
            bill.total_amount = total_amount  # type: ignore
            bill.payment_method = bill_in.payment_method  # type: ignore
            bill.status = bill_in.status  # type: ignore
            bill.items_snapshot = items_snapshot  # type: ignore
            bill.timestamp = dt.datetime.now(timezone.utc)  # type: ignore

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
        local_ts = shop_local(bill.timestamp, shop)
        bill_data = {
            "bill_number": bill.bill_number, "items_snapshot": items_snapshot,
            "subtotal_amount": float(subtotal_amount), "tax_amount": float(tax_amount), "total_amount": float(total_amount),
            "date": local_ts.strftime("%Y-%m-%d %H:%M:%S") if local_ts else "",
        }

        return {
            "status": "success", "bill_number": bill.bill_number, "bill_id": str(bill.slug), "total": float(total_amount),
            "message": "Bill updated successfully", "bill_data": bill_data, "shop_data": shop_data, "printer_ip": str(shop.printer_ip) if shop else None,
            "updated_items": updated_stock_data,
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
        except Exception as exc:
            await self.db.rollback()
            logger.error(f"Failed to cancel bill: {str(exc)}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal Server Error: Failed to cancel bill.")

        return {
            "status": "success",
            "message": "Held bill cancelled successfully",
            "updated_items": updated_stock_data,
        }

    @staticmethod
    def format_whatsapp_bill_message(
        bill_number: str,
        items_snapshot: list,
        total_amount: float,
        shop_name: str,
        currency: str = "₹",
        bill_date: str = "",
        footer_message: str = "Thank you for your order! your order will be delivered soon..."
    ) -> str:
        """Single source of truth for WhatsApp bill receipt message format (Domain Layer)."""
        items_text = "\n".join([
            f"{item.get('qty', 1)} x {item.get('name', 'Item')} - {currency}{float(item.get('line_total', 0)):.2f}"
            for item in items_snapshot
        ])

        date_line = f"Date: {bill_date}\n" if bill_date else ""

        return f"""*{shop_name}*
*Tax Invoice*
Bill #{bill_number}
{date_line}
{items_text}

*Total: {currency}{total_amount:.2f}*

{footer_message}"""
