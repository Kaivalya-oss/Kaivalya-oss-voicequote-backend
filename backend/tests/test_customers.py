import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_customer_crud_and_duplicate_check(client: AsyncClient, sales_token: str, admin_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    test_phone = "9112233445"
    cust_data = {
        "full_name": "Karan Singhal",
        "phone": test_phone,
        "email": "karan.singhal@example.com",
        "city": "Pune",
        "state": "Maharashtra",
        "budget": 110000.00,
    }

    # 1. Create Customer
    res = await client.post("/api/v1/customers", json=cust_data, headers=headers)
    assert res.status_code in [201, 409]
    if res.status_code == 201:
        created = res.json()["data"]
        cust_id = created["id"]
        assert created["full_name"] == "Karan Singhal"
        assert created["phone"] == test_phone

        # 2. Duplicate phone check
        dup_res = await client.post("/api/v1/customers", json=cust_data, headers=headers)
        assert dup_res.status_code == 409
        assert dup_res.json()["error"]["code"] == "CUSTOMER_PHONE_EXISTS"

        # 3. Retrieve Customer
        get_res = await client.get(f"/api/v1/customers/{cust_id}", headers=headers)
        assert get_res.status_code == 200
        assert get_res.json()["data"]["id"] == cust_id

        # 4. Search Customer
        search_res = await client.get(f"/api/v1/customers?search={test_phone}", headers=headers)
        assert search_res.status_code == 200
        assert search_res.json()["total"] >= 1

        # 5. Update Customer
        update_res = await client.put(
            f"/api/v1/customers/{cust_id}",
            json={"city": "Nagpur", "budget": 125000.00},
            headers=headers,
        )
        assert update_res.status_code == 200
        assert update_res.json()["data"]["city"] == "Nagpur"

        # 6. Delete Customer
        del_res = await client.delete(f"/api/v1/customers/{cust_id}", headers=admin_headers)
        assert del_res.status_code == 200
        assert del_res.json()["success"] is True


@pytest.mark.asyncio
async def test_customer_list_pagination(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}
    res = await client.get("/api/v1/customers?page=1&page_size=2", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["page"] == 1
    assert data["page_size"] == 2
