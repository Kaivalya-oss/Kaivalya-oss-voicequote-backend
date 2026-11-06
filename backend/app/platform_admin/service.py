"""Platform Admin sign-in, MFA, sessions and audit. Every outcome that matters writes an audit event."""
import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import status
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.platform_admin import security
from app.platform_admin.config import get_admin_settings
from app.platform_admin.models import (
    AuditEvent,
    PlatformAdmin,
    PlatformAdminRecoveryCode,
    PlatformAdminSession,
    PlatformAdminStatus,
    PlatformAdminThrottle,
)
from app.platform_admin.security import AdminAuthError


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RequestMeta:
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


@dataclass(frozen=True)
class IssuedSession:
    session: PlatformAdminSession
    access_token: str
    refresh_token: str


def add_audit(
    db: AsyncSession,
    action: str,
    meta: RequestMeta,
    actor_id: Optional[uuid.UUID] = None,
    actor_type: str = "PLATFORM_ADMIN",
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    tenant_id: Optional[uuid.UUID] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    reason: Optional[str] = None,
) -> None:
    db.add(
        AuditEvent(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            tenant_id=tenant_id,
            before=before,
            after=after,
            reason=reason,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
        )
    )


# ---------- failure throttling ----------

class Throttle:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_admin_settings()

    def _limit(self, key: str) -> int:
        if key.startswith("ip:"):
            return self.settings.ADMIN_MAX_FAILURES_PER_IP
        if key == "mfa":
            return self.settings.ADMIN_MAX_MFA_FAILURES
        return self.settings.ADMIN_MAX_FAILURES_PER_ACCOUNT

    async def ensure_allowed(self, keys: List[str]) -> None:
        rows = (await self.db.execute(select(PlatformAdminThrottle).where(PlatformAdminThrottle.key.in_(keys)))).scalars().all()
        now = _now()
        waits = [(r.locked_until - now).total_seconds() for r in rows if r.locked_until and r.locked_until > now]
        if waits:
            raise AppException(
                "ADMIN_TEMPORARILY_LOCKED",
                "Too many failed attempts. Try again later.",
                status.HTTP_429_TOO_MANY_REQUESTS,
                {"retry_after_seconds": int(max(waits)) + 1},
            )

    async def record_failure(self, keys: List[str]) -> None:
        now = _now()
        window = timedelta(minutes=self.settings.ADMIN_THROTTLE_WINDOW_MINUTES)
        for key in keys:
            await self.db.execute(
                insert(PlatformAdminThrottle)
                .values(key=key, failures=0, window_started_at=now)
                .on_conflict_do_nothing(index_elements=["key"])
            )
            row = (
                await self.db.execute(select(PlatformAdminThrottle).where(PlatformAdminThrottle.key == key).with_for_update())
            ).scalar_one()
            if now - row.window_started_at > window:
                row.failures, row.window_started_at, row.locked_until = 0, now, None
            row.failures += 1
            if row.failures >= self._limit(key):
                row.locked_until = now + window

    async def clear(self, keys: List[str]) -> None:
        rows = (await self.db.execute(select(PlatformAdminThrottle).where(PlatformAdminThrottle.key.in_(keys)))).scalars().all()
        for row in rows:
            await self.db.delete(row)


# ---------- sign-in ----------

class AdminAuthService:
    def __init__(self, db: AsyncSession, meta: RequestMeta):
        self.db = db
        self.meta = meta
        self.settings = get_admin_settings()
        self.throttle = Throttle(db)

    def _ip_key(self) -> str:
        return f"ip:{self.meta.ip_address or 'unknown'}"

    async def _admin(self, admin_id: Optional[uuid.UUID] = None) -> Optional[PlatformAdmin]:
        query = select(PlatformAdmin)
        if admin_id is not None:
            query = query.where(PlatformAdmin.id == admin_id)
        return (await self.db.execute(query.execution_options(populate_existing=True))).scalars().first()

    async def login(self, email: str, password: str) -> Tuple[str, bool]:
        """Password step. Returns a short-lived MFA token and whether enrolment is still needed.

        No session exists until MFA succeeds, so a password alone can't reach any admin endpoint.
        """
        keys = [self._ip_key(), "account"]
        await self.throttle.ensure_allowed(keys)
        admin = await self._admin()
        email_matches = admin is not None and admin.email == email.strip().lower()
        hashed = admin.password_hash if email_matches else security.DUMMY_PASSWORD_HASH
        valid = await asyncio.to_thread(security.verify_password, password, hashed)
        if not email_matches or not valid or admin.status != PlatformAdminStatus.ACTIVE.value:
            await self.throttle.record_failure(keys)
            add_audit(self.db, "ADMIN_LOGIN_FAILED", self.meta, actor_type="ANONYMOUS")
            await self.db.commit()
            raise AdminAuthError()

        enrolled = admin.mfa_enrolled_at is not None
        add_audit(self.db, "ADMIN_PASSWORD_ACCEPTED", self.meta, actor_id=admin.id, target_type="PLATFORM_ADMIN", target_id=str(admin.id))
        await self.db.commit()
        token_type = security.TYPE_MFA_CHALLENGE if enrolled else security.TYPE_MFA_ENROLL
        return security.issue_mfa_token(admin.id, token_type), not enrolled

    async def _admin_from_mfa_token(self, token: str, token_type: str) -> PlatformAdmin:
        payload = security.decode_token(token, token_type)
        admin = await self._admin(uuid.UUID(payload["sub"]))
        if admin is None or admin.status != PlatformAdminStatus.ACTIVE.value:
            raise AdminAuthError()
        return admin

    async def start_enrollment(self, token: str) -> Tuple[str, str]:
        admin = await self._admin_from_mfa_token(token, security.TYPE_MFA_ENROLL)
        if admin.mfa_enrolled_at is not None:
            raise AppException("ADMIN_MFA_ALREADY_ENROLLED", "MFA is already set up.", status.HTTP_409_CONFLICT)
        secret = security.generate_totp_secret()
        admin.mfa_secret_encrypted = security.encrypt_mfa_secret(secret)
        add_audit(self.db, "ADMIN_MFA_ENROLLMENT_STARTED", self.meta, actor_id=admin.id, target_type="PLATFORM_ADMIN", target_id=str(admin.id))
        await self.db.commit()
        return secret, security.otpauth_uri(secret, admin.email)

    async def confirm_enrollment(self, token: str, code: str) -> Tuple[IssuedSession, List[str], PlatformAdmin]:
        admin = await self._admin_from_mfa_token(token, security.TYPE_MFA_ENROLL)
        if admin.mfa_enrolled_at is not None:
            raise AppException("ADMIN_MFA_ALREADY_ENROLLED", "MFA is already set up.", status.HTTP_409_CONFLICT)
        keys = [self._ip_key(), "mfa"]
        await self.throttle.ensure_allowed(keys)
        step = None
        if admin.mfa_secret_encrypted:
            step = security.match_totp(security.decrypt_mfa_secret(admin.mfa_secret_encrypted), code, admin.mfa_last_used_step)
        if step is None:
            await self.throttle.record_failure(keys)
            add_audit(self.db, "ADMIN_MFA_FAILED", self.meta, actor_id=admin.id, target_type="PLATFORM_ADMIN", target_id=str(admin.id))
            await self.db.commit()
            raise AdminAuthError()

        now = _now()
        admin.mfa_enrolled_at = now
        admin.mfa_last_used_step = step
        codes = [security.generate_recovery_code() for _ in range(security.RECOVERY_CODE_COUNT)]
        for recovery_code in codes:
            self.db.add(PlatformAdminRecoveryCode(admin_id=admin.id, code_hash=security.sha256_hex(security.normalize_recovery_code(recovery_code))))
        add_audit(self.db, "ADMIN_MFA_ENROLLED", self.meta, actor_id=admin.id, target_type="PLATFORM_ADMIN", target_id=str(admin.id))
        issued = await self._open_session(admin, now, keys + ["account"])
        return issued, codes, admin

    async def verify_mfa(self, token: str, code: Optional[str], recovery_code: Optional[str]) -> Tuple[IssuedSession, PlatformAdmin]:
        admin = await self._admin_from_mfa_token(token, security.TYPE_MFA_CHALLENGE)
        if admin.mfa_enrolled_at is None or not admin.mfa_secret_encrypted:
            raise AdminAuthError()
        keys = [self._ip_key(), "mfa"]
        await self.throttle.ensure_allowed(keys)
        admin_id = admin.id  # a rollback below expires the loaded object

        accepted = False
        if recovery_code:
            used = await self.db.execute(
                update(PlatformAdminRecoveryCode)
                .where(
                    PlatformAdminRecoveryCode.admin_id == admin.id,
                    PlatformAdminRecoveryCode.code_hash == security.sha256_hex(security.normalize_recovery_code(recovery_code)),
                    PlatformAdminRecoveryCode.used_at.is_(None),
                )
                .values(used_at=_now())
            )
            accepted = used.rowcount == 1
            if accepted:
                add_audit(self.db, "ADMIN_RECOVERY_CODE_USED", self.meta, actor_id=admin.id, target_type="PLATFORM_ADMIN", target_id=str(admin.id))
        elif code:
            step = security.match_totp(security.decrypt_mfa_secret(admin.mfa_secret_encrypted), code, admin.mfa_last_used_step)
            if step is not None:
                # Conditional, so two simultaneous requests with one code can't both succeed.
                claimed = await self.db.execute(
                    update(PlatformAdmin)
                    .where(
                        PlatformAdmin.id == admin.id,
                        (PlatformAdmin.mfa_last_used_step.is_(None)) | (PlatformAdmin.mfa_last_used_step < step),
                    )
                    .values(mfa_last_used_step=step)
                )
                accepted = claimed.rowcount == 1

        if not accepted:
            await self.db.rollback()
            await self.throttle.record_failure(keys)
            add_audit(self.db, "ADMIN_MFA_FAILED", self.meta, actor_id=admin_id, target_type="PLATFORM_ADMIN", target_id=str(admin_id))
            await self.db.commit()
            raise AdminAuthError()
        return await self._open_session(admin, _now(), keys + ["account"]), admin

    async def _open_session(self, admin: PlatformAdmin, now: datetime, clear_keys: List[str]) -> IssuedSession:
        session_id = uuid.uuid4()
        refresh = security.generate_refresh_token(session_id)
        session = PlatformAdminSession(
            id=session_id,
            admin_id=admin.id,
            refresh_token_hash=security.sha256_hex(refresh),
            created_at=now,
            last_used_at=now,
            absolute_expires_at=now + timedelta(hours=self.settings.ADMIN_SESSION_ABSOLUTE_HOURS),
            mfa_verified_at=now,
            ip_address=self.meta.ip_address,
            user_agent=self.meta.user_agent,
        )
        self.db.add(session)
        admin.last_login_at = now
        await self.throttle.clear(clear_keys)
        add_audit(self.db, "ADMIN_LOGIN_SUCCEEDED", self.meta, actor_id=admin.id, target_type="ADMIN_SESSION", target_id=str(session_id))
        await self.db.commit()
        return IssuedSession(session, security.issue_access_token(admin.id, session_id), refresh)

    # ---------- sessions ----------

    def session_problem(self, session: PlatformAdminSession) -> Optional[str]:
        now = _now()
        if session.revoked_at is not None:
            return "revoked"
        if session.absolute_expires_at <= now:
            return "expired"
        if session.last_used_at + timedelta(minutes=self.settings.ADMIN_SESSION_IDLE_MINUTES) <= now:
            return "idle"
        return None

    async def refresh(self, refresh_token: Optional[str]) -> Tuple[IssuedSession, PlatformAdmin]:
        """Rotates on every use. A token that was already replaced means a copy exists: end the session."""
        session_id = security.refresh_token_session_id(refresh_token or "")
        if session_id is None:
            raise AdminAuthError()
        session = (
            await self.db.execute(select(PlatformAdminSession).where(PlatformAdminSession.id == session_id).with_for_update())
        ).scalar_one_or_none()
        if session is None or self.session_problem(session):
            raise AdminAuthError()
        if session.refresh_token_hash != security.sha256_hex(refresh_token):
            self._revoke(session, "REFRESH_TOKEN_REUSED")
            add_audit(self.db, "ADMIN_REFRESH_REUSE_DETECTED", self.meta, actor_id=session.admin_id, target_type="ADMIN_SESSION", target_id=str(session.id))
            await self.db.commit()
            raise AdminAuthError()
        admin = await self._admin(session.admin_id)
        if admin is None or admin.status != PlatformAdminStatus.ACTIVE.value:
            raise AdminAuthError()
        new_refresh = security.generate_refresh_token(session.id)
        session.refresh_token_hash = security.sha256_hex(new_refresh)
        session.last_used_at = _now()
        await self.db.commit()
        return IssuedSession(session, security.issue_access_token(admin.id, session.id), new_refresh), admin

    def _revoke(self, session: PlatformAdminSession, reason: str) -> None:
        session.revoked_at = _now()
        session.revoked_reason = reason

    async def logout(self, admin: PlatformAdmin, session: PlatformAdminSession) -> None:
        current = await self.db.get(PlatformAdminSession, session.id)
        self._revoke(current, "LOGOUT")
        add_audit(self.db, "ADMIN_LOGOUT", self.meta, actor_id=admin.id, target_type="ADMIN_SESSION", target_id=str(session.id))
        await self.db.commit()

    async def list_sessions(self, admin: PlatformAdmin) -> List[PlatformAdminSession]:
        rows = await self.db.execute(
            select(PlatformAdminSession).where(PlatformAdminSession.admin_id == admin.id).order_by(PlatformAdminSession.created_at.desc()).limit(50)
        )
        return [s for s in rows.scalars().all() if not self.session_problem(s)]

    async def revoke_session(self, admin: PlatformAdmin, session_id: uuid.UUID) -> None:
        session = (
            await self.db.execute(
                select(PlatformAdminSession).where(PlatformAdminSession.id == session_id, PlatformAdminSession.admin_id == admin.id)
            )
        ).scalar_one_or_none()
        if session is None:
            raise AppException("ADMIN_SESSION_NOT_FOUND", "This session doesn't exist.", status.HTTP_404_NOT_FOUND)
        if session.revoked_at is None:
            self._revoke(session, "REVOKED_BY_ADMIN")
            add_audit(self.db, "ADMIN_SESSION_REVOKED", self.meta, actor_id=admin.id, target_type="ADMIN_SESSION", target_id=str(session_id))
            await self.db.commit()

    async def revoke_all_sessions(self, admin: PlatformAdmin) -> int:
        rows = (
            await self.db.execute(
                select(PlatformAdminSession).where(PlatformAdminSession.admin_id == admin.id, PlatformAdminSession.revoked_at.is_(None))
            )
        ).scalars().all()
        for session in rows:
            self._revoke(session, "REVOKED_ALL_BY_ADMIN")
        add_audit(self.db, "ADMIN_ALL_SESSIONS_REVOKED", self.meta, actor_id=admin.id, target_type="PLATFORM_ADMIN", target_id=str(admin.id), after={"revoked": len(rows)})
        await self.db.commit()
        return len(rows)
