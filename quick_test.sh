#!/bin/bash

# ============================================================================
# Burger POS - Quick Test Summary
# ============================================================================

echo "======================================"
echo "⚡ Quick Test Summary"
echo "======================================"
echo ""

# Change to script directory
cd "$(dirname "$0")"

# Run tests with minimal output
python3 -m pytest tests/ -q --tb=no

# Capture exit code
TEST_EXIT_CODE=$?

echo ""
echo "======================================"
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "✅ All tests passed!"
else
    echo "❌ Some tests failed"
    echo "   Run './run_tests.sh' for details"
fi
echo "======================================"

exit $TEST_EXIT_CODE
