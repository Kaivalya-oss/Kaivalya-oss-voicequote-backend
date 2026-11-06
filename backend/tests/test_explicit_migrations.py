"""Starting the app must never change a database: migrations are a separate, explicit step."""
import os
import re

import pytest
from sqlalchemy import text

from app import main
from app.database import schema_check
from tests.conftest import test_engine

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def source(name: str) -> str:
    path = os.path.join(BACKEND, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} is not part of this image")
    return open(path, encoding="utf-8").read()


async def test_current_schema_passes():
    await schema_check.ensure_schema_current(test_engine, "alembic", "alembic_version", "migrate")


async def test_old_or_new_schema_is_refused_and_nothing_changes(monkeypatch):
    before = await schema_check.current_revision(test_engine, "alembic_version")
    monkeypatch.setattr(schema_check, "expected_revision", lambda folder: "some_other_revision")
    with pytest.raises(schema_check.SchemaNotCurrent) as err:
        await schema_check.ensure_schema_current(test_engine, "alembic", "alembic_version", "alembic upgrade head")
    assert "alembic upgrade head" in str(err.value)
    assert before in str(err.value)
    assert await schema_check.current_revision(test_engine, "alembic_version") == before


async def test_unmigrated_database_is_refused():
    with pytest.raises(schema_check.SchemaNotCurrent, match="no version"):
        await schema_check.ensure_schema_current(test_engine, "alembic", "version_table_that_does_not_exist", "migrate")


async def test_startup_stops_before_serving_on_schema_mismatch(monkeypatch):
    started = []
    monkeypatch.setattr(schema_check, "single_showroom_engine", test_engine)
    monkeypatch.setattr(schema_check, "expected_revision", lambda folder: "some_other_revision")
    monkeypatch.setattr(main, "start_scheduler", lambda: started.append(True))
    with pytest.raises(schema_check.SchemaNotCurrent):
        async with main.lifespan(main.app):
            pytest.fail("the application started")
    assert not started


async def test_startup_runs_no_migration_commands(monkeypatch):
    """A successful start issues no schema change: version and table list are untouched."""
    monkeypatch.setattr(schema_check, "single_showroom_engine", test_engine)
    monkeypatch.setattr(main, "engine", test_engine)
    tables = text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
    async with test_engine.connect() as conn:
        before = (await conn.execute(tables)).scalar_one()
    version = await schema_check.current_revision(test_engine, "alembic_version")
    async with main.lifespan(main.app):
        pass
    async with test_engine.connect() as conn:
        assert (await conn.execute(tables)).scalar_one() == before
    assert await schema_check.current_revision(test_engine, "alembic_version") == version


def test_image_starts_only_the_server():
    dockerfile = source("Dockerfile")
    assert 'CMD ["uvicorn", "app.main:app"' in dockerfile
    assert not re.search(r"^(CMD|ENTRYPOINT).*(alembic|seed)", dockerfile, re.MULTILINE)
    assert "start.sh" not in dockerfile
    assert not os.path.exists(os.path.join(BACKEND, "scripts", "start.sh"))


def test_compose_runs_migrations_only_on_request():
    compose = source("docker-compose.yml")
    backend = compose.split("  backend:")[1].split("\n  migrate:")[0]
    assert "alembic" not in backend and "seed" not in backend
    migrate = compose.split("\n  migrate:")[1].split("\n  dev-seed:")[0]
    assert 'profiles: ["deploy"]' in migrate
    seed = compose.split("\n  dev-seed:")[1].split("\nvolumes:")[0]
    assert 'profiles: ["dev"]' in seed
