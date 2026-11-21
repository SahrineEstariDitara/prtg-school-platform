"""M3 PostgreSQL schema, seed idempotency, and partial unique index tests."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash

from app import create_app
from app.config import settings
from app.infrastructure.database import db
from app.infrastructure.database.seed import seed_baseline_data
from app.modules.auth.models import FIXED_ROLE_CODES, Role
from app.modules.incidents.models import (
    INCIDENT_AWAITING_RECOVERY,
    INCIDENT_DETECTED,
    INCIDENT_IN_PROGRESS,
    INCIDENT_RESOLVED,
    Incident,
)
from app.modules.regions.models import Region
from app.modules.schools.models import School
from app.modules.users.models import User


@pytest.fixture
def pg_app():
    """App bound to dedicated PostgreSQL test database with clean schema per test."""
    test_uri = settings.test_database_url
    if not test_uri:
        pytest.skip("TEST_DATABASE_URL belum dikonfigurasi.")

    flask_app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": test_uri})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


def test_all_operational_tables_created_in_postgresql(pg_app):
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    expected_tables = {
        "roles",
        "regions",
        "users",
        "user_regions",
        "schools",
        "prtg_devices",
        "prtg_sensors",
        "school_recovery_policies",
        "school_recovery_policy_sensors",
        "incidents",
        "incident_sensors",
        "incident_histories",
        "incident_assignments",
        "response_logs",
        "sync_operations",
        "verification_records",
        "prtg_recheck_logs",
        "device_tokens",
        "notification_logs",
        "audit_logs",
    }
    assert expected_tables.issubset(tables)


def test_seed_baseline_data_is_idempotent_and_hashes_admin_password(pg_app):
    first = seed_baseline_data(
        db.session,
        admin_username="seed-admin",
        admin_password="StrongPassword!123",
        admin_full_name="Admin Test",
    )
    assert first.roles_created == 5
    assert first.regions_created == 7
    assert first.admin_created is True

    second = seed_baseline_data(
        db.session,
        admin_username="seed-admin",
        admin_password="StrongPassword!123",
    )
    assert second.roles_created == 0
    assert second.regions_created == 0
    assert second.admin_created is False

    role_codes = {r.code for r in db.session.scalars(select(Role)).all()}
    assert role_codes == set(FIXED_ROLE_CODES)

    region_names = {r.name for r in db.session.scalars(select(Region)).all()}
    assert {
        "Bantul",
        "Gunung Kidul",
        "Kulon Progo",
        "Sleman",
        "Kota Jogja",
        "Khusus",
        "Belum Dikelompokkan",
    } == region_names

    admin_user = db.session.scalar(select(User).where(User.username == "seed-admin"))
    assert admin_user is not None
    assert admin_user.password_hash != "StrongPassword!123"
    assert check_password_hash(admin_user.password_hash, "StrongPassword!123")


def test_partial_unique_index_enforces_one_active_incident_per_school(pg_app):
    """FR-007 / NFR-03 / ADR-007: max 1 active incident per school; new allowed after resolved."""
    seed_baseline_data(db.session, admin_username="", admin_password="")
    bantul = db.session.scalar(select(Region).where(Region.code == "bantul"))
    assert bantul is not None

    school = School(
        name="SMA Negeri 1 Bantul",
        normalized_name="SMA NEGERI 1 BANTUL",
        region_id=bantul.id,
    )
    db.session.add(school)
    db.session.flush()

    inc1 = Incident(school_id=school.id, incident_status=INCIDENT_DETECTED)
    db.session.add(inc1)
    db.session.commit()

    # Second active incident for the same school MUST fail at PostgreSQL index level
    inc_dup = Incident(school_id=school.id, incident_status=INCIDENT_IN_PROGRESS)
    db.session.add(inc_dup)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    # After first incident becomes awaiting_recovery -> resolved, a new incident MUST succeed
    inc1.incident_status = INCIDENT_AWAITING_RECOVERY
    db.session.commit()
    inc1.incident_status = INCIDENT_RESOLVED
    db.session.commit()

    inc2 = Incident(school_id=school.id, incident_status=INCIDENT_DETECTED)
    db.session.add(inc2)
    db.session.commit()

    all_incidents = db.session.scalars(
        select(Incident).where(Incident.school_id == school.id)
    ).all()
    assert len(all_incidents) == 2
