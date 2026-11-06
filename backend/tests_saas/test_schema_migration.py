"""Older showroom schemas: clear responses for staff, explicit status, controlled migration, safe recovery."""
import uuid

import pytest
from alembic import command
from httpx import AsyncClient
from sqlalchemy import select, text

from app.control_plane.models import TenantConnection, TenantMigrationRun
from app.database import provisioning
from app.database.connection_manager import build_engine, connection_manager
from app.database.control_plane import control_sessionmaker
from app.database.migration_orchestrator import migrate_all_required, migrate_showroom, plan_migrations
from app.database.provisioning import latest_schema_version
from app.database.secrets import get_secret_store
from app.database.tenant_connection import TenantDbCredentials
from app.platform_admin.models import AuditEvent
from tests_saas.conftest import H, invite_and_join
from tests_saas.showroom_dbs import SERVER_A, SERVER_B
from tests_saas.test_phase2_tenant_databases import DB, STATUS, add_customer, connection_row, showroom

OLD_REVISION = "a1c4e7d2f903"


def tenant_of(owner: dict) -> uuid.UUID:
    return uuid.UUID(owner["active_tenant"]["tenant_id"])


async def put_on_older_schema(owner: dict) -> None:
    """Downgrade the showroom's real database, as if a new release had just shipped."""
    tenant = tenant_of(owner)
    row = await connection_row(str(tenant))
    creds = TenantDbCredentials.from_secret(get_secret_store().get(row.secret_reference))
    engine = build_engine(creds, 1)

    def downgrade(connection):
        config = provisioning._alembic_config()
        config.attributes["connection"] = connection
        command.downgrade(config, OLD_REVISION)

    async with engine.begin() as conn:
        await conn.run_sync(downgrade)
    await engine.dispose()
    async with control_sessionmaker()() as s:
        (await s.execute(select(TenantConnection).where(TenantConnection.tenant_id == tenant))).scalar_one().schema_version = OLD_REVISION
        await s.commit()
    # A deployment restarts the process, so no engine has checked the schema yet.
    await connection_manager.disconnect(tenant)


async def database_version(owner: dict) -> str:
    row = await connection_row(str(tenant_of(owner)))
    creds = TenantDbCredentials.from_secret(get_secret_store().get(row.secret_reference))
    engine = build_engine(creds, 1)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    finally:
        await engine.dispose()


async def older_showroom(client: AsyncClient) -> tuple[dict, dict]:
    owner, _ = await showroom(client, SERVER_A)
    sales = await invite_and_join(client, owner["access_token"], "SALESPERSON")
    await add_customer(client, owner["access_token"], "Older Schema Customer", "9345680001")
    await put_on_older_schema(owner)
    return owner, sales


@pytest.mark.parametrize("path", ["/api/v1/customers", "/api/v1/quotations", "/api/v1/leads", "/api/v1/vehicles", "/api/v1/dashboard/summary", "/api/v1/followups"])
async def test_staff_get_a_clear_503_not_a_500(client: AsyncClient, path):
    owner, sales = await older_showroom(client)
    for member in (owner, sales):
        res = await client.get(path, headers=H(member["access_token"]))
        assert res.status_code == 503, (path, res.status_code, res.text[:160])
        assert res.json()["error"]["code"] == "TENANT_DB_SCHEMA_OUTDATED"
        for leak in ("central_user_id", "column", "alembic", OLD_REVISION):
            assert leak not in res.text


async def test_writes_are_refused_the_same_way(client: AsyncClient):
    owner, sales = await older_showroom(client)
    res = await client.post("/api/v1/customers", headers=H(sales["access_token"]), json={"full_name": "New", "phone": "9345680002", "address": "X"})
    assert res.status_code == 503 and res.json()["error"]["code"] == "TENANT_DB_SCHEMA_OUTDATED"


async def test_no_unhandled_error_is_logged(client: AsyncClient, caplog):
    owner, _ = await older_showroom(client)
    caplog.clear()
    await client.get("/api/v1/customers", headers=H(owner["access_token"]))
    assert "Unhandled" not in caplog.text and "UndefinedColumn" not in caplog.text


async def test_status_tells_the_owner_and_staff_what_is_happening(client: AsyncClient):
    owner, sales = await older_showroom(client)
    for_owner = (await client.get(STATUS, headers=H(owner["access_token"]))).json()["data"]
    assert for_owner["migration_required"] is True and for_owner["next_step"] == "RUN_MIGRATION"
    assert for_owner["schema_version"] == OLD_REVISION and for_owner["required_schema_version"] == latest_schema_version()
    for_staff = (await client.get(STATUS, headers=H(sales["access_token"]))).json()["data"]
    assert for_staff["next_step"] == "WAIT_FOR_OWNER"


async def test_health_reports_the_older_schema_and_records_it(client: AsyncClient):
    owner, _ = await older_showroom(client)
    res = await client.post(f"{DB}/health", headers=H(owner["access_token"]))
    assert res.status_code == 200
    assert res.json()["data"]["healthy"] is False and res.json()["data"]["error_code"] == "TENANT_DB_SCHEMA_OUTDATED"
    row = await connection_row(str(tenant_of(owner)))
    assert row.last_error_code == "TENANT_DB_SCHEMA_OUTDATED" and row.schema_version == OLD_REVISION


async def test_owner_migration_restores_service_and_keeps_data(client: AsyncClient):
    owner, sales = await older_showroom(client)
    res = await client.post(f"{DB}/migrate", headers=H(owner["access_token"]))
    assert res.status_code == 200, res.text
    assert await database_version(owner) == latest_schema_version()
    listed = await client.get("/api/v1/customers", headers=H(owner["access_token"]))
    assert listed.status_code == 200 and "Older Schema Customer" in listed.text
    assert (await client.get("/api/v1/leads", headers=H(sales["access_token"]))).status_code == 200
    assert (await client.get(STATUS, headers=H(owner["access_token"]))).json()["data"]["migration_required"] is False


async def test_staff_cannot_run_migrations(client: AsyncClient):
    owner, sales = await older_showroom(client)
    assert (await client.post(f"{DB}/migrate", headers=H(sales["access_token"]))).status_code == 403
    assert await database_version(owner) == OLD_REVISION


async def test_plan_reports_and_changes_nothing(client: AsyncClient):
    owner, _ = await older_showroom(client)
    planned = {p.tenant_id: p for p in await plan_migrations()}
    item = planned[tenant_of(owner)]
    assert item.required is True and item.schema_version == OLD_REVISION
    assert await database_version(owner) == OLD_REVISION


async def test_command_migrates_one_showroom_and_records_it(client: AsyncClient):
    owner, _ = await older_showroom(client)
    other, _ = await showroom(client, SERVER_B)
    result = await migrate_showroom(tenant_of(owner))
    assert result.outcome == "SUCCEEDED" and result.schema_version == latest_schema_version()
    row = await connection_row(str(tenant_of(owner)))
    assert row.connection_status == "ACTIVE" and row.schema_version == latest_schema_version()
    assert (await client.get("/api/v1/customers", headers=H(owner["access_token"]))).status_code == 200
    async with control_sessionmaker()() as s:
        runs = (await s.execute(select(TenantMigrationRun).where(TenantMigrationRun.tenant_id == tenant_of(owner), TenantMigrationRun.from_version == OLD_REVISION))).scalars().all()
        assert [r.status for r in runs] == ["SUCCEEDED"]
        audits = list((await s.execute(select(AuditEvent.action).where(AuditEvent.tenant_id == tenant_of(owner)))).scalars())
    assert "SHOWROOM_MIGRATION_SUCCEEDED" in audits


async def test_failure_rolls_back_stays_503_and_can_be_retried(client: AsyncClient, monkeypatch):
    owner, sales = await older_showroom(client)
    real = provisioning._seed_required_rows

    async def explode(connection):
        raise RuntimeError("injected failure with secret-looking text")

    monkeypatch.setattr(provisioning, "_seed_required_rows", explode)
    result = await migrate_showroom(tenant_of(owner))
    assert result.outcome == "FAILED" and result.error_code == "TENANT_DB_MIGRATION_FAILED"
    # Rolled back: the database is still on the older schema, not half-changed.
    assert await database_version(owner) == OLD_REVISION
    row = await connection_row(str(tenant_of(owner)))
    assert row.connection_status == "ACTIVE" and row.last_error_code == "TENANT_DB_MIGRATION_FAILED" and row.schema_version == OLD_REVISION
    res = await client.get("/api/v1/customers", headers=H(sales["access_token"]))
    assert res.status_code == 503 and res.json()["error"]["code"] == "TENANT_DB_SCHEMA_OUTDATED"

    monkeypatch.setattr(provisioning, "_seed_required_rows", real)
    retried = await migrate_showroom(tenant_of(owner))
    assert retried.outcome == "SUCCEEDED"
    assert (await client.get("/api/v1/customers", headers=H(sales["access_token"]))).status_code == 200
    assert (await connection_row(str(tenant_of(owner)))).last_error_code is None


async def test_one_failure_doesnt_stop_or_touch_the_others(client: AsyncClient, monkeypatch):
    first, _ = await older_showroom(client)
    second, _ = await showroom(client, SERVER_B)
    await put_on_older_schema(second)
    bad = await connection_row(str(tenant_of(first)))
    real_get = get_secret_store().get

    class Store:
        def get(self, reference):
            if reference == bad.secret_reference:
                from app.database.secrets import SecretNotFound
                raise SecretNotFound()
            return real_get(reference)

    from app.database import migration_orchestrator

    monkeypatch.setattr(migration_orchestrator, "get_secret_store", lambda: Store())
    results = {r.tenant_id: r for r in await migrate_all_required()}
    assert results[tenant_of(first)].outcome == "FAILED"
    assert results[tenant_of(second)].outcome == "SUCCEEDED"
    assert await database_version(second) == latest_schema_version()
    assert await database_version(first) == OLD_REVISION


async def test_staff_get_maintenance_response_during_migration(client: AsyncClient):
    owner, sales = await older_showroom(client)
    async with control_sessionmaker()() as s:
        (await s.execute(select(TenantConnection).where(TenantConnection.tenant_id == tenant_of(owner)))).scalar_one().connection_status = "PROVISIONING"
        await s.commit()
    res = await client.get("/api/v1/customers", headers=H(sales["access_token"]))
    assert res.status_code == 503 and res.json()["error"]["code"] == "TENANT_DB_MAINTENANCE"
    view = (await client.get(STATUS, headers=H(owner["access_token"]))).json()["data"]
    assert view["next_step"] == "WAIT_FOR_MAINTENANCE"
    # Two migrations can't run together.
    assert (await migrate_showroom(tenant_of(owner))).error_code == "DATABASE_SETUP_IN_PROGRESS"


async def test_interrupted_migration_can_be_recovered(client: AsyncClient):
    owner, _ = await older_showroom(client)
    async with control_sessionmaker()() as s:
        await s.execute(text("UPDATE tenant_connections SET connection_status = 'PROVISIONING', updated_at = now() - interval '1 hour' WHERE tenant_id = :t"), {"t": tenant_of(owner)})
        await s.commit()
    result = await migrate_showroom(tenant_of(owner))
    assert result.outcome == "SUCCEEDED"
    assert (await connection_row(str(tenant_of(owner)))).connection_status == "ACTIVE"


async def test_older_showroom_doesnt_affect_another(client: AsyncClient):
    await older_showroom(client)
    other, _ = await showroom(client, SERVER_B)
    assert (await client.get("/api/v1/customers", headers=H(other["access_token"]))).status_code == 200


def test_admin_process_has_no_route_to_migrations():
    from app.platform_admin.main import app

    assert not [p for p in app.openapi()["paths"] if "migrat" in p]
