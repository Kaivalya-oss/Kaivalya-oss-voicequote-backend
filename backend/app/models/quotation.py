from datetime import datetime
from decimal import Decimal
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.user import User
    from app.models.vehicle import VehicleVariant
    from app.models.offer import Offer


class Quotation(Base, TimestampMixin):
    __tablename__ = "quotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quotation_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    sales_executive_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("vehicle_variants.id", ondelete="RESTRICT"), nullable=False, index=True)
    offer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"), nullable=True)

    # Financial Breakdowns - Strict Decimal precision
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    insurance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    rto: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    accessories: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    final_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # Loan / EMI details
    down_payment: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    loan_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"), nullable=False)
    loan_tenure: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # in months
    emi: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)

    # Status & Validity
    status: Mapped[str] = mapped_column(String(30), default="GENERATED", index=True, nullable=False)
    # Statuses: DRAFT, GENERATED, SENT, ACCEPTED, REJECTED, EXPIRED, CANCELLED
    pdf_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    customer: Mapped["Customer"] = relationship("Customer", back_populates="quotations", lazy="joined")
    sales_executive: Mapped["User"] = relationship("User", back_populates="quotations", lazy="joined")
    variant: Mapped["VehicleVariant"] = relationship("VehicleVariant", back_populates="quotations", lazy="joined")
    offer: Mapped[Optional["Offer"]] = relationship("Offer", back_populates="quotations", lazy="joined")
    items: Mapped[List["QuotationItem"]] = relationship("QuotationItem", back_populates="quotation", cascade="all, delete-orphan", lazy="selectin")


class QuotationItem(Base):
    __tablename__ = "quotation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quotation_id: Mapped[int] = mapped_column(ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(150), nullable=False)
    item_type: Mapped[str] = mapped_column(String(50), default="ACCESSORY", nullable=False)  # ACCESSORY, INSURANCE_ADDON, EXTENDED_WARRANTY
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    quotation: Mapped["Quotation"] = relationship("Quotation", back_populates="items")
