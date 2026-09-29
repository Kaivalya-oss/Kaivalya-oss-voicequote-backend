import os
import sys
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.core.security import create_access_token
from app.database.dependencies import get_db
from app.main import app
from app.models.user import User
from app.repositories.user_repository import UserRepository

# Test database engine with NullPool to prevent connection leakage between tests
test_engine = create_async_engine(settings.TEST_DATABASE_URL, echo=False, poolclass=pool.NullPool)
TestingAsyncSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with TestingAsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client():
    async def override_get_db():
        async with TestingAsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def admin_token() -> str:
    async with TestingAsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_email("admin@dealership.com")
        assert user is not None, "Admin user must exist in seed data"
        return create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.name, "name": user.full_name},
        )


@pytest_asyncio.fixture(scope="function")
async def sales_token() -> str:
    async with TestingAsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_email("sales@dealership.com")
        assert user is not None, "Sales user must exist in seed data"
        return create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.name, "name": user.full_name},
        )


@pytest_asyncio.fixture(scope="function")
async def inventory_token() -> str:
    async with TestingAsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_email("inventory@dealership.com")
        assert user is not None, "Inventory user must exist in seed data"
        return create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.name, "name": user.full_name},
        )


@pytest_asyncio.fixture(scope="function")
async def owner_token() -> str:
    async with TestingAsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_email("owner@dealership.com")
        assert user is not None, "Owner user must exist in seed data"
        return create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.name, "name": user.full_name},
        )
