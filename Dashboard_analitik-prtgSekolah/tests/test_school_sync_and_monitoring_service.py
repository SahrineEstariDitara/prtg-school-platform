"""M3 integration tests for SchoolSyncService and MonitoringService on PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import create_app
from app.config import settings
from app.infrastructure.database import db
from app.infrastructure.database.seed import seed_baseline_data
from app.modules.incidents.models import INCIDENT_IN_PROGRESS, Incident
from app.modules.monitoring.service import MonitoringService
from app.modules.regions.models import Region
from app.modules.schools.models import PrtgDevice, PrtgSensor, School
from app.modules.schools.normalizer import normalize_school_name
from app.modules.schools.sync_service import SchoolSyncService


@pytest.fixture
def pg_seeded():
    test_uri = settings.test_database_url
    if not test_uri:
        pytest.skip("TEST_DATABASE_URL belum dikonfigurasi.")

    flask_app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": test_uri})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        seed_baseline_data(db.session, admin_username="", admin_password="")
        yield flask_app
        db.session.remove()
        db.drop_all()


def test_normalize_school_name_unifies_m1_variants():
    assert normalize_school_name("SMA N 1 TURI (103.210.35.72)") == "SMA NEGERI 1 TURI"
    assert normalize_school_name("SMA N 1 TURI") == "SMA NEGERI 1 TURI"
    assert normalize_school_name("SIM063 SMA Negeri 01 Gamping") == "SMA NEGERI 1 GAMPING"
    assert normalize_school_name("SMA N 1 GAMPING (103.210.35.67)") == "SMA NEGERI 1 GAMPING"


def test_school_sync_groups_multi_device_school_and_monitoring_separates_states(pg_seeded):
    """Verify multi-device deduplication, region scoping, stale detection, and FR-008 separation."""
    now = datetime(2026, 11, 21, 14, 0, 0, tzinfo=timezone.utc)
    sync_service = SchoolSyncService()

    snapshot = [
        # Two different PRTG devices for the SAME school: SMA Negeri 1 Turi (Sleman)
        {
            "objid": 5001,
            "parentid": 16278,
            "group": "DIY / Sleman",
            "device": "SMA N 1 TURI (103.210.35.72)",
            "sensor": "Ping",
            "status": "Down",
            "status_raw": 5,
            "downtimesince": "2 h",
            "message": "Timeout",
        },
        {
            "objid": 5002,
            "parentid": 25256,
            "group": "DIY / Sleman",
            "device": "SMA N 1 TURI",
            "sensor": "SNMP Traffic",
            "status": "Up",
            "status_raw": 3,
            "uptimesince": "4 h",
            "message": "OK",
        },
        # Online school in Bantul
        {
            "objid": 5003,
            "parentid": 3118,
            "group": "DIY / Bantul",
            "device": "SMA NEGERI 2 BANTUL (103.210.35.35)",
            "sensor": "Ping",
            "status": "Up",
            "status_raw": 3,
            "uptimesince": "3 d",
            "message": "OK",
        },
        # Non-school core router MUST be ignored
        {
            "objid": 5999,
            "parentid": 99999,
            "group": "BORDER ROUTER",
            "device": "CORE-BORDER-DIY-01",
            "sensor": "Ping",
            "status": "Down",
            "status_raw": 5,
        },
    ]

    summary = sync_service.sync_snapshot(db.session, snapshot, checked_at=now)
    assert summary.sensors_seen == 4
    assert summary.school_sensors_synced == 3
    assert summary.schools_created == 2  # SMA N 1 Turi (merged) + SMA N 2 Bantul
    assert summary.devices_created == 3
    assert summary.sensors_created == 3

    turi = db.session.scalar(
        select(School).where(School.normalized_name == "SMA NEGERI 1 TURI")
    )
    assert turi is not None
    turi_devices = db.session.scalars(
        select(PrtgDevice).where(PrtgDevice.school_id == turi.id)
    ).all()
    assert len(turi_devices) == 2

    # Create an active incident for Bantul even though its network_status is online (FR-008 separation!)
    bantul_school = db.session.scalar(
        select(School).where(School.normalized_name == "SMA NEGERI 2 BANTUL")
    )
    assert bantul_school is not None
    inc = Incident(school_id=bantul_school.id, incident_status=INCIDENT_IN_PROGRESS)
    db.session.add(inc)
    db.session.commit()

    mon = MonitoringService(stale_threshold_seconds=180)
    dash = mon.get_dashboard_summary(db.session, now=now + timedelta(seconds=30))
    assert dash["total_schools"] == 2
    assert dash["counts"]["offline"] == 1
    assert dash["counts"]["online"] == 1
    assert dash["stale_schools"] == 0
    assert dash["active_incidents"] == 1

    # Verify Bantul school displays network_status="online" AND active_incident="in_progress" separately
    detail = mon.get_school_detail(db.session, bantul_school.id, now=now + timedelta(seconds=30))
    assert detail is not None
    assert detail["network_status"] == "online"
    assert detail["active_incident"]["incident_status"] == "in_progress"
    assert detail["is_stale"] is False

    # Verify stale detection when data is older than 180s
    stale_dash = mon.get_dashboard_summary(db.session, now=now + timedelta(seconds=300))
    assert stale_dash["stale_schools"] == 2

    # Verify region scoping (Sleman only)
    sleman = db.session.scalar(select(Region).where(Region.code == "sleman"))
    assert sleman is not None
    sleman_list = mon.list_schools(db.session, region_ids=[sleman.id], now=now + timedelta(seconds=10))
    assert sleman_list["pagination"]["total"] == 1
    assert sleman_list["items"][0]["id"] == turi.id

    # Verify re-sync updates sensor status in-place without duplicating schools/devices/sensors
    snapshot[0]["status"] = "Up"
    snapshot[0]["status_raw"] = 3
    resync = sync_service.sync_snapshot(db.session, snapshot, checked_at=now + timedelta(seconds=60))
    assert resync.schools_created == 0
    assert resync.devices_created == 0
    assert resync.sensors_created == 0
    sensor_5001 = db.session.scalar(select(PrtgSensor).where(PrtgSensor.prtg_sensor_id == "5001"))
    assert sensor_5001 is not None
    assert sensor_5001.normalized_status == "online"
