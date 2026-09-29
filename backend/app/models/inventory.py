from datetime import date
from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, Date, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.vehicle import VehicleVariant


class InventoryUnit(Base, TimestampMixin):
    __tablename__ = "inventory_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("vehicle_variants.id", ondelete="RESTRICT"), nullable=False, index=True)
    vin_chassis_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    engine_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    color: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="AVAILABLE", index=True, nullable=False)
    # Statuses: AVAILABLE, RESERVED, SOLD, SERVICE, IN_TRANSIT
    branch_name: Mapped[str] = mapped_column(String(100), default="Main Branch", nullable=False)
    arrival_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    variant: Mapped["VehicleVariant"] = relationship("VehicleVariant", back_populates="inventory_units", lazy="joined")
