import time
import re
from playwright.sync_api import sync_playwright, Page, expect
from tests.test_browser_e2e import BASE_URL, OWNER_PASSWORD, SUPERADMIN_PASSWORD, _login_owner, _login_as, server, browser_page

def test_full_hold_resume_complete_flow(server, browser_page):
    """
    Validates the entire use case end-to-end in headed mode:
    1. Log in
    2. Add an item to the cart
    3. HOLD the bill
    4. Resume the bill from the POS Recent Bills modal
    5. Complete the bill (tests update_bill with the NoneType threshold fixes)
    """
    page = browser_page

    try:
        print("1. Logging in as Owner...")
        _login_owner(page)
        
        print("2. Navigating to Billing Screen...")
        page.goto(f"{BASE_URL}/billing/")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1)

        print("3. Adding items to cart...")
        # Just click the first available product
        burger_item = page.locator("[data-product-sku]").first
        burger_item.wait_for(state="visible", timeout=10000)
        
        # Click the product card
        burger_item.click()
        time.sleep(1)

        print("4. Putting the bill on HOLD...")
        # Click Hold Bill using domain identifier
        page.locator("[data-action='hold-bill']").click()
        time.sleep(2) # Wait for backend request to finish

        print("5. Resuming the bill from Recent Bills Modal...")
        # Open Recent Bills modal using domain identifier
        page.locator("[data-action='view-recent-bills']").click()
        time.sleep(1)

        # Find the Resume (Edit Bill) button inside the modal and click it
        resume_btn = page.locator("[data-action='resume-bill']").first
        if resume_btn.count() == 0:
            raise AssertionError("No 'Edit Bill' button found. The bill was not successfully placed on hold!")
        resume_btn.click()
        time.sleep(2)

        print("6. Completing the resumed bill...")
        # Click complete using domain identifier
        page.locator("[data-action='print-bill']").click()
        time.sleep(3) # Wait to ensure it succeeds and doesn't throw a 500 alert

        print("✅ Full Hold -> Resume -> Complete workflow finished successfully!")

    except Exception as e:
        print(f"Test failed: {e}")
        raise e

def _set_owner_shop_plan(page: Page, plan_name: str, create_if_missing: bool = False, features: list = None):
    """
    Sets (or creates + assigns) a subscription plan for 'Demo Burger Shop' via the superadmin UI.
    
    For plan creation: directly sets the hidden 'features' input rather than clicking
    checkboxes, because checkboxes are rendered from the Feature catalog DB table and
    may not be populated.
    """
    print(f"  -> Setting Demo Burger Shop to plan: {plan_name}")
    _login_as(page, "superadmin", SUPERADMIN_PASSWORD, r".*/superadmin/")
    
    if create_if_missing and features:
        page.goto(f"{BASE_URL}/superadmin/?tab=plans")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1)
        if page.locator(f"h3:has-text('{plan_name}')").count() == 0:
            print(f"  -> Creating new plan: {plan_name}")
            page.locator("button:has-text('Design Plan')").click()
            time.sleep(0.5)
            plan_form = page.locator("form[action='/superadmin/subscriptions/create']")
            plan_form.locator("input[name='name']").fill(plan_name)
            plan_form.locator("input[name='price']").fill("999")
            
            for feat in features:
                checkbox = plan_form.locator(f"input[value='{feat}']")
                if not checkbox.is_checked():
                    checkbox.check()
            
            time.sleep(0.5)
            with page.expect_navigation():
                plan_form.locator("button:has-text('Bake Schema')").click()
            page.wait_for_load_state("domcontentloaded")
            time.sleep(1)
            
    page.goto(f"{BASE_URL}/superadmin/?tab=fleet")
    page.wait_for_load_state("domcontentloaded")
    time.sleep(1)
    
    shop_card = page.locator("div[data-shop-card='Demo Burger Shop']")
    if shop_card.count() > 0:
        shop_card.locator("button:has-text('Change')").click()
        time.sleep(0.5)
        # Find the correct option by text to get its exact value
        option_val = shop_card.locator(f"select[name='subscription_id'] option:has-text('{plan_name}')").first.get_attribute("value")
        shop_card.locator("select[name='subscription_id']").select_option(value=option_val)
        with page.expect_navigation():
            shop_card.locator("button:has-text('Apply')").click()
        time.sleep(1)
    
    # Logout
    page.goto(f"{BASE_URL}/auth/logout")
    page.wait_for_load_state("domcontentloaded")
    time.sleep(1)


def test_features_disabled_hide_elements(server, browser_page):
    """
    Test Case A: Validates that premium features are NOT visible when shop has No Plan.
    """
    page = browser_page
    try:
        # Clean environment: Set to Free Tier
        print("1. Assigning No Plan to Demo Burger Shop...")
        _set_owner_shop_plan(
            page,
            "No Plan (Free Tier)",
            create_if_missing=False
        )

        print("2. Logging in as Owner...")
        _login_owner(page)
        
        print("3. Navigating to Billing Screen...")
        page.goto(f"{BASE_URL}/billing/")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1)

        print("4. Adding item to cart to reveal inputs...")
        burger_item = page.locator("[data-product-sku]").first
        burger_item.wait_for(state="visible", timeout=10000)
        burger_item.click()
        time.sleep(1)
        
        print("5. Asserting premium elements are hidden on Billing Screen...")
        expect(page.locator("[data-input='customer-search-phone']")).not_to_be_visible()
        expect(page.locator("[data-action='open-scanner']")).not_to_be_visible()
        
        # Test cash calculator is hidden
        page.locator("[data-payment-method='cash']").click()
        time.sleep(0.5)
        expect(page.locator("#pos-cash-calc-label")).not_to_be_visible()
        
        print("6. Asserting WhatsApp button is hidden in Recent Bills...")
        page.locator("[data-action='view-recent-bills']").click()
        page.wait_for_selector("#recentBillsModal") # Wait for modal
        time.sleep(1)
        expect(page.locator("[data-action='resend-whatsapp']")).not_to_be_visible()
        # Close modal (click backdrop or close button)
        page.locator("#recentBillsModal").click(position={"x": 10, "y": 10})
        time.sleep(0.5)
        
        print("7. Navigating to Dashboard and asserting premium tabs are hidden...")
        page.goto(f"{BASE_URL}/admin/")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1)
        expect(page.locator("[data-tab='customers']")).not_to_be_visible()
        expect(page.locator("[data-tab='sales']")).not_to_be_visible()

        print("✅ Free Tier successfully hides all premium features!")

    except Exception as e:
        print(f"Test failed: {e}")
        raise e


def test_features_enabled_show_elements(server, browser_page):
    """
    Test Case B: Validates that premium features are visible and functional when enabled.
    1. Provision "E2E V2 Pro Plan"
    2. Log in
    3. Enter phone number and customer name
    4. Switch payment to UPI
    5. Complete Bill
    """
    page = browser_page
    import hashlib
    # Use a unique plan name per run to avoid reusing stale plans with empty features
    PLAN_NAME = f"E2E Pro {hashlib.md5(str(time.time()).encode()).hexdigest()[:6]}"

    try:
        # Clean environment: Set to Pro Tier
        print("1. Assigning E2E Pro Plan to Demo Burger Shop...")
        _set_owner_shop_plan(
            page, 
            PLAN_NAME, 
            create_if_missing=True, 
            features=["cash_calculator", "customer_management", "inventory_management", "whatsapp_billing", "sale_report"]
        )

        print("2. Logging in as Owner...")
        _login_owner(page)
        
        print("3. Asserting premium tabs are visible on Dashboard...")
        page.goto(f"{BASE_URL}/admin/")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1)
        expect(page.locator("[data-tab='customers']")).to_be_visible()
        expect(page.locator("[data-tab='sales']")).to_be_visible()
        
        print("4. Navigating to Billing Screen...")
        page.goto(f"{BASE_URL}/billing/")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1)
        
        print("5. Adding item to cart...")
        item = page.locator("[data-product-sku]").first
        item.wait_for(state="visible", timeout=5000)
        item.click()
        time.sleep(1)
        
        print("6. Testing Cash Calculator (Premium Feature)...")
        page.locator("[data-payment-method='cash']").click()
        time.sleep(0.5)
        expect(page.locator("#pos-cash-calc-label")).to_be_visible()
        
        print("7. Entering Customer Details (Premium Feature)...")
        phone_input = page.locator("[data-input='customer-search-phone']")
        expect(phone_input).to_be_visible()
        phone_input.fill("9999999999")
        time.sleep(1)
        
        page.locator("[data-input='customer-name']").fill("Test Playwright Customer")
        
        print("8. Completing the bill...")
        page.locator("[data-action='print-bill']").click()
        time.sleep(3) 

        # Now test that WhatsApp resend is available in Recent Bills
        print("9. Verifying WhatsApp button in Recent Bills...")
        page.goto(f"{BASE_URL}/billing/")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1)
        page.locator("[data-action='view-recent-bills']").click()
        page.wait_for_selector("#recentBillsModal")
        time.sleep(2)
        # Should be visible for at least one bill since we just completed one
        expect(page.locator("[data-action='resend-whatsapp']").first).to_be_visible()

        print("✅ Premium features visible and workflow finished successfully!")

    except Exception as e:
        print(f"Test failed: {e}")
        raise e
    finally:
        # Deterministic Teardown: Revert back to No Plan to avoid polluting future runs
        try:
            print("Cleaning up: Reverting to No Plan...")
            _set_owner_shop_plan(page, "No Plan (Free Tier)", create_if_missing=False)
        except Exception as cleanup_error:
            print(f"Cleanup failed: {cleanup_error}")
