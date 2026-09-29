import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_quotation_creation_and_pdf_generation(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}

    # 1. Fetch customer and variant
    c_res = await client.get("/api/v1/customers", headers=headers)
    customer_id = c_res.json()["items"][0]["id"]

    v_res = await client.get("/api/v1/vehicles", headers=headers)
    variant = v_res.json()["items"][0]
    variant_id = variant["id"]

    # Fetch accessory
    acc_res = await client.get("/api/v1/vehicles/catalog/accessories")
    acc_id = acc_res.json()["data"][0]["id"]

    quote_payload = {
        "customer_id": customer_id,
        "variant_id": variant_id,
        "accessory_ids": [acc_id],
        "down_payment": 20000.00,
        "loan_tenure": 36,
        "interest_rate": 9.50,
    }

    # 2. Create Quotation
    res = await client.post("/api/v1/quotations", json=quote_payload, headers=headers)
    assert res.status_code == 201
    data = res.json()["data"]
    quote_id = data["id"]
    quotation_number = data["quotation_number"]

    assert quotation_number.startswith("QT-")
    assert float(data["final_price"]) > 0
    assert float(data["down_payment"]) == 20000.00
    assert float(data["loan_amount"]) == float(data["final_price"]) - 20000.00
    assert float(data["emi"]) > 0
    assert data["status"] == "GENERATED"
    assert data["pdf_url"] is not None

    # 3. Retrieve Quotation PDF
    pdf_res = await client.get(f"/api/v1/quotations/{quote_id}/pdf")
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert len(pdf_res.content) > 1000

    # 4. WhatsApp Dispatch
    wa_res = await client.post(f"/api/v1/quotations/{quote_id}/send-whatsapp", headers=headers)
    assert wa_res.status_code == 200
    wa_data = wa_res.json()["data"]
    assert wa_data["success"] is True
    assert "delivery" in wa_data
    assert wa_data["delivery"]["status"] in ["SENT", "MOCK_SENT"]


@pytest.mark.asyncio
async def test_quotation_excessive_down_payment(client: AsyncClient, sales_token: str):
    headers = {"Authorization": f"Bearer {sales_token}"}

    c_res = await client.get("/api/v1/customers", headers=headers)
    customer_id = c_res.json()["items"][0]["id"]

    v_res = await client.get("/api/v1/vehicles", headers=headers)
    variant_id = v_res.json()["items"][0]["id"]

    # Down payment 500,000 exceeds vehicle on-road price
    res = await client.post(
        "/api/v1/quotations",
        json={
            "customer_id": customer_id,
            "variant_id": variant_id,
            "down_payment": 500000.00,
            "loan_tenure": 12,
            "interest_rate": 10.0,
        },
        headers=headers,
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_DOWN_PAYMENT"
