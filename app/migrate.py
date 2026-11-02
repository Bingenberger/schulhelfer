"""Explicit, exclusive Alembic job. Run with all application writers paused."""
import argparse
import os
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from app.database import engine
from app.platform import data_lock, database_path, HEAD, SCHEMA


def migrate():
    path = database_path()
    with data_lock("migration"):
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                version = connection.exec_driver_sql("PRAGMA user_version").scalar()
                if version not in (0, SCHEMA):
                    raise RuntimeError("Unsupported database schema")
                tables = set(inspect(connection).get_table_names()) - {"sqlite_sequence"}
                if tables and "alembic_version" not in tables:
                    raise RuntimeError("Unversioned existing database: reviewed adoption required")
                cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, HEAD)
                if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("Foreign key check failed")
                connection.exec_driver_sql("PRAGMA user_version=" + str(SCHEMA))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        path.chmod(0o600)


if __name__ == "__main__":
    try:
        migrate()
        print("Migration verified.")
    except Exception:
        raise SystemExit("Migration refused or failed; inspect the private database and release compatibility.")
