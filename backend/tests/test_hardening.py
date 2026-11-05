"""Regression tests for the backtest hardening pass: registration, PDF auth, customer
validation, quotation idempotency, atomic lead transitions, inventory state machine,
WhatsApp truthfulness and cross-salesperson authorization."""
import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from httpx import AsyncClient

from app.core.config import settings


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def unique_phone(prefix: str = "7") -> str:
    return prefix + str(time.time_ns())[-9:]


async def first_variant_id(client: AsyncClient, token: str) -> int:
    res = await client.get("/api/v1/vehicles?page_size=1", headers=H(token))
    return res.json()["items"][0]["id"]


async def new_customer(client: AsyncClient, token: str, name: str = "Hardening Test") -> dict:
    res = await client.post("/api/v1/customers", headers=H(token), json={"full_name": name, "phone": unique_phone()})
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def second_salesperson_token(client: AsyncClient) -> str:
    email = f"sales2_{uuid.uuid4().hex[:8]}@dealer-mail.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Sales2Pass!", "full_name": "Second Salesperson"},
    )
    assert reg.status_code == 201, reg.text
    # Self-registered accounts start inactive until a manager approves them.
    admin = (await client.post("/api/v1/auth/login",
                               json={"email": "admin@dealership.com", "password": "AdminPass123!"})).json()["access_token"]
    act = await client.post(f"/api/v1/users/{reg.json()['data']['id']}/activate", headers=H(admin))
    assert act.status_code == 200, act.text
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": "Sales2Pass!"})
    return login.json()["access_token"]


# ---------------------------------------------------------------- registration
@pytest.mark.asyncio
async def test_public_register_cannot_escalate_role(client: AsyncClient):
    for role in ["ADMIN", "DEALER_OWNER", "INVENTORY_MANAGER", f"PWNED_{uuid.uuid4().hex[:6]}"]:
        res = await client.post(
            "/api/v1/auth/register",
            json={"email": f"x_{uuid.uuid4().hex[:8]}@evilcorp-mail.com", "password": "Attack123!",
                  "full_name": "Mallory", "role_name": role},
        )
        assert res.status_code == 403, f"{role}: {res.status_code} {res.text}"
    roles = (await client.post("/api/v1/auth/login", json={"email": "admin@dealership.com", "password": "AdminPass123!"})).json()
    names = [r["name"] for r in (await client.get("/api/v1/auth/roles", headers=H(roles["access_token"]))).json()["data"]]
    assert not any(n.startswith("PWNED_") for n in names)

    ok = await client.post(
        "/api/v1/auth/register",
        json={"email": f"ok_{uuid.uuid4().hex[:8]}@dealer-mail.com", "password": "SalesPass123!", "full_name": "New Sales"},
    )
    assert ok.status_code == 201
    assert ok.json()["data"]["role"]["name"] == "SALES_EXECUTIVE"
    assert ok.json()["data"]["is_active"] is False  # pending manager approval


# ---------------------------------------------------------------- PDF auth
@pytest.mark.asyncio
async def test_quotation_pdf_requires_authentication(client: AsyncClient, sales_token: str):
    cust = await new_customer(client, sales_token)
    q = (await client.post("/api/v1/quotations", headers=H(sales_token),
                           json={"customer_id": cust["id"], "variant_id": await first_variant_id(client, sales_token),
                                 })).json()["data"]
    url = f"/api/v1/quotations/{q['id']}/pdf"

    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=H("not.a.token"))).status_code == 401
    expired = pyjwt.encode({"sub": "3", "type": "access", "exp": int(time.time()) - 5},
                           settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    assert (await client.get(url, headers=H(expired))).status_code == 401
    forged = pyjwt.encode({"sub": "1", "type": "access", "exp": int(time.time()) + 600},
                          "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    assert (await client.get(url, headers=H(forged))).status_code == 401

    ok = await client.get(url, headers=H(sales_token))
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "application/pdf"
    assert ok.content[:4] == b"%PDF"

    # Guessing other IDs anonymously yields nothing
    for qid in range(1, 4):
        assert (await client.get(f"/api/v1/quotations/{qid}/pdf")).status_code == 401


# ---------------------------------------------------------------- customer validation
@pytest.mark.asyncio
async def test_customer_whitespace_and_phone_normalization(client: AsyncClient, sales_token: str):
    for bad_name in ["   ", " ", "\t\n", " A "]:
        res = await client.post("/api/v1/customers", headers=H(sales_token),
                                json={"full_name": bad_name, "phone": unique_phone()})
        assert res.status_code == 422, f"{bad_name!r}: {res.status_code}"

    # List stays healthy after invalid requests
    assert (await client.get("/api/v1/customers?page_size=5", headers=H(sales_token))).status_code == 200

    local = unique_phone("9")
    res = await client.post("/api/v1/customers", headers=H(sales_token),
                            json={"full_name": "  John Doe  ", "phone": f"+91 {local[:5]}-{local[5:]}",
                                  "address": "   ", "city": " Pune "})
    assert res.status_code == 201, res.text
    c = res.json()["data"]
    assert c["full_name"] == "John Doe"
    assert c["phone"] == local
    assert c["address"] is None
    assert c["city"] == "Pune"

    for variant in [local, "+91" + local, "91" + local, f"{local[:5]} {local[5:]}", "0" + local]:
        dup = await client.post("/api/v1/customers", headers=H(sales_token), json={"full_name": "Dup", "phone": variant})
        assert dup.status_code == 409, f"{variant}: {dup.status_code}"
        assert dup.json()["error"]["code"] == "CUSTOMER_PHONE_EXISTS"


@pytest.mark.asyncio
async def test_concurrent_duplicate_customer_create_is_409_not_500(client: AsyncClient, sales_token: str):
    phone = unique_phone("6")
    results = await asyncio.gather(*[
        client.post("/api/v1/customers", headers=H(sales_token), json={"full_name": "Race Test", "phone": phone})
        for _ in range(4)
    ])
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and all(c in (201, 409) for c in codes), codes


# ---------------------------------------------------------------- quotation idempotency
@pytest.mark.asyncio
async def test_quotation_idempotency(client: AsyncClient, sales_token: str):
    cust = await new_customer(client, sales_token)
    variant_id = await first_variant_id(client, sales_token)
    body = {"customer_id": cust["id"], "variant_id": variant_id}

    async def count() -> int:
        return (await client.get(f"/api/v1/quotations?customer_id={cust['id']}&page_size=1",
                                 headers=H(sales_token))).json()["total"]

    # 1. single submission
    key = str(uuid.uuid4())
    first = await client.post("/api/v1/quotations", headers={**H(sales_token), "Idempotency-Key": key}, json=body)
    assert first.status_code == 201
    assert await count() == 1

    # 3. identical request, same key -> original returned
    again = await client.post("/api/v1/quotations", headers={**H(sales_token), "Idempotency-Key": key}, json=body)
    assert again.status_code == 200
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.json()["data"]["id"] == first.json()["data"]["id"]
    assert await count() == 1

    # 2. simultaneous double click with a fresh key
    key2 = str(uuid.uuid4())
    burst = await asyncio.gather(*[
        client.post("/api/v1/quotations", headers={**H(sales_token), "Idempotency-Key": key2}, json=body)
        for _ in range(5)
    ])
    ids = {r.json()["data"]["id"] for r in burst}
    assert all(r.status_code in (200, 201) for r in burst), [r.status_code for r in burst]
    assert len(ids) == 1
    assert await count() == 2

    # 4. failed request then retry with the same key succeeds once
    key3 = str(uuid.uuid4())
    failed = await client.post("/api/v1/quotations", headers={**H(sales_token), "Idempotency-Key": key3},
                               json={**body, "variant_id": 999999})
    assert failed.status_code == 404
    retry = await client.post("/api/v1/quotations", headers={**H(sales_token), "Idempotency-Key": key3}, json=body)
    assert retry.status_code == 201
    assert await count() == 3

    # 5. new independent quotation (new key / no key) still creates
    assert (await client.post("/api/v1/quotations", headers={**H(sales_token), "Idempotency-Key": str(uuid.uuid4())},
                              json=body)).status_code == 201
    assert (await client.post("/api/v1/quotations", headers=H(sales_token), json=body)).status_code == 201
    assert await count() == 5

    # Key reused for a different request is rejected, not silently replayed
    other = await new_customer(client, sales_token, "Other Customer")
    mismatch = await client.post("/api/v1/quotations", headers={**H(sales_token), "Idempotency-Key": key},
                                 json={**body, "customer_id": other["id"]})
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"


# ---------------------------------------------------------------- lead creation / transitions
@pytest.mark.asyncio
async def test_open_lead_is_unique_per_customer_and_vehicle(client: AsyncClient, sales_token: str):
    cust = await new_customer(client, sales_token)
    variant_id = await first_variant_id(client, sales_token)
    body = {"customer_id": cust["id"], "interested_variant_id": variant_id}
    results = await asyncio.gather(*[client.post("/api/v1/leads", headers=H(sales_token), json=body) for _ in range(4)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and codes.count(409) == 3, codes
    conflict = next(r for r in results if r.status_code == 409).json()["error"]
    assert conflict["code"] == "LEAD_ALREADY_OPEN"
    created = next(r for r in results if r.status_code == 201).json()["data"]
    assert conflict["details"]["lead_id"] == created["id"]

    # Once closed, a new enquiry may open a fresh lead
    await client.post(f"/api/v1/leads/{created['id']}/status", headers=H(sales_token), json={"to_status": "LOST"})
    reopened = await client.post("/api/v1/leads", headers=H(sales_token), json=body)
    assert reopened.status_code == 201


@pytest.mark.asyncio
async def test_concurrent_lead_transitions_are_atomic(client: AsyncClient, sales_token: str):
    cust = await new_customer(client, sales_token)
    lead = (await client.post("/api/v1/leads", headers=H(sales_token), json={"customer_id": cust["id"]})).json()["data"]
    url = f"/api/v1/leads/{lead['id']}/status"

    results = await asyncio.gather(
        *[client.post(url, headers=H(sales_token), json={"to_status": "CONTACTED", "from_status": "NEW"}) for _ in range(3)],
        *[client.post(url, headers=H(sales_token), json={"to_status": "LOST", "from_status": "NEW"}) for _ in range(3)],
    )
    codes = [r.status_code for r in results]
    assert codes.count(200) == 1, codes
    assert all(c == 409 for c in codes if c != 200), codes
    assert all(r.json()["error"]["code"] == "LEAD_STATE_CHANGED" for r in results if r.status_code == 409)

    final = (await client.get(f"/api/v1/leads/{lead['id']}", headers=H(sales_token))).json()["data"]
    history = sorted(final["status_history"], key=lambda h: h["id"])
    assert [(h["from_status"], h["to_status"]) for h in history] == [("NONE", "NEW"), ("NEW", final["status"])]

    # Without from_status, concurrent requests still serialize: history stays a valid chain
    lead2 = (await client.post("/api/v1/leads", headers=H(sales_token), json={"customer_id": cust["id"]})).json()["data"]
    url2 = f"/api/v1/leads/{lead2['id']}/status"
    await asyncio.gather(*[client.post(url2, headers=H(sales_token), json={"to_status": s}) for s in ["CONTACTED", "LOST"] * 3])
    final2 = (await client.get(f"/api/v1/leads/{lead2['id']}", headers=H(sales_token))).json()["data"]
    chain = sorted(final2["status_history"], key=lambda h: h["id"])
    for prev, nxt in zip(chain, chain[1:]):
        assert nxt["from_status"] == prev["to_status"], [(h["from_status"], h["to_status"]) for h in chain]
    assert chain[-1]["to_status"] == final2["status"]


@pytest.mark.asyncio
async def test_transition_response_has_fresh_history(client: AsyncClient, sales_token: str):
    cust = await new_customer(client, sales_token)
    lead = (await client.post("/api/v1/leads", headers=H(sales_token), json={"customer_id": cust["id"]})).json()["data"]
    res = await client.post(f"/api/v1/leads/{lead['id']}/status", headers=H(sales_token),
                            json={"to_status": "CONTACTED", "reason": "called"})
    assert res.status_code == 200
    assert len(res.json()["data"]["status_history"]) == 2


# ---------------------------------------------------------------- inventory
@pytest.mark.asyncio
async def test_inventory_status_enum_and_transitions(client: AsyncClient, inventory_token: str, sales_token: str):
    variant_id = await first_variant_id(client, sales_token)
    vin = f"HT{uuid.uuid4().hex[:12].upper()}"
    base = {"variant_id": variant_id, "color": "Red", "branch_name": "Main"}

    for bad in ["BANANA", "SOLD", "RESERVED", "available"]:
        r = await client.post("/api/v1/inventory", headers=H(inventory_token),
                              json={**base, "vin_chassis_number": vin + bad[:2], "engine_number": "E" + vin + bad[:2], "status": bad})
        assert r.status_code == 422, f"create status={bad}: {r.status_code}"

    unit = (await client.post("/api/v1/inventory", headers=H(inventory_token),
                              json={**base, "vin_chassis_number": vin, "engine_number": "E" + vin})).json()["data"]
    assert unit["status"] == "AVAILABLE"
    dup = await client.post("/api/v1/inventory", headers=H(inventory_token),
                            json={**base, "vin_chassis_number": vin.lower(), "engine_number": "E2" + vin})
    assert dup.status_code == 409  # case-insensitive duplicate (was 500)

    put = lambda s: client.put(f"/api/v1/inventory/{unit['id']}", headers=H(inventory_token), json={"status": s})
    assert (await put("BANANA")).status_code == 422
    assert (await put("RESERVED")).status_code == 400  # must use /reserve

    res = await client.post(f"/api/v1/inventory/{unit['id']}/reserve", headers=H(sales_token), json={})
    assert res.status_code == 200
    assert res.json()["data"]["reserved_by_user_id"] is not None
    assert (await put("AVAILABLE")).status_code == 400  # must use /release
    assert (await put("SOLD")).status_code == 200
    assert (await put("AVAILABLE")).status_code == 400  # SOLD is terminal
    assert (await put("SERVICE")).status_code == 400

    # Normal maintenance transitions still work
    vin2 = f"HT{uuid.uuid4().hex[:12].upper()}"
    u2 = (await client.post("/api/v1/inventory", headers=H(inventory_token),
                            json={**base, "vin_chassis_number": vin2, "engine_number": "E" + vin2, "status": "IN_TRANSIT"})).json()["data"]
    put2 = lambda s: client.put(f"/api/v1/inventory/{u2['id']}", headers=H(inventory_token), json={"status": s})
    assert (await put2("AVAILABLE")).status_code == 200
    assert (await put2("SERVICE")).status_code == 200
    assert (await put2("AVAILABLE")).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_reservation_single_winner(client: AsyncClient, inventory_token: str, sales_token: str):
    variant_id = await first_variant_id(client, sales_token)
    vin = f"HC{uuid.uuid4().hex[:12].upper()}"
    unit = (await client.post("/api/v1/inventory", headers=H(inventory_token),
                              json={"variant_id": variant_id, "vin_chassis_number": vin, "engine_number": "E" + vin,
                                    "color": "Blue"})).json()["data"]
    results = await asyncio.gather(*[
        client.post(f"/api/v1/inventory/{unit['id']}/reserve", headers=H(sales_token), json={}) for _ in range(8)
    ])
    codes = [r.status_code for r in results]
    assert codes.count(200) == 1 and codes.count(400) == 7, codes


# ---------------------------------------------------------------- WhatsApp truthfulness
@pytest.mark.asyncio
async def test_mock_whatsapp_does_not_mark_sent(client: AsyncClient, sales_token: str):
    if settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID:
        pytest.skip("Real WhatsApp provider configured")
    cust = await new_customer(client, sales_token)
    q = (await client.post("/api/v1/quotations", headers=H(sales_token),
                           json={"customer_id": cust["id"], "variant_id": await first_variant_id(client, sales_token),
                                 })).json()["data"]
    res = await client.post(f"/api/v1/quotations/{q['id']}/send-whatsapp", headers=H(sales_token))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["delivery"]["status"] == "MOCK_SENT"
    assert data["delivered"] is False and data["success"] is False and data["simulated"] is True
    assert data["quotation_status"] == "GENERATED"
    after = (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(sales_token))).json()["data"]
    assert after["status"] == "GENERATED"


@pytest.mark.asyncio
async def test_failed_whatsapp_delivery_changes_nothing(client: AsyncClient, sales_token: str, monkeypatch):
    from app.core.exceptions import BadRequestException
    from app.services import whatsapp_service

    async def boom(self, **kwargs):
        raise BadRequestException("WHATSAPP_API_FAILURE", "simulated provider outage")

    monkeypatch.setattr(whatsapp_service.MockWhatsAppProvider, "send_quotation_message", boom)
    cust = await new_customer(client, sales_token)
    q = (await client.post("/api/v1/quotations", headers=H(sales_token),
                           json={"customer_id": cust["id"], "variant_id": await first_variant_id(client, sales_token),
                                 })).json()["data"]
    res = await client.post(f"/api/v1/quotations/{q['id']}/send-whatsapp", headers=H(sales_token))
    assert res.status_code == 400
    after = (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(sales_token))).json()["data"]
    assert after["status"] == "GENERATED"


@pytest.mark.asyncio
async def test_real_whatsapp_delivery_marks_sent(client: AsyncClient, sales_token: str, monkeypatch):
    from app.services import whatsapp_service

    async def delivered(self, **kwargs):
        return {"provider": "MetaCloudAPI", "status": "SENT", "message_id": "wamid.test"}

    monkeypatch.setattr(whatsapp_service.MockWhatsAppProvider, "send_quotation_message", delivered)
    cust = await new_customer(client, sales_token)
    q = (await client.post("/api/v1/quotations", headers=H(sales_token),
                           json={"customer_id": cust["id"], "variant_id": await first_variant_id(client, sales_token),
                                 })).json()["data"]
    res = await client.post(f"/api/v1/quotations/{q['id']}/send-whatsapp", headers=H(sales_token))
    assert res.json()["data"]["delivered"] is True
    after = (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(sales_token))).json()["data"]
    assert after["status"] == "SENT"


# ---------------------------------------------------------------- cross-salesperson authorization
@pytest.mark.asyncio
async def test_salesperson_cannot_touch_another_salespersons_records(
    client: AsyncClient, sales_token: str, owner_token: str, inventory_token: str
):
    other = await second_salesperson_token(client)
    variant_id = await first_variant_id(client, sales_token)

    # Records owned by salesperson A
    cust = await new_customer(client, sales_token)
    lead = (await client.post("/api/v1/leads", headers=H(sales_token),
                              json={"customer_id": cust["id"], "interested_variant_id": variant_id})).json()["data"]
    fu = (await client.post("/api/v1/followups", headers=H(sales_token),
                            json={"lead_id": lead["id"], "type": "CALL",
                                  "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})).json()["data"]
    q = (await client.post("/api/v1/quotations", headers=H(sales_token),
                           json={"customer_id": cust["id"], "variant_id": variant_id})).json()["data"]
    vin = f"HO{uuid.uuid4().hex[:12].upper()}"
    unit = (await client.post("/api/v1/inventory", headers=H(inventory_token),
                              json={"variant_id": variant_id, "vin_chassis_number": vin, "engine_number": "E" + vin,
                                    "color": "Grey"})).json()["data"]
    await client.post(f"/api/v1/inventory/{unit['id']}/reserve", headers=H(sales_token), json={})

    # Salesperson B is refused everywhere
    denied = [
        await client.post(f"/api/v1/leads/{lead['id']}/status", headers=H(other), json={"to_status": "LOST"}),
        await client.put(f"/api/v1/leads/{lead['id']}", headers=H(other), json={"notes": "hijack"}),
        await client.post("/api/v1/followups", headers=H(other),
                          json={"lead_id": lead["id"], "type": "CALL",
                                "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}),
        await client.post(f"/api/v1/followups/{fu['id']}/complete", headers=H(other), json={}),
        await client.put(f"/api/v1/followups/{fu['id']}", headers=H(other), json={"notes": "x"}),
        await client.put(f"/api/v1/customers/{cust['id']}", headers=H(other), json={"phone": unique_phone()}),
        await client.put(f"/api/v1/quotations/{q['id']}/status", headers=H(other), json={"status": "CANCELLED"}),
        await client.post(f"/api/v1/quotations/{q['id']}/send-whatsapp", headers=H(other)),
        await client.post(f"/api/v1/inventory/{unit['id']}/release", headers=H(other), json={}),
        await client.post("/api/v1/leads", headers=H(other),
                          json={"customer_id": cust["id"], "assigned_salesperson_id": 1}),
    ]
    assert [r.status_code for r in denied] == [403] * len(denied), [(r.request.url.path, r.status_code) for r in denied]

    # Nothing changed
    assert (await client.get(f"/api/v1/leads/{lead['id']}", headers=H(sales_token))).json()["data"]["status"] == "NEW"
    assert (await client.get(f"/api/v1/inventory/{unit['id']}", headers=H(sales_token))).json()["data"]["status"] == "RESERVED"

    # Reads are scoped: B cannot see A's quotation (404, not 403, so IDs cannot be probed),
    # but can still quote for the walk-in customer found by phone
    assert (await client.get(f"/api/v1/quotations/{q['id']}/pdf", headers=H(other))).status_code == 404
    assert (await client.post("/api/v1/quotations", headers=H(other),
                              json={"customer_id": cust["id"], "variant_id": variant_id})).status_code == 201

    # The owner (A) and management keep full access
    assert (await client.post(f"/api/v1/followups/{fu['id']}/complete", headers=H(sales_token), json={})).status_code == 200
    assert (await client.post(f"/api/v1/leads/{lead['id']}/status", headers=H(owner_token),
                              json={"to_status": "CONTACTED", "reason": "manager call"})).status_code == 200
    assert (await client.put(f"/api/v1/leads/{lead['id']}", headers=H(owner_token),
                             json={"assigned_salesperson_id": 1})).status_code == 200
    assert (await client.post(f"/api/v1/inventory/{unit['id']}/release", headers=H(inventory_token), json={})).status_code == 200
