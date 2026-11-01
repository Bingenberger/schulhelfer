from datetime import datetime
from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime, Date,
    ForeignKey, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)


class Klasse(Base):
    __tablename__ = "klasse"
    __table_args__ = (UniqueConstraint("label", "school_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    school_year: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    participants: Mapped[list["Participant"]] = relationship(back_populates="klasse")


class Event(Base):
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    event_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    location: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    registration_deadline: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    access_code: Mapped[str | None] = mapped_column(String)
    retention_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    stations: Mapped[list["Station"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="Station.sort_order"
    )
    participants: Mapped[list["Participant"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class Station(Base):
    __tablename__ = "station"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("event.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # helper|donation
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    target_count: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    event: Mapped["Event"] = relationship(back_populates="stations")
    shifts: Mapped[list["Shift"]] = relationship(
        back_populates="station", cascade="all, delete-orphan", order_by="Shift.sort_order"
    )
    donations: Mapped[list["Donation"]] = relationship(back_populates="station")


class Shift(Base):
    __tablename__ = "shift"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(Integer, ForeignKey("station.id"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    required_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    station: Mapped["Station"] = relationship(back_populates="shifts")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="shift", cascade="all, delete-orphan")


class Participant(Base):
    __tablename__ = "participant"
    __table_args__ = (UniqueConstraint("event_id", "email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("event.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    klasse_id: Mapped[int] = mapped_column(Integer, ForeignKey("klasse.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    # Der Zugangslink wird den Eltern als Lesezeichen empfohlen und muss deshalb
    # über die gesamte Laufzeit stabil bleiben – auch beim Veröffentlichen, wo die
    # Bestätigungsmail ihn erneut versendet. Dafür wird das Token zusätzlich im
    # Klartext gehalten; der Hash bleibt der Nachschlageschlüssel.
    token: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    event: Mapped["Event"] = relationship(back_populates="participants")
    klasse: Mapped["Klasse"] = relationship(back_populates="participants")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="participant", cascade="all, delete-orphan")
    donations: Mapped[list["Donation"]] = relationship(back_populates="participant", cascade="all, delete-orphan")
    email_logs: Mapped[list["EmailLog"]] = relationship(back_populates="participant")


class Booking(Base):
    __tablename__ = "booking"
    __table_args__ = (UniqueConstraint("participant_id", "shift_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    participant_id: Mapped[int] = mapped_column(Integer, ForeignKey("participant.id"), nullable=False)
    shift_id: Mapped[int] = mapped_column(Integer, ForeignKey("shift.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    participant: Mapped["Participant"] = relationship(back_populates="bookings")
    shift: Mapped["Shift"] = relationship(back_populates="bookings")


class Donation(Base):
    __tablename__ = "donation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    participant_id: Mapped[int] = mapped_column(Integer, ForeignKey("participant.id"), nullable=False)
    station_id: Mapped[int] = mapped_column(Integer, ForeignKey("station.id"), nullable=False)
    item_text: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    participant: Mapped["Participant"] = relationship(back_populates="donations")
    station: Mapped["Station"] = relationship(back_populates="donations")


class EmailLog(Base):
    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    participant_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("participant.id"))
    kind: Mapped[str] = mapped_column(String, nullable=False)  # access_link|recovery|confirmation
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)

    participant: Mapped["Participant | None"] = relationship(back_populates="email_logs")
