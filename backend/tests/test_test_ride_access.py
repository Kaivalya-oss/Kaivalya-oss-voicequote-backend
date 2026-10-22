"""Test ride booking, listing and the ownership rules on confirm / complete / cancel."""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def other_salesperson(client: AsyncClient, admin_token: str) -> str:
    email = f"ride_{uuid.uuid4().hex[:8]}@dealer-mail.com"
    reg = (await client.post("/api/v1/auth/register",
                             json={"email": email, "password": "OtherPass123!", "full_name": "Other Sales"})).json()["data"]
    await client.post(f"/api/v1/users/{reg['id']}/activate", headers=H(admin_token))
    return (await client.post("/api/v1/auth/login", json={"email": email, "password": "OtherPass123!"})).json()["access_token"]


@pytest.mark.asyncio
async def test_test_ride_lifecycle_and_ownership(client: AsyncClient, sales_token: str, admin_token: str):
    variant = (await client.get("/api/v1/vehicles?page_size=1", headers=H(sales_token))).json()["items"][0]
    customer = (await client.post("/api/v1/customers", headers=H(sales_token),
                                  json={"full_name": "Ride Test", "phone": "7" + str(time.time_ns())[-9:]})).json()["data"]
    # a unique far-future slot so the collision check never trips on other runs
    slot = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=200, minutes=time.time_ns() % 100000)
    body = {"customer_id": customer["id"], "variant_id": variant["id"], "scheduled_at": slot.isoformat(),
            "duration_minutes": 30}

    booked = await client.post("/api/v1/test-rides", headers=H(sales_token), json=body)
    assert booked.status_code == 201, booked.text
    ride = booked.json()["data"]
    assert ride["status"] == "REQUESTED" and ride["customer"]["full_name"] == "Ride Test"

    # same vehicle, overlapping slot -> collision
    clash = await client.post("/api/v1/test-rides", headers=H(sales_token), json=body)
    assert clash.status_code == 409

    # the page lists rides by customer
    listed = (await client.get(f"/api/v1/test-rides?customer_id={customer['id']}", headers=H(sales_token))).json()
    assert [r["id"] for r in listed["items"]] == [ride["id"]]

    # another salesperson can neither see, change, nor book for this customer
    other = await other_salesperson(client, admin_token)
    assert (await client.get(f"/api/v1/test-rides/{ride['id']}", headers=H(other))).status_code == 404
    assert (await client.post(f"/api/v1/test-rides/{ride['id']}/confirm", headers=H(other))).status_code == 404
    assert (await client.post(f"/api/v1/test-rides/{ride['id']}/cancel", headers=H(other), json={})).status_code == 404
    other_body = {**body, "scheduled_at": (slot + timedelta(hours=5)).isoformat()}
    assert (await client.post("/api/v1/test-rides", headers=H(other), json=other_body)).status_code == 404

    # owner confirms, completes with a rating
    confirmed = await client.post(f"/api/v1/test-rides/{ride['id']}/confirm", headers=H(sales_token))
    assert confirmed.status_code == 200 and confirmed.json()["data"]["status"] == "CONFIRMED"
    done = await client.post(f"/api/v1/test-rides/{ride['id']}/complete", headers=H(sales_token),
                             json={"feedback_rating": 4, "notes": "Liked the pickup"})
    assert done.status_code == 200
    assert done.json()["data"]["status"] == "COMPLETED" and done.json()["data"]["feedback_rating"] == 4

    # a completed ride cannot be cancelled
    assert (await client.post(f"/api/v1/test-rides/{ride['id']}/cancel", headers=H(sales_token), json={})).status_code == 400

    # a manager can act on any ride
    slot2 = {**body, "scheduled_at": (slot + timedelta(hours=2)).isoformat()}
    ride2 = (await client.post("/api/v1/test-rides", headers=H(sales_token), json=slot2)).json()["data"]
    cancelled = await client.post(f"/api/v1/test-rides/{ride2['id']}/cancel", headers=H(admin_token),
                                  json={"reason": "Customer rescheduled"})
    assert cancelled.status_code == 200 and cancelled.json()["data"]["status"] == "CANCELLED"
