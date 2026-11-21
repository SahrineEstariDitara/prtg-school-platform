"""Fixed RBAC role and server-side refresh token revocation models (PRD §3, ADR-011)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.extensions import db, utcnow

ROLE_ADMIN = "admin"
ROLE_SUPERVISOR_UTAMA = "supervisor_utama"
ROLE_SUPERVISOR_WILAYAH = "supervisor_wilayah"
ROLE_KOORDINATOR_TEKNISI = "koordinator_teknisi"
ROLE_TEKNISI = "teknisi"

FIXED_ROLE_CODES = (
    ROLE_ADMIN,
    ROLE_SUPERVISOR_UTAMA,
    ROLE_SUPERVISOR_WILAYAH,
    ROLE_KOORDINATOR_TEKNISI,
    ROLE_TEKNISI,
)


class Role(db.Model):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Role {self.code}>"


class RefreshToken(db.Model):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
