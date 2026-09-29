from decimal import Decimal
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, Text, Numeric, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.quotation import Quotation
    from app.models.lead import Lead
    from app.models.test_ride import TestRide


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    occupation: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(50), default="English", nullable=False)
    budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)

    quotations: Mapped[List["Quotation"]] = relationship("Quotation", back_populates="customer")
    leads: Mapped[List["Lead"]] = relationship("Lead", back_populates="customer")
    test_rides: Mapped[List["TestRide"]] = relationship("TestRide", back_populates="customer")
