"""M5 tests: incident correlation, deduplication, auto-assignment tie-breaker, and manual reassignment (TC-05..TC-07)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app import create_app
from app.config import settings
from app.infrastructure.database import db
from app.infrastructure.database.seed import seed_baseline_data
from app.modules.assignments.models import IncidentAssignment
from app.modules.auth.models import (
    ROLE_ADMIN,
    ROLE_KOORDINATOR_TEKNISI,
    ROLE_SUPERVISOR_UTAMA,
    ROLE_SUPERVISOR_WILAYAH,
    ROLE_TEKNISI,
    Role,
)
from app.modules.incidents.models import INCIDENT_ASSIGNED, INCIDENT_UNASSIGNED, Incident
from app.modules.incidents.service import correlate_down_schools
from app.modules.notifications.models import NotificationLog
from app.modules.regions.models import Region, UserRegion
from app.modules.schools.models import PrtgDevice, PrtgSensor, School
from app.modules.users.models import User


def _auth_headers(client, username: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": "Password!123"})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.get_json()['data']['access_token']}"}


@pytest.fixture
def m5_env():
    test_uri = settings.test_database_url
    if not test_uri:
        pytest.skip("TEST_DATABASE_URL belum dikonfigurasi.")

    flask_app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": test_uri})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        seed_baseline_data(
            db.session,
            admin_username="admin_m5",
            admin_password="Password!123",
            admin_full_name="Admin M5",
        )
        roles = {r.code: r for r in db.session.scalars(select(Role)).all()}
        regions = {r.code: r for r in db.session.scalars(select(Region)).all()}

        def mk_user(username, role_code, region_codes, *, duty=False, last_assigned=None):
            u = User(
                username=username,
                full_name=f"User {username}",
                password_hash=generate_password_hash("Password!123"),
                role_id=roles[role_code].id,
                is_active=True,
                is_on_duty=duty,
                last_assigned_at=last_assigned,
            )
            db.session.add(u)
            db.session.flush()
            for rc in region_codes:
                db.session.add(UserRegion(user_id=u.id, region_id=regions[rc].id))
            return u

        now = datetime(2026, 11, 21, 10, 0, 0, tzinfo=timezone.utc)
        mk_user("sup_utama", ROLE_SUPERVISOR_UTAMA, [])
        mk_user("sup_bantul", ROLE_SUPERVISOR_WILAYAH, ["bantul"])
        mk_user("koor_bantul", ROLE_KOORDINATOR_TEKNISI, ["bantul"])
        mk_user("koor_sleman", ROLE_KOORDINATOR_TEKNISI, ["sleman"])

        # Two on-duty technicians in Bantul with EQUAL workload (0) to test tie-breaker:
        # tek_recent was assigned 1 hour ago; tek_older was assigned 5 hours ago -> tek_older MUST win!
        tek_recent = mk_user(
            "tek_recent", ROLE_TEKNISI, ["bantul"], duty=True, last_assigned=now - timedelta(hours=1)
        )
        tek_older = mk_user(
            "tek_older",
            ROLE_TEKNISI,
            ["bantul", "kota_jogja"],  # multi-region technician
            duty=True,
            last_assigned=now - timedelta(hours=5),
        )
        # Sleman has ONLY an off-duty technician -> should go to `unassigned` queue!
        mk_user("tek_sleman_off", ROLE_TEKNISI, ["sleman"], duty=False)

        # School 1 (Bantul) with 2 offline sensors across 2 devices -> MUST create only 1 Incident
        sch_bantul = School(
            name="SMA Negeri 1 Bantul",
            normalized_name="SMA NEGERI 1 BANTUL",
            region_id=regions["bantul"].id,
        )
        sch_sleman = School(
            name="SMA Negeri 1 Sleman",
            normalized_name="SMA NEGERI 1 SLEMAN",
            region_id=regions["sleman"].id,
        )
        db.session.add_all([sch_bantul, sch_sleman])
        db.session.flush()

        d1 = PrtgDevice(prtg_device_id="101", school_id=sch_bantul.id, raw_name="SMA N 1 Bantul Router")
        d2 = PrtgDevice(prtg_device_id="102", school_id=sch_bantul.id, raw_name="SMA N 1 Bantul CCR")
        d3 = PrtgDevice(prtg_device_id="103", school_id=sch_sleman.id, raw_name="SMA N 1 Sleman")
        db.session.add_all([d1, d2, d3])
        db.session.flush()

        db.session.add_all(
            [
                PrtgSensor(prtg_sensor_id="1001", prtg_device_id=d1.id, sensor_name="Ping", raw_status="Down", normalized_status="offline", last_checked_at=now),
                PrtgSensor(prtg_sensor_id="1002", prtg_device_id=d2.id, sensor_name="Uplink", raw_status="Down", normalized_status="offline", last_checked_at=now),
                PrtgSensor(prtg_sensor_id="1003", prtg_device_id=d3.id, sensor_name="Ping", raw_status="Down", normalized_status="offline", last_checked_at=now),
            ]
        )
        db.session.commit()

        with flask_app.test_client() as client:
            yield {
                "client": client,
                "sch_bantul": sch_bantul,
                "sch_sleman": sch_sleman,
                "tek_recent": tek_recent,
                "tek_older": tek_older,
                "now": now,
            }

        db.session.remove()
        db.drop_all()


def test_correlation_deduplicates_multi_sensor_and_applies_tie_breaker_and_unassigned_fallback(m5_env):
    """TC-05 & TC-06: 1 incident per school, tie-breaker picks least recently assigned, empty duty -> unassigned."""
    first = correlate_down_schools(db.session, now=m5_env["now"])
    assert first.schools_offline_seen == 2
    assert first.incidents_created == 2
    assert first.sensors_linked == 3

    # Re-running correlation MUST NOT create duplicate incidents or duplicate notifications (TC-05)
    notif_count_before = len(db.session.scalars(select(NotificationLog)).all())
    second = correlate_down_schools(db.session, now=m5_env["now"] + timedelta(minutes=1))
    assert second.incidents_created == 0
    assert second.incidents_updated == 2
    assert len(db.session.scalars(select(NotificationLog)).all()) == notif_count_before

    inc_bantul = db.session.scalar(select(Incident).where(Incident.school_id == m5_env["sch_bantul"].id))
    assert inc_bantul is not None
    assert inc_bantul.incident_status == INCIDENT_ASSIGNED
    # Tie-breaker (ADR-013): tek_older (last assigned 5h ago) wins over tek_recent (1h ago)
    assert inc_bantul.current_assignee_id == m5_env["tek_older"].id

    inc_sleman = db.session.scalar(select(Incident).where(Incident.school_id == m5_env["sch_sleman"].id))
    assert inc_sleman is not None
    assert inc_sleman.incident_status == INCIDENT_UNASSIGNED
    assert inc_sleman.current_assignee_id is None


def test_manual_reassignment_enforces_scope_requires_reason_and_preserves_history(m5_env):
    """TC-07 & AC-07: Koordinator reassigns in-scope with reason; history kept; Admin/out-of-scope -> 403."""
    correlate_down_schools(db.session, now=m5_env["now"])
    inc_bantul = db.session.scalar(select(Incident).where(Incident.school_id == m5_env["sch_bantul"].id))
    assert inc_bantul is not None

    client = m5_env["client"]
    koor_bantul_h = _auth_headers(client, "koor_bantul")
    koor_sleman_h = _auth_headers(client, "koor_sleman")
    admin_h = _auth_headers(client, "admin_m5")

    # Admin CANNOT assign (403)
    assert (
        client.post(
            f"/api/v1/incidents/{inc_bantul.id}/assign",
            headers=admin_h,
            json={"technician_id": m5_env["tek_recent"].id, "reason": "Test"},
        ).status_code
        == 403
    )

    # Out-of-scope Koordinator Sleman CANNOT assign Bantul incident (403)
    assert (
        client.post(
            f"/api/v1/incidents/{inc_bantul.id}/assign",
            headers=koor_sleman_h,
            json={"technician_id": m5_env["tek_recent"].id, "reason": "Cross region"},
        ).status_code
        == 403
    )

    # Blank reason MUST fail 422
    assert (
        client.post(
            f"/api/v1/incidents/{inc_bantul.id}/assign",
            headers=koor_bantul_h,
            json={"technician_id": m5_env["tek_recent"].id, "reason": "   "},
        ).status_code
        == 422
    )

    # Valid reassignment by Koordinator Bantul
    res = client.post(
        f"/api/v1/incidents/{inc_bantul.id}/assign",
        headers=koor_bantul_h,
        json={"technician_id": m5_env["tek_recent"].id, "reason": "Teknisi awal sedang ke lokasi lain"},
    )
    assert res.status_code == 200
    assert res.get_json()["data"]["assignment_type"] == "reassign"

    # Both historical auto assignment (inactive) and new reassign (active) MUST exist
    assignments = db.session.scalars(
        select(IncidentAssignment)
        .where(IncidentAssignment.incident_id == inc_bantul.id)
        .order_by(IncidentAssignment.id.asc())
    ).all()
    assert len(assignments) == 2
    assert assignments[0].is_active is False and assignments[0].ended_at is not None
    assert assignments[1].is_active is True and assignments[1].technician_id == m5_env["tek_recent"].id

    # Koordinator can query technician workload list
    techs_resp = client.get("/api/v1/technicians", headers=koor_bantul_h)
    assert techs_resp.status_code == 200
    assert techs_resp.get_json()["data"]["total"] == 2
