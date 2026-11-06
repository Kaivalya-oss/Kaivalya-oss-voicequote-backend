"""A showroom database outage: one clear error, quick failure, recorded health, other showrooms untouched."""
import asyncio
import socket
import time
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.connection_manager import connection_manager
from tests_saas.conftest import H
from tests_saas.showroom_dbs import SERVER_A, SERVER_B
from tests_saas.test_phase2_tenant_databases import DB, add_customer, connection_row, showroom

CUSTOMERS = "/api/v1/customers"


def dns_fails_for(monkeypatch, *hosts):
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if host in hosts:
            raise socket.gaierror("Name or service not known")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def _tenant(owner: dict) -> uuid.UUID:
    return uuid.UUID(owner["active_tenant"]["tenant_id"])


async def test_unresolvable_host_is_503_on_every_request(client: AsyncClient, monkeypatch):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    assert (await client.get(CUSTOMERS, headers=H(token))).status_code == 200

    await connection_manager.disconnect(_tenant(owner))
    dns_fails_for(monkeypatch, SERVER_A)
    for _ in range(3):
        res = await client.get(CUSTOMERS, headers=H(token))
        assert res.status_code == 503, res.text
        assert res.json()["error"]["code"] == "TENANT_DB_UNAVAILABLE"
        assert SERVER_A not in res.text


async def test_recovers_once_the_host_resolves_again(client: AsyncClient, monkeypatch):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    await add_customer(client, token, "Survives Outage", "9000000011")
    await connection_manager.disconnect(_tenant(owner))

    with monkeypatch.context() as outage:
        dns_fails_for(outage, SERVER_A)
        assert (await client.get(CUSTOMERS, headers=H(token))).status_code == 503

    res = await client.get(CUSTOMERS, headers=H(token))
    assert res.status_code == 200
    assert "Survives Outage" in res.text


async def test_health_reports_and_records_an_unresolvable_host(client: AsyncClient, monkeypatch):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    before = (await connection_row(owner["active_tenant"]["tenant_id"])).last_health_check

    dns_fails_for(monkeypatch, SERVER_A)
    res = await client.post(f"{DB}/health", headers=H(token))
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["healthy"] is False
    assert data["error_code"] == "TENANT_DB_HOST_UNRESOLVED"

    stored = await connection_row(owner["active_tenant"]["tenant_id"])
    assert stored.last_error_code == "TENANT_DB_HOST_UNRESOLVED"
    assert stored.last_health_check > before


async def test_health_clears_the_error_after_recovery(client: AsyncClient, monkeypatch):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    with monkeypatch.context() as outage:
        dns_fails_for(outage, SERVER_A)
        assert (await client.post(f"{DB}/health", headers=H(token))).json()["data"]["healthy"] is False

    res = await client.post(f"{DB}/health", headers=H(token))
    assert res.json()["data"]["healthy"] is True
    assert (await connection_row(owner["active_tenant"]["tenant_id"])).last_error_code is None


async def test_hung_database_fails_within_the_connect_timeout(client: AsyncClient, monkeypatch):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    assert (await client.get(CUSTOMERS, headers=H(token))).status_code == 200

    async def hang(self, *args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(settings, "TENANT_DB_CONNECT_TIMEOUT", 1)
    with monkeypatch.context() as outage:
        outage.setattr(AsyncSession, "connection", hang)
        started = time.monotonic()
        res = await client.get(CUSTOMERS, headers=H(token))
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "TENANT_DB_UNAVAILABLE"
    assert time.monotonic() - started < 3


async def test_slow_dns_fails_within_the_connect_timeout(client: AsyncClient, monkeypatch):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    await connection_manager.disconnect(_tenant(owner))
    real = socket.getaddrinfo

    def slow(host, *args, **kwargs):
        if host == SERVER_A:
            time.sleep(4)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(settings, "TENANT_DB_CONNECT_TIMEOUT", 1)
    monkeypatch.setattr(socket, "getaddrinfo", slow)
    started = time.monotonic()
    res = await client.get(CUSTOMERS, headers=H(token))
    assert res.status_code == 503
    assert time.monotonic() - started < 3


async def test_hung_health_check_is_bounded_and_recorded(client: AsyncClient, monkeypatch):
    from app.control_plane import database_routes

    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]

    async def hang(engine):
        await asyncio.sleep(60)

    monkeypatch.setattr(database_routes, "verify_schema", hang)
    monkeypatch.setattr(settings, "TENANT_DB_HEALTH_TIMEOUT", 1)
    started = time.monotonic()
    res = await client.post(f"{DB}/health", headers=H(token))
    assert time.monotonic() - started < 3
    assert res.status_code == 200, res.text
    assert res.json()["data"]["error_code"] == "TENANT_DB_TIMEOUT"
    assert (await connection_row(owner["active_tenant"]["tenant_id"])).last_error_code == "TENANT_DB_TIMEOUT"


async def test_outage_does_not_touch_another_showroom(client: AsyncClient, monkeypatch):
    down, _ = await showroom(client, SERVER_A)
    up, _ = await showroom(client, SERVER_B)
    await add_customer(client, up["access_token"], "Still Here", "9000000012")
    await connection_manager.disconnect(_tenant(down))

    dns_fails_for(monkeypatch, SERVER_A)
    assert (await client.get(CUSTOMERS, headers=H(down["access_token"]))).status_code == 503
    started = time.monotonic()
    res = await client.get(CUSTOMERS, headers=H(up["access_token"]))
    assert res.status_code == 200 and "Still Here" in res.text
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("field", ["host", "database", "username"])
async def test_outage_error_reveals_no_connection_details(client: AsyncClient, monkeypatch, field):
    owner, database = await showroom(client, SERVER_A)
    await connection_manager.disconnect(_tenant(owner))
    dns_fails_for(monkeypatch, SERVER_A)
    res = await client.get(CUSTOMERS, headers=H(owner["access_token"]))
    assert res.status_code == 503
    secret = {"host": SERVER_A, "database": database, "username": "vq_app"}[field]
    assert secret not in res.text
