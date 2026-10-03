from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, NotFoundException
from app.models.followup import Followup
from app.models.lead import Lead
from app.repositories.followup_repository import FollowupRepository
from app.repositories.lead_repository import LeadRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.followup import FollowupCancelRequest, FollowupCreate, FollowupUpdate, FollowupCompleteRequest

# Lead stages in which no new follow-ups may be scheduled.
CLOSED_LEAD_STATUSES = {"PURCHASED", "LOST"}
# Small tolerance so a follow-up scheduled "now" from a slightly skewed client clock is accepted.
PAST_TOLERANCE = timedelta(minutes=5)


async def sync_lead_next_followup(session: AsyncSession, lead_id: int) -> None:
    """Set lead.next_followup_date to its earliest still-SCHEDULED follow-up (or None).

    Called after every create / reschedule / complete / cancel so the lead never points at a
    follow-up that has moved or no longer exists. Does not commit.
    """
    await session.flush()  # autoflush is off: make pending reschedules/status changes visible
    earliest = (
        await session.execute(
            select(func.min(Followup.scheduled_at)).where(
                Followup.lead_id == lead_id, Followup.status == "SCHEDULED"
            )
        )
    ).scalar_one_or_none()
    lead = await session.get(Lead, lead_id)
    if lead is not None:
        lead.next_followup_date = earliest


async def cancel_open_followups_for_lead(session: AsyncSession, lead_id: int, reason: str) -> int:
    """Cancel every SCHEDULED follow-up of a lead that has been closed. Does not commit."""
    rows = (
        await session.execute(
            select(Followup).where(Followup.lead_id == lead_id, Followup.status == "SCHEDULED")
        )
    ).scalars().all()
    for f in rows:
        f.status = "CANCELLED"
        f.notes = f"{f.notes}\n[Cancelled]: {reason}" if f.notes else f"[Cancelled]: {reason}"
    await sync_lead_next_followup(session, lead_id)
    return len(rows)


def _ensure_not_past(scheduled_at: datetime) -> None:
    if scheduled_at < datetime.now(timezone.utc) - PAST_TOLERANCE:
        raise BadRequestException(
            "FOLLOWUP_IN_PAST",
            "A follow-up cannot be scheduled in the past. Choose a future date and time.",
        )


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
        if lead.status in CLOSED_LEAD_STATUSES:
            raise BadRequestException(
                "LEAD_CLOSED",
                f"Lead #{lead.id} is {lead.status}; follow-ups cannot be scheduled on a closed lead. Reopen it first.",
            )
        _ensure_not_past(req.scheduled_at)

        followup = Followup(
            lead_id=req.lead_id,
            assigned_user_id=assigned_user_id,
            scheduled_at=req.scheduled_at,
            type=req.type.upper().strip(),
            notes=req.notes,
            status="SCHEDULED",
        )
        self.session.add(followup)
        await self.session.flush()
        await sync_lead_next_followup(self.session, lead.id)
        await self.session.commit()

        await self.audit_repo.log_action(
            user_id=assigned_user_id,
            action="FOLLOWUP_SCHEDULED",
            entity_type="FOLLOWUP",
            entity_id=followup.id,
            new_value={"lead_id": lead.id, "scheduled_at": req.scheduled_at.isoformat()},
            ip_address=ip_address,
        )
        return await self._fresh(followup.id)

    async def get_followup(self, followup_id: int) -> Followup:
        f = await self.followup_repo.get_by_id(followup_id)
        if not f:
            raise NotFoundException("Followup", followup_id)
        return f

    async def _fresh(self, followup_id: int) -> Followup:
        self.session.expire_all()
        return await self.get_followup(followup_id)

    async def list_followups(
        self,
        lead_id: Optional[int] = None,
        assigned_user_id: Optional[int] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
        visible_to_user_id: Optional[int] = None,
    ) -> Tuple[List[Followup], int]:
        skip = (page - 1) * page_size
        return await self.followup_repo.search_followups(
            visible_to_user_id=visible_to_user_id,
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
            return followup  # idempotent: a repeated click does not re-complete or duplicate notes
        if followup.status == "CANCELLED":
            raise BadRequestException("FOLLOWUP_CANCELLED", f"Follow-up #{followup_id} was cancelled and cannot be completed.")

        followup.status = "COMPLETED"
        followup.completed_at = datetime.now(timezone.utc)
        if req.notes:
            followup.notes = f"{followup.notes}\n[Completed Outcome]: {req.notes}" if followup.notes else f"[Completed Outcome]: {req.notes}"
        await sync_lead_next_followup(self.session, followup.lead_id)
        await self.session.commit()

        await self.audit_repo.log_action(
            user_id=user_id,
            action="FOLLOWUP_COMPLETED",
            entity_type="FOLLOWUP",
            entity_id=followup_id,
            new_value={"status": "COMPLETED", "outcome": req.notes},
            ip_address=ip_address,
        )
        return await self._fresh(followup_id)

    async def cancel_followup(
        self,
        followup_id: int,
        req: FollowupCancelRequest,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Followup:
        followup = await self.get_followup(followup_id)
        if followup.status == "CANCELLED":
            return followup
        if followup.status == "COMPLETED":
            raise BadRequestException("FOLLOWUP_COMPLETED", f"Follow-up #{followup_id} is already completed.")

        followup.status = "CANCELLED"
        if req.reason:
            followup.notes = f"{followup.notes}\n[Cancelled]: {req.reason}" if followup.notes else f"[Cancelled]: {req.reason}"
        await sync_lead_next_followup(self.session, followup.lead_id)
        await self.session.commit()

        await self.audit_repo.log_action(
            user_id=user_id,
            action="FOLLOWUP_CANCELLED",
            entity_type="FOLLOWUP",
            entity_id=followup_id,
            new_value={"status": "CANCELLED", "reason": req.reason},
            ip_address=ip_address,
        )
        return await self._fresh(followup_id)

    async def update_followup(
        self,
        followup_id: int,
        req: FollowupUpdate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> Followup:
        """Reschedule / edit an open follow-up. Status changes go through /complete and /cancel."""
        followup = await self.get_followup(followup_id)
        if req.status is not None and req.status.upper().strip() != followup.status:
            raise BadRequestException(
                "USE_STATUS_ENDPOINT",
                "Follow-up status cannot be changed here. Use POST /followups/{id}/complete or /cancel.",
            )
        if followup.status != "SCHEDULED":
            raise BadRequestException(
                "FOLLOWUP_CLOSED",
                f"Follow-up #{followup_id} is {followup.status} and can no longer be edited.",
            )

        if req.scheduled_at is not None:
            _ensure_not_past(req.scheduled_at)
            followup.scheduled_at = req.scheduled_at
        if req.type is not None:
            followup.type = req.type.upper().strip()
        if req.notes is not None:
            followup.notes = req.notes

        await sync_lead_next_followup(self.session, followup.lead_id)
        await self.session.commit()

        await self.audit_repo.log_action(
            user_id=user_id,
            action="FOLLOWUP_UPDATED",
            entity_type="FOLLOWUP",
            entity_id=followup_id,
            new_value={"scheduled_at": followup.scheduled_at.isoformat(), "type": followup.type},
            ip_address=ip_address,
        )
        return await self._fresh(followup_id)
