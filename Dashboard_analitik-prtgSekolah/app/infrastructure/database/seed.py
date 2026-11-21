"""Idempotent baseline seeding for 5 fixed roles, 6 DIY regions + fallback, and initial Admin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app.config import settings
from app.modules.auth.models import (
    ROLE_ADMIN,
    ROLE_KOORDINATOR_TEKNISI,
    ROLE_SUPERVISOR_UTAMA,
    ROLE_SUPERVISOR_WILAYAH,
    ROLE_TEKNISI,
    Role,
)
from app.modules.regions.models import Region
from app.modules.users.models import User

SEED_ROLES = (
    (ROLE_ADMIN, "Admin", "Administrasi akun, 5 role tetap, wilayah, konfigurasi, monitoring global (tanpa verifikasi incident)."),
    (ROLE_SUPERVISOR_UTAMA, "Supervisor Utama", "Monitoring global, override assignment, verifikasi incident lintas wilayah."),
    (ROLE_SUPERVISOR_WILAYAH, "Supervisor Wilayah", "Monitoring, penugasan, dan verifikasi incident pada 1 wilayah yang ditetapkan."),
    (ROLE_KOORDINATOR_TEKNISI, "Koordinator Teknisi", "Penugasan/reassignment dan pantau beban tim pada 1 wilayah yang ditetapkan."),
    (ROLE_TEKNISI, "Teknisi", "Penanganan incident, acknowledge, response log, draft offline, dan resolution submission pada wilayah tugas."),
)

SEED_REGIONS = (
    ("bantul", "Bantul", False),
    ("gunung_kidul", "Gunung Kidul", False),
    ("kulon_progo", "Kulon Progo", False),
    ("sleman", "Sleman", False),
    ("kota_jogja", "Kota Jogja", False),
    ("khusus", "Khusus", False),
    ("belum_dikelompokkan", "Belum Dikelompokkan", True),
)


@dataclass(frozen=True)
class SeedSummary:
    roles_created: int
    regions_created: int
    admin_created: bool


def seed_baseline_data(
    session: Session,
    *,
    admin_username: Optional[str] = None,
    admin_password: Optional[str] = None,
    admin_full_name: Optional[str] = None,
) -> SeedSummary:
    """Insert fixed roles, DIY regions, and initial Admin account if not already present."""
    roles_created = 0
    for code, name, description in SEED_ROLES:
        existing = session.scalar(select(Role).where(Role.code == code))
        if existing is None:
            session.add(Role(code=code, name=name, description=description))
            roles_created += 1

    regions_created = 0
    for code, name, is_fallback in SEED_REGIONS:
        existing = session.scalar(select(Region).where(Region.code == code))
        if existing is None:
            session.add(Region(code=code, name=name, is_fallback=is_fallback, is_active=True))
            regions_created += 1

    session.flush()

    username = (admin_username if admin_username is not None else settings.admin_seed_username).strip()
    password = admin_password if admin_password is not None else settings.admin_seed_password
    full_name = (
        admin_full_name if admin_full_name is not None else settings.admin_seed_full_name
    ).strip() or "Administrator Sistem"

    admin_created = False
    if username and password and not password.startswith("ISI_"):
        existing_admin = session.scalar(select(User).where(User.username == username))
        if existing_admin is None:
            admin_role = session.scalar(select(Role).where(Role.code == ROLE_ADMIN))
            if admin_role is not None:
                session.add(
                    User(
                        username=username,
                        full_name=full_name,
                        password_hash=generate_password_hash(password),
                        role_id=admin_role.id,
                        is_active=True,
                        is_on_duty=False,
                    )
                )
                admin_created = True

    session.commit()
    return SeedSummary(
        roles_created=roles_created,
        regions_created=regions_created,
        admin_created=admin_created,
    )


def main() -> None:
    from app import create_app
    from app.infrastructure.database.extensions import db

    flask_app = create_app()
    with flask_app.app_context():
        summary = seed_baseline_data(db.session)
        print(
            f"Seed selesai: roles_created={summary.roles_created}, "
            f"regions_created={summary.regions_created}, "
            f"admin_created={summary.admin_created}"
        )


if __name__ == "__main__":
    main()
