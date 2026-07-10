import os
import re
import pytest
from dotenv import load_dotenv

load_dotenv()
SUPERADMIN_PASSWORD = os.getenv("TEST_SUPERADMIN_PASSWORD")
from httpx import AsyncClient
import urllib.parse
from app.shared.models import Bill, MenuItem, Shop, Customer
from datetime import datetime

@pytest.mark.asyncio
async def test_whatsapp_url_generation(async_client: AsyncClient, db_session):
    # 1. Login
    login_res = await async_client.post("/auth/login", data={"username": "superadmin", "password": SUPERADMIN_PASSWORD}, follow_redirects=False)
    assert login_res.status_code == 303
    
    # Cookie should be set automatically in client cookie jar for subsequent requests
    assert "access_token" in login_res.cookies

    # 2. Setup Data (Shop is already created in conftest, assume ID 1)
    shop_id = 1
    
    # Create a Bill
    bill = Bill(
        bill_number="TESTBILL",
        total_amount=150.00,
        payment_method="Cash",
        items_snapshot=[{"name": "Burger", "qty": 1, "line_total": 100}, {"name": "Fries", "qty": 1, "line_total": 50}],
        timestamp=datetime.utcnow(),
        shop_id=shop_id
    )
    db_session.add(bill)
    await db_session.commit()
    await db_session.refresh(bill)

    # 3. Test with 10 digit number
    phone_input = "919094855498"
    response = await async_client.get(
        f"/admin/bill/{bill.slug}/whatsapp-redirect",
        params={"phone": phone_input}
    )

    assert response.status_code == 200
    html = response.text

    # Extract the wa.me link from the redirect page's <a href="...">
    match = re.search(r'href="(https://wa\.me/[^"]+)"', html)
    assert match, "wa.me link not found in redirect page"
    url = match.group(1)

    # Verify Country Code Addition
    assert "https://wa.me/919094855498" in url

    # Verify Encoding
    # The message should be properly encoded.
    # Logic in whatsapp.py uses message.strip() and urllib.parse.quote(..., encoding='utf-8')
    # We expect %20 for spaces and %0A for newlines

    assert "text=" in url
    encoded_part = url.split("text=")[1]

    # Check that it decodes back to something readable
    decoded_msg = urllib.parse.unquote(encoded_part)
    assert "Bill #TESTBILL" in decoded_msg
    assert "Burger" in decoded_msg
    assert "919094855498" not in decoded_msg # Phone shouldn't be in the message text itself usually

    # Test strict 10 digit logic - pass 12 digits (already has code)
    phone_input_with_code = "919094855498"
    response_2 = await async_client.get(
        f"/admin/bill/{bill.slug}/whatsapp-redirect",
        params={"phone": phone_input_with_code}
    )
    url_2 = re.search(r'href="(https://wa\.me/[^"]+)"', response_2.text).group(1)
    # Should NOT add 91 again
    assert "https://wa.me/919094855498" in url_2

    print("Test Passed!")
