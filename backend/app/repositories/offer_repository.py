from datetime import date
from typing import List, Optional
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.offer import Offer
from app.repositories.base_repository import BaseRepository


class OfferRepository(BaseRepository[Offer]):
    def __init__(self, session: AsyncSession):
        super().__init__(Offer, session)

    async def list_offers(
        self,
        active_only: bool = True,
        on_date: Optional[date] = None,
        brand_id: Optional[int] = None,
        model_id: Optional[int] = None,
        variant_id: Optional[int] = None,
    ) -> List[Offer]:
        stmt = select(Offer)
        filters = []
        if active_only:
            filters.append(Offer.active.is_(True))
        if on_date:
            filters.append(Offer.valid_from <= on_date)
            filters.append(Offer.valid_until >= on_date)

        target_filters = []
        # General offers
        target_filters.append(
            (Offer.applicable_brand_id.is_(None))
            & (Offer.applicable_model_id.is_(None))
            & (Offer.applicable_variant_id.is_(None))
        )
        if brand_id:
            target_filters.append(Offer.applicable_brand_id == brand_id)
        if model_id:
            target_filters.append(Offer.applicable_model_id == model_id)
        if variant_id:
            target_filters.append(Offer.applicable_variant_id == variant_id)

        filters.append(or_(*target_filters))

        if filters:
            stmt = stmt.where(*filters)

        stmt = stmt.order_by(Offer.id.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
