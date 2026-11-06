import hashlib
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, NoReturn, Optional, Tuple

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.models import (
    AccountStatus,
    ConnectionStatus,
    MembershipStatus,
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
from app.control_plane.schemas import (
    AcceptInvitationRequest,
    ConnectionDetails,
    CreateShowroomRequest,
    MembershipSummary,
    MemberResponse,
    PlatformUserResponse,
    RegisterShowroomRequest,
    SetupStatusResponse,
    TokenPairResponse,
)
from app.core.config import settings
from app.core.exceptions import (
    AppException,
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
)
from app.core.logging import logger
from app.core.dev_accounts import is_refused_dev_credential
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.core.tenant_context import PLATFORM_SCOPE, TenantContext

# Verified when an email is unknown, so login takes the same time either way.
_DUMMY_HASH = hash_password("voicequote-timing-equalizer")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80].strip("-")
    if len(slug) < 2:
        raise BadRequestException("INVALID_SHOWROOM_NAME", "Choose a showroom name containing letters or digits.")
    return slug


def _summary(m: TenantMembership) -> MembershipSummary:
    return MembershipSummary(
        membership_id=m.id,
        tenant_id=m.tenant_id,
        showroom_name=m.tenant.showroom_name,
        slug=m.tenant.slug,
        tenant_status=m.tenant.status,
        role=m.role,
        status=m.status,
    )


def member_response(m: TenantMembership) -> MemberResponse:
    return MemberResponse(
        membership_id=m.id,
        user_id=m.user_id,
        email=m.user.email,
        full_name=m.user.full_name,
        role=m.role,
        status=m.status,
        invited_by=m.invited_by,
        invitation_expires_at=m.invitation_expires_at if m.status == MembershipStatus.INVITED.value else None,
        created_at=m.created_at,
    )


def issue_tokens(user: PlatformUser, membership: Optional[TenantMembership]) -> TokenPairResponse:
    claims = {"scope": PLATFORM_SCOPE}
    if membership is not None:
        claims["tid"] = str(membership.tenant_id)
    return TokenPairResponse(
        access_token=create_access_token(user.id, extra_claims=claims),
        refresh_token=create_refresh_token(user.id, extra_claims=claims),
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=PlatformUserResponse.model_validate(user),
        active_tenant=_summary(membership) if membership is not None else None,
    )


class ControlPlaneService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------- memberships ----------

    async def active_memberships(self, user_id: uuid.UUID) -> List[TenantMembership]:
        rows = await self.session.execute(
            select(TenantMembership)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                TenantMembership.user_id == user_id,
                TenantMembership.status == MembershipStatus.ACTIVE.value,
                Tenant.status != TenantStatus.CANCELLED.value,
            )
            .order_by(TenantMembership.created_at)
        )
        return list(rows.scalars().all())

    async def _active_membership(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[TenantMembership]:
        rows = await self.active_memberships(user_id)
        return next((m for m in rows if m.tenant_id == tenant_id), None)

    # ---------- registration ----------

    async def _new_tenant_records(self, name: str, slug: Optional[str], owner: PlatformUser) -> TenantMembership:
        """Everything a showroom starts with, added to the caller's single transaction."""
        tenant = Tenant(id=uuid.uuid4(), showroom_name=name, slug=slug or _slugify(name))
        self.session.add(tenant)
        await self.session.flush()  # the records below reference tenant_id by value
        membership = TenantMembership(
            tenant=tenant, user=owner, role=TenantRole.OWNER.value, status=MembershipStatus.ACTIVE.value
        )
        subscription = Subscription(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            plan_id=settings.SAAS_DEFAULT_PLAN_ID,
            amount=settings.SAAS_DEFAULT_PLAN_AMOUNT,
            currency=settings.SAAS_DEFAULT_CURRENCY,
            status=SubscriptionStatus.PENDING.value,
            start_date=date.today(),
        )
        event = SubscriptionEvent(
            subscription_id=subscription.id,
            event_type="SUBSCRIPTION_CREATED",
            event_metadata={"plan_id": settings.SAAS_DEFAULT_PLAN_ID},
        )
        connection = TenantConnection(tenant_id=tenant.id, connection_status=ConnectionStatus.PENDING.value)
        self.session.add_all([membership, subscription])
        await self.session.flush()
        self.session.add_all([event, connection])
        return membership

    async def register_showroom(self, req: RegisterShowroomRequest) -> TokenPairResponse:
        email = req.email.lower()
        if is_refused_dev_credential(email, req.password):
            raise BadRequestException("CREDENTIAL_NOT_ALLOWED", "This email or password cannot be used.")
        existing = (
            await self.session.execute(select(PlatformUser).where(PlatformUser.email == email))
        ).scalar_one_or_none()
        if existing:
            raise ConflictException(
                "ACCOUNT_EXISTS", "An account with this email already exists. Sign in to add another showroom."
            )
        user = PlatformUser(
            id=uuid.uuid4(),
            email=email,
            full_name=req.full_name.strip(),
            password_hash=hash_password(req.password),
            account_status=AccountStatus.ACTIVE.value,
        )
        self.session.add(user)
        try:
            await self.session.flush()
            membership = await self._new_tenant_records(req.showroom_name, req.slug, user)
            await self.session.commit()
        except IntegrityError as e:
            await self._duplicate_tenant(e)
        return issue_tokens(user, membership)

    async def create_showroom(self, user: PlatformUser, req: CreateShowroomRequest) -> TokenPairResponse:
        try:
            membership = await self._new_tenant_records(req.showroom_name, req.slug, user)
            await self.session.commit()
        except IntegrityError as e:
            await self._duplicate_tenant(e)
        return issue_tokens(user, membership)

    async def _duplicate_tenant(self, error: IntegrityError) -> NoReturn:
        # A repeated or concurrent request: the unique slug/email keeps only one showroom.
        await self.session.rollback()
        logger.info(f"Showroom registration conflict on {getattr(error.orig, 'constraint_name', 'unknown')}")
        raise ConflictException("SHOWROOM_ALREADY_EXISTS", "This showroom or account is already registered.")

    # ---------- sessions ----------

    async def login(self, email: str, password: str) -> TokenPairResponse:
        user = (
            await self.session.execute(select(PlatformUser).where(PlatformUser.email == email.lower()))
        ).scalar_one_or_none()
        valid = verify_password(password, user.password_hash if user and user.password_hash else _DUMMY_HASH)
        if not user or not user.password_hash or not valid or user.account_status != AccountStatus.ACTIVE.value:
            raise UnauthorizedException("Invalid email or password.")
        if is_refused_dev_credential(user.email, password):
            raise UnauthorizedException("Invalid email or password.")
        memberships = await self.active_memberships(user.id)
        # One showroom: select it. Several: the user must choose.
        return issue_tokens(user, memberships[0] if len(memberships) == 1 else None)

    async def refresh(self, refresh_token: str) -> TokenPairResponse:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh" or payload.get("scope") != PLATFORM_SCOPE:
            raise UnauthorizedException("Invalid token.")
        try:
            user_id = uuid.UUID(str(payload.get("sub")))
        except ValueError:
            raise UnauthorizedException("Invalid token.")
        user = await self.session.get(PlatformUser, user_id)
        if not user or user.account_status != AccountStatus.ACTIVE.value:
            raise UnauthorizedException("Account is not active.")
        membership = None
        if payload.get("tid"):
            # A revoked membership is dropped here rather than carried into the new token.
            membership = await self._active_membership(user.id, uuid.UUID(str(payload["tid"])))
        return issue_tokens(user, membership)

    async def select_tenant(self, user: PlatformUser, tenant_id: uuid.UUID) -> TokenPairResponse:
        membership = await self._active_membership(user.id, tenant_id)
        if membership is None:
            # The same answer whether the showroom exists or not.
            raise ForbiddenException("You do not have access to this showroom.")
        return issue_tokens(user, membership)

    # ---------- team ----------

    async def list_members(self, tenant_id: uuid.UUID) -> List[TenantMembership]:
        rows = await self.session.execute(
            select(TenantMembership)
            .where(TenantMembership.tenant_id == tenant_id)
            .order_by(TenantMembership.created_at)
        )
        return list(rows.scalars().all())

    async def invite(self, ctx: TenantContext, email: str, role: str) -> Tuple[TenantMembership, str]:
        email = email.lower()
        user = (
            await self.session.execute(select(PlatformUser).where(PlatformUser.email == email))
        ).scalar_one_or_none()
        if user is None:
            user = PlatformUser(id=uuid.uuid4(), email=email, account_status=AccountStatus.INVITED.value)
            self.session.add(user)
            await self.session.flush()

        membership = (
            await self.session.execute(
                select(TenantMembership)
                .where(TenantMembership.tenant_id == ctx.tenant.id, TenantMembership.user_id == user.id)
                .with_for_update(of=TenantMembership)
            )
        ).scalar_one_or_none()
        if membership is not None and membership.status == MembershipStatus.ACTIVE.value:
            raise ConflictException("ALREADY_A_MEMBER", "This person is already a member of this showroom.")

        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=settings.INVITATION_EXPIRE_HOURS)
        if membership is None:
            membership = TenantMembership(tenant_id=ctx.tenant.id, user_id=user.id)
            self.session.add(membership)
        # Re-inviting replaces the previous token, which stops working.
        membership.role = role
        membership.status = MembershipStatus.INVITED.value
        membership.invited_by = ctx.user.id
        membership.invitation_token_hash = _hash_token(token)
        membership.invitation_expires_at = expires
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise ConflictException("INVITATION_CONFLICT", "This person was just invited. Refresh and try again.")
        return await self._member(ctx.tenant.id, membership.id), token

    async def accept_invitation(
        self, req: AcceptInvitationRequest, authenticated_user_id: Optional[uuid.UUID]
    ) -> TokenPairResponse:
        membership = (
            await self.session.execute(
                select(TenantMembership)
                .where(TenantMembership.invitation_token_hash == _hash_token(req.token))
                .with_for_update(of=TenantMembership)
            )
        ).scalar_one_or_none()
        invalid = AppException(
            "INVITATION_INVALID", "This invitation is invalid or has expired.", status.HTTP_400_BAD_REQUEST
        )
        if membership is None or membership.status != MembershipStatus.INVITED.value:
            raise invalid
        if membership.invitation_expires_at is None or membership.invitation_expires_at < datetime.now(timezone.utc):
            raise invalid
        if membership.tenant.status == TenantStatus.CANCELLED.value:
            raise invalid

        user = membership.user
        if user.account_status == AccountStatus.INVITED.value:
            if not req.password or not req.full_name:
                raise BadRequestException("ACCOUNT_DETAILS_REQUIRED", "Enter your name and choose a password.")
            if is_refused_dev_credential(user.email, req.password):
                raise BadRequestException("CREDENTIAL_NOT_ALLOWED", "This email or password cannot be used.")
            user.full_name = req.full_name.strip()
            user.password_hash = hash_password(req.password)
            user.account_status = AccountStatus.ACTIVE.value
        else:
            # An existing account must prove itself: a leaked link can never take over an account.
            if authenticated_user_id != user.id or user.account_status != AccountStatus.ACTIVE.value:
                raise ForbiddenException("Sign in as the invited account to accept this invitation.")

        membership.status = MembershipStatus.ACTIVE.value
        membership.invitation_token_hash = None  # single use
        membership.invitation_expires_at = None
        await self.session.commit()
        return issue_tokens(user, membership)

    async def _member(self, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> TenantMembership:
        membership = (
            await self.session.execute(
                select(TenantMembership)
                .where(TenantMembership.id == membership_id, TenantMembership.tenant_id == tenant_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if membership is None:
            # Another showroom's member looks exactly like one that does not exist.
            raise NotFoundException("Member", membership_id)
        return membership

    async def _ensure_other_active_owner(self, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> None:
        # Row locks serialize concurrent demotions, so two owners cannot remove each other at once.
        owners = (
            await self.session.execute(
                select(TenantMembership.id)
                .where(
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.role == TenantRole.OWNER.value,
                    TenantMembership.status == MembershipStatus.ACTIVE.value,
                )
                .with_for_update(of=TenantMembership)
            )
        ).scalars().all()
        if not [o for o in owners if o != membership_id]:
            raise ConflictException("LAST_OWNER", "A showroom must keep at least one active Owner.")

    async def deactivate_member(self, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> TenantMembership:
        membership = await self._member(tenant_id, membership_id)
        if membership.role == TenantRole.OWNER.value and membership.status == MembershipStatus.ACTIVE.value:
            await self._ensure_other_active_owner(tenant_id, membership_id)
        membership.status = MembershipStatus.DEACTIVATED.value
        membership.invitation_token_hash = None
        membership.invitation_expires_at = None
        await self.session.commit()
        return await self._member(tenant_id, membership_id)

    async def update_role(self, tenant_id: uuid.UUID, membership_id: uuid.UUID, role: str) -> TenantMembership:
        membership = await self._member(tenant_id, membership_id)
        if membership.status == MembershipStatus.DEACTIVATED.value:
            raise ConflictException("MEMBER_DEACTIVATED", "Invite this person again before changing their role.")
        if (
            membership.role == TenantRole.OWNER.value
            and role != TenantRole.OWNER.value
            and membership.status == MembershipStatus.ACTIVE.value
        ):
            await self._ensure_other_active_owner(tenant_id, membership_id)
        membership.role = role
        await self.session.commit()
        return await self._member(tenant_id, membership_id)

    # ---------- setup status ----------

    @staticmethod
    def setup_status(ctx: TenantContext) -> SetupStatusResponse:
        conn = ctx.connection
        conn_status = conn.connection_status if conn else ConnectionStatus.PENDING.value
        next_step = None
        if ctx.tenant.status == TenantStatus.SUSPENDED.value:
            next_step = "CONTACT_SUPPORT"
        elif not ctx.operational:
            next_step = "CONNECT_DATABASE" if ctx.role == TenantRole.OWNER.value else "WAIT_FOR_OWNER"
        return SetupStatusResponse(
            tenant_id=ctx.tenant.id,
            showroom_name=ctx.tenant.showroom_name,
            tenant_status=ctx.tenant.status,
            subscription_status=ctx.tenant.subscription_status,
            connection_status=conn_status,
            operational=ctx.operational,
            next_step=next_step,
            connection=ConnectionDetails.model_validate(conn, from_attributes=True)
            if conn and ctx.role == TenantRole.OWNER.value
            else None,
        )
