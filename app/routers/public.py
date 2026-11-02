import time
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.email_service import deliver
from app.flash import ERROR, SUCCESS, flash
from app.jobs import rotate_token
from app.models import Booking, Donation, Event, Klasse, Participant, Shift, Station
from app.security import generate_token, hash_token
from app.templating import templates
from app.timeutil import now_local

router = APIRouter()

_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60
_RATE_MAX = 5


def _check_rate(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _rate_store[ip] if now - t < _RATE_WINDOW]
    _rate_store[ip] = hits
    if len(hits) >= _RATE_MAX:
        return False
    _rate_store[ip].append(now)
    return True


def _get_event(event_id: int, db: Session) -> Event:
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Veranstaltung nicht gefunden")
    return event


def _klassen(db: Session):
    return (
        db.execute(
            select(Klasse).where(Klasse.active.is_(True)).order_by(Klasse.school_year, Klasse.label)
        )
        .scalars()
        .all()
    )


def _booking_open(event: Event) -> bool:
    """Anmeldung möglich? Frist wird in lokaler Zeit verglichen."""
    if event.status != "open":
        return False
    if event.registration_deadline and now_local() > event.registration_deadline:
        return False
    return True


def _stations_context(event: Event, db: Session) -> list[dict]:
    stations = (
        db.execute(
            select(Station)
            .where(Station.event_id == event.id)
            .order_by(Station.sort_order, Station.id)
            .options(selectinload(Station.shifts).selectinload(Shift.bookings))
            .options(selectinload(Station.donations))
        )
        .scalars()
        .all()
    )

    result = []
    for station in stations:
        if station.type == "helper":
            shifts_data = []
            for shift in sorted(station.shifts, key=lambda s: (s.start_time, s.sort_order)):
                booked = len(shift.bookings)
                shifts_data.append({
                    "id": shift.id,
                    "start_time": shift.start_time,
                    "duration_min": shift.duration_min,
                    "required_count": shift.required_count,
                    "booked_count": booked,
                    "full": booked >= shift.required_count,
                })
            result.append({"station": station, "type": "helper", "shifts": shifts_data})
        else:
            don_items = []
            total_qty = 0
            for don in station.donations:
                don_items.append({"item_text": don.item_text, "quantity": don.quantity})
                total_qty += don.quantity
            result.append({
                "station": station,
                "type": "donation",
                "donations": don_items,
                "total_qty": total_qty,
            })
    return result


# ─── Formularzustand ──────────────────────────────────────────────────────────

def _empty_form() -> dict:
    return {"name": "", "email": "", "klasse_id": None, "shift_ids": set(), "donations": {},
            "privacy_accepted": False}


def _form_state(
    name: str,
    email: str,
    klasse_id: int | None,
    shift_ids: list[int],
    donation_station_ids: list[int],
    donation_items: list[str],
    donation_quantities: list[int],
    privacy_accepted: str,
) -> dict:
    """Die abgeschickten Werte so aufbereiten, dass das Template sie
    wieder in die Felder schreiben kann. Ohne das verliert der Nutzer bei
    jedem Validierungsfehler seine gesamte Eingabe."""
    donations = {}
    for i, station_id in enumerate(donation_station_ids):
        donations[station_id] = {
            "item": donation_items[i] if i < len(donation_items) else "",
            "quantity": donation_quantities[i] if i < len(donation_quantities) else 1,
        }
    return {
        "name": name,
        "email": email,
        "klasse_id": klasse_id,
        "shift_ids": set(shift_ids),
        "donations": donations,
        "privacy_accepted": bool(privacy_accepted),
    }


def _event_page(
    request: Request,
    event: Event,
    db: Session,
    *,
    code_ok: bool = True,
    form: dict | None = None,
    field_errors: dict | None = None,
    error: str | None = None,
    show_recovery: bool = False,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "event/view.html",
        {
            "event": event,
            "stations": _stations_context(event, db) if code_ok else [],
            "klassen": _klassen(db) if code_ok else [],
            "code_ok": code_ok,
            "booking_open": _booking_open(event),
            "deadline_passed": bool(
                event.registration_deadline and now_local() > event.registration_deadline
            ),
            "privacy_url": settings.PRIVACY_NOTICE_URL,
            "base_url": settings.BASE_URL,
            "form": form or _empty_form(),
            "field_errors": field_errors or {},
            "error": error,
            "show_recovery": show_recovery,
            "now": now_local(),
        },
        status_code=status_code,
    )


# ─── Öffentliche Startseite ───────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db, scope="function")):
    """Einstieg für Eltern – nicht der Admin-Login.

    Wer einen abgelaufenen Zugangslink anklickt, landet über die Fehlerseite
    hier und muss eine Veranstaltung oder die Link-Wiederherstellung finden.
    """
    events = (
        db.execute(
            select(Event)
            .where(Event.status.in_(("open", "closed", "published")))
            .order_by(Event.event_date.desc())
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(request, "public/index.html", {
        "events": events,
        "booking_open_map": {e.id: _booking_open(e) for e in events},
    })


# ─── Veranstaltungsseite ──────────────────────────────────────────────────────

@router.get("/e/{event_id}", response_class=HTMLResponse)
async def event_view(request: Request, event_id: int, db: Session = Depends(get_db, scope="function")):
    event = _get_event(event_id, db)
    if event.status not in ("open", "closed", "published"):
        raise HTTPException(status_code=404, detail="Veranstaltung nicht gefunden")

    code_ok = True
    if event.access_code:
        code_ok = request.session.get(f"event_{event_id}_code") == event.access_code

    return _event_page(request, event, db, code_ok=code_ok)


@router.post("/e/{event_id}/code", response_class=HTMLResponse)
async def event_code(
    request: Request,
    event_id: int,
    code: Annotated[str, Form()] = "",
    db: Session = Depends(get_db, scope="function"),
):
    event = _get_event(event_id, db)
    if not event.access_code:
        return RedirectResponse(f"/e/{event_id}", status_code=303)

    # Tippfehler beim Abschreiben aus dem Elternbrief sollen nicht scheitern.
    if code.strip().casefold() == event.access_code.strip().casefold():
        request.session[f"event_{event_id}_code"] = event.access_code
    else:
        flash(
            request,
            "Der Zugangscode stimmt nicht. Sie finden ihn im Elternbrief zur Veranstaltung.",
            ERROR,
        )
    return RedirectResponse(f"/e/{event_id}", status_code=303)


@router.post("/e/{event_id}/book", response_class=HTMLResponse)
async def event_book(
    request: Request,
    event_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    klasse_id: Annotated[int | None, Form()] = None,
    shift_ids: Annotated[list[int], Form()] = [],
    donation_station_ids: Annotated[list[int], Form()] = [],
    donation_items: Annotated[list[str], Form()] = [],
    donation_quantities: Annotated[list[int], Form()] = [],
    privacy_accepted: Annotated[str, Form()] = "",
):
    event = _get_event(event_id, db)
    form = _form_state(
        name, email, klasse_id, shift_ids,
        donation_station_ids, donation_items, donation_quantities, privacy_accepted,
    )

    name = name.strip()
    email = email.strip().lower()

    # Fehler am jeweiligen Feld, damit der Nutzer sieht, wo er nachbessern muss.
    field_errors: dict[str, str] = {}
    general_errors: list[str] = []

    if event.status != "open":
        general_errors.append("Die Anmeldung ist für diese Veranstaltung nicht geöffnet.")
    elif event.registration_deadline and now_local() > event.registration_deadline:
        general_errors.append(
            "Die Anmeldefrist ist am "
            f"{event.registration_deadline.strftime('%d.%m.%Y um %H:%M')} Uhr abgelaufen."
        )

    if not name:
        field_errors["name"] = "Bitte geben Sie Ihren Namen an."
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        field_errors["email"] = "Bitte geben Sie eine gültige E-Mail-Adresse an, z. B. name@beispiel.de."
    if not klasse_id:
        field_errors["klasse_id"] = "Bitte wählen Sie eine Klasse."
    if not privacy_accepted:
        field_errors["privacy_accepted"] = "Bitte bestätigen Sie den Datenschutzhinweis."

    klasse = db.get(Klasse, klasse_id) if klasse_id else None
    if klasse_id and (not klasse or not klasse.active):
        field_errors["klasse_id"] = "Diese Klasse steht nicht (mehr) zur Auswahl."

    # Doppelte Anmeldung: Eingaben bleiben stehen, Wiederherstellung wird angeboten.
    if email:
        existing = db.execute(
            select(Participant).where(
                Participant.event_id == event_id, Participant.email == email
            )
        ).scalar_one_or_none()
        if existing:
            return _event_page(
                request, event, db,
                form=form,
                error=(
                    "Diese E-Mail-Adresse ist für diese Veranstaltung bereits angemeldet. "
                    "Über die Link-Wiederherstellung kommen Sie an Ihre bestehende Anmeldung "
                    "und können sie dort ergänzen."
                ),
                show_recovery=True,
                status_code=409,
            )

    if field_errors or general_errors:
        return _event_page(
            request, event, db,
            form=form,
            field_errors=field_errors,
            error=general_errors[0] if general_errors else None,
            status_code=422,
        )

    token = generate_token()
    participant = Participant(
        event_id=event_id,
        name=name,
        email=email,
        klasse_id=klasse_id,
        token=token,
        token_hash=hash_token(token),
    )
    db.add(participant)
    db.flush()

    booking_errors = []
    for shift_id in shift_ids:
        shift = db.get(Shift, shift_id)
        if not shift or shift.station.event_id != event_id:
            booking_errors.append("Eine gewählte Schicht gehört nicht zu dieser Veranstaltung.")
            continue
        booked = db.execute(
            select(Booking).where(Booking.shift_id == shift_id)
        ).scalars().all()
        if len(booked) >= shift.required_count:
            booking_errors.append(
                f"{shift.station.name}, {shift.start_time.strftime('%d.%m.%Y %H:%M')} Uhr – "
                "diese Schicht wurde inzwischen von jemand anderem belegt."
            )
            continue
        db.add(Booking(participant_id=participant.id, shift_id=shift_id))

    for i, station_id in enumerate(donation_station_ids):
        item = donation_items[i].strip() if i < len(donation_items) else ""
        qty = donation_quantities[i] if i < len(donation_quantities) else 1
        if not item:
            continue
        station = db.get(Station, station_id)
        if not station or station.event_id != event_id or station.type != "donation":
            continue
        db.add(Donation(
            participant_id=participant.id, station_id=station_id,
            item_text=item, quantity=max(1, qty),
        ))

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _event_page(
            request, event, db,
            form=form,
            error="Die Anmeldung konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.",
            status_code=409,
        )

    db.refresh(participant)
    # Der Link steht bereits auf der Erfolgsseite – die Mail ist die Kopie.
    # Sie darf die Antwort deshalb nicht aufhalten.
    background.add_task(deliver, "access_link", participant.id, token)

    return templates.TemplateResponse(request, "event/book_success.html", {
        "event": event,
        "participant": participant,
        "token": token,
        "booking_errors": booking_errors,
        "booking_count": len(participant.bookings),
        "donation_count": len(participant.donations),
        "base_url": settings.BASE_URL,
    })


# ─── Link-Wiederherstellung ───────────────────────────────────────────────────

def _recover_for_email(db: Session, email: str) -> list[tuple[Participant, str]]:
    """Neue Tokens für alle noch laufenden Anmeldungen dieser Adresse."""
    participants = (
        db.execute(
            select(Participant)
            .join(Event, Event.id == Participant.event_id)
            .where(Participant.email == email, Event.status != "archived")
            .options(selectinload(Participant.event))
        )
        .scalars()
        .all()
    )
    return [(p, rotate_token(db, p)) for p in participants]


@router.get("/wiederherstellen", response_class=HTMLResponse)
async def recover_global_get(request: Request):
    """Wiederherstellung ohne Veranstaltungsbezug.

    Ein abgelaufener Zugangslink kennt seine Veranstaltung nicht mehr – von
    der Fehlerseite führt deshalb nur dieser Weg zurück.
    """
    return templates.TemplateResponse(request, "event/recover.html", {"event": None})


@router.post("/wiederherstellen", response_class=HTMLResponse)
async def recover_global_post(
    request: Request,
    background: BackgroundTasks,
    email: Annotated[str, Form()] = "",
    db: Session = Depends(get_db, scope="function"),
):
    ip = request.client.host if request.client else "unknown"
    if _check_rate(ip):
        for participant, token in _recover_for_email(db, email.strip().lower()):
            background.add_task(deliver, "recovery", participant.id, token)

    return templates.TemplateResponse(request, "event/recover.html", {
        "event": None,
        "sent": True,
    })


@router.get("/e/{event_id}/recover", response_class=HTMLResponse)
async def recover_get(request: Request, event_id: int, db: Session = Depends(get_db, scope="function")):
    event = _get_event(event_id, db)
    return templates.TemplateResponse(request, "event/recover.html", {"event": event})


@router.post("/e/{event_id}/recover", response_class=HTMLResponse)
async def recover_post(
    request: Request,
    event_id: int,
    background: BackgroundTasks,
    email: Annotated[str, Form()] = "",
    db: Session = Depends(get_db, scope="function"),
):
    event = _get_event(event_id, db)
    ip = request.client.host if request.client else "unknown"

    if _check_rate(ip):
        email = email.strip().lower()
        participant = db.execute(
            select(Participant).where(
                Participant.event_id == event_id, Participant.email == email
            )
        ).scalar_one_or_none()
        if participant:
            token = rotate_token(db, participant)
            background.add_task(deliver, "recovery", participant.id, token)

    return templates.TemplateResponse(request, "event/recover.html", {
        "event": event,
        "sent": True,
    })


# ─── Persönliche Statusseite ──────────────────────────────────────────────────

def _load_participant(token: str, db: Session) -> Participant | None:
    return db.execute(
        select(Participant)
        .where(Participant.token_hash == hash_token(token))
        .options(
            selectinload(Participant.bookings)
            .selectinload(Booking.shift)
            .selectinload(Shift.station),
            selectinload(Participant.donations).selectinload(Donation.station),
            selectinload(Participant.event),
            selectinload(Participant.klasse),
        )
    ).scalar_one_or_none()


def _invalid_token(request: Request):
    return templates.TemplateResponse(
        request, "status/invalid_token.html", {}, status_code=404
    )


@router.get("/s/{token}", response_class=HTMLResponse)
async def status_view(request: Request, token: str, db: Session = Depends(get_db, scope="function")):
    participant = _load_participant(token, db)
    if not participant:
        return _invalid_token(request)

    event = participant.event
    return templates.TemplateResponse(request, "status/view.html", {
        "participant": participant,
        "event": event,
        "stations": _stations_context(event, db),
        "booking_open": _booking_open(event),
        "deadline_passed": bool(
            event.registration_deadline and now_local() > event.registration_deadline
        ),
        "token": token,
        "privacy_url": settings.PRIVACY_NOTICE_URL,
        "now": now_local(),
    })


@router.post("/s/{token}/add", response_class=HTMLResponse)
async def status_add(
    request: Request,
    token: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    shift_ids: Annotated[list[int], Form()] = [],
    donation_station_ids: Annotated[list[int], Form()] = [],
    donation_items: Annotated[list[str], Form()] = [],
    donation_quantities: Annotated[list[int], Form()] = [],
):
    participant = _load_participant(token, db)
    if not participant:
        return _invalid_token(request)

    event = participant.event
    if not _booking_open(event):
        flash(request, "Die Anmeldefrist ist abgelaufen – Änderungen sind nicht mehr möglich.", ERROR)
        return RedirectResponse(f"/s/{token}", status_code=303)

    added_shifts = 0
    added_donations = 0
    rejected: list[str] = []

    for shift_id in shift_ids:
        shift = db.get(Shift, shift_id)
        if not shift or shift.station.event_id != event.id:
            continue
        existing = db.execute(
            select(Booking).where(
                Booking.participant_id == participant.id, Booking.shift_id == shift_id
            )
        ).scalar_one_or_none()
        if existing:
            continue
        current = db.execute(
            select(Booking).where(Booking.shift_id == shift_id)
        ).scalars().all()
        if len(current) >= shift.required_count:
            rejected.append(
                f"{shift.station.name}, {shift.start_time.strftime('%d.%m. %H:%M')} Uhr (inzwischen voll)"
            )
            continue
        db.add(Booking(participant_id=participant.id, shift_id=shift_id))
        added_shifts += 1

    for i, station_id in enumerate(donation_station_ids):
        item = donation_items[i].strip() if i < len(donation_items) else ""
        qty = donation_quantities[i] if i < len(donation_quantities) else 1
        if not item:
            continue
        station = db.get(Station, station_id)
        if not station or station.event_id != event.id or station.type != "donation":
            continue
        db.add(Donation(
            participant_id=participant.id, station_id=station_id,
            item_text=item, quantity=max(1, qty),
        ))
        added_donations += 1

    db.commit()

    parts = []
    if added_shifts:
        parts.append(f"{added_shifts} Schicht{'en' if added_shifts != 1 else ''}")
    if added_donations:
        parts.append(f"{added_donations} Sachspende{'n' if added_donations != 1 else ''}")
    if parts:
        flash(request, f"{' und '.join(parts)} hinzugefügt.", SUCCESS)
        background.add_task(deliver, "change", participant.id, token)
    elif not rejected:
        flash(request, "Es wurde nichts ausgewählt – es hat sich nichts geändert.", "info")

    for text in rejected:
        flash(request, f"Nicht übernommen: {text}", ERROR)

    return RedirectResponse(f"/s/{token}", status_code=303)


@router.post("/s/{token}/cancel", response_class=HTMLResponse)
async def status_cancel(
    request: Request,
    token: str,
    background: BackgroundTasks,
    booking_id: Annotated[int | None, Form()] = None,
    donation_id: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db, scope="function"),
):
    participant = _load_participant(token, db)
    if not participant:
        return _invalid_token(request)

    if not _booking_open(participant.event):
        flash(request, "Die Anmeldefrist ist abgelaufen – Änderungen sind nicht mehr möglich.", ERROR)
        return RedirectResponse(f"/s/{token}", status_code=303)

    changed = False
    if booking_id:
        booking = db.get(Booking, booking_id)
        if booking and booking.participant_id == participant.id:
            label = (
                f"{booking.shift.station.name}, "
                f"{booking.shift.start_time.strftime('%d.%m. %H:%M')} Uhr"
            )
            db.delete(booking)
            changed = True
            flash(request, f"Schicht storniert: {label}.", SUCCESS)
    if donation_id:
        donation = db.get(Donation, donation_id)
        if donation and donation.participant_id == participant.id:
            label = donation.item_text
            db.delete(donation)
            changed = True
            flash(request, f"Sachspende storniert: {label}.", SUCCESS)

    db.commit()
    if changed:
        background.add_task(deliver, "change", participant.id, token)

    return RedirectResponse(f"/s/{token}", status_code=303)


@router.post("/s/{token}/donation/{donation_id}", response_class=HTMLResponse)
async def status_donation_edit(
    request: Request,
    token: str,
    donation_id: int,
    background: BackgroundTasks,
    item_text: Annotated[str, Form()] = "",
    quantity: Annotated[int, Form()] = 1,
    db: Session = Depends(get_db, scope="function"),
):
    """Zusage korrigieren, statt sie löschen und neu anlegen zu müssen."""
    participant = _load_participant(token, db)
    if not participant:
        return _invalid_token(request)

    if not _booking_open(participant.event):
        flash(request, "Die Anmeldefrist ist abgelaufen – Änderungen sind nicht mehr möglich.", ERROR)
        return RedirectResponse(f"/s/{token}", status_code=303)

    donation = db.get(Donation, donation_id)
    if not donation or donation.participant_id != participant.id:
        return RedirectResponse(f"/s/{token}", status_code=303)

    item_text = item_text.strip()
    if not item_text:
        flash(request, "Bitte geben Sie an, was Sie mitbringen möchten.", ERROR)
        return RedirectResponse(f"/s/{token}", status_code=303)

    donation.item_text = item_text
    donation.quantity = max(1, quantity)
    db.commit()

    flash(request, f"Sachspende aktualisiert: {item_text} (×{donation.quantity}).", SUCCESS)
    background.add_task(deliver, "change", participant.id, token)
    return RedirectResponse(f"/s/{token}", status_code=303)


# ─── Live-Belegung ────────────────────────────────────────────────────────────

@router.get("/e/{event_id}/availability")
async def availability(event_id: int, db: Session = Depends(get_db, scope="function")):
    """Aktuelle Belegung als JSON.

    Die Seite aktualisiert damit Zählerstände, Fortschrittsbalken und volle
    Schichten, ohne das Formular neu zu rendern – eine bereits getroffene
    Auswahl bleibt dadurch erhalten.
    """
    event = _get_event(event_id, db)

    shifts: dict[str, dict] = {}
    donations: dict[str, dict] = {}
    for entry in _stations_context(event, db):
        if entry["type"] == "helper":
            for shift in entry["shifts"]:
                shifts[str(shift["id"])] = {
                    "booked": shift["booked_count"],
                    "required": shift["required_count"],
                    "full": shift["full"],
                }
        else:
            donations[str(entry["station"].id)] = {
                "total": entry["total_qty"],
                "target": entry["station"].target_count,
                "items": [
                    {"text": d["item_text"], "quantity": d["quantity"]}
                    for d in entry["donations"]
                ],
            }

    return JSONResponse({
        "shifts": shifts,
        "donations": donations,
        "updated": now_local().strftime("%H:%M"),
        "booking_open": _booking_open(event),
    })
