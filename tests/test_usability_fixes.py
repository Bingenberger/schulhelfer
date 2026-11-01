"""Regressionstests zu den behobenen Usability-Befunden.

Jeder Test benennt den Befund, den er absichert.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Booking, Donation, EmailLog, Event, Klasse, Participant, Shift, Station
from app.timeutil import now_local
from tests.conftest import (
    TestingSessionLocal, csrf_token, get_don_station_id, get_event_id,
    get_klasse_id, get_shift_id,
)


def book(client, db, email="t@test.de", name="Test Eltern", shifts=None):
    event_id = get_event_id(db)
    return client.post(f"/e/{event_id}/book", data={
        "name": name,
        "email": email,
        "klasse_id": str(get_klasse_id(db)),
        "shift_ids": [str(s) for s in (shifts if shifts is not None else [get_shift_id(db)])],
        "privacy_accepted": "1",
    })


def token_of(db, email="t@test.de") -> str:
    participant = db.execute(
        select(Participant).where(Participant.email == email)
    ).scalar_one()
    db.refresh(participant)
    return participant.token


# ─── Befund 01: Storno im Dashboard ───────────────────────────────────────────

def test_admin_cancel_booking_works(admin_client, client, db):
    """event_id gehört ins Formular – vorher antwortete die Route mit 422."""
    book(client, db)
    event_id = get_event_id(db)
    booking = db.execute(select(Booking)).scalar_one()

    r = admin_client.post(
        f"/admin/bookings/{booking.id}/delete",
        data={"event_id": str(event_id)},
        follow_redirects=True,
    )

    assert r.status_code == 200
    assert db.execute(select(Booking)).scalars().all() == []
    assert "storniert" in r.text


def test_dashboard_cancel_form_carries_event_id(admin_client, client, db):
    """Das Template muss event_id als Feld mitschicken, nicht als Query."""
    book(client, db)
    event_id = get_event_id(db)
    r = admin_client.get(f"/admin/events/{event_id}/dashboard")
    assert '<input type="hidden" name="event_id"' in r.text
    assert "/delete?event_id=" not in r.text


# ─── Befund 02: Sackgasse bei ungültigem Link ─────────────────────────────────

def test_invalid_token_page_links_to_recovery(client):
    r = client.get("/s/gibtsnicht")
    assert r.status_code == 404
    assert "/wiederherstellen" in r.text


def test_root_is_public_not_admin_login(client, db):
    """"/" führte zuvor auf den Admin-Login."""
    r = client.get("/", follow_redirects=True)
    assert r.status_code == 200
    assert "Helfer-Anmeldung" in r.text
    assert "Passwort" not in r.text


def test_global_recovery_without_event(client, db):
    book(client, db, email="global@test.de")
    old = token_of(db, "global@test.de")

    r = client.post("/wiederherstellen", data={"email": "global@test.de"})
    assert r.status_code == 200
    assert "Falls eine Anmeldung" in r.text

    participant = db.execute(
        select(Participant).where(Participant.email == "global@test.de")
    ).scalar_one()
    db.refresh(participant)
    assert participant.token != old


# ─── Befund 03: Veröffentlichen entwertet Links nicht mehr ────────────────────

def test_publish_keeps_existing_link(admin_client, client, db):
    book(client, db, email="keep@test.de")
    token = token_of(db, "keep@test.de")
    event_id = get_event_id(db)

    event = db.get(Event, event_id)
    event.status = "closed"
    db.commit()

    admin_client.post(f"/admin/events/{event_id}/publish", follow_redirects=True)

    # Das gespeicherte Lesezeichen funktioniert weiterhin.
    assert client.get(f"/s/{token}").status_code == 200

    participant = db.execute(
        select(Participant).where(Participant.email == "keep@test.de")
    ).scalar_one()
    db.refresh(participant)
    assert participant.token == token


def test_publish_sends_confirmations_in_background(admin_client, client, db):
    book(client, db, email="conf@test.de")
    event_id = get_event_id(db)
    event = db.get(Event, event_id)
    event.status = "closed"
    db.commit()

    admin_client.post(f"/admin/events/{event_id}/publish", follow_redirects=True)

    logs = db.execute(
        select(EmailLog).where(EmailLog.kind == "confirmation")
    ).scalars().all()
    assert len(logs) == 1


# ─── Befund 04: Falscher Zugangscode ──────────────────────────────────────────

def test_wrong_access_code_reports_error(client, db):
    event = db.get(Event, get_event_id(db))
    event.access_code = "Sommer26"
    db.commit()

    r = client.post(f"/e/{event.id}/code", data={"code": "falsch"}, follow_redirects=True)
    assert "Zugangscode stimmt nicht" in r.text

    # Groß-/Kleinschreibung darf nicht scheitern lassen.
    r2 = client.post(f"/e/{event.id}/code", data={"code": "  sommer26 "}, follow_redirects=True)
    assert "Zugangscode stimmt nicht" not in r2.text
    assert "Zugangscode erforderlich" not in r2.text


# ─── Befund 05: Klasse mit Anmeldungen löschen ────────────────────────────────

def test_delete_used_class_is_refused_not_crashing(admin_client, client, db):
    book(client, db, email="klasse@test.de")
    klasse_id = get_klasse_id(db)

    r = admin_client.post(
        f"/admin/classes/{klasse_id}/delete",
        token_from="/admin/classes",
        follow_redirects=True,
    )

    assert r.status_code == 200
    assert "kann nicht gelöscht werden" in r.text
    assert db.get(Klasse, klasse_id) is not None


# ─── Befund 06: Formular bleibt nach Fehler befüllt ───────────────────────────

def test_form_values_survive_validation_error(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    klasse_id = get_klasse_id(db)

    r = client.post(f"/e/{event_id}/book", data={
        "name": "Anna Beispiel",
        "email": "keine-mail",
        "klasse_id": str(klasse_id),
        "shift_ids": [str(shift_id)],
        # Datenschutz-Haken fehlt absichtlich
    })

    assert r.status_code == 422
    assert 'value="Anna Beispiel"' in r.text
    assert 'value="keine-mail"' in r.text
    assert f'value="{klasse_id}" selected' in r.text
    # Die Schichtauswahl darf nicht verlorengehen.
    assert f'value="{shift_id}"' in r.text and "checked" in r.text
    assert "Datenschutzhinweis" in r.text


def test_duplicate_email_keeps_form_and_offers_recovery(client, db):
    book(client, db, email="dup2@test.de", name="Erste Anmeldung")
    event_id = get_event_id(db)

    r = client.post(f"/e/{event_id}/book", data={
        "name": "Zweite Anmeldung",
        "email": "dup2@test.de",
        "klasse_id": str(get_klasse_id(db)),
        "privacy_accepted": "1",
    })

    assert r.status_code == 409
    assert 'value="Zweite Anmeldung"' in r.text
    assert f"/e/{event_id}/recover" in r.text


# ─── Befund 07: Teilerfolg ehrlich benennen ───────────────────────────────────

def test_partial_booking_is_labelled_as_such(client, db):
    event_id = get_event_id(db)
    shift = db.execute(select(Shift)).scalar_one()
    shift.required_count = 1
    db.commit()

    book(client, db, email="erste@test.de")
    r = book(client, db, email="zweite@test.de")

    assert "Anmeldung teilweise übernommen" in r.text
    assert "Vielen Dank" not in r.text


def test_full_booking_says_thank_you(client, db):
    r = book(client, db, email="voll@test.de")
    assert "Vielen Dank" in r.text
    assert "teilweise" not in r.text


# ─── Befund 08/09/10: Schichtzeile ────────────────────────────────────────────

def test_shift_row_is_a_label_with_for(client, db):
    event_id = get_event_id(db)
    shift_id = get_shift_id(db)
    r = client.get(f"/e/{event_id}")
    assert f'<label class="shift-label" for="e-shift-{shift_id}">' in r.text


def test_station_appears_only_once(client, db):
    """Belegung und Auswahl standen zuvor getrennt auf der Seite."""
    event_id = get_event_id(db)
    r = client.get(f"/e/{event_id}")
    assert r.text.count('id="station-') == len(
        db.execute(select(Station)).scalars().all()
    )


def test_availability_endpoint_has_no_pii(client, db):
    event_id = get_event_id(db)
    book(client, db, email="geheim@test.de", name="Geheime Person")

    r = client.get(f"/e/{event_id}/availability")
    assert r.status_code == 200
    payload = r.json()
    assert "geheim@test.de" not in r.text
    assert "Geheime Person" not in r.text
    assert payload["shifts"][str(get_shift_id(db))]["booked"] == 1


# ─── Befund 11/12/15: Mails ───────────────────────────────────────────────────

def test_booking_response_does_not_wait_for_smtp(client, db):
    """Die Anmeldung wird gespeichert, auch wenn der Mailversand scheitert."""
    r = book(client, db, email="async@test.de")
    assert r.status_code == 200
    assert db.execute(
        select(Participant).where(Participant.email == "async@test.de")
    ).scalar_one_or_none() is not None


def test_change_notification_is_logged(client, db):
    book(client, db, email="change@test.de")
    token = token_of(db, "change@test.de")
    booking = db.execute(select(Booking)).scalar_one()

    client.post(f"/s/{token}/cancel", data={"booking_id": str(booking.id)},
                follow_redirects=True)

    logs = db.execute(select(EmailLog).where(EmailLog.kind == "change")).scalars().all()
    assert len(logs) == 1


def test_access_mail_summary_escapes_html(db):
    from app.email_service import _summary_html

    html = _summary_html(["Kuchen & <b>mehr</b>"], [])
    assert "&lt;b&gt;" in html
    assert "<b>mehr</b>" not in html


# ─── Befund 14: Spende korrigieren ────────────────────────────────────────────

def test_donation_can_be_edited(client, db):
    event_id = get_event_id(db)
    don_station_id = get_don_station_id(db)
    client.post(f"/e/{event_id}/book", data={
        "name": "Spender", "email": "spende@test.de",
        "klasse_id": str(get_klasse_id(db)),
        "privacy_accepted": "1",
        "donation_station_ids": [str(don_station_id)],
        "donation_items": ["Marmorkuchen"],
        "donation_quantities": ["1"],
    })
    token = token_of(db, "spende@test.de")
    donation = db.execute(select(Donation)).scalar_one()

    r = client.post(f"/s/{token}/donation/{donation.id}",
                    data={"item_text": "Obstkuchen", "quantity": "3"},
                    follow_redirects=True)

    assert r.status_code == 200
    db.refresh(donation)
    assert donation.item_text == "Obstkuchen"
    assert donation.quantity == 3


# ─── Befund 16/21: Rückmeldungen im Admin ─────────────────────────────────────

def test_admin_actions_confirm_themselves(admin_client, db):
    event_id = get_event_id(db)
    r = admin_client.post(
        f"/admin/events/{event_id}/stations/new",
        data={"station_type": "helper", "name": "Waffelstand", "sort_order": "5"},
        follow_redirects=True,
    )
    assert "Waffelstand" in r.text
    assert "angelegt" in r.text


def test_csv_import_reports_counts(admin_client, db):
    csv = "label,school_year\n4a,2026/27\n4b,2026/27\n,2026/27\n3b,2026/27\n"
    r = admin_client.post(
        "/admin/classes/import",
        token_from="/admin/classes",
        files={"file": ("klassen.csv", csv.encode(), "text/csv")},
        follow_redirects=True,
    )
    assert "2 Klassen importiert" in r.text
    assert "1 bereits vorhanden" in r.text
    assert "1 Zeile unvollständig übersprungen" in r.text


def test_csv_import_reports_wrong_columns(admin_client, db):
    csv = "klasse;jahrgang\n4a;2026/27\n"
    r = admin_client.post(
        "/admin/classes/import",
        token_from="/admin/classes",
        files={"file": ("falsch.csv", csv.encode(), "text/csv")},
        follow_redirects=True,
    )
    assert "nicht die erwarteten Spalten" in r.text


# ─── Befund 17/18/22: Admin-Bedienbarkeit ─────────────────────────────────────

def test_shift_can_be_edited_without_losing_bookings(admin_client, client, db):
    book(client, db, email="edit@test.de")
    shift = db.execute(select(Shift)).scalar_one()
    new_start = (now_local() + timedelta(days=20)).replace(second=0, microsecond=0)

    admin_client.post(f"/admin/shifts/{shift.id}/edit", data={
        "start_time": new_start.strftime("%Y-%m-%dT%H:%M"),
        "duration_min": "90",
        "required_count": "3",
        "sort_order": "0",
    }, token_from=f"/admin/events/{get_event_id(db)}", follow_redirects=True)

    db.refresh(shift)
    assert shift.duration_min == 90
    assert shift.required_count == 3
    assert len(db.execute(select(Booking)).scalars().all()) == 1


def test_shift_series_creates_all_shifts(admin_client, db):
    station = db.execute(
        select(Station).where(Station.type == "helper")
    ).scalar_one()
    before = len(station.shifts)

    r = admin_client.post(f"/admin/stations/{station.id}/shifts/series", data={
        "series_date": "2026-09-12",
        "series_from": "10:00",
        "series_to": "18:00",
        "duration_min": "60",
        "required_count": "2",
    }, token_from=f"/admin/events/{get_event_id(db)}", follow_redirects=True)

    db.refresh(station)
    assert len(station.shifts) == before + 8
    assert "8 Schichten angelegt" in r.text


def test_manual_booking_respects_event_scope(admin_client, client, db):
    book(client, db, email="manuell@test.de")
    event_id = get_event_id(db)
    participant = db.execute(select(Participant)).scalar_one()
    booking = db.execute(select(Booking)).scalar_one()
    db.delete(booking)
    db.commit()

    r = admin_client.post(f"/admin/events/{event_id}/bookings/add", data={
        "participant_id": str(participant.id),
        "shift_id": str(get_shift_id(db)),
    }, token_from=f"/admin/events/{event_id}/dashboard", follow_redirects=True)

    assert "eingetragen" in r.text
    assert len(db.execute(select(Booking)).scalars().all()) == 1


# ─── Befund 20: Anmeldefrist in Ortszeit ──────────────────────────────────────

def test_deadline_uses_local_time(client, db):
    """Eine Frist, die vor einer Stunde ablief, muss auch abgelaufen sein.

    Zuvor wurde lokale Wandzeit gegen UTC verglichen, wodurch die Frist im
    Sommer zwei Stunden zu spät griff.
    """
    event = db.get(Event, get_event_id(db))
    event.registration_deadline = now_local() - timedelta(hours=1)
    db.commit()

    r = client.get(f"/e/{event.id}")
    assert "Anmeldefrist ist am" in r.text

    booked = book(client, db, email="spaet@test.de")
    assert booked.status_code == 422
    assert db.execute(
        select(Participant).where(Participant.email == "spaet@test.de")
    ).scalar_one_or_none() is None


def test_deadline_in_future_still_allows_booking(client, db):
    event = db.get(Event, get_event_id(db))
    event.registration_deadline = now_local() + timedelta(hours=1)
    db.commit()

    r = book(client, db, email="rechtzeitig@test.de")
    assert r.status_code == 200


# ─── Befund 23–26: Barrierefreiheit ───────────────────────────────────────────

def test_progress_bars_expose_aria(client, db):
    r = client.get(f"/e/{get_event_id(db)}")
    assert 'role="progressbar"' in r.text
    assert "aria-valuenow=" in r.text
    assert "aria-valuemax=" in r.text


def test_donation_inputs_have_labels(client, db):
    station_id = get_don_station_id(db)
    r = client.get(f"/e/{get_event_id(db)}")
    assert f'for="e-don-item-{station_id}"' in r.text
    assert f'id="e-don-item-{station_id}"' in r.text


def test_admin_tables_scroll(admin_client, db):
    r = admin_client.get(f"/admin/events/{get_event_id(db)}/dashboard")
    assert 'class="table-scroll"' in r.text


def test_live_region_announces_updates(client, db):
    r = client.get(f"/e/{get_event_id(db)}")
    assert 'aria-live="polite"' in r.text


# ─── Befund 28/29: CSRF und Dark Mode ─────────────────────────────────────────

def test_admin_post_without_csrf_is_rejected(client, db):
    client.post("/admin/login", data={"username": "admin", "password": "admin123"})
    r = client.post(f"/admin/events/{get_event_id(db)}/stations/new",
                    data={"station_type": "helper", "name": "Ohne Token"})
    assert r.status_code == 400
    assert db.execute(
        select(Station).where(Station.name == "Ohne Token")
    ).scalar_one_or_none() is None


def test_csrf_failure_renders_html_not_json(client, db):
    client.post("/admin/login", data={"username": "admin", "password": "admin123"})
    r = client.post(f"/admin/events/{get_event_id(db)}/stations/new",
                    data={"station_type": "helper", "name": "X"})
    assert "text/html" in r.headers["content-type"]
    assert "Sicherheitstoken" in r.text


def test_no_hardcoded_light_theme(client, db):
    r = client.get(f"/e/{get_event_id(db)}")
    assert 'data-theme="light"' not in r.text
    assert 'name="color-scheme" content="light dark"' in r.text
