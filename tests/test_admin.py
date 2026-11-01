"""Tests für Abnahmekriterien 8–12 (Admin-Funktionen)."""
import io
import pytest
from sqlalchemy import select

from app.models import Booking, Event, Klasse, Participant, Shift
from app.security import hash_token
from tests.conftest import get_event_id, get_shift_id, get_klasse_id


def book_participant(client, db, email="p@test.de", name="Petra Test"):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)
    client.post(f"/e/{event_id}/book", data={
        "name": name, "email": email,
        "klasse_id": str(klasse_id),
        "shift_ids": [str(shift_id)],
        "privacy_accepted": "1",
    })


# AK8: Dashboard zeigt unterbesetzte Schichten
def test_ak8_dashboard_shows_understaffed(admin_client, db):
    event_id = get_event_id(db)
    r = admin_client.get(f"/admin/events/{event_id}/dashboard")
    assert r.status_code == 200
    assert "unterbesetzt" in r.text  # shift with 0/2 bookings


# AK8: CSV-Export enthält korrekte Daten
def test_ak8_csv_export(admin_client, client, db):
    book_participant(client, db)
    event_id = get_event_id(db)

    r = admin_client.get(f"/admin/events/{event_id}/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "Petra Test" in r.text
    assert "p@test.de" in r.text


# AK8: PDF-Export
def test_ak8_pdf_export(admin_client, db):
    event_id = get_event_id(db)
    r = admin_client.get(f"/admin/events/{event_id}/export.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


# AK9: Veröffentlichung → Bestätigungsmails (email_log entry)
def test_ak9_publish_logs_email(admin_client, client, db):
    book_participant(client, db)
    event_id = get_event_id(db)

    # Close event first
    event = db.get(Event, event_id)
    event.status = "closed"
    db.commit()

    r = admin_client.post(f"/admin/events/{event_id}/publish")
    assert r.status_code in (200, 303)

    from app.models import EmailLog
    logs = db.execute(select(EmailLog).where(EmailLog.kind == "confirmation")).scalars().all()
    assert len(logs) >= 1

    db.expire_all()
    event = db.get(Event, event_id)
    assert event.status == "published"


# AK10: Löschjob entfernt Teilnehmerdaten nach Frist
def test_ak10_retention_cleanup(db):
    from datetime import datetime, timedelta, date
    from app.scheduler import _run_retention_cleanup
    from app.models import Event as EventModel, Klasse, Participant, Booking

    event_id = get_event_id(db)
    event = db.get(EventModel, event_id)
    event.event_date = date.today() - timedelta(days=8)
    event.retention_weeks = 1  # 1 week retention → already expired
    db.commit()

    _run_retention_cleanup()

    db.expire_all()
    participants = db.execute(select(Participant).where(Participant.event_id == event_id)).scalars().all()
    assert len(participants) == 0


# AK11: CSV-Import der Klassenliste
def test_ak11_csv_import_klassen(admin_client, db):
    csv_content = "label,school_year\n5a,2027/28\n5b,2027/28\n3b,2026/27\n"
    r = admin_client.post(
        "/admin/classes/import",
        files={"file": ("klassen.csv", csv_content.encode(), "text/csv")},
    )
    assert r.status_code in (200, 303)

    klassen = db.execute(select(Klasse).where(Klasse.school_year == "2027/28")).scalars().all()
    assert len(klassen) == 2

    # Duplicate (3b, 2026/27) must not be duplicated
    dups = db.execute(
        select(Klasse).where(Klasse.label == "3b", Klasse.school_year == "2026/27")
    ).scalars().all()
    assert len(dups) == 1


# AK12: Token wird nur als Hash gespeichert
def test_ak12_token_hashed_in_db(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)

    r = client.post(f"/e/{event_id}/book", data={
        "name": "Token Test",
        "email": "token@test.de",
        "klasse_id": str(klasse_id),
        "shift_ids": [str(shift_id)],
        "privacy_accepted": "1",
    })

    import re
    match = re.search(r"/s/([A-Za-z0-9_-]{43,})", r.text)
    assert match
    token = match.group(1)

    participant = db.execute(select(Participant)).scalar_one()

    # Token must NOT be stored in plain text
    assert participant.token_hash != token
    # Token hash must be SHA-256 hex (64 chars)
    assert len(participant.token_hash) == 64
    # Computed hash must match
    assert participant.token_hash == hash_token(token)
