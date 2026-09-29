from datetime import datetime
from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.test_ride import TestRide
from app.repositories.test_ride_repository import TestRideRepository
from app.repositories.customer_repository import CustomerRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.test_ride import TestRideCompleteRequest, TestRideCreate, TestRideUpdate


class TestRideService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.ride_repo = TestRideRepository(session)
        self.customer_repo = CustomerRepository(session)
        self.vehicle_repo = VehicleRepository(session)
        self.audit_repo = AuditLogRepository(session)

    async def book_test_ride(
        self,
        req: TestRideCreate,
        sales_executive_id: int,
        ip_address: Optional[str] = None,
    ) -> TestRide:
        customer = await self.customer_repo.get_by_id(req.customer_id)
        if not customer:
            raise NotFoundException("Customer", req.customer_id)

        variant = await self.vehicle_repo.get_variant_by_id(req.variant_id)
        if not variant:
            raise NotFoundException("VehicleVariant", req.variant_id)

        if variant.status != "ACTIVE":
            raise BadRequestException("VARIANT_INACTIVE", f"Vehicle variant '{variant.name}' is not active for test rides.")

        # Check double-booking collision
        collision = await self.ride_repo.check_collision(
            variant_id=req.variant_id,
            scheduled_at=req.scheduled_at,
            duration_minutes=req.duration_minutes,
        )
        if collision:
            raise ConflictException(
                code="TEST_RIDE_SLOT_COLLISION",
                message=f"A test ride for vehicle '{variant.name}' is already scheduled within the requested time window around {req.scheduled_at.isoformat()}. Please select an alternate slot.",
            )

        ride = TestRide(
            customer_id=req.customer_id,
            variant_id=req.variant_id,
            sales_executive_id=sales_executive_id,
            scheduled_at=req.scheduled_at,
            duration_minutes=req.duration_minutes,
            status="REQUESTED",
            driving_license_number=req.driving_license_number,
            notes=req.notes,
        )
        created = await self.ride_repo.create(ride)

        await self.audit_repo.log_action(
            user_id=sales_executive_id,
            action="TEST_RIDE_BOOKED",
            entity_type="TEST_RIDE",
            entity_id=created.id,
            new_value={
                "customer_id": created.customer_id,
                "variant_id": created.variant_id,
                "scheduled_at": created.scheduled_at.isoformat(),
            },
            ip_address=ip_address,
        )
        return await self.ride_repo.get_by_id(created.id)  # type: ignore

    async def get_test_ride(self, ride_id: int) -> TestRide:
        ride = await self.ride_repo.get_by_id(ride_id)
        if not ride:
            raise NotFoundException("TestRide", ride_id)
        return ride

    async def list_test_rides(
        self,
        customer_id: Optional[int] = None,
        variant_id: Optional[int] = None,
        sales_executive_id: Optional[int] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[TestRide], int]:
        skip = (page - 1) * page_size
        return await self.ride_repo.search_test_rides(
            customer_id=customer_id,
            variant_id=variant_id,
            sales_executive_id=sales_executive_id,
            status=status,
            from_date=from_date,
            to_date=to_date,
            skip=skip,
            limit=page_size,
        )

    async def confirm_test_ride(
        self,
        ride_id: int,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> TestRide:
        ride = await self.get_test_ride(ride_id)
        if ride.status not in ["REQUESTED"]:
            raise BadRequestException("INVALID_STATUS", f"Only REQUESTED test rides can be confirmed (current status: {ride.status}).")

        ride.status = "CONFIRMED"
        updated = await self.ride_repo.update(ride)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="TEST_RIDE_CONFIRMED",
            entity_type="TEST_RIDE",
            entity_id=ride_id,
            ip_address=ip_address,
        )
        return await self.ride_repo.get_by_id(updated.id)  # type: ignore

    async def complete_test_ride(
        self,
        ride_id: int,
        req: TestRideCompleteRequest,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> TestRide:
        ride = await self.get_test_ride(ride_id)
        if ride.status not in ["CONFIRMED", "REQUESTED"]:
            raise BadRequestException("INVALID_STATUS", f"Cannot complete test ride with status '{ride.status}'.")

        ride.status = "COMPLETED"
        ride.feedback_rating = req.feedback_rating
        if req.notes:
            ride.notes = f"{ride.notes}\n[Completed Feedback]: {req.notes}" if ride.notes else f"[Completed Feedback]: {req.notes}"

        updated = await self.ride_repo.update(ride)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="TEST_RIDE_COMPLETED",
            entity_type="TEST_RIDE",
            entity_id=ride_id,
            new_value={"rating": req.feedback_rating, "notes": req.notes},
            ip_address=ip_address,
        )
        return await self.ride_repo.get_by_id(updated.id)  # type: ignore

    async def cancel_test_ride(
        self,
        ride_id: int,
        reason: Optional[str] = None,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> TestRide:
        ride = await self.get_test_ride(ride_id)
        if ride.status in ["COMPLETED", "CANCELLED"]:
            raise BadRequestException("INVALID_STATUS", f"Cannot cancel already {ride.status} test ride.")

        ride.status = "CANCELLED"
        if reason:
            ride.notes = f"{ride.notes}\n[Cancelled]: {reason}" if ride.notes else f"[Cancelled]: {reason}"

        updated = await self.ride_repo.update(ride)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="TEST_RIDE_CANCELLED",
            entity_type="TEST_RIDE",
            entity_id=ride_id,
            new_value={"reason": reason},
            ip_address=ip_address,
        )
        return await self.ride_repo.get_by_id(updated.id)  # type: ignore
