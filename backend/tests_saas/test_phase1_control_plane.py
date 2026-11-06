"""Phase 1: tenant registration, memberships, showroom switching and authorization."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.control_plane.models import ControlPlaneBase, Subscription, SubscriptionEvent, TenantConnection, TenantMembership
from app.core.security import create_access_token
from app.database.control_plane import control_sessionmaker
from tests_saas.conftest import H, invite_and_join, register, unique

P = "/api/v1/platform"


async def test_two_independent_showrooms_each_with_owner_subscription_and_pending_connection(client: AsyncClient):
    a = await register(client)
    b = await register(client)
    ta, tb = a["active_tenant"], b["active_tenant"]
    assert ta["tenant_id"] != tb["tenant_id"]
    assert ta["role"] == "OWNER" and tb["role"] == "OWNER"
    assert ta["tenant_status"] == "PENDING_SETUP"

    async with control_sessionmaker()() as s:
        for t in (ta, tb):
            tid = uuid.UUID(t["tenant_id"])
            sub = (await s.execute(select(Subscription).where(Subscription.tenant_id == tid))).scalar_one()
            assert sub.status == "PENDING"
            events = (await s.execute(select(SubscriptionEvent).where(SubscriptionEvent.subscription_id == sub.id))).scalars().all()
            assert [e.event_type for e in events] == ["SUBSCRIPTION_CREATED"]
            conn = (await s.execute(select(TenantConnection).where(TenantConnection.tenant_id == tid))).scalar_one()
            assert conn.connection_status == "PENDING" and conn.secret_reference is None


async def test_setup_status_and_no_secrets_in_responses(client: AsyncClient):
    a = await register(client)
    res = await client.get(f"{P}/tenants/current/setup-status", headers=H(a["access_token"]))
    assert res.status_code == 200
    d = res.json()["data"]
    assert d["operational"] is False and d["next_step"] == "CONNECT_DATABASE"
    assert "secret_reference" not in res.text and "password" not in res.text


async def test_duplicate_registration_prevented(client: AsyncClient):
    email = f"dup-{unique()}@example.com"
    name = f"Dup {unique()}"
    await register(client, name=name, email=email)
    again = await client.post(f"{P}/showrooms/register", json={
        "showroom_name": name, "email": email, "password": "OwnerPass123!", "full_name": "Show Owner"})
    assert again.status_code == 409
    other_email = await client.post(f"{P}/showrooms/register", json={
        "showroom_name": name, "email": f"other-{unique()}@example.com", "password": "OwnerPass123!", "full_name": "Show Owner"})
    assert other_email.status_code == 409 and other_email.json()["error"]["code"] == "SHOWROOM_ALREADY_EXISTS"


async def test_concurrent_registration_creates_one_showroom(client: AsyncClient):
    name, email = f"Race {unique()}", f"race-{unique()}@example.com"
    body = {"showroom_name": name, "email": email, "password": "OwnerPass123!", "full_name": "Show Owner"}
    results = await asyncio.gather(*[client.post(f"{P}/showrooms/register", json=body) for _ in range(4)])
    assert sorted(r.status_code for r in results) == [201, 409, 409, 409]


async def test_failed_registration_leaves_nothing_behind(client: AsyncClient):
    name = f"Atomic {unique()}"
    await register(client, name=name)
    email = f"atomic-{unique()}@example.com"
    res = await client.post(f"{P}/showrooms/register", json={
        "showroom_name": name, "email": email, "password": "OwnerPass123!", "full_name": "Show Owner"})
    assert res.status_code == 409
    login = await client.post(f"{P}/auth/login", json={"email": email, "password": "OwnerPass123!"})
    assert login.status_code == 401  # the user row rolled back with the conflicting tenant


async def test_login_is_generic_and_multi_showroom_user_must_choose(client: AsyncClient):
    a = await register(client)
    email = a["user"]["email"]
    assert (await client.post(f"{P}/auth/login", json={"email": email, "password": "wrong"})).status_code == 401
    assert (await client.post(f"{P}/auth/login", json={"email": "nobody@example.com", "password": "wrong"})).status_code == 401

    one = (await client.post(f"{P}/auth/login", json={"email": email, "password": "OwnerPass123!"})).json()["data"]
    assert one["active_tenant"]["tenant_id"] == a["active_tenant"]["tenant_id"]

    await client.post(f"{P}/showrooms", headers=H(a["access_token"]), json={"showroom_name": f"Second {unique()}"})
    two = (await client.post(f"{P}/auth/login", json={"email": email, "password": "OwnerPass123!"})).json()["data"]
    assert two["active_tenant"] is None
    res = await client.get(f"{P}/tenants/current", headers=H(two["access_token"]))
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_ACTIVE_SHOWROOM"


async def test_user_can_switch_between_authorized_showrooms_with_separate_roles(client: AsyncClient):
    a = await register(client)
    b = await register(client)
    member = await invite_and_join(client, a["access_token"], "HEAD_OF_SALES")
    # the same person joins showroom B with a different role
    res = await client.post(f"{P}/tenants/current/invitations", headers=H(b["access_token"]),
                            json={"email": member["user"]["email"], "role": "SALESPERSON"})
    token = res.json()["data"]["invitation_token"]
    joined = await client.post(f"{P}/invitations/accept", headers=H(member["access_token"]), json={"token": token})
    assert joined.status_code == 200, joined.text
    access = joined.json()["data"]["access_token"]

    me = (await client.get(f"{P}/me", headers=H(access))).json()["data"]
    assert {m["tenant_id"] for m in me["memberships"]} == {a["active_tenant"]["tenant_id"], b["active_tenant"]["tenant_id"]}
    assert me["active_tenant_id"] == b["active_tenant"]["tenant_id"] and me["active_role"] == "SALESPERSON"

    switched = await client.post(f"{P}/tenants/select", headers=H(access), json={"tenant_id": a["active_tenant"]["tenant_id"]})
    assert switched.status_code == 200
    me2 = (await client.get(f"{P}/me", headers=H(switched.json()["data"]["access_token"]))).json()["data"]
    assert me2["active_tenant_id"] == a["active_tenant"]["tenant_id"] and me2["active_role"] == "HEAD_OF_SALES"


async def test_cannot_select_unauthorized_or_unknown_showroom(client: AsyncClient):
    a = await register(client)
    b = await register(client)
    denied = await client.post(f"{P}/tenants/select", headers=H(a["access_token"]), json={"tenant_id": b["active_tenant"]["tenant_id"]})
    unknown = await client.post(f"{P}/tenants/select", headers=H(a["access_token"]), json={"tenant_id": str(uuid.uuid4())})
    assert denied.status_code == 403 and unknown.status_code == 403
    assert denied.json()["error"] == unknown.json()["error"]  # existence is not revealed


async def test_changing_tenant_id_in_a_request_grants_nothing(client: AsyncClient):
    a = await register(client)
    b = await register(client)
    tb = b["active_tenant"]["tenant_id"]
    res = await client.get(f"{P}/tenants/current?tenant_id={tb}",
                           headers={**H(a["access_token"]), "X-Tenant-ID": tb, "X-Tenant": tb})
    assert res.json()["data"]["id"] == a["active_tenant"]["tenant_id"]
    members = await client.get(f"{P}/tenants/current/members?tenant_id={tb}", headers={**H(a["access_token"]), "X-Tenant-ID": tb})
    assert {m["email"] for m in members.json()["data"]} == {a["user"]["email"]}


async def test_token_naming_another_showroom_without_membership_is_rejected(client: AsyncClient):
    a = await register(client)
    b = await register(client)
    # A correctly signed token cannot bypass the membership check made on each request.
    forged = create_access_token(a["user"]["id"], extra_claims={"scope": "platform", "tid": b["active_tenant"]["tenant_id"]})
    assert (await client.get(f"{P}/tenants/current", headers=H(forged))).status_code == 403


async def test_tampered_and_wrong_scope_tokens_rejected(client: AsyncClient):
    a = await register(client)
    assert (await client.get(f"{P}/me")).status_code == 401
    assert (await client.get(f"{P}/me", headers=H(a["access_token"] + "x"))).status_code == 401
    legacy = create_access_token(1, extra_claims={"role": "ADMIN"})
    assert (await client.get(f"{P}/me", headers=H(legacy))).status_code == 401
    assert (await client.get(f"{P}/me", headers=H(a["refresh_token"]))).status_code == 401


async def test_roles_are_enforced_on_the_backend(client: AsyncClient):
    a = await register(client)
    sales = await invite_and_join(client, a["access_token"], "SALESPERSON")
    head = await invite_and_join(client, a["access_token"], "HEAD_OF_SALES")
    for token, list_status in ((sales["access_token"], 403), (head["access_token"], 200)):
        assert (await client.get(f"{P}/tenants/current/members", headers=H(token))).status_code == list_status
        assert (await client.post(f"{P}/tenants/current/invitations", headers=H(token),
                                  json={"email": "x@example.com", "role": "SALESPERSON"})).status_code == 403
        assert (await client.post(f"{P}/tenants/current/members/{uuid.uuid4()}/deactivate", headers=H(token))).status_code == 403
        assert (await client.put(f"{P}/tenants/current/members/{uuid.uuid4()}/role", headers=H(token),
                                 json={"role": "OWNER"})).status_code == 403
    bad_role = await client.post(f"{P}/tenants/current/invitations", headers=H(a["access_token"]),
                                 json={"email": "x@example.com", "role": "ADMIN"})
    assert bad_role.status_code == 422
    # Connection details are for Owners only.
    status_view = (await client.get(f"{P}/tenants/current/setup-status", headers=H(sales["access_token"]))).json()["data"]
    assert status_view["connection"] is None and status_view["next_step"] == "WAIT_FOR_OWNER"


async def test_role_change_persists_and_applies_to_existing_tokens(client: AsyncClient):
    a = await register(client)
    sales = await invite_and_join(client, a["access_token"], "SALESPERSON")
    token = sales["access_token"]
    assert (await client.get(f"{P}/tenants/current/members", headers=H(token))).status_code == 403
    mid = sales["active_tenant"]["membership_id"]
    res = await client.put(f"{P}/tenants/current/members/{mid}/role", headers=H(a["access_token"]), json={"role": "HEAD_OF_SALES"})
    assert res.status_code == 200 and res.json()["data"]["role"] == "HEAD_OF_SALES"
    assert (await client.get(f"{P}/tenants/current/members", headers=H(token))).status_code == 200
    async with control_sessionmaker()() as s:
        assert (await s.get(TenantMembership, uuid.UUID(mid))).role == "HEAD_OF_SALES"


async def test_deactivated_member_loses_access_immediately_even_on_refresh(client: AsyncClient):
    a = await register(client)
    sales = await invite_and_join(client, a["access_token"], "SALESPERSON")
    mid = sales["active_tenant"]["membership_id"]
    assert (await client.post(f"{P}/tenants/current/members/{mid}/deactivate", headers=H(a["access_token"]))).status_code == 200
    assert (await client.get(f"{P}/tenants/current", headers=H(sales["access_token"]))).status_code == 403
    refreshed = (await client.post(f"{P}/auth/refresh", json={"refresh_token": sales["refresh_token"]})).json()["data"]
    assert refreshed["active_tenant"] is None
    assert (await client.get(f"{P}/tenants/current", headers=H(refreshed["access_token"]))).status_code == 409
    select_again = await client.post(f"{P}/tenants/select", headers=H(refreshed["access_token"]),
                                     json={"tenant_id": a["active_tenant"]["tenant_id"]})
    assert select_again.status_code == 403


async def test_showroom_keeps_at_least_one_active_owner(client: AsyncClient):
    a = await register(client)
    mid = a["active_tenant"]["membership_id"]
    demote = await client.put(f"{P}/tenants/current/members/{mid}/role", headers=H(a["access_token"]), json={"role": "SALESPERSON"})
    assert demote.status_code == 409 and demote.json()["error"]["code"] == "LAST_OWNER"
    deactivate = await client.post(f"{P}/tenants/current/members/{mid}/deactivate", headers=H(a["access_token"]))
    assert deactivate.status_code == 409

    second = await invite_and_join(client, a["access_token"], "OWNER")
    assert (await client.put(f"{P}/tenants/current/members/{mid}/role", headers=H(second["access_token"]),
                             json={"role": "SALESPERSON"})).status_code == 200
    last = await client.post(f"{P}/tenants/current/members/{second['active_tenant']['membership_id']}/deactivate",
                             headers=H(second["access_token"]))
    assert last.status_code == 409


async def test_owner_cannot_touch_members_of_another_showroom(client: AsyncClient):
    a = await register(client)
    b = await register(client)
    b_member = await invite_and_join(client, b["access_token"], "SALESPERSON")
    mid = b_member["active_tenant"]["membership_id"]
    assert (await client.post(f"{P}/tenants/current/members/{mid}/deactivate", headers=H(a["access_token"]))).status_code == 404
    assert (await client.put(f"{P}/tenants/current/members/{mid}/role", headers=H(a["access_token"]),
                             json={"role": "OWNER"})).status_code == 404
    assert (await client.get(f"{P}/tenants/current", headers=H(b_member["access_token"]))).status_code == 200


async def test_invitation_is_single_use_hashed_and_replaced_by_reinvite(client: AsyncClient):
    a = await register(client)
    email = f"inv-{unique()}@example.com"
    first = (await client.post(f"{P}/tenants/current/invitations", headers=H(a["access_token"]),
                               json={"email": email, "role": "SALESPERSON"})).json()["data"]
    second = (await client.post(f"{P}/tenants/current/invitations", headers=H(a["access_token"]),
                                json={"email": email, "role": "HEAD_OF_SALES"})).json()["data"]
    assert first["membership_id"] == second["membership_id"]  # no duplicate membership

    async with control_sessionmaker()() as s:
        stored = await s.get(TenantMembership, uuid.UUID(second["membership_id"]))
        assert stored.invitation_token_hash != second["invitation_token"]

    body = {"full_name": "New Person", "password": "StaffPass123!"}
    assert (await client.post(f"{P}/invitations/accept", json={"token": first["invitation_token"], **body})).status_code == 400
    ok = await client.post(f"{P}/invitations/accept", json={"token": second["invitation_token"], **body})
    assert ok.status_code == 200 and ok.json()["data"]["active_tenant"]["role"] == "HEAD_OF_SALES"
    assert (await client.post(f"{P}/invitations/accept", json={"token": second["invitation_token"], **body})).status_code == 400
    # the invited member appears in lists without the token or its hash
    listing = await client.get(f"{P}/tenants/current/members", headers=H(a["access_token"]))
    assert "invitation_token" not in listing.text


async def test_expired_invitation_rejected(client: AsyncClient):
    a = await register(client)
    inv = (await client.post(f"{P}/tenants/current/invitations", headers=H(a["access_token"]),
                             json={"email": f"exp-{unique()}@example.com", "role": "SALESPERSON"})).json()["data"]
    async with control_sessionmaker()() as s:
        await s.execute(update(TenantMembership).where(TenantMembership.id == uuid.UUID(inv["membership_id"]))
                        .values(invitation_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await s.commit()
    res = await client.post(f"{P}/invitations/accept", json={"token": inv["invitation_token"], "full_name": "Late Person", "password": "StaffPass123!"})
    assert res.status_code == 400 and res.json()["error"]["code"] == "INVITATION_INVALID"


async def test_leaked_invitation_cannot_take_over_an_existing_account(client: AsyncClient):
    a = await register(client)
    victim = await register(client)
    attacker = await register(client)
    inv = (await client.post(f"{P}/tenants/current/invitations", headers=H(a["access_token"]),
                             json={"email": victim["user"]["email"], "role": "SALESPERSON"})).json()["data"]
    token = inv["invitation_token"]
    anonymous = await client.post(f"{P}/invitations/accept", json={"token": token, "full_name": "Evil", "password": "EvilPass123!"})
    wrong_user = await client.post(f"{P}/invitations/accept", headers=H(attacker["access_token"]), json={"token": token})
    assert anonymous.status_code == 403 and wrong_user.status_code == 403
    # password unchanged, and the invitation still works for the right person
    assert (await client.post(f"{P}/auth/login", json={"email": victim["user"]["email"], "password": "EvilPass123!"})).status_code == 401
    ok = await client.post(f"{P}/invitations/accept", headers=H(victim["access_token"]), json={"token": token})
    assert ok.status_code == 200


async def test_invited_user_cannot_log_in_before_accepting(client: AsyncClient):
    a = await register(client)
    email = f"pending-{unique()}@example.com"
    await client.post(f"{P}/tenants/current/invitations", headers=H(a["access_token"]), json={"email": email, "role": "SALESPERSON"})
    assert (await client.post(f"{P}/auth/login", json={"email": email, "password": "anything123"})).status_code == 401


async def test_central_schema_holds_no_showroom_business_data():
    import app.platform_admin.models  # noqa: F401

    tables = set(ControlPlaneBase.metadata.tables)
    assert tables == {
        "tenants", "users", "tenant_memberships", "tenant_connections",
        "subscriptions", "subscription_events", "tenant_migration_runs",
        "platform_admins", "platform_admin_sessions", "platform_admin_recovery_codes",
        "platform_admin_throttle", "audit_events",
    }
    forbidden = {"customers", "quotations", "quotation_items", "leads", "followups", "test_rides",
                 "inventory_units", "vehicle_variants", "vehicle_models", "vehicle_brands", "offers", "accessories"}
    assert not tables & forbidden
    columns = {c.name for t in ControlPlaneBase.metadata.tables.values() for c in t.columns}
    for sensitive in ("phone", "address", "connection_string", "database_url", "db_password"):
        assert sensitive not in columns
