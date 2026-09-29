from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, NotFoundException
from app.models.quotation import Quotation, QuotationItem
from app.repositories.quotation_repository import QuotationRepository
from app.repositories.customer_repository import CustomerRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.offer_repository import OfferRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.quotation import QuotationCreate, QuotationStatusUpdate
from app.services.emi_service import EMIService
from app.services.pdf_service import PDFService


class QuotationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.quote_repo = QuotationRepository(session)
        self.customer_repo = CustomerRepository(session)
        self.vehicle_repo = VehicleRepository(session)
        self.offer_repo = OfferRepository(session)
        self.audit_repo = AuditLogRepository(session)
        self.pdf_service = PDFService()

    async def create_quotation(
        self,
        req: QuotationCreate,
        sales_executive_id: int,
        ip_address: Optional[str] = None,
    ) -> Quotation:
        # 1. Validate Customer
        customer = await self.customer_repo.get_by_id(req.customer_id)
        if not customer:
            raise NotFoundException("Customer", req.customer_id)

        # 2. Validate Vehicle Variant
        variant = await self.vehicle_repo.get_variant_by_id(req.variant_id)
        if not variant:
            raise NotFoundException("VehicleVariant", req.variant_id)

        if variant.status != "ACTIVE":
            raise BadRequestException("VARIANT_INACTIVE", f"Vehicle variant '{variant.name}' is discontinued and cannot be quoted.")

        # 3. Base pricing from database
        base_price = variant.ex_showroom_price
        insurance = variant.insurance_price
        rto = variant.rto_price

        # 4. Accessories Calculation
        accessories_total = Decimal("0.00")
        quotation_items: List[QuotationItem] = []

        if req.accessory_ids:
            for acc_id in req.accessory_ids:
                acc = await self.vehicle_repo.get_accessory_by_id(acc_id)
                if acc and acc.is_active:
                    accessories_total += acc.price
                    quotation_items.append(
                        QuotationItem(
                            item_name=acc.name,
                            item_type="ACCESSORY",
                            unit_price=acc.price,
                            quantity=1,
                            total_price=acc.price,
                        )
                    )

        if req.custom_items:
            for custom_item in req.custom_items:
                tot = (custom_item.unit_price * Decimal(custom_item.quantity)).quantize(Decimal("0.01"))
                accessories_total += tot
                quotation_items.append(
                    QuotationItem(
                        item_name=custom_item.item_name.strip(),
                        item_type=custom_item.item_type,
                        unit_price=custom_item.unit_price,
                        quantity=custom_item.quantity,
                        total_price=tot,
                    )
                )

        # 5. Applicable Offers & Discount Calculation
        discount_amount = Decimal("0.00")
        offer_title = ""
        today = date.today()

        if req.offer_id:
            offer = await self.offer_repo.get_by_id(req.offer_id)
            if not offer:
                raise NotFoundException("Offer", req.offer_id)

            # Check Offer Validity
            if not offer.active:
                raise BadRequestException("OFFER_INACTIVE", f"Offer '{offer.title}' is not active.")
            if offer.valid_from > today or offer.valid_until < today:
                raise BadRequestException("OFFER_EXPIRED", f"Offer '{offer.title}' has expired or is not yet valid.")

            # Check Vehicle Applicability
            if offer.applicable_variant_id and offer.applicable_variant_id != variant.id:
                raise BadRequestException("OFFER_NOT_APPLICABLE", "Offer is not applicable to the selected variant.")
            if offer.applicable_model_id and offer.applicable_model_id != variant.model_id:
                raise BadRequestException("OFFER_NOT_APPLICABLE", "Offer is not applicable to the selected model.")

            offer_title = offer.title
            if offer.discount_type == "PERCENTAGE":
                discount_amount = (base_price * (offer.discount_value / Decimal("100.00"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                discount_amount = min(offer.discount_value, base_price)

        # 6. Final On-Road Calculation
        final_price = base_price + insurance + rto + accessories_total - discount_amount
        if final_price < Decimal("0.00"):
            final_price = Decimal("0.00")

        # 7. Down Payment & Loan
        if req.down_payment > final_price:
            raise BadRequestException("INVALID_DOWN_PAYMENT", f"Down payment (Rs. {req.down_payment}) cannot exceed final on-road price (Rs. {final_price}).")

        loan_amount = (final_price - req.down_payment).quantize(Decimal("0.01"))
        emi_amount = Decimal("0.00")

        if loan_amount > Decimal("0.00") and req.loan_tenure > 0:
            emi_calc = EMIService.calculate(
                principal=loan_amount,
                annual_interest_rate=req.interest_rate,
                tenure_months=req.loan_tenure,
            )
            emi_amount = emi_calc.monthly_emi

        # 8. Unique Quotation Number & Expiry
        quotation_number = await self.quote_repo.generate_unique_quotation_number()
        expires_at = datetime.now(timezone.utc) + timedelta(days=15)

        quotation = Quotation(
            quotation_number=quotation_number,
            customer_id=req.customer_id,
            sales_executive_id=sales_executive_id,
            variant_id=req.variant_id,
            offer_id=req.offer_id,
            base_price=base_price,
            insurance=insurance,
            rto=rto,
            accessories=accessories_total,
            discount=discount_amount,
            final_price=final_price,
            down_payment=req.down_payment,
            loan_amount=loan_amount,
            interest_rate=req.interest_rate,
            loan_tenure=req.loan_tenure,
            emi=emi_amount,
            status="GENERATED",
            expires_at=expires_at,
        )

        for item in quotation_items:
            quotation.items.append(item)

        created_quotation = await self.quote_repo.create(quotation)

        # 9. Generate PDF
        pdf_data = {
            "quotation_number": created_quotation.quotation_number,
            "date": created_quotation.created_at.strftime("%d-%b-%Y"),
            "valid_until": expires_at.strftime("%d-%b-%Y"),
            "status": created_quotation.status,
            "customer": {
                "full_name": customer.full_name,
                "phone": customer.phone,
                "email": customer.email or "",
                "city": customer.city or "",
                "address": customer.address or "",
            },
            "sales_executive": {
                "full_name": created_quotation.sales_executive.full_name,
                "email": created_quotation.sales_executive.email,
                "phone": created_quotation.sales_executive.phone or "",
            },
            "variant": {
                "name": variant.name,
                "engine_cc": variant.engine_cc or "",
                "fuel_type": variant.fuel_type,
                "transmission": variant.transmission,
            },
            "base_price": f"{base_price:.2f}",
            "insurance": f"{insurance:.2f}",
            "rto": f"{rto:.2f}",
            "accessories": f"{accessories_total:.2f}",
            "discount": f"{discount_amount:.2f}",
            "offer_title": offer_title,
            "final_price": f"{final_price:.2f}",
            "down_payment": f"{req.down_payment:.2f}",
            "loan_amount": f"{loan_amount:.2f}",
            "interest_rate": f"{req.interest_rate:.2f}",
            "loan_tenure": req.loan_tenure,
            "emi": f"{emi_amount:.2f}",
            "items": [
                {
                    "item_name": item.item_name,
                    "quantity": item.quantity,
                    "unit_price": f"{item.unit_price:.2f}",
                    "total_price": f"{item.total_price:.2f}",
                }
                for item in quotation_items
            ],
        }

        try:
            pdf_path = self.pdf_service.generate_quotation_pdf(pdf_data)
            created_quotation.pdf_url = f"/api/v1/quotations/{created_quotation.id}/pdf"
            await self.quote_repo.update(created_quotation)
        except Exception as e:
            # Do not fail database transaction if PDF generation runs into an IO hiccup
            pass

        await self.audit_repo.log_action(
            user_id=sales_executive_id,
            action="QUOTATION_GENERATED",
            entity_type="QUOTATION",
            entity_id=created_quotation.id,
            new_value={
                "quotation_number": quotation_number,
                "final_price": str(final_price),
                "customer_id": req.customer_id,
                "variant_id": req.variant_id,
            },
            ip_address=ip_address,
        )

        return await self.quote_repo.get_by_id(created_quotation.id)  # type: ignore

    async def get_quotation(self, quotation_id: int) -> Quotation:
        quote = await self.quote_repo.get_by_id(quotation_id)
        if not quote:
            raise NotFoundException("Quotation", quotation_id)
        return quote

    async def list_quotations(
        self,
        customer_id: Optional[int] = None,
        sales_executive_id: Optional[int] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Quotation], int]:
        skip = (page - 1) * page_size
        return await self.quote_repo.search_quotations(
            customer_id=customer_id,
            sales_executive_id=sales_executive_id,
            status=status,
            from_date=from_date,
            to_date=to_date,
            skip=skip,
            limit=page_size,
        )

    async def update_status(
        self,
        quotation_id: int,
        req: QuotationStatusUpdate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Quotation:
        quote = await self.get_quotation(quotation_id)
        old_status = quote.status
        quote.status = req.status.upper().strip()
        updated = await self.quote_repo.update(quote)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="QUOTATION_STATUS_UPDATED",
            entity_type="QUOTATION",
            entity_id=quotation_id,
            old_value={"status": old_status},
            new_value={"status": quote.status, "reason": req.reason},
            ip_address=ip_address,
        )
        return updated
