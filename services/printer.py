import logging
from escpos.printer import Network, Dummy
from config import settings

logger = logging.getLogger(__name__)

class ThermalPrinter:
    def __init__(self, ip=None):
        self.ip = ip or settings.DEFAULT_PRINTER_IP
        self.printer = None

    def connect(self):
        try:
            if self.ip == "mock":
                 logger.info("Connecting to MOCK printer")
                 self.printer = Dummy()
            else:
                 logger.info(f"Connecting to Network printer at {self.ip}")
                 self.printer = Network(self.ip, timeout=5) # 5s timeout
            return True
        except Exception as e:
            logger.error(f"Printer Connection Failed: {e}")
            return False

    def print_bill(self, bill_data: dict, shop_profile: dict = None):
        """
        Prints the bill receipt.
        bill_data: {
            "bill_number": str,
            "items_snapshot": [{"name": str, "qty": int, "price": float}],
            "total_amount": float,
            "date": str
        }
        """
        if not self.printer:
            if not self.connect():
                logger.warning("Skipping print job - Printer unreachable")
                # In production, we might want to queue this or retry.
                return

        try:
            # Header
            shop_name = shop_profile.get("name", "Burger Shop") if shop_profile else "Burger Shop"
            address = shop_profile.get("address", "") if shop_profile else ""
            
            # Simple formatting
            self.printer.set(align='center')
            self.printer.text(f"\n{shop_name}\n")
            if address:
                self.printer.text(f"{address}\n")
            self.printer.text("--------------------------------\n")
            
            # Bill Details
            self.printer.set(align='left')
            self.printer.text(f"Bill No: {bill_data.get('bill_number')}\n")
            self.printer.text(f"Date:    {bill_data.get('date')}\n")
            self.printer.text("--------------------------------\n")
            
            # Items
            # 32 char width standard for many thermal printers, or 42/48. We assume 32-42 approx.
            # Format: Name (20) Qty (5) Price (Right)
            self.printer.text(f"{'Item':<16} {'Qty':<4} {'Price':>10}\n")
            
            for item in bill_data.get('items_snapshot', []):
                name = item.get('name', 'Item')[:16]
                qty = item.get('qty', 1)
                price = item.get('price', 0.0) * qty
                self.printer.text(f"{name:<16} {qty:<4} {price:>10.2f}\n")
            
            self.printer.text("--------------------------------\n")
            self.printer.set(align='right')
            self.printer.text(f"TOTAL: {bill_data.get('total_amount', 0.0):.2f}\n")
            self.printer.text("--------------------------------\n")
            
            self.printer.set(align='center')
            self.printer.text("Thank You!\nVisit Again\n\n\n")
            
            self.printer.cut()
            
            # If dummy, log output
            if isinstance(self.printer, Dummy):
                logger.info(f"Mock Print Output:\n{self.printer.output.decode('utf-8', errors='ignore')}")

        except Exception as e:
            logger.error(f"Printing Error: {e}")
        finally:
            try:
                # Close connection to free resources
                self.printer.close()
            except OSError as e:
                logger.warning(f"Failed to close printer connection: {e}")
            self.printer = None

# Global helper can be used if needed, or instantiate per request
def print_bill_bg(bill_data: dict, shop_data: dict, printer_ip: str):
    """Background task wrapper"""
    printer = ThermalPrinter(ip=printer_ip)
    printer.print_bill(bill_data, shop_data)
