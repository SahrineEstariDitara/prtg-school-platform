"""Idempotent UAT & Demo account seeder covering all 5 fixed roles (ADR-011, ADR-012).

Creates 6 representative accounts for local/staging UAT and mobile/web E2E demonstrations:
- admin (seeded via seed_baseline_data)
- spv_utama (supervisor_utama, global scope)
- spv_bantul (supervisor_wilayah, 1 region: Bantul)
- koor_bantul (koordinator_teknisi, 1 region: Bantul)
- teknisi_bantul (teknisi, multi-region: Bantul + Kota Jogja, on_duty=True)
- teknisi_sleman (teknisi, Sleman, on_duty=True — for TC-02 cross-region boundary tests)
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

# Ensure `app.*` imports resolve when executed directly via python path/to/seed_uat.py
BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.infrastructure.database.seed import seed_baseline_data
from app.modules.auth.models import (
    ROLE_KOORDINATOR_TEKNISI,
    ROLE_SUPERVISOR_UTAMA,
    ROLE_SUPERVISOR_WILAYAH,
    ROLE_TEKNISI,
    Role,
)
from app.modules.regions.models import Region, UserRegion
from app.modules.users.models import User

DEFAULT_UAT_PASSWORD = "UatDemo!2026"

UAT_DEMO_ACCOUNTS: tuple[tuple[str, str, str, tuple[str, ...], bool], ...] = (
    ("spv_utama", "Supervisor Utama DIY", ROLE_SUPERVISOR_UTAMA, (), False),
    ("spv_bantul", "Supervisor Wilayah Bantul", ROLE_SUPERVISOR_WILAYAH, ("bantul",), False),
    ("koor_bantul", "Koordinator Teknisi Bantul", ROLE_KOORDINATOR_TEKNISI, ("bantul",), False),
    (
        "teknisi_bantul",
        "Teknisi Lapangan Bantul-Jogja",
        ROLE_TEKNISI,
        ("bantul", "kota_jogja"),
        True,
    ),
    ("teknisi_sleman", "Teknisi Lapangan Sleman", ROLE_TEKNISI, ("sleman",), True),
)


@dataclass(frozen=True)
class UatSeedSummary:
    users_created: int
    users_existing: int
    usernames: tuple[str, ...]


def seed_uat_demo_accounts(
    session: Session,
    *,
    demo_password: Optional[str] = None,
) -> UatSeedSummary:
    """Idempotently seed baseline roles/regions and 5-role UAT accounts."""
    seed_baseline_data(session)

    raw_pw = (
        demo_password
        if demo_password is not None
        else (os.environ.get("UAT_DEMO_PASSWORD") or DEFAULT_UAT_PASSWORD)
    )
    pw_hash = generate_password_hash(raw_pw)

    roles_by_code = {r.code: r for r in session.scalars(select(Role)).all()}
    regions_by_code = {r.code: r for r in session.scalars(select(Region)).all()}

    users_created = 0
    users_existing = 0
    usernames: list[str] = []

    for username, full_name, role_code, region_codes, is_on_duty in UAT_DEMO_ACCOUNTS:
        usernames.append(username)
        existing = session.scalar(select(User).where(User.username == username))
        if existing is not None:
            users_existing += 1
            continue

        role = roles_by_code.get(role_code)
        if role is None:
            continue

        user = User(
            username=username,
            full_name=full_name,
            password_hash=pw_hash,
            role_id=role.id,
            is_active=True,
            is_on_duty=is_on_duty,
        )
        session.add(user)
        session.flush()

        for r_code in region_codes:
            region = regions_by_code.get(r_code)
            if region is not None:
                session.add(UserRegion(user_id=user.id, region_id=region.id))

        users_created += 1

    session.commit()
    return UatSeedSummary(
        users_created=users_created,
        users_existing=users_existing,
        usernames=tuple(usernames),
    )


def main() -> None:
    from app import create_app
    from app.infrastructure.database.extensions import db

    flask_app = create_app()
    with flask_app.app_context():
        summary = seed_uat_demo_accounts(db.session)
        print(
            f"UAT seed selesai: created={summary.users_created}, "
            f"existing={summary.users_existing}, accounts={', '.join(summary.usernames)}"
        )


if __name__ == "__main__":
    main()
