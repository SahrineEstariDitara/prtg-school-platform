"""School, PRTG device/sensor, and recovery policy models (ADR-005, ADR-006, ADR-009)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.extensions import db, utcnow

NETWORK_STATUS_ONLINE = "online"
NETWORK_STATUS_OFFLINE = "offline"
NETWORK_STATUS_WARNING = "warning"
NETWORK_STATUS_UNKNOWN = "unknown"

NETWORK_STATUS_VALUES = (
    NETWORK_STATUS_ONLINE,
    NETWORK_STATUS_OFFLINE,
    NETWORK_STATUS_WARNING,
    NETWORK_STATUS_UNKNOWN,
)


class School(db.Model):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_code: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    region = relationship("Region", lazy="joined")
    devices = relationship("PrtgDevice", back_populates="school", cascade="all, delete-orphan")
    recovery_policy = relationship(
        "SchoolRecoveryPolicy",
        back_populates="school",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<School {self.id}:{self.name}>"


class PrtgDevice(db.Model):
    __tablename__ = "prtg_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    prtg_device_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    school_id: Mapped[int] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prtg_group_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    school = relationship("School", back_populates="devices")
    sensors = relationship("PrtgSensor", back_populates="device", cascade="all, delete-orphan")


class PrtgSensor(db.Model):
    __tablename__ = "prtg_sensors"
    __table_args__ = (
        CheckConstraint(
            "normalized_status IN ('online', 'offline', 'warning', 'unknown')",
            name="normalized_status_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prtg_sensor_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    prtg_device_id: Mapped[int] = mapped_column(
        ForeignKey("prtg_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sensor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    raw_status: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    raw_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NETWORK_STATUS_UNKNOWN, index=True
    )
    downtime_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    uptime_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    device = relationship("PrtgDevice", back_populates="sensors")


class SchoolRecoveryPolicy(db.Model):
    __tablename__ = "school_recovery_policies"
    __table_args__ = (
        CheckConstraint("required_consecutive_normal >= 1", name="consecutive_normal_min"),
        CheckConstraint("recheck_interval_seconds >= 10", name="recheck_interval_min"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    required_consecutive_normal: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    recheck_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    school = relationship("School", back_populates="recovery_policy")
    required_sensors = relationship(
        "SchoolRecoveryPolicySensor",
        back_populates="policy",
        cascade="all, delete-orphan",
    )


class SchoolRecoveryPolicySensor(db.Model):
    __tablename__ = "school_recovery_policy_sensors"
    __table_args__ = (
        CheckConstraint(
            "expected_status IN ('online', 'warning')",
            name="expected_status_valid",
        ),
    )

    policy_id: Mapped[int] = mapped_column(
        ForeignKey("school_recovery_policies.id", ondelete="CASCADE"), primary_key=True
    )
    prtg_sensor_id: Mapped[int] = mapped_column(
        ForeignKey("prtg_sensors.id", ondelete="CASCADE"), primary_key=True
    )
    expected_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NETWORK_STATUS_ONLINE
    )

    policy = relationship("SchoolRecoveryPolicy", back_populates="required_sensors")
    sensor = relationship("PrtgSensor")
