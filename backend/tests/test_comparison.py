"""Comparison quotation PDF (vehicle A vs vehicle B)."""
import time
import uuid

import pytest
from httpx import AsyncClient


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def customer(client: AsyncClient, token: str) -> dict:
    return (await client.post("/api/v1/customers", headers=H(token),
                              json={"full_name": "Compare Test", "phone": "8" + str(time.time_ns())[-9:]})).json()["data"]


async def quote(client: AsyncClient, token: str, customer_id: int, variant_id: int) -> dict:
    r = await client.post("/api/v1/quotations", headers=H(token),
                          json={"customer_id": customer_id, "variant_id": variant_id, "down_payment": 0})
    assert r.status_code == 201, r.text
    return r.json()["data"]


@pytest.mark.asyncio
async def test_comparison_pdf(client: AsyncClient, sales_token: str, admin_token: str):
    variants = (await client.get("/api/v1/vehicles?page_size=2", headers=H(sales_token))).json()["items"]
    assert len(variants) >= 2
    c = await customer(client, sales_token)
    qa = await quote(client, sales_token, c["id"], variants[0]["id"])
    qb = await quote(client, sales_token, c["id"], variants[1]["id"])
    url = f"/api/v1/quotations/compare/pdf?ids={qa['id']}&ids={qb['id']}"

    ok = await client.get(url, headers=H(sales_token))
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "application/pdf"
    assert ok.content[:4] == b"%PDF" and len(ok.content) > 1500
    assert qa["quotation_number"] in ok.headers["content-disposition"]

    assert (await client.get(url)).status_code == 401
    one = await client.get(f"/api/v1/quotations/compare/pdf?ids={qa['id']}", headers=H(sales_token))
    assert one.status_code == 400 and one.json()["error"]["code"] == "COMPARE_COUNT"

    other_c = await customer(client, sales_token)
    qx = await quote(client, sales_token, other_c["id"], variants[0]["id"])
    mixed = await client.get(f"/api/v1/quotations/compare/pdf?ids={qa['id']}&ids={qx['id']}", headers=H(sales_token))
    assert mixed.status_code == 400 and mixed.json()["error"]["code"] == "COMPARE_CUSTOMER_MISMATCH"

    # another salesperson cannot see these quotations, so cannot compare them either
    email = f"cmp_{uuid.uuid4().hex[:8]}@dealer-mail.com"
    reg = (await client.post("/api/v1/auth/register",
                             json={"email": email, "password": "OtherPass123!", "full_name": "Other Sales"})).json()["data"]
    await client.post(f"/api/v1/users/{reg['id']}/activate", headers=H(admin_token))
    other = (await client.post("/api/v1/auth/login", json={"email": email, "password": "OtherPass123!"})).json()["access_token"]
    assert (await client.get(url, headers=H(other))).status_code == 404
    # management can
    assert (await client.get(url, headers=H(admin_token))).status_code == 200


@pytest.mark.asyncio
async def test_single_quotation_multiple_vehicle_comparison(client: AsyncClient, sales_token: str):
    """Test the single-quotation comparison model (Acceptance Tests 1-10)."""
    variants = (await client.get("/api/v1/vehicles?page_size=2", headers=H(sales_token))).json()["items"]
    assert len(variants) >= 2
    var_a = variants[0]
    var_b = variants[1]

    # Create customer with phone and email
    c = (await client.post("/api/v1/customers", headers=H(sales_token),
                           json={
                               "full_name": "Mike Comparison",
                               "phone": "9" + str(time.time_ns())[-9:],
                               "email": f"mike_{uuid.uuid4().hex[:6]}@example.com",
                           })).json()["data"]

    # 1. Create a single comparison quotation
    payload = {
        "customer_id": c["id"],
        "variant_id": var_a["id"],
        "compare_variant_id": var_b["id"],
        "down_payment": 20000,
        "loan_tenure": 36,
        "interest_rate": 9.5,
    }
    res = await client.post("/api/v1/quotations", headers=H(sales_token), json=payload)
    assert res.status_code == 201, res.text
    quote_data = res.json()["data"]

    # 2. Verify: ONE quotation ID and ONE quotation number
    q_id = quote_data["id"]
    q_num = quote_data["quotation_number"]
    assert q_num.startswith("QT-")

    # 3. Verify: Two quotation items referencing the SAME quotation
    items = quote_data.get("items", [])
    vehicle_items = [it for it in items if it.get("item_type") == "VEHICLE_OPTION"]
    assert len(vehicle_items) == 2, f"Expected 2 vehicle options, got: {items}"
    assert "Option A:" in vehicle_items[0]["item_name"]
    assert "Option B:" in vehicle_items[1]["item_name"]

    # 4. Verify independent prices and NO summation
    price_a = float(vehicle_items[0]["total_price"])
    price_b = float(vehicle_items[1]["total_price"])
    assert price_a > 0 and price_b > 0
    final_price = float(quote_data["final_price"])
    # final_price must be Option A's independent price, NEVER the sum of A + B
    assert final_price == price_a
    assert final_price != (price_a + price_b)

    # 5. Verify snapshots in details_json
    assert vehicle_items[0]["details_json"]["variant_name"] == var_a["name"]
    assert vehicle_items[1]["details_json"]["variant_name"] == var_b["name"]
    assert float(vehicle_items[0]["details_json"]["final_price"]) == price_a
    assert float(vehicle_items[1]["details_json"]["final_price"]) == price_b

    # 6. Verify single PDF contains comparison
    pdf_res = await client.get(f"/api/v1/quotations/{q_id}/pdf", headers=H(sales_token))
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert pdf_res.content[:4] == b"%PDF"

    # 7. Verify single WhatsApp send
    wa_res = await client.post(f"/api/v1/quotations/{q_id}/send-whatsapp", headers=H(sales_token))
    assert wa_res.status_code == 200
    wa_data = wa_res.json()["data"]
    assert wa_data["quotation_number"] == q_num

    # 8. Verify single Email send
    email_res = await client.post(f"/api/v1/quotations/{q_id}/send-email", headers=H(sales_token),
                                 json={"to_email": c["email"]})
    assert email_res.status_code == 200
    email_data = email_res.json()["data"]
    assert email_data["quotation_number"] == q_num

    # 9. Verify quotation history displays exactly ONE quotation
    list_res = await client.get(f"/api/v1/quotations?search={q_num}", headers=H(sales_token))
    assert list_res.status_code == 200
    found_items = [it for it in list_res.json()["items"] if it["quotation_number"] == q_num]
    assert len(found_items) == 1, f"Expected 1 quotation in history, found {len(found_items)}"

