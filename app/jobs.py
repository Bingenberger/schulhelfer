"""Hintergrundaufgaben, die länger dauern als ein HTTP-Request.

Der Bestätigungsversand beim Veröffentlichen betrifft alle Teilnehmer einer
Veranstaltung. Bei 150 Anmeldungen sind das 150 SMTP-Zustellungen mit
Drosselpause – deutlich mehr, als ein Reverse Proxy an Antwortzeit zulässt.
Der Versand läuft deshalb nach der Antwort weiter; den Fortschritt liest das
Dashboard aus ``email_log``.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import session_factory
from app.email_service import send_confirmation
from app.models import Booking, Donation, EmailLog, Event, Participant, Shift
from app.security import generate_token, hash_token

log = logging.getLogger(__name__)


def ensure_token(db: Session, participant: Participant) -> str:
    """Das bestehende Token zurückgeben – oder eines nachziehen.

    Teilnehmer aus der Zeit vor dem Klartext-Feld haben nur den Hash. Für sie
    lässt sich der alte Link nicht rekonstruieren; sie bekommen einmalig ein
    neues Token. Alle anderen behalten ihren Link.
    """
    if participant.token:
        return participant.token
    token = generate_token()
    participant.token = token
    participant.token_hash = hash_token(token)
    db.commit()
    return token


def rotate_token(db: Session, participant: Participant) -> str:
    """Neues Token erzeugen und das alte entwerten (Link-Wiederherstellung)."""
    token = generate_token()
    participant.token = token
    participant.token_hash = hash_token(token)
    db.commit()
    return token


def _participants_for_event(db: Session, event_id: int) -> list[Participant]:
    return list(
        db.execute(
            select(Participant)
            .where(Participant.event_id == event_id)
            .options(
                selectinload(Participant.event),
                selectinload(Participant.bookings)
                .selectinload(Booking.shift)
                .selectinload(Shift.station),
                selectinload(Participant.donations).selectinload(Donation.station),
            )
        )
        .scalars()
        .all()
    )


async def send_confirmations(event_id: int, only_unsent: bool = False) -> None:
    """Bestätigungsmails für eine Veranstaltung versenden.

    ``only_unsent`` überspringt alle Teilnehmer, für die bereits eine
    erfolgreiche Bestätigung protokolliert ist – das ist der Wiederholungslauf
    für fehlgeschlagene Zustellungen.
    """
    db = session_factory()
    try:
        participants = _participants_for_event(db, event_id)
        if only_unsent:
            already = _successful_confirmation_ids(db, event_id)
            participants = [p for p in participants if p.id not in already]

        log.info("Bestätigungsversand für Event %d: %d Mails", event_id, len(participants))
        for participant in participants:
            token = ensure_token(db, participant)
            try:
                await send_confirmation(db, participant, token)
            except Exception:
                log.exception("Bestätigung an %s fehlgeschlagen", participant.email)
            await asyncio.sleep(settings.SMTP_SEND_DELAY_MS / 1000)
        log.info("Bestätigungsversand für Event %d abgeschlossen", event_id)
    except Exception:
        log.exception("Bestätigungsversand für Event %d abgebrochen", event_id)
    finally:
        db.close()


def _successful_confirmation_ids(db: Session, event_id: int) -> set[int]:
    rows = db.execute(
        select(EmailLog.participant_id)
        .join(Participant, Participant.id == EmailLog.participant_id)
        .where(
            Participant.event_id == event_id,
            EmailLog.kind == "confirmation",
            EmailLog.success.is_(True),
        )
    ).scalars().all()
    return {pid for pid in rows if pid is not None}


def confirmation_progress(db: Session, event: Event) -> dict | None:
    """Versandstand für das Dashboard – ``None``, solange nichts versandt wurde."""
    total = db.execute(
        select(Participant.id).where(Participant.event_id == event.id)
    ).scalars().all()
    if not total:
        return None

    logs = db.execute(
        select(EmailLog.participant_id, EmailLog.success)
        .join(Participant, Participant.id == EmailLog.participant_id)
        .where(Participant.event_id == event.id, EmailLog.kind == "confirmation")
    ).all()
    if not logs:
        return None

    sent: set[int] = set()
    failed: set[int] = set()
    for participant_id, success in logs:
        if participant_id is None:
            continue
        if success:
            sent.add(participant_id)
            failed.discard(participant_id)
        elif participant_id not in sent:
            failed.add(participant_id)

    return {
        "total": len(total),
        "sent": len(sent),
        "failed": len(failed),
        "pending": len(total) - len(sent) - len(failed),
    }
