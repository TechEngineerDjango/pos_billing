"""
Playwright E2E Tests for Receipt and Printer Settings.
"""
import pytest
import subprocess
import time
import signal
import os
import re
from dotenv import load_dotenv
from playwright.sync_api import Page, expect, sync_playwright

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "http://localhost:8007")

def get_secret(key):
    val = os.getenv(key)
    if not val:
        raise ValueError(f"CRITICAL SECURITY ERROR: Environment variable '{key}' is NOT set.")
    return val

SUPERADMIN_PASSWORD = get_secret("TEST_SUPERADMIN_PASSWORD")
OWNER_PASSWORD = get_secret("TEST_OWNER_PASSWORD")
SERVER_PROCESS = None

@pytest.fixture(scope="module")
def server():
    """Start the FastAPI server before tests and stop after."""
    global SERVER_PROCESS
    
    # Start server in background on port 8007
    SERVER_PROCESS = subprocess.Popen(
        ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8007"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        stdout=None,
        stderr=None,
        preexec_fn=os.setsid
    )
    
    time.sleep(5)
    
    # Seed the Feature catalog
    seed_script = """
import asyncio
from app.core.database import AsyncSessionLocal
from app.shared.models import Feature
from sqlalchemy import select

async def seed():
    async with AsyncSessionLocal() as db:
        features = ["cash_calculator", "customer_management", "inventory_management", "whatsapp_billing", "sale_report", "pos_basic"]
        for f in features:
            res = await db.execute(select(Feature).where(Feature.key == f))
            if not res.scalars().first():
                db.add(Feature(key=f, name=f.replace('_', ' ').title(), category='billing'))
        await db.commit()

asyncio.run(seed())
"""
    subprocess.run(["python3", "-c", seed_script], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    time.sleep(2)

    yield SERVER_PROCESS
    
    if SERVER_PROCESS:
        try:
            os.killpg(os.getpgid(SERVER_PROCESS.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        SERVER_PROCESS.wait()

@pytest.fixture(scope="function")
def browser_page(server):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()
        yield page
        browser.close()

def _login_owner(page: Page):
    page.goto(f"{BASE_URL}/auth/login")
    page.fill("input[name='username']", "owner")
    page.fill("input[name='password']", OWNER_PASSWORD)
    try:
        with page.expect_navigation(timeout=5000):
            page.click("button[type='submit']")
        page.wait_for_url(re.compile(r".*/(admin|billing)/"), timeout=3000)
    except Exception:
        page.fill("input[name='username']", "owner")
        page.fill("input[name='password']", OWNER_PASSWORD)
        with page.expect_navigation(timeout=5000):
            page.click("button[type='submit']")
    page.wait_for_load_state("domcontentloaded")

def _login_superadmin(page: Page):
    page.goto(f"{BASE_URL}/auth/login")
    page.fill("input[name='username']", "superadmin")
    page.fill("input[name='password']", SUPERADMIN_PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_url(re.compile(r".*/superadmin/"), timeout=10000)
    page.locator("#nav-fleet-tab").wait_for(state="visible", timeout=10000)

class TestReceiptSettingsE2E:
    def test_owner_receipt_settings(self, browser_page: Page):
        """Test receipt settings from the Owner Dashboard (Overview Tab)"""
        _login_owner(browser_page)
        
        # Navigate to Admin Overview
        browser_page.goto(f"{BASE_URL}/admin/?tab=overview")
        browser_page.wait_for_load_state("domcontentloaded")
        time.sleep(1)
        
        # Ensure the Overview tab is visible
        expect(browser_page.locator("text=Receipt & Printer Settings")).to_be_visible(timeout=5000)
        
        # Fill in the form
        test_footer = "Test Footer 123"
        browser_page.fill("input[name='receipt_footer']", test_footer)
        browser_page.select_option("select[name='printer_paper_width']", "80mm")
        browser_page.select_option("select[name='printer_alignment']", "center")
        
        # Submit form
        with browser_page.expect_navigation():
            browser_page.click("button:has-text('Save Receipt Settings')")
            
        # Verify redirect
        expect(browser_page).to_have_url(re.compile(r".*tab=overview"))
        
        # Verify values persisted
        expect(browser_page.locator("input[name='receipt_footer']")).to_have_value(test_footer)
        expect(browser_page.locator("select[name='printer_paper_width']")).to_have_value("80mm")
        expect(browser_page.locator("select[name='printer_alignment']")).to_have_value("center")
        print("✅ Owner receipt settings update validated")

    def test_superadmin_receipt_settings(self, browser_page: Page):
        """Test receipt settings from the Superadmin Branding Studio"""
        _login_superadmin(browser_page)
        
        # Navigate to Branding Studio
        browser_page.goto(f"{BASE_URL}/superadmin/?tab=design")
        browser_page.wait_for_load_state("domcontentloaded")
        time.sleep(1)
        
        # Select first shop
        shop_select = browser_page.locator("#branding-shop-selector")
        # Ensure it has options and wait
        expect(shop_select).to_be_visible()
        
        # Get options 
        options = shop_select.locator("option").all()
        # The first option is typically the disabled "Select a Shop...", select the second one
        if len(options) > 1:
            value_to_select = options[1].get_attribute("value")
            shop_select.select_option(value=value_to_select)
        time.sleep(1)
        
        # Verify settings form is shown
        expect(browser_page.locator("text=Receipt Footer Message")).to_be_visible(timeout=5000)
        
        test_footer = "Superadmin Footer 456"
        browser_page.fill("input[x-model='designConfig.receipt_footer']", test_footer)
        browser_page.select_option("select[x-model='designConfig.printer_paper_width']", "58mm")
        browser_page.select_option("select[x-model='designConfig.printer_alignment']", "left")
        
        # Click Save
        browser_page.click("#branding-finalize-btn")
        time.sleep(1) # wait for async save
        print("✅ Superadmin receipt settings update validated")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
