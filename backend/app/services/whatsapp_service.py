from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
import os
from typing import Any, Dict, Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestException, NotFoundException
from app.core.logging import logger
from app.models.notification import Notification
from app.models.quotation import Quotation
from app.repositories.quotation_repository import QuotationRepository
from app.repositories.audit_log_repository import AuditLogRepository


class BaseWhatsAppProvider(ABC):
    @abstractmethod
    async def send_quotation_message(
        self,
        to_phone: str,
        customer_name: str,
        quotation_number: str,
        vehicle_name: str,
        final_price: Decimal,
        emi: Decimal,
        pdf_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send quotation WhatsApp message and return delivery response."""
        pass


class MetaCloudWhatsAppProvider(BaseWhatsAppProvider):
    """Production provider using Meta WhatsApp Business Cloud API."""

    def __init__(self, access_token: str, phone_number_id: str, api_version: str):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version
        self.base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

    async def send_quotation_message(
        self,
        to_phone: str,
        customer_name: str,
        quotation_number: str,
        vehicle_name: str,
        final_price: Decimal,
        emi: Decimal,
        pdf_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Clean phone number (strip spaces/dashes)
        clean_phone = "".join(filter(str.isdigit, to_phone))

        body_text = (
            f"Hello {customer_name},\n\n"
            f"Thank you for visiting our showroom! Here is your official quotation for the *{vehicle_name}*.\n\n"
            f"📄 *Quotation No:* {quotation_number}\n"
            f"💰 *On-Road Price:* Rs. {final_price:,.2f}\n"
        )
        if emi > Decimal("0.00"):
            body_text += f"💳 *Estimated Monthly EMI:* Rs. {emi:,.2f}/month\n"

        body_text += (
            f"\nPlease find your detailed quotation document attached.\n"
            f"Feel free to reply to this message or visit our showroom to book your vehicle!"
        )

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "text",
            "text": {"body": body_text},
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(self.base_url, headers=headers, json=payload)
                res.raise_for_status()
                data = res.json()
                logger.info(f"WhatsApp Cloud API message sent to {clean_phone}: {data}")
                return {
                    "provider": "MetaCloudAPI",
                    "status": "SENT",
                    "message_id": data.get("messages", [{}])[0].get("id", "wamid.success"),
                    "details": data,
                }
        except Exception as e:
            logger.error(f"WhatsApp Cloud API error: {str(e)}")
            raise BadRequestException("WHATSAPP_API_FAILURE", f"Failed to send WhatsApp message via Meta Cloud: {str(e)}")


class MockWhatsAppProvider(BaseWhatsAppProvider):
    """Development and fallback mock provider that records delivery without external credentials."""

    async def send_quotation_message(
        self,
        to_phone: str,
        customer_name: str,
        quotation_number: str,
        vehicle_name: str,
        final_price: Decimal,
        emi: Decimal,
        pdf_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_phone = "".join(filter(str.isdigit, to_phone))
        simulated_msg_id = f"mock_wamid_{quotation_number}_{int(datetime.now(timezone.utc).timestamp())}"
        
        logger.info(
            f"[MOCK WHATSAPP DISPATCH] To: {clean_phone} | Customer: {customer_name} | "
            f"Quote: {quotation_number} | Vehicle: {vehicle_name} | Price: Rs. {final_price:,.2f} | "
            f"Attachment: {pdf_path}"
        )
        return {
            "provider": "MockDevelopmentProvider",
            "status": "MOCK_SENT",
            "message_id": simulated_msg_id,
            "recipient": clean_phone,
            "note": "WhatsApp Cloud API credentials not configured; simulated successfully in dev mode.",
        }


class WhatsAppService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.quote_repo = QuotationRepository(session)
        self.audit_repo = AuditLogRepository(session)

        # Decide provider based on credentials
        if settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID:
            self.provider: BaseWhatsAppProvider = MetaCloudWhatsAppProvider(
                access_token=settings.WHATSAPP_ACCESS_TOKEN,
                phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
                api_version=settings.WHATSAPP_API_VERSION,
            )
        else:
            self.provider = MockWhatsAppProvider()

    async def send_quotation_via_whatsapp(
        self,
        quotation_id: int,
        user_id: int,
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        quote = await self.quote_repo.get_by_id(quotation_id)
        if not quote:
            raise NotFoundException("Quotation", quotation_id)

        customer = quote.customer
        if not customer or not customer.phone:
            raise BadRequestException("NO_PHONE_NUMBER", "Customer has no registered phone number for WhatsApp.")

        pdf_path = os.path.join(settings.PDF_STORAGE_DIR, f"{quote.quotation_number}.pdf")
        if not os.path.exists(pdf_path):
            pdf_path = None

        result = await self.provider.send_quotation_message(
            to_phone=customer.phone,
            customer_name=customer.full_name,
            quotation_number=quote.quotation_number,
            vehicle_name=quote.variant.name,
            final_price=quote.final_price,
            emi=quote.emi,
            pdf_path=pdf_path,
        )

        # Record notification in database
        notification = Notification(
            user_id=user_id,
            title=f"Quotation Sent to {customer.full_name}",
            message=f"Quotation #{quote.quotation_number} sent via WhatsApp to {customer.phone}. Status: {result['status']}",
            type="QUOTATION_UPDATE",
            is_read=False,
            reference_id=quote.id,
            reference_type="QUOTATION",
        )
        self.session.add(notification)

        # Transition quotation status if GENERATED
        if quote.status == "GENERATED":
            quote.status = "SENT"

        await self.session.commit()

        await self.audit_repo.log_action(
            user_id=user_id,
            action="WHATSAPP_QUOTATION_SENT",
            entity_type="QUOTATION",
            entity_id=quote.id,
            new_value={"recipient": customer.phone, "provider": result["provider"], "status": result["status"]},
            ip_address=ip_address,
        )

        return {
            "success": True,
            "quotation_number": quote.quotation_number,
            "delivery": result,
        }
