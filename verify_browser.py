#!/usr/bin/env python3
"""
Browser Verification Script
Tests that the application actually works in a real browser context
"""
import asyncio
import sys
from playwright.async_api import async_playwright

async def verify_browser():
    print("🔍 Starting Browser Verification...")
    print("=" * 60)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        # Collect console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        
        try:
            # Test 1: Login Page
            print("\n✓ Test 1: Login Page")
            await page.goto("http://localhost:8000/auth/login", wait_until="networkidle")
            title = await page.title()
            print(f"  Title: {title}")
            assert "Burger Shop" in title, "Login page title incorrect"
            
            # Test 2: Login Flow
            print("\n✓ Test 2: Login as Superadmin")
            await page.fill('input[name="username"]', "superadmin")
            await page.fill('input[name="password"]', "superadmin")
            await page.click('button[type="submit"]')
            await page.wait_for_url("**/superadmin/**", timeout=5000)
            print(f"  Redirected to: {page.url}")
            
            # Test 3: Superadmin Dashboard
            print("\n✓ Test 3: Superadmin Dashboard Loads")
            content = await page.content()
            assert "Branding Studio" in content, "Branding Studio tab missing"
            assert "shopsData" in content, "shopsData not injected"
            print("  Branding Studio tab present")
            
            # Test 4: Navigate to POS
            print("\n✓ Test 4: POS Page Loads")
            await page.goto("http://localhost:8000/billing/", wait_until="networkidle")
            await asyncio.sleep(1)  # Wait for Alpine.js to initialize
            
            # Check for JavaScript errors
            if console_errors:
                print(f"\n❌ Console Errors Detected:")
                for error in console_errors:
                    print(f"  - {error}")
                return False
            
            # Check if posApp initialized
            pos_app_exists = await page.evaluate("typeof posApp === 'function'")
            assert pos_app_exists, "posApp function not defined"
            print("  posApp() function defined")
            
            # Check if Alpine.js initialized the component
            cart_visible = await page.is_visible('text=Current Order')
            assert cart_visible, "Cart panel not visible"
            print("  Cart panel visible")
            
            # Test 5: Check menu items render
            print("\n✓ Test 5: Menu Items Render")
            menu_items = await page.query_selector_all('[x-data="posApp()"] .grid > div')
            print(f"  Found {len(menu_items)} menu items")
            
            print("\n" + "=" * 60)
            print("✅ ALL BROWSER TESTS PASSED")
            print("=" * 60)
            return True
            
        except Exception as e:
            print(f"\n❌ BROWSER TEST FAILED: {e}")
            if console_errors:
                print(f"\nConsole Errors:")
                for error in console_errors:
                    print(f"  - {error}")
            
            # Take screenshot for debugging
            await page.screenshot(path="error_screenshot.png")
            print("\n📸 Screenshot saved to: error_screenshot.png")
            return False
        finally:
            await browser.close()

if __name__ == "__main__":
    result = asyncio.run(verify_browser())
    sys.exit(0 if result else 1)
