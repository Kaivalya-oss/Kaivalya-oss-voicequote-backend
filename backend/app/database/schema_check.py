"""Refuse to serve a database whose schema does not match this release.

Migrations are an explicit deployment step (docs/DEPLOYMENT_VERIFICATION.md), never a side effect of
starting a container. This check only reads, so starting a new image cannot change a database.
"""
import os
from typing import Optional

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings
from app.database.session import engine as single_showroom_engine

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class SchemaNotCurrent(RuntimeError):
    pass


def expected_revision(script_folder: str) -> str:
    config = Config()
    config.set_main_option("script_location", os.path.join(_BACKEND_ROOT, script_folder))
    return ScriptDirectory.from_config(config).get_current_head()


async def current_revision(engine: AsyncEngine, version_table: str) -> Optional[str]:
    async with engine.connect() as conn:
        try:
            return (await conn.execute(text(f"SELECT version_num FROM {version_table}"))).scalar_one_or_none()
        except ProgrammingError:
            return None  # never migrated


async def ensure_schema_current(engine: AsyncEngine, script_folder: str, version_table: str, migrate_command: str) -> None:
    expected = expected_revision(script_folder)
    current = await current_revision(engine, version_table)
    if current != expected:
        raise SchemaNotCurrent(
            f"Database schema is at '{current or 'no version'}' but this release needs '{expected}'. "
            f"Nothing was changed. Back up the database, then run: {migrate_command}"
        )


async def ensure_app_schema_current() -> None:
    if settings.MULTI_TENANT_ENABLED:
        # Showroom databases are checked per showroom; this is only the central database.
        from app.database.control_plane import control_engine

        await ensure_schema_current(
            control_engine(), "alembic_control", "control_plane_alembic_version", "alembic -c alembic_control.ini upgrade head"
        )
    else:
        await ensure_schema_current(single_showroom_engine, "alembic", "alembic_version", "alembic upgrade head")
