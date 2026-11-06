"""Phase 2: showroom-owned databases. These connect to real TLS PostgreSQL servers; nothing is mocked
except where a test name says so (DNS answers, and one injected migration failure)."""
import asyncio
import glob
import json
import os
import re
import socket
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.control_plane.models import ConnectionStatus, ControlPlaneBase, TenantConnection, TenantMigrationRun
from app.core.config import Settings, settings
from app.database import provisioning, tenant_connection
from app.database.connection_manager import connection_manager
from app.database.control_plane import control_sessionmaker
from app.database.provisioning import latest_schema_version
from app.database.secrets import LocalEncryptedSecretStore, SecretNotFound, get_secret_store
from tests_saas.conftest import H, invite_and_join, register, unique
from tests_saas.showroom_dbs import (
    APP_PASSWORD,
    APP_USER,
    SERVER_A,
    SERVER_B,
    admin_connect,
    ca_pem,
    connection_body,
    new_database,
)

DB = "/api/v1/platform/tenants/current/database"
STATUS = "/api/v1/platform/tenants/current/setup-status"


async def connect(client: AsyncClient, token: str, host: str, database: str | None = None, **overrides):
    database = database or await new_database(host)
    return await client.post(f"{DB}/connect", headers=H(token), json=connection_body(host, database, **overrides))


async def showroom(client: AsyncClient, host: str) -> tuple[dict, str]:
    owner = await register(client)
    database = await new_database(host)
    res = await connect(client, owner["access_token"], host, database)
    assert res.status_code == 200, res.text
    return owner, database


async def connection_row(tenant_id: str) -> TenantConnection:
    async with control_sessionmaker()() as s:
        return (await s.execute(select(TenantConnection).where(TenantConnection.tenant_id == uuid.UUID(tenant_id)))).scalar_one()


async def add_customer(client: AsyncClient, token: str, name: str, phone: str) -> dict:
    res = await client.post("/api/v1/customers", headers=H(token), json={"full_name": name, "phone": phone, "address": "12 Test Road"})
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def seed_vehicles(client: AsyncClient, token: str) -> list[dict]:
    brand = (await client.post("/api/v1/vehicles/catalog/brands", headers=H(token), json={"name": f"Brand {unique()}"})).json()["data"]
    model = (await client.post("/api/v1/vehicles/catalog/models", headers=H(token), json={"brand_id": brand["id"], "name": "Model X"})).json()["data"]
    variants = []
    for name, price in (("Standard", 90000), ("Sport", 120000)):
        res = await client.post("/api/v1/vehicles", headers=H(token), json={
            "model_id": model["id"], "name": name, "code": unique("V"), "ex_showroom_price": price,
            "insurance_price": 5000, "rto_price": 4000})
        assert res.status_code == 201, res.text
        variants.append(res.json()["data"])
    return variants


# ---------- connection workflow ----------


async def test_two_showrooms_connect_two_independent_databases(client: AsyncClient):
    a, db_a = await showroom(client, SERVER_A)
    b, db_b = await showroom(client, SERVER_B)
    for owner in (a, b):
        d = (await client.get(STATUS, headers=H(owner["access_token"]))).json()["data"]
        assert d["operational"] is True and d["tenant_status"] == "ACTIVE" and d["connection_status"] == "ACTIVE"
        assert d["connection"]["schema_version"] == latest_schema_version()

    # The schema exists in each showroom's own database, on its own server.
    for host, name in ((SERVER_A, db_a), (SERVER_B, db_b)):
        conn = await admin_connect(host, name)
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == latest_schema_version()
            assert await conn.fetchval("SELECT count(*) FROM pg_tables WHERE tablename = 'customers'") == 1
        finally:
            await conn.close()


async def test_owner_only_and_no_credentials_ever_returned(client: AsyncClient):
    owner = await register(client)
    sales = await invite_and_join(client, owner["access_token"], "SALESPERSON")
    head = await invite_and_join(client, owner["access_token"], "HEAD_OF_SALES")
    database = await new_database(SERVER_A)
    body = connection_body(SERVER_A, database)
    for member in (sales, head):
        for path in ("test", "connect"):
            assert (await client.post(f"{DB}/{path}", headers=H(member["access_token"]), json=body)).status_code == 403
        for path in ("migrate", "health", "revoke"):
            assert (await client.post(f"{DB}/{path}", headers=H(member["access_token"]))).status_code == 403
        assert (await client.post(f"{DB}/rotate-credentials", headers=H(member["access_token"]), json={"password": "x"})).status_code == 403

    res = await client.post(f"{DB}/connect", headers=H(owner["access_token"]), json=body)
    assert res.status_code == 200
    status_view = await client.get(STATUS, headers=H(owner["access_token"]))
    for response in (res, status_view):
        for secret in (APP_PASSWORD, APP_USER, "secret_reference", "local:", database, SERVER_A):
            assert secret not in response.text
    sales_view = await client.get(STATUS, headers=H(sales["access_token"]))
    assert sales_view.json()["data"]["connection"] is None


async def test_credentials_only_in_encrypted_store_with_reference_in_central_db(client: AsyncClient):
    owner, _ = await showroom(client, SERVER_A)
    row = await connection_row(owner["active_tenant"]["tenant_id"])
    assert row.secret_reference and row.secret_reference.startswith("local:")
    assert get_secret_store().get(row.secret_reference)["password"] == APP_PASSWORD

    for path in glob.glob(os.path.join(settings.SECRETS_LOCAL_DIR, "*.enc")):
        with open(path, "rb") as fh:
            assert APP_PASSWORD.encode() not in fh.read()

    # The whole central database, every table and column, holds no credential.
    async with control_sessionmaker()() as s:
        for table in ControlPlaneBase.metadata.sorted_tables:
            rows = (await s.execute(select(table))).all()
            assert APP_PASSWORD not in str(rows), table.name


async def test_failed_validation_leaves_showroom_inactive_and_stores_nothing(client: AsyncClient):
    owner = await register(client)
    token = owner["access_token"]
    before = set(glob.glob(os.path.join(settings.SECRETS_LOCAL_DIR, "*.enc")))
    res = await connect(client, token, SERVER_A, password="wrong-password-value-77")
    assert res.status_code == 422 and res.json()["error"]["code"] == "TENANT_DB_AUTH_FAILED"
    assert "wrong-password-value-77" not in res.text and APP_USER not in res.text
    d = (await client.get(STATUS, headers=H(token))).json()["data"]
    assert d["operational"] is False and d["tenant_status"] == "PENDING_SETUP"
    assert d["connection_status"] == "FAILED" and d["connection"]["last_error_code"] == "TENANT_DB_AUTH_FAILED"
    assert d["connection"]["last_error_message"] == "The database rejected these credentials."
    assert (await connection_row(owner["active_tenant"]["tenant_id"])).secret_reference is None
    assert set(glob.glob(os.path.join(settings.SECRETS_LOCAL_DIR, "*.enc"))) == before
    assert (await client.get("/api/v1/customers", headers=H(token))).status_code == 403


async def test_test_endpoint_changes_nothing(client: AsyncClient):
    owner = await register(client)
    database = await new_database(SERVER_A)
    res = await client.post(f"{DB}/test", headers=H(owner["access_token"]), json=connection_body(SERVER_A, database))
    assert res.status_code == 200, res.text
    assert res.json()["data"]["tls"] is True and res.json()["data"]["schema_version"] is None
    conn = await admin_connect(SERVER_A, database)
    try:
        assert await conn.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == 0
    finally:
        await conn.close()
    assert (await connection_row(owner["active_tenant"]["tenant_id"])).connection_status == "PENDING"


async def test_compatibility_checks_refuse_unsafe_databases(client: AsyncClient):
    owner = await register(client)
    token = owner["access_token"]

    superuser = await connect(client, token, SERVER_A, username="saas_admin", password="saas-admin-test-only")
    assert superuser.json()["error"]["code"] == "TENANT_DB_ROLE_TOO_PRIVILEGED"

    occupied = await new_database(SERVER_A)
    conn = await admin_connect(SERVER_A, occupied)
    await conn.execute("CREATE TABLE somebody_elses_data (id int)")
    await conn.close()
    res = await connect(client, token, SERVER_A, occupied)
    assert res.json()["error"]["code"] == "TENANT_DB_SCHEMA_CONFLICT"
    conn = await admin_connect(SERVER_A, occupied)
    assert await conn.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname='public'") == 1  # untouched
    await conn.close()

    no_create = await new_database(SERVER_A)
    conn = await admin_connect(SERVER_A, no_create)
    await conn.execute(f"REVOKE ALL ON SCHEMA public FROM {APP_USER}")
    await conn.close()
    assert (await connect(client, token, SERVER_A, no_create)).json()["error"]["code"] == "TENANT_DB_PERMISSIONS"

    missing = await connect(client, token, SERVER_A, "no_such_database_here")
    assert missing.json()["error"]["code"] == "TENANT_DB_NOT_FOUND"


# ---------- TLS ----------


async def test_connections_really_use_tls(client: AsyncClient):
    owner, _ = await showroom(client, SERVER_A)
    await add_customer(client, owner["access_token"], "Tls Check", "7" + str(uuid.uuid4().int)[:9])
    tid = uuid.UUID(owner["active_tenant"]["tenant_id"])
    row = await connection_row(str(tid))
    async with connection_manager.session(tid, row.secret_reference) as session:
        tls = (await session.execute(text("SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()"))).one()
    assert tls[0] is True and tls[1] in ("TLSv1.2", "TLSv1.3")
    health = (await client.post(f"{DB}/health", headers=H(owner["access_token"]))).json()["data"]
    assert health["healthy"] is True


async def test_server_without_tls_is_refused(client: AsyncClient):
    owner = await register(client)
    # The main application Postgres has no TLS, standing in for a non-TLS showroom server.
    res = await connect(client, owner["access_token"], "postgres", "dealership_test_db", username="postgres", password="postgrespassword")
    assert res.status_code == 422 and res.json()["error"]["code"] == "TENANT_DB_TLS_FAILED"
    assert "postgrespassword" not in res.text


async def test_untrusted_certificate_and_wrong_hostname_are_refused(client: AsyncClient):
    owner = await register(client)
    token = owner["access_token"]
    database = await new_database(SERVER_A)
    untrusted = await connect(client, token, SERVER_A, database, ca_certificate=ca_pem("other-ca.crt"))
    assert untrusted.json()["error"]["code"] == "TENANT_DB_TLS_FAILED"
    no_ca = await connect(client, token, SERVER_A, database, ca_certificate=None)
    assert no_ca.json()["error"]["code"] == "TENANT_DB_TLS_FAILED"  # not in the system trust store
    # Server B's certificate is valid, but only for B: a redirect to it fails hostname verification.
    context = tenant_connection.build_ssl_context(SERVER_A, ca_pem())
    import asyncpg

    with pytest.raises(Exception) as err:
        await asyncpg.connect(host=SERVER_B, user=APP_USER, password=APP_PASSWORD, database="showroom_test", ssl=context, timeout=5)
    assert "hostname" in str(err.value).lower() or "certificate" in str(err.value).lower()


# ---------- SSRF ----------


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "::1", "fe80::1", "fd00::1", "::ffff:10.0.0.1",
])
async def test_internal_addresses_refused_mocked_dns(monkeypatch, address: str):
    monkeypatch.setattr(settings, "TENANT_DB_ALLOW_PRIVATE_HOSTS", False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", (address, 0))])
    with pytest.raises(tenant_connection.TenantDatabaseError) as err:
        tenant_connection.resolve_allowed_address("db.example.com")
    assert err.value.code == "TENANT_DB_HOST_NOT_ALLOWED"


async def test_dns_answer_mixing_public_and_internal_refused_mocked_dns(monkeypatch):
    monkeypatch.setattr(settings, "TENANT_DB_ALLOW_PRIVATE_HOSTS", False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("8.8.8.8", 0)), (0, 0, 0, "", ("10.0.0.1", 0))])
    with pytest.raises(tenant_connection.TenantDatabaseError):
        tenant_connection.resolve_allowed_address("rebind.example.com")


async def test_public_address_allowed_and_connection_pinned_to_it_mocked_dns(monkeypatch):
    monkeypatch.setattr(settings, "TENANT_DB_ALLOW_PRIVATE_HOSTS", False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("8.8.8.8", 0))])
    creds = tenant_connection.TenantDbCredentials("db.example.com", 5432, "d", "u", "p")
    url, args = tenant_connection.engine_arguments(creds)
    assert url.host == "8.8.8.8"  # connects to the checked address, not a re-resolved name
    assert args["ssl"].pinned_hostname == "db.example.com" and args["ssl"].check_hostname is True


async def test_real_server_on_private_network_refused_when_policy_on(client: AsyncClient, monkeypatch):
    owner = await register(client)
    database = await new_database(SERVER_A)
    monkeypatch.setattr(settings, "TENANT_DB_ALLOW_PRIVATE_HOSTS", False)
    res = await client.post(f"{DB}/test", headers=H(owner["access_token"]), json=connection_body(SERVER_A, database))
    assert res.status_code == 422 and res.json()["error"]["code"] == "TENANT_DB_HOST_NOT_ALLOWED"


@pytest.mark.parametrize("host", [
    "postgres://evil.example.com/db", "evil.example.com:5432", "evil.example.com/db", "user@evil.example.com",
    "evil.example.com?sslmode=disable", "host evil.example.com", "-oProxyCommand", "",
])
async def test_only_a_bare_hostname_is_accepted(client: AsyncClient, host: str):
    owner = await register(client)
    res = await client.post(f"{DB}/test", headers=H(owner["access_token"]), json=connection_body(host, "d"))
    assert res.status_code == 422


async def test_low_ports_refused(client: AsyncClient):
    owner = await register(client)
    res = await client.post(f"{DB}/test", headers=H(owner["access_token"]), json=connection_body(SERVER_A, "d", port=22))
    assert res.status_code == 422


def test_production_refuses_insecure_settings():
    strong = "x" * 40
    with pytest.raises(ValueError):
        Settings(APP_ENV="production", JWT_SECRET_KEY=strong, MULTI_TENANT_ENABLED=True, SECRETS_BACKEND="local")
    with pytest.raises(ValueError):
        Settings(APP_ENV="production", JWT_SECRET_KEY=strong, MULTI_TENANT_ENABLED=True,
                 SECRETS_BACKEND="aws", TENANT_DB_ALLOW_PRIVATE_HOSTS=True)


# ---------- routing and isolation ----------


async def test_requests_route_to_the_right_database_and_tenants_are_isolated(client: AsyncClient):
    a, db_a = await showroom(client, SERVER_A)
    b, db_b = await showroom(client, SERVER_B)
    phone = "9" + str(uuid.uuid4().int)[:9]
    # The same phone can exist in both, because they are separate databases.
    ca = await add_customer(client, a["access_token"], "Alice Of A", phone)
    cb = await add_customer(client, b["access_token"], "Bob Of B", phone)

    list_a = (await client.get("/api/v1/customers", headers=H(a["access_token"]))).json()["items"]
    list_b = (await client.get("/api/v1/customers", headers=H(b["access_token"]))).json()["items"]
    assert [c["full_name"] for c in list_a] == ["Alice Of A"]
    assert [c["full_name"] for c in list_b] == ["Bob Of B"]

    # Independently numbered, so B asking for id 1 gets B's row, never A's.
    assert ca["id"] == cb["id"]
    assert (await client.get(f"/api/v1/customers/{ca['id']}", headers=H(b["access_token"]))).json()["data"]["full_name"] == "Bob Of B"

    # Confirmed directly on each server, bypassing the application.
    for host, name, expected in ((SERVER_A, db_a, "Alice Of A"), (SERVER_B, db_b, "Bob Of B")):
        conn = await admin_connect(host, name)
        assert [r[0] for r in await conn.fetch("SELECT full_name FROM customers")] == [expected]
        await conn.close()


async def test_tenant_id_in_request_cannot_redirect_to_another_database(client: AsyncClient):
    a, _ = await showroom(client, SERVER_A)
    b, _ = await showroom(client, SERVER_B)
    await add_customer(client, b["access_token"], "Secret Of B", "8" + str(uuid.uuid4().int)[:9])
    tb = b["active_tenant"]["tenant_id"]
    res = await client.get(f"/api/v1/customers?tenant_id={tb}", headers={**H(a["access_token"]), "X-Tenant-ID": tb})
    assert res.status_code == 200 and "Secret Of B" not in res.text


async def test_switching_showroom_switches_database(client: AsyncClient):
    a, _ = await showroom(client, SERVER_A)
    b, _ = await showroom(client, SERVER_B)
    await add_customer(client, a["access_token"], "Only In A", "7" + str(uuid.uuid4().int)[:9])
    await add_customer(client, b["access_token"], "Only In B", "7" + str(uuid.uuid4().int)[:9])

    member = await invite_and_join(client, a["access_token"], "HEAD_OF_SALES")
    inv = (await client.post("/api/v1/platform/tenants/current/invitations", headers=H(b["access_token"]),
                             json={"email": member["user"]["email"], "role": "SALESPERSON"})).json()["data"]
    joined = (await client.post("/api/v1/platform/invitations/accept", headers=H(member["access_token"]),
                                json={"token": inv["invitation_token"]})).json()["data"]

    # In B they are a Salesperson with their own (empty) records.
    in_b = await client.get("/api/v1/customers", headers=H(joined["access_token"]))
    assert "Only In A" not in in_b.text
    switched = (await client.post("/api/v1/platform/tenants/select", headers=H(joined["access_token"]),
                                  json={"tenant_id": a["active_tenant"]["tenant_id"]})).json()["data"]
    in_a = await client.get("/api/v1/customers", headers=H(switched["access_token"]))
    assert "Only In A" in in_a.text and "Only In B" not in in_a.text

    # Their old token for A stops working the moment the membership ends.
    mid = next(m["membership_id"] for m in (await client.get("/api/v1/platform/tenants/current/members", headers=H(a["access_token"]))).json()["data"]
               if m["email"] == member["user"]["email"])
    await client.post(f"/api/v1/platform/tenants/current/members/{mid}/deactivate", headers=H(a["access_token"]))
    assert (await client.get("/api/v1/customers", headers=H(switched["access_token"]))).status_code == 403


async def test_business_routes_blocked_until_connected_and_require_auth(client: AsyncClient):
    owner = await register(client)
    assert (await client.get("/api/v1/customers")).status_code == 401
    for path in ("/api/v1/customers", "/api/v1/quotations", "/api/v1/vehicles", "/api/v1/leads"):
        res = await client.get(path, headers=H(owner["access_token"]))
        assert res.status_code == 403 and res.json()["error"]["code"] == "TENANT_NOT_OPERATIONAL", path


async def test_single_showroom_auth_routes_are_not_served(client: AsyncClient):
    assert (await client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "x"})).status_code == 404
    assert (await client.get("/api/v1/users")).status_code == 404


async def test_salesperson_scoping_and_staff_row_have_no_password(client: AsyncClient):
    owner, database = await showroom(client, SERVER_A)
    s1 = await invite_and_join(client, owner["access_token"], "SALESPERSON")
    s2 = await invite_and_join(client, owner["access_token"], "SALESPERSON")
    c = await add_customer(client, s1["access_token"], "Kept By S1", "6" + str(uuid.uuid4().int)[:9])
    assert (await client.get(f"/api/v1/customers/{c['id']}", headers=H(s2["access_token"]))).status_code == 404
    assert (await client.get(f"/api/v1/customers/{c['id']}", headers=H(owner["access_token"]))).status_code == 200

    conn = await admin_connect(SERVER_A, database)
    rows = await conn.fetch("SELECT email, password_hash, central_user_id FROM users")
    await conn.close()
    assert {r["email"] for r in rows} >= {s1["user"]["email"], s2["user"]["email"]}
    assert all(r["password_hash"] == "!central-account" and r["central_user_id"] for r in rows)


async def test_role_change_reaches_the_showroom_staff_row(client: AsyncClient):
    owner, database = await showroom(client, SERVER_A)
    sales = await invite_and_join(client, owner["access_token"], "SALESPERSON")
    await client.get("/api/v1/customers", headers=H(sales["access_token"]))
    assert (await client.get("/api/v1/reports/leads", headers=H(sales["access_token"]))).status_code == 403
    await client.put(f"/api/v1/platform/tenants/current/members/{sales['active_tenant']['membership_id']}/role",
                     headers=H(owner["access_token"]), json={"role": "HEAD_OF_SALES"})
    assert (await client.get("/api/v1/reports/leads", headers=H(sales["access_token"]))).status_code == 200


# ---------- business workflow on the tenant database ----------


async def test_comparison_quotation_in_tenant_database(client: AsyncClient):
    owner, database = await showroom(client, SERVER_A)
    token = owner["access_token"]
    a, b = await seed_vehicles(client, token)
    c = await add_customer(client, token, "Comparison Buyer", "9" + str(uuid.uuid4().int)[:9])
    res = await client.post("/api/v1/quotations", headers=H(token), json={
        "customer_id": c["id"], "variant_id": a["id"], "compare_variant_id": b["id"], "payment_mode": "FINANCE"})
    assert res.status_code == 201, res.text
    q = res.json()["data"]
    assert q["payment_mode"] == "FINANCE" and q["is_comparison"] is True

    conn = await admin_connect(SERVER_A, database)
    assert await conn.fetchval("SELECT count(*) FROM quotations") == 1
    items = await conn.fetch(
        "SELECT vehicle_id, model_name_snapshot, variant_snapshot, ex_showroom, rto, insurance, total "
        "FROM quotation_items WHERE item_type = 'VEHICLE_OPTION' ORDER BY id")
    await conn.close()
    assert len(items) == 2 and [i["vehicle_id"] for i in items] == [a["id"], b["id"]]
    assert float(items[0]["total"]) == 99000 and float(items[1]["total"]) == 129000
    assert items[0]["variant_snapshot"] == "Standard" and items[0]["model_name_snapshot"] == "Model X"

    before = set(glob.glob("generated_pdfs/*")) | set(glob.glob(os.path.join(settings.PDF_STORAGE_DIR, "*")))
    pdf = await client.get(f"/api/v1/quotations/{q['id']}/pdf", headers=H(token))
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert not os.path.exists(os.path.join(settings.PDF_STORAGE_DIR, f"{q['quotation_number']}.pdf"))
    assert set(glob.glob(os.path.join(settings.PDF_STORAGE_DIR, "*"))) <= before

    # Every core business area works through the tenant database.
    single = (await client.post("/api/v1/quotations", headers=H(token), json={"customer_id": c["id"], "variant_id": a["id"]})).json()["data"]
    assert single["is_comparison"] is False
    lead = await client.post("/api/v1/leads", headers=H(token), json={"customer_id": c["id"], "interested_variant_id": a["id"]})
    assert lead.status_code == 201, lead.text
    for path in ("/api/v1/leads", "/api/v1/followups", "/api/v1/test-rides", "/api/v1/inventory", "/api/v1/dashboard/summary", "/api/v1/dashboard/revenue"):
        assert (await client.get(path, headers=H(token))).status_code == 200, path
    export = await client.get("/api/v1/reports/daily-export?format=csv", headers=H(token))
    assert export.status_code == 200 and q["quotation_number"] in export.text


async def test_no_customer_data_reaches_the_central_database(client: AsyncClient):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    a, b = await seed_vehicles(client, token)
    marker_name, marker_phone = f"Privacy{unique()}", "9" + str(uuid.uuid4().int)[:9]
    c = await add_customer(client, token, marker_name, marker_phone)
    q = await client.post("/api/v1/quotations", headers=H(token), json={"customer_id": c["id"], "variant_id": a["id"], "compare_variant_id": b["id"]})
    assert q.status_code == 201
    number = q.json()["data"]["quotation_number"]
    async with control_sessionmaker()() as s:
        for table in ControlPlaneBase.metadata.sorted_tables:
            dump = str((await s.execute(select(table))).all())
            for marker in (marker_name, marker_phone, number, "12 Test Road"):
                assert marker not in dump, table.name


async def test_customer_details_are_not_logged(client: AsyncClient, caplog):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    a, b = await seed_vehicles(client, token)
    name, phone = f"Logged{unique()}", "9" + str(uuid.uuid4().int)[:9]
    with caplog.at_level("DEBUG"):
        c = await add_customer(client, token, name, phone)
        q = (await client.post("/api/v1/quotations", headers=H(token), json={"customer_id": c["id"], "variant_id": a["id"], "compare_variant_id": b["id"]})).json()["data"]
        assert (await client.post(f"/api/v1/quotations/{q['id']}/send-whatsapp", headers=H(token))).status_code == 200
        assert (await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(token), json={"email": "buyer@example.com"})).status_code == 200
    for secret in (name, phone, "buyer@example.com", APP_PASSWORD):
        assert secret not in caplog.text


# ---------- pooling ----------


async def test_engine_is_shared_and_connections_stay_within_the_pool(client: AsyncClient):
    owner, database = await showroom(client, SERVER_A)
    token = owner["access_token"]
    tid = uuid.UUID(owner["active_tenant"]["tenant_id"])

    await client.get("/api/v1/customers", headers=H(token))
    engine = connection_manager.engine_for(tid)
    results = await asyncio.gather(*[client.get("/api/v1/customers", headers=H(token)) for _ in range(40)])
    assert all(r.status_code == 200 for r in results)
    assert connection_manager.engine_for(tid) is engine  # no new engine per request

    conn = await admin_connect(SERVER_A, database)
    used = await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname = $1 AND usename = $2", database, APP_USER)
    await conn.close()
    assert 1 <= used <= settings.TENANT_DB_POOL_SIZE + settings.TENANT_DB_MAX_OVERFLOW


async def test_engines_are_capped_and_least_recently_used_evicted(client: AsyncClient, monkeypatch):
    a, _ = await showroom(client, SERVER_A)
    b, _ = await showroom(client, SERVER_B)
    c, _ = await showroom(client, SERVER_A)
    await connection_manager.dispose_all()
    monkeypatch.setattr(settings, "TENANT_DB_MAX_ENGINES", 2)
    for owner in (a, b, c):
        assert (await client.get("/api/v1/customers", headers=H(owner["access_token"]))).status_code == 200
    assert connection_manager.engine_count == 2
    assert connection_manager.engine_for(uuid.UUID(a["active_tenant"]["tenant_id"])) is None
    # An evicted showroom simply reconnects.
    assert (await client.get("/api/v1/customers", headers=H(a["access_token"]))).status_code == 200


async def test_idle_showroom_connections_are_released(client: AsyncClient, monkeypatch):
    owner, database = await showroom(client, SERVER_A)
    other, _ = await showroom(client, SERVER_B)
    token = owner["access_token"]
    assert (await client.get("/api/v1/customers", headers=H(token))).status_code == 200
    assert (await client.get("/api/v1/customers", headers=H(other["access_token"]))).status_code == 200
    tid = uuid.UUID(owner["active_tenant"]["tenant_id"])
    other_id = uuid.UUID(other["active_tenant"]["tenant_id"])
    engine = connection_manager.engine_for(tid)
    for _ in range(100):
        if engine.pool.checkedout() == 0:
            break
        await asyncio.sleep(0.02)

    # Recently used showrooms keep their pools.
    assert await connection_manager.dispose_idle() == 0
    assert connection_manager.engine_for(tid) is engine

    monkeypatch.setattr(settings, "TENANT_DB_IDLE_SECONDS", 0)
    await asyncio.sleep(0.01)
    await connection_manager.dispose_idle()
    assert connection_manager.engine_for(tid) is None and connection_manager.engine_for(other_id) is None
    conn = await admin_connect(SERVER_A, database)
    remaining = await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname = $1 AND usename = $2", database, APP_USER)
    await conn.close()
    assert remaining == 0
    # The next request simply reconnects.
    assert (await client.get("/api/v1/customers", headers=H(token))).status_code == 200


async def test_sessions_released_after_requests_and_errors(client: AsyncClient):
    owner, database = await showroom(client, SERVER_A)
    token = owner["access_token"]
    for _ in range(10):
        await client.get("/api/v1/customers/999999", headers=H(token))  # 404 path
    tid = uuid.UUID(owner["active_tenant"]["tenant_id"])
    pool = connection_manager.engine_for(tid).pool
    # Cleanup finishes just after the response is sent; a real leak would never reach zero.
    for _ in range(100):
        if pool.checkedout() == 0:
            break
        await asyncio.sleep(0.02)
    assert pool.checkedout() == 0


# ---------- failure, rotation and revocation ----------


async def test_database_failure_gives_clear_error_without_credentials(client: AsyncClient):
    owner, database = await showroom(client, SERVER_A)
    token = owner["access_token"]
    assert (await client.get("/api/v1/customers", headers=H(token))).status_code == 200

    conn = await admin_connect(SERVER_A)
    await conn.execute(f"ALTER ROLE {APP_USER} PASSWORD 'changed-by-the-showroom-1'")
    await conn.close()
    await connection_manager.disconnect(uuid.UUID(owner["active_tenant"]["tenant_id"]))
    try:
        res = await client.get("/api/v1/customers", headers=H(token))
        assert res.status_code == 503 and res.json()["error"]["code"] == "TENANT_DB_UNAVAILABLE"
        for secret in (APP_PASSWORD, APP_USER, database, SERVER_A, "asyncpg", "postgresql"):
            assert secret not in res.text

        health = (await client.post(f"{DB}/health", headers=H(token))).json()["data"]
        assert health["healthy"] is False and health["error_code"] == "TENANT_DB_UNAVAILABLE"

        # Rotation: a wrong password is refused and the working reference is kept.
        old_ref = (await connection_row(owner["active_tenant"]["tenant_id"])).secret_reference
        bad = await client.post(f"{DB}/rotate-credentials", headers=H(token), json={"password": "still-wrong-22"})
        assert bad.status_code == 422 and "still-wrong-22" not in bad.text
        assert (await connection_row(owner["active_tenant"]["tenant_id"])).secret_reference == old_ref

        ok = await client.post(f"{DB}/rotate-credentials", headers=H(token), json={"password": "changed-by-the-showroom-1"})
        assert ok.status_code == 200, ok.text
        new_ref = (await connection_row(owner["active_tenant"]["tenant_id"])).secret_reference
        assert new_ref != old_ref
        with pytest.raises(SecretNotFound):
            get_secret_store().get(old_ref)
        assert (await client.get("/api/v1/customers", headers=H(token))).status_code == 200
    finally:
        conn = await admin_connect(SERVER_A)
        await conn.execute(f"ALTER ROLE {APP_USER} PASSWORD '{APP_PASSWORD}'")
        await conn.close()


async def test_revocation_affects_only_that_showroom_and_keeps_its_data(client: AsyncClient):
    a, db_a = await showroom(client, SERVER_A)
    b, _ = await showroom(client, SERVER_B)
    await add_customer(client, a["access_token"], "Kept After Revoke", "9" + str(uuid.uuid4().int)[:9])
    await add_customer(client, b["access_token"], "Unaffected", "9" + str(uuid.uuid4().int)[:9])
    tid = a["active_tenant"]["tenant_id"]
    ref = (await connection_row(tid)).secret_reference

    res = await client.post(f"{DB}/revoke", headers=H(a["access_token"]))
    assert res.status_code == 200 and res.json()["data"]["connection_status"] == "REVOKED"
    row = await connection_row(tid)
    assert row.secret_reference is None
    with pytest.raises(SecretNotFound):
        get_secret_store().get(ref)
    assert connection_manager.engine_for(uuid.UUID(tid)) is None

    blocked = await client.get("/api/v1/customers", headers=H(a["access_token"]))
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "TENANT_NOT_OPERATIONAL"
    assert "Unaffected" in (await client.get("/api/v1/customers", headers=H(b["access_token"]))).text

    # No connections remain, and the showroom's data is still in its own database.
    conn = await admin_connect(SERVER_A, db_a)
    assert await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=$1 AND usename=$2", db_a, APP_USER) == 0
    assert await conn.fetchval("SELECT count(*) FROM customers WHERE full_name = 'Kept After Revoke'") == 1
    await conn.close()

    # Reconnecting the same database restores access to the same data.
    again = await connect(client, a["access_token"], SERVER_A, db_a)
    assert again.status_code == 200, again.text
    assert "Kept After Revoke" in (await client.get("/api/v1/customers", headers=H(a["access_token"]))).text


async def test_failed_replacement_keeps_working_connection(client: AsyncClient):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    await add_customer(client, token, "Still Here", "9" + str(uuid.uuid4().int)[:9])
    ref = (await connection_row(owner["active_tenant"]["tenant_id"])).secret_reference
    res = await connect(client, token, SERVER_B, password="nope-wrong-password")
    assert res.status_code == 422
    row = await connection_row(owner["active_tenant"]["tenant_id"])
    assert row.connection_status == "ACTIVE" and row.secret_reference == ref
    assert "Still Here" in (await client.get("/api/v1/customers", headers=H(token))).text


async def test_replacement_moves_to_new_database_and_retires_old_secret(client: AsyncClient):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    await add_customer(client, token, "Old Database Row", "9" + str(uuid.uuid4().int)[:9])
    ref = (await connection_row(owner["active_tenant"]["tenant_id"])).secret_reference
    res = await connect(client, token, SERVER_B)
    assert res.status_code == 200, res.text
    with pytest.raises(SecretNotFound):
        get_secret_store().get(ref)
    assert "Old Database Row" not in (await client.get("/api/v1/customers", headers=H(token))).text


async def test_concurrent_setup_is_refused(client: AsyncClient):
    owner = await register(client)
    database = await new_database(SERVER_A)
    results = await asyncio.gather(*[
        client.post(f"{DB}/connect", headers=H(owner["access_token"]), json=connection_body(SERVER_A, database)) for _ in range(3)
    ])
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) >= 1 and set(codes) <= {200, 409}
    assert (await client.get(STATUS, headers=H(owner["access_token"]))).json()["data"]["operational"] is True


# ---------- migrations ----------


async def test_migrations_are_repeatable(client: AsyncClient):
    owner, database = await showroom(client, SERVER_A)
    token = owner["access_token"]
    await add_customer(client, token, "Survives Migrate", "9" + str(uuid.uuid4().int)[:9])
    for _ in range(2):
        res = await client.post(f"{DB}/migrate", headers=H(token))
        assert res.status_code == 200, res.text
        assert res.json()["data"]["schema_version"] == latest_schema_version()
    assert "Survives Migrate" in (await client.get("/api/v1/customers", headers=H(token))).text
    conn = await admin_connect(SERVER_A, database)
    assert await conn.fetchval("SELECT count(*) FROM alembic_version") == 1
    assert await conn.fetchval("SELECT count(*) FROM roles") == 3
    await conn.close()


async def test_older_database_is_upgraded_incrementally(client: AsyncClient):
    owner, database = await showroom(client, SERVER_A)
    token = owner["access_token"]
    await add_customer(client, token, "Pre Upgrade Row", "9" + str(uuid.uuid4().int)[:9])
    # Rewind to the previous version, as a showroom left behind by an update would be.
    conn = await admin_connect(SERVER_A, database)
    await conn.execute("ALTER TABLE users DROP COLUMN central_user_id")
    for col in ("vehicle_id", "model_name_snapshot", "variant_snapshot", "ex_showroom", "rto", "insurance",
                "accessories", "other_charges", "discount", "total", "created_at"):
        await conn.execute(f"ALTER TABLE quotation_items DROP COLUMN {col}")
    await conn.execute("UPDATE alembic_version SET version_num = 'a1c4e7d2f903'")
    await conn.close()

    res = await client.post(f"{DB}/migrate", headers=H(token))
    assert res.status_code == 200, res.text
    assert res.json()["data"]["schema_version"] == latest_schema_version()
    assert "Pre Upgrade Row" in (await client.get("/api/v1/customers", headers=H(token))).text
    tid = uuid.UUID(owner["active_tenant"]["tenant_id"])
    async with control_sessionmaker()() as s:
        runs = (await s.execute(select(TenantMigrationRun).where(TenantMigrationRun.tenant_id == tid)
                                .order_by(TenantMigrationRun.started_at))).scalars().all()
    assert runs[-1].from_version == "a1c4e7d2f903" and runs[-1].to_version == latest_schema_version()
    assert runs[-1].status == "SUCCEEDED"


async def test_failed_migration_rolls_back_completely_and_retry_succeeds(client: AsyncClient, monkeypatch):
    owner = await register(client)
    token = owner["access_token"]
    database = await new_database(SERVER_A)
    real_seed = provisioning._seed_required_rows

    async def explode(connection):
        raise RuntimeError(f"secret-looking driver text {APP_PASSWORD}")

    # Injected failure, after every migration has already run in the transaction.
    monkeypatch.setattr(provisioning, "_seed_required_rows", explode)
    res = await connect(client, token, SERVER_A, database)
    assert res.status_code == 422 and res.json()["error"]["code"] == "TENANT_DB_MIGRATION_FAILED"
    assert APP_PASSWORD not in res.text

    conn = await admin_connect(SERVER_A, database)
    assert await conn.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == 0  # nothing left behind
    await conn.close()
    tid = uuid.UUID(owner["active_tenant"]["tenant_id"])
    async with control_sessionmaker()() as s:
        run = (await s.execute(select(TenantMigrationRun).where(TenantMigrationRun.tenant_id == tid))).scalars().one()
        assert run.status == "FAILED" and run.error_code == "TENANT_DB_MIGRATION_FAILED"
        assert run.error_detail == "RuntimeError" and APP_PASSWORD not in str(run.error_detail)
    assert (await client.get(STATUS, headers=H(token))).json()["data"]["operational"] is False

    monkeypatch.setattr(provisioning, "_seed_required_rows", real_seed)
    retry = await connect(client, token, SERVER_A, database)
    assert retry.status_code == 200, retry.text
    assert (await client.get(STATUS, headers=H(token))).json()["data"]["operational"] is True


async def test_database_from_a_newer_release_is_refused(client: AsyncClient):
    owner, database = await showroom(client, SERVER_A)
    conn = await admin_connect(SERVER_A, database)
    await conn.execute("UPDATE alembic_version SET version_num = 'from_the_future_1'")
    await conn.close()
    res = await client.post(f"{DB}/migrate", headers=H(owner["access_token"]))
    assert res.status_code == 422 and res.json()["error"]["code"] == "TENANT_DB_SCHEMA_NEWER"


def test_upgrades_contain_no_destructive_operations():
    versions = os.path.join(os.path.dirname(__file__), "..", "alembic", "versions", "*.py")
    pattern = re.compile(r"op\.drop_(table|column)\b|DROP\s+(TABLE|COLUMN)|TRUNCATE", re.IGNORECASE)
    files = glob.glob(versions)
    assert files
    for path in files:
        source = open(path, encoding="utf-8").read().split("def downgrade")[0]
        assert not pattern.search(source), path


# ---------- secret store ----------


def test_secret_store_encrypts_and_rejects_bad_references(tmp_path):
    from cryptography.fernet import Fernet

    store = LocalEncryptedSecretStore(str(tmp_path), Fernet.generate_key().decode())
    ref = store.put(uuid.uuid4(), {"password": "topsecret"})
    assert store.get(ref) == {"password": "topsecret"}
    assert all(b"topsecret" not in p.read_bytes() for p in tmp_path.iterdir())
    for bad in ("local:../../etc/passwd", "local:not-a-uuid", "vault:abc", ""):
        with pytest.raises(SecretNotFound):
            store.get(bad)
    other = LocalEncryptedSecretStore(str(tmp_path), Fernet.generate_key().decode())
    with pytest.raises(SecretNotFound):
        other.get(ref)  # the wrong master key decrypts nothing
    store.delete(ref)
    store.delete(ref)  # idempotent
    with pytest.raises(SecretNotFound):
        store.get(ref)


def test_credentials_hidden_from_repr():
    creds = tenant_connection.TenantDbCredentials("h", 5432, "d", "u", "topsecret", "ca-data")
    assert "topsecret" not in repr(creds) and "ca-data" not in repr(creds)
    assert "postgres" not in json.dumps({"code": tenant_connection.TenantDatabaseError("TENANT_DB_UNAVAILABLE").message}).lower()
