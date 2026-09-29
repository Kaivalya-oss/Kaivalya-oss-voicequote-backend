from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.inventory import InventoryUnit
    from app.models.quotation import Quotation
    from app.models.lead import Lead
    from app.models.test_ride import TestRide


class VehicleBrand(Base):
    __tablename__ = "vehicle_brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    models: Mapped[List["VehicleModel"]] = relationship("VehicleModel", back_populates="brand", cascade="all, delete-orphan")


class VehicleModel(Base):
    __tablename__ = "vehicle_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("vehicle_brands.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(50), default="SCOOTER", nullable=False)  # SCOOTER, MOTORCYCLE, EV
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    brand: Mapped["VehicleBrand"] = relationship("VehicleBrand", back_populates="models", lazy="joined")
    variants: Mapped[List["VehicleVariant"]] = relationship("VehicleVariant", back_populates="model", cascade="all, delete-orphan")


class VehicleVariant(Base, TimestampMixin):
    __tablename__ = "vehicle_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    engine_cc: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fuel_type: Mapped[str] = mapped_column(String(30), default="PETROL", nullable=False)  # PETROL, ELECTRIC, HYBRID
    transmission: Mapped[str] = mapped_column(String(30), default="AUTOMATIC", nullable=False)  # MANUAL, AUTOMATIC, CVT
    
    # Financial amounts - Decimal precision
    ex_showroom_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    insurance_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    rto_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    accessories_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    on_road_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    
    color_options: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Comma-separated or string
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE", nullable=False)  # ACTIVE, DISCONTINUED
    specs_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    model: Mapped["VehicleModel"] = relationship("VehicleModel", back_populates="variants", lazy="joined")
    inventory_units: Mapped[List["InventoryUnit"]] = relationship("InventoryUnit", back_populates="variant")
    quotations: Mapped[List["Quotation"]] = relationship("Quotation", back_populates="variant")
    leads: Mapped[List["Lead"]] = relationship("Lead", back_populates="interested_variant")
    test_rides: Mapped[List["TestRide"]] = relationship("TestRide", back_populates="variant")


class Accessory(Base):
    __tablename__ = "accessories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    part_number: Mapped[Optional[str]] = mapped_column(String(50), unique=True, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
