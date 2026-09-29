import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_dashboard_summary_and_metrics(client: AsyncClient, owner_token: str, sales_token: str):
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    # 1. Summary with owner token
    res = await client.get("/api/v1/dashboard/summary", headers=owner_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert "total_customers" in data
    assert "total_leads" in data
    assert "total_revenue" in data
    assert "lead_conversion_rate_percentage" in data

    # 2. Sales Executive cannot access dashboard summary (403)
    forbidden_res = await client.get("/api/v1/dashboard/summary", headers=sales_headers)
    assert forbidden_res.status_code == 403


@pytest.mark.asyncio
async def test_ai_vehicle_recommendation(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}

    req_payload = {
        "budget": 110000.00,
        "preferred_brand": "Honda",
        "usage": "COMMUTE",
        "preferred_fuel_type": "PETROL",
        "preferences": "Good mileage and reliability",
    }
    res = await client.post("/api/v1/ai/recommend-vehicle", json=req_payload, headers=headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert "recommendations" in data
    assert len(data["recommendations"]) >= 1
    assert "ai_analysis" in data
    assert "provider" in data


@pytest.mark.asyncio
async def test_ai_lead_insights(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}

    # Fetch a lead ID
    leads_res = await client.get("/api/v1/leads", headers=headers)
    lead_id = leads_res.json()["items"][0]["id"]

    res = await client.post(f"/api/v1/ai/lead-insights/{lead_id}", headers=headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert "conversion_probability" in data
    assert "suggested_next_action" in data
    assert "recommended_followup_timing" in data
    assert data["lead_id"] == lead_id
