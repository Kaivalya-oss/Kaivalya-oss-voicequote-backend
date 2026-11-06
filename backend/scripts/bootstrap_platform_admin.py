"""Create the one Platform Admin. A server-side step, run once, and never reachable from the API.

    ADMIN_BOOTSTRAP_DATABASE_URL=... ADMIN_EMAIL=... ADMIN_FULL_NAME=... python scripts/bootstrap_platform_admin.py

The URL is the migration owner's, because the running admin process's role can't insert admins. The
password is prompted for when a terminal is attached; otherwise it comes from ADMIN_PASSWORD. See
docs/PLATFORM_ADMIN.md. It refuses to run if a Platform Admin already exists.
"""
import asyncio
import getpass
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bcrypt  # noqa: E402
from pydantic import EmailStr, TypeAdapter, ValidationError  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.platform_admin.models import AuditEvent, PlatformAdmin, PlatformAdminStatus  # noqa: E402

MIN_PASSWORD_LENGTH = 14
PUBLISHED_PASSWORDS = frozenset({"AdminPass123!", "OwnerPass123!", "SalesPass123!", "FinancePass123!", "InventoryPass123!"})


class BootstrapError(Exception):
    pass


async def bootstrap(database_url: str, email: str, full_name: str, password: str) -> uuid.UUID:
    try:
        email = TypeAdapter(EmailStr).validate_python(email.strip()).lower()
    except ValidationError:
        raise BootstrapError("ADMIN_EMAIL isn't a valid email address.") from None
    if not full_name.strip():
        raise BootstrapError("ADMIN_FULL_NAME is required.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise BootstrapError(f"The password must have at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > 72:
        raise BootstrapError("The password can't be longer than 72 bytes.")
    if password in PUBLISHED_PASSWORDS:
        raise BootstrapError("This password is published in the repository and can't be used.")

    engine = create_async_engine(database_url, hide_parameters=True)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            if (await session.execute(select(func.count()).select_from(PlatformAdmin))).scalar_one():
                raise BootstrapError("A Platform Admin already exists. There can only be one.")
            admin_id = uuid.uuid4()
            now = datetime.now(timezone.utc)
            session.add(
                PlatformAdmin(
                    id=admin_id,
                    email=email,
                    full_name=full_name.strip(),
                    password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
                    status=PlatformAdminStatus.ACTIVE.value,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                AuditEvent(
                    actor_type="SYSTEM",
                    action="PLATFORM_ADMIN_BOOTSTRAPPED",
                    target_type="PLATFORM_ADMIN",
                    target_id=str(admin_id),
                    after={"email": email},
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                # Two simultaneous runs: the singleton constraint keeps only one.
                raise BootstrapError("A Platform Admin already exists. There can only be one.") from None
            return admin_id
    finally:
        await engine.dispose()


def main() -> None:
    database_url = os.environ.get("ADMIN_BOOTSTRAP_DATABASE_URL", "")
    email = os.environ.get("ADMIN_EMAIL", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password and sys.stdin.isatty():
        password = getpass.getpass("Platform Admin password: ")
    if not database_url or not email or not password:
        sys.exit("ADMIN_BOOTSTRAP_DATABASE_URL, ADMIN_EMAIL and a password are required.")
    try:
        asyncio.run(bootstrap(database_url, email, os.environ.get("ADMIN_FULL_NAME", ""), password))
    except BootstrapError as err:
        sys.exit(str(err))
    print("Platform Admin created. Sign in to set up MFA before doing anything else.")


if __name__ == "__main__":
    main()
