from typing import Dict, List, Optional, Set, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.customer import Customer
from app.models.lead import Lead, LeadStatusHistory
from app.repositories.lead_repository import LeadRepository
from app.repositories.customer_repository import CustomerRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.lead import LeadCreate, LeadUpdate, LeadStatusTransitionRequest
from app.services.followup_service import cancel_open_followups_for_lead

# Leads in these states are closed; a new enquiry may open a fresh lead.
CLOSED_LEAD_STATUSES: Set[str] = {"PURCHASED", "LOST"}

# Valid lead state transition graph
VALID_TRANSITIONS: Dict[str, Set[str]] = {
    "NEW": {"CONTACTED", "LOST"},
    "CONTACTED": {"INTERESTED", "LOST"},
    "INTERESTED": {"TEST_RIDE", "NEGOTIATION", "LOST"},
    "TEST_RIDE": {"NEGOTIATION", "LOST"},
    "NEGOTIATION": {"BOOKED", "LOST"},
    "BOOKED": {"PURCHASED", "LOST"},
    "PURCHASED": set(),  # Terminal state
    "LOST": {"NEW", "CONTACTED"},  # Re-opened leads
}


class LeadService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.lead_repo = LeadRepository(session)
        self.customer_repo = CustomerRepository(session)
        self.vehicle_repo = VehicleRepository(session)
        self.audit_repo = AuditLogRepository(session)

    async def create_lead(
        self,
        req: LeadCreate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Lead:
        # Lock the customer row so concurrent creates for the same customer are serialized;
        # this is what makes the open-lead check below race-free.
        locked = await self.session.execute(
            select(Customer.id).where(Customer.id == req.customer_id).with_for_update()
        )
        if locked.scalar_one_or_none() is None:
            raise NotFoundException("Customer", req.customer_id)

        if req.interested_variant_id:
            variant = await self.vehicle_repo.get_variant_by_id(req.interested_variant_id)
            if not variant:
                raise NotFoundException("VehicleVariant", req.interested_variant_id)

            existing = (
                await self.session.execute(
                    select(Lead.id, Lead.status, Lead.assigned_salesperson_id)
                    .where(
                        Lead.customer_id == req.customer_id,
                        Lead.interested_variant_id == req.interested_variant_id,
                        Lead.status.not_in(CLOSED_LEAD_STATUSES),
                    )
                    .order_by(Lead.id)
                    .limit(1)
                )
            ).first()
            if existing is not None:
                await self.session.rollback()
                raise ConflictException(
                    code="LEAD_ALREADY_OPEN",
                    message=f"Customer already has open lead #{existing.id} ({existing.status}) for this vehicle.",
                    details={
                        "lead_id": existing.id,
                        "status": existing.status,
                        "assigned_salesperson_id": existing.assigned_salesperson_id,
                    },
                )

        lead = Lead(
            customer_id=req.customer_id,
            assigned_salesperson_id=req.assigned_salesperson_id or user_id,
            source=req.source.upper().strip(),
            status="NEW",
            priority=req.priority.upper().strip(),
            interested_variant_id=req.interested_variant_id,
            expected_purchase_date=req.expected_purchase_date,
            next_followup_date=req.next_followup_date,
            notes=req.notes,
        )
        created = await self.lead_repo.create(lead)

        # Initial history
        await self.lead_repo.add_status_history(
            lead_id=created.id,
            from_status="NONE",
            to_status="NEW",
            user_id=user_id,
            reason="Initial lead creation",
        )

        await self.audit_repo.log_action(
            user_id=user_id,
            action="LEAD_CREATED",
            entity_type="LEAD",
            entity_id=created.id,
            new_value={"customer_id": created.customer_id, "status": created.status},
            ip_address=ip_address,
        )
        return await self.lead_repo.get_by_id(created.id)  # type: ignore

    async def get_lead(self, lead_id: int) -> Lead:
        lead = await self.lead_repo.get_by_id(lead_id)
        if not lead:
            raise NotFoundException("Lead", lead_id)
        return lead

    async def list_leads(
        self,
        customer_id: Optional[int] = None,
        assigned_salesperson_id: Optional[int] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        source: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        visible_to_user_id: Optional[int] = None,
    ) -> Tuple[List[Lead], int]:
        skip = (page - 1) * page_size
        return await self.lead_repo.search_leads(
            visible_to_user_id=visible_to_user_id,
            customer_id=customer_id,
            assigned_salesperson_id=assigned_salesperson_id,
            status=status,
            priority=priority,
            source=source,
            search=search,
            skip=skip,
            limit=page_size,
        )

    async def update_lead(
        self,
        lead_id: int,
        req: LeadUpdate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Lead:
        lead = await self.get_lead(lead_id)

        if req.assigned_salesperson_id is not None:
            lead.assigned_salesperson_id = req.assigned_salesperson_id
        if req.source is not None:
            lead.source = req.source.upper().strip()
        if req.priority is not None:
            lead.priority = req.priority.upper().strip()
        if req.interested_variant_id is not None:
            if not await self.vehicle_repo.get_variant_by_id(req.interested_variant_id):
                raise NotFoundException("VehicleVariant", req.interested_variant_id)
            lead.interested_variant_id = req.interested_variant_id
        if req.expected_purchase_date is not None:
            lead.expected_purchase_date = req.expected_purchase_date
        if req.next_followup_date is not None:
            lead.next_followup_date = req.next_followup_date
        if req.notes is not None:
            lead.notes = req.notes

        updated = await self.lead_repo.update(lead)
        await self.audit_repo.log_action(
            user_id=user_id,
            action="LEAD_UPDATED",
            entity_type="LEAD",
            entity_id=lead_id,
            ip_address=ip_address,
        )
        return await self.lead_repo.get_by_id(updated.id)  # type: ignore

    async def transition_status(
        self,
        lead_id: int,
        req: LeadStatusTransitionRequest,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Lead:
        target_status = req.to_status.upper().strip()

        # Row lock: concurrent transitions on the same lead queue up here and each one
        # re-validates against the status committed by the previous one.
        current_status = (
            await self.session.execute(select(Lead.status).where(Lead.id == lead_id).with_for_update())
        ).scalar_one_or_none()
        if current_status is None:
            raise NotFoundException("Lead", lead_id)

        if req.from_status and req.from_status.upper().strip() != current_status:
            await self.session.rollback()
            raise ConflictException(
                code="LEAD_STATE_CHANGED",
                message=(
                    f"Lead #{lead_id} is now '{current_status}', not '{req.from_status}'. "
                    "It was changed by someone else; refresh and try again."
                ),
                details={"current_status": current_status},
            )

        if current_status == target_status:
            await self.session.rollback()
            return await self._fresh_lead(lead_id)

        allowed_targets = VALID_TRANSITIONS.get(current_status, set())
        if target_status not in allowed_targets:
            await self.session.rollback()
            raise BadRequestException(
                code="INVALID_STATE_TRANSITION",
                message=f"Cannot transition lead from '{current_status}' to '{target_status}'. Allowed transitions from '{current_status}' are: {sorted(allowed_targets)}.",
            )

        lead = await self.session.get(Lead, lead_id)
        lead.status = target_status  # type: ignore[union-attr]
        self.session.add(
            LeadStatusHistory(
                lead_id=lead_id,
                from_status=current_status,
                to_status=target_status,
                changed_by_user_id=user_id,
                reason=req.reason,
            )
        )
        if target_status in CLOSED_LEAD_STATUSES:
            await cancel_open_followups_for_lead(
                self.session, lead_id, f"Lead closed as {target_status}"
            )
        # Status, history row and any follow-up cancellations commit together, releasing the lock.
        await self.session.commit()

        await self.audit_repo.log_action(
            user_id=user_id,
            action="LEAD_STATUS_TRANSITION",
            entity_type="LEAD",
            entity_id=lead_id,
            old_value={"status": current_status},
            new_value={"status": target_status, "reason": req.reason},
            ip_address=ip_address,
        )
        return await self._fresh_lead(lead_id)

    async def _fresh_lead(self, lead_id: int) -> Lead:
        """Reload a lead bypassing the session identity map so status_history is current."""
        self.session.expire_all()
        lead = await self.lead_repo.get_by_id(lead_id)
        if not lead:
            raise NotFoundException("Lead", lead_id)
        return lead

    async def delete_lead(
        self,
        lead_id: int,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> None:
        lead = await self.get_lead(lead_id)
        await self.lead_repo.delete(lead)
        await self.audit_repo.log_action(user_id, "LEAD_DELETED", "LEAD", lead_id, ip_address=ip_address)
