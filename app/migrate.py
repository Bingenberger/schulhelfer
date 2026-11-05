"""Explicit, exclusive Alembic job. Run with all application writers paused."""
import argparse
import os
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from app.database import engine
from app.platform import data_lock, database_path, HEAD, SCHEMA


ADOPTABLE = ("b7c2e9a41d38", "a5ef5d7feb55")   # newest first


def _structure(connection):
    """Tables with columns, foreign keys and unique column sets, as SQLite reports them."""
    result = {}
    names = [row[0] for row in connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "AND name <> 'alembic_version'")]
    for table in sorted(names):
        quoted = '"' + table.replace('"', '""') + '"'
        columns = sorted((row[1], (row[2] or "").upper(), bool(row[3]), row[5])
                         for row in connection.exec_driver_sql(f"PRAGMA table_info({quoted})"))
        keys = sorted((row[2], row[3], row[4]) for row in connection.exec_driver_sql(f"PRAGMA foreign_key_list({quoted})"))
        uniques = set()
        for index in connection.exec_driver_sql(f"PRAGMA index_list({quoted})").fetchall():
            if index[2] and index[3] != "pk":
                name = '"' + index[1].replace('"', '""') + '"'
                uniques.add(tuple(sorted(row[2] for row in connection.exec_driver_sql(f"PRAGMA index_info({name})"))))
        result[table] = {"columns": columns, "foreignKeys": keys, "unique": sorted(uniques)}
    return result


def _reference(revision):
    """Structure that the bundled migrations produce at one revision (private in-memory database)."""
    from sqlalchemy import create_engine
    engine_ = create_engine("sqlite://")
    with engine_.connect() as connection:
        cfg = _config(connection)
        command.upgrade(cfg, revision)
        connection.commit()
        return _structure(connection)


def _config(connection):
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def adopt(connection):
    """Old installs built their tables with create_all and never stamped Alembic. Stamp only a database
    whose structure equals a known revision exactly; anything else is refused with the difference."""
    actual = _structure(connection)
    for revision in ADOPTABLE:
        if actual == _reference(revision):
            command.stamp(_config(connection), revision)
            return revision
    expected = _reference(ADOPTABLE[0])
    tables = sorted(set(actual) ^ set(expected)) or sorted(
        name for name in actual if actual[name] != expected.get(name))
    raise RuntimeError("Unversioned existing database differs from every known schema (tables: "
                       + ", ".join(tables[:8]) + "); reviewed adoption required")


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
                    adopted = adopt(connection)
                    print(f"Unversioned database adopted as {adopted} (structure verified).")
                command.upgrade(_config(connection), HEAD)
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
    except Exception as exc:
        # The message names schema facts only (revisions, table names), never data or secrets.
        raise SystemExit("Migration refused or failed; inspect the private database and release compatibility: "
                         + str(exc)[:500])
