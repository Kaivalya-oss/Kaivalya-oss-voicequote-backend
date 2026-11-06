"""Black-box checks of a running image in multi-tenant mode.

Phases, with the caller stopping and starting a showroom database between them:
  BASE=... CA_FILE=... APP_PASSWORD=... python verify_multitenant.py setup   > state.json
  STATE='<state json>' ... python verify_multitenant.py down
  STATE='<state json>' ... python verify_multitenant.py up
"""
import json
import os
import sys
import time
import uuid

import httpx

BASE = os.environ["BASE"]
P = f"{BASE}/api/v1/platform"
A = f"{BASE}/api/v1"
DB = f"{P}/tenants/current/database"
PASSWORD = os.environ.get("APP_PASSWORD", "")
results = []
log = sys.stderr


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:200] if detail and not ok else ''}", file=log)


c = httpx.Client(timeout=120)


def H(t):
    return {"Authorization": f"Bearer {t}"}


def register(tag):
    r = c.post(f"{P}/showrooms/register", json={
        "showroom_name": f"Verify {tag} {uuid.uuid4().hex[:6]}", "email": f"owner-{tag}-{uuid.uuid4().hex[:6]}@example.com",
        "password": "VerifyOwner123!", "full_name": f"Owner {tag}"})
    return r


def conn_body(host, database, **kw):
    return {"provider": "OTHER", "host": host, "port": 5432, "database": database, "username": "vq_app",
            "password": PASSWORD, "ca_certificate": open(os.environ["CA_FILE"]).read(), **kw}


def finish():
    print(f"{sum(results)}/{len(results)} checks passed", file=log)
    if not all(results):
        sys.exit(1)


phase = sys.argv[1]
if phase == "setup":
    check("health endpoint", c.get(f"{BASE}/health").json().get("status") == "healthy")
    check("control-plane connectivity", c.get(f"{BASE}/health/db").json().get("database") == "connected")
    check("single-showroom login not served", c.post(f"{A}/auth/login", json={"email": "a@example.com", "password": "x"}).status_code == 404)

    ra, rb = register("a"), register("b")
    check("tenant registration", ra.status_code == 201 and rb.status_code == 201, ra.text)
    a, b = ra.json()["data"], rb.json()["data"]
    check("separate tenant IDs", a["active_tenant"]["tenant_id"] != b["active_tenant"]["tenant_id"])
    ta, tb = a["access_token"], b["access_token"]
    me = c.get(f"{P}/me", headers=H(ta)).json()["data"]
    check("membership resolution", me["active_role"] == "OWNER" and len(me["memberships"]) == 1)
    denied = c.post(f"{P}/tenants/select", headers=H(ta), json={"tenant_id": b["active_tenant"]["tenant_id"]})
    check("cannot select another showroom", denied.status_code == 403)
    blocked = c.get(f"{A}/customers", headers=H(ta))
    check("no business access before database setup", blocked.status_code == 403 and blocked.json()["error"]["code"] == "TENANT_NOT_OPERATIONAL")

    bad = c.post(f"{DB}/test", headers=H(ta), json=conn_body("saas-tenant-a", "showroom_a", password="wrong-password-xyz"))
    check("wrong database password refused", bad.status_code == 422 and bad.json()["error"]["code"] == "TENANT_DB_AUTH_FAILED", bad.text)
    check("error does not echo credentials", "wrong-password-xyz" not in bad.text and "vq_app" not in bad.text)
    plain = c.post(f"{DB}/test", headers=H(ta), json=conn_body("postgres", "vqv_single", username="postgres", password="vqv-test-only"))
    check("server without TLS refused", plain.json()["error"]["code"] == "TENANT_DB_TLS_FAILED", plain.text)

    for label, token, host, name in (("A", ta, "saas-tenant-a", "showroom_a"), ("B", tb, "saas-tenant-b", "showroom_b")):
        r = c.post(f"{DB}/connect", headers=H(token), json=conn_body(host, name))
        check(f"tenant {label} database provisioned (migrations ran)", r.status_code == 200 and r.json()["data"]["connection_status"] == "ACTIVE", r.text)
        check(f"tenant {label} response holds no credentials", PASSWORD not in r.text and "secret_reference" not in r.text)
        h = c.post(f"{DB}/health", headers=H(token)).json()["data"]
        check(f"tenant {label} health check", h["healthy"] is True, h)

    r = c.post(f"{A}/customers", headers=H(ta), json={"full_name": "Only In A", "phone": "9000000001", "address": "A Road"})
    check("tenant connection resolves, customer created in A", r.status_code == 201, r.text)
    r = c.post(f"{A}/customers", headers=H(tb), json={"full_name": "Only In B", "phone": "9000000001", "address": "B Road"})
    check("same phone allowed in B: separate databases", r.status_code == 201, r.text)
    la = c.get(f"{A}/customers", headers=H(ta)).text
    lb = c.get(f"{A}/customers", headers=H(tb)).text
    check("routing isolates the two showrooms", "Only In A" in la and "Only In B" not in la and "Only In B" in lb and "Only In A" not in lb)
    spoof = c.get(f"{A}/customers", headers={**H(ta), "X-Tenant-ID": b["active_tenant"]["tenant_id"]}).text
    check("tenant header cannot redirect the request", "Only In B" not in spoof)

    brand = c.post(f"{A}/vehicles/catalog/brands", headers=H(ta), json={"name": "Verify Brand"}).json()["data"]
    model = c.post(f"{A}/vehicles/catalog/models", headers=H(ta), json={"brand_id": brand["id"], "name": "Verify Model"}).json()["data"]
    vs = [c.post(f"{A}/vehicles", headers=H(ta), json={"model_id": model["id"], "name": n, "code": n, "ex_showroom_price": p, "insurance_price": 1000, "rto_price": 1000}).json()["data"] for n, p in (("VV-One", 90000), ("VV-Two", 95000))]
    cust = c.get(f"{A}/customers", headers=H(ta)).json()["items"][0]
    q = c.post(f"{A}/quotations", headers=H(ta), json={"customer_id": cust["id"], "variant_id": vs[0]["id"], "compare_variant_id": vs[1]["id"], "payment_mode": "FINANCE"})
    check("comparison quotation in tenant database", q.status_code == 201 and q.json()["data"]["is_comparison"], q.text)
    pdf = c.get(f"{A}/quotations/{q.json()['data']['id']}/pdf", headers=H(ta))
    check("PDF rendered on demand", pdf.status_code == 200 and pdf.content[:4] == b"%PDF")

    results_many = [c.get(f"{A}/customers", headers=H(ta)).status_code for _ in range(25)]
    check("repeated requests through the tenant pool", set(results_many) == {200})

    print(json.dumps({"ta": ta, "tb": tb}))
    finish()
    sys.exit(0)

state = json.loads(os.environ["STATE"])
ta, tb = state["ta"], state["tb"]

if phase == "down":
    # Showroom A's database has been stopped by the caller.
    t0 = time.time()
    r = c.get(f"{A}/customers", headers=H(ta))
    check("unavailable tenant database gives a clear 503", r.status_code == 503 and r.json()["error"]["code"] == "TENANT_DB_UNAVAILABLE", r.text)
    check("unavailable error leaks nothing", not any(s in r.text for s in (PASSWORD, "vq_app", "saas-tenant-a", "asyncpg", "postgresql")))
    check("fails within the connect timeout", time.time() - t0 < 30, time.time() - t0)
    check("other showroom unaffected", "Only In B" in c.get(f"{A}/customers", headers=H(tb)).text)
    h = c.post(f"{DB}/health", headers=H(ta))
    check("health check reports the outage in a normal response", h.status_code == 200 and h.json()["data"]["healthy"] is False, h.text)
    again = c.get(f"{A}/customers", headers=H(ta))
    check("repeat request during the outage is also a 503", again.status_code == 503, f"{again.status_code} {again.text}")
    finish()

if phase == "up":
    # The caller restarted Showroom A's database. tmpfs data is gone, so it comes back empty.
    r = c.get(f"{BASE}/health")
    check("application stayed up through the outage", r.status_code == 200)
    r = c.get(f"{A}/customers", headers=H(tb))
    check("other showroom still serving", r.status_code == 200)
    finish()
