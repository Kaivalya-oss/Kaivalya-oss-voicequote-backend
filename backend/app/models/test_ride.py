from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.vehicle import VehicleVariant
    from app.models.user import User


class TestRide(Base, TimestampMixin):
    __tablename__ = "test_rides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("vehicle_variants.id", ondelete="RESTRICT"), nullable=False, index=True)
    sales_executive_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="REQUESTED", index=True, nullable=False)
    # Statuses: REQUESTED, CONFIRMED, COMPLETED, CANCELLED, NO_SHOW
    driving_license_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    feedback_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1 to 5
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    customer: Mapped["Customer"] = relationship("Customer", back_populates="test_rides", lazy="joined")
    variant: Mapped["VehicleVariant"] = relationship("VehicleVariant", back_populates="test_rides", lazy="joined")
    sales_executive: Mapped["User"] = relationship("User", back_populates="test_rides", lazy="joined")
