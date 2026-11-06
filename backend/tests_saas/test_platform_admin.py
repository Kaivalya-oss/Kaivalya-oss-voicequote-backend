"""Phase A: Platform Admin identity, authentication and isolation.

These use real PostgreSQL. Admin requests connect as the least-privilege role, exactly as in
production, and every refusal below is a real response or a real database error.
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid

import asyncpg
import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.core import dev_accounts
from app.core.config import settings
from app.core.security import create_access_token
from app.database.control_plane import control_sessionmaker
from app.platform_admin import security
from app.platform_admin.config import FORBIDDEN_ENVIRONMENT, AdminSettings, forbidden_environment_present, get_admin_settings
from app.platform_admin.database import FORBIDDEN_PRIVILEGE_CHECKS, admin_sessionmaker, boundary_violations
from app.platform_admin.main import AdminStartupRefused, app as admin_app, check_startup_boundary
from app.platform_admin.models import AuditEvent, PlatformAdmin, PlatformAdminSession
from app.platform_admin.routes import REFRESH_COOKIE
from scripts.bootstrap_platform_admin import BootstrapError, bootstrap
from tests_saas.conftest import ADMIN_TEST_ROLE, ADMIN_TEST_ROLE_PASSWORD, H, register
from tests_saas.showroom_dbs import SERVER_A

A = "/api/v1/admin"
ORIGIN = "https://admin.test.invalid"
EMAIL = "platform.admin@example.com"
PASSWORD = "Test-Only-Admin-Passphrase-1"
BOOTSTRAP_URL = os.environ["ADMIN_BOOTSTRAP_DATABASE_URL"]


def clean_role_url() -> dict:
    url = make_url(settings.CONTROL_PLANE_DATABASE_URL)
    return {"host": url.host, "port": url.port or 5432, "database": url.database}


async def owner_execute(sql: str, *args) -> None:
    url = make_url(settings.CONTROL_PLANE_DATABASE_URL)
    conn = await asyncpg.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, database=url.database)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def admin_role_connection() -> asyncpg.Connection:
    return await asyncpg.connect(user=ADMIN_TEST_ROLE, password=ADMIN_TEST_ROLE_PASSWORD, **clean_role_url())


@pytest_asyncio.fixture
async def fresh_admin():
    """Exactly one newly bootstrapped admin, with no MFA, sessions or throttling."""
    await owner_execute("DELETE FROM platform_admins")
    await owner_execute("DELETE FROM platform_admin_throttle")
    return await bootstrap(BOOTSTRAP_URL, EMAIL, "Platform Admin", PASSWORD)


@pytest_asyncio.fixture
async def admin_client():
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="https://testserver", headers={"Origin": ORIGIN}) as ac:
        yield ac


async def start_login(client, password=PASSWORD, email=EMAIL) -> dict:
    res = await client.post(f"{A}/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["data"]


async def enroll(client) -> dict:
    """Returns the secret, recovery codes and the first access token."""
    login = await start_login(client)
    assert login["mfa_enrollment_required"] is True
    start = (await client.post(f"{A}/auth/mfa/enroll/start", json={"mfa_token": login["mfa_token"]})).json()["data"]
    code = security.totp_code(start["secret"], security.totp_step())
    res = await client.post(f"{A}/auth/mfa/enroll/confirm", json={"mfa_token": login["mfa_token"], "code": code})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    return {"secret": start["secret"], "recovery_codes": data["recovery_codes"], "access_token": data["access_token"], "step": security.totp_step()}


async def fresh_code_step(secret: str) -> str:
    """A code for a later step than any used, without waiting 30 seconds."""
    return security.totp_code(secret, security.totp_step() + 1)


async def audit_actions() -> list[str]:
    async with control_sessionmaker()() as s:
        rows = await s.execute(select(AuditEvent.action).order_by(AuditEvent.occurred_at))
        return list(rows.scalars())


def forge(claims: dict, key: str | None = None, algorithm: str = "HS256") -> str:
    now = int(time.time())
    payload = {"iss": "voicequote-admin-api", "aud": "voicequote-admin", "sub": str(uuid.uuid4()), "typ": security.TYPE_ACCESS,
               "jti": "x", "iat": now, "exp": now + 300, "sid": str(uuid.uuid4()), "mfa": True, **claims}
    return jwt.encode(payload, key or get_admin_settings().ADMIN_JWT_SECRET_KEY, algorithm=algorithm)


# ---------- authentication ----------

async def test_admin_authenticates_with_password_and_mfa(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    res = await admin_client.get(f"{A}/me", headers=H(enrolled["access_token"]))
    assert res.status_code == 200
    assert res.json()["data"]["email"] == EMAIL
    assert "password" not in res.text and "mfa_secret" not in res.text


async def test_password_alone_reaches_nothing(fresh_admin, admin_client):
    login = await start_login(admin_client)
    assert (await admin_client.get(f"{A}/me", headers=H(login["mfa_token"]))).status_code == 401
    assert (await admin_client.get(f"{A}/auth/sessions", headers=H(login["mfa_token"]))).status_code == 401
    assert REFRESH_COOKIE not in admin_client.cookies


async def test_second_login_requires_a_totp_code(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    login = await start_login(admin_client)
    assert login["mfa_enrollment_required"] is False
    bad = await admin_client.post(f"{A}/auth/mfa/verify", json={"mfa_token": login["mfa_token"], "code": "000000"})
    assert bad.status_code == 401
    res = await admin_client.post(f"{A}/auth/mfa/verify", json={"mfa_token": login["mfa_token"], "code": await fresh_code_step(enrolled["secret"])})
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("email,password", [(EMAIL, "wrong-password-value"), ("someone.else@example.com", PASSWORD)])
async def test_wrong_credentials_give_one_generic_answer(fresh_admin, admin_client, email, password):
    res = await admin_client.post(f"{A}/auth/login", json={"email": email, "password": password})
    assert res.status_code == 401
    assert res.json()["error"] == {"code": "ADMIN_AUTH_FAILED", "message": "Authentication failed.", "details": None}


async def test_totp_code_cannot_be_used_twice(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    code = await fresh_code_step(enrolled["secret"])
    first = await start_login(admin_client)
    assert (await admin_client.post(f"{A}/auth/mfa/verify", json={"mfa_token": first["mfa_token"], "code": code})).status_code == 200
    second = await start_login(admin_client)
    assert (await admin_client.post(f"{A}/auth/mfa/verify", json={"mfa_token": second["mfa_token"], "code": code})).status_code == 401


async def test_recovery_code_works_once_and_is_stored_hashed(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    code = enrolled["recovery_codes"][0]
    login = await start_login(admin_client)
    assert (await admin_client.post(f"{A}/auth/mfa/verify", json={"mfa_token": login["mfa_token"], "recovery_code": code})).status_code == 200
    again = await start_login(admin_client)
    assert (await admin_client.post(f"{A}/auth/mfa/verify", json={"mfa_token": again["mfa_token"], "recovery_code": code})).status_code == 401
    async with control_sessionmaker()() as s:
        stored = (await s.execute(text("SELECT string_agg(code_hash, ',') FROM platform_admin_recovery_codes"))).scalar_one()
    assert code not in stored and len(enrolled["recovery_codes"]) == 10


async def test_mfa_secret_is_encrypted_at_rest(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    async with control_sessionmaker()() as s:
        stored = (await s.execute(select(PlatformAdmin.mfa_secret_encrypted))).scalar_one()
    assert enrolled["secret"] not in stored
    assert security.decrypt_mfa_secret(stored) == enrolled["secret"]


async def test_enrollment_can_not_be_restarted_once_complete(fresh_admin, admin_client):
    await enroll(admin_client)
    token = security.issue_mfa_token(fresh_admin, security.TYPE_MFA_ENROLL)
    res = await admin_client.post(f"{A}/auth/mfa/enroll/start", json={"mfa_token": token})
    assert res.status_code == 409


def test_totp_matches_rfc_6238_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert security.totp_code(secret, 59 // 30) == "287082"
    assert security.totp_code(secret, 1111111109 // 30) == "081804"
    assert security.match_totp(secret, "287082", None, now=59) == 1
    assert security.match_totp(secret, "287082", 1, now=59) is None
    assert security.match_totp(secret, "abcdef", None, now=59) is None


# ---------- lockout ----------

async def test_repeated_failures_lock_the_source(fresh_admin, admin_client):
    for _ in range(get_admin_settings().ADMIN_MAX_FAILURES_PER_IP):
        assert (await admin_client.post(f"{A}/auth/login", json={"email": EMAIL, "password": "wrong-password-value"})).status_code == 401
    locked = await admin_client.post(f"{A}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "ADMIN_TEMPORARILY_LOCKED"


async def test_repeated_mfa_failures_lock(fresh_admin, admin_client):
    await enroll(admin_client)
    login = await start_login(admin_client)
    for _ in range(get_admin_settings().ADMIN_MAX_MFA_FAILURES):
        assert (await admin_client.post(f"{A}/auth/mfa/verify", json={"mfa_token": login["mfa_token"], "code": "000000"})).status_code == 401
    assert (await admin_client.post(f"{A}/auth/mfa/verify", json={"mfa_token": login["mfa_token"], "code": "000000"})).status_code == 429


async def test_forwarded_header_cannot_evade_the_ip_limit(fresh_admin, admin_client):
    for i in range(get_admin_settings().ADMIN_MAX_FAILURES_PER_IP):
        await admin_client.post(f"{A}/auth/login", json={"email": EMAIL, "password": "wrong-password-value"}, headers={"X-Forwarded-For": f"203.0.113.{i}"})
    res = await admin_client.post(f"{A}/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers={"X-Forwarded-For": "203.0.113.200"})
    assert res.status_code == 429


# ---------- token boundary ----------

async def test_showroom_tokens_cannot_reach_admin_routes(fresh_admin, admin_client, client):
    owner = await register(client)
    for token in (owner["access_token"], owner["refresh_token"], create_access_token(fresh_admin, extra_claims={"scope": "platform", "sid": str(uuid.uuid4()), "mfa": True, "typ": security.TYPE_ACCESS})):
        assert (await admin_client.get(f"{A}/me", headers=H(token))).status_code == 401
        assert (await admin_client.get(f"{A}/auth/sessions", headers=H(token))).status_code == 401


async def test_admin_tokens_cannot_authenticate_as_showroom_users(fresh_admin, admin_client, client):
    enrolled = await enroll(admin_client)
    token = enrolled["access_token"]
    for path in ("/api/v1/platform/me", "/api/v1/platform/memberships", "/api/v1/platform/tenants/current", "/api/v1/customers", "/api/v1/quotations", "/api/v1/leads", "/api/v1/dashboard/summary"):
        res = await client.get(path, headers=H(token))
        assert res.status_code == 401, (path, res.status_code)


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "voicequote-showroom"},
        {"aud": "something-else"},
        {"aud": None},
        {"iss": "someone-else"},
        {"exp": int(time.time()) - 10},
        {"typ": security.TYPE_MFA_CHALLENGE},
        {"typ": security.TYPE_MFA_ENROLL},
        {"mfa": False},
    ],
    ids=["showroom-aud", "other-aud", "no-aud", "wrong-issuer", "expired", "mfa-challenge-type", "mfa-enroll-type", "no-mfa"],
)
async def test_invalid_admin_token_claims_are_rejected(fresh_admin, admin_client, claims):
    enrolled = await enroll(admin_client)
    good = jwt.decode(enrolled["access_token"], options={"verify_signature": False})
    token = forge({"sub": good["sub"], "sid": good["sid"], **claims})
    assert (await admin_client.get(f"{A}/me", headers=H(token))).status_code == 401


async def test_token_signed_with_showroom_key_is_rejected(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    good = jwt.decode(enrolled["access_token"], options={"verify_signature": False})
    assert (await admin_client.get(f"{A}/me", headers=H(forge({"sub": good["sub"], "sid": good["sid"]}, key=settings.JWT_SECRET_KEY)))).status_code == 401


async def test_unsigned_token_is_rejected(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    good = jwt.decode(enrolled["access_token"], options={"verify_signature": False})
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(good).encode()).rstrip(b"=").decode()
    assert (await admin_client.get(f"{A}/me", headers=H(f"{header}.{body}."))).status_code == 401


async def test_showroom_tokens_require_their_own_audience(client):
    owner = await register(client)
    assert jwt.decode(owner["access_token"], options={"verify_signature": False})["aud"] == "voicequote-showroom"
    payload = jwt.decode(owner["access_token"], options={"verify_signature": False})
    for audience in ("voicequote-admin", None):
        forged = {**payload}
        forged.pop("aud")
        if audience:
            forged["aud"] = audience
        token = jwt.encode(forged, settings.JWT_SECRET_KEY, algorithm="HS256")
        assert (await client.get("/api/v1/platform/me", headers=H(token))).status_code == 401


def test_signing_keys_are_different():
    assert get_admin_settings().ADMIN_JWT_SECRET_KEY != settings.JWT_SECRET_KEY


# ---------- sessions ----------

async def test_refresh_rotates_and_reuse_ends_the_session(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    old = admin_client.cookies[REFRESH_COOKIE]
    first = await admin_client.post(f"{A}/auth/refresh")
    assert first.status_code == 200, first.text
    new_access, new_cookie = first.json()["data"]["access_token"], admin_client.cookies[REFRESH_COOKIE]
    assert new_cookie != old

    admin_client.cookies.clear()
    reuse = await admin_client.post(f"{A}/auth/refresh", cookies={REFRESH_COOKIE: old})
    assert reuse.status_code == 401
    # The whole session ended, including the token issued most recently.
    assert (await admin_client.get(f"{A}/me", headers=H(new_access))).status_code == 401
    assert (await admin_client.get(f"{A}/me", headers=H(enrolled["access_token"]))).status_code == 401
    admin_client.cookies.clear()
    assert (await admin_client.post(f"{A}/auth/refresh", cookies={REFRESH_COOKIE: new_cookie})).status_code == 401
    assert "ADMIN_REFRESH_REUSE_DETECTED" in await audit_actions()


async def test_refresh_cookie_is_httponly_secure_and_scoped(fresh_admin, admin_client):
    login = await start_login(admin_client)
    start = (await admin_client.post(f"{A}/auth/mfa/enroll/start", json={"mfa_token": login["mfa_token"]})).json()["data"]
    res = await admin_client.post(f"{A}/auth/mfa/enroll/confirm", json={"mfa_token": login["mfa_token"], "code": security.totp_code(start["secret"], security.totp_step())})
    cookie = res.headers["set-cookie"].lower()
    for attribute in ("httponly", "secure", "samesite=strict", "path=/api/v1/admin/auth"):
        assert attribute in cookie
    assert REFRESH_COOKIE not in res.json()["data"] and "refresh" not in res.text


async def test_refresh_refuses_other_origins(fresh_admin, admin_client):
    await enroll(admin_client)
    for headers in ({"Origin": "https://evil.example.com"}, {"Origin": ""}):
        assert (await admin_client.post(f"{A}/auth/refresh", headers=headers)).status_code == 403


async def test_logout_revokes_immediately(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    cookie = admin_client.cookies[REFRESH_COOKIE]
    assert (await admin_client.post(f"{A}/auth/logout", headers=H(enrolled["access_token"]))).status_code == 200
    assert (await admin_client.get(f"{A}/me", headers=H(enrolled["access_token"]))).status_code == 401
    admin_client.cookies.clear()
    assert (await admin_client.post(f"{A}/auth/refresh", cookies={REFRESH_COOKIE: cookie})).status_code == 401


async def test_sessions_can_be_listed_and_revoked_individually_and_together(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    login = await start_login(admin_client)
    second = (await admin_client.post(f"{A}/auth/mfa/verify", json={"mfa_token": login["mfa_token"], "code": await fresh_code_step(enrolled["secret"])})).json()["data"]["access_token"]
    listed = (await admin_client.get(f"{A}/auth/sessions", headers=H(second))).json()["data"]
    assert len(listed) == 2 and sum(s["current"] for s in listed) == 1
    assert "refresh_token_hash" not in json.dumps(listed)
    other = next(s for s in listed if not s["current"])
    assert (await admin_client.post(f"{A}/auth/sessions/{other['id']}/revoke", headers=H(second))).status_code == 200
    assert (await admin_client.get(f"{A}/me", headers=H(enrolled["access_token"]))).status_code == 401
    assert (await admin_client.get(f"{A}/me", headers=H(second))).status_code == 200
    assert (await admin_client.post(f"{A}/auth/logout-all", headers=H(second))).status_code == 200
    assert (await admin_client.get(f"{A}/me", headers=H(second))).status_code == 401


async def test_idle_session_expires(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    await owner_execute("UPDATE platform_admin_sessions SET last_used_at = now() - interval '2 hours'")
    assert (await admin_client.get(f"{A}/me", headers=H(enrolled["access_token"]))).status_code == 401
    assert (await admin_client.post(f"{A}/auth/refresh")).status_code == 401


async def test_absolute_session_limit(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    await owner_execute("UPDATE platform_admin_sessions SET absolute_expires_at = now() - interval '1 second'")
    assert (await admin_client.get(f"{A}/me", headers=H(enrolled["access_token"]))).status_code == 401


async def test_disabled_admin_loses_access_immediately(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    await owner_execute("UPDATE platform_admins SET status = 'DISABLED'")
    assert (await admin_client.get(f"{A}/me", headers=H(enrolled["access_token"]))).status_code == 401
    assert (await admin_client.post(f"{A}/auth/login", json={"email": EMAIL, "password": PASSWORD})).status_code == 401


# ---------- only one admin, no public creation ----------

def admin_route_table() -> dict:
    # The OpenAPI table includes routes from nested routers, which app.routes alone doesn't list.
    return admin_app.openapi()["paths"]


async def test_no_route_can_create_a_platform_admin():
    paths = admin_route_table()
    assert "/api/v1/admin/auth/login" in paths and "/api/v1/admin/me" in paths and len(paths) >= 10
    for path in paths:
        assert path.startswith("/api/v1/admin/"), path
    joined = " ".join(paths).lower()
    for word in ("register", "signup", "sign-up", "/admins", "create-admin"):
        assert word not in joined


@pytest.mark.parametrize("path", ["/api/v1/admin/register", "/api/v1/admin/auth/register", "/api/v1/admin/admins", "/api/v1/admin/signup"])
async def test_registration_paths_do_not_exist(fresh_admin, admin_client, path):
    res = await admin_client.post(path, json={"email": "x@example.com", "password": PASSWORD, "full_name": "X"})
    assert res.status_code == 404


async def test_second_admin_refused_by_bootstrap(fresh_admin):
    with pytest.raises(BootstrapError, match="already exists"):
        await bootstrap(BOOTSTRAP_URL, "second@example.com", "Second", PASSWORD)
    async with control_sessionmaker()() as s:
        assert (await s.execute(text("SELECT count(*) FROM platform_admins"))).scalar_one() == 1


async def test_second_admin_refused_by_the_database_itself(fresh_admin):
    url = make_url(settings.CONTROL_PLANE_DATABASE_URL)
    conn = await asyncpg.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, database=url.database)
    try:
        insert = "INSERT INTO platform_admins (id, singleton, email, full_name, password_hash, status, created_at, updated_at) VALUES ($1, $2, $3, 'S', 'x', 'ACTIVE', now(), now())"
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(insert, uuid.uuid4(), True, "second@example.com")
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(insert, uuid.uuid4(), False, "third@example.com")
    finally:
        await conn.close()


async def test_admin_database_role_cannot_create_or_delete_the_admin(fresh_admin):
    conn = await admin_role_connection()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO platform_admins (id, email, full_name, password_hash, status, created_at, updated_at) VALUES (gen_random_uuid(), 'x@example.com', 'X', 'x', 'ACTIVE', now(), now())")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM platform_admins")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE platform_admins SET password_hash = 'x'")
    finally:
        await conn.close()


@pytest.mark.parametrize("password", ["short-password", "AdminPass123!", "x" * 73])
async def test_bootstrap_refuses_weak_or_published_passwords(password):
    with pytest.raises(BootstrapError):
        await bootstrap(BOOTSTRAP_URL, "new@example.com", "New", password)


def test_published_password_list_matches_the_development_accounts():
    assert security.PUBLISHED_PASSWORDS == frozenset(a[3] for a in dev_accounts.DEV_ACCOUNTS)
    assert security.PUBLISHED_EMAILS == frozenset(a[0] for a in dev_accounts.DEV_ACCOUNTS)


@pytest.mark.parametrize("env", [{}, {"ADMIN_JWT_SECRET_KEY": ""}, {"ADMIN_JWT_SECRET_KEY": "short"}, {"ADMIN_MFA_ENCRYPTION_KEY": "not-a-key"}])
def test_no_default_or_weak_admin_secrets(monkeypatch, env):
    for name in ("ADMIN_JWT_SECRET_KEY", "ADMIN_MFA_ENCRYPTION_KEY", "ADMIN_DATABASE_URL"):
        monkeypatch.setenv(name, os.environ[name])
    if not env:
        monkeypatch.delenv("ADMIN_JWT_SECRET_KEY")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        AdminSettings()


def test_cookie_security_cannot_be_disabled_in_production(monkeypatch):
    monkeypatch.setenv("ADMIN_ENV", "production")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    with pytest.raises(ValidationError, match="ADMIN_COOKIE_SECURE"):
        AdminSettings()


def test_source_has_no_hardcoded_admin_secret():
    root = os.path.join(os.path.dirname(__file__), "..")
    for folder, _, files in os.walk(os.path.join(root, "app", "platform_admin")):
        for name in files:
            if name.endswith(".py"):
                source = open(os.path.join(folder, name), encoding="utf-8").read()
                assert not re.search(r"(?i)(secret|password|key)\s*[:=]\s*[\"'][^\"']{12,}[\"']", source), name


# ---------- no route to showroom data or credentials ----------

def test_admin_process_cannot_load_showroom_code_or_config():
    """A clean interpreter holding only admin settings: it must start and load nothing tenant-related."""
    script = (
        "import sys, json\n"
        "import app.platform_admin.main\n"
        "print(json.dumps(sorted(m for m in sys.modules if m == 'app' or m.startswith('app.'))))\n"
    )
    keep = {k: os.environ[k] for k in os.environ if k.startswith("ADMIN_") or k in ("PATH", "SYSTEMROOT", "HOME")}
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out = subprocess.run([sys.executable, "-c", script], cwd=root, env={**keep, "PYTHONPATH": root}, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    loaded = json.loads(out.stdout.strip().splitlines()[-1])
    allowed = {"app", "app.control_plane", "app.control_plane.models", "app.core", "app.core.exceptions", "app.schemas", "app.schemas.common"}
    unexpected = [m for m in loaded if m not in allowed and not m.startswith("app.platform_admin")]
    assert unexpected == []


async def test_showroom_app_serves_no_admin_routes(client):
    from app.main import app as showroom_app

    paths = showroom_app.openapi()["paths"]
    assert "/api/v1/customers" in paths  # proves this is the full table
    assert not [p for p in paths if p.startswith("/api/v1/admin")]
    assert (await client.post("/api/v1/admin/auth/login", json={"email": EMAIL, "password": PASSWORD})).status_code == 404


async def test_admin_app_serves_no_showroom_routes(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    for path in ("/api/v1/customers", "/api/v1/quotations", "/api/v1/leads", "/api/v1/platform/me", "/api/v1/platform/tenants/current/database"):
        assert (await admin_client.get(path, headers=H(enrolled["access_token"]))).status_code == 404


async def test_admin_role_has_none_of_the_forbidden_privileges():
    async with admin_sessionmaker()() as session:
        assert await boundary_violations(session) == []


async def test_boundary_check_really_detects_a_powerful_role():
    """The owner role has these privileges, so the same check must report them."""
    async with control_sessionmaker()() as session:
        found = await boundary_violations(session)
    assert "read tenant secret references" in found and "create a platform admin" in found
    assert len(FORBIDDEN_PRIVILEGE_CHECKS) >= 17


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT secret_reference FROM tenant_connections",
        "SELECT * FROM tenant_connections",
        "SELECT password_hash FROM users",
        "SELECT * FROM users",
        "SELECT invitation_token_hash FROM tenant_memberships",
    ],
)
async def test_admin_role_cannot_read_credentials(sql):
    conn = await admin_role_connection()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch(sql)
    finally:
        await conn.close()


async def test_admin_role_can_read_only_connection_metadata():
    conn = await admin_role_connection()
    try:
        await conn.fetch("SELECT tenant_id, connection_status, schema_version, last_health_check, last_error_code FROM tenant_connections")
        await conn.fetch("SELECT id, email, full_name FROM users")
    finally:
        await conn.close()


@pytest.mark.parametrize("table", ["customers", "quotations", "quotation_items", "leads", "followups", "inventory_units", "vehicle_variants"])
async def test_no_business_table_exists_for_the_admin_role(table):
    conn = await admin_role_connection()
    try:
        with pytest.raises(asyncpg.UndefinedTableError):
            await conn.fetch(f"SELECT * FROM {table}")
    finally:
        await conn.close()


async def test_admin_role_cannot_log_in_to_a_showroom_database():
    with pytest.raises((asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError)):
        await asyncpg.connect(host=SERVER_A, user=ADMIN_TEST_ROLE, password=ADMIN_TEST_ROLE_PASSWORD, database="showroom_test", ssl=False)


def test_startup_detects_forbidden_environment():
    assert forbidden_environment_present({"ADMIN_ENV": "production"}) == []
    assert forbidden_environment_present({name: "x" for name in FORBIDDEN_ENVIRONMENT}) == list(FORBIDDEN_ENVIRONMENT)
    assert forbidden_environment_present({"SECRETS_MASTER_KEY": ""}) == []


async def test_startup_refuses_when_holding_tenant_secrets(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", "anything")
    with pytest.raises(AdminStartupRefused, match="SECRETS_MASTER_KEY"):
        await check_startup_boundary()


async def test_error_responses_and_validation_never_echo_input(fresh_admin, admin_client, caplog):
    secret_value = "Not-In-Any-Log-" + uuid.uuid4().hex
    res = await admin_client.post(f"{A}/auth/login", json={"email": "not-an-email", "password": secret_value})
    assert res.status_code == 422
    assert secret_value not in res.text
    assert secret_value not in caplog.text


# ---------- audit ----------

async def test_administrative_actions_produce_audit_events(fresh_admin, admin_client):
    await owner_execute("SELECT 1")
    enrolled = await enroll(admin_client)
    await admin_client.post(f"{A}/auth/login", json={"email": EMAIL, "password": "wrong-password-value"})
    await admin_client.post(f"{A}/auth/logout", headers=H(enrolled["access_token"]))
    actions = await audit_actions()
    for action in ("PLATFORM_ADMIN_BOOTSTRAPPED", "ADMIN_PASSWORD_ACCEPTED", "ADMIN_MFA_ENROLLMENT_STARTED", "ADMIN_MFA_ENROLLED", "ADMIN_LOGIN_SUCCEEDED", "ADMIN_LOGIN_FAILED", "ADMIN_LOGOUT"):
        assert action in actions, action
    async with control_sessionmaker()() as s:
        row = (await s.execute(select(AuditEvent).where(AuditEvent.action == "ADMIN_LOGOUT").order_by(AuditEvent.occurred_at.desc()))).scalars().first()
    assert row.actor_type == "PLATFORM_ADMIN" and row.actor_id == fresh_admin


async def test_audit_events_hold_no_secrets(fresh_admin, admin_client):
    enrolled = await enroll(admin_client)
    async with control_sessionmaker()() as s:
        everything = " ".join(str(r) for r in (await s.execute(text("SELECT * FROM audit_events"))).all())
    for value in (PASSWORD, enrolled["secret"], enrolled["access_token"], *enrolled["recovery_codes"]):
        assert value not in everything


@pytest.mark.parametrize("sql", ["UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"])
async def test_audit_events_are_append_only_even_for_the_owner(fresh_admin, sql):
    with pytest.raises(asyncpg.RestrictViolationError):
        await owner_execute(sql)


@pytest.mark.parametrize("sql", ["UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"])
async def test_admin_role_has_no_privilege_to_change_audit_events(sql):
    conn = await admin_role_connection()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(sql)
    finally:
        await conn.close()


# ---------- session records ----------

async def test_session_stores_only_a_hash_of_the_refresh_token(fresh_admin, admin_client):
    await enroll(admin_client)
    cookie = admin_client.cookies[REFRESH_COOKIE]
    async with control_sessionmaker()() as s:
        stored = (await s.execute(select(PlatformAdminSession.refresh_token_hash))).scalar_one()
    assert stored == security.sha256_hex(cookie) and cookie not in stored
