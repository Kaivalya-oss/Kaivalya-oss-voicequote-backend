from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dev_accounts import is_refused_dev_credential
from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, UnauthorizedException
from app.core.logging import logger
from app.core.permissions import RoleEnum
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse, RoleResponse


PUBLIC_SIGNUP_ROLE = RoleEnum.SALES_EXECUTIVE.value


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session)
        self.audit_repo = AuditLogRepository(session)

    async def register(self, req: RegisterRequest, ip_address: Optional[str] = None) -> User:
        if is_refused_dev_credential(req.email, req.password):
            raise BadRequestException("CREDENTIAL_NOT_ALLOWED", "This email or password cannot be used.")

        existing = await self.user_repo.get_by_email(req.email)
        if existing:
            raise ConflictException(
                code="USER_ALREADY_EXISTS",
                message=f"User with email '{req.email}' already exists.",
            )

        # Public self-registration may only create Sales Executives. Elevated roles
        # (ADMIN, DEALER_OWNER, FINANCE, INVENTORY) must be granted by management, and
        # roles are never created on the fly from client input.
        requested_role = (req.role_name or PUBLIC_SIGNUP_ROLE).upper().strip()
        if requested_role != PUBLIC_SIGNUP_ROLE:
            raise ForbiddenException(
                f"Self-registration can only create '{PUBLIC_SIGNUP_ROLE}' accounts. "
                "Ask an administrator to assign other roles."
            )
        role = await self.user_repo.get_role_by_name(PUBLIC_SIGNUP_ROLE)
        if not role:
            raise BadRequestException("ROLE_NOT_CONFIGURED", f"Role '{PUBLIC_SIGNUP_ROLE}' is not configured.")

        user = User(
            email=req.email.lower().strip(),
            full_name=req.full_name.strip(),
            phone=req.phone.strip() if req.phone else None,
            password_hash=hash_password(req.password),
            role_id=role.id,
            # Self-registered accounts wait for a manager to approve them (POST /users/{id}/activate).
            is_active=False,
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

        if is_refused_dev_credential(user.email, req.password):
            logger.warning(f"Refused sign-in for user {user.id}: development account or published password.")
            raise UnauthorizedException("Invalid email or password.")

        if not user.is_active:
            if user.last_login_at is None:
                raise UnauthorizedException(
                    "Your account is awaiting approval. A manager must activate it before you can sign in."
                )
            raise UnauthorizedException("Your account has been deactivated. Please contact an administrator.")

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
        if not user or not user.is_active or is_refused_dev_credential(user.email):
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
