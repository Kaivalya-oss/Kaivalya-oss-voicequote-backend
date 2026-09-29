import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_inventory_reserve_and_release_cycle(client: AsyncClient, inventory_token: str, sales_token: str):
    inv_headers = {"Authorization": f"Bearer {inventory_token}"}
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    # 1. List inventory units
    list_res = await client.get("/api/v1/inventory?status=AVAILABLE", headers=sales_headers)
    assert list_res.status_code == 200
    available_units = list_res.json()["items"]
    assert len(available_units) >= 1
    target_unit = available_units[0]
    unit_id = target_unit["id"]

    # 2. Reserve unit
    res_reserve = await client.post(
        f"/api/v1/inventory/{unit_id}/reserve",
        json={"notes": "Reserved for Customer Test"},
        headers=sales_headers,
    )
    assert res_reserve.status_code == 200
    assert res_reserve.json()["data"]["status"] == "RESERVED"

    # 3. Double-reserve edge case: Cannot reserve already reserved unit
    res_double = await client.post(
        f"/api/v1/inventory/{unit_id}/reserve",
        json={"notes": "Illegal double reserve attempt"},
        headers=sales_headers,
    )
    assert res_double.status_code == 400
    assert res_double.json()["error"]["code"] == "UNIT_NOT_AVAILABLE"

    # 4. Release unit back to AVAILABLE
    res_release = await client.post(
        f"/api/v1/inventory/{unit_id}/release",
        json={"notes": "Customer cancelled reservation"},
        headers=sales_headers,
    )
    assert res_release.status_code == 200
    assert res_release.json()["data"]["status"] == "AVAILABLE"

    # 5. Double-release edge case: Cannot release unit that is already AVAILABLE
    res_release_again = await client.post(
        f"/api/v1/inventory/{unit_id}/release",
        json={"notes": "Invalid release attempt"},
        headers=sales_headers,
    )
    assert res_release_again.status_code == 400
    assert res_release_again.json()["error"]["code"] == "UNIT_NOT_RESERVED"


@pytest.mark.asyncio
async def test_inventory_variant_stock_summary(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}

    # Get first variant ID
    v_res = await client.get("/api/v1/vehicles")
    variant_id = v_res.json()["items"][0]["id"]

    summary_res = await client.get(f"/api/v1/inventory/summary/{variant_id}", headers=headers)
    assert summary_res.status_code == 200
    data = summary_res.json()["data"]
    assert "total_units" in data
    assert "available_units" in data
    assert "reserved_units" in data
    assert data["total_units"] >= 0
