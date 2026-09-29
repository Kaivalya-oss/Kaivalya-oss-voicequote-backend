from datetime import date, datetime, timezone
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, Date, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.user import User
    from app.models.vehicle import VehicleVariant
    from app.models.followup import Followup


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_salesperson_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(50), default="WALK_IN", nullable=False)
    # Sources: WALK_IN, ONLINE, REFERRAL, PHONE_INQUIRY, CAMPAIGN
    status: Mapped[str] = mapped_column(String(50), default="NEW", index=True, nullable=False)
    # Statuses: NEW, CONTACTED, INTERESTED, TEST_RIDE, NEGOTIATION, BOOKED, PURCHASED, LOST
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM", index=True, nullable=False)
    # Priorities: LOW, MEDIUM, HIGH, URGENT
    interested_variant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicle_variants.id", ondelete="SET NULL"), nullable=True)
    expected_purchase_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    next_followup_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    customer: Mapped["Customer"] = relationship("Customer", back_populates="leads", lazy="joined")
    assigned_salesperson: Mapped[Optional["User"]] = relationship("User", back_populates="assigned_leads", lazy="joined")
    interested_variant: Mapped[Optional["VehicleVariant"]] = relationship("VehicleVariant", back_populates="leads", lazy="joined")
    followups: Mapped[List["Followup"]] = relationship("Followup", back_populates="lead", cascade="all, delete-orphan", lazy="selectin")
    status_history: Mapped[List["LeadStatusHistory"]] = relationship("LeadStatusHistory", back_populates="lead", cascade="all, delete-orphan", lazy="selectin")


class LeadStatusHistory(Base):
    __tablename__ = "lead_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status: Mapped[str] = mapped_column(String(50), nullable=False)
    to_status: Mapped[str] = mapped_column(String(50), nullable=False)
    changed_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    lead: Mapped["Lead"] = relationship("Lead", back_populates="status_history")
