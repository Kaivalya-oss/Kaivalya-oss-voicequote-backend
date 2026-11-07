"""EMI needs a signed-in user, the development JWT secret is for development only, and client IPs can't be forged."""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from starlette.requests import Request

from app.api.dependencies import get_client_ip
from app.core.config import DEV_FALLBACK_JWT_SECRET, INSECURE_JWT_SECRETS, Settings, settings

EMI = "/api/v1/finance/calculate-emi"
LOAN = {"principal": 100000.00, "annual_interest_rate": 10.50, "tenure_months": 24}
STRONG = "s" * 48


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_emi_requires_a_signed_in_user(client: AsyncClient):
    assert (await client.post(EMI, json=LOAN)).status_code == 401
    assert (await client.post(EMI, json=LOAN, headers=H("not-a-token"))).status_code == 401


async def test_emi_works_for_staff(client: AsyncClient, sales_token: str):
    r = await client.post(EMI, json=LOAN, headers=H(sales_token))
    assert r.status_code == 200
    assert float(r.json()["data"]["monthly_emi"]) > 0


@pytest.mark.parametrize("env", ["production", "staging", "prod", "Staging", "anything-else", ""])
def test_jwt_secret_is_required_outside_development_and_test(env):
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, APP_ENV=env, JWT_SECRET_KEY="")


@pytest.mark.parametrize("env", ["staging", "prod"])
@pytest.mark.parametrize("secret", sorted(INSECURE_JWT_SECRETS))
def test_published_secrets_are_refused_outside_development_and_test(env, secret):
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, APP_ENV=env, JWT_SECRET_KEY=secret)


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_short_secret_is_refused_outside_development_and_test(env):
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, APP_ENV=env, JWT_SECRET_KEY="short-secret")


@pytest.mark.parametrize("env", ["staging", "production"])
def test_strong_secret_is_kept(env):
    assert Settings(_env_file=None, APP_ENV=env, JWT_SECRET_KEY=STRONG).JWT_SECRET_KEY == STRONG


@pytest.mark.parametrize("env", ["development", "test", "Development"])
def test_fallback_only_in_development_and_test(env):
    assert Settings(_env_file=None, APP_ENV=env, JWT_SECRET_KEY="").JWT_SECRET_KEY == DEV_FALLBACK_JWT_SECRET


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (peer, 40000)})


async def test_forwarded_header_ignored_without_trusted_proxies(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "")
    assert await get_client_ip(_request("203.0.113.9", "198.51.100.1")) == "203.0.113.9"


async def test_forwarded_header_ignored_from_an_untrusted_peer(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.0.0.2")
    assert await get_client_ip(_request("203.0.113.9", "198.51.100.1")) == "203.0.113.9"


async def test_trusted_proxy_reports_the_client_it_saw(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.0.0.2")
    assert await get_client_ip(_request("10.0.0.2", "198.51.100.1")) == "198.51.100.1"


async def test_client_supplied_hops_before_the_proxy_are_ignored(monkeypatch):
    # The client sent "X-Forwarded-For: 1.2.3.4" itself; the proxy appended the real address.
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.0.0.2, 10.0.0.3")
    assert await get_client_ip(_request("10.0.0.2", "1.2.3.4, 198.51.100.1, 10.0.0.3")) == "198.51.100.1"


async def test_trusted_proxy_without_a_header_reports_itself(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.0.0.2")
    assert await get_client_ip(_request("10.0.0.2")) == "10.0.0.2"


async def test_login_audit_does_not_record_a_forged_address(client: AsyncClient):
    from sqlalchemy import text

    from tests.conftest import TestingAsyncSessionLocal

    r = await client.post("/api/v1/auth/login", json={"email": "sales@dealership.com", "password": "SalesPass123!"},
                          headers={"X-Forwarded-For": "6.6.6.6"})
    assert r.status_code == 200
    async with TestingAsyncSessionLocal() as session:
        recorded = (await session.execute(text("SELECT ip_address FROM audit_logs WHERE action = 'USER_LOGIN' ORDER BY id DESC LIMIT 1"))).scalar_one()
    assert recorded != "6.6.6.6"
