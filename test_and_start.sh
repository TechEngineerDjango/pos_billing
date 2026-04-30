#!/bin/bash

# ============================================================================
# Burger POS - Run Tests Then Start Server
# ============================================================================

echo "======================================"
echo "🧪 Burger POS - Test & Start"
echo "======================================"
echo ""

# Change to script directory
cd "$(dirname "$0")"

# Step 1: Run tests
echo "Step 1: Running tests..."
echo "======================================"
python3 -m pytest tests/ -v --tb=line

TEST_EXIT_CODE=$?

echo ""
echo "======================================"
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "✅ All tests passed!"
    echo ""
    echo "Step 2: Starting server..."
    echo "======================================"
    
    # Check if port is in use
    if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo "⚠️  Port 8000 already in use. Stopping existing server..."
        lsof -ti :8000 | xargs kill -9
        sleep 2
    fi
    
    echo "🚀 Starting FastAPI server..."
    echo "   URL: http://localhost:8000"
    echo "   Press Ctrl+C to stop"
    echo ""
    
    # Start server
    python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
else
    echo "❌ Tests failed! Server not started."
    echo "   Please fix failing tests before starting the server."
    exit $TEST_EXIT_CODE
fi
