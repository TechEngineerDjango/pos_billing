# Burger POS - Architecture Overview (DDD)

This document outlines the **Domain-Driven Design (DDD)** structure of the Burger POS system. The project is organized to ensure high scalability, separation of concerns, and ease of testing.

## 📂 Directory Structure

### 🏗️ `/app` (Core Application)
The heart of the system, containing all business logic, infrastructure, and frontend assets.

#### 📦 `app/domains/`
Contains the core business domains. Each folder represents a vertical slice of the application.
- **`auth/`**: Authentication, login, session management, and RBAC (Role-Based Access Control).
- **`billing/`**: POS terminal logic, bill generation, and printing orchestration.
- **`tenancy/`**: Multi-tenant logic, superadmin dashboard, and shop management.
- **`inventory/`**, **`customers/`**, **`subscriptions/`**: (Future/Placeholder) Dedicated domain logic for scaling.

#### ⚙️ `app/core/`
System-wide infrastructure and shared services.
- **`config.py`**: Pydantic-based configuration management (Environment variables).
- **`database.py`**: SQLAlchemy async engine and session management.
- **`base.py`**: Base declarative model for SQLAlchemy.
- **`middleware/`**: Custom FastAPI middleware (CSRF, Request ID tracking).
- **`dependencies/`**: Shared FastAPI dependencies (e.g., `verify_csrf`).

#### 🌐 `app/frontend/`
All presentation layer assets.
- **`templates/`**: Jinja2 templates (Dashboards, Login, POS).
- **`static/`**: Static assets (Tailwind CSS, Alpine.js, Uploaded logos/icons).

#### 🛠️ `app/infrastructure/`
External integrations and service adaptations.
- **`integrations/`**: 
    - `whatsapp.py`: WhatsApp API integration.
    - `printer.py`: Thermal printer communication.
    - `image.py`: Image upload and processing logic.

#### 🔗 `app/shared/`
Shared resources used across multiple domains.
- **`models.py`**: Centralized SQLAlchemy data models.
- **`schemas.py`**: Shared Pydantic models for request/response validation.
- **`features.py`**: Subscription-based feature flagging logic.

---

### 🧪 `/tests`
Comprehensive automated test suite.
- **`test_core.py`**: Backend API and integration tests.
- **`test_browser_e2e.py`**: Playwright-based browser automation (Real UI testing).
- **`conftest.py`**: Shared test fixtures and in-memory DB setup.

---

### 📦 Root Files
- **`app/main.py`**: The application entry point (FastAPI initialization).
- **`start_server.sh`**: Developer-friendly startup script.
- **`run_tests.sh`**: Script to execute the full test suite.
- **`alembic/`**: Database migration scripts.
- **`requirements.txt`**: Python dependencies.

## 🚀 Key Design Principles
1. **Separation of Concerns**: Domain logic is isolated from infrastructure.
2. **Deterministic State**: Frontend state (Alpine.js) reflects backend truth.
3. **Async Everywhere**: The entire stack uses non-blocking I/O for performance.
4. **Security First**: Built-in CSRF protection, RBAC, and strict input validation.
