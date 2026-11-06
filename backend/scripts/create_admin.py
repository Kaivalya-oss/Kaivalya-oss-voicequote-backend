"""Create the first administrator of a single-showroom database, or reset an administrator's password.

An explicit deployment step, never run at startup. Also creates the staff roles if they are missing.

    ADMIN_EMAIL=... ADMIN_FULL_NAME=... ADMIN_PASSWORD=... python scripts/create_admin.py [--reset-password]

ADMIN_PASSWORD is prompted for when it is not set and a terminal is attached.
"""
import argparse
import asyncio
import getpass
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pydantic import EmailStr, TypeAdapter, ValidationError  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.dev_accounts import DEV_ACCOUNTS, is_refused_dev_credential  # noqa: E402
from app.core.permissions import RoleEnum  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.database.session import AsyncSessionLocal  # noqa: E402
from app.models.user import Role, User  # noqa: E402

MIN_PASSWORD_LENGTH = 12
_PUBLISHED = {account[0] for account in DEV_ACCOUNTS} | {account[3] for account in DEV_ACCOUNTS}


class AdminSetupError(Exception):
    pass


async def create_admin(email: str, full_name: str, password: str, reset_password: bool = False, session_factory=None) -> str:
    """Returns "created" or "password reset". Refuses published accounts in every environment."""
    if settings.MULTI_TENANT_ENABLED:
        raise AdminSetupError("Multi-tenant mode has no shared staff accounts. Owners register their own showroom.")
    try:
        email = TypeAdapter(EmailStr).validate_python(email.strip()).lower()
    except ValidationError:
        raise AdminSetupError("ADMIN_EMAIL is not a valid email address.") from None
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminSetupError(f"The password must have at least {MIN_PASSWORD_LENGTH} characters.")
    if email in _PUBLISHED or password in _PUBLISHED or is_refused_dev_credential(email, password):
        raise AdminSetupError("This email or password is published in the repository and cannot be used.")

    async with (session_factory or AsyncSessionLocal)() as session:
        roles = {role.name: role for role in (await session.execute(select(Role))).scalars()}
        for member in RoleEnum:
            if member.value not in roles:
                roles[member.value] = Role(name=member.value, description=member.value)
                session.add(roles[member.value])
        await session.flush()

        user = (await session.execute(select(User).where(User.email == email))).scalars().first()
        if user and not reset_password:
            raise AdminSetupError("This account already exists. Use --reset-password to change its password.")
        if user:
            user.password_hash = hash_password(password)
            user.role_id = roles[RoleEnum.ADMIN.value].id
            user.is_active = True
            outcome = "password reset"
        else:
            if not full_name.strip():
                raise AdminSetupError("ADMIN_FULL_NAME is required for a new account.")
            session.add(
                User(
                    email=email,
                    full_name=full_name.strip(),
                    password_hash=hash_password(password),
                    role_id=roles[RoleEnum.ADMIN.value].id,
                    is_active=True,
                )
            )
            outcome = "created"
        await session.commit()
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset-password", action="store_true", help="Change the password of an existing account")
    args = parser.parse_args()

    email = os.environ.get("ADMIN_EMAIL", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password and sys.stdin.isatty():
        password = getpass.getpass("New administrator password: ")
    if not email or not password:
        sys.exit("ADMIN_EMAIL and ADMIN_PASSWORD are required.")
    try:
        outcome = asyncio.run(create_admin(email, os.environ.get("ADMIN_FULL_NAME", ""), password, args.reset_password))
    except AdminSetupError as err:
        sys.exit(str(err))
    print(f"Administrator {email}: {outcome}.")


if __name__ == "__main__":
    main()
