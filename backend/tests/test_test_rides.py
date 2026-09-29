from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_test_ride_booking_and_collision_detection(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}

    c_res = await client.get("/api/v1/customers", headers=headers)
    customer_id = c_res.json()["items"][0]["id"]

    v_res = await client.get("/api/v1/vehicles", headers=headers)
    variant_id = v_res.json()["items"][0]["id"]

    slot_time = (datetime.now(timezone.utc) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)

    # 1. Book first test ride
    res1 = await client.post(
        "/api/v1/test-rides",
        json={
            "customer_id": customer_id,
            "variant_id": variant_id,
            "scheduled_at": slot_time.isoformat(),
            "duration_minutes": 30,
            "driving_license_number": "DL-1420110012345",
        },
        headers=headers,
    )
    assert res1.status_code == 201
    ride_id = res1.json()["data"]["id"]
    assert res1.json()["data"]["status"] == "REQUESTED"

    # 2. Collision detection: attempt to book overlapping slot for same vehicle
    overlapping_slot = (slot_time + timedelta(minutes=10)).isoformat()
    res_collision = await client.post(
        "/api/v1/test-rides",
        json={
            "customer_id": customer_id,
            "variant_id": variant_id,
            "scheduled_at": overlapping_slot,
            "duration_minutes": 30,
        },
        headers=headers,
    )
    assert res_collision.status_code == 409
    assert res_collision.json()["error"]["code"] == "TEST_RIDE_SLOT_COLLISION"

    # 3. Confirm Test Ride
    res_conf = await client.post(f"/api/v1/test-rides/{ride_id}/confirm", headers=headers)
    assert res_conf.status_code == 200
    assert res_conf.json()["data"]["status"] == "CONFIRMED"

    # 4. Complete Test Ride with Rating
    res_comp = await client.post(
        f"/api/v1/test-rides/{ride_id}/complete",
        json={"feedback_rating": 5, "notes": "Customer thoroughly enjoyed the ride and acceleration."},
        headers=headers,
    )
    assert res_comp.status_code == 200
    assert res_comp.json()["data"]["status"] == "COMPLETED"
    assert res_comp.json()["data"]["feedback_rating"] == 5
