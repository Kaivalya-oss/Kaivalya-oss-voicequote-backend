from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.inventory import InventoryUnit
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.inventory import InventoryUnitCreate, InventoryUnitUpdate, VariantStockSummary


class InventoryService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.inv_repo = InventoryRepository(session)
        self.vehicle_repo = VehicleRepository(session)
        self.audit_repo = AuditLogRepository(session)

    async def create_unit(
        self,
        req: InventoryUnitCreate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> InventoryUnit:
        variant = await self.vehicle_repo.get_variant_by_id(req.variant_id)
        if not variant:
            raise NotFoundException("VehicleVariant", req.variant_id)

        # Check VIN duplicate
        if await self.inv_repo.get_by_vin(req.vin_chassis_number):
            raise ConflictException("VIN_EXISTS", f"Inventory unit with VIN '{req.vin_chassis_number}' already exists.")

        # Check Engine duplicate
        if await self.inv_repo.get_by_engine(req.engine_number):
            raise ConflictException("ENGINE_NO_EXISTS", f"Inventory unit with engine number '{req.engine_number}' already exists.")

        unit = InventoryUnit(
            variant_id=req.variant_id,
            vin_chassis_number=req.vin_chassis_number.strip().upper(),
            engine_number=req.engine_number.strip().upper(),
            color=req.color.strip(),
            status=req.status.upper().strip(),
            branch_name=req.branch_name.strip(),
            arrival_date=req.arrival_date,
            notes=req.notes,
        )
        created = await self.inv_repo.create(unit)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="INVENTORY_UNIT_CREATED",
            entity_type="INVENTORY_UNIT",
            entity_id=created.id,
            new_value={
                "vin": created.vin_chassis_number,
                "variant_id": created.variant_id,
                "status": created.status,
            },
            ip_address=ip_address,
        )
        return await self.inv_repo.get_by_id(created.id)  # type: ignore

    async def get_unit(self, unit_id: int) -> InventoryUnit:
        unit = await self.inv_repo.get_by_id(unit_id)
        if not unit:
            raise NotFoundException("InventoryUnit", unit_id)
        return unit

    async def list_units(
        self,
        variant_id: Optional[int] = None,
        status: Optional[str] = None,
        branch_name: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[InventoryUnit], int]:
        skip = (page - 1) * page_size
        return await self.inv_repo.search_units(
            variant_id=variant_id,
            status=status,
            branch_name=branch_name,
            search=search,
            skip=skip,
            limit=page_size,
        )

    async def reserve_unit(
        self,
        unit_id: int,
        notes: Optional[str] = None,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> InventoryUnit:
        # Atomic lock
        unit = await self.inv_repo.get_by_id_for_update(unit_id)
        if not unit:
            raise NotFoundException("InventoryUnit", unit_id)

        if unit.status != "AVAILABLE":
            raise BadRequestException(
                code="UNIT_NOT_AVAILABLE",
                message=f"Unit #{unit_id} (VIN: {unit.vin_chassis_number}) cannot be reserved because current status is '{unit.status}'. Only 'AVAILABLE' units can be reserved.",
            )

        old_status = unit.status
        unit.status = "RESERVED"
        if notes:
            unit.notes = f"{unit.notes}\n[Reservation]: {notes}" if unit.notes else f"[Reservation]: {notes}"

        updated = await self.inv_repo.update(unit)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="INVENTORY_UNIT_RESERVED",
            entity_type="INVENTORY_UNIT",
            entity_id=unit_id,
            old_value={"status": old_status},
            new_value={"status": unit.status, "notes": notes},
            ip_address=ip_address,
        )
        return updated

    async def release_unit(
        self,
        unit_id: int,
        notes: Optional[str] = None,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> InventoryUnit:
        # Atomic lock
        unit = await self.inv_repo.get_by_id_for_update(unit_id)
        if not unit:
            raise NotFoundException("InventoryUnit", unit_id)

        if unit.status != "RESERVED":
            raise BadRequestException(
                code="UNIT_NOT_RESERVED",
                message=f"Unit #{unit_id} cannot be released because current status is '{unit.status}'. Only 'RESERVED' units can be released.",
            )

        old_status = unit.status
        unit.status = "AVAILABLE"
        if notes:
            unit.notes = f"{unit.notes}\n[Released]: {notes}" if unit.notes else f"[Released]: {notes}"

        updated = await self.inv_repo.update(unit)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="INVENTORY_UNIT_RELEASED",
            entity_type="INVENTORY_UNIT",
            entity_id=unit_id,
            old_value={"status": old_status},
            new_value={"status": unit.status, "notes": notes},
            ip_address=ip_address,
        )
        return updated

    async def update_unit(
        self,
        unit_id: int,
        req: InventoryUnitUpdate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> InventoryUnit:
        unit = await self.inv_repo.get_by_id_for_update(unit_id)
        if not unit:
            raise NotFoundException("InventoryUnit", unit_id)

        old_data = {"status": unit.status, "color": unit.color, "branch": unit.branch_name}

        if req.color is not None:
            unit.color = req.color.strip()
        if req.status is not None:
            unit.status = req.status.upper().strip()
        if req.branch_name is not None:
            unit.branch_name = req.branch_name.strip()
        if req.arrival_date is not None:
            unit.arrival_date = req.arrival_date
        if req.notes is not None:
            unit.notes = req.notes

        updated = await self.inv_repo.update(unit)
        await self.audit_repo.log_action(
            user_id=user_id,
            action="INVENTORY_UNIT_UPDATED",
            entity_type="INVENTORY_UNIT",
            entity_id=unit_id,
            old_value=old_data,
            new_value={"status": updated.status, "color": updated.color, "branch": updated.branch_name},
            ip_address=ip_address,
        )
        return updated

    async def get_variant_stock_summary(self, variant_id: int) -> VariantStockSummary:
        variant = await self.vehicle_repo.get_variant_by_id(variant_id)
        if not variant:
            raise NotFoundException("VehicleVariant", variant_id)

        counts = await self.inv_repo.get_stock_counts_by_variant(variant_id)
        return VariantStockSummary(
            variant_id=variant.id,
            variant_name=variant.name,
            total_units=counts["TOTAL"],
            available_units=counts["AVAILABLE"],
            reserved_units=counts["RESERVED"],
            sold_units=counts["SOLD"],
            service_units=counts["SERVICE"],
            in_transit_units=counts["IN_TRANSIT"],
        )
