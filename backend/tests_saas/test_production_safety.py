"""Multi-tenant sign-in follows the same rule: published development credentials are refused outside development."""
import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests_saas.conftest import create_showroom, register, unique

ACCEPT = "/api/v1/platform/invitations/accept"
LOGIN = "/api/v1/platform/auth/login"
STRONG = "Str0ng-Unpublished-Passphrase"


async def test_invitation_refuses_a_published_password_in_production(client: AsyncClient, monkeypatch):
    token, _ = await create_showroom()
    monkeypatch.setattr(settings, "APP_ENV", "production")
    r = await client.post(ACCEPT, json={"token": token, "full_name": "Safety Test", "password": "AdminPass123!"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CREDENTIAL_NOT_ALLOWED"


async def test_login_refuses_published_password_in_production(client: AsyncClient, monkeypatch):
    email = f"{unique('safety')}@example.com"
    await register(client, email=email, password="AdminPass123!")
    assert (await client.post(LOGIN, json={"email": email, "password": "AdminPass123!"})).status_code == 200

    monkeypatch.setattr(settings, "APP_ENV", "production")
    r = await client.post(LOGIN, json={"email": email, "password": "AdminPass123!"})
    assert r.status_code == 401
    assert "access_token" not in r.text


async def test_ordinary_account_still_signs_in_in_production(client: AsyncClient, monkeypatch):
    email = f"{unique('safety')}@example.com"
    await register(client, email=email, password=STRONG)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert (await client.post(LOGIN, json={"email": email, "password": STRONG})).status_code == 200
