"""Black-box checks of a running image in single-showroom mode, on a database with no seeded data.

Usage: BASE=http://host:8000 ADMIN_EMAIL=... ADMIN_PASSWORD=... python verify_single.py
The administrator comes from scripts/create_admin.py, as in a real deployment.
"""
import os
import sys
import time
import uuid

import httpx

BASE = os.environ["BASE"]
ADMIN_EMAIL = os.environ["ADMIN_EMAIL"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
STAFF_PASSWORD = f"Verify-{uuid.uuid4().hex}"
A = f"{BASE}/api/v1"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:160] if detail and not ok else ''}")


c = httpx.Client(timeout=30)


def login(email, password):
    r = c.post(f"{A}/auth/login", json={"email": email, "password": password})
    return r


def H(t):
    return {"Authorization": f"Bearer {t}"}


check("health endpoint", c.get(f"{BASE}/health").json().get("status") == "healthy")
db = c.get(f"{BASE}/health/db").json()
check("database connectivity", db.get("database") == "connected", db)
env = c.get(f"{BASE}/health").json().get("environment")
print(f"INFO  reported environment: {env}")

# Authentication
for email, password in (
    ("admin@dealership.com", "AdminPass123!"),
    ("owner@dealership.com", "OwnerPass123!"),
    ("sales@dealership.com", "SalesPass123!"),
    ("finance@dealership.com", "FinancePass123!"),
    ("inventory@dealership.com", "InventoryPass123!"),
):
    check(f"default account refused: {email}", login(email, password).status_code == 401)
check("wrong password rejected", login(ADMIN_EMAIL, "wrong-password-value").status_code == 401)
check("no token rejected", c.get(f"{A}/customers").status_code == 401)
check("tampered token rejected", c.get(f"{A}/customers", headers=H("abc.def.ghi")).status_code == 401)
r = login(ADMIN_EMAIL, ADMIN_PASSWORD)
check("login", r.status_code == 200, r.text)
admin = r.json()["access_token"]
refresh = c.post(f"{A}/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})
check("token refresh", refresh.status_code == 200, refresh.text)


def add_staff(role):
    """Register, approve and assign a role, as a manager does."""
    email = f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    r = c.post(f"{A}/auth/register", json={"email": email, "full_name": f"Verify {role}", "password": STAFF_PASSWORD})
    check(f"staff registration ({role})", r.status_code in (200, 201), r.text)
    check(f"new account waits for approval ({role})", login(email, STAFF_PASSWORD).status_code == 401)
    user_id = r.json()["data"]["id"]
    check(f"staff approval ({role})", c.post(f"{A}/users/{user_id}/activate", headers=H(admin)).status_code == 200)
    if role != "SALES_EXECUTIVE":
        check(f"role assignment ({role})", c.put(f"{A}/users/{user_id}/role", headers=H(admin), json={"role_name": role}).status_code == 200)
    return login(email, STAFF_PASSWORD).json()["access_token"]


sales = add_staff("SALES_EXECUTIVE")
finance = add_staff("FINANCE_EXECUTIVE")

# Permissions
phone = "9" + str(int(time.time() * 1000))[-9:]
body = {"full_name": "Verify Customer", "phone": phone, "email": "verify@example.com", "city": "Pune", "address": "1 Test Road"}
check("role check: Finance cannot create a customer", c.post(f"{A}/customers", headers=H(finance), json=body).status_code == 403)
check("role check: Sales cannot read sales report", c.get(f"{A}/reports/sales", headers=H(sales)).status_code == 403)
check("role check: Admin can read sales report", c.get(f"{A}/reports/sales", headers=H(admin)).status_code == 200)

# Customers
r = c.post(f"{A}/customers", headers=H(sales), json=body)
check("customer creation", r.status_code == 201, r.text)
cust = r.json()["data"]
check("customer retrieval", c.get(f"{A}/customers/{cust['id']}", headers=H(sales)).json()["data"]["phone"] == phone)
check("customer list", any(x["id"] == cust["id"] for x in c.get(f"{A}/customers?page_size=100", headers=H(sales)).json()["items"]))
check("duplicate phone refused", c.post(f"{A}/customers", headers=H(sales), json=body).status_code == 409)

# Quotations
check("database starts with no vehicles or customers from a seed", c.get(f"{A}/vehicles", headers=H(sales)).json()["items"] == [])
brand = c.post(f"{A}/vehicles/catalog/brands", headers=H(admin), json={"name": "Verify Brand"})
check("catalogue: brand", brand.status_code == 201, brand.text)
model = c.post(f"{A}/vehicles/catalog/models", headers=H(admin), json={"brand_id": brand.json()["data"]["id"], "name": "Verify Model"})
check("catalogue: model", model.status_code == 201, model.text)
for name, price in (("VV-One", 90000), ("VV-Two", 95000)):
    v = c.post(f"{A}/vehicles", headers=H(admin), json={"model_id": model.json()["data"]["id"], "name": name, "code": name, "ex_showroom_price": price, "insurance_price": 1000, "rto_price": 1000})
    check(f"catalogue: vehicle {name}", v.status_code == 201, v.text)
variants = c.get(f"{A}/vehicles?page_size=2", headers=H(sales)).json()["items"]
check("vehicle catalogue readable by sales", len(variants) == 2)
FINANCE_KEYS = {"emi", "down_payment", "loan_amount", "loan_tenure", "interest_rate"}


def quote(**extra):
    return c.post(f"{A}/quotations", headers=H(sales), json={"customer_id": cust["id"], "variant_id": variants[0]["id"], **extra})


r = quote(payment_mode="CASH")
check("quotation creation (CASH)", r.status_code == 201 and r.json()["data"]["payment_mode"] == "CASH", r.text)
cash = r.json()["data"]
r = quote(payment_mode="FINANCE")
check("quotation creation (FINANCE)", r.status_code == 201 and r.json()["data"]["payment_mode"] == "FINANCE", r.text)
check("invalid payment mode refused", quote(payment_mode="EMI").status_code == 422)
check("no EMI or loan fields in responses", not FINANCE_KEYS & cash.keys())
r = quote(payment_mode="FINANCE", compare_variant_id=variants[1]["id"])
check("comparison quotation", r.status_code == 201, r.text)
comp = r.json()["data"]
options = [i for i in comp["items"] if i["item_type"] == "VEHICLE_OPTION"]
check("comparison: one quotation, two options", comp["is_comparison"] and len(options) == 2)
check("comparison: typed price columns stored", all(o.get("total_price") for o in options))

# History and details
hist = c.get(f"{A}/quotations?customer_id={cust['id']}", headers=H(sales)).json()
check("quotation history", hist["total"] == 3 and {q["payment_mode"] for q in hist["items"]} == {"CASH", "FINANCE"})
detail = c.get(f"{A}/quotations/{comp['id']}", headers=H(sales)).json()["data"]
check("quotation details", detail["quotation_number"] == comp["quotation_number"] and detail["is_comparison"])

# PDF
for name, q in (("single", cash), ("comparison", comp)):
    p = c.get(f"{A}/quotations/{q['id']}/pdf", headers=H(sales))
    check(f"PDF generation ({name})", p.status_code == 200 and p.content[:4] == b"%PDF" and len(p.content) > 1000, p.status_code)

# Required integrations, with no credentials configured
wa = c.post(f"{A}/quotations/{cash['id']}/send-whatsapp", headers=H(sales)).json()["data"]
check("WhatsApp: simulated, not claimed as delivered", wa["simulated"] is True and wa["delivered"] is False)
em = c.post(f"{A}/quotations/{cash['id']}/send-email", headers=H(sales), json={}).json()["data"]
check("Email: simulated, not claimed as delivered", em["simulated"] is True and em["delivered"] is False, em)
check("Quotation stays Generated after simulated sends", c.get(f"{A}/quotations/{cash['id']}", headers=H(sales)).json()["data"]["status"] == "GENERATED")

# Other workflow areas
lead = c.post(f"{A}/leads", headers=H(sales), json={"customer_id": cust["id"], "interested_variant_id": variants[0]["id"]})
check("lead creation", lead.status_code == 201, lead.text)
for path, token in (("/dashboard/summary", admin), ("/dashboard/revenue", admin), ("/followups", sales), ("/test-rides", sales), ("/inventory", sales)):
    check(f"GET {path}", c.get(f"{A}{path}", headers=H(token)).status_code == 200)
x = c.get(f"{A}/reports/daily-export?format=xlsx", headers=H(admin))
check("daily Excel export", x.status_code == 200 and x.content[:2] == b"PK", x.status_code)
check("multi-tenant routes absent in this mode", c.get(f"{A}/platform/me").status_code == 404)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
