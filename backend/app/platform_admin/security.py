"""Passwords, TOTP, recovery codes and tokens for the Platform Admin. Separate from showroom sign-in."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import status

from app.core.exceptions import AppException
from app.platform_admin.config import TOKEN_ALGORITHM, TOKEN_AUDIENCE, TOKEN_ISSUER, get_admin_settings

MIN_PASSWORD_LENGTH = 14
# Published in the repository as development accounts, so they can never protect this account.
PUBLISHED_PASSWORDS = frozenset({"AdminPass123!", "OwnerPass123!", "SalesPass123!", "FinancePass123!", "InventoryPass123!"})

TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
TOTP_ALLOWED_DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10

TYPE_ACCESS = "admin_access"
TYPE_MFA_CHALLENGE = "admin_mfa_challenge"
TYPE_MFA_ENROLL = "admin_mfa_enroll"


class AdminAuthError(AppException):
    """One generic message, so a response never reveals which check failed."""

    def __init__(self, message: str = "Authentication failed."):
        super().__init__("ADMIN_AUTH_FAILED", message, status.HTTP_401_UNAUTHORIZED)


# ---------- passwords ----------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# Checked when no such account exists, so a login takes the same time either way.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


def password_problem(password: str) -> Optional[str]:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"The password must have at least {MIN_PASSWORD_LENGTH} characters."
    if len(password.encode("utf-8")) > 72:
        return "The password can't be longer than 72 bytes."
    if password in PUBLISHED_PASSWORDS:
        return "This password is published in the repository and can't be used."
    return None


# ---------- TOTP (RFC 6238, SHA-1, the setting authenticator apps support) ----------

def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def totp_code(secret: str, step: int) -> str:
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def totp_step(now: Optional[float] = None) -> int:
    return int((time.time() if now is None else now) // TOTP_PERIOD_SECONDS)


def match_totp(secret: str, code: str, last_used_step: Optional[int], now: Optional[float] = None) -> Optional[int]:
    """The matching step, or None. Steps at or before last_used_step are refused, which stops replay."""
    code = code.strip()
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    current = totp_step(now)
    matched = None
    for step in range(current - TOTP_ALLOWED_DRIFT_STEPS, current + TOTP_ALLOWED_DRIFT_STEPS + 1):
        if hmac.compare_digest(totp_code(secret, step), code) and (last_used_step is None or step > last_used_step):
            matched = step
    return matched


def otpauth_uri(secret: str, email: str) -> str:
    label = quote(f"VoiceQuote Platform Admin:{email}")
    query = urlencode({"secret": secret, "issuer": "VoiceQuote Platform Admin", "digits": TOTP_DIGITS, "period": TOTP_PERIOD_SECONDS})
    return f"otpauth://totp/{label}?{query}"


def encrypt_mfa_secret(secret: str) -> str:
    return Fernet(get_admin_settings().ADMIN_MFA_ENCRYPTION_KEY.encode("utf-8")).encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_mfa_secret(encrypted: str) -> str:
    try:
        return Fernet(get_admin_settings().ADMIN_MFA_ENCRYPTION_KEY.encode("utf-8")).decrypt(encrypted.encode("ascii")).decode("ascii")
    except InvalidToken:
        raise AdminAuthError() from None


# ---------- recovery codes and refresh tokens: random enough that a plain SHA-256 is sufficient ----------

def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(15)).decode("ascii")
    return "-".join(raw[i:i + 6] for i in range(0, 24, 6))


def normalize_recovery_code(code: str) -> str:
    return code.strip().upper().replace(" ", "")


def generate_refresh_token(session_id: uuid.UUID) -> str:
    return f"{session_id}.{secrets.token_urlsafe(48)}"


def refresh_token_session_id(token: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(token.split(".", 1)[0])
    except ValueError:
        return None


# ---------- signed tokens ----------

def _issue(subject: uuid.UUID, token_type: str, lifetime: timedelta, claims: Optional[Dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "sub": str(subject),
        "typ": token_type,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + lifetime,
    }
    payload.update(claims or {})
    return jwt.encode(payload, get_admin_settings().ADMIN_JWT_SECRET_KEY, algorithm=TOKEN_ALGORITHM)


def issue_access_token(admin_id: uuid.UUID, session_id: uuid.UUID) -> str:
    minutes = get_admin_settings().ADMIN_ACCESS_TOKEN_MINUTES
    return _issue(admin_id, TYPE_ACCESS, timedelta(minutes=minutes), {"sid": str(session_id), "mfa": True})


def issue_mfa_token(admin_id: uuid.UUID, token_type: str) -> str:
    return _issue(admin_id, token_type, timedelta(minutes=get_admin_settings().ADMIN_MFA_TOKEN_MINUTES))


def decode_token(token: str, expected_type: str) -> Dict[str, Any]:
    """Signature, algorithm, audience, issuer, expiry and token type must all be right."""
    try:
        payload = jwt.decode(
            token,
            get_admin_settings().ADMIN_JWT_SECRET_KEY,
            algorithms=[TOKEN_ALGORITHM],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
            options={"require": ["exp", "iat", "aud", "iss", "sub", "typ", "jti"]},
        )
    except jwt.InvalidTokenError:
        raise AdminAuthError() from None
    if payload.get("typ") != expected_type:
        raise AdminAuthError()
    return payload
