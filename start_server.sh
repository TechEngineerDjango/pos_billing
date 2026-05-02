#!/bin/bash

# ============================================================================
# Burger POS - Start Development Server
# ============================================================================

echo "======================================"
echo "🚀 Starting Burger POS Server"
echo "======================================"
echo ""

# Change to script directory
cd "$(dirname "$0")"

# Check if port 8000 is already in use
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚠️  Port 8000 is already in use!"
    echo "   Checking what's running..."
    lsof -i :8000
    echo ""
    read -p "Kill existing process and restart? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "🔄 Stopping existing server..."
        lsof -ti :8000 | xargs kill -9
        sleep 2
    else
        echo "❌ Cancelled. Please stop the existing server first."
        exit 1
    fi
fi

echo "📦 Starting FastAPI server with Uvicorn..."
echo "   URL: http://localhost:8000"
echo "   Press Ctrl+C to stop"
echo ""

# Start the server
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
