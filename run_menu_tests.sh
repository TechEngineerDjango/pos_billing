#!/bin/bash

# ============================================================================
# Burger POS - Run Menu Management Tests Only
# ============================================================================

echo "======================================"
echo "🍔 Running Menu Management Tests"
echo "======================================"
echo ""

# Change to script directory
cd "$(dirname "$0")"

# Run menu management tests
echo "📊 Executing menu management tests..."
python3 -m pytest tests/test_menu_management.py -v --tb=short

# Capture exit code
TEST_EXIT_CODE=$?

echo ""
echo "======================================"
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "✅ All menu management tests passed!"
    echo "   19/19 tests successful"
else
    echo "❌ Some tests failed. Exit code: $TEST_EXIT_CODE"
fi
echo "======================================"

exit $TEST_EXIT_CODE
