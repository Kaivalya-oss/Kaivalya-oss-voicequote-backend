# Frontend Integration Contract: Two-Wheeler Dealership REST API

This document serves as the official API integration contract between the FastAPI backend and frontend client applications.

---

## Base URL & Conventions

- **Base URL:** `http://localhost:8000/api/v1`
- **Interactive Swagger Documentation:** `http://localhost:8000/docs`
- **ReDoc Documentation:** `http://localhost:8000/redoc`
- **OpenAPI Schema (JSON):** `http://localhost:8000/openapi.json`
- **Content-Type:** `application/json`

---

## Standard Response Envelopes

### 1. Single Resource Response
```json
{
  "success": true,
  "data": { ... }
}
```

### 2. Paginated Collection Response
```json
{
  "items": [ ... ],
  "total": 45,
  "page": 1,
  "page_size": 20,
  "pages": 3
}
```

### 3. Standard Error Envelope
```json
{
  "success": false,
  "error": {
    "code": "CUSTOMER_NOT_FOUND",
    "message": "Customer with identifier '999' was not found.",
    "details": null
  }
}
```

---

## Authentication & Authorization (RBAC)

All protected endpoints require an `Authorization` header containing the JWT Bearer token:
```http
Authorization: Bearer <access_token>
```

### Supported User Roles
- `ADMIN`: Full system administrative control
- `DEALER_OWNER`: Access to management analytics, reports, vehicle pricing, and showroom operations
- `SALES_EXECUTIVE`: Access to customer records, catalog lookups, quotation engine, WhatsApp dispatch, leads, follow-ups, and test rides
- `FINANCE_EXECUTIVE`: Specialized access to loan and EMI calculations
- `INVENTORY_MANAGER`: Inventory unit registration, chassis status updates, stock reservation, and warehouse release

---

## Endpoint Catalog

### 1. Authentication (`/auth`)

#### `POST /auth/login`
- **Auth:** None
- **Request Body:**
  ```json
  {
    "email": "sales@dealership.com",
    "password": "SalesPass123!"
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "access_token": "eyJhbGciOi...",
    "refresh_token": "eyJhbGciOi...",
    "token_type": "bearer",
    "expires_in": 3600,
    "user": {
      "id": 3,
      "email": "sales@dealership.com",
      "full_name": "Vikram Malhotra",
      "phone": "9876500003",
      "role": {
        "id": 3,
        "name": "SALES_EXECUTIVE"
      },
      "is_active": true,
      "last_login_at": "2026-09-30T00:44:00Z",
      "created_at": "2026-09-30T00:28:00Z"
    }
  }
  ```
- **Errors:** `401 Unauthorized` (`UNAUTHORIZED` - Invalid email or password).

#### `POST /auth/register`
- **Auth:** None / Admin
- **Request Body:**
  ```json
  {
    "email": "john.doe@dealership.com",
    "full_name": "John Doe",
    "phone": "9876543210",
    "password": "SecurePassword123!",
    "role_name": "SALES_EXECUTIVE"
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "success": true,
    "data": { ... }
  }
  ```
- **Errors:** `409 Conflict` (`USER_ALREADY_EXISTS`).

#### `POST /auth/refresh`
- **Auth:** None
- **Request Body:**
  ```json
  {
    "refresh_token": "eyJhbGciOi..."
  }
  ```
- **Response (200 OK):** TokenResponse.

#### `GET /auth/me`
- **Auth:** Bearer Token
- **Response (200 OK):** Current User profile.

---

### 2. Customers (`/customers`)

#### `POST /customers`
- **Auth:** `SALES_EXECUTIVE`, `DEALER_OWNER`, `ADMIN`
- **Request Body:**
  ```json
  {
    "full_name": "Rahul Sharma",
    "phone": "9876543210",
    "email": "rahul.sharma@example.com",
    "address": "Flat 302, Palm Heights",
    "city": "Mumbai",
    "state": "Maharashtra",
    "pincode": "400001",
    "occupation": "Software Engineer",
    "preferred_language": "English",
    "budget": 120000.00
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "success": true,
    "data": {
      "id": 1,
      "full_name": "Rahul Sharma",
      "phone": "9876543210",
      "email": "rahul.sharma@example.com",
      "city": "Mumbai",
      "state": "Maharashtra",
      "pincode": "400001",
      "budget": 120000.00,
      "created_at": "2026-09-30T00:28:00Z",
      "updated_at": "2026-09-30T00:28:00Z"
    }
  }
  ```
- **Errors:** `409 Conflict` (`CUSTOMER_PHONE_EXISTS`), `422 Validation Error`.

#### `GET /customers`
- **Auth:** Any authenticated user
- **Query Params:**
  - `search`: string (Name, phone, or email)
  - `city`: string
  - `min_budget`: decimal
  - `max_budget`: decimal
  - `page`: int (default 1)
  - `page_size`: int (default 20, max 100)
- **Response (200 OK):** Paginated collection of customers.

#### `GET /customers/{id}`
- **Auth:** Any authenticated user
- **Response (200 OK):** Customer object.
- **Errors:** `404 Not Found` (`CUSTOMER_NOT_FOUND`).

#### `PUT /customers/{id}`
- **Auth:** `SALES_EXECUTIVE`, `DEALER_OWNER`, `ADMIN`
- **Request Body:** Partial update fields.

#### `DELETE /customers/{id}`
- **Auth:** `DEALER_OWNER`, `ADMIN`

---

### 3. Vehicles Catalog (`/vehicles`)

#### `GET /vehicles`
- **Auth:** None (Public / Authenticated)
- **Query Params:**
  - `brand_id`: int
  - `model_id`: int
  - `fuel_type`: string (`PETROL`, `ELECTRIC`, `HYBRID`)
  - `min_price`: decimal
  - `max_price`: decimal
  - `status`: string (default `ACTIVE`)
  - `search`: string
  - `page`: int, `page_size`: int
- **Response (200 OK):** Paginated vehicle variants with on-road pricing and specifications.

#### `GET /vehicles/{id}`
- **Response (200 OK):** Single vehicle variant details.

#### `POST /vehicles`
- **Auth:** `DEALER_OWNER`, `ADMIN`
- **Request Body:**
  ```json
  {
    "model_id": 1,
    "name": "Activa 125 DLX",
    "code": "ACTIVA-125-DLX",
    "engine_cc": 124,
    "fuel_type": "PETROL",
    "transmission": "AUTOMATIC",
    "ex_showroom_price": 89000.00,
    "insurance_price": 6500.00,
    "rto_price": 9200.00,
    "accessories_price": 2500.00,
    "color_options": "Pearl Nightstar Black, Rebel Red Metallic"
  }
  ```
- **Response (201 Created):** Vehicle variant with auto-computed `on_road_price`.

#### `GET /vehicles/catalog/brands` & `POST /vehicles/catalog/brands`
#### `GET /vehicles/catalog/models` & `POST /vehicles/catalog/models`
#### `GET /vehicles/catalog/accessories` & `POST /vehicles/catalog/accessories`

---

### 4. Inventory Control (`/inventory`)

#### `GET /inventory`
- **Auth:** Any authenticated user
- **Query Params:** `variant_id`, `status` (`AVAILABLE`, `RESERVED`, `SOLD`, `SERVICE`, `IN_TRANSIT`), `branch_name`, `search`, `page`, `page_size`.

#### `GET /inventory/summary/{variant_id}`
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "data": {
      "variant_id": 1,
      "variant_name": "Activa 125 DLX",
      "total_units": 3,
      "available_units": 2,
      "reserved_units": 1,
      "sold_units": 0,
      "service_units": 0,
      "in_transit_units": 0
    }
  }
  ```

#### `POST /inventory`
- **Auth:** `INVENTORY_MANAGER`, `ADMIN`
- **Request Body:** `variant_id`, `vin_chassis_number`, `engine_number`, `color`, `branch_name`, `arrival_date`.

#### `POST /inventory/{id}/reserve`
- **Auth:** Any authenticated user
- **Description:** Atomically locks row and reserves unit if status is `AVAILABLE`.
- **Errors:** `400 Bad Request` (`UNIT_NOT_AVAILABLE`).

#### `POST /inventory/{id}/release`
- **Auth:** Any authenticated user
- **Description:** Releases a reserved unit back to `AVAILABLE`.
- **Errors:** `400 Bad Request` (`UNIT_NOT_RESERVED`).

---

### 5. Offers & Promotions (`/offers`)

#### `GET /offers`
- **Query Params:** `active_only` (bool), `brand_id`, `model_id`, `variant_id`.

#### `POST /offers`
- **Auth:** `DEALER_OWNER`, `ADMIN`
- **Request Body:**
  ```json
  {
    "title": "Festive Flat Discount",
    "description": "Flat ₹5,000 off on festive booking",
    "discount_type": "FIXED",
    "discount_value": 5000.00,
    "valid_from": "2026-09-01",
    "valid_until": "2026-10-31",
    "min_order_value": 80000.00,
    "active": true
  }
  ```

---

### 6. Finance & Exact EMI Calculator (`/finance`)

#### `POST /finance/calculate-emi`
- **Auth:** None (Public / Authenticated)
- **Request Body:**
  ```json
  {
    "principal": 80000.00,
    "annual_interest_rate": 9.50,
    "tenure_months": 36
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "data": {
      "monthly_emi": 2562.64,
      "total_interest": 12255.04,
      "total_payable": 92255.04,
      "principal": 80000.00,
      "tenure_months": 36,
      "annual_interest_rate": 9.50
    }
  }
  ```
- **Validation:** `principal > 0`, `annual_interest_rate >= 0`, `tenure_months > 0`.

---

### 7. Quotation Engine (`/quotations`)

#### `POST /quotations`
- **Auth:** `SALES_EXECUTIVE`, `DEALER_OWNER`, `ADMIN`
- **Request Body:**
  ```json
  {
    "customer_id": 1,
    "variant_id": 1,
    "accessory_ids": [1, 2],
    "offer_id": 1,
    "down_payment": 25000.00,
    "loan_tenure": 36,
    "interest_rate": 9.50,
    "notes": "Customer interested in dark black shade"
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "success": true,
    "data": {
      "id": 1,
      "quotation_number": "QT-20260930-1001",
      "customer_id": 1,
      "sales_executive_id": 3,
      "variant_id": 1,
      "base_price": 89000.00,
      "insurance": 6500.00,
      "rto": 9200.00,
      "accessories": 2400.00,
      "discount": 5000.00,
      "final_price": 102100.00,
      "down_payment": 25000.00,
      "loan_amount": 77100.00,
      "interest_rate": 9.50,
      "loan_tenure": 36,
      "emi": 2469.74,
      "status": "GENERATED",
      "pdf_url": "/api/v1/quotations/1/pdf",
      "expires_at": "2026-10-15T00:00:00Z"
    }
  }
  ```

#### `GET /quotations/{id}/pdf`
- **Auth:** None (Direct document link)
- **Response:** PDF Binary stream (`application/pdf`) with `Content-Disposition: inline; filename="QT-...pdf"`.

#### `POST /quotations/{id}/send-whatsapp`
- **Auth:** `SALES_EXECUTIVE`, `DEALER_OWNER`, `ADMIN`
- **Description:** Sends quotation breakdown and document over WhatsApp using Meta Cloud API or development mock provider.
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "data": {
      "success": true,
      "quotation_number": "QT-20260930-1001",
      "delivery": {
        "provider": "MockDevelopmentProvider",
        "status": "MOCK_SENT",
        "message_id": "mock_wamid_...",
        "recipient": "9876543210"
      }
    }
  }
  ```

#### `PUT /quotations/{id}/status`
- **Request Body:** `{"status": "ACCEPTED" | "REJECTED" | "CANCELLED", "reason": "Customer confirmed booking"}`.

---

### 8. Lead Management (`/leads`)

#### `POST /leads`
- **Auth:** `SALES_EXECUTIVE`, `DEALER_OWNER`, `ADMIN`
- **Request Body:**
  ```json
  {
    "customer_id": 1,
    "source": "WALK_IN",
    "priority": "HIGH",
    "interested_variant_id": 1,
    "expected_purchase_date": "2026-10-15",
    "notes": "Visited showroom looking for festive finance scheme"
  }
  ```

#### `POST /leads/{id}/status`
- **Auth:** `SALES_EXECUTIVE`, `DEALER_OWNER`, `ADMIN`
- **Description:** Transitions lead according to the validated sales pipeline state machine.
- **Valid Transitions:**
  - `NEW` → `CONTACTED`, `LOST`
  - `CONTACTED` → `INTERESTED`, `LOST`
  - `INTERESTED` → `TEST_RIDE`, `NEGOTIATION`, `LOST`
  - `TEST_RIDE` → `NEGOTIATION`, `LOST`
  - `NEGOTIATION` → `BOOKED`, `LOST`
  - `BOOKED` → `PURCHASED`, `LOST`
  - `PURCHASED` → (Terminal)
  - `LOST` → `NEW`, `CONTACTED` (Re-opened)
- **Errors:** `400 Bad Request` (`INVALID_STATE_TRANSITION`).

---

### 9. Follow-up Tasks (`/followups`)

#### `POST /followups`
- **Request Body:** `{"lead_id": 1, "scheduled_at": "2026-10-02T10:00:00Z", "type": "CALL", "notes": "Call regarding quotation"}`.
- **Behavior:** Updates parent lead's `next_followup_date`.

#### `POST /followups/{id}/complete`
- **Request Body:** `{"notes": "Customer visited and test ride booked."}`.
- **Behavior:** Marks status as `COMPLETED` and records timestamp.

---

### 10. Test Ride Booking (`/test-rides`)

#### `POST /test-rides`
- **Request Body:**
  ```json
  {
    "customer_id": 1,
    "variant_id": 1,
    "scheduled_at": "2026-10-05T14:30:00Z",
    "duration_minutes": 30,
    "driving_license_number": "MH-01-2022-0012345"
  }
  ```
- **Collision Protection:** Automatically checks for double-booking on the same vehicle during the window.
- **Errors:** `409 Conflict` (`TEST_RIDE_SLOT_COLLISION`).

#### `POST /test-rides/{id}/complete`
- **Request Body:** `{"feedback_rating": 5, "notes": "Very smooth ride and pickup."}`.

---

### 11. Dashboard & Reports (`/dashboard`, `/reports`)

#### `GET /dashboard/summary`
- **Auth:** `DEALER_OWNER`, `ADMIN`
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "data": {
      "total_customers": 12,
      "total_leads": 10,
      "quotations_generated": 8,
      "quotations_accepted": 5,
      "active_bookings": 3,
      "completed_purchases": 2,
      "total_revenue": 524000.00,
      "pending_followups": 4,
      "available_inventory": 7,
      "reserved_inventory": 1,
      "total_test_rides": 6,
      "lead_conversion_rate_percentage": 20.00
    }
  }
  ```

#### `GET /dashboard/sales`, `/leads`, `/inventory`, `/revenue`
#### `GET /reports/sales`, `/leads`, `/inventory`, `/quotations` (supports `from_date`, `to_date`, `limit`).

---

### 12. AI Features (`/ai`)

#### `POST /ai/recommend-vehicle`
- **Request Body:**
  ```json
  {
    "budget": 120000.00,
    "preferred_brand": "Honda",
    "usage": "COMMUTE",
    "preferred_fuel_type": "PETROL",
    "preferences": "High fuel economy, comfortable seat"
  }
  ```
- **Response (200 OK):** Top scored vehicle variants with explanation, estimated EMI, and fit score.
- **Resilience:** Uses Google Gemini if configured, otherwise automatically falls back to the deterministic heuristic recommendation engine.

#### `POST /ai/lead-insights/{lead_id}`
- **Response (200 OK):** Structured insights including `conversion_probability`, `recommended_followup_timing`, `suggested_next_action`, and vehicle suggestions.
