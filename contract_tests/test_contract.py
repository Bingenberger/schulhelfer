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
