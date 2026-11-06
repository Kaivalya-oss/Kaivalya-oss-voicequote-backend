"""Outside development and tests, the published development accounts must be unusable."""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import Settings, settings
from app.core.dev_accounts import DEV_ACCOUNTS, dev_accounts_permitted, is_refused_dev_credential
from app.core.permissions import RoleEnum
from app.core.security import create_access_token, create_refresh_token, hash_password
from app.models.user import User
from scripts.create_admin import AdminSetupError, create_admin
from scripts.seed import seed
from tests.conftest import TestingAsyncSessionLocal

LOGIN = "/api/v1/auth/login"
NON_DEV_ENVIRONMENTS = ["production", "PRODUCTION", " production ", "staging", "prod", "anything-else", ""]
STRONG = "Str0ng-Unpublished-Passphrase"


@pytest.fixture(params=NON_DEV_ENVIRONMENTS)
def non_dev(request, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", request.param)


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")


@pytest.mark.parametrize("env", ["development", "test", "Development"])
def test_only_development_and_test_permit_dev_accounts(monkeypatch, env):
    monkeypatch.setattr(settings, "APP_ENV", env)
    assert dev_accounts_permitted()
    assert not is_refused_dev_credential("admin@dealership.com", "AdminPass123!")


def test_every_other_environment_refuses_dev_accounts(non_dev):
    assert not dev_accounts_permitted()
    for email, _, _, password, _ in DEV_ACCOUNTS:
        assert is_refused_dev_credential(email)
        assert is_refused_dev_credential(email.upper())
        assert is_refused_dev_credential("someone@example.com", password)
    assert not is_refused_dev_credential("someone@example.com", STRONG)


@pytest.mark.parametrize("account", DEV_ACCOUNTS, ids=lambda a: a[0])
async def test_production_login_refuses_every_dev_account(client: AsyncClient, production, account):
    email, _, _, password, _ = account
    r = await client.post(LOGIN, json={"email": email, "password": password})
    assert r.status_code == 401
    assert "access_token" not in r.text


async def test_refusal_is_the_same_as_a_wrong_password(client: AsyncClient, production):
    refused = await client.post(LOGIN, json={"email": "admin@dealership.com", "password": "AdminPass123!"})
    wrong = await client.post(LOGIN, json={"email": "admin@dealership.com", "password": "not-the-password"})
    assert refused.status_code == wrong.status_code == 401
    assert refused.json()["error"]["message"] == wrong.json()["error"]["message"]


async def test_dev_accounts_sign_in_during_development(client: AsyncClient):
    r = await client.post(LOGIN, json={"email": "admin@dealership.com", "password": "AdminPass123!"})
    assert r.status_code == 200


async def _user_id(email: str) -> int:
    async with TestingAsyncSessionLocal() as session:
        return (await session.execute(select(User.id).where(User.email == email))).scalar_one()


async def test_production_refuses_tokens_already_issued_to_dev_accounts(client: AsyncClient, admin_token: str, production):
    r = await client.get("/api/v1/customers", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 401


async def test_production_refuses_refresh_for_dev_accounts(client: AsyncClient, production):
    token = create_refresh_token(subject=await _user_id("admin@dealership.com"))
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    assert r.status_code == 401
    assert "access_token" not in r.text


async def test_production_refuses_a_published_password_on_any_account(client: AsyncClient, production):
    """A real account that was given a published password cannot use it."""
    email = "real.manager.published@example.com"
    async with TestingAsyncSessionLocal() as session:
        if not (await session.execute(select(User).where(User.email == email))).scalars().first():
            role = await _role(session, RoleEnum.ADMIN.value)
            session.add(User(email=email, full_name="Real", password_hash=hash_password("AdminPass123!"), role_id=role, is_active=True))
            await session.commit()
    r = await client.post(LOGIN, json={"email": email, "password": "AdminPass123!"})
    assert r.status_code == 401


async def _role(session, name: str) -> int:
    from app.models.user import Role

    return (await session.execute(select(Role.id).where(Role.name == name))).scalar_one()


@pytest.mark.parametrize(
    "email,password",
    [("admin@dealership.com", STRONG), ("new.person.prod@example.com", "SalesPass123!")],
)
async def test_production_registration_refuses_dev_credentials(client: AsyncClient, production, email, password):
    r = await client.post("/api/v1/auth/register", json={"email": email, "full_name": "New Person", "password": password})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CREDENTIAL_NOT_ALLOWED"
    async with TestingAsyncSessionLocal() as session:
        assert (await session.execute(select(func.count(User.id)).where(User.email == "new.person.prod@example.com"))).scalar_one() == 0


async def _user_count() -> int:
    async with TestingAsyncSessionLocal() as session:
        return (await session.execute(select(func.count(User.id)))).scalar_one()


async def test_seed_refuses_without_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "SEED_DEV_DATA", False)
    before = await _user_count()
    with pytest.raises(SystemExit):
        await seed()
    assert await _user_count() == before


async def test_seed_refuses_in_production_even_with_opt_in(production, monkeypatch):
    monkeypatch.setattr(settings, "SEED_DEV_DATA", True)
    before = await _user_count()
    with pytest.raises(SystemExit):
        await seed()
    assert await _user_count() == before


@pytest.mark.parametrize("env", ["production", "staging", "prod"])
def test_app_refuses_to_start_with_seeding_enabled_outside_development(env):
    with pytest.raises(ValidationError, match="SEED_DEV_DATA"):
        Settings(_env_file=None, APP_ENV=env, JWT_SECRET_KEY="k" * 40, SEED_DEV_DATA=True)


def test_seeding_is_off_by_default():
    assert Settings(_env_file=None, APP_ENV="development").SEED_DEV_DATA is False


@pytest.mark.parametrize(
    "email,password",
    [
        ("admin@dealership.com", STRONG),
        ("new.admin@example.com", "AdminPass123!"),
        ("new.admin@example.com", "short"),
        ("not-an-email", STRONG),
    ],
)
async def test_create_admin_refuses_published_or_weak_values(email, password):
    """Refused in every environment, including this development one."""
    before = await _user_count()
    with pytest.raises(AdminSetupError):
        await create_admin(email, "New Admin", password, session_factory=TestingAsyncSessionLocal)
    assert await _user_count() == before


async def test_create_admin_then_sign_in_under_production(client: AsyncClient, production):
    email = "first.admin.prod@example.com"
    async with TestingAsyncSessionLocal() as session:
        exists = (await session.execute(select(User).where(User.email == email))).scalars().first()
    outcome = await create_admin(email, "First Admin", STRONG, reset_password=bool(exists), session_factory=TestingAsyncSessionLocal)
    assert outcome in ("created", "password reset")

    r = await client.post(LOGIN, json={"email": email, "password": STRONG})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"]["name"] == RoleEnum.ADMIN.value

    token = create_access_token(subject=r.json()["user"]["id"])
    assert (await client.get("/api/v1/customers", headers={"Authorization": f"Bearer {token}"})).status_code == 200


async def test_create_admin_does_not_overwrite_an_account_without_the_flag():
    email = "existing.admin@example.com"
    async with TestingAsyncSessionLocal() as session:
        exists = (await session.execute(select(User).where(User.email == email))).scalars().first()
    if not exists:
        await create_admin(email, "Existing", STRONG, session_factory=TestingAsyncSessionLocal)
    with pytest.raises(AdminSetupError, match="already exists"):
        await create_admin(email, "Existing", "Another-Long-Passphrase", session_factory=TestingAsyncSessionLocal)


def test_seed_log_never_contains_passwords():
    import scripts.seed as seed_module

    source = open(seed_module.__file__, encoding="utf-8").read()
    assert "[Password" not in source
    for _, _, _, password, _ in DEV_ACCOUNTS:
        assert password not in source
