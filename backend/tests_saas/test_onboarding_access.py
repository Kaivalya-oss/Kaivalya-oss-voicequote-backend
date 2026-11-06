"""Invitation-only onboarding, employee deactivation and showroom suspension, on real databases."""
import asyncio
import uuid

import asyncpg
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, text

from app.control_plane.models import Tenant, TenantConnection, TenantMembership
from app.database.control_plane import control_sessionmaker
from app.platform_admin import delivery
from app.platform_admin.models import AuditEvent
from tests_saas.conftest import H, invite_and_join, register, unique
from tests_saas.showroom_dbs import SERVER_A, SERVER_B, connection_body, new_database
from tests_saas.test_phase2_tenant_databases import DB, add_customer, connect, seed_vehicles, showroom
from tests_saas.test_platform_admin import admin_client, admin_role_connection, enroll, fresh_admin, owner_execute  # noqa: F401

A = "/api/v1/admin/showrooms"
P = "/api/v1/platform"
ACCEPT = f"{P}/invitations/accept"
OWNER_PASSWORD = "Owner-Test-Passphrase-1"


def body(**overrides) -> dict:
    tag = unique()
    return {"showroom_name": f"Showroom {tag}", "legal_business_name": f"Showroom {tag} Pvt Ltd", "business_registration_number": "REG123", "city": "Pune", "state": "MH",
            "business_address": "1 Registered Road", "contact_phone": "9000000099", "owner_email": f"owner-{tag}@example.com", "owner_full_name": "First Owner", **overrides}


@pytest.fixture
def mailbox(monkeypatch):
    """Captures real outgoing messages at the last step before the network."""
    sent = []
    monkeypatch.setattr(delivery, "send_message", sent.append)
    return sent


def token_from(message) -> str:
    return message.get_content().split("#token=")[1].split()[0]


@pytest_asyncio.fixture
async def admin_token(fresh_admin, admin_client):  # noqa: F811
    return (await enroll(admin_client))["access_token"]


# ---------- who can start onboarding ----------

async def test_public_registration_routes_are_gone(client: AsyncClient):
    public = {"showroom_name": f"Claimed {unique()}", "email": f"x-{unique()}@example.com", "password": OWNER_PASSWORD, "full_name": "Claimer"}
    assert (await client.post(f"{P}/showrooms/register", json=public)).status_code == 404
    assert (await client.post(f"{P}/showrooms", json=public)).status_code == 404
    from app.main import app

    paths = app.openapi()["paths"]
    assert f"{P}/invitations/accept" in paths
    assert not [p for p in paths if "showrooms" in p or "register" in p]


async def test_admin_creates_a_showroom_and_invites_the_owner(admin_client, admin_token, mailbox, client):  # noqa: F811
    req = body()
    res = await admin_client.post(A, json=req, headers=H(admin_token))
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    assert data["onboarding_stage"] == "INVITED" and data["status"] == "PENDING_SETUP" and data["invitation_delivery"] == "SENT"
    assert data["public_code"].startswith("VQ-")
    assert len(mailbox) == 1 and mailbox[0]["To"] == req["owner_email"]

    # The owner accepts, sets their own password, and still has no business access.
    accepted = await client.post(ACCEPT, json={"token": token_from(mailbox[0]), "full_name": "First Owner", "password": OWNER_PASSWORD})
    assert accepted.status_code == 200, accepted.text
    token = accepted.json()["data"]["access_token"]
    assert accepted.json()["data"]["active_tenant"]["role"] == "OWNER"
    status_view = (await client.get(f"{P}/tenants/current/setup-status", headers=H(token))).json()["data"]
    assert status_view["onboarding_stage"] == "DATABASE_SETUP" and status_view["operational"] is False
    assert (await client.get("/api/v1/customers", headers=H(token))).status_code == 403

    # Operational only after the database is verified.
    assert (await connect(client, token, SERVER_A)).status_code == 200
    status_view = (await client.get(f"{P}/tenants/current/setup-status", headers=H(token))).json()["data"]
    assert status_view["onboarding_stage"] == "COMPLETE" and status_view["operational"] is True
    assert (await client.get("/api/v1/customers", headers=H(token))).status_code == 200
    assert (await client.post(f"{P}/auth/login", json={"email": req["owner_email"], "password": OWNER_PASSWORD})).status_code == 200


@pytest.mark.parametrize("use", ["none", "tampered"])
async def test_onboarding_requires_the_platform_admin(admin_client, mailbox, use):  # noqa: F811
    headers = {} if use == "none" else H("abc.def.ghi")
    assert (await admin_client.post(A, json=body(), headers=headers)).status_code == 401
    assert mailbox == []


async def test_showroom_owner_cannot_onboard_invite_primary_or_suspend(admin_client, admin_token, client, mailbox):  # noqa: F811
    owner = await register(client)
    token = owner["access_token"]
    tenant = owner["active_tenant"]["tenant_id"]
    assert (await admin_client.post(A, json=body(), headers=H(token))).status_code == 401
    for path, payload in ((f"{A}/{tenant}/owner-invitation", {}), (f"{A}/{tenant}/suspend", {"reason": "Not allowed"}), (f"{A}/{tenant}/reactivate", {"reason": "Not allowed"})):
        assert (await admin_client.post(path, json=payload, headers=H(token))).status_code == 401
    assert (await admin_client.get(f"{A}/{tenant}", headers=H(token))).status_code == 401
    assert mailbox == []


async def test_token_is_never_returned_stored_raw_audited_or_logged(admin_client, admin_token, mailbox, caplog):  # noqa: F811
    res = await admin_client.post(A, json=body(), headers=H(admin_token))
    token = token_from(mailbox[0])
    assert token not in res.text and "token" not in res.text
    async with control_sessionmaker()() as s:
        everything = " ".join(str(r) for r in (await s.execute(text("SELECT * FROM audit_events"))).all())
        stored = (await s.execute(select(TenantMembership.invitation_token_hash).where(TenantMembership.tenant_id == uuid.UUID(res.json()["data"]["id"])))).scalar_one()
    assert token not in everything and token not in caplog.text
    assert stored != token and len(stored) == 64


async def test_admin_role_can_write_but_never_read_invitation_hashes():
    conn = await admin_role_connection()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT invitation_token_hash FROM tenant_memberships")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE tenant_memberships SET role = 'OWNER'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE users SET password_hash = 'x'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE tenant_connections SET secret_reference = 'x'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM tenants")
    finally:
        await conn.close()


async def test_invitation_works_once(admin_client, admin_token, mailbox, client):  # noqa: F811
    await admin_client.post(A, json=body(), headers=H(admin_token))
    token = token_from(mailbox[0])
    first = {"token": token, "full_name": "First Owner", "password": OWNER_PASSWORD}
    assert (await client.post(ACCEPT, json=first)).status_code == 200
    again = await client.post(ACCEPT, json=first)
    assert again.status_code == 400 and again.json()["error"]["code"] == "INVITATION_INVALID"


async def test_expired_invitation_is_refused(admin_client, admin_token, mailbox, client):  # noqa: F811
    res = await admin_client.post(A, json=body(), headers=H(admin_token))
    await owner_execute("UPDATE tenant_memberships SET invitation_expires_at = now() - interval '1 minute' WHERE tenant_id = $1", uuid.UUID(res.json()["data"]["id"]))
    refused = await client.post(ACCEPT, json={"token": token_from(mailbox[0]), "full_name": "Late Owner", "password": OWNER_PASSWORD})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "INVITATION_INVALID"


async def test_invalid_token_is_refused_without_detail(client: AsyncClient):
    res = await client.post(ACCEPT, json={"token": "not-a-real-invitation-token-value", "full_name": "X Y", "password": OWNER_PASSWORD})
    assert res.status_code == 400 and res.json()["error"]["message"] == "This invitation is invalid or has expired."


async def test_resend_replaces_the_previous_token(admin_client, admin_token, mailbox, client):  # noqa: F811
    res = await admin_client.post(A, json=body(), headers=H(admin_token))
    showroom_id = res.json()["data"]["id"]
    resent = await admin_client.post(f"{A}/{showroom_id}/owner-invitation", json={}, headers=H(admin_token))
    assert resent.status_code == 200, resent.text
    old, new = token_from(mailbox[0]), token_from(mailbox[1])
    assert old != new
    assert (await client.post(ACCEPT, json={"token": old, "full_name": "First Owner", "password": OWNER_PASSWORD})).status_code == 400
    assert (await client.post(ACCEPT, json={"token": new, "full_name": "First Owner", "password": OWNER_PASSWORD})).status_code == 200


async def test_primary_owner_can_be_replaced_before_onboarding_completes(admin_client, admin_token, mailbox, client):  # noqa: F811
    res = await admin_client.post(A, json=body(), headers=H(admin_token))
    showroom_id = res.json()["data"]["id"]
    new_email = f"replacement-{unique()}@example.com"
    replaced = await admin_client.post(f"{A}/{showroom_id}/owner-invitation", json={"owner_email": new_email, "owner_full_name": "Replacement Owner"}, headers=H(admin_token))
    assert replaced.status_code == 200, replaced.text
    assert (await client.post(ACCEPT, json={"token": token_from(mailbox[0]), "full_name": "First Owner", "password": OWNER_PASSWORD})).status_code == 400
    assert (await client.post(ACCEPT, json={"token": token_from(mailbox[1]), "full_name": "Replacement Owner", "password": OWNER_PASSWORD})).status_code == 200
    async with control_sessionmaker()() as s:
        actions = list((await s.execute(select(AuditEvent.action).where(AuditEvent.tenant_id == uuid.UUID(showroom_id)))).scalars())
    assert "PRIMARY_OWNER_REPLACED" in actions


async def test_admin_cannot_invite_or_replace_an_owner_after_onboarding(admin_client, admin_token, mailbox, client):  # noqa: F811
    """No silent takeover: this is the path an administrator would use to enter an active showroom."""
    res = await admin_client.post(A, json=body(), headers=H(admin_token))
    showroom_id = res.json()["data"]["id"]
    accepted = await client.post(ACCEPT, json={"token": token_from(mailbox[0]), "full_name": "First Owner", "password": OWNER_PASSWORD})
    assert (await connect(client, accepted.json()["data"]["access_token"], SERVER_A)).status_code == 200
    count = len(mailbox)
    for payload in ({}, {"owner_email": f"attacker-{unique()}@example.com", "owner_full_name": "Attacker"}):
        refused = await admin_client.post(f"{A}/{showroom_id}/owner-invitation", json=payload, headers=H(admin_token))
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "OWNER_RECOVERY_REQUIRED"
    assert len(mailbox) == count


async def test_admin_email_and_published_emails_cannot_be_owners(admin_client, admin_token, mailbox):  # noqa: F811
    for email in ("platform.admin@example.com", "admin@dealership.com"):
        res = await admin_client.post(A, json=body(owner_email=email), headers=H(admin_token))
        assert res.status_code == 400 and res.json()["error"]["code"] == "OWNER_EMAIL_NOT_ALLOWED"
    assert mailbox == []


async def test_existing_account_must_sign_in_to_accept(admin_client, admin_token, mailbox, client):  # noqa: F811
    existing = await register(client, password=OWNER_PASSWORD)
    email = existing["user"]["email"]
    await admin_client.post(A, json=body(owner_email=email), headers=H(admin_token))
    token = token_from(mailbox[0])
    # A leaked link alone can't take over the account, and can't change its password.
    refused = await client.post(ACCEPT, json={"token": token, "full_name": "Attacker", "password": "Attacker-Chosen-Password-1"})
    assert refused.status_code == 403
    assert (await client.post(f"{P}/auth/login", json={"email": email, "password": "Attacker-Chosen-Password-1"})).status_code == 401
    joined = await client.post(ACCEPT, json={"token": token}, headers=H(existing["access_token"]))
    assert joined.status_code == 200, joined.text


async def test_duplicate_showroom_refused_and_rolls_back_completely(admin_client, admin_token, mailbox, client):  # noqa: F811
    req = body()
    assert (await admin_client.post(A, json=req, headers=H(admin_token))).status_code == 201
    email = f"atomic-{unique()}@example.com"
    again = await admin_client.post(A, json={**req, "owner_email": email}, headers=H(admin_token))
    assert again.status_code == 409 and again.json()["error"]["code"] == "SHOWROOM_ALREADY_EXISTS"
    assert len(mailbox) == 1
    async with control_sessionmaker()() as s:
        assert (await s.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": email})).scalar_one() == 0


async def test_concurrent_creation_makes_one_showroom(admin_client, admin_token, mailbox):  # noqa: F811
    req = body()
    results = await asyncio.gather(*[admin_client.post(A, json={**req, "owner_email": f"race-{i}-{unique()}@example.com"}, headers=H(admin_token)) for i in range(4)])
    assert sorted(r.status_code for r in results) == [201, 409, 409, 409]


async def test_delivery_failure_is_reported_and_can_be_retried(admin_client, admin_token, monkeypatch, client):  # noqa: F811
    def fail(message):
        raise OSError("connection refused for owner@example.com")

    monkeypatch.setattr(delivery, "send_message", fail)
    res = await admin_client.post(A, json=body(), headers=H(admin_token))
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["invitation_delivery"] == "FAILED" and data["onboarding_stage"] == "CREATED"

    sent = []
    monkeypatch.setattr(delivery, "send_message", sent.append)
    retry = await admin_client.post(f"{A}/{data['id']}/owner-invitation", json={}, headers=H(admin_token))
    assert retry.json()["data"]["invitation_delivery"] == "SENT" and retry.json()["data"]["onboarding_stage"] == "INVITED"
    assert (await client.post(ACCEPT, json={"token": token_from(sent[0]), "full_name": "First Owner", "password": OWNER_PASSWORD})).status_code == 200


async def test_nothing_is_created_when_delivery_isnt_configured(admin_client, admin_token, monkeypatch):  # noqa: F811
    monkeypatch.setattr(delivery, "delivery_configured", lambda: False)
    req = body()
    res = await admin_client.post(A, json=req, headers=H(admin_token))
    assert res.status_code == 503 and res.json()["error"]["code"] == "INVITATION_DELIVERY_NOT_CONFIGURED"
    async with control_sessionmaker()() as s:
        assert (await s.execute(select(Tenant.id).where(Tenant.showroom_name == req["showroom_name"]))).first() is None


async def test_onboarding_writes_audit_events_naming_the_admin_and_showroom(admin_client, admin_token, mailbox, client, fresh_admin):  # noqa: F811
    res = await admin_client.post(A, json=body(), headers=H(admin_token))
    showroom_id = uuid.UUID(res.json()["data"]["id"])
    await client.post(ACCEPT, json={"token": token_from(mailbox[0]), "full_name": "First Owner", "password": OWNER_PASSWORD})
    async with control_sessionmaker()() as s:
        rows = (await s.execute(select(AuditEvent).where(AuditEvent.tenant_id == showroom_id))).scalars().all()
    by_action = {r.action: r for r in rows}
    assert {"SHOWROOM_CREATED", "OWNER_INVITATION_SENT", "INVITATION_ACCEPTED"} <= set(by_action)
    assert by_action["SHOWROOM_CREATED"].actor_id == fresh_admin and by_action["SHOWROOM_CREATED"].actor_type == "PLATFORM_ADMIN"
    assert by_action["INVITATION_ACCEPTED"].actor_type == "SHOWROOM_ACCOUNT"


async def test_admin_view_contains_no_credentials_or_business_data(admin_client, admin_token, client, mailbox):  # noqa: F811
    res = await admin_client.post(A, json=body(), headers=H(admin_token))
    showroom_id = res.json()["data"]["id"]
    accepted = await client.post(ACCEPT, json={"token": token_from(mailbox[0]), "full_name": "First Owner", "password": OWNER_PASSWORD})
    token = accepted.json()["data"]["access_token"]
    database = await new_database(SERVER_A)
    assert (await client.post(f"{DB}/connect", headers=H(token), json=connection_body(SERVER_A, database))).status_code == 200
    await add_customer(client, token, "Private Customer", "9345670001")
    view = await admin_client.get(f"{A}/{showroom_id}", headers=H(admin_token))
    assert view.status_code == 200
    for forbidden in ("secret_reference", "password", "token", "Private Customer", "9345670001", SERVER_A, database):
        assert forbidden not in view.text


async def test_additional_owner_through_a_controlled_invitation(client: AsyncClient):
    owner, _ = await showroom(client, SERVER_A)
    second = await invite_and_join(client, owner["access_token"], "OWNER")
    assert second["active_tenant"]["role"] == "OWNER"
    assert (await client.get("/api/v1/customers", headers=H(second["access_token"]))).status_code == 200


# ---------- employee deactivation ----------

async def test_deactivated_employee_cannot_do_anything(client: AsyncClient):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    sales = await invite_and_join(client, token, "SALESPERSON")
    staff_token = sales["access_token"]
    vehicle = (await seed_vehicles(client, token))[0]
    customer = await add_customer(client, staff_token, "Kept Customer", "9345670002")
    quote = await client.post("/api/v1/quotations", headers=H(staff_token), json={"customer_id": customer["id"], "variant_id": vehicle["id"], "payment_mode": "CASH"})
    assert quote.status_code == 201, quote.text
    quote_id = quote.json()["data"]["id"]
    assert (await client.post(f"{P}/tenants/current/members/{sales['active_tenant']['membership_id']}/deactivate", headers=H(token))).status_code == 200

    # Existing access token, every kind of route.
    for path in ("/api/v1/customers", "/api/v1/quotations", f"/api/v1/quotations/{quote_id}", f"{P}/tenants/current"):
        assert (await client.get(path, headers=H(staff_token))).status_code == 401, path
    assert (await client.post("/api/v1/customers", headers=H(staff_token), json={"full_name": "New", "phone": "9345670003", "address": "X"})).status_code == 401
    # Existing refresh token, a new sign-in, and rejoining by selecting the showroom.
    assert (await client.post(f"{P}/auth/refresh", json={"refresh_token": sales["refresh_token"]})).status_code == 401
    assert (await client.post(f"{P}/auth/login", json={"email": sales["user"]["email"], "password": "StaffPass123!"})).status_code == 401
    assert (await client.post(f"{P}/tenants/select", headers=H(staff_token), json={"tenant_id": owner["active_tenant"]["tenant_id"]})).status_code == 401

    # History stays, and still names them.
    kept = await client.get(f"/api/v1/quotations/{quote_id}", headers=H(token))
    assert kept.status_code == 200 and kept.json()["data"]["id"] == quote_id
    assert kept.json()["data"].get("sales_executive_id") is not None
    assert (await client.get(f"/api/v1/customers/{customer['id']}", headers=H(token))).status_code == 200
    async with control_sessionmaker()() as s:
        membership = await s.get(TenantMembership, uuid.UUID(sales["active_tenant"]["membership_id"]))
        assert membership.status == "DEACTIVATED"  # deactivated, not deleted
        actions = list((await s.execute(select(AuditEvent.action).where(AuditEvent.target_id == str(membership.id)))).scalars())
    assert "MEMBER_DEACTIVATED" in actions


async def test_deactivated_employee_cannot_create_a_showroom(client: AsyncClient):
    owner = await register(client)
    sales = await invite_and_join(client, owner["access_token"], "SALESPERSON")
    assert (await client.post(f"{P}/tenants/current/members/{sales['active_tenant']['membership_id']}/deactivate", headers=H(owner["access_token"]))).status_code == 200
    for token in (sales["access_token"], sales["refresh_token"]):
        assert (await client.post(f"{P}/showrooms", headers=H(token), json={"showroom_name": "Mine Now"})).status_code == 404


async def test_deactivation_in_one_showroom_leaves_the_other(client: AsyncClient):
    a = await register(client)
    b = await register(client)
    member = await invite_and_join(client, a["access_token"], "SALESPERSON")
    invite = await client.post(f"{P}/tenants/current/invitations", headers=H(b["access_token"]), json={"email": member["user"]["email"], "role": "SALESPERSON"})
    joined = await client.post(ACCEPT, headers=H(member["access_token"]), json={"token": invite.json()["data"]["invitation_token"]})
    assert joined.status_code == 200
    assert (await client.post(f"{P}/tenants/current/members/{member['active_tenant']['membership_id']}/deactivate", headers=H(a["access_token"]))).status_code == 200
    # Still signs in, into B only.
    login = await client.post(f"{P}/auth/login", json={"email": member["user"]["email"], "password": "StaffPass123!"})
    assert login.status_code == 200 and login.json()["data"]["active_tenant"]["tenant_id"] == b["active_tenant"]["tenant_id"]
    assert (await client.get(f"{P}/tenants/current", headers=H(member["access_token"]))).status_code == 403


async def test_reinvited_employee_can_return(client: AsyncClient):
    owner = await register(client)
    member = await invite_and_join(client, owner["access_token"], "SALESPERSON")
    mid = member["active_tenant"]["membership_id"]
    assert (await client.post(f"{P}/tenants/current/members/{mid}/deactivate", headers=H(owner["access_token"]))).status_code == 200
    email = member["user"]["email"]
    assert (await client.post(f"{P}/auth/login", json={"email": email, "password": "StaffPass123!"})).status_code == 401
    invite = await client.post(f"{P}/tenants/current/invitations", headers=H(owner["access_token"]), json={"email": email, "role": "SALESPERSON"})
    assert invite.status_code == 201, invite.text
    login = await client.post(f"{P}/auth/login", json={"email": email, "password": "StaffPass123!"})
    assert login.status_code == 200, login.text
    accepted = await client.post(ACCEPT, headers=H(login.json()["data"]["access_token"]), json={"token": invite.json()["data"]["invitation_token"]})
    assert accepted.status_code == 200, accepted.text


# ---------- showroom suspension ----------

async def suspended_showroom(client, admin_client, admin_token):  # noqa: F811
    owner, database = await showroom(client, SERVER_A)
    token = owner["access_token"]
    customer = await add_customer(client, token, "Suspension Customer", "9345670010")
    head = await invite_and_join(client, token, "HEAD_OF_SALES")
    sales = await invite_and_join(client, token, "SALESPERSON")
    tenant = owner["active_tenant"]["tenant_id"]
    res = await admin_client.post(f"{A}/{tenant}/suspend", json={"reason": "Test suspension"}, headers=H(admin_token))
    assert res.status_code == 200, res.text
    return owner, head, sales, customer, tenant, database


async def test_suspension_blocks_every_role_on_existing_sessions(client, admin_client, admin_token):  # noqa: F811
    owner, head, sales, customer, *_ = await suspended_showroom(client, admin_client, admin_token)
    for member in (owner, head, sales):
        for path in ("/api/v1/customers", "/api/v1/quotations", "/api/v1/leads", "/api/v1/dashboard/summary", f"/api/v1/customers/{customer['id']}"):
            res = await client.get(path, headers=H(member["access_token"]))
            assert res.status_code == 403 and res.json()["error"]["code"] == "TENANT_SUSPENDED", (path, res.status_code)
        write = await client.post("/api/v1/customers", headers=H(member["access_token"]), json={"full_name": "Blocked", "phone": "9345670011", "address": "X"})
        assert write.status_code == 403


async def test_suspended_owner_cannot_administer_the_showroom(client, admin_client, admin_token):  # noqa: F811
    owner, _, sales, *_, database = await suspended_showroom(client, admin_client, admin_token)
    token = owner["access_token"]
    mid = sales["active_tenant"]["membership_id"]
    attempts = [
        ("post", f"{P}/tenants/current/invitations", {"email": f"x-{unique()}@example.com", "role": "SALESPERSON"}),
        ("post", f"{P}/tenants/current/members/{mid}/deactivate", None),
        ("put", f"{P}/tenants/current/members/{mid}/role", {"role": "HEAD_OF_SALES"}),
        ("get", f"{P}/tenants/current/members", None),
        ("post", f"{DB}/test", connection_body(SERVER_A, database)),
        ("post", f"{DB}/connect", connection_body(SERVER_A, database)),
        ("post", f"{DB}/migrate", None),
        ("post", f"{DB}/health", None),
        ("post", f"{DB}/rotate-credentials", {"password": "new-password-value"}),
    ]
    for method, path, payload in attempts:
        kwargs = {"json": payload} if payload is not None else {}
        res = await getattr(client, method)(path, headers=H(token), **kwargs)
        assert res.status_code == 403 and res.json()["error"]["code"] == "TENANT_SUSPENDED", (path, res.status_code, res.text[:100])


async def test_suspended_owner_can_see_status(client, admin_client, admin_token):  # noqa: F811
    owner, *_ = await suspended_showroom(client, admin_client, admin_token)
    view = await client.get(f"{P}/tenants/current/setup-status", headers=H(owner["access_token"]))
    assert view.status_code == 200
    data = view.json()["data"]
    assert data["tenant_status"] == "SUSPENDED" and data["next_step"] == "CONTACT_SUPPORT" and data["suspended_at"]
    assert data["subscription_status"]
    # The reason is the Platform Admin's note, and isn't shown.
    assert "Test suspension" not in view.text


async def test_suspended_owner_can_disconnect_but_stays_suspended(client, admin_client, admin_token):  # noqa: F811
    owner, _, _, _, tenant, database = await suspended_showroom(client, admin_client, admin_token)
    res = await client.post(f"{DB}/revoke", headers=H(owner["access_token"]))
    assert res.status_code == 200, res.text
    # It must say plainly what this did and didn't do.
    assert "didn't change the password on your database server" in res.json()["data"]["notice"]
    async with control_sessionmaker()() as s:
        conn = (await s.execute(select(TenantConnection).where(TenantConnection.tenant_id == uuid.UUID(tenant)))).scalar_one()
        assert conn.secret_reference is None and conn.connection_status == "REVOKED"
        assert (await s.get(Tenant, uuid.UUID(tenant))).status == "SUSPENDED"
    # Reactivating can't jump back to ACTIVE with no connection.
    reactivated = await admin_client.post(f"{A}/{tenant}/reactivate", json={"reason": "Test reactivation"}, headers=H(admin_token))
    assert reactivated.json()["data"]["status"] == "PENDING_SETUP"


async def test_staff_cannot_disconnect_while_suspended(client, admin_client, admin_token):  # noqa: F811
    _, head, sales, *_ = await suspended_showroom(client, admin_client, admin_token)
    for member in (head, sales):
        assert (await client.post(f"{DB}/revoke", headers=H(member["access_token"]))).status_code == 403


async def test_invitation_cannot_be_accepted_while_suspended(client, admin_client, admin_token):  # noqa: F811
    owner, database = await showroom(client, SERVER_A)
    invite = await client.post(f"{P}/tenants/current/invitations", headers=H(owner["access_token"]), json={"email": f"late-{unique()}@example.com", "role": "SALESPERSON"})
    tenant = owner["active_tenant"]["tenant_id"]
    assert (await admin_client.post(f"{A}/{tenant}/suspend", json={"reason": "Test suspension"}, headers=H(admin_token))).status_code == 200
    res = await client.post(ACCEPT, json={"token": invite.json()["data"]["invitation_token"], "full_name": "Late Person", "password": OWNER_PASSWORD})
    assert res.status_code == 403 and res.json()["error"]["code"] == "TENANT_SUSPENDED"


async def test_suspension_deletes_nothing_and_reactivation_restores_access(client, admin_client, admin_token):  # noqa: F811
    owner, head, sales, customer, tenant, _ = await suspended_showroom(client, admin_client, admin_token)
    res = await admin_client.post(f"{A}/{tenant}/reactivate", json={"reason": "Test reactivation"}, headers=H(admin_token))
    assert res.status_code == 200 and res.json()["data"]["status"] == "ACTIVE" and res.json()["data"]["suspended_at"] is None
    # The same tokens work again, and the data is all there.
    found = await client.get(f"/api/v1/customers/{customer['id']}", headers=H(owner["access_token"]))
    assert found.status_code == 200 and found.json()["data"]["full_name"] == "Suspension Customer"
    assert (await client.get("/api/v1/leads", headers=H(sales["access_token"]))).status_code == 200


async def test_suspension_needs_a_reason_and_the_right_state(client, admin_client, admin_token):  # noqa: F811
    owner, _ = await showroom(client, SERVER_A)
    tenant = owner["active_tenant"]["tenant_id"]
    assert (await admin_client.post(f"{A}/{tenant}/suspend", json={}, headers=H(admin_token))).status_code == 422
    assert (await admin_client.post(f"{A}/{tenant}/reactivate", json={"reason": "Not suspended"}, headers=H(admin_token))).status_code == 409
    assert (await admin_client.post(f"{A}/{tenant}/suspend", json={"reason": "First time"}, headers=H(admin_token))).status_code == 200
    assert (await admin_client.post(f"{A}/{tenant}/suspend", json={"reason": "Second time"}, headers=H(admin_token))).status_code == 409
    assert (await admin_client.post(f"{A}/{uuid.uuid4()}/suspend", json={"reason": "No such one"}, headers=H(admin_token))).status_code == 404


async def test_suspension_is_audited_with_reason_and_actor(client, admin_client, admin_token, fresh_admin):  # noqa: F811
    owner, _, _, _, tenant, _ = await suspended_showroom(client, admin_client, admin_token)
    await admin_client.post(f"{A}/{tenant}/reactivate", json={"reason": "Test reactivation"}, headers=H(admin_token))
    async with control_sessionmaker()() as s:
        rows = (await s.execute(select(AuditEvent).where(AuditEvent.tenant_id == uuid.UUID(tenant)).order_by(AuditEvent.occurred_at))).scalars().all()
    by_action = {r.action: r for r in rows}
    assert by_action["SHOWROOM_SUSPENDED"].reason == "Test suspension" and by_action["SHOWROOM_SUSPENDED"].actor_id == fresh_admin
    assert by_action["SHOWROOM_SUSPENDED"].before == {"status": "ACTIVE"} and by_action["SHOWROOM_SUSPENDED"].after == {"status": "SUSPENDED"}
    assert by_action["SHOWROOM_REACTIVATED"].reason == "Test reactivation"


async def test_suspending_one_showroom_leaves_another_running(client, admin_client, admin_token):  # noqa: F811
    await suspended_showroom(client, admin_client, admin_token)
    other, _ = await showroom(client, SERVER_B)
    assert (await client.get("/api/v1/customers", headers=H(other["access_token"]))).status_code == 200
