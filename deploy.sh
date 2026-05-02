#!/bin/bash

# ==============================================================================
# Burger POS - Single-Click Deployment & Verification Script
# ==============================================================================
# This script prepares the production environment, installs dependencies, 
# applies database migrations, starts the server, and runs the E2E test suite
# to verify all functionality before customer handover.
# ==============================================================================

set -e # Exit immediately if a command exits with a non-zero status

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}  🍔 Burger POS - Pre-Delivery Deployment & Testing   ${NC}"
echo -e "${BLUE}======================================================${NC}\n"

# ------------------------------------------------------------------------------
# 1. Environment Setup
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[1/6] Checking Python Virtual Environment...${NC}"

if [ -n "$VIRTUAL_ENV" ]; then
    echo -e "${GREEN}✓ Using currently active virtual environment: $(basename "$VIRTUAL_ENV")${NC}\n"
else
    # Default to "venv" if no environment is active
    VENV_NAME="venv"
    if [ ! -d "$VENV_NAME" ]; then
        python3 -m venv $VENV_NAME
        echo -e "${GREEN}✓ Created default virtual environment ($VENV_NAME).${NC}"
    fi
    source $VENV_NAME/bin/activate
    echo -e "${GREEN}✓ Default virtual environment ($VENV_NAME) activated.${NC}\n"
fi

# ------------------------------------------------------------------------------
# 2. Dependency Installation
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[2/6] Installing dependencies...${NC}"
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}✓ Python dependencies installed.${NC}"

# Ensure Playwright browsers are installed for the E2E testing
playwright install chromium -q
echo -e "${GREEN}✓ Playwright testing browsers installed.${NC}\n"

# ------------------------------------------------------------------------------
# 3. Configuration Setup
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[3/6] Checking configuration (.env)...${NC}"
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    
    # Generate a secure secret key automatically
    SECRET_KEY=$(openssl rand -hex 64)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env
    else
        sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env
    fi
    echo -e "${GREEN}✓ .env file created with a secure SECRET_KEY.${NC}"
    echo -e "${RED}⚠️  IMPORTANT: Please update DB_PASSWORD in the .env file if it differs from your Postgres password.${NC}"
else
    echo -e "${GREEN}✓ .env file already exists.${NC}"
fi
echo ""

# ------------------------------------------------------------------------------
# 4. Database Migrations
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[4/6] Applying database migrations...${NC}"
alembic upgrade head
echo -e "${GREEN}✓ Database schema is up to date.${NC}\n"

# ------------------------------------------------------------------------------
# 5. Background Server Startup
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[5/6] Starting FastAPI Server temporarily for testing...${NC}"
# Kill any existing server on port 8000
lsof -t -i:8000 | xargs kill -9 2>/dev/null || true

# Start server in the background and pipe logs to a file
uvicorn main:app --host 0.0.0.0 --port 8000 > server_startup.log 2>&1 &
SERVER_PID=$!

# Wait for server to be fully ready
echo "Waiting for server to initialize..."
sleep 5

if ps -p $SERVER_PID > /dev/null; then
   echo -e "${GREEN}✓ Server started successfully (PID: $SERVER_PID).${NC}\n"
else
   echo -e "${RED}✗ Server failed to start. Check server_startup.log for details.${NC}"
   cat server_startup.log
   exit 1
fi

# ------------------------------------------------------------------------------
# 6. E2E Verification Testing
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[6/6] Running Full E2E Verification Suite...${NC}"
echo "This will launch a headless browser and simulate user interactions."
echo "Running Playwright Tests..."

# We run headless to ensure it works on servers without GUI displays
if pytest tests/test_browser_e2e.py -v -s; then
    TESTS_PASSED=true
    echo -e "\n${GREEN}======================================================${NC}"
    echo -e "${GREEN}  ✅ ALL 22/22 E2E TESTS PASSED SUCCESSFULLY!         ${NC}"
    echo -e "${GREEN}  ✅ APPLICATION IS VERIFIED AND READY FOR HANDOVER!  ${NC}"
    echo -e "${GREEN}======================================================${NC}"
else
    TESTS_PASSED=false
    echo -e "\n${RED}======================================================${NC}"
    echo -e "${RED}  ❌ TESTS FAILED. DO NOT DEPLOY TO CUSTOMER YET.     ${NC}"
    echo -e "${RED}======================================================${NC}"
fi

# ------------------------------------------------------------------------------
# Cleanup
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}Cleaning up testing environment...${NC}"
kill -9 $SERVER_PID 2>/dev/null || true
echo -e "${GREEN}✓ Test server shutdown.${NC}"

if [ "$TESTS_PASSED" = true ]; then
    echo -e "\n${BLUE}To run the app permanently on your server, use PM2 or Systemd:${NC}"
    echo -e "  ${YELLOW}pm2 start \"uvicorn main:app --host 0.0.0.0 --port 8000\" --name \"burger-pos\"${NC}"
    exit 0
else
    exit 1
fi
