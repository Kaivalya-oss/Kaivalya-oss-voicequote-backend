from decimal import Decimal
from typing import List, Optional, Tuple
from sqlalchemy import or_, select, func, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.repositories.base_repository import BaseRepository


class CustomerRepository(BaseRepository[Customer]):
    def __init__(self, session: AsyncSession):
        super().__init__(Customer, session)

    async def get_by_phone(self, phone: str) -> Optional[Customer]:
        stmt = select(Customer).where(Customer.phone == phone.strip())
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def search_customers(
        self,
        search: Optional[str] = None,
        city: Optional[str] = None,
        min_budget: Optional[Decimal] = None,
        max_budget: Optional[Decimal] = None,
        sort_by: str = "id",
        sort_order: str = "desc",
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Customer], int]:
        stmt = select(Customer)
        count_stmt = select(func.count(Customer.id))

        filters = []
        if search:
            search_term = f"%{search.strip()}%"
            filters.append(
                or_(
                    Customer.full_name.ilike(search_term),
                    Customer.phone.ilike(search_term),
                    Customer.email.ilike(search_term),
                )
            )
        if city:
            filters.append(Customer.city.ilike(f"%{city.strip()}%"))
        if min_budget is not None:
            filters.append(Customer.budget >= min_budget)
        if max_budget is not None:
            filters.append(Customer.budget <= max_budget)

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        # Sorting
        sort_col = getattr(Customer, sort_by, Customer.id)
        if sort_order.lower() == "asc":
            stmt = stmt.order_by(asc(sort_col))
        else:
            stmt = stmt.order_by(desc(sort_col))

        # Pagination
        stmt = stmt.offset(skip).limit(limit)

        total = (await self.session.execute(count_stmt)).scalar_one()
        records = (await self.session.execute(stmt)).scalars().all()

        return list(records), total
