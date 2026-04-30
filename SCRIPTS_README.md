# 🚀 Burger POS - Quick Start Scripts

This directory contains convenient shell scripts to run tests and start the server.

## 📋 Available Scripts

### 1. **run_tests.sh** - Run All Tests
Runs the complete test suite with detailed output.

```bash
./run_tests.sh
```

**Output:** Verbose test results showing all test names and their status.

---

### 2. **run_menu_tests.sh** - Run Menu Management Tests Only
Runs only the menu management test suite (19 tests).

```bash
./run_menu_tests.sh
```

**Output:** Focused test results for menu management module.

---

### 3. **quick_test.sh** - Quick Test Summary
Runs all tests with minimal output for a quick health check.

```bash
./quick_test.sh
```

**Output:** Simple pass/fail summary without verbose details.

---

### 4. **start_server.sh** - Start Development Server
Starts the FastAPI development server on port 8000.

```bash
./start_server.sh
```

**Features:**
- Checks if port 8000 is already in use
- Offers to kill existing processes
- Starts server with hot-reload enabled
- Shows server URL: http://localhost:8000

**Stop Server:** Press `Ctrl+C`

---

### 5. **test_and_start.sh** - Test Then Start Server ⭐ RECOMMENDED
Runs all tests and only starts the server if all tests pass.

```bash
./test_and_start.sh
```

**Workflow:**
1. ✅ Runs complete test suite
2. ✅ If all tests pass → Starts server
3. ❌ If tests fail → Shows error, does NOT start server

**This is the recommended script for development!**

---

## 🎯 Common Usage Scenarios

### Quick Development Workflow
```bash
# Test and start in one command
./test_and_start.sh
```

### Just Run Tests
```bash
# Full test suite
./run_tests.sh

# Or just menu tests
./run_menu_tests.sh

# Or quick check
./quick_test.sh
```

### Just Start Server
```bash
./start_server.sh
```

---

## 📊 Test Coverage

Current test statistics:
- **Total Tests:** 42
- **Menu Management:** 19 tests (100% coverage)
- **Authentication:** 5 tests
- **Dashboard:** 4 tests
- **Billing/POS:** 5 tests
- **Customization:** 2 tests
- **Integration:** 9 tests

---

## 🔧 Troubleshooting

### "Permission denied" error
Make sure scripts are executable:
```bash
chmod +x *.sh
```

### Port 8000 already in use
The `start_server.sh` and `test_and_start.sh` scripts will automatically detect and offer to kill existing processes.

Manual fix:
```bash
lsof -ti :8000 | xargs kill -9
```

### Tests failing
Run with verbose output to see details:
```bash
./run_tests.sh
```

Or run specific test file:
```bash
python3 -m pytest tests/test_menu_management.py -v
```

---

## 📝 Manual Commands

If you prefer to run commands manually:

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_menu_management.py -v

# Start server
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## 🎨 Script Features

All scripts include:
- ✅ Proper error handling
- ✅ Exit codes (0 = success, non-zero = failure)
- ✅ Colored/formatted output
- ✅ Current directory auto-detection
- ✅ Clear status messages

---

## 🚦 Quick Reference

| Need to... | Run this... |
|-----------|-------------|
| Test everything | `./run_tests.sh` |
| Test menu only | `./run_menu_tests.sh` |
| Quick health check | `./quick_test.sh` |
| Start server only | `./start_server.sh` |
| **Test & start** ⭐ | `./test_and_start.sh` |

---

**Happy coding! 🍔**
