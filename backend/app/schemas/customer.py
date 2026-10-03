import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from pydantic import BaseModel, EmailStr, Field, field_validator

_NON_DIGITS = re.compile(r"\D")


def normalize_phone(value: str) -> str:
    """Canonical phone form used for storage and duplicate detection.

    Indian mobile numbers are stored as their 10 local digits, so "+91 98765-43210",
    "919876543210" and "9876543210" are the same customer. Other numbers keep their
    digits with a leading "+".
    """
    raw = value.strip()
    digits = _NON_DIGITS.sub("", raw)
    if len(digits) == 10:
        return digits
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return f"+{digits}" if raw.startswith("+") else digits


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _strip_or_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class _CustomerInputNormalization(BaseModel):
    """Trims text *before* length/pattern checks so whitespace-only input is rejected."""

    @field_validator("full_name", "preferred_language", mode="before", check_fields=False)
    @classmethod
    def _strip_required(cls, v: Any) -> Any:
        return _strip(v)

    @field_validator("email", "address", "city", "state", "pincode", "occupation", mode="before", check_fields=False)
    @classmethod
    def _strip_optional(cls, v: Any) -> Any:
        return _strip_or_none(v)

    @field_validator("phone", mode="before", check_fields=False)
    @classmethod
    def _normalize_phone(cls, v: Any) -> Any:
        return normalize_phone(v) if isinstance(v, str) else v


class CustomerBase(_CustomerInputNormalization):
    full_name: str = Field(..., min_length=2, max_length=150, example="Rahul Sharma")
    phone: str = Field(..., min_length=10, max_length=16, pattern=r"^\+?[0-9]{10,15}$", example="9876543210")
    email: Optional[EmailStr] = Field(None, example="rahul.sharma@example.com")
    address: Optional[str] = Field(None, example="Flat 402, Green Acres")
    city: Optional[str] = Field(None, example="Mumbai")
    state: Optional[str] = Field(None, example="Maharashtra")
    pincode: Optional[str] = Field(None, pattern=r"^[0-9]{4,10}$", example="400001")
    occupation: Optional[str] = Field(None, example="Software Engineer")
    preferred_language: str = Field(default="English", min_length=1, example="English")
    budget: Optional[Decimal] = Field(None, ge=Decimal("0"), example=Decimal("120000.00"))


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(_CustomerInputNormalization):
    full_name: Optional[str] = Field(None, min_length=2, max_length=150)
    phone: Optional[str] = Field(None, min_length=10, max_length=16, pattern=r"^\+?[0-9]{10,15}$")
    email: Optional[EmailStr] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = Field(None, pattern=r"^[0-9]{4,10}$")
    occupation: Optional[str] = None
    preferred_language: Optional[str] = Field(None, min_length=1)
    budget: Optional[Decimal] = Field(None, ge=Decimal("0"))


class CustomerResponse(BaseModel):
    """Output model. Deliberately free of input constraints so a legacy or malformed row
    can never make list/detail endpoints fail to serialize."""

    id: int
    full_name: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    occupation: Optional[str] = None
    preferred_language: str
    budget: Optional[Decimal] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
