#!/bin/bash

# ============================================================================
# Burger POS - Run All Tests
# ============================================================================

echo "======================================"
echo "🧪 Running Burger POS Test Suite"
echo "======================================"
echo ""

# Change to script directory
cd "$(dirname "$0")"

# Run all tests with verbose output
echo "📊 Executing all tests..."
python3 -m pytest tests/ -v --tb=short

# Capture exit code
TEST_EXIT_CODE=$?

echo ""
echo "======================================"
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "✅ All tests passed successfully!"
else
    echo "❌ Some tests failed. Exit code: $TEST_EXIT_CODE"
fi
echo "======================================"

exit $TEST_EXIT_CODE
