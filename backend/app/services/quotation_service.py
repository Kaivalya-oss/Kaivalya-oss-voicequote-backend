from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.quotation import Quotation, QuotationItem
from app.repositories.quotation_repository import QuotationRepository
from app.repositories.customer_repository import CustomerRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.offer_repository import OfferRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.quotation import QuotationCreate, QuotationStatusUpdate
from app.services.pdf_service import PDFService


# Allowed manual status changes. EXPIRED is also set automatically by the scheduler once
# expires_at has passed. REJECTED / EXPIRED / CANCELLED are terminal.
QUOTATION_STATUS_TRANSITIONS = {
    "DRAFT": {"GENERATED", "CANCELLED"},
    "GENERATED": {"SENT", "ACCEPTED", "REJECTED", "EXPIRED", "CANCELLED"},
    "SENT": {"ACCEPTED", "REJECTED", "EXPIRED", "CANCELLED"},
    "ACCEPTED": {"CANCELLED"},
    "REJECTED": set(),
    "EXPIRED": set(),
    "CANCELLED": set(),
}
OPEN_QUOTATION_STATUSES = ("DRAFT", "GENERATED", "SENT")


class QuotationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.quote_repo = QuotationRepository(session)
        self.customer_repo = CustomerRepository(session)
        self.vehicle_repo = VehicleRepository(session)
        self.offer_repo = OfferRepository(session)
        self.audit_repo = AuditLogRepository(session)
        self.pdf_service = PDFService()

    async def _calculate_option(
        self,
        variant_id: int,
        offer_id: Optional[int],
        accessory_ids: Optional[List[int]],
        custom_items: Optional[List[Any]],
        option_label: str = "A",
    ) -> Dict[str, Any]:
        variant = await self.vehicle_repo.get_variant_by_id(variant_id)
        if not variant:
            raise NotFoundException("VehicleVariant", variant_id)

        if variant.status != "ACTIVE":
            raise BadRequestException(
                "VARIANT_INACTIVE",
                f"Vehicle variant '{variant.name}' is discontinued and cannot be quoted.",
            )

        base_price = variant.ex_showroom_price
        insurance = variant.insurance_price
        rto = variant.rto_price

        accessories_total = Decimal("0.00")
        items_breakdown = []
        accessories = []

        if accessory_ids:
            for acc_id in accessory_ids:
                acc = await self.vehicle_repo.get_accessory_by_id(acc_id)
                if not acc:
                    raise NotFoundException("Accessory", acc_id)
                if not acc.is_active:
                    raise BadRequestException("ACCESSORY_INACTIVE", f"Accessory '{acc.name}' is no longer available.")
                accessories.append(acc)
                accessories_total += acc.price
                items_breakdown.append({
                    "item_name": acc.name,
                    "item_type": "ACCESSORY",
                    "unit_price": f"{acc.price:.2f}",
                    "quantity": 1,
                    "total_price": f"{acc.price:.2f}",
                })

        if custom_items:
            for custom_item in custom_items:
                tot = (custom_item.unit_price * Decimal(custom_item.quantity)).quantize(Decimal("0.01"))
                accessories_total += tot
                items_breakdown.append({
                    "item_name": custom_item.item_name.strip(),
                    "item_type": custom_item.item_type,
                    "unit_price": f"{custom_item.unit_price:.2f}",
                    "quantity": custom_item.quantity,
                    "total_price": f"{tot:.2f}",
                })

        discount_amount = Decimal("0.00")
        offer_title = ""
        today = date.today()

        if offer_id:
            offer = await self.offer_repo.get_by_id(offer_id)
            if not offer:
                raise NotFoundException("Offer", offer_id)

            if not offer.active:
                raise BadRequestException("OFFER_INACTIVE", f"Offer '{offer.title}' is not active.")
            if offer.valid_from > today or offer.valid_until < today:
                raise BadRequestException("OFFER_EXPIRED", f"Offer '{offer.title}' has expired or is not yet valid.")

            if offer.applicable_variant_id and offer.applicable_variant_id != variant.id:
                raise BadRequestException("OFFER_NOT_APPLICABLE", f"Offer '{offer.title}' is not applicable to variant '{variant.name}'.")
            if offer.applicable_model_id and offer.applicable_model_id != variant.model_id:
                raise BadRequestException("OFFER_NOT_APPLICABLE", "Offer is not applicable to the selected model.")

            offer_title = offer.title
            if offer.discount_type == "PERCENTAGE":
                discount_amount = (base_price * (offer.discount_value / Decimal("100.00"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                discount_amount = min(offer.discount_value, base_price)

        final_price = base_price + insurance + rto + accessories_total - discount_amount
        if final_price < Decimal("0.00"):
            final_price = Decimal("0.00")

        brand_name = variant.model.brand.name if variant.model and variant.model.brand else ""
        model_name = variant.model.name if variant.model else ""
        full_vehicle_name = f"{brand_name} {model_name} {variant.name}".strip() if (brand_name or model_name) else variant.name

        snapshot = {
            "option_letter": option_label,
            "variant_id": variant.id,
            "variant_name": variant.name,
            "brand_name": brand_name,
            "model_name": model_name,
            "full_name": full_vehicle_name,
            "engine_cc": variant.engine_cc or "",
            "fuel_type": variant.fuel_type,
            "transmission": variant.transmission,
            "base_price": f"{base_price:.2f}",
            "insurance": f"{insurance:.2f}",
            "rto": f"{rto:.2f}",
            "accessories": f"{accessories_total:.2f}",
            "discount": f"{discount_amount:.2f}",
            "offer_title": offer_title,
            "final_price": f"{final_price:.2f}",
            "items": items_breakdown,
        }

        return {
            "variant": variant,
            "base_price": base_price,
            "insurance": insurance,
            "rto": rto,
            "accessories_total": accessories_total,
            "discount_amount": discount_amount,
            "offer_title": offer_title,
            "final_price": final_price,
            "full_name": full_vehicle_name,
            "accessories": accessories,
            "snapshot": snapshot,
        }

    async def create_quotation(
        self,
        req: QuotationCreate,
        sales_executive_id: int,
        ip_address: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Quotation, bool]:
        """Create a quotation. Returns (quotation, replayed); replayed is True when the
        idempotency key was already used and the original quotation is returned."""
        key = idempotency_key.strip() if idempotency_key else None
        if key:
            existing = await self.quote_repo.get_by_idempotency_key(key)
            if existing:
                return self._replay(existing, req, sales_executive_id), True

        # 1. Validate Customer
        customer = await self.customer_repo.get_by_id(req.customer_id)
        if not customer:
            raise NotFoundException("Customer", req.customer_id)

        # 2. Calculate Option A
        opt_a = await self._calculate_option(
            variant_id=req.variant_id,
            offer_id=req.offer_id,
            accessory_ids=req.accessory_ids,
            custom_items=req.custom_items,
            option_label="A",
        )

        # 3. Calculate Option B if comparison requested
        opt_b = None
        if req.compare_variant_id:
            if req.compare_variant_id == req.variant_id:
                raise BadRequestException(
                    "DUPLICATE_VARIANT_COMPARE",
                    "Choose two different vehicle variants for comparison.",
                )
            opt_b = await self._calculate_option(
                variant_id=req.compare_variant_id,
                offer_id=req.compare_offer_id,
                accessory_ids=req.accessory_ids,
                custom_items=req.custom_items,
                option_label="B",
            )

        # 4. Unique Quotation Number & Expiry
        quotation_number = await self.quote_repo.generate_unique_quotation_number()
        expires_at = datetime.now(timezone.utc) + timedelta(days=15)

        # The primary quotation record holds Option A's independent figures (never the sum of A + B).
        quotation = Quotation(
            quotation_number=quotation_number,
            customer_id=req.customer_id,
            sales_executive_id=sales_executive_id,
            variant_id=req.variant_id,
            offer_id=req.offer_id,
            base_price=opt_a["base_price"],
            insurance=opt_a["insurance"],
            rto=opt_a["rto"],
            accessories=opt_a["accessories_total"],
            discount=opt_a["discount_amount"],
            final_price=opt_a["final_price"],
            payment_mode=req.payment_mode,
            status="GENERATED",
            expires_at=expires_at,
            idempotency_key=key,
        )

        # 5. Build Quotation Items (VEHICLE_OPTION items for comparison, plus accessories & custom items)
        quotation_items = []
        if opt_b:
            item_a = QuotationItem(
                item_name=f"Option A: {opt_a['full_name']}",
                item_type="VEHICLE_OPTION",
                unit_price=opt_a["final_price"],
                quantity=1,
                total_price=opt_a["final_price"],
                details_json=opt_a["snapshot"],
                **self._typed_option_fields(opt_a),
            )
            item_b = QuotationItem(
                item_name=f"Option B: {opt_b['full_name']}",
                item_type="VEHICLE_OPTION",
                unit_price=opt_b["final_price"],
                quantity=1,
                total_price=opt_b["final_price"],
                details_json=opt_b["snapshot"],
                **self._typed_option_fields(opt_b),
            )
            quotation_items.extend([item_a, item_b])

        if opt_a["accessories"]:
            for acc in opt_a["accessories"]:
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
            for ci in req.custom_items:
                quotation_items.append(
                    QuotationItem(
                        item_name=ci.item_name.strip(),
                        item_type=ci.item_type or "CUSTOM",
                        unit_price=ci.unit_price,
                        quantity=ci.quantity or 1,
                        total_price=ci.unit_price * (ci.quantity or 1),
                    )
                )
        quotation.items = quotation_items

        try:
            created_quotation = await self.quote_repo.create(quotation)
        except IntegrityError:
            # A concurrent request with the same idempotency key won the insert.
            await self.session.rollback()
            if key:
                existing = await self.quote_repo.get_by_idempotency_key(key)
                if existing:
                    return self._replay(existing, req, sales_executive_id), True
            raise

        # 6. Generate PDF (one document containing both options if comparison)
        options_list = [opt_a["snapshot"]]
        if opt_b:
            options_list.append(opt_b["snapshot"])

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
                "name": opt_a["variant"].name,
                "engine_cc": opt_a["variant"].engine_cc or "",
                "fuel_type": opt_a["variant"].fuel_type,
                "transmission": opt_a["variant"].transmission,
            },
            "base_price": f"{opt_a['base_price']:.2f}",
            "insurance": f"{opt_a['insurance']:.2f}",
            "rto": f"{opt_a['rto']:.2f}",
            "accessories": f"{opt_a['accessories_total']:.2f}",
            "discount": f"{opt_a['discount_amount']:.2f}",
            "offer_title": opt_a["offer_title"],
            "final_price": f"{opt_a['final_price']:.2f}",
            "payment_mode": req.payment_mode,
            "items": opt_a["snapshot"]["items"],
            "options": options_list,
            "is_comparison": len(options_list) > 1,
        }

        try:
            # Multi-tenant mode renders on demand: customer details never rest on VoiceQuote's disk.
            if not settings.MULTI_TENANT_ENABLED:
                self.pdf_service.generate_quotation_pdf(pdf_data)
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
                "final_price": None if opt_b else str(opt_a["final_price"]),
                "option_prices": {"A": str(opt_a["final_price"]), "B": str(opt_b["final_price"])} if opt_b else None,
                "payment_mode": req.payment_mode,
                "customer_id": req.customer_id,
                "variant_id": req.variant_id,
                "compare_variant_id": req.compare_variant_id,
            },
            ip_address=ip_address,
        )

        return await self.quote_repo.get_by_id(created_quotation.id), False  # type: ignore

    @staticmethod
    def _typed_option_fields(opt: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "vehicle_id": opt["variant"].id,
            "model_name_snapshot": opt["snapshot"]["model_name"] or None,
            "variant_snapshot": opt["variant"].name,
            "ex_showroom": opt["base_price"],
            "rto": opt["rto"],
            "insurance": opt["insurance"],
            "accessories": opt["accessories_total"],
            "other_charges": Decimal("0.00"),
            "discount": opt["discount_amount"],
            "total": opt["final_price"],
        }

    @staticmethod
    def pdf_data_from_quote(quote: Quotation) -> Dict[str, Any]:
        """Rebuild the PDF input from stored figures; nothing is recalculated."""
        options = [it.details_json or {} for it in quote.items if it.item_type == "VEHICLE_OPTION"]
        items = [
            {
                "item_name": it.item_name,
                "item_type": it.item_type,
                "unit_price": f"{it.unit_price:.2f}",
                "quantity": it.quantity,
                "total_price": f"{it.total_price:.2f}",
            }
            for it in quote.items
            if it.item_type != "VEHICLE_OPTION"
        ]
        customer, executive, variant = quote.customer, quote.sales_executive, quote.variant
        return {
            "quotation_number": quote.quotation_number,
            "date": quote.created_at.strftime("%d-%b-%Y"),
            "valid_until": quote.expires_at.strftime("%d-%b-%Y") if quote.expires_at else "",
            "status": quote.status,
            "customer": {
                "full_name": customer.full_name,
                "phone": customer.phone,
                "email": customer.email or "",
                "city": customer.city or "",
                "address": customer.address or "",
            },
            "sales_executive": {
                "full_name": executive.full_name,
                "email": executive.email,
                "phone": executive.phone or "",
            },
            "variant": {
                "name": variant.name,
                "engine_cc": variant.engine_cc or "",
                "fuel_type": variant.fuel_type,
                "transmission": variant.transmission,
            },
            "base_price": f"{quote.base_price:.2f}",
            "insurance": f"{quote.insurance:.2f}",
            "rto": f"{quote.rto:.2f}",
            "accessories": f"{quote.accessories:.2f}",
            "discount": f"{quote.discount:.2f}",
            "offer_title": quote.offer.title if quote.offer else "",
            "final_price": f"{quote.final_price:.2f}",
            "payment_mode": quote.payment_mode,
            "items": items,
            "options": options,
            "is_comparison": len(options) >= 2,
        }

    @staticmethod
    def _replay(existing: Quotation, req: QuotationCreate, sales_executive_id: int) -> Quotation:
        if existing.sales_executive_id != sales_executive_id:
            raise ConflictException(
                code="IDEMPOTENCY_KEY_CONFLICT",
                message="This idempotency key was already used by another user.",
            )
        if (
            existing.customer_id != req.customer_id
            or existing.variant_id != req.variant_id
            or existing.payment_mode != req.payment_mode
        ):
            raise ConflictException(
                code="IDEMPOTENCY_KEY_MISMATCH",
                message="This idempotency key was already used for a different quotation request.",
            )
        return existing

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
        target = req.status.upper().strip()
        if target == old_status:
            return quote

        if (
            target == "ACCEPTED"
            and quote.expires_at is not None
            and quote.expires_at < datetime.now(timezone.utc)
            and old_status in OPEN_QUOTATION_STATUSES
        ):
            quote.status = "EXPIRED"
            await self.quote_repo.update(quote)
            raise BadRequestException(
                "QUOTATION_EXPIRED",
                f"Quotation {quote.quotation_number} expired on {quote.expires_at:%d-%b-%Y} and cannot be accepted. "
                "Create a new quotation with current prices.",
            )

        allowed = QUOTATION_STATUS_TRANSITIONS.get(old_status, set())
        if target not in allowed:
            raise BadRequestException(
                "INVALID_QUOTATION_TRANSITION",
                f"Cannot change quotation {quote.quotation_number} from '{old_status}' to '{target}'. "
                f"Allowed: {sorted(allowed) or 'none (final state)'}.",
            )
        quote.status = target
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


async def expire_overdue_quotations(session: AsyncSession) -> int:
    """Mark open quotations whose expires_at has passed as EXPIRED. Returns the count."""
    from sqlalchemy import update

    result = await session.execute(
        update(Quotation)
        .where(
            Quotation.status.in_(OPEN_QUOTATION_STATUSES),
            Quotation.expires_at.is_not(None),
            Quotation.expires_at < datetime.now(timezone.utc),
        )
        .values(status="EXPIRED")
    )
    await session.commit()
    return result.rowcount or 0
