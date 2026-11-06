"""The control-plane database follows the same rule as a single-showroom one."""
import pytest

from app.database import schema_check
from app.database.control_plane import control_engine, control_sessionmaker


async def test_current_control_schema_passes():
    control_sessionmaker()
    await schema_check.ensure_app_schema_current()


async def test_control_schema_mismatch_names_the_control_command(monkeypatch):
    monkeypatch.setattr(schema_check, "expected_revision", lambda folder: "some_other_revision")
    with pytest.raises(schema_check.SchemaNotCurrent, match="alembic_control.ini"):
        await schema_check.ensure_app_schema_current()


async def test_control_check_reads_the_control_version_table():
    current = await schema_check.current_revision(control_engine(), "control_plane_alembic_version")
    assert current == schema_check.expected_revision("alembic_control")
