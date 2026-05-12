#!/bin/bash

# ============================================================================
# Burger POS - Start Development Server (DDD Structure)
# ============================================================================

echo "======================================"
echo "🚀 Starting Burger POS Server"
echo "======================================"
echo ""

# Change to script directory
cd "$(dirname "$0")"

# Check if port 8000 is already in use
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚠️  Port 8000 is already in use! Restarting..."
    lsof -ti :8000 | xargs kill -9
    sleep 1
fi

echo "📦 Starting FastAPI server with Uvicorn..."
echo "   URL: http://localhost:8000"
echo ""

# Start the server using the new app package
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
