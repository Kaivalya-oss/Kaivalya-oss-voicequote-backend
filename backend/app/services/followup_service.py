from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, NotFoundException
from app.models.followup import Followup
from app.repositories.followup_repository import FollowupRepository
from app.repositories.lead_repository import LeadRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.followup import FollowupCreate, FollowupUpdate, FollowupCompleteRequest


class FollowupService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.followup_repo = FollowupRepository(session)
        self.lead_repo = LeadRepository(session)
        self.audit_repo = AuditLogRepository(session)

    async def create_followup(
        self,
        req: FollowupCreate,
        assigned_user_id: int,
        ip_address: Optional[str] = None,
    ) -> Followup:
        lead = await self.lead_repo.get_by_id(req.lead_id)
        if not lead:
            raise NotFoundException("Lead", req.lead_id)

        followup = Followup(
            lead_id=req.lead_id,
            assigned_user_id=assigned_user_id,
            scheduled_at=req.scheduled_at,
            type=req.type.upper().strip(),
            notes=req.notes,
            status="SCHEDULED",
        )
        created = await self.followup_repo.create(followup)

        # Update lead's next follow-up date
        lead.next_followup_date = req.scheduled_at
        await self.lead_repo.update(lead)

        await self.audit_repo.log_action(
            user_id=assigned_user_id,
            action="FOLLOWUP_SCHEDULED",
            entity_type="FOLLOWUP",
            entity_id=created.id,
            new_value={"lead_id": lead.id, "scheduled_at": req.scheduled_at.isoformat()},
            ip_address=ip_address,
        )
        return await self.followup_repo.get_by_id(created.id)  # type: ignore

    async def get_followup(self, followup_id: int) -> Followup:
        f = await self.followup_repo.get_by_id(followup_id)
        if not f:
            raise NotFoundException("Followup", followup_id)
        return f

    async def list_followups(
        self,
        lead_id: Optional[int] = None,
        assigned_user_id: Optional[int] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Followup], int]:
        skip = (page - 1) * page_size
        return await self.followup_repo.search_followups(
            lead_id=lead_id,
            assigned_user_id=assigned_user_id,
            status=status,
            from_date=from_date,
            to_date=to_date,
            skip=skip,
            limit=page_size,
        )

    async def complete_followup(
        self,
        followup_id: int,
        req: FollowupCompleteRequest,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Followup:
        followup = await self.get_followup(followup_id)
        if followup.status == "COMPLETED":
            return followup

        followup.status = "COMPLETED"
        followup.completed_at = datetime.now(timezone.utc)
        if req.notes:
            followup.notes = f"{followup.notes}\n[Completed Outcome]: {req.notes}" if followup.notes else f"[Completed Outcome]: {req.notes}"

        updated = await self.followup_repo.update(followup)

        await self.audit_repo.log_action(
            user_id=user_id,
            action="FOLLOWUP_COMPLETED",
            entity_type="FOLLOWUP",
            entity_id=followup_id,
            new_value={"status": "COMPLETED", "outcome": req.notes},
            ip_address=ip_address,
        )
        return await self.followup_repo.get_by_id(updated.id)  # type: ignore

    async def update_followup(
        self,
        followup_id: int,
        req: FollowupUpdate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Followup:
        followup = await self.get_followup(followup_id)

        if req.scheduled_at is not None:
            followup.scheduled_at = req.scheduled_at
        if req.type is not None:
            followup.type = req.type.upper().strip()
        if req.notes is not None:
            followup.notes = req.notes
        if req.status is not None:
            followup.status = req.status.upper().strip()

        updated = await self.followup_repo.update(followup)
        return await self.followup_repo.get_by_id(updated.id)  # type: ignore
