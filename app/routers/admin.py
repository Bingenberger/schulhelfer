import csv
import io
import time
from collections import defaultdict
from datetime import timedelta
from typing import Annotated

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.dependencies import require_admin, require_csrf
from app.flash import ERROR, INFO, SUCCESS, WARNING, flash
from app.jobs import confirmation_progress, send_confirmations
from app.models import (
    AdminUser, Booking, Donation, Event, Klasse, Participant, Shift, Station,
)
from app.security import hash_password, verify_password
from app.templating import templates
from app.timeutil import now_local, parse_date, parse_local

router = APIRouter(prefix="/admin")

_login_rate: dict[str, list[float]] = defaultdict(list)

# Jede zustandsändernde Route bekommt zusätzlich die CSRF-Prüfung.
CSRF = Depends(require_csrf)


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _check_login_rate(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _login_rate[ip] if now - t < 60]
    _login_rate[ip] = hits
    if len(hits) >= 10:
        return False
    _login_rate[ip].append(now)
    return True


def _get_event(event_id: int, db: Session) -> Event:
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "Veranstaltung nicht gefunden")
    return event


# ─── Login / Logout ───────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if request.session.get("admin_id"):
        return _redirect("/admin")
    return templates.TemplateResponse(request, "admin/login.html", {})


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(ip):
        return templates.TemplateResponse(request, "admin/login.html", {
            "error": "Zu viele Anmeldeversuche. Bitte warten Sie eine Minute.",
        }, status_code=429)

    admin = db.execute(
        select(AdminUser).where(AdminUser.username == username.strip())
    ).scalar_one_or_none()

    if not admin or not verify_password(password, admin.password_hash):
        return templates.TemplateResponse(request, "admin/login.html", {
            "error": "Benutzername oder Passwort ist falsch.",
            "username": username,
        }, status_code=401)

    request.session["admin_id"] = admin.id
    return _redirect("/admin")


@router.post("/logout")
async def logout(request: Request, _csrf: None = CSRF):
    request.session.clear()
    return _redirect("/admin/login")


# ─── Event-Liste ──────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def event_list(
    request: Request,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    events = db.execute(select(Event).order_by(Event.event_date.desc())).scalars().all()
    counts = dict(
        db.execute(
            select(Participant.event_id, func.count(Participant.id)).group_by(Participant.event_id)
        ).all()
    )
    return templates.TemplateResponse(request, "admin/events/list.html", {
        "events": events,
        "participant_counts": counts,
        "admin": admin,
    })


# ─── Event CRUD ───────────────────────────────────────────────────────────────

@router.get("/events/new", response_class=HTMLResponse)
async def event_new_get(request: Request, admin: AdminUser = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/events/edit.html", {
        "admin": admin,
        "event": None,
        "form": {},
    })


def _event_form_values(
    name: str, event_date: str, location: str, description: str,
    registration_deadline: str, access_code: str, retention_weeks: int,
) -> dict:
    return {
        "name": name, "event_date": event_date, "location": location,
        "description": description, "registration_deadline": registration_deadline,
        "access_code": access_code, "retention_weeks": retention_weeks,
    }


def _validate_event(name: str, event_date: str, registration_deadline: str) -> dict:
    errors: dict[str, str] = {}
    if not name.strip():
        errors["name"] = "Bitte geben Sie einen Namen an."
    parsed_date = parse_date(event_date)
    if not parsed_date:
        errors["event_date"] = "Bitte geben Sie ein gültiges Datum an."
    if registration_deadline.strip():
        deadline = parse_local(registration_deadline)
        if not deadline:
            errors["registration_deadline"] = "Bitte geben Sie eine gültige Frist an."
        elif parsed_date and deadline.date() > parsed_date:
            errors["registration_deadline"] = (
                "Die Anmeldefrist liegt nach der Veranstaltung."
            )
    return errors


@router.post("/events/new", response_class=HTMLResponse)
async def event_new_post(
    request: Request,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    name: Annotated[str, Form()] = "",
    event_date: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    registration_deadline: Annotated[str, Form()] = "",
    access_code: Annotated[str, Form()] = "",
    retention_weeks: Annotated[int, Form()] = 4,
):
    errors = _validate_event(name, event_date, registration_deadline)
    if errors:
        return templates.TemplateResponse(request, "admin/events/edit.html", {
            "admin": admin,
            "event": None,
            "form": _event_form_values(
                name, event_date, location, description,
                registration_deadline, access_code, retention_weeks,
            ),
            "field_errors": errors,
        }, status_code=422)

    event = Event(
        name=name.strip(),
        event_date=parse_date(event_date),
        location=location.strip() or None,
        description=description.strip() or None,
        registration_deadline=parse_local(registration_deadline),
        access_code=access_code.strip() or None,
        retention_weeks=retention_weeks,
    )
    db.add(event)
    db.commit()
    flash(request, f"Veranstaltung „{event.name}“ angelegt.")
    return _redirect(f"/admin/events/{event.id}")


@router.get("/events/{event_id}", response_class=HTMLResponse)
async def event_detail(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    event = db.execute(
        select(Event)
        .where(Event.id == event_id)
        .options(
            selectinload(Event.stations)
            .selectinload(Station.shifts)
            .selectinload(Shift.bookings)
        )
        .options(selectinload(Event.stations).selectinload(Station.donations))
    ).scalar_one_or_none()
    if not event:
        raise HTTPException(404)

    # Vorlagen für „Aufbau übernehmen“
    other_events = (
        db.execute(
            select(Event)
            .where(Event.id != event_id)
            .order_by(Event.event_date.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(request, "admin/events/detail.html", {
        "event": event,
        "admin": admin,
        "other_events": [e for e in other_events if e.stations],
        "default_date": (event.event_date or now_local().date()).strftime("%Y-%m-%d"),
    })


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
async def event_edit_get(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    event = _get_event(event_id, db)
    return templates.TemplateResponse(request, "admin/events/edit.html", {
        "event": event,
        "admin": admin,
        "form": {},
    })


@router.post("/events/{event_id}/edit", response_class=HTMLResponse)
async def event_edit_post(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    name: Annotated[str, Form()] = "",
    event_date: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    registration_deadline: Annotated[str, Form()] = "",
    access_code: Annotated[str, Form()] = "",
    retention_weeks: Annotated[int, Form()] = 4,
):
    event = _get_event(event_id, db)
    errors = _validate_event(name, event_date, registration_deadline)
    if errors:
        return templates.TemplateResponse(request, "admin/events/edit.html", {
            "admin": admin,
            "event": event,
            "form": _event_form_values(
                name, event_date, location, description,
                registration_deadline, access_code, retention_weeks,
            ),
            "field_errors": errors,
        }, status_code=422)

    event.name = name.strip()
    event.event_date = parse_date(event_date) or event.event_date
    event.location = location.strip() or None
    event.description = description.strip() or None
    event.access_code = access_code.strip() or None
    event.retention_weeks = retention_weeks
    event.registration_deadline = parse_local(registration_deadline)
    db.commit()
    flash(request, "Änderungen gespeichert.")
    return _redirect(f"/admin/events/{event_id}")


@router.post("/events/{event_id}/delete")
async def event_delete(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
):
    event = _get_event(event_id, db)
    name = event.name
    db.delete(event)
    db.commit()
    flash(request, f"Veranstaltung „{name}“ und alle zugehörigen Daten wurden gelöscht.", WARNING)
    return _redirect("/admin")


_TRANSITIONS = {
    "draft": ["open"],
    "open": ["closed"],
    "closed": ["published", "open"],
    "published": ["archived"],
    "archived": [],
}

_TRANSITION_MESSAGE = {
    "open": "Die Anmeldung ist jetzt geöffnet.",
    "closed": "Die Anmeldung ist geschlossen. Es sind keine neuen Eintragungen mehr möglich.",
    "published": "Der Helferplan ist veröffentlicht.",
    "archived": "Die Veranstaltung wurde archiviert.",
}


@router.post("/events/{event_id}/status")
async def event_status(
    request: Request,
    event_id: int,
    background: BackgroundTasks,
    new_status: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
):
    event = _get_event(event_id, db)

    if new_status not in _TRANSITIONS.get(event.status, []):
        flash(request, "Dieser Statuswechsel ist nicht möglich.", ERROR)
        return _redirect(f"/admin/events/{event_id}")

    if new_status == "open" and not any(
        s.shifts for s in event.stations if s.type == "helper"
    ) and not any(s.type == "donation" for s in event.stations):
        flash(
            request,
            "Die Anmeldung wurde geöffnet, aber es gibt noch keine Schichten oder "
            "Spenden-Stationen – Eltern sehen eine leere Seite.",
            WARNING,
        )

    event.status = new_status
    db.commit()

    if new_status == "published":
        background.add_task(send_confirmations, event_id)
        flash(
            request,
            "Der Helferplan ist veröffentlicht. Die Bestätigungsmails werden im "
            "Hintergrund versandt – der Fortschritt steht im Dashboard.",
        )
    else:
        flash(request, _TRANSITION_MESSAGE.get(new_status, "Status geändert."))

    return _redirect(f"/admin/events/{event_id}")


# ─── Stationen ────────────────────────────────────────────────────────────────

@router.post("/events/{event_id}/stations/new")
async def station_new(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    station_type: Annotated[str, Form()] = "helper",
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    target_count: Annotated[str, Form()] = "",
    sort_order: Annotated[int, Form()] = 0,
):
    event = _get_event(event_id, db)
    if not name.strip():
        flash(request, "Die Station braucht einen Namen.", ERROR)
        return _redirect(f"/admin/events/{event_id}")
    if station_type not in ("helper", "donation"):
        flash(request, "Unbekannter Stationstyp.", ERROR)
        return _redirect(f"/admin/events/{event_id}")

    tc = int(target_count) if target_count.strip().isdigit() else None
    station = Station(
        event_id=event.id,
        type=station_type,
        name=name.strip(),
        description=description.strip() or None,
        target_count=tc if station_type == "donation" else None,
        sort_order=sort_order,
    )
    db.add(station)
    db.commit()

    if station_type == "helper":
        flash(request, f"Station „{station.name}“ angelegt. Legen Sie jetzt die Schichten an.")
    else:
        flash(request, f"Spenden-Station „{station.name}“ angelegt.")
    return _redirect(f"/admin/events/{event_id}#station-{station.id}")


@router.post("/stations/{station_id}/edit")
async def station_edit(
    request: Request,
    station_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    target_count: Annotated[str, Form()] = "",
    sort_order: Annotated[int, Form()] = 0,
):
    station = db.get(Station, station_id)
    if not station:
        raise HTTPException(404)
    if not name.strip():
        flash(request, "Die Station braucht einen Namen.", ERROR)
        return _redirect(f"/admin/events/{station.event_id}")

    station.name = name.strip()
    station.description = description.strip() or None
    station.target_count = (
        int(target_count) if target_count.strip().isdigit() and station.type == "donation" else None
    )
    station.sort_order = sort_order
    db.commit()
    flash(request, f"Station „{station.name}“ gespeichert.")
    return _redirect(f"/admin/events/{station.event_id}#station-{station.id}")


@router.post("/stations/{station_id}/delete")
async def station_delete(
    request: Request,
    station_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
):
    station = db.get(Station, station_id)
    if not station:
        raise HTTPException(404)

    event_id = station.event_id
    name = station.name
    bookings = sum(len(s.bookings) for s in station.shifts)
    donations = len(station.donations)
    db.delete(station)
    db.commit()

    lost = []
    if bookings:
        lost.append(f"{bookings} Buchung{'en' if bookings != 1 else ''}")
    if donations:
        lost.append(f"{donations} Zusage{'n' if donations != 1 else ''}")
    suffix = f" Dabei wurden {' und '.join(lost)} entfernt." if lost else ""
    flash(request, f"Station „{name}“ gelöscht.{suffix}", WARNING if lost else SUCCESS)
    return _redirect(f"/admin/events/{event_id}")


@router.post("/events/{event_id}/stations/copy")
async def stations_copy(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    source_event_id: Annotated[int, Form()] = 0,
    with_shifts: Annotated[str, Form()] = "",
):
    """Aufbau einer früheren Veranstaltung übernehmen.

    Der Stationsaufbau wiederholt sich jährlich fast unverändert; ihn jedes
    Mal neu einzutippen ist die mit Abstand langwierigste Admin-Aufgabe.
    """
    event = _get_event(event_id, db)
    source = db.get(Event, source_event_id)
    if not source or source.id == event.id:
        flash(request, "Bitte wählen Sie eine andere Veranstaltung als Vorlage.", ERROR)
        return _redirect(f"/admin/events/{event_id}")

    offset = None
    if event.event_date and source.event_date:
        offset = event.event_date - source.event_date

    stations_added = 0
    shifts_added = 0
    base_order = len(event.stations)

    for source_station in sorted(source.stations, key=lambda s: s.sort_order):
        station = Station(
            event_id=event.id,
            type=source_station.type,
            name=source_station.name,
            description=source_station.description,
            target_count=source_station.target_count,
            sort_order=base_order + stations_added,
        )
        db.add(station)
        db.flush()
        stations_added += 1

        if with_shifts and source_station.type == "helper":
            for source_shift in sorted(source_station.shifts, key=lambda s: s.start_time):
                start = source_shift.start_time
                if offset is not None:
                    start = start + offset
                db.add(Shift(
                    station_id=station.id,
                    start_time=start,
                    duration_min=source_shift.duration_min,
                    required_count=source_shift.required_count,
                    sort_order=source_shift.sort_order,
                ))
                shifts_added += 1

    db.commit()

    parts = [f"{stations_added} Station{'en' if stations_added != 1 else ''}"]
    if shifts_added:
        parts.append(f"{shifts_added} Schicht{'en' if shifts_added != 1 else ''}")
    flash(
        request,
        f"{' und '.join(parts)} aus „{source.name}“ übernommen. "
        "Bitte prüfen Sie die Zeiten.",
    )
    return _redirect(f"/admin/events/{event_id}")


# ─── Schichten ────────────────────────────────────────────────────────────────

@router.post("/stations/{station_id}/shifts/new")
async def shift_new(
    request: Request,
    station_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    start_time: Annotated[str, Form()] = "",
    duration_min: Annotated[int, Form()] = 60,
    required_count: Annotated[int, Form()] = 1,
    sort_order: Annotated[int, Form()] = 0,
):
    station = db.get(Station, station_id)
    if not station or station.type != "helper":
        raise HTTPException(404)

    start = parse_local(start_time)
    if not start:
        flash(request, "Bitte geben Sie eine gültige Startzeit an.", ERROR)
        return _redirect(f"/admin/events/{station.event_id}#station-{station.id}")
    if duration_min < 1 or required_count < 1:
        flash(request, "Dauer und Soll-Personen müssen mindestens 1 betragen.", ERROR)
        return _redirect(f"/admin/events/{station.event_id}#station-{station.id}")

    db.add(Shift(
        station_id=station_id,
        start_time=start,
        duration_min=duration_min,
        required_count=required_count,
        sort_order=sort_order,
    ))
    db.commit()
    flash(request, f"Schicht am {start.strftime('%d.%m.%Y um %H:%M')} Uhr angelegt.")
    return _redirect(f"/admin/events/{station.event_id}#station-{station.id}")


@router.post("/stations/{station_id}/shifts/series")
async def shift_series(
    request: Request,
    station_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    series_date: Annotated[str, Form()] = "",
    series_from: Annotated[str, Form()] = "",
    series_to: Annotated[str, Form()] = "",
    duration_min: Annotated[int, Form()] = 60,
    required_count: Annotated[int, Form()] = 1,
):
    """Eine ganze Schichtreihe auf einmal anlegen.

    Ein Stand von 10 bis 18 Uhr im Stundentakt sind acht Schichten – einzeln
    angelegt acht Formulardurchläufe.
    """
    station = db.get(Station, station_id)
    if not station or station.type != "helper":
        raise HTTPException(404)

    anchor = f"/admin/events/{station.event_id}#station-{station.id}"
    day = parse_date(series_date)
    start = parse_local(f"{series_date}T{series_from}") if day and series_from else None
    end = parse_local(f"{series_date}T{series_to}") if day and series_to else None

    if not (day and start and end):
        flash(request, "Bitte geben Sie Datum, Beginn und Ende der Reihe an.", ERROR)
        return _redirect(anchor)
    if duration_min < 1 or required_count < 1:
        flash(request, "Dauer und Soll-Personen müssen mindestens 1 betragen.", ERROR)
        return _redirect(anchor)
    if end <= start:
        flash(request, "Das Ende der Reihe muss nach dem Beginn liegen.", ERROR)
        return _redirect(anchor)

    step = timedelta(minutes=duration_min)
    if (end - start) < step:
        flash(
            request,
            f"Der Zeitraum ist kürzer als eine Schicht ({duration_min} Min.).",
            ERROR,
        )
        return _redirect(anchor)

    existing = {s.start_time for s in station.shifts}
    order = len(station.shifts)
    created = 0
    skipped = 0
    cursor = start
    while cursor + step <= end:
        if cursor in existing:
            skipped += 1
        else:
            db.add(Shift(
                station_id=station.id,
                start_time=cursor,
                duration_min=duration_min,
                required_count=required_count,
                sort_order=order,
            ))
            order += 1
            created += 1
        cursor += step

    db.commit()

    message = f"{created} Schicht{'en' if created != 1 else ''} angelegt."
    if skipped:
        message += f" {skipped} bereits vorhandene übersprungen."
    flash(request, message, SUCCESS if created else INFO)
    return _redirect(anchor)


@router.post("/shifts/{shift_id}/edit")
async def shift_edit(
    request: Request,
    shift_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    start_time: Annotated[str, Form()] = "",
    duration_min: Annotated[int, Form()] = 60,
    required_count: Annotated[int, Form()] = 1,
    sort_order: Annotated[int, Form()] = 0,
):
    shift = db.get(Shift, shift_id)
    if not shift:
        raise HTTPException(404)

    anchor = f"/admin/events/{shift.station.event_id}#station-{shift.station_id}"
    start = parse_local(start_time)
    if not start:
        flash(request, "Bitte geben Sie eine gültige Startzeit an.", ERROR)
        return _redirect(anchor)
    if duration_min < 1 or required_count < 1:
        flash(request, "Dauer und Soll-Personen müssen mindestens 1 betragen.", ERROR)
        return _redirect(anchor)

    booked = len(shift.bookings)
    shift.start_time = start
    shift.duration_min = duration_min
    shift.required_count = required_count
    shift.sort_order = sort_order
    db.commit()

    if required_count < booked:
        flash(
            request,
            f"Schicht gespeichert – die Soll-Zahl ({required_count}) liegt jetzt unter "
            f"den {booked} bereits eingetragenen Helfern. Die Schicht gilt als überbelegt.",
            WARNING,
        )
    else:
        flash(request, f"Schicht am {start.strftime('%d.%m.%Y um %H:%M')} Uhr gespeichert.")
    return _redirect(anchor)


@router.post("/shifts/{shift_id}/delete")
async def shift_delete(
    request: Request,
    shift_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
):
    shift = db.get(Shift, shift_id)
    if not shift:
        raise HTTPException(404)

    event_id = shift.station.event_id
    station_id = shift.station_id
    label = shift.start_time.strftime("%d.%m.%Y %H:%M")
    booked = len(shift.bookings)
    db.delete(shift)
    db.commit()

    if booked:
        flash(
            request,
            f"Schicht {label} Uhr gelöscht – dabei wurden {booked} eingetragene "
            f"Helfer entfernt.",
            WARNING,
        )
    else:
        flash(request, f"Schicht {label} Uhr gelöscht.")
    return _redirect(f"/admin/events/{event_id}#station-{station_id}")


# ─── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/events/{event_id}/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    event = db.execute(
        select(Event)
        .where(Event.id == event_id)
        .options(
            selectinload(Event.stations)
            .selectinload(Station.shifts)
            .selectinload(Shift.bookings)
            .selectinload(Booking.participant)
            .selectinload(Participant.klasse)
        )
        .options(
            selectinload(Event.stations)
            .selectinload(Station.donations)
            .selectinload(Donation.participant)
            .selectinload(Participant.klasse)
        )
    ).scalar_one_or_none()
    if not event:
        raise HTTPException(404)

    participants = (
        db.execute(
            select(Participant)
            .where(Participant.event_id == event_id)
            .options(selectinload(Participant.klasse))
            .order_by(Participant.name)
        )
        .scalars()
        .all()
    )

    understaffed = 0
    for station in event.stations:
        for shift in station.shifts:
            if len(shift.bookings) < shift.required_count:
                understaffed += 1

    return templates.TemplateResponse(request, "admin/events/dashboard.html", {
        "event": event,
        "admin": admin,
        "participants": participants,
        "participant_count": len(participants),
        "understaffed": understaffed,
        "mail_progress": confirmation_progress(db, event),
    })


# ─── Manuelle Korrekturen ─────────────────────────────────────────────────────

@router.post("/events/{event_id}/bookings/add")
async def booking_add_manual(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    participant_id: Annotated[int, Form()] = 0,
    shift_id: Annotated[int, Form()] = 0,
):
    """Telefonische Zusagen nachtragen – der Normalfall am Festtag."""
    event = _get_event(event_id, db)
    target = f"/admin/events/{event_id}/dashboard"

    participant = db.get(Participant, participant_id)
    shift = db.get(Shift, shift_id)

    if not participant or participant.event_id != event.id:
        flash(request, "Bitte wählen Sie einen Teilnehmer dieser Veranstaltung.", ERROR)
        return _redirect(target)
    if not shift or shift.station.event_id != event.id:
        flash(request, "Diese Schicht gehört nicht zu dieser Veranstaltung.", ERROR)
        return _redirect(target)

    existing = db.execute(
        select(Booking).where(
            Booking.participant_id == participant.id, Booking.shift_id == shift.id
        )
    ).scalar_one_or_none()
    if existing:
        flash(request, f"{participant.name} ist für diese Schicht bereits eingetragen.", INFO)
        return _redirect(target)

    booked = len(shift.bookings)
    db.add(Booking(participant_id=participant.id, shift_id=shift.id))
    db.commit()

    label = f"{shift.station.name}, {shift.start_time.strftime('%d.%m. %H:%M')} Uhr"
    if booked >= shift.required_count:
        flash(
            request,
            f"{participant.name} wurde für {label} eingetragen – die Schicht ist damit "
            f"überbelegt ({booked + 1} von {shift.required_count}).",
            WARNING,
        )
    else:
        flash(request, f"{participant.name} für {label} eingetragen.")
    return _redirect(target)


@router.post("/bookings/{booking_id}/delete")
async def booking_delete_manual(
    request: Request,
    booking_id: int,
    event_id: Annotated[int, Form()],
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
):
    booking = db.get(Booking, booking_id)
    if booking:
        label = (
            f"{booking.participant.name} – {booking.shift.station.name}, "
            f"{booking.shift.start_time.strftime('%d.%m. %H:%M')} Uhr"
        )
        db.delete(booking)
        db.commit()
        flash(request, f"Buchung storniert: {label}.")
    else:
        flash(request, "Diese Buchung existiert nicht mehr.", INFO)
    return _redirect(f"/admin/events/{event_id}/dashboard")


# ─── Schließen / Veröffentlichen ──────────────────────────────────────────────

@router.post("/events/{event_id}/close")
async def event_close(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
):
    event = _get_event(event_id, db)
    if event.status == "open":
        event.status = "closed"
        db.commit()
        flash(request, _TRANSITION_MESSAGE["closed"])
    else:
        flash(request, "Die Anmeldung war bereits geschlossen.", INFO)
    return _redirect(f"/admin/events/{event_id}/dashboard")


@router.post("/events/{event_id}/publish")
async def event_publish(
    request: Request,
    event_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
):
    event = _get_event(event_id, db)
    if event.status != "closed":
        flash(request, "Nur eine geschlossene Anmeldung kann veröffentlicht werden.", ERROR)
        return _redirect(f"/admin/events/{event_id}/dashboard")

    event.status = "published"
    db.commit()

    # Der Versand läuft nach der Antwort weiter – bei 150 Teilnehmern dauert er
    # länger als jedes vernünftige Request-Timeout.
    background.add_task(send_confirmations, event_id)
    flash(
        request,
        "Der Helferplan ist veröffentlicht. Die Bestätigungsmails werden im "
        "Hintergrund versandt – den Fortschritt sehen Sie hier auf der Seite.",
    )
    return _redirect(f"/admin/events/{event_id}/dashboard")


@router.post("/events/{event_id}/resend")
async def event_resend(
    request: Request,
    event_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
):
    """Nur die fehlgeschlagenen bzw. noch offenen Bestätigungen erneut versenden."""
    event = _get_event(event_id, db)
    if event.status not in ("published", "archived"):
        flash(request, "Es wurde noch kein Helferplan veröffentlicht.", ERROR)
        return _redirect(f"/admin/events/{event_id}/dashboard")

    background.add_task(send_confirmations, event_id, True)
    flash(request, "Der erneute Versand an die offenen Adressen wurde gestartet.")
    return _redirect(f"/admin/events/{event_id}/dashboard")


# ─── Export ───────────────────────────────────────────────────────────────────

@router.get("/events/{event_id}/export.csv")
async def export_csv(
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    event = _get_event(event_id, db)

    participants = db.execute(
        select(Participant)
        .where(Participant.event_id == event_id)
        .options(
            selectinload(Participant.klasse),
            selectinload(Participant.bookings).selectinload(Booking.shift).selectinload(Shift.station),
            selectinload(Participant.donations).selectinload(Donation.station),
        )
        .order_by(Participant.name)
    ).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, dialect="excel", delimiter=";")
    writer.writerow(["Name", "E-Mail", "Klasse", "Typ", "Station", "Detail", "Datum/Zeit"])

    for p in participants:
        for booking in sorted(p.bookings, key=lambda b: b.shift.start_time):
            shift = booking.shift
            writer.writerow([
                p.name, p.email, p.klasse.label,
                "Schicht", shift.station.name,
                f"{shift.duration_min} Min.",
                shift.start_time.strftime("%d.%m.%Y %H:%M"),
            ])
        for don in p.donations:
            writer.writerow([
                p.name, p.email, p.klasse.label,
                "Spende", don.station.name,
                f"{don.item_text} (×{don.quantity})", "",
            ])

    output.seek(0)
    # BOM, damit Excel die Umlaute richtig liest.
    payload = "﻿" + output.getvalue()
    filename = f"helferplan_{event_id}.csv"
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/events/{event_id}/export.pdf")
async def export_pdf(
    event_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    event = db.execute(
        select(Event)
        .where(Event.id == event_id)
        .options(
            selectinload(Event.stations)
            .selectinload(Station.shifts)
            .selectinload(Shift.bookings)
            .selectinload(Booking.participant)
            .selectinload(Participant.klasse)
        )
        .options(
            selectinload(Event.stations)
            .selectinload(Station.donations)
            .selectinload(Donation.participant)
            .selectinload(Participant.klasse)
        )
    ).scalar_one_or_none()
    if not event:
        raise HTTPException(404)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"Helferplan: {event.name}", styles["Title"]))
    event_date_str = event.event_date.strftime('%d.%m.%Y') if event.event_date else ''
    story.append(Paragraph(f"Datum: {event_date_str}", styles["Normal"]))
    story.append(Spacer(1, 0.5*cm))

    for station in sorted(event.stations, key=lambda s: s.sort_order):
        story.append(Paragraph(station.name, styles["Heading2"]))
        if station.description:
            story.append(Paragraph(station.description, styles["Normal"]))

        if station.type == "helper":
            for shift in sorted(station.shifts, key=lambda s: s.start_time):
                story.append(Paragraph(
                    f"{shift.start_time.strftime('%H:%M')} – {shift.duration_min} Min. "
                    f"({len(shift.bookings)}/{shift.required_count})",
                    styles["Heading3"],
                ))
                if shift.bookings:
                    data = [["Name", "Klasse", "E-Mail"]]
                    for b in shift.bookings:
                        data.append([b.participant.name, b.participant.klasse.label, b.participant.email])
                    tbl = Table(data, colWidths=[6*cm, 2.5*cm, 7*cm])
                    tbl.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ]))
                    story.append(tbl)
                story.append(Spacer(1, 0.3*cm))
        else:
            total = sum(d.quantity for d in station.donations)
            target_str = f" (Ziel: {station.target_count})" if station.target_count else ""
            story.append(Paragraph(f"Gesamt: {total}{target_str}", styles["Normal"]))
            if station.donations:
                data = [["Spende", "Menge", "Name", "Klasse"]]
                for don in station.donations:
                    data.append([don.item_text, str(don.quantity), don.participant.name, don.participant.klasse.label])
                tbl = Table(data, colWidths=[6*cm, 2*cm, 5*cm, 2.5*cm])
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgreen),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                ]))
                story.append(tbl)
        story.append(Spacer(1, 0.5*cm))

    doc.build(story)
    buf.seek(0)
    filename = f"helferplan_{event_id}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── Klassenliste ─────────────────────────────────────────────────────────────

@router.get("/classes", response_class=HTMLResponse)
async def classes_list(
    request: Request,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    klassen = db.execute(select(Klasse).order_by(Klasse.school_year, Klasse.label)).scalars().all()
    usage = dict(
        db.execute(
            select(Participant.klasse_id, func.count(Participant.id))
            .group_by(Participant.klasse_id)
        ).all()
    )
    return templates.TemplateResponse(request, "admin/classes/list.html", {
        "klassen": klassen,
        "usage": usage,
        "admin": admin,
    })


@router.post("/classes/new")
async def klasse_new(
    request: Request,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    label: Annotated[str, Form()] = "",
    school_year: Annotated[str, Form()] = "",
    active: Annotated[str, Form()] = "",
):
    label = label.strip()
    school_year = school_year.strip()
    if not label or not school_year:
        flash(request, "Klasse und Schuljahr sind Pflichtfelder.", ERROR)
        return _redirect("/admin/classes")

    existing = db.execute(
        select(Klasse).where(Klasse.label == label, Klasse.school_year == school_year)
    ).scalar_one_or_none()
    if existing:
        flash(request, f"Die Klasse {label} ({school_year}) gibt es bereits.", INFO)
        return _redirect("/admin/classes")

    db.add(Klasse(label=label, school_year=school_year, active=bool(active)))
    db.commit()
    flash(request, f"Klasse {label} ({school_year}) angelegt.")
    return _redirect("/admin/classes")


@router.post("/classes/{klasse_id}/edit")
async def klasse_edit(
    request: Request,
    klasse_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    label: Annotated[str, Form()] = "",
    school_year: Annotated[str, Form()] = "",
    active: Annotated[str, Form()] = "",
):
    klasse = db.get(Klasse, klasse_id)
    if not klasse:
        raise HTTPException(404)
    if not label.strip() or not school_year.strip():
        flash(request, "Klasse und Schuljahr sind Pflichtfelder.", ERROR)
        return _redirect("/admin/classes")

    was_active = klasse.active
    klasse.label = label.strip()
    klasse.school_year = school_year.strip()
    klasse.active = bool(active)
    db.commit()

    if was_active != klasse.active:
        state = "aktiviert" if klasse.active else "deaktiviert"
        hint = "" if klasse.active else " Sie steht bei neuen Anmeldungen nicht mehr zur Auswahl."
        flash(request, f"Klasse {klasse.label} {state}.{hint}")
    else:
        flash(request, f"Klasse {klasse.label} gespeichert.")
    return _redirect("/admin/classes")


@router.post("/classes/{klasse_id}/delete")
async def klasse_delete(
    request: Request,
    klasse_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
):
    klasse = db.get(Klasse, klasse_id)
    if not klasse:
        flash(request, "Diese Klasse existiert nicht mehr.", INFO)
        return _redirect("/admin/classes")

    # Ohne diese Prüfung schlägt der Fremdschlüssel zu und der Admin sieht
    # einen Serverfehler statt einer Erklärung.
    in_use = db.execute(
        select(func.count(Participant.id)).where(Participant.klasse_id == klasse_id)
    ).scalar_one()
    if in_use:
        flash(
            request,
            f"Klasse {klasse.label} kann nicht gelöscht werden – {in_use} Anmeldung"
            f"{'en sind' if in_use != 1 else ' ist'} damit verknüpft. "
            "Deaktivieren Sie die Klasse stattdessen, dann steht sie bei neuen "
            "Anmeldungen nicht mehr zur Auswahl.",
            ERROR,
        )
        return _redirect("/admin/classes")

    label = klasse.label
    db.delete(klasse)
    db.commit()
    flash(request, f"Klasse {label} gelöscht.")
    return _redirect("/admin/classes")


@router.post("/classes/import")
async def klasse_import(
    request: Request,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
    _csrf: None = CSRF,
    file: UploadFile = File(...),
):
    content = await file.read()
    if not content:
        flash(request, "Die Datei ist leer.", ERROR)
        return _redirect("/admin/classes")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    # Semikolon ist das, was Excel im deutschen Sprachraum schreibt.
    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    fields = {(name or "").strip().lower() for name in (reader.fieldnames or [])}
    missing = {"label", "school_year"} - fields
    if missing:
        flash(
            request,
            "Die Datei hat nicht die erwarteten Spalten. Es fehlt: "
            f"{', '.join(sorted(missing))}. Erwartet werden „label“ und „school_year“ "
            f"in der ersten Zeile (gefunden: {', '.join(sorted(fields)) or 'nichts'}).",
            ERROR,
        )
        return _redirect("/admin/classes")

    created = 0
    duplicates = 0
    incomplete = 0

    for row in reader:
        normalized = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
        label = normalized.get("label", "").strip()
        school_year = normalized.get("school_year", "").strip()
        if not label or not school_year:
            incomplete += 1
            continue
        existing = db.execute(
            select(Klasse).where(Klasse.label == label, Klasse.school_year == school_year)
        ).scalar_one_or_none()
        if existing:
            duplicates += 1
            continue
        db.add(Klasse(label=label, school_year=school_year, active=True))
        created += 1

    db.commit()

    parts = [f"{created} Klasse{'n' if created != 1 else ''} importiert"]
    if duplicates:
        parts.append(f"{duplicates} bereits vorhanden")
    if incomplete:
        parts.append(f"{incomplete} Zeile{'n' if incomplete != 1 else ''} unvollständig übersprungen")
    flash(request, ", ".join(parts) + ".", SUCCESS if created else WARNING)
    return _redirect("/admin/classes")
