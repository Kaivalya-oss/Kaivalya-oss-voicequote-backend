"""Multi-tenant test harness. Runs separately from tests/ because the flag changes which routes exist:

    python -m pytest -c pytest_saas.ini

Uses a disposable control-plane database (the name must end in _test) on the configured server.
Showroom databases come from docker-compose.saas-test.yml; no real showroom database is ever used.
"""
import asyncio
import os
import sys
import tempfile
import uuid

from cryptography.fernet import Fernet

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_base = os.environ.get("SAAS_TEST_SERVER_URL") or os.environ["DATABASE_URL"].rsplit("/", 1)[0]
CONTROL_DB = "voicequote_control_test"
os.environ["MULTI_TENANT_ENABLED"] = "true"
os.environ["APP_ENV"] = "test"
os.environ["CONTROL_PLANE_DATABASE_URL"] = f"{_base}/{CONTROL_DB}"
os.environ["TENANT_DB_ALLOW_PRIVATE_HOSTS"] = "true"  # the test servers are on a private Docker network
_secrets_dir = tempfile.mkdtemp(prefix="vq-test-secrets-")
os.environ["SECRETS_LOCAL_DIR"] = _secrets_dir
os.environ["SECRETS_MASTER_KEY"] = Fernet.generate_key().decode()
os.environ["PDF_STORAGE_DIR"] = tempfile.mkdtemp(prefix="vq-test-pdfs-")

# The admin API's own settings. Test-only values, generated per run and different from showroom ones.
ADMIN_TEST_ROLE = "vq_admin_test"
ADMIN_TEST_ROLE_PASSWORD = "admin-role-test-only-" + uuid.uuid4().hex
os.environ["ADMIN_DATABASE_URL"] = _base.replace(_base.split("://", 1)[1].split("@", 1)[0], f"{ADMIN_TEST_ROLE}:{ADMIN_TEST_ROLE_PASSWORD}", 1) + f"/{CONTROL_DB}"
os.environ["ADMIN_BOOTSTRAP_DATABASE_URL"] = f"{_base}/{CONTROL_DB}"
os.environ["ADMIN_JWT_SECRET_KEY"] = "admin-test-signing-key-" + uuid.uuid4().hex
os.environ["ADMIN_MFA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["ADMIN_ALLOWED_ORIGINS"] = "https://admin.test.invalid"
os.environ["ADMIN_ENV"] = "test"

import asyncpg  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import settings  # noqa: E402


async def _recreate_control_db() -> None:
    url = make_url(settings.CONTROL_PLANE_DATABASE_URL)
    assert url.database.endswith("_test"), "Refusing to reset a database whose name does not end in _test."
    conn = await asyncpg.connect(
        host=url.host, port=url.port or 5432, user=url.username, password=url.password, database="postgres"
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


async def _create_admin_login_role() -> None:
    url = make_url(settings.CONTROL_PLANE_DATABASE_URL)
    conn = await asyncpg.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, database=url.database)
    try:
        await conn.execute(f"DROP ROLE IF EXISTS {ADMIN_TEST_ROLE}")
        await conn.execute(f"CREATE ROLE {ADMIN_TEST_ROLE} LOGIN PASSWORD '{ADMIN_TEST_ROLE_PASSWORD}' IN ROLE voicequote_platform_admin")
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def control_plane_schema():
    asyncio.run(_recreate_control_db())
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cfg = Config(os.path.join(root, "alembic_control.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic_control"))
    command.upgrade(cfg, "head")
    asyncio.run(_create_admin_login_role())


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def unique(prefix: str = "x") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


async def register(client: AsyncClient, name: str | None = None, email: str | None = None) -> dict:
    tag = unique()
    res = await client.post(
        "/api/v1/platform/showrooms/register",
        json={
            "showroom_name": name or f"Showroom {tag}",
            "email": email or f"owner-{tag}@example.com",
            "password": "OwnerPass123!",
            "full_name": "Show Owner",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def invite_and_join(client: AsyncClient, owner_token: str, role: str = "SALESPERSON", email: str | None = None) -> dict:
    email = email or f"staff-{unique()}@example.com"
    res = await client.post(
        "/api/v1/platform/tenants/current/invitations", headers=H(owner_token), json={"email": email, "role": role}
    )
    assert res.status_code == 201, res.text
    token = res.json()["data"]["invitation_token"]
    res = await client.post(
        "/api/v1/platform/invitations/accept",
        json={"token": token, "full_name": "Team Member", "password": "StaffPass123!"},
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]
