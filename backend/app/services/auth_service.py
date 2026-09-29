from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestException, ConflictException, UnauthorizedException
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse, RoleResponse


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session)
        self.audit_repo = AuditLogRepository(session)

    async def register(self, req: RegisterRequest, ip_address: Optional[str] = None) -> User:
        existing = await self.user_repo.get_by_email(req.email)
        if existing:
            raise ConflictException(
                code="USER_ALREADY_EXISTS",
                message=f"User with email '{req.email}' already exists.",
            )

        role = await self.user_repo.get_role_by_name(req.role_name)
        if not role:
            # Create role if missing for initial flexibility
            role = await self.user_repo.create_role(req.role_name, f"System role {req.role_name}")

        user = User(
            email=req.email.lower().strip(),
            full_name=req.full_name.strip(),
            phone=req.phone.strip() if req.phone else None,
            password_hash=hash_password(req.password),
            role_id=role.id,
            is_active=True,
        )
        created_user = await self.user_repo.create(user)

        await self.audit_repo.log_action(
            user_id=created_user.id,
            action="USER_REGISTERED",
            entity_type="USER",
            entity_id=created_user.id,
            new_value={"email": created_user.email, "role": role.name},
            ip_address=ip_address,
        )

        # Refresh with role loaded
        return await self.user_repo.get_by_email(created_user.email)  # type: ignore

    async def login(self, req: LoginRequest, ip_address: Optional[str] = None) -> TokenResponse:
        user = await self.user_repo.get_by_email(req.email)
        if not user:
            raise UnauthorizedException("Invalid email or password.")

        if not verify_password(req.password, user.password_hash):
            raise UnauthorizedException("Invalid email or password.")

        if not user.is_active:
            raise UnauthorizedException("User account is inactive. Please contact administrator.")

        await self.user_repo.update_last_login(user)

        extra_claims = {
            "email": user.email,
            "role": user.role.name,
            "name": user.full_name,
        }
        access_token = create_access_token(subject=user.id, extra_claims=extra_claims)
        refresh_token = create_refresh_token(subject=user.id)

        await self.audit_repo.log_action(
            user_id=user.id,
            action="USER_LOGIN",
            entity_type="USER",
            entity_id=user.id,
            ip_address=ip_address,
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=UserResponse.model_validate(user),
        )

    async def refresh(self, refresh_token: str) -> TokenResponse:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise UnauthorizedException("Invalid token type. Refresh token required.")

        user_id = int(payload.get("sub", 0))
        user = await self.user_repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise UnauthorizedException("User no longer exists or is inactive.")

        extra_claims = {
            "email": user.email,
            "role": user.role.name,
            "name": user.full_name,
        }
        new_access_token = create_access_token(subject=user.id, extra_claims=extra_claims)
        new_refresh_token = create_refresh_token(subject=user.id)

        return TokenResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
            expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=UserResponse.model_validate(user),
        )
