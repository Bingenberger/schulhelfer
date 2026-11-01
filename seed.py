"""Seed-Skript: Demo-Daten anlegen (Admin, Klassen, Demo-Event)."""
import argparse
import sys
from datetime import datetime, timedelta, date

from app.database import init_db, SessionLocal
from app.models import AdminUser, Klasse, Event, Station, Shift
from app.security import hash_password


def create_admin(db, username: str, password: str):
    from sqlalchemy import select
    existing = db.execute(select(AdminUser).where(AdminUser.username == username)).scalar_one_or_none()
    if existing:
        print(f"Admin '{username}' existiert bereits.")
        return
    admin = AdminUser(username=username, password_hash=hash_password(password))
    db.add(admin)
    db.commit()
    print(f"Admin '{username}' angelegt.")


def seed_demo(db):
    from sqlalchemy import select

    # Klassen anlegen
    klassen_data = [
        ("1a", "2026/27"), ("1b", "2026/27"), ("2a", "2026/27"), ("2b", "2026/27"),
        ("3a", "2026/27"), ("3b", "2026/27"), ("4a", "2026/27"), ("4b", "2026/27"),
    ]
    for label, year in klassen_data:
        existing = db.execute(
            select(Klasse).where(Klasse.label == label, Klasse.school_year == year)
        ).scalar_one_or_none()
        if not existing:
            db.add(Klasse(label=label, school_year=year, active=True))
    db.commit()
    print(f"Klassenliste ({len(klassen_data)} Klassen) angelegt.")

    # Demo-Event
    event_date = date.today() + timedelta(days=30)
    event = Event(
        name="Schulfest 2026",
        event_date=event_date,
        location="Schulhof GGS Niederkassel",
        description="Herzlich willkommen zu unserem Schulfest! Bitte melden Sie sich als Helfer an.",
        registration_deadline=datetime.now() + timedelta(days=25),
        status="open",
        retention_weeks=4,
    )
    db.add(event)
    db.flush()

    # Helfer-Station: Kuchenverkauf
    station1 = Station(event_id=event.id, type="helper", name="Kuchenverkauf", sort_order=0)
    db.add(station1)
    db.flush()

    base_dt = datetime.combine(event_date, datetime.strptime("10:00", "%H:%M").time())
    for i in range(3):
        db.add(Shift(
            station_id=station1.id,
            start_time=base_dt + timedelta(hours=i),
            duration_min=60,
            required_count=3,
            sort_order=i,
        ))

    # Helfer-Station: Einlass
    station2 = Station(event_id=event.id, type="helper", name="Einlass", sort_order=1)
    db.add(station2)
    db.flush()
    db.add(Shift(
        station_id=station2.id,
        start_time=base_dt,
        duration_min=120,
        required_count=2,
        sort_order=0,
    ))

    # Spenden-Station: Kuchenspenden
    station3 = Station(
        event_id=event.id, type="donation", name="Kuchenspenden",
        description="Bitte bringen Sie einen selbst gebackenen Kuchen mit.",
        target_count=20, sort_order=2,
    )
    db.add(station3)

    db.commit()
    print(f"Demo-Event 'Schulfest 2026' (ID={event.id}) angelegt.")


def main():
    parser = argparse.ArgumentParser(description="Schulfest-Helfer Seed-Skript")
    parser.add_argument("--admin-user", default="admin", help="Admin-Benutzername")
    parser.add_argument("--admin-pass", default="admin123", help="Admin-Passwort")
    parser.add_argument("--demo", action="store_true", help="Demo-Daten anlegen")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        create_admin(db, args.admin_user, args.admin_pass)
        if args.demo:
            seed_demo(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
