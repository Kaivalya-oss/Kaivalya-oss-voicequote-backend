from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class CustomerBase(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=150, example="Rahul Sharma")
    phone: str = Field(..., min_length=10, max_length=15, pattern=r"^\+?[0-9]{10,15}$", example="9876543210")
    email: Optional[EmailStr] = Field(None, example="rahul.sharma@example.com")
    address: Optional[str] = Field(None, example="Flat 402, Green Acres")
    city: Optional[str] = Field(None, example="Mumbai")
    state: Optional[str] = Field(None, example="Maharashtra")
    pincode: Optional[str] = Field(None, pattern=r"^[0-9]{4,10}$", example="400001")
    occupation: Optional[str] = Field(None, example="Software Engineer")
    preferred_language: str = Field(default="English", example="English")
    budget: Optional[Decimal] = Field(None, ge=Decimal("0"), example=Decimal("120000.00"))


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=150)
    phone: Optional[str] = Field(None, min_length=10, max_length=15, pattern=r"^\+?[0-9]{10,15}$")
    email: Optional[EmailStr] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    occupation: Optional[str] = None
    preferred_language: Optional[str] = None
    budget: Optional[Decimal] = Field(None, ge=Decimal("0"))


class CustomerResponse(CustomerBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
