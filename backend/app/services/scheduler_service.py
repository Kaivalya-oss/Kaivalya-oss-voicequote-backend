import asyncio
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import logger
from app.database.session import AsyncSessionLocal
from app.models.followup import Followup
from app.models.notification import Notification

scheduler = AsyncIOScheduler()


async def _for_each_database(job: str, run) -> None:
    """Run a job on the application database, or on every active showroom's own database."""
    if not settings.MULTI_TENANT_ENABLED:
        try:
            async with AsyncSessionLocal() as session:
                await run(session)
        except Exception as e:
            logger.error(f"Error executing {job} job: {e}")
        return

    from app.control_plane.models import ConnectionStatus, Tenant, TenantConnection, TenantStatus
    from app.database.connection_manager import connection_manager
    from app.database.control_plane import control_sessionmaker

    async with control_sessionmaker()() as control:
        rows = (
            await control.execute(
                select(TenantConnection.tenant_id, TenantConnection.secret_reference)
                .join(Tenant, Tenant.id == TenantConnection.tenant_id)
                .where(
                    Tenant.status == TenantStatus.ACTIVE.value,
                    TenantConnection.connection_status == ConnectionStatus.ACTIVE.value,
                )
            )
        ).all()

    # Bounded, so background jobs cannot use up the connection budget meant for requests.
    limit = asyncio.Semaphore(settings.TENANT_DB_SCHEDULER_CONCURRENCY)

    async def one(tenant_id, reference) -> None:
        async with limit:
            try:
                async with connection_manager.session(tenant_id, reference) as session:
                    await run(session)
            except Exception as e:
                # One showroom's outage must not affect the others. Class name only: no customer data in logs.
                logger.error(f"{job} job failed for tenant {tenant_id}: {type(e).__name__}")

    await asyncio.gather(*(one(t, r) for t, r in rows))


async def check_upcoming_followups():
    """
    Background job: checks followups scheduled within the next 2 hours
    and inserts notification records for sales executives if not already notified.
    """
    now = datetime.now(timezone.utc)
    lookahead = now + timedelta(hours=2)

    async def run(session):
        stmt = select(Followup).where(
            Followup.status == "SCHEDULED",
            Followup.scheduled_at >= now - timedelta(minutes=15),
            Followup.scheduled_at <= lookahead,
        )
        result = await session.execute(stmt)
        followups = result.scalars().all()

        for f in followups:
            # Check if notification already logged for this followup
            notif_stmt = select(Notification).where(
                Notification.user_id == f.assigned_user_id,
                Notification.reference_id == f.id,
                Notification.reference_type == "FOLLOWUP",
            )
            existing = (await session.execute(notif_stmt)).scalars().first()
            if not existing:
                notif = Notification(
                    user_id=f.assigned_user_id,
                    title="Upcoming Follow-up Due",
                    message=f"You have a {f.type} follow-up scheduled for {f.scheduled_at.strftime('%H:%M %d-%b')}.",
                    type="FOLLOWUP_ALERT",
                    reference_id=f.id,
                    reference_type="FOLLOWUP",
                )
                session.add(notif)
        await session.commit()

    await _for_each_database("check_upcoming_followups", run)


async def expire_quotations_job():
    """Background job: move open quotations past their expiry date to EXPIRED."""
    from app.services.quotation_service import expire_overdue_quotations

    async def run(session):
        count = await expire_overdue_quotations(session)
        if count:
            logger.info(f"Expired {count} overdue quotation(s).")

    await _for_each_database("expire_quotations", run)


def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            check_upcoming_followups,
            "interval",
            minutes=10,
            id="check_followups_job",
            replace_existing=True,
        )
        scheduler.add_job(
            expire_quotations_job,
            "interval",
            minutes=30,
            id="expire_quotations_job",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
        )
        try:
            scheduler.start()
            logger.info("APScheduler background scheduler started.")
        except Exception as e:
            logger.warning(f"Could not start background scheduler: {e}")


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler background scheduler stopped.")
