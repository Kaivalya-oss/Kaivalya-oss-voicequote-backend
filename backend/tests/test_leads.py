import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_lead_state_machine_valid_transitions(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}

    c_res = await client.get("/api/v1/customers", headers=headers)
    customer_id = c_res.json()["items"][0]["id"]

    # 1. Create Lead
    lead_res = await client.post(
        "/api/v1/leads",
        json={
            "customer_id": customer_id,
            "source": "WALK_IN",
            "priority": "HIGH",
            "notes": "Testing lead flow",
        },
        headers=headers,
    )
    assert lead_res.status_code == 201
    lead_id = lead_res.json()["data"]["id"]
    assert lead_res.json()["data"]["status"] == "NEW"

    # 2. Transition NEW -> CONTACTED
    t1 = await client.post(
        f"/api/v1/leads/{lead_id}/status",
        json={"to_status": "CONTACTED", "reason": "Called customer"},
        headers=headers,
    )
    assert t1.status_code == 200
    assert t1.json()["data"]["status"] == "CONTACTED"

    # 3. Transition CONTACTED -> INTERESTED
    t2 = await client.post(
        f"/api/v1/leads/{lead_id}/status",
        json={"to_status": "INTERESTED", "reason": "Customer confirmed visit"},
        headers=headers,
    )
    assert t2.status_code == 200
    assert t2.json()["data"]["status"] == "INTERESTED"

    # 4. Transition INTERESTED -> NEGOTIATION
    t3 = await client.post(
        f"/api/v1/leads/{lead_id}/status",
        json={"to_status": "NEGOTIATION", "reason": "Discussing discounts"},
        headers=headers,
    )
    assert t3.status_code == 200
    assert t3.json()["data"]["status"] == "NEGOTIATION"

    # 5. Transition NEGOTIATION -> BOOKED
    t4 = await client.post(
        f"/api/v1/leads/{lead_id}/status",
        json={"to_status": "BOOKED", "reason": "Received booking token deposit"},
        headers=headers,
    )
    assert t4.status_code == 200
    assert t4.json()["data"]["status"] == "BOOKED"

    # 6. Transition BOOKED -> PURCHASED
    t5 = await client.post(
        f"/api/v1/leads/{lead_id}/status",
        json={"to_status": "PURCHASED", "reason": "Delivered vehicle"},
        headers=headers,
    )
    assert t5.status_code == 200
    assert t5.json()["data"]["status"] == "PURCHASED"

    # Verify audit history length
    get_lead_res = await client.get(f"/api/v1/leads/{lead_id}", headers=headers)
    assert len(get_lead_res.json()["data"]["status_history"]) >= 6


@pytest.mark.asyncio
async def test_lead_invalid_state_transition_rejected(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}

    c_res = await client.get("/api/v1/customers", headers=headers)
    customer_id = c_res.json()["items"][0]["id"]

    # 1. Create Lead in NEW status
    lead_res = await client.post(
        "/api/v1/leads",
        json={"customer_id": customer_id, "source": "ONLINE"},
        headers=headers,
    )
    lead_id = lead_res.json()["data"]["id"]

    # 2. Attempt illegal jump from NEW directly to PURCHASED
    bad_res = await client.post(
        f"/api/v1/leads/{lead_id}/status",
        json={"to_status": "PURCHASED", "reason": "Illegal transition"},
        headers=headers,
    )
    assert bad_res.status_code == 400
    assert bad_res.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
