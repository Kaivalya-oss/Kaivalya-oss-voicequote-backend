"""Quotation e-mail: simulation mode, real-send path (SMTP stubbed, nothing leaves the
machine), admin notice, missing address, failures and authorization."""
import smtplib
import time
import uuid

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services import email_service


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def make_quote(client: AsyncClient, token: str, email: str | None = "buyer@example.com") -> dict:
    vid = (await client.get("/api/v1/vehicles?page_size=1", headers=H(token))).json()["items"][0]["id"]
    body = {"full_name": "Email Test", "phone": "7" + str(time.time_ns())[-9:]}
    if email:
        body["email"] = email
    cust = (await client.post("/api/v1/customers", headers=H(token), json=body)).json()["data"]
    q = await client.post("/api/v1/quotations", headers=H(token),
                          json={"customer_id": cust["id"], "variant_id": vid})
    return q.json()["data"]


@pytest.fixture
def smtp_on(monkeypatch):
    """Pretend SMTP is configured and capture messages instead of sending them."""
    sent = []
    monkeypatch.setattr(settings, "SMTP_USER", "dealer@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "test-app-password")
    monkeypatch.setattr(settings, "ADMIN_NOTIFY_EMAIL", "admin-notify@example.com")
    monkeypatch.setattr(email_service, "_send_smtp", lambda msg: sent.append(msg))
    return sent


@pytest.mark.asyncio
async def test_email_simulated_when_not_configured(client: AsyncClient, sales_token: str, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_USER", "")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "")
    called = []
    monkeypatch.setattr(email_service, "_send_smtp", lambda msg: called.append(msg))
    q = await make_quote(client, sales_token)
    r = await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(sales_token))
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["simulated"] is True and d["delivered"] is False and d["status"] == "SIMULATED"
    assert called == []
    after = (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(sales_token))).json()["data"]
    assert after["status"] == "GENERATED"


@pytest.mark.asyncio
async def test_email_sent_with_pdf_and_admin_notice(client: AsyncClient, sales_token: str, smtp_on):
    q = await make_quote(client, sales_token)
    r = await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(sales_token))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["delivered"] is True and d["status"] == "SENT" and d["admin_notified"] is True
    assert d["recipient"] == "buyer@example.com"

    customer_msg, admin_msg = smtp_on
    assert customer_msg["To"] == "buyer@example.com"
    assert q["quotation_number"] in customer_msg["Subject"]
    pdfs = [p for p in customer_msg.iter_attachments() if p.get_content_type() == "application/pdf"]
    assert len(pdfs) == 1 and pdfs[0].get_payload(decode=True)[:4] == b"%PDF"
    assert admin_msg["To"] == "admin-notify@example.com"
    assert "buyer@example.com" in admin_msg.get_content()

    after = (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(sales_token))).json()["data"]
    assert after["status"] == "SENT"


@pytest.mark.asyncio
async def test_email_override_address_and_missing_address(client: AsyncClient, sales_token: str, smtp_on):
    q = await make_quote(client, sales_token, email=None)
    r = await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(sales_token))
    assert r.status_code == 400 and r.json()["error"]["code"] == "NO_CUSTOMER_EMAIL"
    assert (await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(sales_token),
                              json={"email": "not-an-email"})).status_code == 422
    r = await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(sales_token),
                          json={"email": "typed@example.com"})
    assert r.status_code == 200 and r.json()["data"]["recipient"] == "typed@example.com"
    assert smtp_on[0]["To"] == "typed@example.com"


@pytest.mark.asyncio
async def test_email_failure_changes_nothing(client: AsyncClient, sales_token: str, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_USER", "dealer@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "test-app-password")

    def boom(msg):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(email_service, "_send_smtp", boom)
    q = await make_quote(client, sales_token)
    r = await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(sales_token))
    assert r.status_code == 502 and r.json()["error"]["code"] == "EMAIL_SEND_FAILED"
    after = (await client.get(f"/api/v1/quotations/{q['id']}", headers=H(sales_token))).json()["data"]
    assert after["status"] == "GENERATED"


@pytest.mark.asyncio
async def test_email_authorization(client: AsyncClient, sales_token: str, admin_token: str, smtp_on):
    q = await make_quote(client, sales_token)
    assert (await client.post(f"/api/v1/quotations/{q['id']}/send-email")).status_code == 401
    fin = (await client.post("/api/v1/auth/login",
                             json={"email": "finance@dealership.com", "password": "FinancePass123!"})).json()["access_token"]
    assert (await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(fin))).status_code == 403
    email = f"other_{uuid.uuid4().hex[:8]}@dealer-mail.com"
    reg = (await client.post("/api/v1/auth/register",
                             json={"email": email, "password": "OtherPass123!", "full_name": "Other Sales"})).json()["data"]
    await client.post(f"/api/v1/users/{reg['id']}/activate", headers=H(admin_token))
    other = (await client.post("/api/v1/auth/login", json={"email": email, "password": "OtherPass123!"})).json()["access_token"]
    assert (await client.post(f"/api/v1/quotations/{q['id']}/send-email", headers=H(other))).status_code in (403, 404)
    assert smtp_on == []
