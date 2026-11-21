"""Incident, affected sensors, and immutable history models (FR-007, ADR-007)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.extensions import db, utcnow

INCIDENT_DETECTED = "detected"
INCIDENT_UNASSIGNED = "unassigned"
INCIDENT_ASSIGNED = "assigned"
INCIDENT_ACKNOWLEDGED = "acknowledged"
INCIDENT_IN_PROGRESS = "in_progress"
INCIDENT_PENDING_VERIFICATION = "pending_verification"
INCIDENT_AWAITING_RECOVERY = "awaiting_recovery"
INCIDENT_RESOLVED = "resolved"

INCIDENT_STATUS_VALUES = (
    INCIDENT_DETECTED,
    INCIDENT_UNASSIGNED,
    INCIDENT_ASSIGNED,
    INCIDENT_ACKNOWLEDGED,
    INCIDENT_IN_PROGRESS,
    INCIDENT_PENDING_VERIFICATION,
    INCIDENT_AWAITING_RECOVERY,
    INCIDENT_RESOLVED,
)


class Incident(db.Model):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            "incident_status IN ("
            "'detected','unassigned','assigned','acknowledged',"
            "'in_progress','pending_verification','awaiting_recovery','resolved'"
            ")",
            name="incident_status_valid",
        ),
        CheckConstraint("recovery_stable_streak >= 0", name="stable_streak_non_negative"),
        Index(
            "uq_incidents_one_active_per_school",
            "school_id",
            unique=True,
            postgresql_where=text("incident_status != 'resolved'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    incident_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=INCIDENT_DETECTED, index=True
    )
    current_assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    recovery_stable_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    school = relationship("School")
    current_assignee = relationship("User", foreign_keys=[current_assignee_id])
    affected_sensors = relationship(
        "IncidentSensor", back_populates="incident", cascade="all, delete-orphan"
    )
    histories = relationship(
        "IncidentHistory", back_populates="incident", cascade="all, delete-orphan"
    )


class IncidentSensor(db.Model):
    __tablename__ = "incident_sensors"

    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    prtg_sensor_id: Mapped[int] = mapped_column(
        ForeignKey("prtg_sensors.id", ondelete="CASCADE"), primary_key=True
    )
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_down_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    incident = relationship("Incident", back_populates="affected_sensors")
    sensor = relationship("PrtgSensor")


class IncidentHistory(db.Model):
    __tablename__ = "incident_histories"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    incident = relationship("Incident", back_populates="histories")
