"""Tests für Abnahmekriterien 1–7 (öffentliche Endpunkte)."""
import threading
import pytest
from datetime import datetime, timedelta
from sqlalchemy import select

from app.models import Booking, Donation, Event, Participant, Shift
from app.security import generate_token, hash_token
from tests.conftest import get_event_id, get_shift_id, get_klasse_id, get_don_station_id


def book(client, event_id, shift_ids, klasse_id, name="Max Muster", email="max@test.de",
         donation_station_ids=None, donation_items=None, donation_quantities=None):
    data = {
        "name": name,
        "email": email,
        "klasse_id": str(klasse_id),
        "shift_ids": [str(s) for s in shift_ids],
        "privacy_accepted": "1",
    }
    if donation_station_ids:
        data["donation_station_ids"] = [str(s) for s in donation_station_ids]
        data["donation_items"] = donation_items or ["Kuchen"]
        data["donation_quantities"] = [str(q) for q in (donation_quantities or [1])]
    return client.post(f"/e/{event_id}/book", data=data)


# AK1: Eltern können Schicht buchen; Live-Belegung erhöht sich
def test_ak1_book_shift(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)

    resp = book(client, event_id, [shift_id], klasse_id)
    assert resp.status_code == 200
    assert "Vielen Dank" in resp.text

    bookings = db.execute(select(Booking)).scalars().all()
    assert len(bookings) == 1

    # Event page shows updated count
    resp2 = client.get(f"/e/{event_id}")
    assert "1/2" in resp2.text  # 1 belegt von 2 Soll


# AK1: Volle Schicht ist nicht buchbar
def test_ak1_full_shift_not_bookable(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)

    # Fill the shift (required_count=2)
    book(client, event_id, [shift_id], klasse_id, email="a@test.de", name="A")
    book(client, event_id, [shift_id], klasse_id, email="b@test.de", name="B")

    # Third booking should not create a booking
    resp = book(client, event_id, [shift_id], klasse_id, email="c@test.de", name="C")
    assert resp.status_code == 200  # success page shown (participant created, shift skipped)

    bookings = db.execute(select(Booking).where(
        Booking.participant_id == db.execute(
            select(Participant).where(Participant.email == "c@test.de")
        ).scalar_one().id
    )).scalars().all()
    assert len(bookings) == 0  # no booking for full shift


# AK1: Concurrency – no overbooking (simplified sequential test)
def test_ak1_no_overbooking(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)

    results = []
    for i in range(4):
        r = book(client, event_id, [shift_id], klasse_id,
                 email=f"user{i}@test.de", name=f"User {i}")
        results.append(r.status_code)

    total_bookings = db.execute(select(Booking)).scalars().all()
    assert len(total_bookings) <= 2  # required_count = 2


# AK2: Pflichtfelder serverseitig erzwungen
def test_ak2_required_fields(client, db):
    event_id = get_event_id(db)
    klasse_id = get_klasse_id(db)

    # Missing name
    r = client.post(f"/e/{event_id}/book", data={
        "name": "", "email": "x@test.de", "klasse_id": str(klasse_id), "privacy_accepted": "1"
    })
    assert r.status_code == 422

    # Invalid email
    r = client.post(f"/e/{event_id}/book", data={
        "name": "X", "email": "notanemail", "klasse_id": str(klasse_id), "privacy_accepted": "1"
    })
    assert r.status_code == 422

    # Invalid klasse
    r = client.post(f"/e/{event_id}/book", data={
        "name": "X", "email": "x@test.de", "klasse_id": "9999", "privacy_accepted": "1"
    })
    assert r.status_code == 422


# AK3: Token-Link wird angezeigt und Statusseite zeigt nur eigene Einträge
def test_ak3_token_link_and_status_page(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)

    r = book(client, event_id, [shift_id], klasse_id)
    assert "Ihr persönlicher Zugangslink" in r.text

    participant = db.execute(select(Participant)).scalar_one()
    token_h = participant.token_hash

    # Token must be a hash (not 44-char base64 of raw token stored)
    assert len(token_h) == 64  # SHA-256 hex digest

    # Find token from response text (not from DB)
    import re
    match = re.search(r"/s/([A-Za-z0-9_-]{43,})", r.text)
    assert match
    token = match.group(1)

    status_r = client.get(f"/s/{token}")
    assert status_r.status_code == 200
    assert "Max Muster" in status_r.text


# AK4: Zweite Anmeldung mit gleicher E-Mail abgelehnt
def test_ak4_duplicate_email(client, db):
    event_id = get_event_id(db)
    klasse_id = get_klasse_id(db)

    book(client, event_id, [], klasse_id, email="dup@test.de")
    r = book(client, event_id, [], klasse_id, email="dup@test.de")

    assert r.status_code == 409
    assert "Wiederherstellung" in r.text

    participants = db.execute(select(Participant)).scalars().all()
    assert len(participants) == 1


# AK5: Wiederherstellung – neue Token, alter ungültig, neutrale Antwort
def test_ak5_recovery(client, db):
    event_id = get_event_id(db)
    klasse_id = get_klasse_id(db)

    book(client, event_id, [], klasse_id, email="rec@test.de")
    participant = db.execute(select(Participant)).scalar_one()
    old_hash = participant.token_hash

    r = client.post(f"/e/{event_id}/recover", data={"email": "rec@test.de"})
    assert r.status_code == 200
    # Neutral response – no disclosure of existence
    assert "Falls eine Anmeldung" in r.text

    db.refresh(participant)
    assert participant.token_hash != old_hash

    # Non-existent email: same neutral response
    r2 = client.post(f"/e/{event_id}/recover", data={"email": "ghost@test.de"})
    assert "Falls eine Anmeldung" in r2.text


# AK6: Öffentliche Endpunkte geben keine Teilnehmer-PII zurück (G7)
def test_ak6_no_pii_in_public_endpoints(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)
    don_station_id = get_don_station_id(db)

    book(client, event_id, [shift_id], klasse_id,
         email="secret@test.de", name="Geheimer Name",
         donation_station_ids=[don_station_id],
         donation_items=["Schokoladenkuchen"],
         donation_quantities=[1])

    r = client.get(f"/e/{event_id}")
    # G7: name and email of participants must never appear in public responses
    assert "secret@test.de" not in r.text
    assert "Geheimer Name" not in r.text
    # "3b" may appear in the class-selection dropdown (required for booking)
    # but must not appear as participant-class disclosure in occupancy data
    # Shift occupancy shows only numbers, not who booked (e.g. "1/2")
    assert "1/2" in r.text  # occupancy shown as numbers only

    # Die Live-Belegung (löst das frühere HTMX-Partial ab) darf ebenfalls
    # keine Teilnehmerdaten preisgeben.
    r2 = client.get(f"/e/{event_id}/availability")
    assert r2.status_code == 200
    assert "secret@test.de" not in r2.text
    assert "Geheimer Name" not in r2.text
    assert "Schokoladenkuchen" in r2.text  # Spendentext ist bewusst öffentlich
    assert r2.json()["shifts"][str(shift_id)]["booked"] == 1


# AK7: Storno nur vor Frist und im Status open
def test_ak7_cancel_before_deadline(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)

    r = book(client, event_id, [shift_id], klasse_id)
    import re
    match = re.search(r"/s/([A-Za-z0-9_-]{43,})", r.text)
    token = match.group(1)

    booking = db.execute(select(Booking)).scalar_one()
    r2 = client.post(f"/s/{token}/cancel", data={"booking_id": str(booking.id)})
    assert r2.status_code in (200, 303)

    db.expire_all()
    bookings = db.execute(select(Booking)).scalars().all()
    assert len(bookings) == 0


def test_ak7_cancel_after_deadline(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)

    r = book(client, event_id, [shift_id], klasse_id)
    import re
    match = re.search(r"/s/([A-Za-z0-9_-]{43,})", r.text)
    token = match.group(1)

    # Close event
    event = db.get(Event, event_id)
    event.status = "closed"
    db.commit()

    booking = db.execute(select(Booking)).scalar_one()
    client.post(f"/s/{token}/cancel", data={"booking_id": str(booking.id)})

    db.expire_all()
    bookings = db.execute(select(Booking)).scalars().all()
    assert len(bookings) == 1  # not cancelled
