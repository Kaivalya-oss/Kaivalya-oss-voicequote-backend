"""Quotation e-mail delivery over SMTP (Gmail in the demo setup).

* With SMTP_USER and SMTP_PASSWORD configured, the customer receives the quotation with
  its PDF attached, and ADMIN_NOTIFY_EMAIL receives a short notice that it was sent.
* Without credentials the service runs in simulation mode: nothing is sent, nothing about
  the quotation changes, and the response says so (status "SIMULATED").
* Only a real send marks a GENERATED quotation as SENT.
"""
import asyncio
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, BadRequestException, NotFoundException
from app.core.logging import logger
from app.models.notification import Notification
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.quotation_repository import QuotationRepository


def email_configured() -> bool:
    return bool(settings.SMTP_USER and settings.SMTP_PASSWORD)


def _send_smtp(msg: EmailMessage) -> None:
    """Blocking SMTP send (run in a worker thread)."""
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD.replace(" ", ""))
        smtp.send_message(msg)


def _money(value) -> str:
    return f"Rs. {value:,.2f}"


class EmailService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.quote_repo = QuotationRepository(session)
        self.audit_repo = AuditLogRepository(session)

    def _sender(self) -> str:
        return formataddr((settings.SMTP_FROM_NAME, settings.SMTP_USER))

    def _customer_message(self, quote, to_email: str, pdf_path: Optional[str]) -> EmailMessage:
        customer = quote.customer
        variant = quote.variant
        vehicle = variant.name if variant else f"Variant #{quote.variant_id}"
        exec_name = quote.sales_executive.full_name if quote.sales_executive else "our sales team"
        lines = [
            f"Dear {customer.full_name},",
            "",
            f"Thank you for your interest in the {vehicle}. Please find your quotation attached.",
            "",
            f"Quotation number: {quote.quotation_number}",
            f"On-road price:    {_money(quote.final_price)}",
        ]
        if quote.emi and quote.emi > 0:
            lines.append(f"Monthly EMI:      {_money(quote.emi)} x {quote.loan_tenure} months")
        if quote.expires_at:
            lines.append(f"Valid until:      {quote.expires_at:%d %b %Y}")
        lines += [
            "",
            "Reply to this email or visit the showroom to book a test ride or your vehicle.",
            "",
            "Regards,",
            exec_name,
            settings.SMTP_FROM_NAME,
        ]
        msg = EmailMessage()
        msg["Subject"] = f"Your quotation {quote.quotation_number} - {vehicle}"
        msg["From"] = self._sender()
        msg["To"] = to_email
        msg.set_content("\n".join(lines))
        if pdf_path:
            with open(pdf_path, "rb") as fh:
                msg.add_attachment(
                    fh.read(), maintype="application", subtype="pdf",
                    filename=f"{quote.quotation_number}.pdf",
                )
        return msg

    def _admin_message(self, quote, to_email: str, sent_by: str) -> EmailMessage:
        admin_to = settings.ADMIN_NOTIFY_EMAIL or settings.SMTP_USER
        msg = EmailMessage()
        msg["Subject"] = f"[VoiceQuote] Quotation {quote.quotation_number} emailed to {quote.customer.full_name}"
        msg["From"] = self._sender()
        msg["To"] = admin_to
        msg.set_content(
            "\n".join([
                "A quotation was emailed to a customer.",
                "",
                f"Quotation:  {quote.quotation_number}",
                f"Customer:   {quote.customer.full_name} ({quote.customer.phone})",
                f"Sent to:    {to_email}",
                f"Vehicle:    {quote.variant.name if quote.variant else quote.variant_id}",
                f"Price:      {_money(quote.final_price)}",
                f"Sent by:    {sent_by}",
            ])
        )
        return msg

    async def send_quotation_email(
        self,
        quotation_id: int,
        user_id: int,
        sent_by: str,
        to_email: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        quote = await self.quote_repo.get_by_id(quotation_id)
        if not quote:
            raise NotFoundException("Quotation", quotation_id)
        recipient = (to_email or quote.customer.email or "").strip()
        if not recipient:
            raise BadRequestException(
                "NO_CUSTOMER_EMAIL",
                "This customer has no email address. Add one to the customer or enter it before sending.",
            )

        pdf_path = os.path.join(settings.PDF_STORAGE_DIR, f"{quote.quotation_number}.pdf")
        if not os.path.exists(pdf_path):
            pdf_path = None

        if not email_configured():
            await self.audit_repo.log_action(
                user_id=user_id, action="EMAIL_QUOTATION_SIMULATED", entity_type="QUOTATION",
                entity_id=quote.id, new_value={"recipient": recipient}, ip_address=ip_address,
            )
            return {
                "success": False, "delivered": False, "simulated": True,
                "status": "SIMULATED", "recipient": recipient,
                "quotation_number": quote.quotation_number, "quotation_status": quote.status,
                "admin_notified": False, "attachment": bool(pdf_path),
                "note": "Email is not configured on the server (SMTP_USER / SMTP_PASSWORD); nothing was sent.",
            }

        try:
            await asyncio.to_thread(_send_smtp, self._customer_message(quote, recipient, pdf_path))
        except (smtplib.SMTPException, OSError) as e:
            logger.error(f"Quotation email to {recipient} failed: {type(e).__name__}")
            raise AppException(
                code="EMAIL_SEND_FAILED",
                message="The email could not be sent. Check the SMTP settings and try again.",
                status_code=502,
            )

        admin_notified = True
        try:
            await asyncio.to_thread(_send_smtp, self._admin_message(quote, recipient, sent_by))
        except (smtplib.SMTPException, OSError) as e:
            admin_notified = False
            logger.warning(f"Admin notification email failed: {type(e).__name__}")

        self.session.add(
            Notification(
                user_id=user_id,
                title=f"Quotation emailed to {quote.customer.full_name}",
                message=f"Quotation #{quote.quotation_number} emailed to {recipient}.",
                type="QUOTATION_UPDATE", is_read=False,
                reference_id=quote.id, reference_type="QUOTATION",
            )
        )
        if quote.status == "GENERATED":
            quote.status = "SENT"
        await self.session.commit()
        await self.audit_repo.log_action(
            user_id=user_id, action="EMAIL_QUOTATION_SENT", entity_type="QUOTATION", entity_id=quote.id,
            new_value={"recipient": recipient, "admin_notified": admin_notified}, ip_address=ip_address,
        )
        return {
            "success": True, "delivered": True, "simulated": False,
            "status": "SENT", "recipient": recipient,
            "quotation_number": quote.quotation_number, "quotation_status": quote.status,
            "admin_notified": admin_notified, "attachment": bool(pdf_path),
        }
