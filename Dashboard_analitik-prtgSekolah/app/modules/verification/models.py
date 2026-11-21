"""Supervisor verification records and PRTG multi-check recheck logs (FR-017..019)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.extensions import db, utcnow

VERIFICATION_DECISION_VALUES = ("approved", "rejected")


class VerificationRecord(db.Model):
    __tablename__ = "verification_records"
    __table_args__ = (
        CheckConstraint("decision IN ('approved', 'rejected')", name="decision_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supervisor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    incident = relationship("Incident")
    supervisor = relationship("User")


class PrtgRecheckLog(db.Model):
    __tablename__ = "prtg_recheck_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    verification_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("verification_records.id"), nullable=True
    )
    check_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    all_required_normal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_valid_response: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    streak_after_check: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
