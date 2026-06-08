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
echo "   💻 Local URL: http://localhost:8000"

# Fetch local IP (macOS/Linux)
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
if [ ! -z "$LOCAL_IP" ]; then
    echo "   📱 Phone URL: https://${LOCAL_IP}:8000"
else
    echo "   📱 Phone URL: https://<your-computer-ip>:8000"
fi
echo ""
echo "Note: Your browser may warn you about an 'unsafe' connection because this is a local offline certificate. You can safely click 'Advanced -> Proceed' to test the camera."
echo ""

# Start the server using the new app package with local SSL
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem
