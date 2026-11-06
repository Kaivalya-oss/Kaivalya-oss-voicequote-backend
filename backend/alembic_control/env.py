"""Migrations for the central control-plane database only (see alembic/ for showroom databases)."""
import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.control_plane.models import ControlPlaneBase  # noqa: E402
import app.platform_admin.models  # noqa: E402,F401  (registers the platform admin tables)
from app.core.config import settings  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = ControlPlaneBase.metadata
VERSION_TABLE = "control_plane_alembic_version"


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, version_table=VERSION_TABLE)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = settings.CONTROL_PLANE_DATABASE_URL
    if not url:
        raise RuntimeError("CONTROL_PLANE_DATABASE_URL is not set.")
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=settings.CONTROL_PLANE_DATABASE_URL, target_metadata=target_metadata,
                      literal_binds=True, version_table=VERSION_TABLE)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async_migrations())
