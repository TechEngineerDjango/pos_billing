import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class WhatsAppService:
    def __init__(self, api_key: str = None):
        self.api_key = api_key

    def format_bill_message(self, bill_data: Dict[str, Any], shop_data: Dict[str, Any]) -> str:
        """Format bill information as a text message."""
        currency = shop_data.get("currency", "₹")
        
        message = f"""🏪 *{shop_data.get('name', 'Shop')}*
{shop_data.get('address', '')}

📄 *Bill #{bill_data['bill_number']}*
📅 Date: {bill_data['timestamp']}
💳 Payment: {bill_data['payment_method']}

*Items:*
"""
        
        for item in bill_data.get('items', []):
            name = item.get('name', 'Item')
            qty = item.get('qty', 1)
            price = item.get('price', 0)
            line_total = item.get('line_total', qty * price)
            message += f"\n• {name} x{qty} - {currency}{line_total:.2f}"
        
        message += f"\n\n{'='*30}\n*Total: {currency}{bill_data['total_amount']:.2f}*\n{'='*30}"
        message += "\n\nThank you for your business! 🙏"
        
        return message

    async def send_bill_receipt(self, phone_number: str, bill_data: Dict[str, Any], shop_data: Dict[str, Any]):
        """
        Send bill receipt via WhatsApp.
        In production, this would integrate with Twilio, Meta Cloud API, or similar.
        """
        message = self.format_bill_message(bill_data, shop_data)
        
        logger.info(f"[WhatsApp Stub] Sending to {phone_number}:")
        logger.info(f"\n{message}\n")
        
        # Valid implementation would be:
        # await client.messages.create(
        #     from_='whatsapp:+YOUR_NUMBER',
        #     to=f'whatsapp:{phone_number}',
        #     body=message
        # )
        
        return True

    def get_whatsapp_url(self, phone_number: str, bill_data: Dict[str, Any], shop_data: Dict[str, Any]) -> str:
        """Generate a wa.me URL for client-side sending."""
        import urllib.parse
        message = self.format_bill_message(bill_data, shop_data)
        encoded_message = urllib.parse.quote(message)
        return f"https://wa.me/{phone_number}?text={encoded_message}"

whatsapp_service = WhatsAppService()

