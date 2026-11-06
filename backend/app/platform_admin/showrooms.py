"""Showroom onboarding and suspension by the Platform Admin. Central metadata only.

Showrooms exist only because the Platform Admin created them, and an Owner exists only by accepting an
emailed invitation. Once onboarding is complete, this code refuses to invite or replace an Owner, so
an administrator can't quietly place an account they control into an active showroom.
"""
import asyncio
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.models import (
    AccountStatus,
    ConnectionStatus,
    MembershipStatus,
    OnboardingStage,
    PlatformUser,
    Subscription,
    SubscriptionEvent,
    SubscriptionStatus,
    Tenant,
    TenantConnection,
    TenantMembership,
    TenantRole,
    TenantStatus,
)
from app.core.exceptions import AppException
from app.platform_admin import delivery
from app.platform_admin.config import get_admin_settings
from app.platform_admin.database import get_admin_db
from app.platform_admin.dependencies import AdminContext, require_platform_admin
from app.platform_admin.models import PlatformAdmin
from app.platform_admin.security import PUBLISHED_EMAILS
from app.platform_admin.service import RequestMeta, add_audit
from app.schemas.common import SuccessResponse

router = APIRouter(prefix="/api/v1/admin/showrooms", tags=["Platform Admin: Showrooms"])

# Until plans exist. No commercial values are invented here.
UNASSIGNED_PLAN = "unassigned"
DEFAULT_CURRENCY = "INR"


class CreateShowroomRequest(BaseModel):
    showroom_name: str = Field(..., min_length=2, max_length=150)
    legal_business_name: str = Field(..., min_length=2, max_length=200)
    business_registration_number: Optional[str] = Field(None, max_length=60)
    business_address: Optional[str] = Field(None, max_length=300)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    contact_phone: Optional[str] = Field(None, max_length=20)
    owner_email: EmailStr
    owner_full_name: str = Field(..., min_length=2, max_length=150)


class OwnerInvitationRequest(BaseModel):
    """Leave the email out to resend to the current primary Owner, or give one to replace them."""

    owner_email: Optional[EmailStr] = None
    owner_full_name: Optional[str] = Field(None, min_length=2, max_length=150)


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000)


class ShowroomView(BaseModel):
    """An allow-list. Nothing about credentials, tokens or business records can appear here."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    public_code: str
    showroom_name: str
    slug: str
    status: str
    onboarding_stage: str
    legal_business_name: Optional[str] = None
    business_registration_number: Optional[str] = None
    business_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    contact_phone: Optional[str] = None
    primary_owner_user_id: Optional[uuid.UUID] = None
    suspended_at: Optional[datetime] = None
    created_at: datetime


class ShowroomOnboardingResponse(ShowroomView):
    invitation_delivery: str  # SENT or FAILED. The token itself is never returned.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80].strip("-")
    if len(slug) < 2:
        raise AppException("INVALID_SHOWROOM_NAME", "Choose a showroom name containing letters or digits.", status.HTTP_400_BAD_REQUEST)
    return slug


def _new_invitation() -> Tuple[str, str, datetime]:
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(hours=get_admin_settings().ADMIN_INVITATION_EXPIRE_HOURS)
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest(), expires


async def _owner_account(db: AsyncSession, email: str, full_name: str) -> uuid.UUID:
    """An existing account stays as it is, and must sign in to accept. Otherwise one is created with no password."""
    existing = (await db.execute(select(PlatformUser.id).where(PlatformUser.email == email))).scalar_one_or_none()
    if existing is not None:
        return existing
    user_id, now = uuid.uuid4(), _now()
    await db.execute(
        insert(PlatformUser).values(id=user_id, email=email, full_name=full_name.strip(), account_status=AccountStatus.INVITED.value, created_at=now, updated_at=now)
    )
    return user_id


async def _refuse_reserved_owner_email(db: AsyncSession, email: str) -> None:
    admin_email = (await db.execute(select(PlatformAdmin.email))).scalar_one_or_none()
    if email == admin_email or email in PUBLISHED_EMAILS:
        raise AppException("OWNER_EMAIL_NOT_ALLOWED", "This email can't be used for a showroom Owner.", status.HTTP_400_BAD_REQUEST)


async def create_showroom_records(
    db: AsyncSession, req: CreateShowroomRequest, admin_id: Optional[uuid.UUID], meta: RequestMeta
) -> Tuple[uuid.UUID, str, uuid.UUID]:
    """One transaction: showroom, Owner invitation, pending subscription and connection. Returns the raw token."""
    email = req.owner_email.lower()
    await _refuse_reserved_owner_email(db, email)
    tenant_id, now = uuid.uuid4(), _now()
    token, token_hash, expires = _new_invitation()
    try:
        user_id = await _owner_account(db, email, req.owner_full_name)
        await db.execute(
            insert(Tenant).values(
                id=tenant_id,
                public_code="VQ-" + secrets.token_hex(4).upper(),
                showroom_name=req.showroom_name.strip(),
                slug=_slugify(req.showroom_name),
                status=TenantStatus.PENDING_SETUP.value,
                subscription_status=SubscriptionStatus.PENDING.value,
                onboarding_stage=OnboardingStage.CREATED.value,
                legal_business_name=req.legal_business_name.strip(),
                business_registration_number=req.business_registration_number,
                business_address=req.business_address,
                city=req.city,
                state=req.state,
                contact_phone=req.contact_phone,
                primary_owner_user_id=user_id,
                created_by_admin_id=admin_id,
                created_at=now,
                updated_at=now,
            )
        )
        await db.execute(
            insert(TenantMembership).values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                role=TenantRole.OWNER.value,
                status=MembershipStatus.INVITED.value,
                invitation_token_hash=token_hash,
                invitation_expires_at=expires,
                created_at=now,
                updated_at=now,
            )
        )
        subscription_id = uuid.uuid4()
        await db.execute(
            insert(Subscription).values(
                id=subscription_id,
                tenant_id=tenant_id,
                plan_id=UNASSIGNED_PLAN,
                billing_cycle="MONTHLY",
                amount=Decimal("0.00"),
                currency=DEFAULT_CURRENCY,
                status=SubscriptionStatus.PENDING.value,
                created_at=now,
                updated_at=now,
            )
        )
        await db.execute(insert(SubscriptionEvent).values(id=uuid.uuid4(), subscription_id=subscription_id, event_type="SUBSCRIPTION_CREATED", event_metadata={"plan_id": UNASSIGNED_PLAN}, created_at=now))
        await db.execute(
            insert(TenantConnection).values(id=uuid.uuid4(), tenant_id=tenant_id, database_type="POSTGRESQL", connection_status=ConnectionStatus.PENDING.value, created_at=now, updated_at=now)
        )
        add_audit(db, "SHOWROOM_CREATED", meta, actor_id=admin_id, target_type="SHOWROOM", target_id=str(tenant_id), tenant_id=tenant_id, after={"showroom_name": req.showroom_name.strip(), "owner_email": email})
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppException("SHOWROOM_ALREADY_EXISTS", "A showroom with this name already exists.", status.HTTP_409_CONFLICT) from None
    return tenant_id, token, user_id


async def _deliver(db: AsyncSession, tenant_id: uuid.UUID, email: str, showroom_name: str, token: str, actor_id: uuid.UUID, meta: RequestMeta) -> str:
    """The record is committed first, so a mail failure can be retried and leaves nothing half-made."""
    try:
        await asyncio.to_thread(delivery.send_owner_invitation, email, showroom_name, token)
    except delivery.InvitationDeliveryError as err:
        add_audit(db, "OWNER_INVITATION_DELIVERY_FAILED", meta, actor_id=actor_id, target_type="SHOWROOM", target_id=str(tenant_id), tenant_id=tenant_id, after={"owner_email": email, "error": str(err)})
        await db.commit()
        return "FAILED"
    await db.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id, Tenant.onboarding_stage == OnboardingStage.CREATED.value)
        .values(onboarding_stage=OnboardingStage.INVITED.value, updated_at=_now())
    )
    add_audit(db, "OWNER_INVITATION_SENT", meta, actor_id=actor_id, target_type="SHOWROOM", target_id=str(tenant_id), tenant_id=tenant_id, after={"owner_email": email})
    await db.commit()
    return "SENT"


def _require_delivery() -> None:
    if not delivery.delivery_configured():
        raise AppException("INVITATION_DELIVERY_NOT_CONFIGURED", "Invitation email isn't configured, so nothing was created.", status.HTTP_503_SERVICE_UNAVAILABLE)


async def _locked_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update(of=Tenant).execution_options(populate_existing=True))).scalar_one_or_none()
    if tenant is None:
        raise AppException("SHOWROOM_NOT_FOUND", "This showroom doesn't exist.", status.HTTP_404_NOT_FOUND)
    return tenant


@router.post("", response_model=SuccessResponse[ShowroomOnboardingResponse], status_code=status.HTTP_201_CREATED)
async def create_showroom(req: CreateShowroomRequest, ctx: AdminContext = Depends(require_platform_admin), db: AsyncSession = Depends(get_admin_db)):
    _require_delivery()
    tenant_id, token, _ = await create_showroom_records(db, req, ctx.admin.id, ctx.meta)
    outcome = await _deliver(db, tenant_id, req.owner_email.lower(), req.showroom_name.strip(), token, ctx.admin.id, ctx.meta)
    tenant = await db.get(Tenant, tenant_id, populate_existing=True)
    return SuccessResponse(data=ShowroomOnboardingResponse(**ShowroomView.model_validate(tenant).model_dump(), invitation_delivery=outcome))


@router.get("/{tenant_id}", response_model=SuccessResponse[ShowroomView])
async def get_showroom(tenant_id: uuid.UUID, _: AdminContext = Depends(require_platform_admin), db: AsyncSession = Depends(get_admin_db)):
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise AppException("SHOWROOM_NOT_FOUND", "This showroom doesn't exist.", status.HTTP_404_NOT_FOUND)
    return SuccessResponse(data=ShowroomView.model_validate(tenant))


@router.post("/{tenant_id}/owner-invitation", response_model=SuccessResponse[ShowroomOnboardingResponse])
async def invite_primary_owner(tenant_id: uuid.UUID, req: OwnerInvitationRequest, ctx: AdminContext = Depends(require_platform_admin), db: AsyncSession = Depends(get_admin_db)):
    """Resend or replace, only before onboarding completes. A completed showroom needs Owner recovery."""
    _require_delivery()
    tenant = await _locked_tenant(db, tenant_id)
    if tenant.status == TenantStatus.CANCELLED.value:
        raise AppException("SHOWROOM_CANCELLED", "This showroom is cancelled.", status.HTTP_409_CONFLICT)
    if tenant.onboarding_stage == OnboardingStage.COMPLETE.value:
        raise AppException(
            "OWNER_RECOVERY_REQUIRED",
            "Onboarding is complete, so an Owner can only change through the Owner recovery process.",
            status.HTTP_409_CONFLICT,
        )

    current_email = None
    if tenant.primary_owner_user_id:
        current_email = (await db.execute(select(PlatformUser.email).where(PlatformUser.id == tenant.primary_owner_user_id))).scalar_one_or_none()
    email = req.owner_email.lower() if req.owner_email else current_email
    if email is None:
        raise AppException("OWNER_EMAIL_REQUIRED", "Enter the Owner's email.", status.HTTP_400_BAD_REQUEST)
    await _refuse_reserved_owner_email(db, email)

    now = _now()
    token, token_hash, expires = _new_invitation()
    replacing = email != current_email
    if replacing:
        if not req.owner_full_name:
            raise AppException("OWNER_NAME_REQUIRED", "Enter the new Owner's name.", status.HTTP_400_BAD_REQUEST)
        # The previous person loses any access or pending invitation.
        if tenant.primary_owner_user_id:
            await db.execute(
                update(TenantMembership)
                .where(TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == tenant.primary_owner_user_id)
                .values(status=MembershipStatus.DEACTIVATED.value, invitation_token_hash=None, invitation_expires_at=None, updated_at=now)
            )
    user_id = await _owner_account(db, email, req.owner_full_name or email)
    membership = (
        await db.execute(select(TenantMembership.id, TenantMembership.status).where(TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id))
    ).first()
    if membership is not None and membership.status == MembershipStatus.ACTIVE.value:
        raise AppException("OWNER_ALREADY_ACCEPTED", "This Owner has already accepted.", status.HTTP_409_CONFLICT)
    if membership is None:
        await db.execute(
            insert(TenantMembership).values(
                id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, role=TenantRole.OWNER.value, status=MembershipStatus.INVITED.value,
                invitation_token_hash=token_hash, invitation_expires_at=expires, created_at=now, updated_at=now,
            )
        )
    else:
        # Replaces the previous token, which stops working.
        await db.execute(
            update(TenantMembership).where(TenantMembership.id == membership.id).values(status=MembershipStatus.INVITED.value, invitation_token_hash=token_hash, invitation_expires_at=expires, updated_at=now)
        )
    await db.execute(
        update(Tenant).where(Tenant.id == tenant_id).values(primary_owner_user_id=user_id, onboarding_stage=OnboardingStage.CREATED.value, updated_at=now)
    )
    add_audit(
        db, "PRIMARY_OWNER_REPLACED" if replacing else "OWNER_INVITATION_REISSUED", ctx.meta, actor_id=ctx.admin.id, target_type="SHOWROOM", target_id=str(tenant_id), tenant_id=tenant_id,
        before={"owner_email": current_email}, after={"owner_email": email},
    )
    showroom_name = tenant.showroom_name
    await db.commit()
    outcome = await _deliver(db, tenant_id, email, showroom_name, token, ctx.admin.id, ctx.meta)
    tenant = await db.get(Tenant, tenant_id, populate_existing=True)
    return SuccessResponse(data=ShowroomOnboardingResponse(**ShowroomView.model_validate(tenant).model_dump(), invitation_delivery=outcome))


@router.post("/{tenant_id}/suspend", response_model=SuccessResponse[ShowroomView])
async def suspend_showroom(tenant_id: uuid.UUID, req: ReasonRequest, ctx: AdminContext = Depends(require_platform_admin), db: AsyncSession = Depends(get_admin_db)):
    """Blocks access on the next request for everyone, because each request re-reads this status. Deletes nothing."""
    tenant = await _locked_tenant(db, tenant_id)
    if tenant.status in (TenantStatus.SUSPENDED.value, TenantStatus.CANCELLED.value):
        raise AppException("SHOWROOM_STATUS_CONFLICT", f"This showroom is already {tenant.status.lower()}.", status.HTTP_409_CONFLICT)
    before = tenant.status
    now = _now()
    tenant.status = TenantStatus.SUSPENDED.value
    tenant.suspended_at = now
    tenant.suspended_by_admin_id = ctx.admin.id
    tenant.suspension_reason = req.reason
    add_audit(db, "SHOWROOM_SUSPENDED", ctx.meta, actor_id=ctx.admin.id, target_type="SHOWROOM", target_id=str(tenant_id), tenant_id=tenant_id, before={"status": before}, after={"status": tenant.status}, reason=req.reason)
    await db.commit()
    return SuccessResponse(data=ShowroomView.model_validate(tenant))


@router.post("/{tenant_id}/reactivate", response_model=SuccessResponse[ShowroomView])
async def reactivate_showroom(tenant_id: uuid.UUID, req: ReasonRequest, ctx: AdminContext = Depends(require_platform_admin), db: AsyncSession = Depends(get_admin_db)):
    """Back to ACTIVE only if onboarding finished and the database connection is still good."""
    tenant = await _locked_tenant(db, tenant_id)
    if tenant.status != TenantStatus.SUSPENDED.value:
        raise AppException("SHOWROOM_STATUS_CONFLICT", "Only a suspended showroom can be reactivated.", status.HTTP_409_CONFLICT)
    connection_status = (await db.execute(select(TenantConnection.connection_status).where(TenantConnection.tenant_id == tenant_id))).scalar_one_or_none()
    ready = tenant.onboarding_stage == OnboardingStage.COMPLETE.value and connection_status == ConnectionStatus.ACTIVE.value
    tenant.status = TenantStatus.ACTIVE.value if ready else TenantStatus.PENDING_SETUP.value
    tenant.suspended_at = None
    tenant.suspended_by_admin_id = None
    tenant.suspension_reason = None
    add_audit(db, "SHOWROOM_REACTIVATED", ctx.meta, actor_id=ctx.admin.id, target_type="SHOWROOM", target_id=str(tenant_id), tenant_id=tenant_id, before={"status": TenantStatus.SUSPENDED.value}, after={"status": tenant.status}, reason=req.reason)
    await db.commit()
    return SuccessResponse(data=ShowroomView.model_validate(tenant))
