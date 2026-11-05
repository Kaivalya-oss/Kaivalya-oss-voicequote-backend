"""Disposable showroom databases on the two TLS test servers from docker-compose.saas-test.yml."""
import os
import ssl
import uuid

import asyncpg

SERVER_A = "saas-tenant-a"
SERVER_B = "saas-tenant-b"
CERT_DIR = os.environ.get("SAAS_TEST_CERT_DIR", os.path.join(os.path.dirname(__file__), "..", ".saas-test-certs"))
ADMIN = {"user": "saas_admin", "password": "saas-admin-test-only"}
APP_USER = "vq_app"
APP_PASSWORD = "app-user-test-password-9x7"


def ca_pem(name: str = "ca.crt") -> str:
    with open(os.path.join(CERT_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cadata=ca_pem())
    return ctx


async def admin_connect(host: str, database: str = "showroom_test") -> asyncpg.Connection:
    assert host in (SERVER_A, SERVER_B), "Refusing to administer a server that is not a test server."
    return await asyncpg.connect(host=host, database=database, ssl=_ssl(), **ADMIN)


async def new_database(host: str) -> str:
    """A new empty database, with the restricted application user allowed to create tables in it."""
    name = f"showroom_{uuid.uuid4().hex[:12]}"
    conn = await admin_connect(host)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", APP_USER)
        if not exists:
            await conn.execute(f"CREATE ROLE {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE")
        await conn.execute(f"ALTER ROLE {APP_USER} PASSWORD '{APP_PASSWORD}'")
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    conn = await admin_connect(host, name)
    try:
        await conn.execute(f"GRANT ALL ON SCHEMA public TO {APP_USER}")
    finally:
        await conn.close()
    return name


def connection_body(host: str, database: str, **overrides) -> dict:
    return {
        "provider": "OTHER",
        "host": host,
        "port": 5432,
        "database": database,
        "username": APP_USER,
        "password": APP_PASSWORD,
        "ca_certificate": ca_pem(),
        **overrides,
    }
