"""Sign-out ends a session on the server, refresh tokens rotate, and a reused one ends the session."""
import uuid

import jwt
from httpx import AsyncClient
from sqlalchemy import select

from app.control_plane.models import ShowroomSession
from app.core.security import create_access_token, create_refresh_token
from app.database.control_plane import control_sessionmaker
from app.platform_admin.models import AuditEvent
from tests_saas.conftest import H, invite_and_join, register

P = "/api/v1/platform"


def claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


async def stored(session_id: str) -> ShowroomSession:
    async with control_sessionmaker()() as session:
        return await session.get(ShowroomSession, uuid.UUID(session_id))


async def refresh(client: AsyncClient, token: str):
    return await client.post(f"{P}/auth/refresh", json={"refresh_token": token})


async def test_tokens_name_their_session_and_only_a_hash_is_stored(client: AsyncClient):
    a = await register(client)
    access, refresh_token = claims(a["access_token"]), claims(a["refresh_token"])
    assert access["sid"] == refresh_token["sid"]
    row = await stored(access["sid"])
    assert row is not None and row.revoked_at is None
    assert refresh_token["jti"] not in row.refresh_token_hash and len(row.refresh_token_hash) == 64


async def test_logout_ends_access_and_refresh_tokens_at_once(client: AsyncClient):
    a = await register(client)
    assert (await client.get(f"{P}/me", headers=H(a["access_token"]))).status_code == 200
    r = await client.post(f"{P}/auth/logout", headers=H(a["access_token"]))
    assert r.status_code == 200
    assert (await client.get(f"{P}/me", headers=H(a["access_token"]))).status_code == 401
    assert (await client.get(f"{P}/tenants/current", headers=H(a["access_token"]))).status_code == 401
    # Business routes check the session before anything else.
    assert (await client.get("/api/v1/customers", headers=H(a["access_token"]))).status_code == 401
    assert (await refresh(client, a["refresh_token"])).status_code == 401
    assert (await client.post(f"{P}/auth/logout", headers=H(a["access_token"]))).status_code == 401
    row = await stored(claims(a["access_token"])["sid"])
    assert row.revoked_reason == "SIGNED_OUT"


async def test_logout_is_audited_without_tokens(client: AsyncClient):
    a = await register(client)
    sid = claims(a["access_token"])["sid"]
    await client.post(f"{P}/auth/logout", headers=H(a["access_token"]))
    async with control_sessionmaker()() as session:
        events = (await session.execute(select(AuditEvent).where(AuditEvent.target_id == sid))).scalars().all()
    assert [e.action for e in events] == ["SIGNED_OUT"]
    assert str(events[0].actor_id) == a["user"]["id"]
    text = " ".join(str(v) for e in events for v in (e.before, e.after, e.reason))
    assert a["refresh_token"] not in text and a["access_token"] not in text


async def test_logout_needs_an_access_token(client: AsyncClient):
    a = await register(client)
    assert (await client.post(f"{P}/auth/logout")).status_code == 401
    assert (await client.post(f"{P}/auth/logout", headers=H(a["refresh_token"]))).status_code == 401
    assert (await client.get(f"{P}/me", headers=H(a["access_token"]))).status_code == 200


async def test_logout_ends_only_that_sign_in(client: AsyncClient):
    a = await register(client, password="OwnerPass123!")
    second = (await client.post(f"{P}/auth/login", json={"email": a["user"]["email"], "password": "OwnerPass123!"})).json()["data"]
    assert claims(second["access_token"])["sid"] != claims(a["access_token"])["sid"]
    await client.post(f"{P}/auth/logout", headers=H(a["access_token"]))
    assert (await client.get(f"{P}/me", headers=H(second["access_token"]))).status_code == 200
    assert (await refresh(client, second["refresh_token"])).status_code == 200


async def test_refresh_rotates_and_reuse_ends_the_session(client: AsyncClient):
    a = await register(client)
    first = a["refresh_token"]
    r = await refresh(client, first)
    assert r.status_code == 200
    rotated = r.json()["data"]
    assert claims(rotated["refresh_token"])["sid"] == claims(first)["sid"]
    assert claims(rotated["refresh_token"])["jti"] != claims(first)["jti"]
    # The old one is replayed, as a thief holding a copy would.
    assert (await refresh(client, first)).status_code == 401
    # That ends the session for everyone holding its tokens.
    assert (await refresh(client, rotated["refresh_token"])).status_code == 401
    assert (await client.get(f"{P}/me", headers=H(rotated["access_token"]))).status_code == 401
    row = await stored(claims(first)["sid"])
    assert row.revoked_reason == "REFRESH_REUSED"
    async with control_sessionmaker()() as session:
        actions = (await session.execute(select(AuditEvent.action).where(AuditEvent.target_id == str(row.id)))).scalars().all()
    assert actions == ["SESSION_REFRESH_REUSED"]


async def test_tokens_without_a_live_session_are_refused(client: AsyncClient):
    a = await register(client)
    user, tid = a["user"]["id"], a["active_tenant"]["tenant_id"]
    sid = claims(a["access_token"])["sid"]
    base = {"scope": "platform", "tid": tid}
    # Correctly signed, but naming no session, an unknown one, or one belonging to someone else.
    assert (await client.get(f"{P}/me", headers=H(create_access_token(user, extra_claims=base)))).status_code == 401
    assert (await client.get(f"{P}/me", headers=H(create_access_token(user, extra_claims={**base, "sid": str(uuid.uuid4())})))).status_code == 401
    assert (await client.get(f"{P}/me", headers=H(create_access_token(user, extra_claims={**base, "sid": "not-a-uuid"})))).status_code == 401
    other = await register(client)
    assert (await client.get(f"{P}/me", headers=H(create_access_token(other["user"]["id"], extra_claims={**base, "sid": sid})))).status_code == 401
    # A refresh token without the current ID, or with a guessed one.
    assert (await refresh(client, create_refresh_token(user, extra_claims={**base, "sid": sid}))).status_code == 401
    assert (await refresh(client, create_refresh_token(user, extra_claims={**base, "sid": str(uuid.uuid4()), "jti": "x"}))).status_code == 401


async def test_switching_showroom_stays_in_the_same_session(client: AsyncClient):
    a = await register(client)
    b = await register(client, email=a["user"]["email"], existing_token=a["access_token"])
    switched = await client.post(f"{P}/tenants/select", headers=H(b["access_token"]), json={"tenant_id": a["active_tenant"]["tenant_id"]})
    assert switched.status_code == 200
    tokens = switched.json()["data"]
    assert claims(tokens["access_token"])["sid"] == claims(b["access_token"])["sid"]
    await client.post(f"{P}/auth/logout", headers=H(tokens["access_token"]))
    for token in (tokens["access_token"], b["access_token"]):
        assert (await client.get(f"{P}/me", headers=H(token))).status_code == 401
    assert (await refresh(client, tokens["refresh_token"])).status_code == 401


async def test_staff_sessions_end_on_logout_too(client: AsyncClient):
    a = await register(client)
    staff = await invite_and_join(client, a["access_token"], "SALESPERSON")
    await client.post(f"{P}/auth/logout", headers=H(staff["access_token"]))
    assert (await client.get(f"{P}/me", headers=H(staff["access_token"]))).status_code == 401
    assert (await client.get(f"{P}/me", headers=H(a["access_token"]))).status_code == 200


async def test_emi_needs_a_signed_in_member(client: AsyncClient):
    a = await register(client)
    loan = {"principal": 100000, "annual_interest_rate": 10.5, "tenure_months": 24}
    assert (await client.post("/api/v1/finance/calculate-emi", json=loan)).status_code == 401
    await client.post(f"{P}/auth/logout", headers=H(a["access_token"]))
    assert (await client.post("/api/v1/finance/calculate-emi", json=loan, headers=H(a["access_token"]))).status_code == 401
