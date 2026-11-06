"""Passwords, tokens and other submitted values must never reach logs or error responses."""
import ast
import logging
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import session as session_module
from app.main import app
from app.services.auth_service import AuthService
from tests.conftest import TestingAsyncSessionLocal

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def secret() -> str:
    return "Sensitive-Value-" + uuid.uuid4().hex


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


@pytest.fixture
async def raising_client():
    # The real server's view: an exception that escapes the app here would be logged by uvicorn.
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=True), base_url="http://testserver") as ac:
        yield ac


@pytest.mark.parametrize(
    "path,field",
    [
        ("/api/v1/auth/login", "password"),
        ("/api/v1/auth/register", "password"),
        ("/api/v1/auth/refresh", "refresh_token"),
    ],
)
async def test_validation_failure_never_logs_or_returns_the_values(client: AsyncClient, logs, path, field):
    value = secret()
    # A wrong type for the secret field forces a validation error that carries the value.
    for body in ({field: {"nested": value}}, {field: [value]}, {"email": "not-an-email", field: {"nested": value}, "full_name": "X"}):
        res = await client.post(path, json=body)
        assert res.status_code == 422
        assert value not in res.text
    assert value not in logs.text
    assert "Validation error on POST" in logs.text


async def test_malformed_json_body_is_not_logged(client: AsyncClient, logs):
    value = secret()
    res = await client.post("/api/v1/auth/login", content='{"password": "' + value + '"', headers={"Content-Type": "application/json"})
    assert res.status_code == 422
    assert value not in logs.text and value not in res.text


async def test_query_string_values_are_not_logged_by_the_app(client: AsyncClient, logs):
    value = secret()
    await client.get(f"/api/v1/customers?token={value}")
    # httpx here is this test's own client, logging the URL it sent. Everything else is the server side.
    assert not [r for r in logs.records if value in r.getMessage() and r.name != "httpx"]


def test_server_access_log_is_silenced():
    """uvicorn's access log records query strings, so setup_logging must keep it off."""
    from app.core.logging import setup_logging

    setup_logging()
    assert logging.getLogger("uvicorn.access").getEffectiveLevel() >= logging.WARNING


async def test_unhandled_error_logs_only_its_class(raising_client: AsyncClient, logs, monkeypatch):
    value = secret()

    async def explode(self, *args, **kwargs):
        raise RuntimeError(f"failed while handling password={value}")

    monkeypatch.setattr(AuthService, "login", explode)
    res = await raising_client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "x"})
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert value not in res.text and value not in logs.text
    assert "Unhandled RuntimeError" in logs.text


async def test_database_error_never_exposes_sql_parameters(raising_client: AsyncClient, logs, monkeypatch):
    value = secret()

    async def explode(self, *args, **kwargs):
        raise IntegrityError("INSERT INTO users (password_hash) VALUES ($1)", {"password_hash": value}, Exception("duplicate"), hide_parameters=False)

    monkeypatch.setattr(AuthService, "login", explode)
    res = await raising_client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "x"})
    assert res.status_code == 500
    assert value not in res.text and value not in logs.text


async def test_domain_error_message_stays_out_of_logs(client: AsyncClient, logs):
    res = await client.post("/api/v1/auth/register", json={"email": "admin@dealership.com", "full_name": "Admin Copy", "password": "Long-Enough-Password-1"})
    assert res.status_code == 409
    assert "USER_ALREADY_EXISTS" in logs.text
    assert "admin@dealership.com" not in logs.text


async def test_failed_login_doesnt_log_the_attempted_password(client: AsyncClient, logs):
    value = secret()
    assert (await client.post("/api/v1/auth/login", json={"email": "admin@dealership.com", "password": value})).status_code == 401
    assert value not in logs.text


async def test_real_database_error_message_hides_parameters():
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    assert session_module.engine.sync_engine.hide_parameters is True
    # The real behaviour of an engine configured the same way.
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], hide_parameters=True)
    value = secret()
    try:
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as err:
                await conn.execute(text("SELECT :v::int"), {"v": value})
        assert value not in str(err.value)
    finally:
        await engine.dispose()


def _python_files():
    for folder, _, files in os.walk(os.path.join(BACKEND, "app")):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(folder, name)


RISKY_NAMES = {"e", "exc", "err", "error", "exception", "password", "token", "secret", "body", "credentials", "creds", "payload", "req"}
RISKY_METHODS = {"errors", "body", "json"}
SAFE_ATTRIBUTES = {"code", "id"}


def _unsafe_uses(node) -> list[str]:
    """Names and calls in a log argument that could carry data. type(x).__name__ and x.code are safe."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "type":
        return []
    if isinstance(node, ast.Attribute) and node.attr in SAFE_ATTRIBUTES:
        return []
    found = []
    if isinstance(node, ast.Name) and node.id in RISKY_NAMES:
        found.append(node.id)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in RISKY_METHODS:
        found.append(node.func.attr + "()")
    for child in ast.iter_child_nodes(node):
        found.extend(_unsafe_uses(child))
    return found


def test_no_log_call_formats_an_exception_or_request_data():
    """Reads every logging call in the application, so a new unsafe one fails the build."""
    problems = []
    for path in _python_files():
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            target = node.func.value
            if not (isinstance(target, ast.Name) and target.id in ("logger", "logging")):
                continue
            if node.func.attr == "exception":
                problems.append(f"{path}:{node.lineno} uses logger.exception, which prints the message")
            for argument in node.args:
                problems.extend(f"{path}:{node.lineno} logs {use}" for use in _unsafe_uses(argument))
    assert problems == []


def test_the_log_checker_really_finds_unsafe_calls():
    for unsafe in ('logger.error(f"failed: {e}")', 'logger.warning("x %s", password)', 'logger.warning(f"{exc.errors()}")', 'logger.info(f"{req}")'):
        call = ast.parse(unsafe).body[0].value
        assert any(_unsafe_uses(a) for a in call.args), unsafe
    for safe in ('logger.error(f"failed: {type(e).__name__}")', 'logger.warning("%s", exc.code)'):
        call = ast.parse(safe).body[0].value
        assert not any(_unsafe_uses(a) for a in call.args), safe


def test_engines_hide_parameters():
    for name in ("database/session.py", "database/control_plane.py", "database/connection_manager.py", "platform_admin/database.py"):
        source = open(os.path.join(BACKEND, "app", name), encoding="utf-8").read()
        assert "hide_parameters=True" in source, name
        assert "echo=True" not in source, name
