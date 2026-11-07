import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.models import (
    AccountStatus,
    ConnectionStatus,
    MembershipStatus,
    OnboardingStage,
    PlatformUser,
    SessionRevokedReason,
    ShowroomSession,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantStatus,
)
from app.control_plane.schemas import (
    AcceptInvitationRequest,
    ConnectionDetails,
    MembershipSummary,
    MemberResponse,
    PlatformUserResponse,
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
from app.core.dev_accounts import is_refused_dev_credential
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.core.tenant_context import PLATFORM_SCOPE, TenantContext, account_has_access, active_session
from app.database.provisioning import latest_schema_version
from app.platform_admin.models import AuditEvent

# Verified when an email is unknown, so login takes the same time either way.
_DUMMY_HASH = hash_password("voicequote-timing-equalizer")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def issue_tokens(user: PlatformUser, membership: Optional[TenantMembership], sign_in: ShowroomSession) -> TokenPairResponse:
    """A new token pair for this sign-in. Only the newest refresh token works afterwards."""
    claims = {"scope": PLATFORM_SCOPE, "sid": str(sign_in.id)}
    if membership is not None:
        claims["tid"] = str(membership.tenant_id)
    refresh_id = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    sign_in.refresh_token_hash = _hash_token(refresh_id)
    sign_in.last_refreshed_at = now
    sign_in.expires_at = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    return TokenPairResponse(
        access_token=create_access_token(user.id, extra_claims=claims),
        refresh_token=create_refresh_token(user.id, extra_claims={**claims, "jti": refresh_id}),
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

    # ---------- sessions ----------

    def _new_sign_in(self, user: PlatformUser) -> ShowroomSession:
        sign_in = ShowroomSession(id=uuid.uuid4(), user_id=user.id, refresh_token_hash="")
        self.session.add(sign_in)
        return sign_in

    async def _issue(self, user: PlatformUser, membership: Optional[TenantMembership], sign_in: ShowroomSession) -> TokenPairResponse:
        tokens = issue_tokens(user, membership, sign_in)
        await self.session.commit()
        return tokens

    async def login(self, email: str, password: str) -> TokenPairResponse:
        user = (
            await self.session.execute(select(PlatformUser).where(PlatformUser.email == email.lower()))
        ).scalar_one_or_none()
        valid = verify_password(password, user.password_hash if user and user.password_hash else _DUMMY_HASH)
        if not user or not user.password_hash or not valid or user.account_status != AccountStatus.ACTIVE.value:
            raise UnauthorizedException("Invalid email or password.")
        if is_refused_dev_credential(user.email, password):
            raise UnauthorizedException("Invalid email or password.")
        # Someone deactivated everywhere can't sign in at all, so a password alone gets them nothing.
        if not await account_has_access(self.session, user.id):
            raise UnauthorizedException("Invalid email or password.")
        memberships = await self.active_memberships(user.id)
        # One showroom: select it. Several: the user must choose.
        return await self._issue(user, memberships[0] if len(memberships) == 1 else None, self._new_sign_in(user))

    async def refresh(self, refresh_token: str) -> TokenPairResponse:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh" or payload.get("scope") != PLATFORM_SCOPE:
            raise UnauthorizedException("Invalid token.")
        try:
            user_id = uuid.UUID(str(payload.get("sub")))
            session_id = uuid.UUID(str(payload.get("sid")))
        except ValueError:
            raise UnauthorizedException("Invalid token.")
        sign_in = (
            await self.session.execute(
                select(ShowroomSession).where(ShowroomSession.id == session_id).with_for_update().execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        ended = UnauthorizedException("This session has ended. Sign in again.")
        if sign_in is None or sign_in.user_id != user_id or sign_in.revoked_at is not None or sign_in.expires_at <= datetime.now(timezone.utc):
            raise ended
        if not secrets.compare_digest(sign_in.refresh_token_hash, _hash_token(str(payload.get("jti", "")))):
            # An older refresh token from this sign-in: someone else may hold a copy, so end it for both.
            await self._revoke(sign_in, SessionRevokedReason.REFRESH_REUSED, "SESSION_REFRESH_REUSED")
            raise ended
        user = await self.session.get(PlatformUser, user_id)
        if not user or user.account_status != AccountStatus.ACTIVE.value or not await account_has_access(self.session, user.id):
            raise UnauthorizedException("Account is not active.")
        membership = None
        if payload.get("tid"):
            # A revoked membership is dropped here rather than carried into the new token.
            membership = await self._active_membership(user.id, uuid.UUID(str(payload["tid"])))
        return await self._issue(user, membership, sign_in)

    async def logout(self, payload: dict) -> None:
        """Ends the sign-in the access token belongs to, including its refresh token."""
        sign_in = await active_session(self.session, payload, uuid.UUID(str(payload.get("sub"))))
        await self._revoke(sign_in, SessionRevokedReason.SIGNED_OUT, "SIGNED_OUT")

    async def _revoke(self, sign_in: ShowroomSession, reason: SessionRevokedReason, action: str) -> None:
        sign_in.revoked_at = datetime.now(timezone.utc)
        sign_in.revoked_reason = reason.value
        self.session.add(
            AuditEvent(actor_type="SHOWROOM_ACCOUNT", actor_id=sign_in.user_id, action=action, target_type="SESSION", target_id=str(sign_in.id))
        )
        await self.session.commit()

    async def select_tenant(self, user: PlatformUser, tenant_id: uuid.UUID, payload: dict) -> TokenPairResponse:
        membership = await self._active_membership(user.id, tenant_id)
        if membership is None:
            # The same answer whether the showroom exists or not.
            raise ForbiddenException("You do not have access to this showroom.")
        # Switching showroom stays within the same sign-in, so signing out still ends it.
        return await self._issue(user, membership, await active_session(self.session, payload, user.id))

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
        if membership.tenant.status == TenantStatus.SUSPENDED.value:
            raise AppException("TENANT_SUSPENDED", "This showroom is suspended.", status.HTTP_403_FORBIDDEN)

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
        tenant = membership.tenant
        if membership.role == TenantRole.OWNER.value and tenant.onboarding_stage in (OnboardingStage.CREATED.value, OnboardingStage.INVITED.value):
            tenant.onboarding_stage = OnboardingStage.DATABASE_SETUP.value
        self.session.add(
            AuditEvent(
                actor_type="SHOWROOM_ACCOUNT", actor_id=user.id, action="INVITATION_ACCEPTED", target_type="MEMBERSHIP",
                target_id=str(membership.id), tenant_id=membership.tenant_id, after={"role": membership.role},
            )
        )
        return await self._issue(user, membership, self._new_sign_in(user))

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

    async def deactivate_member(self, tenant_id: uuid.UUID, membership_id: uuid.UUID, actor_id: uuid.UUID) -> TenantMembership:
        membership = await self._member(tenant_id, membership_id)
        if membership.role == TenantRole.OWNER.value and membership.status == MembershipStatus.ACTIVE.value:
            await self._ensure_other_active_owner(tenant_id, membership_id)
        membership.status = MembershipStatus.DEACTIVATED.value
        membership.invitation_token_hash = None
        membership.invitation_expires_at = None
        self.session.add(
            AuditEvent(
                actor_type="SHOWROOM_ACCOUNT", actor_id=actor_id, action="MEMBER_DEACTIVATED", target_type="MEMBERSHIP",
                target_id=str(membership_id), tenant_id=tenant_id, after={"role": membership.role},
            )
        )
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
        required_version = latest_schema_version()
        stored_version = conn.schema_version if conn else None
        migration_required = bool(conn and conn.secret_reference and stored_version != required_version)
        next_step = None
        if ctx.tenant.status == TenantStatus.SUSPENDED.value:
            next_step = "CONTACT_SUPPORT"
        elif conn_status == ConnectionStatus.PROVISIONING.value and ctx.tenant.onboarding_stage == OnboardingStage.COMPLETE.value:
            next_step = "WAIT_FOR_MAINTENANCE"
        elif not ctx.operational:
            next_step = "CONNECT_DATABASE" if ctx.role == TenantRole.OWNER.value else "WAIT_FOR_OWNER"
        elif migration_required:
            next_step = "RUN_MIGRATION" if ctx.role == TenantRole.OWNER.value else "WAIT_FOR_OWNER"
        return SetupStatusResponse(
            onboarding_stage=ctx.tenant.onboarding_stage,
            suspended_at=ctx.tenant.suspended_at,
            schema_version=stored_version,
            required_schema_version=required_version,
            migration_required=migration_required,
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
