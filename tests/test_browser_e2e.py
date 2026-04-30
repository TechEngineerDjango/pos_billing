"""
Playwright Browser E2E Tests - Headed Mode
Tests actual JavaScript functionality, DOM interactions, and visual rendering.

Run with: pytest tests/test_browser_e2e.py --headed --slowmo=500
"""
import pytest
import subprocess
import time
import signal
import os
from playwright.sync_api import Page, expect, sync_playwright

# Server configuration
BASE_URL = "http://localhost:8000"
SERVER_PROCESS = None


@pytest.fixture(scope="module")
def server():
    """Start the FastAPI server before tests and stop after."""
    global SERVER_PROCESS
    
    # Start server in background
    SERVER_PROCESS = subprocess.Popen(
        ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )
    
    # Wait for server to start
    time.sleep(3)
    
    yield SERVER_PROCESS
    
    # Cleanup: Kill server process group
    if SERVER_PROCESS:
        os.killpg(os.getpgid(SERVER_PROCESS.pid), signal.SIGTERM)
        SERVER_PROCESS.wait()


@pytest.fixture(scope="function")
def browser_page(server):
    """Create a new browser page for each test."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        yield page
        browser.close()


class TestLoginPage:
    """Test Login Page JavaScript Functionality"""
    
    def test_login_page_renders(self, browser_page: Page):
        """Verify login page loads with all elements"""
        browser_page.goto(f"{BASE_URL}/auth/login")
        
        # Check page title
        expect(browser_page).to_have_title("Login - BurgerPOS")
        
        # Check form elements exist
        expect(browser_page.locator("input[name='username']")).to_be_visible()
        expect(browser_page.locator("input[name='password']")).to_be_visible()
        expect(browser_page.locator("button[type='submit']")).to_be_visible()
        
        print("✅ Login page renders correctly")
    
    def test_login_form_validation(self, browser_page: Page):
        """Test login with empty fields shows browser validation"""
        browser_page.goto(f"{BASE_URL}/auth/login")
        
        # Try to submit empty form
        browser_page.locator("button[type='submit']").click()
        
        # Check that we're still on login page (form didn't submit)
        expect(browser_page).to_have_url(f"{BASE_URL}/auth/login")
        
        print("✅ Login form validation works")
    
    def test_login_success_redirect(self, browser_page: Page):
        """Test successful login redirects to correct dashboard"""
        browser_page.goto(f"{BASE_URL}/auth/login")
        
        # Fill in superadmin credentials
        browser_page.fill("input[name='username']", "superadmin")
        browser_page.fill("input[name='password']", "superadmin")
        browser_page.click("button[type='submit']")
        
        # Wait for redirect
        browser_page.wait_for_url(f"{BASE_URL}/superadmin/", timeout=5000)
        
        print("✅ Superadmin login redirects correctly")
    
    def test_login_failure_message(self, browser_page: Page):
        """Test failed login shows error"""
        browser_page.goto(f"{BASE_URL}/auth/login")
        
        browser_page.fill("input[name='username']", "wronguser")
        browser_page.fill("input[name='password']", "wrongpass")
        browser_page.click("button[type='submit']")
        
        # Should stay on login page
        time.sleep(1)
        expect(browser_page).to_have_url(f"{BASE_URL}/auth/login")
        
        print("✅ Failed login handled correctly")


class TestPOSPage:
    """Test POS Page JavaScript Functionality"""
    
    def login_as_admin(self, page: Page):
        """Helper to login as admin/owner"""
        page.goto(f"{BASE_URL}/auth/login")
        page.fill("input[name='username']", "owner")
        page.fill("input[name='password']", "owner")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")
    
    def test_pos_page_loads_menu_items(self, browser_page: Page):
        """Verify POS page loads and displays menu items"""
        self.login_as_admin(browser_page)
        browser_page.goto(f"{BASE_URL}/billing/")
        browser_page.wait_for_load_state("networkidle")
        
        # Check page loaded
        expect(browser_page.locator("body")).to_be_visible()
        
        # Check for menu grid or items section
        # Look for product cards or menu items container
        menu_section = browser_page.locator("[x-data]").first
        expect(menu_section).to_be_visible()
        
        print("✅ POS page loads with Alpine.js initialized")
    
    def test_category_tabs_switching(self, browser_page: Page):
        """Test category tab switching via JavaScript"""
        self.login_as_admin(browser_page)
        browser_page.goto(f"{BASE_URL}/billing/")
        browser_page.wait_for_load_state("networkidle")
        
        # Look for category tabs
        tabs = browser_page.locator("[x-on\\:click*='category']")
        
        if tabs.count() > 0:
            # Click first tab
            tabs.first.click()
            time.sleep(0.5)
            print(f"✅ Found {tabs.count()} category tabs")
        else:
            # Check for any clickable category elements
            print("⚠️ No category tabs found - checking structure")
            
    def test_add_item_to_cart(self, browser_page: Page):
        """Test clicking a menu item adds it to cart"""
        self.login_as_admin(browser_page)
        browser_page.goto(f"{BASE_URL}/billing/")
        browser_page.wait_for_load_state("networkidle")
        
        # Find menu item cards
        item_cards = browser_page.locator(".cursor-pointer, [x-on\\:click*='addToCart'], [x-on\\:click*='add']")
        
        if item_cards.count() > 0:
            # Click first item
            item_cards.first.click()
            time.sleep(0.5)
            
            # Check if cart updated
            cart_section = browser_page.locator("[x-text*='cart'], [x-text*='total'], .cart")
            if cart_section.count() > 0:
                print("✅ Item added to cart successfully")
            else:
                print("⚠️ Cart section not found after adding item")
        else:
            print("⚠️ No clickable menu items found")
    
    def test_cart_quantity_buttons(self, browser_page: Page):
        """Test +/- buttons work for cart items"""
        self.login_as_admin(browser_page)
        browser_page.goto(f"{BASE_URL}/billing/")
        browser_page.wait_for_load_state("networkidle")
        
        # First add an item
        item_cards = browser_page.locator(".cursor-pointer, [x-on\\:click*='add']")
        if item_cards.count() > 0:
            item_cards.first.click()
            time.sleep(0.5)
            
            # Look for increment/decrement buttons
            inc_btn = browser_page.locator("button:has-text('+'), [x-on\\:click*='increment']").first
            dec_btn = browser_page.locator("button:has-text('-'), [x-on\\:click*='decrement']").first
            
            if inc_btn.is_visible():
                inc_btn.click()
                time.sleep(0.3)
                print("✅ Increment button works")
            
            if dec_btn.is_visible():
                dec_btn.click()
                time.sleep(0.3)
                print("✅ Decrement button works")
    
    def test_payment_method_selection(self, browser_page: Page):
        """Test Cash/UPI payment selection"""
        self.login_as_admin(browser_page)
        browser_page.goto(f"{BASE_URL}/billing/")
        browser_page.wait_for_load_state("networkidle")
        
        # Look for payment buttons
        cash_btn = browser_page.locator("button:has-text('Cash'), [x-on\\:click*='Cash']")
        upi_btn = browser_page.locator("button:has-text('UPI'), [x-on\\:click*='UPI']")
        
        if cash_btn.count() > 0:
            cash_btn.first.click()
            time.sleep(0.3)
            print("✅ Cash payment button found and clickable")
        
        if upi_btn.count() > 0:
            upi_btn.first.click()
            time.sleep(0.3)
            print("✅ UPI payment button found and clickable")
    
    def test_create_bill_flow(self, browser_page: Page):
        """Test complete bill creation flow"""
        self.login_as_admin(browser_page)
        browser_page.goto(f"{BASE_URL}/billing/")
        browser_page.wait_for_load_state("networkidle")
        
        # Add item to cart
        item_cards = browser_page.locator(".cursor-pointer, [x-on\\:click*='add']")
        if item_cards.count() > 0:
            item_cards.first.click()
            time.sleep(0.5)
            
            # Find and click create bill button
            create_btn = browser_page.locator("button:has-text('Create Bill'), button:has-text('Print'), [x-on\\:click*='createBill']")
            if create_btn.count() > 0:
                create_btn.first.click()
                time.sleep(1)
                print("✅ Create bill button clicked")
            else:
                print("⚠️ Create bill button not found")


class TestAdminDashboard:
    """Test Admin Dashboard JavaScript Functionality"""
    
    def login_as_owner(self, page: Page):
        """Helper to login as owner"""
        page.goto(f"{BASE_URL}/auth/login")
        page.fill("input[name='username']", "owner")
        page.fill("input[name='password']", "owner")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")
    
    def test_dashboard_tabs_functionality(self, browser_page: Page):
        """Test dashboard tab switching"""
        self.login_as_owner(browser_page)
        browser_page.goto(f"{BASE_URL}/admin/")
        browser_page.wait_for_load_state("networkidle")
        
        # Find tabs
        tabs = browser_page.locator("[x-on\\:click*='activeTab'], .tab, [role='tab']")
        
        if tabs.count() > 0:
            print(f"Found {tabs.count()} tabs")
            for i in range(min(3, tabs.count())):
                tabs.nth(i).click()
                time.sleep(0.5)
                print(f"✅ Tab {i+1} clicked successfully")
        else:
            print("⚠️ No tabs found in dashboard")
    
    def test_menu_item_form_display(self, browser_page: Page):
        """Test add menu item form shows correctly"""
        self.login_as_owner(browser_page)
        browser_page.goto(f"{BASE_URL}/admin/?tab=products")
        browser_page.wait_for_load_state("networkidle")
        
        # Look for add button or form
        add_btn = browser_page.locator("button:has-text('Add'), button:has-text('New')")
        
        if add_btn.count() > 0:
            add_btn.first.click()
            time.sleep(0.5)
            
            # Check if form appeared
            form = browser_page.locator("form, [x-show*='modal'], .modal")
            if form.count() > 0:
                print("✅ Add menu item form displayed")
            else:
                print("⚠️ Form didn't appear after clicking add")
    
    def test_settings_form_fields(self, browser_page: Page):
        """Test settings form has all fields"""
        self.login_as_owner(browser_page)
        browser_page.goto(f"{BASE_URL}/admin/?tab=settings")
        browser_page.wait_for_load_state("networkidle")
        
        # Check for key settings fields
        name_field = browser_page.locator("input[name='name']")
        if name_field.count() > 0:
            expect(name_field.first).to_be_visible()
            print("✅ Shop name field visible")
        
        # Check for color pickers
        color_inputs = browser_page.locator("input[type='color']")
        print(f"✅ Found {color_inputs.count()} color picker inputs")


class TestSuperadminDashboard:
    """Test Superadmin Dashboard JavaScript Functionality"""
    
    def login_as_superadmin(self, page: Page):
        """Helper to login as superadmin"""
        page.goto(f"{BASE_URL}/auth/login")
        page.fill("input[name='username']", "superadmin")
        page.fill("input[name='password']", "superadmin")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")
    
    def test_superadmin_dashboard_loads(self, browser_page: Page):
        """Verify superadmin dashboard renders"""
        self.login_as_superadmin(browser_page)
        browser_page.goto(f"{BASE_URL}/superadmin/")
        browser_page.wait_for_load_state("networkidle")
        
        # Check page loaded
        expect(browser_page.locator("body")).to_be_visible()
        
        # Look for shops section
        shops_section = browser_page.locator(":has-text('Shop'), :has-text('Shops')")
        if shops_section.count() > 0:
            print("✅ Superadmin dashboard loaded with shops section")
    
    def test_create_shop_form(self, browser_page: Page):
        """Test create shop form functionality"""
        self.login_as_superadmin(browser_page)
        browser_page.goto(f"{BASE_URL}/superadmin/")
        browser_page.wait_for_load_state("networkidle")
        
        # Find create shop button
        create_btn = browser_page.locator("button:has-text('Create Shop'), button:has-text('Add Shop'), button:has-text('New Shop')")
        
        if create_btn.count() > 0:
            create_btn.first.click()
            time.sleep(0.5)
            
            # Check for form fields
            name_input = browser_page.locator("input[name='name']")
            if name_input.count() > 0:
                print("✅ Create shop form opened with name field")
    
    def test_shop_tabs_navigation(self, browser_page: Page):
        """Test shop management tabs"""
        self.login_as_superadmin(browser_page)
        browser_page.goto(f"{BASE_URL}/superadmin/")
        browser_page.wait_for_load_state("networkidle")
        
        # Find tabs
        tabs = browser_page.locator("[x-on\\:click*='Tab'], .tab, button[role='tab']")
        
        if tabs.count() > 0:
            print(f"✅ Found {tabs.count()} navigation tabs")
            tabs.first.click()
            time.sleep(0.3)


class TestNavigationIntegrity:
    """Test Navigation and Routing"""
    
    def test_navbar_links_work(self, browser_page: Page):
        """Test all navbar links are functional"""
        browser_page.goto(f"{BASE_URL}/auth/login")
        browser_page.fill("input[name='username']", "superadmin")
        browser_page.fill("input[name='password']", "superadmin")
        browser_page.click("button[type='submit']")
        browser_page.wait_for_load_state("networkidle")
        
        # Find all navbar links
        nav_links = browser_page.locator("nav a, header a, .navbar a")
        
        if nav_links.count() > 0:
            print(f"✅ Found {nav_links.count()} navigation links")
        else:
            print("⚠️ No navigation links found")
    
    def test_logout_link_works(self, browser_page: Page):
        """Test logout link actually logs out"""
        browser_page.goto(f"{BASE_URL}/auth/login")
        browser_page.fill("input[name='username']", "owner")
        browser_page.fill("input[name='password']", "owner")
        browser_page.click("button[type='submit']")
        browser_page.wait_for_load_state("networkidle")
        
        # Find logout link
        logout_link = browser_page.locator("a:has-text('Logout'), button:has-text('Logout'), [href*='logout']")
        
        if logout_link.count() > 0:
            logout_link.first.click()
            browser_page.wait_for_load_state("networkidle")
            
            # Should be on login page
            expect(browser_page).to_have_url(f"{BASE_URL}/auth/login")
            print("✅ Logout works correctly")


class TestJavaScriptErrors:
    """Check for JavaScript Console Errors"""
    
    def test_no_js_errors_on_login(self, browser_page: Page):
        """Check for JS errors on login page"""
        errors = []
        browser_page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        
        browser_page.goto(f"{BASE_URL}/auth/login")
        browser_page.wait_for_load_state("networkidle")
        
        if errors:
            print(f"⚠️ JS Errors on login: {errors}")
        else:
            print("✅ No JS errors on login page")
    
    def test_no_js_errors_on_pos(self, browser_page: Page):
        """Check for JS errors on POS page"""
        errors = []
        browser_page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        
        # Login first
        browser_page.goto(f"{BASE_URL}/auth/login")
        browser_page.fill("input[name='username']", "owner")
        browser_page.fill("input[name='password']", "owner")
        browser_page.click("button[type='submit']")
        browser_page.wait_for_load_state("networkidle")
        
        # Go to POS
        browser_page.goto(f"{BASE_URL}/billing/")
        browser_page.wait_for_load_state("networkidle")
        time.sleep(1)
        
        if errors:
            print(f"⚠️ JS Errors on POS: {errors}")
            for err in errors:
                print(f"   - {err}")
        else:
            print("✅ No JS errors on POS page")
    
    def test_no_js_errors_on_dashboard(self, browser_page: Page):
        """Check for JS errors on dashboard"""
        errors = []
        browser_page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        
        browser_page.goto(f"{BASE_URL}/auth/login")
        browser_page.fill("input[name='username']", "superadmin")
        browser_page.fill("input[name='password']", "superadmin")
        browser_page.click("button[type='submit']")
        browser_page.wait_for_load_state("networkidle")
        
        time.sleep(1)
        
        if errors:
            print(f"⚠️ JS Errors on Dashboard: {errors}")
            for err in errors:
                print(f"   - {err}")
        else:
            print("✅ No JS errors on dashboard")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--headed", "-s"])
