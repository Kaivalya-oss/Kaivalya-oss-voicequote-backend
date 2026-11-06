"""Sends Owner invitations by email. This is the only place the raw invitation token leaves the process.

The token never appears in an API response, an audit event or a log, so the Platform Admin can't use it
to enter a showroom. The link puts it after a # so it isn't sent to web servers or kept in their logs.
"""
import smtplib
from email.message import EmailMessage

from app.platform_admin.config import get_admin_settings


class InvitationDeliveryError(Exception):
    pass


def delivery_configured() -> bool:
    settings = get_admin_settings()
    return bool(settings.ADMIN_SMTP_HOST and settings.ADMIN_SMTP_FROM and settings.ADMIN_OWNER_INVITE_URL)


def send_message(message: EmailMessage) -> None:
    settings = get_admin_settings()
    with smtplib.SMTP(settings.ADMIN_SMTP_HOST, settings.ADMIN_SMTP_PORT, timeout=20) as smtp:
        if settings.ADMIN_SMTP_STARTTLS:
            smtp.starttls()
        if settings.ADMIN_SMTP_USER:
            smtp.login(settings.ADMIN_SMTP_USER, settings.ADMIN_SMTP_PASSWORD)
        smtp.send_message(message)


def send_owner_invitation(to_email: str, showroom_name: str, token: str) -> None:
    settings = get_admin_settings()
    hours = settings.ADMIN_INVITATION_EXPIRE_HOURS
    message = EmailMessage()
    message["Subject"] = f"Set up {showroom_name} on VoiceQuote"
    message["From"] = settings.ADMIN_SMTP_FROM
    message["To"] = to_email
    message.set_content(
        f"You've been invited to be the Owner of {showroom_name} on VoiceQuote.\n\n"
        f"Use this link to create your account. It works once, and expires in {hours} hours:\n"
        f"{settings.ADMIN_OWNER_INVITE_URL}#token={token}\n\n"
        "If you weren't expecting this, you can ignore this email."
    )
    try:
        send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        # The class name only: SMTP errors can quote the message or its recipients.
        raise InvitationDeliveryError(type(exc).__name__) from None
