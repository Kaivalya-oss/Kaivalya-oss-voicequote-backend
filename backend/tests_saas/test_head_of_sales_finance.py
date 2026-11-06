"""Head of Sales sees quotation prices, but never totals or summaries of money, whatever they request.

Every GET route with no path parameter is called as a real Head of Sales on a real showroom database,
so a new endpoint is covered without anyone remembering to add it here.
"""
import io
import re

import pytest
from httpx import AsyncClient
from openpyxl import load_workbook

from app.main import app
from tests_saas.conftest import H, invite_and_join
from tests_saas.showroom_dbs import SERVER_A
from tests_saas.test_phase2_tenant_databases import add_customer, seed_vehicles, showroom

BANNED_KEY = re.compile(r"revenue|profit|margin|pipeline|average_|discount_total|total_discount|total_value|total_sales|sales_value|turnover", re.IGNORECASE)
TOTAL_LABEL = "Single-vehicle quotation value"
# Salesperson-scoped or non-financial routes are still called, and must simply hold no banned keys.
SKIP_PATHS = {"/api/v1/reports/daily-export"}


def banned_keys(value, path="") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, inner in value.items():
            if BANNED_KEY.search(str(key)):
                found.append(f"{path}.{key}")
            found.extend(banned_keys(inner, f"{path}.{key}"))
    elif isinstance(value, list):
        for inner in value:
            found.extend(banned_keys(inner, path))
    return found


async def populated_showroom(client: AsyncClient):
    owner, _ = await showroom(client, SERVER_A)
    token = owner["access_token"]
    a, b = await seed_vehicles(client, token)
    customer = await add_customer(client, token, "Finance Probe", "9345678901")
    single = await client.post("/api/v1/quotations", headers=H(token), json={"customer_id": customer["id"], "variant_id": a["id"], "payment_mode": "CASH"})
    assert single.status_code == 201, single.text
    accepted = await client.put(f"/api/v1/quotations/{single.json()['data']['id']}/status", headers=H(token), json={"status": "ACCEPTED"})
    second = await client.post("/api/v1/quotations", headers=H(token), json={"customer_id": customer["id"], "variant_id": b["id"], "payment_mode": "FINANCE"})
    assert second.status_code == 201, second.text
    head = await invite_and_join(client, token, "HEAD_OF_SALES")
    sales = await invite_and_join(client, token, "SALESPERSON")
    return owner, head, sales, single.json()["data"], accepted


async def test_head_of_sales_gets_403_on_financial_endpoints(client: AsyncClient):
    owner, head, *_ = await populated_showroom(client)
    for path in ("/api/v1/dashboard/revenue", "/api/v1/reports/sales", "/api/v1/reports/sales?limit=500"):
        assert (await client.get(path, headers=H(head["access_token"]))).status_code == 403, path
        assert (await client.get(path, headers=H(owner["access_token"]))).status_code == 200, path


async def test_summary_omits_revenue_for_head_of_sales_only(client: AsyncClient):
    owner, head, *_ = await populated_showroom(client)
    for_head = (await client.get("/api/v1/dashboard/summary", headers=H(head["access_token"]))).json()["data"]
    assert "total_revenue" not in for_head and "accepted_comparisons_excluded_from_revenue" not in for_head
    assert for_head["quotations_generated"] >= 2  # team operational figures stay
    for_owner = (await client.get("/api/v1/dashboard/summary", headers=H(owner["access_token"]))).json()["data"]
    assert float(for_owner["total_revenue"]) > 0


@pytest.mark.parametrize("params", ["", "?include_financials=true", "?financial=1&role=OWNER", "?scope=all&admin=true"])
async def test_parameters_cannot_turn_financials_back_on(client: AsyncClient, params):
    _, head, *_ = await populated_showroom(client)
    res = await client.get(f"/api/v1/dashboard/summary{params}", headers={**H(head["access_token"]), "X-Role": "OWNER", "X-Tenant-Role": "OWNER"})
    assert res.status_code == 200
    assert banned_keys(res.json()) == []


async def test_no_endpoint_returns_a_financial_summary_to_head_of_sales(client: AsyncClient):
    """Calls everything reachable. Anything forbidden is fine; anything returned must have no summary."""
    _, head, *_ = await populated_showroom(client)
    called = []
    # The OpenAPI table includes routes from nested routers, which app.routes alone doesn't list.
    for path, operations in app.openapi()["paths"].items():
        if "get" not in operations or "{" in path or not path.startswith("/api/v1") or path in SKIP_PATHS:
            continue
        res = await client.get(path, headers=H(head["access_token"]))
        called.append((path, res.status_code))
        if res.status_code == 200 and "json" in res.headers.get("content-type", ""):
            assert banned_keys(res.json()) == [], path
    assert len(called) >= 15
    assert ("/api/v1/dashboard/sales", 200) in called and ("/api/v1/reports/quotations", 200) in called


async def test_individual_quotation_prices_stay_visible_to_head_of_sales(client: AsyncClient):
    _, head, _, quote, _ = await populated_showroom(client)
    token = head["access_token"]
    one = await client.get(f"/api/v1/quotations/{quote['id']}", headers=H(token))
    assert one.status_code == 200 and float(one.json()["data"]["final_price"]) > 0
    listed = await client.get("/api/v1/quotations", headers=H(token))
    assert float(listed.json()["items"][0]["final_price"]) > 0
    report = await client.get("/api/v1/reports/quotations", headers=H(token))
    assert report.status_code == 200 and float(report.json()["data"][0]["final_price"]) > 0


async def test_quotation_lists_hold_prices_but_no_totals(client: AsyncClient):
    _, head, *_ = await populated_showroom(client)
    body = (await client.get("/api/v1/quotations", headers=H(head["access_token"]))).json()
    # "total" here is a count of records for paging, not money.
    assert isinstance(body["total"], int) and body["total"] >= 2
    assert banned_keys(body) == []


async def test_export_has_row_prices_and_no_total_for_head_of_sales(client: AsyncClient):
    owner, head, *_ = await populated_showroom(client)
    for_head = await client.get("/api/v1/reports/daily-export?format=csv", headers=H(head["access_token"]))
    assert for_head.status_code == 200
    assert TOTAL_LABEL not in for_head.text
    for_owner = await client.get("/api/v1/reports/daily-export?format=csv", headers=H(owner["access_token"]))
    assert TOTAL_LABEL in for_owner.text
    # Both still carry the rows, with their prices.
    for text in (for_head.text, for_owner.text):
        assert "Quotation No." in text and "Finance Probe" in text


async def test_excel_export_has_no_total_for_head_of_sales(client: AsyncClient):
    owner, head, *_ = await populated_showroom(client)
    for token, expected in ((head["access_token"], False), (owner["access_token"], True)):
        res = await client.get("/api/v1/reports/daily-export?format=xlsx", headers=H(token))
        assert res.status_code == 200
        workbook = load_workbook(io.BytesIO(res.content), read_only=True)
        cells = [str(c) for ws in workbook.worksheets for row in ws.iter_rows(values_only=True) for c in row if c is not None]
        assert any(TOTAL_LABEL in c for c in cells) is expected


async def test_salesperson_can_export_only_own_records_with_no_total(client: AsyncClient):
    _, _, sales, *_ = await populated_showroom(client)
    res = await client.get("/api/v1/reports/daily-export?format=csv", headers=H(sales["access_token"]))
    assert res.status_code == 200
    assert TOTAL_LABEL not in res.text
    assert "Finance Probe" not in res.text  # those quotations belong to the Owner


async def test_salesperson_has_no_financial_or_team_analytics(client: AsyncClient):
    _, _, sales, *_ = await populated_showroom(client)
    for path in ("/api/v1/dashboard/summary", "/api/v1/dashboard/revenue", "/api/v1/dashboard/sales", "/api/v1/reports/sales", "/api/v1/reports/quotations"):
        assert (await client.get(path, headers=H(sales["access_token"]))).status_code == 403, path


@pytest.mark.parametrize("role,status", [("HEAD_OF_SALES", 403), ("SALESPERSON", 403)])
async def test_only_owner_can_delete_customers(client: AsyncClient, role, status):
    owner, _ = await showroom(client, SERVER_A)
    customer = await add_customer(client, owner["access_token"], "Delete Probe", "9345678902")
    member = await invite_and_join(client, owner["access_token"], role)
    assert (await client.delete(f"/api/v1/customers/{customer['id']}", headers=H(member["access_token"]))).status_code == status
    assert (await client.get(f"/api/v1/customers/{customer['id']}", headers=H(owner["access_token"]))).status_code == 200
    assert (await client.delete(f"/api/v1/customers/{customer['id']}", headers=H(owner["access_token"]))).status_code in (200, 204)
