"""Technician response log and idempotent offline sync models (FR-013, FR-015)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.extensions import db, utcnow

SYNC_STATUS_VALUES = ("draft", "pending_sync", "synced", "conflict", "failed")


class ResponseLog(db.Model):
    __tablename__ = "response_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    action_summary: Mapped[str] = mapped_column(String(255), nullable=False)
    result_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attachment_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_operation_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    incident = relationship("Incident")
    actor = relationship("User")


class SyncOperation(db.Model):
    __tablename__ = "sync_operations"
    __table_args__ = (
        CheckConstraint(
            "sync_status IN ('draft', 'pending_sync', 'synced', 'conflict', 'failed')",
            name="sync_status_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_operation_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending_sync")
    conflict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
