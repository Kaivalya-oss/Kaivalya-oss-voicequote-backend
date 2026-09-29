import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_auth_login_success(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "sales@dealership.com", "password": "SalesPass123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "sales@dealership.com"
    assert data["user"]["role"]["name"] == "SALES_EXECUTIVE"


@pytest.mark.asyncio
async def test_auth_login_invalid_password(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "sales@dealership.com", "password": "WrongPassword999!"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_auth_login_nonexistent_user(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "unknown_user@example.com", "password": "AnyPassword123!"},
    )
    assert response.status_code == 401
    assert response.json()["success"] is False


@pytest.mark.asyncio
async def test_auth_me_authenticated(client: AsyncClient, sales_token: str):
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {sales_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["email"] == "sales@dealership.com"


@pytest.mark.asyncio
async def test_auth_me_unauthorized(client: AsyncClient):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["success"] is False


@pytest.mark.asyncio
async def test_auth_register_and_duplicate(client: AsyncClient):
    new_email = "new_sales_rep@dealership.com"
    req_payload = {
        "email": new_email,
        "full_name": "New Sales Rep",
        "phone": "9998887776",
        "password": "SecurePassword123!",
        "role_name": "SALES_EXECUTIVE",
    }
    # First registration
    res1 = await client.post("/api/v1/auth/register", json=req_payload)
    assert res1.status_code in [201, 409]

    # Duplicate registration should always fail with 409
    res2 = await client.post("/api/v1/auth/register", json=req_payload)
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "USER_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_auth_refresh_token(client: AsyncClient):
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@dealership.com", "password": "AdminPass123!"},
    )
    refresh_token = login_res.json()["refresh_token"]

    refresh_res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_res.status_code == 200
    data = refresh_res.json()
    assert "access_token" in data
    assert "refresh_token" in data
