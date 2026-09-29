from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User, Role
from app.repositories.base_repository import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession):
        super().__init__(User, session)

    async def get_by_email(self, email: str) -> Optional[User]:
        stmt = (
            select(User)
            .where(User.email == email.lower().strip())
            .options(selectinload(User.role))
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_role_by_name(self, name: str) -> Optional[Role]:
        stmt = select(Role).where(Role.name == name.upper().strip())
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_all_roles(self) -> List[Role]:
        stmt = select(Role).order_by(Role.id.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_role(self, name: str, description: Optional[str] = None) -> Role:
        role = Role(name=name.upper().strip(), description=description)
        self.session.add(role)
        await self.session.commit()
        await self.session.refresh(role)
        return role

    async def update_last_login(self, user: User) -> None:
        user.last_login_at = datetime.now(timezone.utc)
        await self.session.commit()
