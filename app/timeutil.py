"""Zeitrechnung in lokaler Zeit.

Alle Datums-/Zeitfelder in der Datenbank sind naive lokale Wandzeit
(Europe/Berlin): so, wie sie im Admin eingetippt und auf der Seite
angezeigt werden. Vergleiche müssen deshalb ebenfalls gegen die lokale
Zeit laufen – nicht gegen ``datetime.utcnow()``.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import settings


def tz() -> ZoneInfo:
    return ZoneInfo(settings.TIMEZONE)


def now_local() -> datetime:
    """Aktuelle lokale Zeit als naives datetime – vergleichbar mit DB-Werten."""
    return datetime.now(tz()).replace(tzinfo=None)


def today_local() -> date:
    return now_local().date()


def parse_local(value: str) -> datetime | None:
    """``datetime-local``-Eingabe robust in ein naives datetime wandeln."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def fmt_dt(value: datetime | None, pattern: str = "%d.%m.%Y %H:%M") -> str:
    return value.strftime(pattern) if value else "–"
