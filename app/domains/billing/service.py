from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException
from pydantic import BaseModel
from typing import List, Optional
import datetime as dt
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP
import re
import logging

logger = logging.getLogger(__name__)

DEFAULT_LOW_STOCK_THRESHOLD = 5.0

from app.shared.models import Bill, MenuItem, Shop, User, Customer, StockMovement
from app.infrastructure.integrations.notifications import get_notification_service
from app.shared.schemas import BillCreate

class BillingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notification_svc = get_notification_service(db)

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

        # Traceable bill number
        shop_code = re.sub(r"[^A-Z0-9]", "", shop.name.upper())[:4] or "SHOP"
        timestamp_ms = int(dt.datetime.now(timezone.utc).timestamp())
        bill_number = f"{shop_code}-{timestamp_ms}"

        subtotal_amount = Decimal("0.00")
        tax_amount = Decimal("0.00")
        total_amount = Decimal("0.00")
        items_snapshot = []
        stock_updates: list[tuple] = []

        # 1. Validate and build snapshot
        for cart_item in bill_in.items:
            result = await self.db.execute(
                select(MenuItem).where(
                    MenuItem.id == cart_item.id,
                    MenuItem.shop_id == shop.id,
                    MenuItem.is_active == True,
                ).with_for_update()
            )
            menu_item = result.scalars().first()

            if not menu_item:
                raise HTTPException(
                    status_code=400,
                    detail=f"Item ID {cart_item.id} not found in this shop's menu"
                )

            # Server-side stock enforcement
            if menu_item.stock_quantity is not None and cart_item.qty > menu_item.stock_quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for '{menu_item.name}'. "
                           f"Available: {menu_item.stock_quantity}, Requested: {cart_item.qty}"
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

        if not items_snapshot:
            raise HTTPException(status_code=400, detail="No valid items found")

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
                    customer = Customer(
                        name=customer_name,
                        phone_number=phone,
                        shop_id=shop.id
                    )
                    self.db.add(customer)
                    await self.db.flush()
                customer_id = customer.id

        shop_data = {"name": shop.name, "address": shop.address or ""}
        printer_ip = shop.printer_ip

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

            # Deduct stock and create audit records ONLY if Completed
            if bill_in.status == "Completed":
                for menu_item, qty_sold in stock_updates:
                    if menu_item.stock_quantity is not None:
                        menu_item.stock_quantity -= qty_sold
                        # Fire in-app notification if at or below threshold
                        threshold = menu_item.low_stock_threshold or DEFAULT_LOW_STOCK_THRESHOLD
                        if menu_item.stock_quantity <= threshold:
                            await self.notification_svc.send_low_stock_alert(
                                shop_id=shop.id,
                                item_name=menu_item.name,
                                current_qty=menu_item.stock_quantity,
                                threshold=threshold,
                                item_id=menu_item.id,
                            )

                    self.db.add(StockMovement(
                        menu_item_id=menu_item.id,
                        shop_id=shop.id,
                        change_qty=-qty_sold,
                        reason="sale",
                        note=f"Bill #{bill_number}",
                        created_by_user_id=current_user.id,
                    ))

            await self.db.commit()
            await self.db.refresh(new_bill)
        except HTTPException:
            raise
        except Exception as exc:
            await self.db.rollback()
            logger.error(f"Failed to create bill: {str(exc)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to create bill. Error: {str(exc)}")

        bill_data = {
            "bill_number": new_bill.bill_number,
            "items_snapshot": items_snapshot,
            "subtotal_amount": float(subtotal_amount),
            "tax_amount": float(tax_amount),
            "total_amount": float(total_amount),
            "date": new_bill.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        }

        return {
            "status": "success",
            "bill_number": new_bill.bill_number,
            "bill_id": new_bill.slug, # Use slug for frontend links
            "total": float(total_amount),
            "message": "Bill created and printing",
            "bill_data": bill_data,
            "shop_data": shop_data,
            "printer_ip": printer_ip
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

        for cart_item in bill_in.items:
            result = await self.db.execute(
                select(MenuItem)
                .where(
                    MenuItem.id == cart_item.id, 
                    MenuItem.shop_id == shop_id, 
                    MenuItem.is_active == True
                )
                .with_for_update()
            )
            menu_item = result.scalars().first()

            if not menu_item:
                raise HTTPException(status_code=400, detail=f"Item ID {cart_item.id} not found")

            if bill_in.status == "Completed" and menu_item.stock_quantity is not None and cart_item.qty > menu_item.stock_quantity:
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
            bill.subtotal_amount = subtotal_amount
            bill.tax_amount = tax_amount
            bill.total_amount = total_amount
            bill.payment_method = bill_in.payment_method
            bill.status = bill_in.status
            bill.items_snapshot = items_snapshot
            bill.timestamp = dt.datetime.now(timezone.utc)

            if bill_in.status == "Completed":
                for menu_item, qty_sold in stock_updates:
                    if menu_item.stock_quantity is not None:
                        menu_item.stock_quantity -= qty_sold
                        self.db.add(StockMovement(
                            menu_item_id=menu_item.id, shop_id=shop_id, change_qty=-qty_sold,
                            reason="sale", note=f"Bill #{bill.bill_number} (Resumed)", created_by_user_id=current_user.id,
                        ))
                        
                        threshold = menu_item.low_stock_threshold or DEFAULT_LOW_STOCK_THRESHOLD
                        if menu_item.stock_quantity <= threshold:
                            await self.notification_svc.send_low_stock_alert(
                                shop_id=shop_id,
                                item_name=menu_item.name,
                                current_qty=menu_item.stock_quantity,
                                threshold=threshold,
                                item_id=menu_item.id,
                            )

            await self.db.commit()
            await self.db.refresh(bill)
        except Exception as exc:
            await self.db.rollback()
            logger.error(f"Failed to update bill: {str(exc)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to update bill. Error: {str(exc)}")

        shop_res = await self.db.execute(select(Shop).where(Shop.id == shop_id))
        shop = shop_res.scalars().first()
        shop_data = {"name": shop.name if shop else "", "address": shop.address if shop else ""}

        bill_data = {
            "bill_number": bill.bill_number, "items_snapshot": items_snapshot,
            "subtotal_amount": float(subtotal_amount), "tax_amount": float(tax_amount), "total_amount": float(total_amount),
            "date": bill.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        }

        return {
            "status": "success", "bill_number": bill.bill_number, "bill_id": bill.slug, "total": float(total_amount),
            "message": "Bill updated successfully", "bill_data": bill_data, "shop_data": shop_data, "printer_ip": shop.printer_ip if shop else None
        }
