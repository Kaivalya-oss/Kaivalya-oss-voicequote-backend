"""Settings for the Platform Admin process only.

This package deliberately doesn't import app.core.config. That module requires the showroom signing
key and showroom database URLs, and this process must never hold those. There are no default secrets:
a missing or weak value stops the process.
"""
import os
from functools import lru_cache
from typing import List, Mapping

from cryptography.fernet import Fernet
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TOKEN_AUDIENCE = "voicequote-admin"
TOKEN_ISSUER = "voicequote-admin-api"
TOKEN_ALGORITHM = "HS256"
DEV_ENVIRONMENTS = frozenset({"development", "test"})

# Holding any of these would give this process a route to showroom data, credentials or sign-in.
FORBIDDEN_ENVIRONMENT = (
    "JWT_SECRET_KEY",
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "CONTROL_PLANE_DATABASE_URL",
    "SECRETS_MASTER_KEY",
    "SECRETS_LOCAL_DIR",
    "SECRETS_BACKEND",
    "SMTP_PASSWORD",
    "WHATSAPP_ACCESS_TOKEN",
    "GEMINI_API_KEY",
)


class AdminSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, case_sensitive=True, extra="ignore")

    ADMIN_ENV: str = "production"
    ADMIN_DATABASE_URL: str
    ADMIN_JWT_SECRET_KEY: str
    ADMIN_MFA_ENCRYPTION_KEY: str
    ADMIN_ALLOWED_ORIGINS: str = ""  # comma separated. Empty allows no browser origin at all.
    ADMIN_TRUSTED_PROXY_IPS: str = ""  # comma separated. Empty means forwarded headers are never trusted.
    ADMIN_COOKIE_SECURE: bool = True

    # Owner invitations. Without these, showrooms can't be created, rather than sending nothing silently.
    ADMIN_SMTP_HOST: str = ""
    ADMIN_SMTP_PORT: int = 587
    ADMIN_SMTP_USER: str = ""
    ADMIN_SMTP_PASSWORD: str = ""
    ADMIN_SMTP_FROM: str = ""
    ADMIN_SMTP_STARTTLS: bool = True
    ADMIN_OWNER_INVITE_URL: str = ""  # the showroom app's invitation page
    ADMIN_INVITATION_EXPIRE_HOURS: int = 72

    ADMIN_ACCESS_TOKEN_MINUTES: int = 10
    ADMIN_MFA_TOKEN_MINUTES: int = 5
    ADMIN_SESSION_IDLE_MINUTES: int = 30
    ADMIN_SESSION_ABSOLUTE_HOURS: int = 12

    ADMIN_THROTTLE_WINDOW_MINUTES: int = 15
    ADMIN_MAX_FAILURES_PER_IP: int = 5
    ADMIN_MAX_FAILURES_PER_ACCOUNT: int = 20  # higher, so one source can't easily lock out the only admin
    ADMIN_MAX_MFA_FAILURES: int = 5

    @field_validator("ADMIN_DATABASE_URL")
    @classmethod
    def _database_url(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("ADMIN_DATABASE_URL must be a postgresql+asyncpg URL.")
        return v

    @field_validator("ADMIN_JWT_SECRET_KEY")
    @classmethod
    def _signing_key(cls, v: str) -> str:
        if len(v.strip()) < 32:
            raise ValueError("ADMIN_JWT_SECRET_KEY must have at least 32 characters.")
        return v

    @field_validator("ADMIN_MFA_ENCRYPTION_KEY")
    @classmethod
    def _mfa_key(cls, v: str) -> str:
        try:
            Fernet(v.encode("utf-8"))
        except ValueError:
            raise ValueError("ADMIN_MFA_ENCRYPTION_KEY must be a Fernet key.") from None
        return v

    @model_validator(mode="after")
    def _secure_cookie_outside_development(self) -> "AdminSettings":
        if not self.ADMIN_COOKIE_SECURE and self.ADMIN_ENV.strip().lower() not in DEV_ENVIRONMENTS:
            raise ValueError("ADMIN_COOKIE_SECURE can only be disabled in development or test.")
        return self

    @model_validator(mode="after")
    def _secure_invitations_in_production(self) -> "AdminSettings":
        if self.ADMIN_ENV.strip().lower() == "production":
            if not self.ADMIN_SMTP_STARTTLS:
                raise ValueError("ADMIN_SMTP_STARTTLS can't be disabled in production.")
            if self.ADMIN_OWNER_INVITE_URL and not self.ADMIN_OWNER_INVITE_URL.startswith("https://"):
                raise ValueError("ADMIN_OWNER_INVITE_URL must use https in production.")
        return self

    @property
    def allowed_origins(self) -> List[str]:
        return [o.strip().rstrip("/") for o in self.ADMIN_ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def trusted_proxies(self) -> List[str]:
        return [ip.strip() for ip in self.ADMIN_TRUSTED_PROXY_IPS.split(",") if ip.strip()]


@lru_cache()
def get_admin_settings() -> AdminSettings:
    return AdminSettings()


def forbidden_environment_present(environ: Mapping[str, str] | None = None) -> List[str]:
    """Names of variables that this process must not hold. Values are never returned."""
    source = os.environ if environ is None else environ
    return [name for name in FORBIDDEN_ENVIRONMENT if source.get(name)]
