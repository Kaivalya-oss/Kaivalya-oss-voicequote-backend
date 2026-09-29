import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_vehicles_list_and_filter(client: AsyncClient):
    # 1. Public catalog list
    res = await client.get("/api/v1/vehicles")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) >= 1

    # 2. Filter by fuel type = ELECTRIC
    ev_res = await client.get("/api/v1/vehicles?fuel_type=ELECTRIC")
    assert ev_res.status_code == 200
    ev_data = ev_res.json()
    for item in ev_data["items"]:
        assert item["fuel_type"] == "ELECTRIC"

    # 3. Filter by price range
    price_res = await client.get("/api/v1/vehicles?min_price=80000&max_price=120000")
    assert price_res.status_code == 200
    for item in price_res.json()["items"]:
        assert 80000 <= float(item["on_road_price"]) <= 120000


@pytest.mark.asyncio
async def test_vehicle_authorization_and_validation(client: AsyncClient, sales_token: str, admin_token: str):
    sales_headers = {"Authorization": f"Bearer {sales_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Fetch existing model ID
    models_res = await client.get("/api/v1/vehicles/catalog/models")
    model_id = models_res.json()["data"][0]["id"]

    new_variant = {
        "model_id": model_id,
        "name": "Activa Special Edition",
        "code": "ACTIVA-SPEC-2026",
        "engine_cc": 125,
        "fuel_type": "PETROL",
        "transmission": "AUTOMATIC",
        "ex_showroom_price": 92000.00,
        "insurance_price": 6800.00,
        "rto_price": 9500.00,
        "accessories_price": 2000.00,
    }

    # 1. Sales Executive cannot create variants (403 Forbidden)
    forbidden_res = await client.post("/api/v1/vehicles", json=new_variant, headers=sales_headers)
    assert forbidden_res.status_code == 403
    assert forbidden_res.json()["error"]["code"] == "FORBIDDEN"

    # 2. Negative price payload validation check (422)
    invalid_variant = dict(new_variant, ex_showroom_price=-5000.00)
    invalid_res = await client.post("/api/v1/vehicles", json=invalid_variant, headers=admin_headers)
    assert invalid_res.status_code == 422
    assert invalid_res.json()["error"]["code"] == "VALIDATION_ERROR"
