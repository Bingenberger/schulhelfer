import re

import pytest
from datetime import datetime, timedelta, date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import AdminUser, Event, Klasse, Shift, Station
from app.security import hash_password, generate_token, hash_token

import os
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite:///./data/test.db")
os.makedirs(os.path.dirname(TEST_DB_URL.removeprefix("sqlite:///")) or ".", exist_ok=True)

engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

# Hintergrundjobs öffnen ihre eigene Sitzung über die Factory in
# app.database – die muss ebenfalls auf die Test-Datenbank zeigen.
import app.database as _database
_database.SessionLocal = TestingSessionLocal
_database.engine = engine
_settings_config = __import__("app.config", fromlist=["settings"]).settings
_settings_config.DATABASE_URL = TEST_DB_URL

# Kein echter Mailversand in Tests: Zustellung schlägt sofort fehl und wird
# – wie im Betrieb – als Fehlschlag im email_log protokolliert.
from app.config import settings as _settings
_settings.SMTP_HOST = "127.0.0.1"
_settings.SMTP_PORT = 1
_settings.SMTP_TIMEOUT_S = 1
_settings.SMTP_SEND_DELAY_MS = 0


@pytest.fixture(scope="function", autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    from app.platform import HEAD, SCHEMA
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE IF NOT EXISTS alembic_version(version_num VARCHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql("DELETE FROM alembic_version")
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES (?)", (HEAD,))
        connection.exec_driver_sql("PRAGMA user_version=" + str(SCHEMA))
    db = TestingSessionLocal()

    # Admin
    admin = AdminUser(username="admin", password_hash=hash_password("admin123"))
    db.add(admin)

    # Klasse
    klasse = Klasse(label="3b", school_year="2026/27", active=True)
    db.add(klasse)
    db.flush()

    # Event (open, with deadline in future)
    event = Event(
        name="Testfest",
        event_date=date.today() + timedelta(days=14),
        registration_deadline=datetime.utcnow() + timedelta(days=10),
        status="open",
        retention_weeks=1,
    )
    db.add(event)
    db.flush()

    # Station + Shift
    station = Station(event_id=event.id, type="helper", name="Kuchenverkauf", sort_order=0)
    db.add(station)
    db.flush()

    shift = Shift(
        station_id=station.id,
        start_time=datetime.utcnow() + timedelta(days=14, hours=2),
        duration_min=60,
        required_count=2,
        sort_order=0,
    )
    db.add(shift)

    # Donation station
    don_station = Station(
        event_id=event.id, type="donation", name="Kuchenspenden", target_count=10, sort_order=1
    )
    db.add(don_station)
    db.commit()
    db.close()
    yield


@pytest.fixture(scope="function", autouse=True)
def reset_rate_limits():
    """Die Rate-Limiter sind Prozess-Zustand und würden sonst zwischen Tests
    durchschlagen – ab dem elften Admin-Login der Sitzung käme sonst 429."""
    from app.routers import admin as admin_router
    from app.routers import public as public_router

    admin_router._login_rate.clear()
    public_router._rate_store.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def csrf_token(client, url: str = "/admin") -> str:
    """Das CSRF-Token aus einer gerenderten Admin-Seite lesen."""
    page = client.get(url)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match, f"Kein CSRF-Token auf {url} gefunden"
    return match.group(1)


class AdminClient:
    """TestClient-Hülle, die zustandsändernde Admin-Posts mit CSRF-Token versieht."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def post(self, url, *, data=None, files=None, token_from="/admin", **kwargs):
        payload = dict(data or {})
        payload.setdefault("csrf_token", csrf_token(self._client, token_from))
        return self._client.post(url, data=payload, files=files, **kwargs)


@pytest.fixture
def admin_client(client):
    client.post("/admin/login", data={"username": "admin", "password": "admin123"})
    return AdminClient(client)


def get_event_id(db) -> int:
    from sqlalchemy import select
    from app.models import Event as EventModel
    return db.execute(select(EventModel)).scalar_one().id


def get_shift_id(db) -> int:
    from sqlalchemy import select
    return db.execute(select(Shift)).scalar_one().id


def get_klasse_id(db) -> int:
    from sqlalchemy import select
    return db.execute(select(Klasse)).scalar_one().id


def get_don_station_id(db) -> int:
    from sqlalchemy import select
    return db.execute(select(Station).where(Station.type == "donation")).scalar_one().id
