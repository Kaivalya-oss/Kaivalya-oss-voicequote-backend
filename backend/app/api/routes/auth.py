from typing import List
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.permissions import RoleEnum
from app.database.dependencies import get_db
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, RefreshTokenRequest, RegisterRequest, RoleResponse, TokenResponse, UserResponse
from app.schemas.common import MessageResponse, SuccessResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=SuccessResponse[UserResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register a new system user",
)
async def register(
    req: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ip_address: str = Depends(get_client_ip),
):
    service = AuthService(db)
    user = await service.register(req, ip_address=ip_address)
    return SuccessResponse(data=UserResponse.model_validate(user))


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate user and obtain JWT tokens",
)
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
    ip_address: str = Depends(get_client_ip),
):
    service = AuthService(db)
    return await service.login(req, ip_address=ip_address)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token using refresh token",
)
async def refresh_token(
    req: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    return await service.refresh(req.refresh_token)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Logout user session",
)
async def logout(
    current_user: User = Depends(get_current_user),
):
    # JWT is stateless; client drops tokens.
    return MessageResponse(success=True, message="Successfully logged out.")


@router.get(
    "/me",
    response_model=SuccessResponse[UserResponse],
    summary="Get current authenticated user profile",
)
async def get_me(
    current_user: User = Depends(get_current_user),
):
    return SuccessResponse(data=UserResponse.model_validate(current_user))


@router.get(
    "/roles",
    response_model=SuccessResponse[List[RoleResponse]],
    summary="List all system RBAC roles",
)
async def get_roles(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_repo = UserRepository(db)
    roles = await user_repo.get_all_roles()
    return SuccessResponse(data=[RoleResponse.model_validate(r) for r in roles])
