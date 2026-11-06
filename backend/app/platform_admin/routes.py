"""The Platform Admin API. Deliberately has no endpoint that creates a Platform Admin."""
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform_admin.config import get_admin_settings
from app.platform_admin.database import get_admin_db
from app.platform_admin.dependencies import AdminContext, request_meta, require_allowed_origin, require_platform_admin
from app.platform_admin.models import PlatformAdmin, PlatformAdminSession
from app.platform_admin.service import AdminAuthService, IssuedSession
from app.schemas.common import MessageResponse, SuccessResponse

router = APIRouter(prefix="/api/v1/admin", tags=["Platform Admin"])

REFRESH_COOKIE = "vq_admin_refresh"
REFRESH_COOKIE_PATH = "/api/v1/admin/auth"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)


class LoginResponse(BaseModel):
    mfa_token: str
    mfa_enrollment_required: bool
    expires_in: int


class MfaTokenRequest(BaseModel):
    mfa_token: str = Field(..., min_length=20, max_length=2000)


class EnrollmentStartResponse(BaseModel):
    secret: str
    otpauth_uri: str


class EnrollmentConfirmRequest(MfaTokenRequest):
    code: str = Field(..., min_length=6, max_length=6)


class MfaVerifyRequest(MfaTokenRequest):
    code: Optional[str] = Field(None, min_length=6, max_length=6)
    recovery_code: Optional[str] = Field(None, min_length=10, max_length=64)

    @model_validator(mode="after")
    def _exactly_one(self) -> "MfaVerifyRequest":
        if bool(self.code) == bool(self.recovery_code):
            raise ValueError("Provide either a code or a recovery code.")
        return self


class AdminProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    status: str
    mfa_enrolled_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None


class SessionTokens(BaseModel):
    """The refresh token isn't here: it's only in an HttpOnly cookie that scripts can't read."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    admin: AdminProfile


class EnrollmentConfirmResponse(SessionTokens):
    recovery_codes: List[str]  # shown once, stored only as hashes


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime
    absolute_expires_at: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    current: bool = False


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_admin_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.ADMIN_SESSION_ABSOLUTE_HOURS * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.ADMIN_COOKIE_SECURE,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_admin_settings()
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, httponly=True, secure=settings.ADMIN_COOKIE_SECURE, samesite="strict")


def _tokens(issued: IssuedSession, admin: PlatformAdmin) -> dict:
    return {
        "access_token": issued.access_token,
        "expires_in": get_admin_settings().ADMIN_ACCESS_TOKEN_MINUTES * 60,
        "admin": AdminProfile.model_validate(admin),
    }


def _service(request: Request, db: AsyncSession) -> AdminAuthService:
    return AdminAuthService(db, request_meta(request))


@router.post("/auth/login", response_model=SuccessResponse[LoginResponse])
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_admin_db)):
    """First step only. No session exists until MFA succeeds."""
    token, enrollment_required = await _service(request, db).login(req.email, req.password)
    return SuccessResponse(
        data=LoginResponse(
            mfa_token=token,
            mfa_enrollment_required=enrollment_required,
            expires_in=get_admin_settings().ADMIN_MFA_TOKEN_MINUTES * 60,
        )
    )


@router.post("/auth/mfa/enroll/start", response_model=SuccessResponse[EnrollmentStartResponse])
async def enroll_start(req: MfaTokenRequest, request: Request, db: AsyncSession = Depends(get_admin_db)):
    secret, uri = await _service(request, db).start_enrollment(req.mfa_token)
    return SuccessResponse(data=EnrollmentStartResponse(secret=secret, otpauth_uri=uri))


@router.post("/auth/mfa/enroll/confirm", response_model=SuccessResponse[EnrollmentConfirmResponse])
async def enroll_confirm(req: EnrollmentConfirmRequest, request: Request, response: Response, db: AsyncSession = Depends(get_admin_db)):
    issued, codes, admin = await _service(request, db).confirm_enrollment(req.mfa_token, req.code)
    _set_refresh_cookie(response, issued.refresh_token)
    return SuccessResponse(data=EnrollmentConfirmResponse(**_tokens(issued, admin), recovery_codes=codes))


@router.post("/auth/mfa/verify", response_model=SuccessResponse[SessionTokens])
async def mfa_verify(req: MfaVerifyRequest, request: Request, response: Response, db: AsyncSession = Depends(get_admin_db)):
    issued, admin = await _service(request, db).verify_mfa(req.mfa_token, req.code, req.recovery_code)
    _set_refresh_cookie(response, issued.refresh_token)
    return SuccessResponse(data=SessionTokens(**_tokens(issued, admin)))


@router.post("/auth/refresh", response_model=SuccessResponse[SessionTokens], dependencies=[Depends(require_allowed_origin)])
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_admin_db),
    refresh_token: Optional[str] = Cookie(None, alias=REFRESH_COOKIE),
):
    issued, admin = await _service(request, db).refresh(refresh_token)
    _set_refresh_cookie(response, issued.refresh_token)
    return SuccessResponse(data=SessionTokens(**_tokens(issued, admin)))


@router.post("/auth/logout", response_model=MessageResponse)
async def logout(request: Request, response: Response, ctx: AdminContext = Depends(require_platform_admin), db: AsyncSession = Depends(get_admin_db)):
    await _service(request, db).logout(ctx.admin, ctx.session)
    _clear_refresh_cookie(response)
    return MessageResponse(message="Signed out.")


@router.post("/auth/logout-all", response_model=MessageResponse)
async def logout_all(request: Request, response: Response, ctx: AdminContext = Depends(require_platform_admin), db: AsyncSession = Depends(get_admin_db)):
    count = await _service(request, db).revoke_all_sessions(ctx.admin)
    _clear_refresh_cookie(response)
    return MessageResponse(message=f"Ended {count} session(s).")


@router.get("/auth/sessions", response_model=SuccessResponse[List[SessionView]])
async def sessions(request: Request, ctx: AdminContext = Depends(require_platform_admin), db: AsyncSession = Depends(get_admin_db)):
    rows: List[PlatformAdminSession] = await _service(request, db).list_sessions(ctx.admin)
    views = [SessionView.model_validate(s).model_copy(update={"current": s.id == ctx.session.id}) for s in rows]
    return SuccessResponse(data=views)


@router.post("/auth/sessions/{session_id}/revoke", response_model=MessageResponse)
async def revoke_session(session_id: uuid.UUID, request: Request, ctx: AdminContext = Depends(require_platform_admin), db: AsyncSession = Depends(get_admin_db)):
    await _service(request, db).revoke_session(ctx.admin, session_id)
    return MessageResponse(message="Session ended.")


@router.get("/me", response_model=SuccessResponse[AdminProfile])
async def me(ctx: AdminContext = Depends(require_platform_admin)):
    return SuccessResponse(data=AdminProfile.model_validate(ctx.admin))
