# AI-Powered Two-Wheeler Dealership Sales Automation & Lead Management System

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.12%20%7C%203.13-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%7C%2018-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-D71F00.svg?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Tests](https://img.shields.io/badge/pytest-25%20passed-brightgreen.svg?logo=pytest&logoColor=white)](https://pytest.org/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)

A production-grade, enterprise-ready modular monolith backend for two-wheeler dealerships and showrooms. The system automates the complete showroom sales lifecycle—from customer walk-in, vehicle discovery, and inventory reservation to on-road pricing calculation, quotation PDF generation, WhatsApp dispatch, lead state machines, test ride scheduling, analytics dashboards, and AI-driven buyer recommendations.

---

## 1. Key Features

- **Layered Clean Architecture:** Strict separation between API Routes, Pydantic Schemas, Domain Services, Repositories, and SQLAlchemy Async Models.
- **Strict Financial Math:** High-precision `Decimal(12, 2)` calculations across ex-showroom, insurance, RTO, accessories, discounts, and loan EMI annuities—**zero floating-point math**.
- **Role-Based Access Control (RBAC):** Native JWT access & refresh token authentication protecting endpoints across 5 roles: `ADMIN`, `DEALER_OWNER`, `SALES_EXECUTIVE`, `FINANCE_EXECUTIVE`, and `INVENTORY_MANAGER`.
- **Server-Side Quotation & PDF Invoicing:** Automated calculation of on-road vehicle prices, applicable festive offers, down payment loan splits, and instant server-side PDF generation using ReportLab.
- **WhatsApp Cloud API Integration:** Decoupled abstraction supporting Meta WhatsApp Business Cloud API with an automatic development mock provider when credentials are not configured.
- **Atomic Inventory Control:** Physical unit chassis and engine tracking with `SELECT ... FOR UPDATE` row locks to prevent negative inventory and concurrency race conditions.
- **Finite State Machine Lead Tracking:** Validated sales pipeline transitions (`NEW` → `CONTACTED` → `INTERESTED` → `TEST_RIDE` → `NEGOTIATION` → `BOOKED` → `PURCHASED`) with full audit history.
- **Test Ride Double-Booking Prevention:** Collision detection checks that guarantee no duplicate test rides on the same vehicle variant in overlapping windows.
- **Background Task Scheduling:** APScheduler worker continuously checking upcoming follow-up deadlines and dispatching notifications.
- **AI Recommendation Engine:** Vehicle recommendation and lead insight endpoints integrated with Google Gemini GenAI SDK (`gemini-2.5-flash`), with a deterministic heuristic fallback engine ensuring 100% uptime even if external AI keys are unavailable.
- **Audit Logging:** Tamper-evident logging of administrative actions, pricing edits, and inventory adjustments.
- **Automated Test Suite:** 25 comprehensive async integration and unit tests covering all core modules, business rules, and edge cases.

---

## 2. Technology Stack

| Component | Technology | Description |
|---|---|---|
| **Framework** | FastAPI (v0.115+) | High-performance async ASGI web framework |
| **Language** | Python 3.13 / 3.12 | Modern type-annotated Python |
| **Database** | PostgreSQL 16 / 18 | Normalized relational storage |
| **Driver & ORM** | `asyncpg` + SQLAlchemy 2.0+ | Fully asynchronous database queries and connection pooling |
| **Migrations** | Alembic | Version-controlled async transactional schema migrations |
| **Validation** | Pydantic v2 | Strict request/response parsing and data sanitization |
| **Authentication** | PyJWT + Bcrypt | Stateless JWT Bearer tokens and salted password hashing |
| **PDF Generation** | ReportLab 5.0+ | Server-side invoice rendering with zero external C dll dependencies |
| **Job Scheduler** | APScheduler | Background periodic checking of due follow-ups and notifications |
| **AI Integration** | Google GenAI SDK | `gemini-2.5-flash` model for vehicle matching and conversion insights |
| **Testing** | pytest + pytest-asyncio + httpx | Async test client and fixtures |
| **Containerization**| Docker + Docker Compose | Multi-stage image build and single-command local deployment |

---

## 3. Directory Structure

```
backend/
├── alembic/                      # Database migration scripts
│   ├── versions/                 # Revision history files
│   └── env.py                    # Async migration runner
├── app/
│   ├── api/
│   │   ├── dependencies.py       # Current user, RBAC role guards, client IP
│   │   └── routes/               # Modular REST API endpoints (13 routers)
│   ├── core/
│   │   ├── config.py             # Pydantic Settings & environment variables
│   │   ├── exceptions.py         # Custom domain exceptions
│   │   ├── logging.py            # Formatted structured logging
│   │   ├── permissions.py        # RBAC role definitions
│   │   └── security.py           # Bcrypt password hashing & JWT encode/decode
│   ├── database/
│   │   ├── base.py               # DeclarativeBase class
│   │   ├── dependencies.py       # FastAPI get_db dependency
│   │   └── session.py            # Async engine and sessionmaker
│   ├── models/                   # 17 normalized SQLAlchemy 2.0 ORM models
│   ├── schemas/                  # Pydantic v2 schemas and validation contracts
│   ├── repositories/             # Data access layer with SELECT FOR UPDATE locks
│   ├── services/                 # Core business logic services
│   ├── middleware/               # Error handling, request timing, CORS
│   └── main.py                   # FastAPI application factory and lifespan
├── docs/
│   └── API.md                    # Frontend Developer Integration Contract
├── generated_pdfs/               # Server-generated quotation PDF documents
├── scripts/
│   └── seed.py                   # Database seeder (roles, users, catalog, stock)
├── tests/                        # 25 automated pytest test cases
├── .env.example                  # Environment configuration template
├── alembic.ini                   # Alembic configuration
├── Dockerfile                    # Multi-stage production container build
├── docker-compose.yml            # Multi-container orchestration (Backend + Postgres)
└── requirements.txt              # Pinned Python package dependencies
```

---

## 4. Default Seed Users & Credentials

> [!WARNING]
> These credentials are provided for local development and testing. Change them before deploying to production environments.

| Role | Email | Password | Allowed Capabilities |
|---|---|---|---|
| **ADMIN** | `admin@dealership.com` | `AdminPass123!` | Full administrative control, catalog, user management, audit logs |
| **DEALER_OWNER** | `owner@dealership.com` | `OwnerPass123!` | Management KPIs, executive sales/revenue dashboard, reports, pricing |
| **SALES_EXECUTIVE**| `sales@dealership.com` | `SalesPass123!` | Customer CRUD, quotations, PDF generation, WhatsApp dispatch, leads, follow-ups |
| **FINANCE_EXECUTIVE**| `finance@dealership.com` | `FinancePass123!` | Loan eligibility, EMI calculations, financing reviews |
| **INVENTORY_MANAGER**| `inventory@dealership.com`| `InventoryPass123!` | Inventory unit creation, VIN tracking, chassis reservation/release |

---

## 5. Local Development Setup

### Prerequisites
- Python 3.12+ (or Python 3.13)
- PostgreSQL 16+ running locally
- Git

### Step-by-Step Installation

1. **Clone the repository:**
   ```bash
   git clone <repo_url>
   cd SE_Project
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   # Windows:
   .\.venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r backend/requirements.txt
   ```

4. **Configure environment variables:**
   ```bash
   cp backend/.env.example backend/.env
   ```
   Edit `backend/.env` with your PostgreSQL database credentials:
   ```ini
   DATABASE_URL=postgresql+asyncpg://postgres:your_password@localhost:5432/dealership_db
   TEST_DATABASE_URL=postgresql+asyncpg://postgres:your_password@localhost:5432/dealership_test_db
   JWT_SECRET_KEY=your_secure_random_key_here
   ```

5. **Create the databases in PostgreSQL:**
   ```sql
   CREATE DATABASE dealership_db;
   CREATE DATABASE dealership_test_db;
   ```

6. **Run database migrations:**
   ```bash
   cd backend
   alembic upgrade head
   ```

7. **Seed initial development data:**
   ```bash
   python scripts/seed.py
   ```

8. **Start the FastAPI server:**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

9. **Access the application:**
   - Interactive Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
   - Alternative ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)
   - Service Health Check: [http://localhost:8000/health](http://localhost:8000/health)
   - Database Health Check: [http://localhost:8000/health/db](http://localhost:8000/health/db)

---

## 6. Docker Deployment

The application includes a complete Docker setup with Docker Compose.

```bash
# Build and start all services (Backend + PostgreSQL)
docker compose up -d --build

# View container logs
docker compose logs -f backend

# Stop services
docker compose down
```

The containerized backend will automatically run migrations, seed data, and expose the API on `http://localhost:8000`.

---

## 7. Running the Automated Test Suite

The test suite runs against the isolated `dealership_test_db` database using pytest and `pytest-asyncio`.

```bash
cd backend
pytest -v
```

### Verified Test Categories:
- **Authentication:** Login success, invalid credentials, unregistered users, access & refresh token cycles, `/auth/me` security.
- **Customers:** Deduplication by phone number, profile updates, search by phone/name, pagination.
- **Vehicles:** Public catalog discovery, fuel type and price range filtering, RBAC restrictions, negative pricing rejection.
- **Inventory:** Atomic unit reservation, release, double-reservation rejection (`UNIT_NOT_AVAILABLE`), stock summaries.
- **EMI Calculator:** 0% interest promotions, standard 12% 1-year banking calculation, invalid input rejections.
- **Quotations:** Server-side pricing calculation, offer discounts, excessive down payment rejection, PDF invoice generation, WhatsApp dispatch.
- **Leads:** Pipeline state machine transitions (`NEW` through `PURCHASED`), illegal state jump prevention (`NEW` → `PURCHASED` rejected), audit history recording.
- **Follow-ups:** Scheduled task creation, lead date updating, completion tracking with outcome notes.
- **Test Rides:** Booking with time-window collision detection, customer star ratings and feedback capture, cancellation.
- **Dashboard & AI:** KPI aggregation queries, Google Gemini vehicle recommendation with resilient heuristic fallback.

---

## 8. Frontend Integration Contract

The API is fully documented for the independent frontend team in [`docs/API.md`](docs/API.md).

---

## 9. Known Limitations & Future Improvements

### Current Design Considerations
- **Stateless JWT Tokens:** Logout is client-side by discarding tokens. For enterprise blacklisting of revoked access tokens before expiry, Redis-based token invalidation can be added.
- **Local PDF Storage:** Quotation PDFs are stored in the local file system (`generated_pdfs/`). In distributed multi-server deployments, configure AWS S3 / Google Cloud Storage.
- **Synchronous WhatsApp Dispatch:** Messages are sent synchronously or mocked. For ultra-high volume dealerships, dispatch can be offloaded to Celery / RabbitMQ.

### Future Enhancements
- Integration with third-party payment gateways (Razorpay, Stripe) for booking deposit collection.
- Automated OCR extraction of Customer Driving License documents.
- Real-time customer chat integration with WebSocket support.
