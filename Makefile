.PHONY: test clean server test-core test-all format help

help:
	@echo "Burger POS - Common Commands"
	@echo "============================"
	@echo "make test       - Run core tests"
	@echo "make test-all   - Run all tests"
	@echo "make server     - Start development server"
	@echo "make clean      - Clear Python cache"
	@echo "make format     - Format code (future)"

test: clean
	pytest tests/test_core.py

test-all: clean
	pytest tests/ -v

server:
	lsof -ti:8000 | xargs kill -9 2>/dev/null || true
	python3 main.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

# Quick test without cache clear
quick-test:
	pytest tests/test_core.py

# Test with coverage (future enhancement)
coverage:
	pytest tests/ --cov=. --cov-report=html
