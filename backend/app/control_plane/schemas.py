import re
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, computed_field, field_validator

from app.database.tenant_connection import CONNECTION_ERROR_MESSAGES

TenantRoleName = Literal["OWNER", "HEAD_OF_SALES", "SALESPERSON"]
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class _ShowroomFields(BaseModel):
    showroom_name: str = Field(..., min_length=2, max_length=150)
    slug: Optional[str] = Field(None, min_length=2, max_length=80)

    @field_validator("showroom_name", mode="before")
    @classmethod
    def _strip_name(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v):
        if v is not None and not SLUG_PATTERN.match(v):
            raise ValueError("slug may contain lowercase letters, digits and single hyphens only")
        return v


class RegisterShowroomRequest(_ShowroomFields):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)
    full_name: str = Field(..., min_length=2, max_length=150)


class CreateShowroomRequest(_ShowroomFields):
    pass


class PlatformLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=72)


class RefreshRequest(BaseModel):
    refresh_token: str


class SelectTenantRequest(BaseModel):
    tenant_id: uuid.UUID


class InviteRequest(BaseModel):
    email: EmailStr
    role: TenantRoleName


class AcceptInvitationRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)
    # Required only when the invited person has no account yet.
    full_name: Optional[str] = Field(None, min_length=2, max_length=150)
    password: Optional[str] = Field(None, min_length=8, max_length=72)


class UpdateRoleRequest(BaseModel):
    role: TenantRoleName


class PlatformUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: Optional[str] = None
    account_status: str

    class Config:
        from_attributes = True


class MembershipSummary(BaseModel):
    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    showroom_name: str
    slug: str
    tenant_status: str
    role: TenantRoleName
    status: str


class TenantResponse(BaseModel):
    id: uuid.UUID
    showroom_name: str
    slug: str
    status: str
    subscription_status: str
    created_at: datetime

    class Config:
        from_attributes = True


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: PlatformUserResponse
    active_tenant: Optional[MembershipSummary] = None


class MemberResponse(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: Optional[str] = None
    role: TenantRoleName
    status: str
    invited_by: Optional[uuid.UUID] = None
    invitation_expires_at: Optional[datetime] = None
    created_at: datetime


class InvitationResponse(MemberResponse):
    # Shown once to the Owner to pass on. Only its hash is stored.
    invitation_token: str


class ConnectionDetails(BaseModel):
    """Owner-only view. There is deliberately no secret_reference or credential field."""

    database_provider: Optional[str] = None
    database_type: str
    connection_status: str
    schema_version: Optional[str] = None
    last_health_check: Optional[datetime] = None
    last_error_code: Optional[str] = None

    @computed_field
    @property
    def last_error_message(self) -> Optional[str]:
        return CONNECTION_ERROR_MESSAGES.get(self.last_error_code) if self.last_error_code else None


class SetupStatusResponse(BaseModel):
    tenant_id: uuid.UUID
    showroom_name: str
    tenant_status: str
    subscription_status: str
    connection_status: str
    operational: bool
    next_step: Optional[str] = None
    # Present for Owners only.
    connection: Optional[ConnectionDetails] = None


class MeResponse(BaseModel):
    user: PlatformUserResponse
    memberships: List[MembershipSummary]
    active_tenant_id: Optional[uuid.UUID] = None
    active_role: Optional[TenantRoleName] = None
