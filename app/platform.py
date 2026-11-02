"""Local runtime contract. SQLite jobs and scheduler share a private data root."""
import fcntl
import os
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import text
from sqlalchemy.engine import make_url
from app.config import settings

HEAD = "b7c2e9a41d38"
SCHEMA = 1
os.umask(0o077)


def database_path():
    url = make_url(settings.DATABASE_URL)
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        raise RuntimeError("This release requires a file-backed SQLite database")
    return Path(url.database).resolve()


@contextmanager
def data_lock(name):
    root = database_path().parent
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / (name + ".lock")).open("a") as stream:
        os.chmod(stream.name, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def schema_ready():
    from app.database import engine
    if not database_path().is_file():
        return False
    try:
        with engine.connect() as connection:
            return (connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [HEAD]
                    and connection.exec_driver_sql("PRAGMA user_version").scalar() == SCHEMA
                    and connection.exec_driver_sql("SELECT 1").scalar() == 1)
    except Exception:
        return False
