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


def build_redirect_page(safe_whatsapp_url: str) -> str:
    """Auto-redirecting HTML page that bridges a server-formatted WhatsApp
    message into a synchronous window.open() from the frontend. Caller must
    have already HTML-escaped safe_whatsapp_url."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Redirecting to WhatsApp...</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: sans-serif; background: #111; color: #fff; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
            .loader {{ border: 4px solid #333; border-top: 4px solid #25D366; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin-bottom: 20px; }}
            @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
            a {{ color: #25D366; text-decoration: none; border: 1px solid #25D366; padding: 10px 20px; border-radius: 20px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="loader"></div>
        <h2>Opening WhatsApp...</h2>
        <p>If it doesn't open automatically, <a href="{safe_whatsapp_url}">Click Here</a></p>
        <script>
            window.location.href = "{safe_whatsapp_url}";
        </script>
    </body>
    </html>
    """


whatsapp_service = WhatsAppService()
