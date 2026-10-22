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
