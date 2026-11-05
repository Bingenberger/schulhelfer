"""Runtime contract checks; all databases and credentials are synthetic."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

ROOT = Path("/app")

def call(source, database, **kwargs):
    env = os.environ | {"DATABASE_URL": "sqlite:///" + str(database),
                       "SECRET_KEY": "synthetic-session-fixture-key-0123456789", "SECRET_KEY_FILE": ""}
    return subprocess.run([sys.executable, "-c", source], env=env, text=True,
                          capture_output=True, timeout=30, **kwargs)


def test_missing_secret_refuses_import_without_exposing_value():
    env = os.environ | {"SECRET_KEY": "", "SECRET_KEY_FILE": ""}
    result = subprocess.run([sys.executable, "-c", "import app.main"], env=env, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert b"changeme" not in result.stdout


def test_explicit_migration_idempotence_and_no_schema_on_import(tmp_path):
    path = tmp_path / "app.db"
    result = call("import app.main;from app.platform import schema_ready;assert not schema_ready()", path)
    assert result.returncode == 0, result.stderr
    assert not path.exists()
    result = call("from app.migrate import migrate;migrate();migrate();from app.platform import schema_ready;assert schema_ready()", path)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM admin_user").fetchone() == (0,)
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("b7c2e9a41d38",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert path.stat().st_mode & 0o777 == 0o600


def test_unversioned_existing_and_future_schema_fail_closed(tmp_path):
    for name, version in (("legacy", 0), ("future", 99)):
        path = tmp_path / (name + ".db")
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE existing(id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO existing VALUES(7)")
            db.execute("PRAGMA user_version=" + str(version))
        result = call("from app.migrate import migrate;migrate()", path)
        assert result.returncode != 0
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT id FROM existing").fetchall() == [(7,)]
            assert db.execute("SELECT name FROM sqlite_master WHERE name='admin_user'").fetchall() == []


def test_local_setup_and_one_time_guard(tmp_path):
    path = tmp_path / "app.db"
    source = """from app.migrate import migrate
migrate()
from app.setup_admin import setup
setup('synthetic-admin','synthetic-private-password')
from app.database import SessionLocal
from app.models import AdminUser
from sqlalchemy import select
with SessionLocal() as db:
    assert db.execute(select(AdminUser.id)).scalars().all()==[1]
try:
    setup('another-admin','synthetic-private-password')
except ValueError:
    pass
else:
    raise AssertionError('Setup was replayed')
"""
    result = call(source, path)
    assert result.returncode == 0, result.stderr
    # A missing controlling terminal must not issue an account.
    result = call("from app.setup_admin import main;main()", tmp_path / "no-tty.db")
    assert result.returncode != 0 and not (tmp_path / "no-tty.db").exists()


@pytest.mark.parametrize("lock", ["migration", "scheduler"])
def test_lock_prevents_a_second_process(tmp_path, lock):
    path = tmp_path / "app.db"
    env = os.environ | {"DATABASE_URL": "sqlite:///" + str(path),
                       "SECRET_KEY": "synthetic-session-fixture-key-0123456789", "SECRET_KEY_FILE": ""}
    source = ("from app.platform import data_lock;import sys;guard=data_lock('" + lock +
              "');guard.__enter__();print('held',flush=True);sys.stdin.readline()")
    first = subprocess.Popen([sys.executable, "-c", source], env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == "held"
        result = call("from app.platform import data_lock;guard=data_lock('" + lock + "');guard.__enter__()", path)
        assert result.returncode != 0
    finally:
        first.communicate("\n", timeout=10)
    assert call("from app.platform import data_lock;guard=data_lock('" + lock + "');guard.__enter__()", path).returncode == 0


def test_pdf_slot_refuses_parallel_export_and_recovers():
    from app.export_limit import pdf_slot
    from fastapi import HTTPException
    first = pdf_slot()
    next(first)
    try:
        second = pdf_slot()
        with pytest.raises(HTTPException) as problem:
            next(second)
        assert problem.value.status_code == 503
    finally:
        first.close()
    resumed = pdf_slot()
    next(resumed)
    resumed.close()


def test_smtp_timeout_bounds_the_entire_send(monkeypatch):
    import asyncio
    import time
    from app import email_service
    async def hanging_send(*args, **kwargs):
        await asyncio.sleep(30)
    monkeypatch.setattr(email_service.aiosmtplib, "send", hanging_send)
    monkeypatch.setattr(email_service.settings, "SMTP_TIMEOUT_S", 1)
    started = time.monotonic()
    assert asyncio.run(email_service._send("synthetic@example.invalid", "Synthetic", "Synthetic", "<p>Synthetic</p>")) is False
    assert time.monotonic() - started < 3


def test_retention_removes_dependent_mail_logs_before_participants(tmp_path):
    path = tmp_path / "retention.db"
    source = """from datetime import date,datetime
from app.migrate import migrate
migrate()
from app.database import SessionLocal
from app.models import Klasse,Event,Station,Shift,Participant,Booking,Donation,EmailLog
from app.scheduler import _run_retention_cleanup
from sqlalchemy import select,func
with SessionLocal() as db:
    klasse=Klasse(label='Synthetic',school_year='2000/01',active=True)
    event=Event(name='Synthetic expired event',event_date=date(2000,1,1),status='archived',retention_weeks=1)
    db.add_all([klasse,event]);db.flush()
    station=Station(event_id=event.id,type='helper',name='Synthetic',sort_order=0)
    db.add(station);db.flush()
    shift=Shift(station_id=station.id,start_time=datetime(2000,1,1,10),duration_min=60,required_count=1,sort_order=0)
    participant=Participant(event_id=event.id,klasse_id=klasse.id,name='Synthetic',email='synthetic@example.invalid',token_hash='synthetic-hash',token='synthetic-capability')
    db.add_all([shift,participant]);db.flush()
    db.add_all([Booking(participant_id=participant.id,shift_id=shift.id),Donation(participant_id=participant.id,station_id=station.id,item_text='Synthetic',quantity=1),EmailLog(participant_id=participant.id,kind='access_link',success=True)])
    db.commit()
_run_retention_cleanup()
_run_retention_cleanup()
with SessionLocal() as db:
    for model in [Participant,Booking,Donation,EmailLog]:
        assert db.execute(select(func.count()).select_from(model)).scalar_one()==0
    assert db.execute(select(func.count()).select_from(Event)).scalar_one()==1
    assert db.execute(select(func.count()).select_from(Klasse)).scalar_one()==1
"""
    result = call(source, path)
    assert result.returncode == 0, result.stderr


LEGACY = """
import sqlite3, sys
from sqlalchemy import create_engine
from app.database import Base
from app import models
engine = create_engine('sqlite:///' + sys.argv[1])
Base.metadata.create_all(engine)          # exactly what the old init_db() did on every start
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("INSERT INTO event(id,name,event_date,status,retention_weeks,created_at) VALUES(1,'Schulfest','2026-06-20','open',8,'2026-05-01')")
    db.execute("INSERT INTO klasse(id,label,school_year,active) VALUES(1,'1a','2025/26',1)")
    db.execute("INSERT INTO participant(id,event_id,name,email,klasse_id,token_hash,created_at) VALUES(1,1,'Eltern','e@x.invalid',1,'h','2026-05-02')")
"""


def legacy_database(path, drop_token=False):
    result = subprocess.run([sys.executable, "-c", LEGACY, str(path)], capture_output=True, text=True, timeout=30,
                            env=os.environ | {"SECRET_KEY": "synthetic-session-fixture-key-0123456789"})
    assert result.returncode == 0, result.stderr
    if drop_token:
        with sqlite3.connect(path) as db:
            db.execute("ALTER TABLE participant DROP COLUMN token")


@pytest.mark.parametrize("drop_token,revision", [(False, "b7c2e9a41d38"), (True, "a5ef5d7feb55")])
def test_create_all_database_of_the_old_version_is_adopted(tmp_path, drop_token, revision):
    # Real old server: app.db built by create_all (no alembic_version); the import was refused.
    path = tmp_path / "app.db"
    legacy_database(path, drop_token)
    result = call("from app.migrate import migrate;migrate();migrate();from app.platform import schema_ready;assert schema_ready()", path)
    assert result.returncode == 0, result.stderr
    assert f"adopted as {revision}" in result.stdout
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("b7c2e9a41d38",)
        assert db.execute("SELECT name, email FROM participant").fetchall() == [("Eltern", "e@x.invalid")]
        assert "token" in [row[1] for row in db.execute("PRAGMA table_info(participant)")]


def test_adoption_refuses_a_structure_that_matches_no_known_revision(tmp_path):
    path = tmp_path / "app.db"
    legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE booking ADD COLUMN note TEXT")
    result = call("from app.migrate import migrate;migrate()", path)
    assert result.returncode != 0 and "booking" in result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchall() == []
        assert db.execute("SELECT count(*) FROM participant").fetchone() == (1,)
