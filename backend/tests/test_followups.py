from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_followup_scheduling_and_completion(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}

    # Create our own open lead (an arbitrary existing lead may be closed, and closed leads
    # correctly refuse new follow-ups)
    import time
    cust = await client.post(
        "/api/v1/customers",
        json={"full_name": "Follow-up Test", "phone": "6" + str(time.time_ns())[-9:]},
        headers=headers,
    )
    lead_res = await client.post(
        "/api/v1/leads", json={"customer_id": cust.json()["data"]["id"]}, headers=headers
    )
    lead_id = lead_res.json()["data"]["id"]

    scheduled_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    # 1. Schedule Follow-up
    create_res = await client.post(
        "/api/v1/followups",
        json={
            "lead_id": lead_id,
            "scheduled_at": scheduled_time,
            "type": "CALL",
            "notes": "Follow up on color preferences",
        },
        headers=headers,
    )
    assert create_res.status_code == 201
    f_data = create_res.json()["data"]
    followup_id = f_data["id"]
    assert f_data["status"] == "SCHEDULED"

    # Verify lead was updated
    lead_res = await client.get(f"/api/v1/leads/{lead_id}", headers=headers)
    assert lead_res.json()["data"]["next_followup_date"] is not None

    # 2. Complete Follow-up
    comp_res = await client.post(
        f"/api/v1/followups/{followup_id}/complete",
        json={"notes": "Customer confirmed visit this Saturday."},
        headers=headers,
    )
    assert comp_res.status_code == 200
    comp_data = comp_res.json()["data"]
    assert comp_data["status"] == "COMPLETED"
    assert comp_data["completed_at"] is not None
    assert "Saturday" in comp_data["notes"]
