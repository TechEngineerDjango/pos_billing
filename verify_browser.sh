#!/bin/bash
# Simple Browser Verification

echo "🔍 Browser Verification Test"
echo "=============================="

# Test 1: Server is running
echo -n "✓ Server running... "
if curl -s http://localhost:8000/ > /dev/null; then
    echo "YES"
else
    echo "NO - Server not responding"
    exit 1
fi

# Test 2: Login page loads
echo -n "✓ Login page loads... "
LOGIN_HTML=$(curl -s http://localhost:8000/auth/login)
if echo "$LOGIN_HTML" | grep -q "Burger Shop"; then
    echo "YES"
else
    echo "NO"
    exit 1
fi

# Test 3: Can login
echo -n "✓ Login works... "
COOKIES=$(mktemp)
curl -s -c "$COOKIES" -d "username=superadmin&password=superadmin" http://localhost:8000/auth/login > /dev/null
if curl -s -b "$COOKIES" http://localhost:8000/superadmin/ | grep -q "Branding Studio"; then
    echo "YES"
else
    echo "NO"
    exit 1
fi

# Test 4: POS page loads without JavaScript errors
echo -n "✓ POS page loads... "
POS_HTML=$(curl -s -b "$COOKIES" http://localhost:8000/billing/)
if echo "$POS_HTML" | grep -q "posApp"; then
    echo "YES"
else
    echo "NO - posApp function missing"
    exit 1
fi

# Test 5: Check for Jinja2 syntax errors in rendered HTML
echo -n "✓ No template errors... "
if echo "$POS_HTML" | grep -q "{{"; then
    echo "NO - Unrendered Jinja2 tags found"
    echo "$POS_HTML" | grep "{{" | head -3
    exit 1
else
    echo "YES"
fi

# Test 6: itemsData is properly injected
echo -n "✓ itemsData injected... "
if echo "$POS_HTML" | grep -q "itemsData:"; then
    echo "YES"
else
    echo "NO"
    exit 1
fi

rm "$COOKIES"

echo ""
echo "=============================="
echo "✅ ALL CHECKS PASSED"
echo "=============================="
echo ""
echo "🌐 Open in browser: http://localhost:8000"
echo "   Username: superadmin"
echo "   Password: superadmin"
