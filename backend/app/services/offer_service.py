from datetime import date
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, NotFoundException
from app.models.offer import Offer
from app.repositories.offer_repository import OfferRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.offer import OfferCreate, OfferUpdate


class OfferService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.offer_repo = OfferRepository(session)
        self.audit_repo = AuditLogRepository(session)

    async def create_offer(
        self,
        req: OfferCreate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Offer:
        offer = Offer(
            title=req.title.strip(),
            description=req.description,
            discount_type=req.discount_type,
            discount_value=req.discount_value,
            valid_from=req.valid_from,
            valid_until=req.valid_until,
            applicable_brand_id=req.applicable_brand_id,
            applicable_model_id=req.applicable_model_id,
            applicable_variant_id=req.applicable_variant_id,
            min_order_value=req.min_order_value,
            active=req.active,
        )
        created = await self.offer_repo.create(offer)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="OFFER_CREATED",
            entity_type="OFFER",
            entity_id=created.id,
            new_value={"title": created.title, "discount_type": created.discount_type, "value": str(created.discount_value)},
            ip_address=ip_address,
        )
        return created

    async def get_offer(self, offer_id: int) -> Offer:
        offer = await self.offer_repo.get_by_id(offer_id)
        if not offer:
            raise NotFoundException("Offer", offer_id)
        return offer

    async def list_offers(
        self,
        active_only: bool = True,
        on_date: Optional[date] = None,
        brand_id: Optional[int] = None,
        model_id: Optional[int] = None,
        variant_id: Optional[int] = None,
    ) -> List[Offer]:
        return await self.offer_repo.list_offers(
            active_only=active_only,
            on_date=on_date,
            brand_id=brand_id,
            model_id=model_id,
            variant_id=variant_id,
        )

    async def update_offer(
        self,
        offer_id: int,
        req: OfferUpdate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Offer:
        offer = await self.get_offer(offer_id)

        old_data = {"title": offer.title, "active": offer.active, "value": str(offer.discount_value)}

        if req.title is not None:
            offer.title = req.title.strip()
        if req.description is not None:
            offer.description = req.description
        if req.discount_type is not None:
            offer.discount_type = req.discount_type
        if req.discount_value is not None:
            offer.discount_value = req.discount_value
        if req.valid_from is not None:
            offer.valid_from = req.valid_from
        if req.valid_until is not None:
            offer.valid_until = req.valid_until
        if req.applicable_brand_id is not None:
            offer.applicable_brand_id = req.applicable_brand_id
        if req.applicable_model_id is not None:
            offer.applicable_model_id = req.applicable_model_id
        if req.applicable_variant_id is not None:
            offer.applicable_variant_id = req.applicable_variant_id
        if req.min_order_value is not None:
            offer.min_order_value = req.min_order_value
        if req.active is not None:
            offer.active = req.active

        if offer.valid_until < offer.valid_from:
            raise BadRequestException("INVALID_DATE_RANGE", "valid_until cannot be earlier than valid_from.")

        updated = await self.offer_repo.update(offer)
        await self.audit_repo.log_action(
            user_id=user_id,
            action="OFFER_UPDATED",
            entity_type="OFFER",
            entity_id=offer_id,
            old_value=old_data,
            new_value={"title": updated.title, "active": updated.active, "value": str(updated.discount_value)},
            ip_address=ip_address,
        )
        return updated

    async def delete_offer(
        self,
        offer_id: int,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> None:
        offer = await self.get_offer(offer_id)
        await self.offer_repo.delete(offer)
        await self.audit_repo.log_action(user_id, "OFFER_DELETED", "OFFER", offer_id, ip_address=ip_address)
