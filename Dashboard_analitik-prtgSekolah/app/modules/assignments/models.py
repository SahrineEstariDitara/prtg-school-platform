"""Historical incident assignment model (preserves every assignment/reassignment)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.extensions import db, utcnow

ASSIGNMENT_TYPE_VALUES = ("auto", "manual", "reassign")


class IncidentAssignment(db.Model):
    __tablename__ = "incident_assignments"
    __table_args__ = (
        CheckConstraint(
            "assignment_type IN ('auto', 'manual', 'reassign')",
            name="assignment_type_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    technician_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    assigned_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assignment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    incident = relationship("Incident")
    technician = relationship("User", foreign_keys=[technician_id])
    assigned_by = relationship("User", foreign_keys=[assigned_by_id])
