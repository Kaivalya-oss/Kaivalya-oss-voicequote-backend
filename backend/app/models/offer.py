from datetime import date
from decimal import Decimal
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, Date, ForeignKey, Integer, Numeric, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.quotation import Quotation


class Offer(Base, TimestampMixin):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discount_type: Mapped[str] = mapped_column(String(20), nullable=False)  # PERCENTAGE, FIXED
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    
    applicable_brand_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicle_brands.id", ondelete="SET NULL"), nullable=True)
    applicable_model_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicle_models.id", ondelete="SET NULL"), nullable=True)
    applicable_variant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicle_variants.id", ondelete="SET NULL"), nullable=True)
    min_order_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    quotations: Mapped[List["Quotation"]] = relationship("Quotation", back_populates="offer")
