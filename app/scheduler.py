import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import delete, select

from app.database import session_factory
from app.platform import data_lock, schema_ready
from app.models import Booking, Donation, Event, Participant
from app.timeutil import now_local

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None
_scheduler_lock = None


def _run_retention_cleanup():
    if not schema_ready():
        return
    db = session_factory()
    try:
        now = now_local()
        events = db.execute(select(Event)).scalars().all()
        for event in events:
            if event.event_date is None:
                continue
            cutoff = datetime.combine(event.event_date, datetime.min.time()) + timedelta(
                weeks=event.retention_weeks
            )
            if now >= cutoff:
                participant_ids = db.execute(
                    select(Participant.id).where(Participant.event_id == event.id)
                ).scalars().all()
                if participant_ids:
                    db.execute(delete(Booking).where(Booking.participant_id.in_(participant_ids)))
                    db.execute(delete(Donation).where(Donation.participant_id.in_(participant_ids)))
                    db.execute(delete(Participant).where(Participant.event_id == event.id))
                    db.commit()
                    log.info("Teilnehmerdaten für Event %d gelöscht (Frist abgelaufen)", event.id)
    except Exception:
        log.error("Retention-Cleanup fehlgeschlagen; private Datenbank prüfen")
        db.rollback()
    finally:
        db.close()


def start_scheduler():
    global _scheduler, _scheduler_lock
    if _scheduler is not None:
        return
    _scheduler_lock = data_lock("scheduler")
    _scheduler_lock.__enter__()
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(_run_retention_cleanup, "interval", hours=24, id="retention_cleanup", max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Scheduler gestartet")


def stop_scheduler():
    global _scheduler, _scheduler_lock
    if _scheduler:
        _scheduler.shutdown(wait=True)
        _scheduler = None
    if _scheduler_lock:
        _scheduler_lock.__exit__(None, None, None)
        _scheduler_lock = None
