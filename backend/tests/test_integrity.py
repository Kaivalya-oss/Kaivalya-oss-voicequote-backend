"""Regression tests for: account approval, scoped reads, follow-up integrity, quotation input
validation, customer deletion and the quotation status state machine / expiry."""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.quotation import Quotation
from app.services.quotation_service import expire_overdue_quotations


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def unique_phone(prefix: str = "8") -> str:
    return prefix + str(time.time_ns())[-9:]


def future(days: float = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


async def variant_id(client: AsyncClient, token: str) -> int:
    return (await client.get("/api/v1/vehicles?page_size=1", headers=H(token))).json()["items"][0]["id"]


async def login(client: AsyncClient, email: str, password: str):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def make_salesperson(client: AsyncClient, admin_token: str) -> tuple[str, int]:
    email = f"sp_{uuid.uuid4().hex[:8]}@dealer-mail.com"
    reg = await client.post("/api/v1/auth/register", json={"email": email, "password": "SalesPass123!", "full_name": "Scoped Sales"})
    uid = reg.json()["data"]["id"]
    await client.post(f"/api/v1/users/{uid}/activate", headers=H(admin_token))
    return (await login(client, email, "SalesPass123!")).json()["access_token"], uid


async def customer_lead_quote(client: AsyncClient, token: str, vid: int):
    c = (await client.post("/api/v1/customers", headers=H(token),
                           json={"full_name": "Integrity Test", "phone": unique_phone()})).json()["data"]
    lead = (await client.post("/api/v1/leads", headers=H(token),
                              json={"customer_id": c["id"], "interested_variant_id": vid})).json()["data"]
    q = (await client.post("/api/v1/quotations", headers=H(token),
                           json={"customer_id": c["id"], "variant_id": vid})).json()["data"]
    return c, lead, q


# ---------------------------------------------------------------- approval
@pytest.mark.asyncio
async def test_new_accounts_need_manager_approval(client: AsyncClient, admin_token: str, sales_token: str, owner_token: str):
    email = f"pending_{uuid.uuid4().hex[:8]}@dealer-mail.com"
    reg = await client.post("/api/v1/auth/register", json={"email": email, "password": "Pending123!", "full_name": "Pending User"})
    assert reg.status_code == 201 and reg.json()["data"]["is_active"] is False
    uid = reg.json()["data"]["id"]

    denied = await login(client, email, "Pending123!")
    assert denied.status_code == 401
    assert "awaiting approval" in denied.json()["error"]["message"]
    # Wrong password still gives the generic message (no account-state disclosure)
    assert "Invalid email or password" in (await login(client, email, "WrongPass1!")).json()["error"]["message"]

    # Salespeople cannot see or approve accounts
    assert (await client.get("/api/v1/users", headers=H(sales_token))).status_code == 403
    assert (await client.post(f"/api/v1/users/{uid}/activate", headers=H(sales_token))).status_code == 403

    pending = (await client.get("/api/v1/users?is_active=false&page_size=100", headers=H(owner_token))).json()["items"]
    assert any(u["id"] == uid for u in pending)
    assert (await client.post(f"/api/v1/users/{uid}/activate", headers=H(owner_token))).status_code == 200
    ok = await login(client, email, "Pending123!")
    assert ok.status_code == 200
    token = ok.json()["access_token"]

    # Deactivation takes effect immediately, even for an already-issued token
    assert (await client.post(f"/api/v1/users/{uid}/deactivate", headers=H(owner_token))).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=H(token))).status_code == 401
    assert "deactivated" in (await login(client, email, "Pending123!")).json()["error"]["message"]

    # Role changes: ADMIN only, never your own
    assert (await client.put(f"/api/v1/users/{uid}/role", headers=H(owner_token), json={"role_name": "ADMIN"})).status_code == 403
    r = await client.put(f"/api/v1/users/{uid}/role", headers=H(admin_token), json={"role_name": "FINANCE_EXECUTIVE"})
    assert r.status_code == 200 and r.json()["data"]["role"]["name"] == "FINANCE_EXECUTIVE"
    me = (await client.get("/api/v1/auth/me", headers=H(admin_token))).json()["data"]
    assert (await client.put(f"/api/v1/users/{me['id']}/role", headers=H(admin_token), json={"role_name": "SALES_EXECUTIVE"})).status_code == 400
    assert (await client.post(f"/api/v1/users/{me['id']}/deactivate", headers=H(admin_token))).status_code == 400


# ---------------------------------------------------------------- scoped reads
@pytest.mark.asyncio
async def test_salesperson_reads_are_scoped(client: AsyncClient, admin_token: str, owner_token: str):
    a_token, a_id = await make_salesperson(client, admin_token)
    b_token, b_id = await make_salesperson(client, admin_token)
    vid = await variant_id(client, a_token)
    cust, lead, q = await customer_lead_quote(client, a_token, vid)
    fu = (await client.post("/api/v1/followups", headers=H(a_token),
                            json={"lead_id": lead["id"], "type": "CALL", "scheduled_at": future()})).json()["data"]

    # B: lists never contain A's records
    leads_b = (await client.get("/api/v1/leads?page_size=100", headers=H(b_token))).json()["items"]
    assert all(l["assigned_salesperson_id"] in (None, b_id) for l in leads_b)
    quotes_b = (await client.get("/api/v1/quotations?page_size=100", headers=H(b_token))).json()["items"]
    assert all(x["sales_executive_id"] == b_id for x in quotes_b)
    quotes_b2 = (await client.get(f"/api/v1/quotations?sales_executive_id={a_id}", headers=H(b_token))).json()["items"]
    assert quotes_b2 == []  # cannot widen the scope with a filter
    fus_b = (await client.get("/api/v1/followups?page_size=100", headers=H(b_token))).json()["items"]
    assert all(f["id"] != fu["id"] for f in fus_b)
    custs_b = (await client.get("/api/v1/customers?page_size=100", headers=H(b_token))).json()["items"]
    assert all(c["id"] != cust["id"] for c in custs_b)
    partial = (await client.get(f"/api/v1/customers?search={cust['phone'][:6]}", headers=H(b_token))).json()["items"]
    assert all(c["id"] != cust["id"] for c in partial)  # partial searches reveal nothing

    # B: direct reads by ID are 404 (indistinguishable from nonexistent)
    for url in [f"/api/v1/leads/{lead['id']}", f"/api/v1/quotations/{q['id']}", f"/api/v1/quotations/{q['id']}/pdf",
                f"/api/v1/followups/{fu['id']}", f"/api/v1/customers/{cust['id']}", f"/api/v1/ai/lead-insights/{lead['id']}"]:
        r = await client.request("POST" if "/ai/" in url else "GET", url, headers=H(b_token))
        assert r.status_code == 404, f"{url}: {r.status_code}"

    # B: exact phone lookup still finds the walk-in customer (intake reuse)
    exact = (await client.get(f"/api/v1/customers?search=%2B91{cust['phone']}", headers=H(b_token))).json()
    assert exact["total"] == 1 and exact["items"][0]["id"] == cust["id"]

    # B quotes for that customer -> B now handles them and can open the customer and own quote
    qb = (await client.post("/api/v1/quotations", headers=H(b_token),
                            json={"customer_id": cust["id"], "variant_id": vid})).json()["data"]
    assert (await client.get(f"/api/v1/customers/{cust['id']}", headers=H(b_token))).status_code == 200
    assert (await client.get(f"/api/v1/quotations/{qb['id']}/pdf", headers=H(b_token))).status_code == 200
    # ...but still not A's quotation or A's lead
    assert (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(b_token))).status_code == 404
    # Open lead for same customer+vehicle is reported with its owner so intake can explain it
    conflict = await client.post("/api/v1/leads", headers=H(b_token), json={"customer_id": cust["id"], "interested_variant_id": vid})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["details"]["assigned_salesperson_id"] == a_id

    # A sees own records; management sees everything
    assert (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(a_token))).status_code == 200
    for token in (owner_token, admin_token):
        assert (await client.get(f"/api/v1/leads/{lead['id']}", headers=H(token))).status_code == 200
        assert (await client.get(f"/api/v1/quotations/{q['id']}/pdf", headers=H(token))).status_code == 200
        ids = [c["id"] for c in (await client.get("/api/v1/customers?page_size=100&sort_by=id&sort_order=desc", headers=H(token))).json()["items"]]
        assert cust["id"] in ids


# ---------------------------------------------------------------- follow-up integrity
@pytest.mark.asyncio
async def test_followup_integrity(client: AsyncClient, sales_token: str):
    vid = await variant_id(client, sales_token)
    _, lead, _ = await customer_lead_quote(client, sales_token, vid)
    lead_url = f"/api/v1/leads/{lead['id']}"
    next_date = lambda: client.get(lead_url, headers=H(sales_token))

    f5 = (await client.post("/api/v1/followups", headers=H(sales_token),
                            json={"lead_id": lead["id"], "type": "CALL", "scheduled_at": future(5)})).json()["data"]
    f2 = (await client.post("/api/v1/followups", headers=H(sales_token),
                            json={"lead_id": lead["id"], "type": "VISIT", "scheduled_at": future(2)})).json()["data"]
    assert (await next_date()).json()["data"]["next_followup_date"] == f2["scheduled_at"]  # earliest, not last created

    # reschedule the 5-day one to 1 day -> becomes next
    r = await client.put(f"/api/v1/followups/{f5['id']}", headers=H(sales_token), json={"scheduled_at": future(1)})
    assert r.status_code == 200
    assert (await next_date()).json()["data"]["next_followup_date"] == r.json()["data"]["scheduled_at"]

    # complete it -> next falls back to the 2-day one; completed_at always set; idempotent
    done = await client.post(f"/api/v1/followups/{f5['id']}/complete", headers=H(sales_token), json={"notes": "spoke"})
    assert done.json()["data"]["status"] == "COMPLETED" and done.json()["data"]["completed_at"]
    again = await client.post(f"/api/v1/followups/{f5['id']}/complete", headers=H(sales_token), json={"notes": "x"})
    assert again.status_code == 200 and again.json()["data"]["notes"].count("[Completed Outcome]") == 1
    assert (await next_date()).json()["data"]["next_followup_date"] == f2["scheduled_at"]

    # completed follow-ups cannot be reopened, edited or "completed" via PUT
    for body in ({"status": "SCHEDULED"}, {"scheduled_at": future(3)}, {"notes": "edit"}):
        assert (await client.put(f"/api/v1/followups/{f5['id']}", headers=H(sales_token), json=body)).status_code == 400
    assert (await client.put(f"/api/v1/followups/{f2['id']}", headers=H(sales_token), json={"status": "COMPLETED"})).status_code == 400

    # past dates rejected (create + reschedule); naive datetimes handled, not 500
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert (await client.post("/api/v1/followups", headers=H(sales_token),
                              json={"lead_id": lead["id"], "type": "CALL", "scheduled_at": past})).status_code == 400
    assert (await client.put(f"/api/v1/followups/{f2['id']}", headers=H(sales_token), json={"scheduled_at": past})).status_code == 400
    naive = (datetime.utcnow() + timedelta(days=3)).replace(microsecond=0).isoformat()
    assert (await client.post("/api/v1/followups", headers=H(sales_token),
                              json={"lead_id": lead["id"], "type": "CALL", "scheduled_at": naive})).status_code == 201

    # cancel -> excluded from next date; cancelled cannot be completed
    c = await client.post(f"/api/v1/followups/{f2['id']}/cancel", headers=H(sales_token), json={"reason": "customer busy"})
    assert c.json()["data"]["status"] == "CANCELLED"
    assert (await client.post(f"/api/v1/followups/{f2['id']}/complete", headers=H(sales_token), json={})).status_code == 400

    # closing the lead cancels open follow-ups and clears next date; closed lead rejects new ones
    await client.post(f"{lead_url}/status", headers=H(sales_token), json={"to_status": "LOST", "reason": "bought elsewhere"})
    closed = (await next_date()).json()["data"]
    assert closed["next_followup_date"] is None
    open_left = (await client.get(f"/api/v1/followups?lead_id={lead['id']}&status=SCHEDULED", headers=H(sales_token))).json()["total"]
    assert open_left == 0
    r = await client.post("/api/v1/followups", headers=H(sales_token),
                          json={"lead_id": lead["id"], "type": "CALL", "scheduled_at": future()})
    assert r.status_code == 400 and r.json()["error"]["code"] == "LEAD_CLOSED"


# ---------------------------------------------------------------- quotation validation
@pytest.mark.asyncio
async def test_quotation_input_validation(client: AsyncClient, sales_token: str):
    vid = await variant_id(client, sales_token)
    cust = (await client.post("/api/v1/customers", headers=H(sales_token),
                              json={"full_name": "Validation Test", "phone": unique_phone()})).json()["data"]
    accs = (await client.get("/api/v1/vehicles/catalog/accessories", headers=H(sales_token))).json()["data"]
    base = {"customer_id": cust["id"], "variant_id": vid}
    post = lambda **kw: client.post("/api/v1/quotations", headers=H(sales_token), json={**base, **kw})

    assert (await post(payment_mode="LOAN")).status_code == 422
    assert (await post(payment_mode="FINANCE")).status_code == 201
    assert (await post(accessory_ids=[accs[0]["id"], accs[0]["id"]])).status_code == 422
    unknown = await post(accessory_ids=[999999])
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "ACCESSORY_NOT_FOUND"
    assert (await post(custom_items=[{"item_name": "   ", "unit_price": 100}])).status_code == 422
    ok = await post(accessory_ids=[accs[0]["id"]], custom_items=[{"item_name": "  Warranty  ", "unit_price": 100}])
    assert ok.status_code == 201
    assert {i["item_name"] for i in ok.json()["data"]["items"]} == {accs[0]["name"], "Warranty"}


# ---------------------------------------------------------------- customer deletion
@pytest.mark.asyncio
async def test_customer_delete_refuses_sales_history(client: AsyncClient, sales_token: str, admin_token: str):
    vid = await variant_id(client, sales_token)
    with_history, lead, q = await customer_lead_quote(client, sales_token, vid)
    r = await client.delete(f"/api/v1/customers/{with_history['id']}", headers=H(admin_token))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CUSTOMER_HAS_HISTORY"
    assert r.json()["error"]["details"] == {"quotations": 1, "leads": 1}
    assert (await client.get(f"/api/v1/customers/{with_history['id']}", headers=H(admin_token))).status_code == 200
    assert (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(admin_token))).status_code == 200

    lead_only = (await client.post("/api/v1/customers", headers=H(sales_token),
                                   json={"full_name": "Lead Only", "phone": unique_phone()})).json()["data"]
    await client.post("/api/v1/leads", headers=H(sales_token), json={"customer_id": lead_only["id"]})
    assert (await client.delete(f"/api/v1/customers/{lead_only['id']}", headers=H(admin_token))).status_code == 409

    clean = (await client.post("/api/v1/customers", headers=H(sales_token),
                               json={"full_name": "No History", "phone": unique_phone()})).json()["data"]
    assert (await client.delete(f"/api/v1/customers/{clean['id']}", headers=H(admin_token))).status_code == 200


# ---------------------------------------------------------------- quotation status / expiry
@pytest.mark.asyncio
async def test_quotation_status_state_machine_and_expiry(client: AsyncClient, sales_token: str, db_session):
    vid = await variant_id(client, sales_token)
    cust = (await client.post("/api/v1/customers", headers=H(sales_token),
                              json={"full_name": "Status Test", "phone": unique_phone()})).json()["data"]
    mk = lambda: client.post("/api/v1/quotations", headers=H(sales_token),
                             json={"customer_id": cust["id"], "variant_id": vid})
    st = lambda qid, s: client.put(f"/api/v1/quotations/{qid}/status", headers=H(sales_token), json={"status": s})

    q = (await mk()).json()["data"]
    assert (await st(q["id"], "DRAFT")).status_code == 400          # backwards
    assert (await st(q["id"], "SENT")).status_code == 200
    assert (await st(q["id"], "GENERATED")).status_code == 400      # backwards
    assert (await st(q["id"], "ACCEPTED")).status_code == 200
    assert (await st(q["id"], "REJECTED")).status_code == 400
    assert (await st(q["id"], "CANCELLED")).status_code == 200
    assert (await st(q["id"], "ACCEPTED")).status_code == 400       # terminal
    assert (await st(q["id"], "CANCELLED")).status_code == 200      # same status is a no-op

    # An expired quotation cannot be accepted; it is marked EXPIRED
    q2 = (await mk()).json()["data"]
    await db_session.execute(update(Quotation).where(Quotation.id == q2["id"])
                             .values(expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
    await db_session.commit()
    r = await st(q2["id"], "ACCEPTED")
    assert r.status_code == 400 and r.json()["error"]["code"] == "QUOTATION_EXPIRED"
    assert (await client.get(f"/api/v1/quotations/{q2['id']}", headers=H(sales_token))).json()["data"]["status"] == "EXPIRED"
    assert (await st(q2["id"], "SENT")).status_code == 400

    # The scheduler job expires overdue open quotations and leaves the rest alone
    q3 = (await mk()).json()["data"]
    q4 = (await mk()).json()["data"]
    await db_session.execute(update(Quotation).where(Quotation.id == q3["id"])
                             .values(expires_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    await db_session.commit()
    assert await expire_overdue_quotations(db_session) >= 1
    get = lambda qid: client.get(f"/api/v1/quotations/{qid}", headers=H(sales_token))
    assert (await get(q3["id"])).json()["data"]["status"] == "EXPIRED"
    assert (await get(q4["id"])).json()["data"]["status"] == "GENERATED"
