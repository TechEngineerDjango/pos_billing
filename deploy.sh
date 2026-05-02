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
    # Use absolute paths derived from $VIRTUAL_ENV to avoid PATH issues in subshells
    PIP_CMD="$VIRTUAL_ENV/bin/pip"
    PY_CMD="$VIRTUAL_ENV/bin/python"
    PLAYWRIGHT_CMD="$VIRTUAL_ENV/bin/playwright"
    PYTEST_CMD="$VIRTUAL_ENV/bin/pytest"
    ALEMBIC_CMD="$VIRTUAL_ENV/bin/alembic"
else
    # Default to ".venv" if no environment is active
    VENV_NAME=".venv"
    if [ ! -d "$VENV_NAME" ]; then
        echo "Creating virtual environment ($VENV_NAME)..."
        if ! python3 -m venv $VENV_NAME; then
            echo -e "${RED}❌ Failed to create virtual environment.${NC}"
            echo -e "${YELLOW}On Ubuntu/Debian, you may need to run: sudo apt install python3-venv${NC}"
            exit 1
        fi
        echo -e "${GREEN}✓ Created default virtual environment ($VENV_NAME).${NC}"
    fi
    # Use direct paths to avoid flaky 'source/activate' behavior in scripts
    PIP_CMD="$VENV_NAME/bin/pip"
    PY_CMD="$VENV_NAME/bin/python"
    PLAYWRIGHT_CMD="$VENV_NAME/bin/playwright"
    PYTEST_CMD="$VENV_NAME/bin/pytest"
    ALEMBIC_CMD="$VENV_NAME/bin/alembic"
    echo -e "${GREEN}✓ Virtual environment paths linked.${NC}\n"
fi

# ------------------------------------------------------------------------------
# 2. Dependency Installation
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[2/5] Installing dependencies...${NC}"
$PIP_CMD install --upgrade pip
$PIP_CMD install -r requirements.txt
echo -e "${GREEN}✓ Python dependencies installed.${NC}"

# Ensure Playwright browsers and Linux system dependencies are installed
$PLAYWRIGHT_CMD install chromium
sudo $PLAYWRIGHT_CMD install-deps chromium || $PLAYWRIGHT_CMD install-deps chromium
echo -e "${GREEN}✓ Playwright testing browsers and dependencies installed.${NC}\n"

# ------------------------------------------------------------------------------
# 3. Configuration Setup
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[3/5] Checking configuration (.env)...${NC}"
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
echo -e "${YELLOW}[4/5] Applying database migrations...${NC}"
$ALEMBIC_CMD upgrade head
echo -e "${GREEN}✓ Database schema is up to date.${NC}\n"

# ------------------------------------------------------------------------------
# 5. E2E Verification Testing
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[5/5] Running Full E2E Verification Suite...${NC}"
echo "This will launch a headless browser and simulate user interactions."
echo "Running Playwright Tests..."

# We run headless to ensure it works on servers without GUI displays
if $PYTEST_CMD tests/test_browser_e2e.py -v -s; then
    TESTS_PASSED=true
    echo -e "\n${GREEN}======================================================${NC}"
    echo -e "${GREEN}  ✅ ALL E2E TESTS PASSED SUCCESSFULLY!               ${NC}"
    echo -e "${GREEN}  ✅ APPLICATION IS VERIFIED AND READY FOR HANDOVER!  ${NC}"
    echo -e "${GREEN}======================================================${NC}"
else
    TESTS_PASSED=false
    echo -e "\n${RED}======================================================${NC}"
    echo -e "${RED}  ❌ TESTS FAILED. DO NOT DEPLOY TO CUSTOMER YET.     ${NC}"
    echo -e "${RED}======================================================${NC}"
fi

# ------------------------------------------------------------------------------
# Final Instructions
# ------------------------------------------------------------------------------
if [ "$TESTS_PASSED" = true ]; then
    echo -e "\n${BLUE}To run the app permanently on your server, use PM2 or Systemd:${NC}"
    echo -e "  ${YELLOW}pm2 start \"uvicorn main:app --host 0.0.0.0 --port 8000\" --name \"burger-pos\"${NC}"
    exit 0
else
    exit 1
fi
