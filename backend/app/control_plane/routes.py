import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.models import PlatformUser, TenantRole
from app.control_plane.schemas import (
    AcceptInvitationRequest,
    InvitationResponse,
    InviteRequest,
    MeResponse,
    MemberResponse,
    MembershipSummary,
    PlatformLoginRequest,
    PlatformUserResponse,
    RefreshRequest,
    SelectTenantRequest,
    SetupStatusResponse,
    TenantResponse,
    TokenPairResponse,
    UpdateRoleRequest,
)
from app.control_plane.service import ControlPlaneService, _summary, member_response
from app.core.tenant_context import (
    TenantContext,
    bearer,
    decode_platform_token,
    get_platform_user,
    get_tenant_context,
    get_token_payload,
    load_active_user,
    require_tenant_roles,
)
from app.database.control_plane import get_control_db
from app.schemas.common import SuccessResponse

router = APIRouter(prefix="/platform", tags=["Platform"])

owner_only = require_tenant_roles(TenantRole.OWNER)


@router.post("/auth/login", response_model=SuccessResponse[TokenPairResponse])
async def login(req: PlatformLoginRequest, session: AsyncSession = Depends(get_control_db)):
    return SuccessResponse(data=await ControlPlaneService(session).login(req.email, req.password))


@router.post("/auth/refresh", response_model=SuccessResponse[TokenPairResponse])
async def refresh(req: RefreshRequest, session: AsyncSession = Depends(get_control_db)):
    return SuccessResponse(data=await ControlPlaneService(session).refresh(req.refresh_token))


@router.get("/me", response_model=SuccessResponse[MeResponse])
async def me(
    payload: dict = Depends(get_token_payload),
    session: AsyncSession = Depends(get_control_db),
):
    user = await load_active_user(session, payload)
    memberships = await ControlPlaneService(session).active_memberships(user.id)
    tid = payload.get("tid")
    # A stale claim for a revoked membership reports no active showroom.
    active = next((m for m in memberships if str(m.tenant_id) == tid), None)
    return SuccessResponse(
        data=MeResponse(
            user=PlatformUserResponse.model_validate(user),
            memberships=[_summary(m) for m in memberships],
            active_tenant_id=active.tenant_id if active else None,
            active_role=active.role if active else None,
        )
    )


@router.get("/memberships", response_model=SuccessResponse[List[MembershipSummary]])
async def memberships(user: PlatformUser = Depends(get_platform_user), session: AsyncSession = Depends(get_control_db)):
    rows = await ControlPlaneService(session).active_memberships(user.id)
    return SuccessResponse(data=[_summary(m) for m in rows])


@router.post("/tenants/select", response_model=SuccessResponse[TokenPairResponse])
async def select_tenant(
    req: SelectTenantRequest,
    user: PlatformUser = Depends(get_platform_user),
    session: AsyncSession = Depends(get_control_db),
):
    """Switch showroom: verifies membership, then issues a token bound to that showroom."""
    return SuccessResponse(data=await ControlPlaneService(session).select_tenant(user, req.tenant_id))


@router.get("/tenants/current", response_model=SuccessResponse[TenantResponse])
async def current_tenant(ctx: TenantContext = Depends(get_tenant_context)):
    return SuccessResponse(data=TenantResponse.model_validate(ctx.tenant))


@router.get("/tenants/current/setup-status", response_model=SuccessResponse[SetupStatusResponse])
async def setup_status(ctx: TenantContext = Depends(get_tenant_context)):
    return SuccessResponse(data=ControlPlaneService.setup_status(ctx))


@router.get("/tenants/current/members", response_model=SuccessResponse[List[MemberResponse]])
async def list_members(
    ctx: TenantContext = Depends(require_tenant_roles(TenantRole.OWNER, TenantRole.HEAD_OF_SALES)),
    session: AsyncSession = Depends(get_control_db),
):
    rows = await ControlPlaneService(session).list_members(ctx.tenant.id)
    return SuccessResponse(data=[member_response(m) for m in rows])


@router.post(
    "/tenants/current/invitations",
    response_model=SuccessResponse[InvitationResponse],
    status_code=status.HTTP_201_CREATED,
)
async def invite_employee(
    req: InviteRequest,
    ctx: TenantContext = Depends(owner_only),
    session: AsyncSession = Depends(get_control_db),
):
    membership, token = await ControlPlaneService(session).invite(ctx, req.email, req.role)
    return SuccessResponse(
        data=InvitationResponse(**member_response(membership).model_dump(), invitation_token=token)
    )


@router.post("/invitations/accept", response_model=SuccessResponse[TokenPairResponse])
async def accept_invitation(
    req: AcceptInvitationRequest,
    auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    session: AsyncSession = Depends(get_control_db),
):
    authenticated_id: Optional[uuid.UUID] = None
    if auth:
        authenticated_id = (await load_active_user(session, decode_platform_token(auth))).id
    return SuccessResponse(data=await ControlPlaneService(session).accept_invitation(req, authenticated_id))


@router.post("/tenants/current/members/{membership_id}/deactivate", response_model=SuccessResponse[MemberResponse])
async def deactivate_member(
    membership_id: uuid.UUID,
    ctx: TenantContext = Depends(owner_only),
    session: AsyncSession = Depends(get_control_db),
):
    membership = await ControlPlaneService(session).deactivate_member(ctx.tenant.id, membership_id, ctx.user.id)
    return SuccessResponse(data=member_response(membership))


@router.put("/tenants/current/members/{membership_id}/role", response_model=SuccessResponse[MemberResponse])
async def update_member_role(
    membership_id: uuid.UUID,
    req: UpdateRoleRequest,
    ctx: TenantContext = Depends(owner_only),
    session: AsyncSession = Depends(get_control_db),
):
    membership = await ControlPlaneService(session).update_role(ctx.tenant.id, membership_id, req.role)
    return SuccessResponse(data=member_response(membership))
