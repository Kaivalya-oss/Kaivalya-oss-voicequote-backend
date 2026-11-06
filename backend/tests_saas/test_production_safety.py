"""Multi-tenant sign-in follows the same rule: published development credentials are refused outside development."""
import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests_saas.conftest import unique

REGISTER = "/api/v1/platform/showrooms/register"
LOGIN = "/api/v1/platform/auth/login"
STRONG = "Str0ng-Unpublished-Passphrase"


def body(email: str, password: str) -> dict:
    return {"showroom_name": f"Safety {unique()}", "email": email, "password": password, "full_name": "Safety Test"}


@pytest.mark.parametrize(
    "email,password",
    [("admin@dealership.com", STRONG), ("safety-new@example.com", "AdminPass123!")],
)
async def test_registration_refuses_dev_credentials_in_production(client: AsyncClient, monkeypatch, email, password):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    r = await client.post(REGISTER, json=body(email, password))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CREDENTIAL_NOT_ALLOWED"


async def test_login_refuses_published_password_in_production(client: AsyncClient, monkeypatch):
    email = f"{unique('safety')}@example.com"
    assert (await client.post(REGISTER, json=body(email, "AdminPass123!"))).status_code == 201
    assert (await client.post(LOGIN, json={"email": email, "password": "AdminPass123!"})).status_code == 200

    monkeypatch.setattr(settings, "APP_ENV", "production")
    r = await client.post(LOGIN, json={"email": email, "password": "AdminPass123!"})
    assert r.status_code == 401
    assert "access_token" not in r.text


async def test_ordinary_account_still_signs_in_in_production(client: AsyncClient, monkeypatch):
    email = f"{unique('safety')}@example.com"
    assert (await client.post(REGISTER, json=body(email, STRONG))).status_code == 201
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert (await client.post(LOGIN, json={"email": email, "password": STRONG})).status_code == 200
