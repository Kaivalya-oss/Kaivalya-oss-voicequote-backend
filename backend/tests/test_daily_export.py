"""Daily update export (Excel / CSV)."""
import io
import time
import uuid

import pytest
from httpx import AsyncClient
from openpyxl import load_workbook


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def cells(ws) -> list:
    return [c for row in ws.iter_rows(values_only=True) for c in row if c is not None]


@pytest.mark.asyncio
async def test_daily_export_excel_csv_and_scoping(client: AsyncClient, sales_token: str, admin_token: str):
    name = f"Export {uuid.uuid4().hex[:6]}"
    customer = (await client.post("/api/v1/customers", headers=H(sales_token),
                                  json={"full_name": name, "phone": "5" + str(time.time_ns())[-9:],
                                        "city": "Nashik"})).json()["data"]
    variant = (await client.get("/api/v1/vehicles?page_size=1", headers=H(sales_token))).json()["items"][0]
    await client.post("/api/v1/leads", headers=H(sales_token),
                      json={"customer_id": customer["id"], "interested_variant_id": variant["id"]})
    quote = (await client.post("/api/v1/quotations", headers=H(sales_token),
                               json={"customer_id": customer["id"], "variant_id": variant["id"],
                                     })).json()["data"]

    # Excel: summary + one sheet per section, today's records present
    res = await client.get("/api/v1/reports/daily-export?format=xlsx", headers=H(sales_token))
    assert res.status_code == 200, res.text
    assert "spreadsheetml" in res.headers["content-type"]
    assert ".xlsx" in res.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == ["Summary", "New customers", "Lead activity", "Quotations", "Follow-ups", "Test rides"]
    assert name in cells(wb["New customers"])
    assert name in cells(wb["Lead activity"])
    assert quote["quotation_number"] in cells(wb["Quotations"])

    # CSV: all sections stacked in one file
    res = await client.get("/api/v1/reports/daily-export?format=csv", headers=H(sales_token))
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    text = res.content.decode("utf-8-sig")
    assert name in text and quote["quotation_number"] in text and "TEST RIDES" in text

    # Another salesperson only gets their own records
    email = f"exp_{uuid.uuid4().hex[:8]}@dealer-mail.com"
    reg = (await client.post("/api/v1/auth/register",
                             json={"email": email, "password": "OtherPass123!", "full_name": "Other Sales"})).json()["data"]
    await client.post(f"/api/v1/users/{reg['id']}/activate", headers=H(admin_token))
    other = (await client.post("/api/v1/auth/login", json={"email": email, "password": "OtherPass123!"})).json()["access_token"]
    res = await client.get("/api/v1/reports/daily-export?format=csv", headers=H(other))
    assert res.status_code == 200 and name not in res.content.decode("utf-8-sig")

    # Management sees everyone's
    res = await client.get("/api/v1/reports/daily-export?format=csv", headers=H(admin_token))
    assert name in res.content.decode("utf-8-sig")

    # A past day with no activity still produces a valid file
    res = await client.get("/api/v1/reports/daily-export?format=xlsx&day=2001-01-01", headers=H(admin_token))
    wb = load_workbook(io.BytesIO(res.content))
    assert "No activity on this day." in cells(wb["Quotations"])

    assert (await client.get("/api/v1/reports/daily-export?format=pdf", headers=H(admin_token))).status_code == 422
    assert (await client.get("/api/v1/reports/daily-export")).status_code == 401
