"""SQLAlchemy ORM schema for the LCFTP server."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Assignment",
    "Base",
    "BrokerStateRow",
    "CandidateRow",
    "CandidateSourceRow",
    "DeviceTokenRow",
    "FrameRow",
    "ObservatoryRow",
    "OutcomeRow",
    "PlanRow",
    "UploadBatchRow",
    "UserRow",
]

_TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    """Declarative base for all tables."""

    type_annotation_map = {dict[str, Any]: JSONB, datetime: _TS}  # noqa: RUF012


class UserRow(Base):
    """A GitHub user who has logged in."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_login: Mapped[str] = mapped_column(String(100), unique=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    created_at: Mapped[datetime]
    last_login: Mapped[datetime]


class ObservatoryRow(Base):
    """A registered observatory; the profile is stored as JSON."""

    __tablename__ = "observatories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    limiting_mag: Mapped[float] = mapped_column(Float, index=True)
    profile: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class DeviceTokenRow(Base):
    """A hashed device token for an observatory's client."""

    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observatory_id: Mapped[str] = mapped_column(
        ForeignKey("observatories.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime]
    last_used: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class CandidateRow(Base):
    """A cached candidate target, keyed by Rubin diaObjectId."""

    __tablename__ = "candidates"

    dia_object_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ra: Mapped[float]
    dec: Mapped[float]
    mag: Mapped[float] = mapped_column(Float, index=True)
    band: Mapped[str] = mapped_column(String(16))
    latest_alert_id: Mapped[str | None] = mapped_column(String(64))
    latest_alert_time: Mapped[datetime]
    classification: Mapped[str | None] = mapped_column(String(100))
    score: Mapped[float | None]
    first_seen: Mapped[datetime]
    last_updated: Mapped[datetime] = mapped_column(_TS, index=True)


class CandidateSourceRow(Base):
    """A broker's identity for a cached candidate."""

    __tablename__ = "candidate_sources"

    broker: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker_object_id: Mapped[str] = mapped_column(
        String(128), primary_key=True
    )
    dia_object_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.dia_object_id", ondelete="CASCADE"),
        index=True,
    )


class BrokerStateRow(Base):
    """Ingest progress and backoff state for one broker."""

    __tablename__ = "broker_state"

    broker: Mapped[str] = mapped_column(String(64), primary_key=True)
    high_water_mark: Mapped[datetime | None]
    last_success: Mapped[datetime | None]
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime | None]


class PlanRow(Base):
    """An issued observation plan; the plan itself is stored as JSON."""

    __tablename__ = "plans"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    observatory_id: Mapped[str] = mapped_column(
        ForeignKey("observatories.id", ondelete="CASCADE"), index=True
    )
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    created_at: Mapped[datetime]
    document: Mapped[dict[str, Any]]
    superseded_by: Mapped[str | None] = mapped_column(String(64))


class Assignment(Base):
    """A candidate assigned to an observatory for a night by a plan."""

    __tablename__ = "assignments"

    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plans.plan_id", ondelete="CASCADE"), primary_key=True
    )
    dia_object_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    observatory_id: Mapped[str] = mapped_column(String(64))
    night: Mapped[date] = mapped_column(Date, index=True)


class UploadBatchRow(Base):
    """A batch of frames a client is uploading."""

    __tablename__ = "upload_batches"

    batch_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    observatory_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]


class FrameRow(Base):
    """A frame's provenance record and upload status."""

    __tablename__ = "frames"

    frame_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_batches.batch_id"), index=True
    )
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    item_id: Mapped[str] = mapped_column(String(64))
    observatory_id: Mapped[str] = mapped_column(String(64), index=True)
    dia_object_id: Mapped[int] = mapped_column(BigInteger, index=True)
    object_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    record: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(16))
    received_at: Mapped[datetime | None]


class OutcomeRow(Base):
    """The outcome a client reported for one plan item."""

    __tablename__ = "outcomes"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    observatory_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(32))
    record: Mapped[dict[str, Any]]
    reported_at: Mapped[datetime]
