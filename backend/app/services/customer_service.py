from decimal import Decimal
from typing import Optional, Tuple, List
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, NotFoundException
from app.models.customer import Customer
from app.repositories.customer_repository import CustomerRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.customer import CustomerCreate, CustomerUpdate


class CustomerService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.customer_repo = CustomerRepository(session)
        self.audit_repo = AuditLogRepository(session)

    async def create_customer(
        self,
        req: CustomerCreate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Customer:
        # Check duplicate phone
        existing = await self.customer_repo.get_by_phone(req.phone)
        if existing:
            raise ConflictException(
                code="CUSTOMER_PHONE_EXISTS",
                message=f"A customer with phone number '{req.phone}' already exists (Customer ID: {existing.id}, Name: '{existing.full_name}').",
            )

        customer = Customer(
            full_name=req.full_name.strip(),
            phone=req.phone.strip(),
            email=req.email.lower().strip() if req.email else None,
            address=req.address,
            city=req.city,
            state=req.state,
            pincode=req.pincode,
            occupation=req.occupation,
            preferred_language=req.preferred_language,
            budget=req.budget,
        )
        created = await self.customer_repo.create(customer)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="CUSTOMER_CREATED",
            entity_type="CUSTOMER",
            entity_id=created.id,
            new_value={"full_name": created.full_name, "phone": created.phone},
            ip_address=ip_address,
        )
        return created

    async def get_customer(self, customer_id: int) -> Customer:
        customer = await self.customer_repo.get_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer", customer_id)
        return customer

    async def list_customers(
        self,
        search: Optional[str] = None,
        city: Optional[str] = None,
        min_budget: Optional[Decimal] = None,
        max_budget: Optional[Decimal] = None,
        sort_by: str = "id",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Customer], int]:
        skip = (page - 1) * page_size
        return await self.customer_repo.search_customers(
            search=search,
            city=city,
            min_budget=min_budget,
            max_budget=max_budget,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=page_size,
        )

    async def update_customer(
        self,
        customer_id: int,
        req: CustomerUpdate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Customer:
        customer = await self.get_customer(customer_id)

        old_data = {
            "full_name": customer.full_name,
            "phone": customer.phone,
            "email": customer.email,
        }

        # Check phone conflict if changing
        if req.phone and req.phone.strip() != customer.phone:
            existing = await self.customer_repo.get_by_phone(req.phone)
            if existing and existing.id != customer.id:
                raise ConflictException(
                    code="CUSTOMER_PHONE_EXISTS",
                    message=f"Phone number '{req.phone}' is already registered to another customer.",
                )
            customer.phone = req.phone.strip()

        if req.full_name is not None:
            customer.full_name = req.full_name.strip()
        if req.email is not None:
            customer.email = req.email.lower().strip() if req.email else None
        if req.address is not None:
            customer.address = req.address
        if req.city is not None:
            customer.city = req.city
        if req.state is not None:
            customer.state = req.state
        if req.pincode is not None:
            customer.pincode = req.pincode
        if req.occupation is not None:
            customer.occupation = req.occupation
        if req.preferred_language is not None:
            customer.preferred_language = req.preferred_language
        if req.budget is not None:
            customer.budget = req.budget

        updated = await self.customer_repo.update(customer)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="CUSTOMER_UPDATED",
            entity_type="CUSTOMER",
            entity_id=updated.id,
            old_value=old_data,
            new_value={"full_name": updated.full_name, "phone": updated.phone, "email": updated.email},
            ip_address=ip_address,
        )
        return updated

    async def delete_customer(
        self,
        customer_id: int,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> None:
        customer = await self.get_customer(customer_id)
        await self.customer_repo.delete(customer)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="CUSTOMER_DELETED",
            entity_type="CUSTOMER",
            entity_id=customer_id,
            old_value={"full_name": customer.full_name, "phone": customer.phone},
            ip_address=ip_address,
        )
