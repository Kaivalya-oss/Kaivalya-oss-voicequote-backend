"""Black-box checks of a running image in multi-tenant mode.

Phases, with the caller stopping and starting a showroom database between them:
  BASE=... CA_FILE=... APP_PASSWORD=... python verify_multitenant.py setup   > state.json
  STATE='<state json>' ... python verify_multitenant.py down
  STATE='<state json>' ... python verify_multitenant.py up
  STATE='<state json>' WHICH=a|b EXPECT=<status> LIMIT=<seconds> python verify_multitenant.py probe
  STATE='<state json>' WHICH=a|b HEALTHY=true|false CODE=<code> python verify_multitenant.py health
  STATE='<state json>' WHICH=a|b python verify_multitenant.py observe    (prints what staff get, checks nothing)
  STATE='<state json>' WHICH=a|b python verify_multitenant.py migrate
  STATE='<state json>' python verify_multitenant.py access    (Head of Sales, takeover, suspension, deactivation)
"""
import base64
import hashlib
import hmac
import json
import os
import re
import struct
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


def totp(secret, step):
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def admin_sign_in():
    """First sign-in for the Platform Admin, through password and MFA enrolment."""
    A_ = f"{os.environ['ADMIN_BASE']}/api/v1/admin"
    login = c.post(f"{A_}/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}).json()["data"]
    start = c.post(f"{A_}/auth/mfa/enroll/start", json={"mfa_token": login["mfa_token"]}).json()["data"]
    done = c.post(f"{A_}/auth/mfa/enroll/confirm", json={"mfa_token": login["mfa_token"], "code": totp(start["secret"], int(time.time() // 30))})
    c.cookies.clear()
    return done.json()["data"]["access_token"]


def onboard(tag, admin_token):
    """The real path: the Platform Admin creates the showroom, and the Owner accepts the emailed invitation."""
    email = f"owner-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    r = c.post(f"{os.environ['ADMIN_BASE']}/api/v1/admin/showrooms", headers=H(admin_token), json={
        "showroom_name": f"Verify {tag} {uuid.uuid4().hex[:6]}", "legal_business_name": f"Verify {tag} Pvt Ltd", "owner_email": email, "owner_full_name": f"Owner {tag}"})
    check(f"Platform Admin creates showroom {tag}", r.status_code == 201 and r.json()["data"]["invitation_delivery"] == "SENT", r.text)
    mail = c.get(f"{os.environ['SINK_BASE']}/message", params={"to": email})
    token = re.search(r"#token=([\w=\-]+)", mail.text.replace("=\n", ""))[1]
    check(f"invitation token isn't in the admin response {tag}", token not in r.text)
    accepted = c.post(f"{P}/invitations/accept", json={"token": token, "full_name": f"Owner {tag}", "password": "VerifyOwner123!"})
    check(f"Owner accepts invitation {tag}", accepted.status_code == 200, accepted.text)
    check(f"invitation can't be reused {tag}", c.post(f"{P}/invitations/accept", json={"token": token, "full_name": "Again", "password": "VerifyOwner123!"}).status_code == 400)
    return accepted, r.json()["data"]["id"], email


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

    public = {"showroom_name": "Claimed", "email": "claimer@example.com", "password": "Claimer-Password-1", "full_name": "Claimer"}
    check("public showroom registration is gone", c.post(f"{P}/showrooms/register", json=public).status_code == 404)
    check("creating a showroom from the showroom API is gone", c.post(f"{P}/showrooms", json=public).status_code == 404)
    admin_token = admin_sign_in()
    ra, showroom_a, email_a = onboard("a", admin_token)
    rb, showroom_b, _ = onboard("b", admin_token)
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

    # Team, for the access checks that follow.
    def join(role):
        email = f"{role.lower()}-{uuid.uuid4().hex[:6]}@example.com"
        invite = c.post(f"{P}/tenants/current/invitations", headers=H(ta), json={"email": email, "role": role}).json()["data"]
        res = c.post(f"{P}/invitations/accept", json={"token": invite["invitation_token"], "full_name": f"Verify {role}", "password": "VerifyStaff123!"}).json()["data"]
        return {"email": email, "token": res["access_token"], "refresh": res["refresh_token"], "membership": res["active_tenant"]["membership_id"]}

    head, sales = join("HEAD_OF_SALES"), join("SALESPERSON")
    print(json.dumps({"ta": ta, "tb": tb, "admin": admin_token, "showroom_a": showroom_a, "head": head, "sales": sales, "quote": q.json()["data"]["id"], "email_a": email_a}))
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
    check("fails within the connect timeout", time.time() - t0 < 8, time.time() - t0)
    check("other showroom unaffected", "Only In B" in c.get(f"{A}/customers", headers=H(tb)).text)
    h = c.post(f"{DB}/health", headers=H(ta))
    check("health check reports the outage in a normal response", h.status_code == 200 and h.json()["data"]["healthy"] is False, h.text)
    again = c.get(f"{A}/customers", headers=H(ta))
    check("repeat request during the outage is also a 503", again.status_code == 503, f"{again.status_code} {again.text}")
    finish()

if phase == "access":
    admin_api = f"{os.environ['ADMIN_BASE']}/api/v1/admin"
    head, sales, admin = state["head"], state["sales"], state["admin"]
    FIN = re.compile("revenue|profit|margin|pipeline|average_", re.IGNORECASE)

    # Head of Sales
    check("Head of Sales: revenue dashboard refused", c.get(f"{A}/dashboard/revenue", headers=H(head["token"])).status_code == 403)
    check("Head of Sales: sales report refused", c.get(f"{A}/reports/sales", headers=H(head["token"])).status_code == 403)
    r = c.get(f"{A}/dashboard/summary?include_financials=true", headers={**H(head["token"]), "X-Role": "OWNER"})
    check("Head of Sales: summary has team figures but no revenue, even when asked", r.status_code == 200 and "total_customers" in r.text and not FIN.search(r.text), r.text)
    r = c.get(f"{A}/dashboard/sales", headers=H(head["token"]))
    check("Head of Sales: team analytics work, with no money", r.status_code == 200 and not FIN.search(r.text), r.text[:200])
    r = c.get(f"{A}/reports/daily-export?format=csv", headers=H(head["token"]))
    check("Head of Sales: export has no value total", r.status_code == 200 and "quotation value" not in r.text)
    check("Owner: export has the value total", "quotation value" in c.get(f"{A}/reports/daily-export?format=csv", headers=H(ta)).text)
    check("Owner: revenue dashboard works", c.get(f"{A}/dashboard/revenue", headers=H(ta)).status_code == 200)
    r = c.get(f"{A}/quotations/{state['quote']}", headers=H(head["token"]))
    check("Head of Sales: individual quotation is readable", r.status_code == 200, r.text[:160])
    check("Salesperson: financials refused", c.get(f"{A}/dashboard/revenue", headers=H(sales["token"])).status_code == 403)
    check("Salesperson: another person's quotation is hidden", c.get(f"{A}/quotations/{state['quote']}", headers=H(sales["token"])).status_code == 404)

    # Admin can't take over a completed showroom
    r = c.post(f"{admin_api}/showrooms/{state['showroom_a']}/owner-invitation", headers=H(admin), json={"owner_email": "attacker@example.com", "owner_full_name": "Attacker"})
    check("Platform Admin can't invite an Owner into a completed showroom", r.status_code == 409 and r.json()["error"]["code"] == "OWNER_RECOVERY_REQUIRED", r.text)
    check("showroom token refused on admin showroom routes", c.get(f"{admin_api}/showrooms/{state['showroom_a']}", headers=H(ta)).status_code == 401)
    r = c.get(f"{admin_api}/showrooms/{state['showroom_a']}", headers=H(admin))
    check("admin view holds no credentials or business data", r.status_code == 200 and not re.search("secret|password|Only In A|saas-tenant", r.text), r.text[:200])

    # Suspension
    r = c.post(f"{admin_api}/showrooms/{state['showroom_a']}/suspend", headers=H(admin), json={"reason": "Verification test"})
    check("Platform Admin suspends the showroom", r.status_code == 200, r.text)
    for label, token in (("Owner", ta), ("Head of Sales", head["token"]), ("Salesperson", sales["token"])):
        r = c.get(f"{A}/customers", headers=H(token))
        check(f"suspended: {label}'s existing session is blocked", r.status_code == 403 and r.json()["error"]["code"] == "TENANT_SUSPENDED", r.text[:120])
    r = c.post(f"{P}/tenants/current/invitations", headers=H(ta), json={"email": "x@example.com", "role": "SALESPERSON"})
    check("suspended: Owner can't invite", r.status_code == 403)
    check("suspended: Owner can't change the database", c.post(f"{DB}/test", headers=H(ta), json=conn_body("saas-tenant-a", "showroom_a")).status_code == 403)
    r = c.get(f"{P}/tenants/current/setup-status", headers=H(ta))
    check("suspended: Owner can see the status, not the reason", r.status_code == 200 and r.json()["data"]["next_step"] == "CONTACT_SUPPORT" and "Verification test" not in r.text)
    check("suspended: another showroom keeps working", c.get(f"{A}/customers", headers=H(tb)).status_code == 200)
    r = c.post(f"{admin_api}/showrooms/{state['showroom_a']}/reactivate", headers=H(admin), json={"reason": "Verification finished"})
    check("reactivation restores the showroom", r.status_code == 200 and r.json()["data"]["status"] == "ACTIVE", r.text)
    check("reactivated: data is intact", "Only In A" in c.get(f"{A}/customers", headers=H(ta)).text)

    # Deactivation
    r = c.post(f"{P}/tenants/current/members/{sales['membership']}/deactivate", headers=H(ta))
    check("Owner deactivates the salesperson", r.status_code == 200, r.text)
    check("deactivated: existing session refused", c.get(f"{A}/customers", headers=H(sales["token"])).status_code == 401)
    check("deactivated: refresh refused", c.post(f"{P}/auth/refresh", json={"refresh_token": sales["refresh"]}).status_code == 401)
    check("deactivated: sign-in refused", c.post(f"{P}/auth/login", json={"email": sales["email"], "password": "VerifyStaff123!"}).status_code == 401)
    finish()

if phase == "probe":
    token = ta if os.environ["WHICH"] == "a" else tb
    expect, limit = int(os.environ["EXPECT"]), float(os.environ["LIMIT"])
    for attempt in range(3):
        t0 = time.time()
        r = c.get(f"{A}/customers", headers=H(token))
        took = time.time() - t0
        print(f"INFO  showroom {os.environ['WHICH']} request {attempt + 1}: {r.status_code} in {took:.2f}s", file=log)
        check(f"status {expect} within {limit}s", r.status_code == expect and took < limit, f"{r.status_code} {took:.2f}s {r.text[:100]}")
        if expect == 503:
            want = os.environ.get("CODE", "TENANT_DB_UNAVAILABLE")
            check(f"error is {want}", r.json()["error"]["code"] == want, r.text[:100])
    finish()

if phase == "health":
    token = ta if os.environ["WHICH"] == "a" else tb
    t0 = time.time()
    r = c.post(f"{DB}/health", headers=H(token))
    print(f"INFO  health {r.status_code} in {time.time() - t0:.2f}s: {r.text[:160]}", file=log)
    check("health responds normally", r.status_code == 200, r.text)
    data = r.json()["data"]
    check(f"healthy is {os.environ['HEALTHY']}", str(data["healthy"]).lower() == os.environ["HEALTHY"], data)
    if os.environ.get("CODE"):
        check(f"error code {os.environ['CODE']}", data["error_code"] == os.environ["CODE"], data)
    finish()

if phase == "observe":
    token = ta if os.environ["WHICH"] == "a" else tb
    r = c.get(f"{A}/customers", headers=H(token))
    print(f"INFO  customer list on older schema: {r.status_code} {r.text[:120]}", file=log)
    v = c.get(f"{A}/vehicles", headers=H(token))
    print(f"INFO  vehicle list on older schema: {v.status_code}", file=log)
    q = c.get(f"{A}/quotations", headers=H(token))
    print(f"INFO  quotation list on older schema: {q.status_code} {q.text[:120]}", file=log)
    u = c.get(f"{P}/me", headers=H(token))
    print(f"INFO  account details on older schema: {u.status_code}", file=log)
    sys.exit(0)

if phase == "migrate":
    token = ta if os.environ["WHICH"] == "a" else tb
    r = c.post(f"{DB}/migrate", headers=H(token))
    print(f"INFO  migrate: {r.status_code}", file=log)
    check("migrate succeeded", r.status_code == 200, r.text)
    check("schema reported at head", r.json()["data"]["schema_version"] == "b2d5f8a1c604", r.text)
    check("response holds no credentials", PASSWORD not in r.text)
    finish()

if phase == "up":
    # The caller restarted Showroom A's database. tmpfs data is gone, so it comes back empty.
    r = c.get(f"{BASE}/health")
    check("application stayed up through the outage", r.status_code == 200)
    r = c.get(f"{A}/customers", headers=H(tb))
    check("other showroom still serving", r.status_code == 200)
    finish()
