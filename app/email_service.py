import logging
import asyncio
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from html import escape

import aiosmtplib
from sqlalchemy.orm import Session

from app.config import settings
from app.models import EmailLog, Participant

log = logging.getLogger(__name__)


def _format_from() -> str:
    """SMTP_FROM RFC-2047-sicher aufbereiten.

    Enthält der Anzeigename Umlaute, kodiert die einfache Header-Zuweisung
    (``msg["From"] = "Café Orga <a@b.de>"``) den GESAMTEN Wert inkl. der
    spitzen Klammern als ein einziges Encoded-Word – die Adresse ist danach
    für Empfänger wie Gmail nicht mehr als solche erkennbar ("Messages
    missing a valid address in From: header"). Deshalb nur den Anzeigenamen
    kodieren, die Adresse bleibt Klartext.
    """
    name, addr = parseaddr(settings.SMTP_FROM)
    if not addr:
        return settings.SMTP_FROM
    return formataddr((str(Header(name, "utf-8")), addr))


async def _send(to_addr: str, subject: str, text_body: str, html_body: str) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _format_from()
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        await asyncio.wait_for(aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            # use_tls (Port 465) und start_tls (Port 587) schließen sich aus –
            # bei implizitem TLS darf kein STARTTLS-Kommando gesendet werden.
            use_tls=settings.SMTP_USE_TLS,
            start_tls=False if settings.SMTP_USE_TLS else settings.SMTP_STARTTLS,
            username=settings.SMTP_USER or None,
            password=settings.SMTP_PASS or None,
            timeout=settings.SMTP_TIMEOUT_S,
        ), timeout=settings.SMTP_TIMEOUT_S)
        return True
    except Exception as exc:
        log.error("E-Mail-Versand fehlgeschlagen (%s); SMTP-Zustand prüfen", type(exc).__name__)
        return False


def _log_email(db: Session, participant: Participant, kind: str, success: bool):
    # SMTP awaits may outlive a SQLite read snapshot. Record delivery in a fresh
    # write transaction, never upgrade the stale reader after the network await.
    from app.database import session_factory
    with session_factory() as writer:
        writer.add(EmailLog(participant_id=participant.id, kind=kind, success=success))
        writer.commit()


# ─── Zusammenfassung der Buchungen ────────────────────────────────────────────

def _summary_lines(participant: Participant) -> tuple[list[str], list[str]]:
    """Schichten und Spenden als reine Textzeilen (ohne Aufzählungszeichen)."""
    bookings = []
    for booking in sorted(participant.bookings, key=lambda b: b.shift.start_time):
        shift = booking.shift
        bookings.append(
            f"{shift.station.name}: {shift.start_time.strftime('%d.%m.%Y %H:%M')} Uhr "
            f"({shift.duration_min} Min.)"
        )

    donations = []
    for don in participant.donations:
        donations.append(f"{don.station.name}: {don.item_text} (×{don.quantity})")

    return bookings, donations


def _summary_text(bookings: list[str], donations: list[str]) -> str:
    parts = []
    if bookings:
        parts.append("Ihre Schichten:\n" + "\n".join(f"  • {line}" for line in bookings))
    if donations:
        parts.append("Ihre Sachspenden:\n" + "\n".join(f"  • {line}" for line in donations))
    return "\n\n".join(parts) if parts else "Bisher haben Sie nichts eingetragen."


def _summary_html(bookings: list[str], donations: list[str]) -> str:
    parts = []
    if bookings:
        items = "".join(f"<li>{escape(line)}</li>" for line in bookings)
        parts.append(f"<p><strong>Ihre Schichten:</strong></p><ul>{items}</ul>")
    if donations:
        items = "".join(f"<li>{escape(line)}</li>" for line in donations)
        parts.append(f"<p><strong>Ihre Sachspenden:</strong></p><ul>{items}</ul>")
    return "".join(parts) or "<p>Bisher haben Sie nichts eingetragen.</p>"


def _signature_text() -> str:
    return f"Mit freundlichen Grüßen\n{settings.ORGANISATION}"


def _signature_html() -> str:
    return f"<p>Mit freundlichen Grüßen<br>{escape(settings.ORGANISATION)}</p>"


# ─── Mailtypen ────────────────────────────────────────────────────────────────

async def send_access_link(db: Session, participant: Participant, token: str):
    url = f"{settings.BASE_URL}/s/{token}"
    event_name = participant.event.name
    bookings, donations = _summary_lines(participant)

    subject = f"Ihre Anmeldung für {event_name}"
    text = (
        f"Hallo {participant.name},\n\n"
        f"vielen Dank für Ihre Anmeldung zu „{event_name}“!\n\n"
        f"{_summary_text(bookings, donations)}\n\n"
        f"Ihr persönlicher Zugangslink lautet:\n{url}\n\n"
        f"Bitte speichern Sie diesen Link als Lesezeichen. "
        f"Über ihn können Sie Ihre Buchungen jederzeit einsehen, ergänzen und stornieren.\n\n"
        f"{_signature_text()}"
    )
    html = (
        f"<p>Hallo {escape(participant.name)},</p>"
        f"<p>vielen Dank für Ihre Anmeldung zu „{escape(event_name)}“!</p>"
        f"{_summary_html(bookings, donations)}"
        f'<p>Ihr persönlicher Zugangslink: <a href="{escape(url)}">{escape(url)}</a></p>'
        f"<p>Bitte speichern Sie diesen Link als Lesezeichen. Über ihn können Sie Ihre "
        f"Buchungen jederzeit einsehen, ergänzen und stornieren.</p>"
        f"{_signature_html()}"
    )

    success = await _send(participant.email, subject, text, html)
    _log_email(db, participant, "access_link", success)


async def send_recovery_link(db: Session, participant: Participant, token: str):
    url = f"{settings.BASE_URL}/s/{token}"
    subject = "Ihr neuer Zugangslink"
    text = (
        f"Hallo {participant.name},\n\n"
        f"auf Anfrage wurde ein neuer Zugangslink erstellt:\n{url}\n\n"
        f"Der bisherige Link ist damit nicht mehr gültig.\n\n"
        f"{_signature_text()}"
    )
    html = (
        f"<p>Hallo {escape(participant.name)},</p>"
        f'<p>auf Anfrage wurde ein neuer Zugangslink erstellt: <a href="{escape(url)}">{escape(url)}</a></p>'
        f"<p>Der bisherige Link ist damit nicht mehr gültig.</p>"
        f"{_signature_html()}"
    )

    success = await _send(participant.email, subject, text, html)
    _log_email(db, participant, "recovery", success)


async def send_change_notification(db: Session, participant: Participant, token: str):
    """Beleg über eine Änderung, die der Teilnehmer selbst vorgenommen hat."""
    url = f"{settings.BASE_URL}/s/{token}"
    event_name = participant.event.name
    bookings, donations = _summary_lines(participant)

    subject = f"Ihre Anmeldung für {event_name} wurde geändert"
    text = (
        f"Hallo {participant.name},\n\n"
        f"Ihre Anmeldung wurde soeben geändert. Aktueller Stand:\n\n"
        f"{_summary_text(bookings, donations)}\n\n"
        f"Ihr Zugangslink:\n{url}\n\n"
        f"Falls Sie diese Änderung nicht vorgenommen haben, melden Sie sich bitte beim "
        f"Organisationsteam.\n\n"
        f"{_signature_text()}"
    )
    html = (
        f"<p>Hallo {escape(participant.name)},</p>"
        f"<p>Ihre Anmeldung wurde soeben geändert. Aktueller Stand:</p>"
        f"{_summary_html(bookings, donations)}"
        f'<p>Ihr Zugangslink: <a href="{escape(url)}">{escape(url)}</a></p>'
        f"<p>Falls Sie diese Änderung nicht vorgenommen haben, melden Sie sich bitte beim "
        f"Organisationsteam.</p>"
        f"{_signature_html()}"
    )

    success = await _send(participant.email, subject, text, html)
    _log_email(db, participant, "change", success)


async def send_confirmation(db: Session, participant: Participant, token: str):
    url = f"{settings.BASE_URL}/s/{token}"
    event_name = participant.event.name
    bookings, donations = _summary_lines(participant)

    subject = f"Bestätigung Ihrer Helfer-Einteilung – {event_name}"
    text = (
        f"Hallo {participant.name},\n\n"
        f"der Helferplan wurde veröffentlicht. Hier ist Ihre Einteilung:\n\n"
        f"{_summary_text(bookings, donations)}\n\n"
        f"Ihren persönlichen Link finden Sie hier: {url}\n\n"
        f"Herzlichen Dank für Ihre Unterstützung!\n\n"
        f"{_signature_text()}"
    )
    html = (
        f"<p>Hallo {escape(participant.name)},</p>"
        f"<p>der Helferplan wurde veröffentlicht. Hier ist Ihre Einteilung:</p>"
        f"{_summary_html(bookings, donations)}"
        f'<p>Ihr persönlicher Link: <a href="{escape(url)}">{escape(url)}</a></p>'
        f"<p>Herzlichen Dank für Ihre Unterstützung!</p>"
        f"{_signature_html()}"
    )

    success = await _send(participant.email, subject, text, html)
    _log_email(db, participant, "confirmation", success)
    return success


# ─── Hintergrund-Zustellung ───────────────────────────────────────────────────
#
# Mails werden nach der HTTP-Antwort versendet, damit der Nutzer nicht auf den
# SMTP-Server wartet. Diese Funktionen öffnen dafür eine eigene DB-Sitzung – die
# Request-Sitzung ist zu diesem Zeitpunkt bereits geschlossen.

def _load_participant(db: Session, participant_id: int) -> Participant | None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import Booking, Donation, Shift

    return db.execute(
        select(Participant)
        .where(Participant.id == participant_id)
        .options(
            selectinload(Participant.event),
            selectinload(Participant.bookings)
            .selectinload(Booking.shift)
            .selectinload(Shift.station),
            selectinload(Participant.donations).selectinload(Donation.station),
        )
    ).scalar_one_or_none()


async def deliver(kind: str, participant_id: int, token: str) -> None:
    """Eine einzelne Mail im Hintergrund zustellen."""
    from app.database import session_factory

    senders = {
        "access_link": send_access_link,
        "recovery": send_recovery_link,
        "change": send_change_notification,
        "confirmation": send_confirmation,
    }
    sender = senders.get(kind)
    if not sender:
        log.error("Unbekannter Mailtyp: %s", kind)
        return

    db = session_factory()
    try:
        participant = _load_participant(db, participant_id)
        if participant is None:
            log.warning("Teilnehmer %d nicht mehr vorhanden – Mail entfällt", participant_id)
            return
        # Release the eagerly loaded reader before SMTP to avoid pool exhaustion.
        db.close()
        await sender(db, participant, token)
    except Exception as exc:
        log.error("Hintergrund-Mailversand fehlgeschlagen (%s, %d, %s)", kind, participant_id, type(exc).__name__)
    finally:
        db.close()
