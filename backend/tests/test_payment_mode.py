"""Payment mode (CASH / FINANCE) replaces all quotation finance calculations, and a comparison's
parent price is never presented as an amount payable for both alternatives."""
import base64
import csv
import io
import re
import time
import uuid
import zlib

import pytest
from httpx import AsyncClient
from openpyxl import load_workbook

from app.core.config import settings
from app.services import email_service, whatsapp_service

FINANCE_KEYS = {"down_payment", "loan_amount", "interest_rate", "loan_tenure", "emi"}
FINANCE_WORDS = re.compile(r"\bEMI\b|down payment|loan|interest rate|tenure", re.IGNORECASE)


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def pdf_text(content: bytes) -> str:
    """Text drawn in a ReportLab PDF (ASCII85 + Flate page streams)."""
    text = []
    for stream in re.findall(rb"stream\r?\n(.*?)endstream", content, re.DOTALL):
        try:
            data = zlib.decompress(base64.a85decode(stream.strip().removesuffix(b"~>")))
        except Exception:
            continue
        text += re.findall(r"\(((?:[^()\\]|\\.)*)\)\s*Tj", data.decode("latin-1"))
    return " ".join(text)


async def variants(client: AsyncClient, token: str) -> list:
    items = (await client.get("/api/v1/vehicles?page_size=2", headers=H(token))).json()["items"]
    assert len(items) >= 2
    return items


async def customer(client: AsyncClient, token: str) -> dict:
    return (await client.post("/api/v1/customers", headers=H(token), json={
        "full_name": f"Pay {uuid.uuid4().hex[:6]}",
        "phone": "6" + str(time.time_ns())[-9:],
        "email": "buyer@example.com",
    })).json()["data"]


async def quote(client: AsyncClient, token: str, mode=None, compare=False, **extra) -> dict:
    a, b = await variants(client, token)
    c = await customer(client, token)
    body = {"customer_id": c["id"], "variant_id": a["id"], **extra}
    if mode:
        body["payment_mode"] = mode
    if compare:
        body["compare_variant_id"] = b["id"]
    res = await client.post("/api/v1/quotations", headers=H(token), json=body)
    assert res.status_code == 201, res.text
    return res.json()["data"]


def vehicle_options(q: dict) -> list:
    return [it for it in q["items"] if it["item_type"] == "VEHICLE_OPTION"]


@pytest.fixture
def smtp_on(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "SMTP_USER", "dealer@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "test-app-password")
    monkeypatch.setattr(settings, "ADMIN_NOTIFY_EMAIL", "admin-notify@example.com")
    monkeypatch.setattr(email_service, "_send_smtp", lambda msg: sent.append(msg))
    return sent


@pytest.fixture
def whatsapp_on(monkeypatch):
    """Real provider path with the HTTP call captured instead of sent."""
    payloads = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"messages": [{"id": "wamid.test"}]}

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            payloads.append(json)
            return Response()

    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "12345")
    monkeypatch.setattr(whatsapp_service.httpx, "AsyncClient", Client)
    return payloads


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["CASH", "FINANCE"])
async def test_single_vehicle_payment_mode(client: AsyncClient, sales_token: str, mode: str):
    q = await quote(client, sales_token, mode=mode)
    assert q["payment_mode"] == mode and q["is_comparison"] is False
    assert float(q["final_price"]) > 0
    assert not FINANCE_KEYS & q.keys()

    fetched = (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(sales_token))).json()["data"]
    assert fetched["payment_mode"] == mode and not FINANCE_KEYS & fetched.keys()
    listed = (await client.get(f"/api/v1/quotations?customer_id={q['customer_id']}", headers=H(sales_token))).json()
    assert listed["items"][0]["payment_mode"] == mode

    pdf = (await client.get(f"/api/v1/quotations/{q['id']}/pdf", headers=H(sales_token))).content
    text = pdf_text(pdf)
    assert mode.title() in text and "Payment Mode" in text
    assert not FINANCE_WORDS.search(text), text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["CASH", "FINANCE"])
async def test_comparison_shares_one_payment_mode(client: AsyncClient, sales_token: str, mode: str):
    q = await quote(client, sales_token, mode=mode, compare=True)
    assert q["payment_mode"] == mode and q["is_comparison"] is True
    options = vehicle_options(q)
    assert len(options) == 2
    for opt in options:
        assert not FINANCE_KEYS & opt["details_json"].keys()
        assert float(opt["total_price"]) > 0
    # independent prices, and the parent is one option rather than a sum
    assert float(q["final_price"]) == float(options[0]["total_price"])

    pdf = (await client.get(f"/api/v1/quotations/{q['id']}/pdf", headers=H(sales_token))).content
    text = pdf_text(pdf)
    assert "Payment Mode" in text and mode.title() in text
    assert "OPTION A" in text and "OPTION B" in text
    assert not FINANCE_WORDS.search(text), text


@pytest.mark.asyncio
async def test_payment_mode_validation_and_default(client: AsyncClient, sales_token: str):
    a, _ = await variants(client, sales_token)
    c = await customer(client, sales_token)
    bad = await client.post("/api/v1/quotations", headers=H(sales_token),
                            json={"customer_id": c["id"], "variant_id": a["id"], "payment_mode": "EMI"})
    assert bad.status_code == 422
    assert (await quote(client, sales_token))["payment_mode"] == "CASH"


@pytest.mark.asyncio
async def test_old_finance_inputs_are_ignored(client: AsyncClient, sales_token: str):
    q = await quote(client, sales_token, mode="FINANCE", down_payment=500000, loan_tenure=36, interest_rate=999)
    assert q["payment_mode"] == "FINANCE" and not FINANCE_KEYS & q.keys()


@pytest.mark.asyncio
async def test_idempotent_retry_with_other_payment_mode_conflicts(client: AsyncClient, sales_token: str):
    a, _ = await variants(client, sales_token)
    c = await customer(client, sales_token)
    key = {**H(sales_token), "Idempotency-Key": uuid.uuid4().hex}
    body = {"customer_id": c["id"], "variant_id": a["id"], "payment_mode": "CASH"}
    assert (await client.post("/api/v1/quotations", headers=key, json=body)).status_code == 201
    assert (await client.post("/api/v1/quotations", headers=key, json=body)).status_code == 200
    other = await client.post("/api/v1/quotations", headers=key, json={**body, "payment_mode": "FINANCE"})
    assert other.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("compare", [False, True])
async def test_email_payload(client: AsyncClient, sales_token: str, smtp_on, compare: bool):
    q = await quote(client, sales_token, mode="FINANCE", compare=compare)
    res = await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(sales_token))
    assert res.status_code == 200, res.text
    customer_msg, admin_msg = smtp_on
    assert len(smtp_on) == 2  # one customer email plus one admin notice, even for a comparison
    body = customer_msg.get_body(("plain",)).get_content()
    assert re.search(r"Payment mode:\s+Finance", body)
    assert not FINANCE_WORDS.search(body), body
    assert "Payment:    Finance" in admin_msg.get_content()
    assert len([p for p in customer_msg.iter_attachments() if p.get_content_type() == "application/pdf"]) == 1
    if compare:
        for opt in vehicle_options(q):
            assert f"{float(opt['total_price']):,.2f}" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("compare", [False, True])
async def test_whatsapp_payload(client: AsyncClient, sales_token: str, whatsapp_on, compare: bool):
    q = await quote(client, sales_token, mode="CASH", compare=compare)
    res = await client.post(f"/api/v1/quotations/{q['id']}/send-whatsapp", headers=H(sales_token))
    assert res.status_code == 200, res.text
    assert len(whatsapp_on) == 1  # one message, even for a comparison
    body = whatsapp_on[0]["text"]["body"]
    assert "*Payment Mode:* Cash" in body
    assert not FINANCE_WORDS.search(body), body
    if compare:
        for opt in vehicle_options(q):
            assert f"{float(opt['total_price']):,.2f}" in body


@pytest.mark.asyncio
async def test_comparison_excluded_from_money_totals(client: AsyncClient, admin_token: str):
    before = (await client.get("/api/v1/dashboard/revenue", headers=H(admin_token))).json()["data"]
    summary_before = (await client.get("/api/v1/dashboard/summary", headers=H(admin_token))).json()["data"]

    cmp_q = await quote(client, admin_token, mode="CASH", compare=True)
    accepted = await client.put(f"/api/v1/quotations/{cmp_q['id']}/status", headers=H(admin_token),
                                json={"status": "ACCEPTED"})
    assert accepted.status_code == 200, accepted.text

    after = (await client.get("/api/v1/dashboard/revenue", headers=H(admin_token))).json()["data"]
    summary_after = (await client.get("/api/v1/dashboard/summary", headers=H(admin_token))).json()["data"]
    assert float(after["total_revenue"]) == float(before["total_revenue"])
    assert float(after["quotation_value_pipeline"]) == float(before["quotation_value_pipeline"])
    assert after["comparison_quotations_excluded"] == before["comparison_quotations_excluded"] + 1
    assert float(summary_after["total_revenue"]) == float(summary_before["total_revenue"])
    assert summary_after["accepted_comparisons_excluded_from_revenue"] == (
        summary_before["accepted_comparisons_excluded_from_revenue"] + 1
    )

    # An accepted single-vehicle quotation still counts as revenue
    single = await quote(client, admin_token, mode="CASH")
    await client.put(f"/api/v1/quotations/{single['id']}/status", headers=H(admin_token), json={"status": "ACCEPTED"})
    final = (await client.get("/api/v1/dashboard/revenue", headers=H(admin_token))).json()["data"]
    assert float(final["total_revenue"]) == pytest.approx(float(after["total_revenue"]) + float(single["final_price"]))

    # Sales report lists both alternatives and no single amount
    report = (await client.get("/api/v1/reports/sales?limit=500", headers=H(admin_token))).json()["data"]
    row = next(r for r in report if r["quotation_id"] == cmp_q["id"])
    assert row["final_price"] is None and row["is_comparison"] is True and row["payment_mode"] == "CASH"
    assert [float(o["final_price"]) for o in row["option_prices"]] == [
        float(o["total_price"]) for o in vehicle_options(cmp_q)
    ]
    single_row = next(r for r in report if r["quotation_id"] == single["id"])
    assert float(single_row["final_price"]) == float(single["final_price"])


@pytest.mark.asyncio
async def test_daily_export_shows_options_and_payment_mode(client: AsyncClient, sales_token: str, admin_token: str):
    cmp_q = await quote(client, sales_token, mode="FINANCE", compare=True)
    single = await quote(client, sales_token, mode="CASH")
    a_price, b_price = [float(o["total_price"]) for o in vehicle_options(cmp_q)]

    res = await client.get("/api/v1/reports/daily-export?format=xlsx", headers=H(sales_token))
    assert res.status_code == 200, res.text
    wb = load_workbook(io.BytesIO(res.content))
    sheet = wb["Quotations"]
    header = [c.value for c in sheet[1]]
    assert not [h for h in header if FINANCE_WORDS.search(str(h))], header
    rows = [dict(zip(header, r)) for r in sheet.iter_rows(min_row=2, values_only=True)]
    cmp_row = next(r for r in rows if r["Quotation No."] == cmp_q["quotation_number"])
    assert float(cmp_row["On-road price A (₹)"]) == a_price
    assert float(cmp_row["On-road price B (₹)"]) == b_price
    assert cmp_row["Payment mode"] == "Finance"
    single_row = next(r for r in rows if r["Quotation No."] == single["quotation_number"])
    assert single_row["On-road price B (₹)"] is None and single_row["Payment mode"] == "Cash"

    summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(values_only=True) if r[0]}
    assert summary["Comparison quotations (not totalled)"] >= 1
    # A salesperson's export has no money total. Management's does, across everyone's quotations.
    assert "Single-vehicle quotation value (₹)" not in summary
    res = await client.get("/api/v1/reports/daily-export?format=xlsx", headers=H(admin_token))
    wb = load_workbook(io.BytesIO(res.content))
    sheet = wb["Quotations"]
    header = [c.value for c in sheet[1]]
    rows = [dict(zip(header, r)) for r in sheet.iter_rows(min_row=2, values_only=True)]
    summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(values_only=True) if r[0]}
    single_prices = [float(r["On-road price A (₹)"]) for r in rows if r["On-road price B (₹)"] is None]
    assert float(summary["Single-vehicle quotation value (₹)"]) == pytest.approx(sum(single_prices))

    res = await client.get("/api/v1/reports/daily-export?format=csv", headers=H(sales_token))
    rows = list(csv.reader(io.StringIO(res.content.decode("utf-8-sig"))))
    assert any("Comparison quotations (not totalled)" in r for r in rows)
    assert any(cmp_q["quotation_number"] in r and "Finance" in r for r in rows)
