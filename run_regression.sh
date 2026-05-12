#!/bin/bash

# ---------------------------------------------------------------------------
# Burger POS - Full Regression Suite Runner
# ---------------------------------------------------------------------------

# 1. Set environment path to ensure scripts can find the app module
export PYTHONPATH=$PYTHONPATH:.

echo "🔄 Step 1: Resetting Database State (Passwords & Login Attempts)..."

# 2. Run the reset script (Target the same test DB as the server)
DB_NAME=pos_test python3 tests/reset_test_state.py

# Check if reset was successful
if [ $? -eq 0 ]; then
    echo "✅ Database reset successfully."
    echo ""
    echo "🚀 Step 2: Launching Full E2E Regression Suite (Port 8007)..."
    
    # 3. Run the full Playwright E2E suite
    # Using -v for verbose output and --tb=short for cleaner failures
    python3 -m pytest tests/test_browser_e2e.py -v --tb=short
else
    echo "❌ Error: Database reset failed. Aborting tests."
    exit 1
fi
