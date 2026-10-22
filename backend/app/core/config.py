import os
from functools import lru_cache
from typing import List, Union
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Used only outside production when JWT_SECRET_KEY is not configured.
DEV_FALLBACK_JWT_SECRET = "dev-only-insecure-jwt-secret-do-not-use-in-production"

# Values that have been published (committed to the repository or shipped as examples)
# and must never sign production tokens.
INSECURE_JWT_SECRETS = {
    DEV_FALLBACK_JWT_SECRET,
    "change_this_secret_in_production_super_secure_key_12345",
    "dev_insecure_jwt_secret_key_change_in_production_998877665544332211",
    "production_secure_secret_key_dealership_super_secret_9988",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Two-Wheeler Dealership Sales Automation & Lead Management"
    APP_ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/dealership_db"
    TEST_DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/dealership_test_db"

    # Security & JWT
    # Required in production (APP_ENV=production): set JWT_SECRET_KEY via the environment / .env.
    # Outside production an insecure development fallback is used when it is left empty.
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    FRONTEND_URL: Union[str, List[str]] = ["http://localhost:3000", "http://localhost:5173"]

    @field_validator("FRONTEND_URL", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            return v
        return ["*"]

    # WhatsApp Business Cloud API
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_API_VERSION: str = "v18.0"

    # Email (SMTP). Empty SMTP_USER / SMTP_PASSWORD = simulation mode: nothing is sent.
    # For Gmail use smtp.gmail.com:587 and a Google *app password* (never the account password).
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "VoiceQuote Dealership"
    # Receives a notice each time a quotation is emailed to a customer (defaults to SMTP_USER).
    ADMIN_NOTIFY_EMAIL: str = ""

    # Gemini AI
    GEMINI_API_KEY: str = ""

    # Storage Paths
    UPLOAD_DIR: str = "uploads"
    PDF_STORAGE_DIR: str = "generated_pdfs"

    @model_validator(mode="after")
    def enforce_jwt_secret(self) -> "Settings":
        secret = self.JWT_SECRET_KEY.strip()
        if self.APP_ENV.lower() == "production":
            if len(secret) < 32 or secret in INSECURE_JWT_SECRETS:
                raise ValueError(
                    "JWT_SECRET_KEY must be set to a strong secret (32+ characters) when APP_ENV=production."
                )
        elif not secret:
            self.JWT_SECRET_KEY = DEV_FALLBACK_JWT_SECRET
        return self


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
