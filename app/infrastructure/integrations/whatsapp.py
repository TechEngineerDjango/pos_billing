import logging
import urllib.parse
from typing import Dict, Any, List, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class WhatsAppService:
    def __init__(self, api_key: str = None):
        self.api_key = api_key

    @staticmethod
    def sanitize_phone(phone_number: str, country_code: Optional[str] = None) -> str:
        """
        Strip non-digits and prepend country code.
        Uses provided country_code, falls back to settings default if not provided.
        """
        phone = ''.join(filter(str.isdigit, phone_number))
        code = country_code or settings.DEFAULT_COUNTRY_CODE

        if len(phone) == 10 and not phone.startswith(code):
            phone = code + phone
        return phone

    def generate_wa_link(self, phone_number: str, message: str, country_code: Optional[str] = None) -> str:
        """Generate a complete wa.me URL with pre-filled message text."""
        phone = self.sanitize_phone(phone_number, country_code)
        return f"https://wa.me/{phone}?text={urllib.parse.quote(message.strip(), encoding='utf-8')}"

    async def send_message(self, phone_number: str, message: str, country_code: Optional[str] = None) -> bool:
        """
        Send a WhatsApp message via API.
        In production, this would integrate with Twilio, Meta Cloud API, or similar.
        """
        phone = self.sanitize_phone(phone_number, country_code)
        
        logger.info(f"[WhatsApp Stub] Sending to {phone}:")
        logger.info(f"\n{message}\n")

        # Valid implementation would be:
        # await client.messages.create(
        #     from_='whatsapp:+YOUR_NUMBER',
        #     to=f'whatsapp:{phone}',
        #     body=message
        # )

        return True


whatsapp_service = WhatsAppService()
