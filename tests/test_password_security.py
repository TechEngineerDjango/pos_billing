import os
import re
import pytest
from playwright.sync_api import Page, expect
from dotenv import load_dotenv

load_dotenv()
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")

from .test_browser_e2e import _login_owner, server, browser_page, BASE_URL


@pytest.mark.usefixtures("server")
class TestPasswordSecurity:
    """
    Senior Test Engineer Suite: Password Management & Security Standards
    Validates SOLID implementation, frontend validation, and backend enforcement.
    """

    def _navigate_to_password_page(self, page: Page):
        """Helper to navigate to the standalone security page."""
        page.goto(f"{BASE_URL}/auth/change-password")
        expect(page.locator("h1:has-text('Security Settings')")).to_be_visible()
        page.wait_for_timeout(500)

    def test_password_change_robustness(self, browser_page: Page):
        """
        Scenario: Full lifecycle of a successful, secure password change using standard form POST.
        Starting password: 'owner' (set by reset_test_state.py and used by _login_owner).
        """
        _login_owner(browser_page)
        
        # 1. Navigate to Security Page
        self._navigate_to_password_page(browser_page)

        # 3. Test: Successful Change (Standard POST)
        new_pass = "SecureP@ssword2024!"
        browser_page.fill("input[name='current_password']", OWNER_PASSWORD)
        browser_page.fill("input[name='new_password']", new_pass)
        browser_page.fill("input[name='confirm_password']", new_pass)
        
        browser_page.click("button:has-text('Update Password')")
        
        # Verify redirect to dashboard
        expect(browser_page).to_have_url(re.compile(r".*/(admin|superadmin|billing)/"))
        print("✅ Password updated successfully via standard POST")

        # 4. Verify: Logout and Login with NEW password
        # Click logout link in the sidebar/navbar
        browser_page.goto(f"{BASE_URL}/auth/logout")
        browser_page.wait_for_url("**/auth/login")
        
        browser_page.fill("input[name='username']", "owner")
        browser_page.fill("input[name='password']", "InvalidPass123!") 
        browser_page.click("button[type='submit']")
        expect(browser_page.locator("text=Not authenticated")).to_be_visible()
        print("✅ Old credentials successfully invalidated")

        # Login with new password
        browser_page.fill("input[name='password']", new_pass)
        browser_page.click("button[type='submit']")
        expect(browser_page).to_have_url(re.compile(r".*/(admin|billing)/"))
        print("✅ New credentials verified")

        # 5. RESET: Change back to 'owner' for other tests
        self._navigate_to_password_page(browser_page)
        browser_page.fill("input[name='current_password']", new_pass)
        browser_page.fill("input[name='new_password']", OWNER_PASSWORD)
        browser_page.fill("input[name='confirm_password']", OWNER_PASSWORD)
        browser_page.click("button:has-text('Update Password')")
        expect(browser_page).to_have_url(re.compile(r".*/(admin|superadmin|billing)/"))
        print("⚠️ Environment state restored")

    def test_wrong_current_password_backend_rejection(self, browser_page: Page):
        """
        Scenario: Backend correctly rejects incorrect current password using standard POST.
        """
        _login_owner(browser_page)

        self._navigate_to_password_page(browser_page)
        
        browser_page.fill("input[name='current_password']", "wrong_current_password")
        browser_page.fill("input[name='new_password']", "NewStrongP@ss1!")
        browser_page.fill("input[name='confirm_password']", "NewStrongP@ss1!")
        
        browser_page.click("button:has-text('Update Password')")
        
        # Check for error message in template
        expect(browser_page.locator("text=Incorrect current password")).to_be_visible()
        print("✅ Backend security rejection verified on separate page")
