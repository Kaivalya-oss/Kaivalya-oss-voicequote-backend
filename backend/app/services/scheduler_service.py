from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.logging import logger
from app.database.session import AsyncSessionLocal
from app.models.followup import Followup
from app.models.notification import Notification

scheduler = AsyncIOScheduler()


async def check_upcoming_followups():
    """
    Background job: checks followups scheduled within the next 2 hours
    and inserts notification records for sales executives if not already notified.
    """
    now = datetime.now(timezone.utc)
    lookahead = now + timedelta(hours=2)

    try:
        async with AsyncSessionLocal() as session:
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
    except Exception as e:
        logger.error(f"Error executing check_upcoming_followups job: {e}")


def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            check_upcoming_followups,
            "interval",
            minutes=10,
            id="check_followups_job",
            replace_existing=True,
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
