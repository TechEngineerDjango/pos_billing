import logging
import ipaddress
from escpos.printer import Network, Dummy
from app.core.config import settings

logger = logging.getLogger(__name__)

def is_safe_ip(ip: str) -> bool:
    if ip == "mock":
        return True
    try:
        ip_obj = ipaddress.ip_address(ip)
        # Block localhost (127.0.0.1) and link-local (169.254.x.x - AWS Metadata)
        if ip_obj.is_loopback or ip_obj.is_link_local:
            return False
        # Block 0.0.0.0 or broadcast
        if ip_obj.is_unspecified or ip_obj.is_multicast:
            return False
        return True
    except ValueError:
        return False

class ThermalPrinter:
    def __init__(self, ip=None):
        self.ip = ip or settings.DEFAULT_PRINTER_IP
        self.printer = None

    def connect(self):
        if not self.ip or not is_safe_ip(self.ip):
            logger.error(f"Printer Connection Failed: Invalid or unsafe IP address -> {self.ip}")
            return False
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
            footer = shop_profile.get("receipt_footer", "Thank You!\\nVisit Again\\n\\n\\n") if shop_profile else "Thank You!\\nVisit Again\\n\\n\\n"
            alignment = shop_profile.get("printer_alignment", "center") if shop_profile else "center"
            width_setting = shop_profile.get("printer_paper_width", "80mm") if shop_profile else "80mm"
            
            line_len = 32 if width_setting == "58mm" else 48
            sep_line = "-" * line_len + "\n"
            
            # Simple formatting
            self.printer.set(align=alignment)
            self.printer.text(f"\n{shop_name}\n")
            if address:
                self.printer.text(f"{address}\n")
            self.printer.text(sep_line)
            
            # Bill Details
            self.printer.set(align='left')
            self.printer.text(f"Bill No: {bill_data.get('bill_number')}\n")
            self.printer.text(f"Date:    {bill_data.get('date')}\n")
            self.printer.text(sep_line)
            
            # Items
            # Format: Name (name_len) Qty (5) Price (8)
            name_len = line_len - 14  # space for qty (5) + price (8) + spaces (1)
            self.printer.text(f"{'Item':<{name_len}} {'Qty':<4} {'Price':>8}\n")
            
            for item in bill_data.get('items_snapshot', []):
                name = item.get('name', 'Item')[:name_len]
                qty = item.get('qty', 1)
                price = item.get('price', 0.0) * float(qty)
                self.printer.text(f"{name:<{name_len}} {qty:<4} {price:>8.2f}\n")
            
            self.printer.text(sep_line)
            self.printer.set(align='right')
            
            subtotal = bill_data.get('subtotal_amount')
            tax = bill_data.get('tax_amount')
            total = bill_data.get('total_amount', 0.0)

            if tax is not None and tax > 0:
                self.printer.text(f"SUBTOTAL: {subtotal:.2f}\n")
                self.printer.text(f"TAX: {tax:.2f}\n")
            
            self.printer.text(f"TOTAL: {total:.2f}\n")
            self.printer.text(sep_line)
            
            self.printer.set(align=alignment)
            
            # Format footer message to include newlines if needed, ensuring padding
            footer_text = footer.replace("\\n", "\n")
            if not footer_text.endswith("\n\n\n"):
                footer_text += "\n\n\n"
            
            self.printer.text(footer_text)
            
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
