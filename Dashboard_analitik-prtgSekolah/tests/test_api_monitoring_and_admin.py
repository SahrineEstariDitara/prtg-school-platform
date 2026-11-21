"""M4 API monitoring (Opsi A), Admin CRUD, ADR-012 region rules, AuditLog, and OpenAPI tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import create_app
from app.config import settings
from app.infrastructure.database import db
from app.infrastructure.database.seed import seed_baseline_data
from app.modules.regions.models import Region
from app.modules.schools.models import PrtgDevice, PrtgSensor, School


@pytest.fixture
def admin_api():
    test_uri = settings.test_database_url
    if not test_uri:
        pytest.skip("TEST_DATABASE_URL belum dikonfigurasi.")

    flask_app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": test_uri})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        seed_baseline_data(
            db.session,
            admin_username="admin_m4",
            admin_password="AdminPass!123",
            admin_full_name="Admin M4",
        )
        regions = {r.code: r for r in db.session.scalars(select(Region)).all()}
        now = datetime.now(timezone.utc)

        # School 1: 1 Up sensor + 1 Unusual sensor -> Opsi A should classify school as "online"
        sch1 = School(
            name="SMA Negeri 1 Bantul",
            normalized_name="SMA NEGERI 1 BANTUL",
            region_id=regions["bantul"].id,
        )
        # School 2: 1 genuine Warning sensor -> should classify school as "warning"
        sch2 = School(
            name="SMK Negeri 1 Gedangsari",
            normalized_name="SMK NEGERI 1 GEDANGSARI",
            region_id=regions["gunung_kidul"].id,
        )
        db.session.add_all([sch1, sch2])
        db.session.flush()

        dev1 = PrtgDevice(prtg_device_id="9001", school_id=sch1.id, raw_name=sch1.name)
        dev2 = PrtgDevice(prtg_device_id="9002", school_id=sch2.id, raw_name=sch2.name)
        db.session.add_all([dev1, dev2])
        db.session.flush()

        s1_up = PrtgSensor(
            prtg_sensor_id="8001",
            prtg_device_id=dev1.id,
            sensor_name="Ping",
            raw_status="Up",
            normalized_status="online",
            last_checked_at=now,
        )
        s1_unusual = PrtgSensor(
            prtg_sensor_id="8002",
            prtg_device_id=dev1.id,
            sensor_name="Traffic Port 1",
            raw_status="Unusual",
            normalized_status="warning",
            last_checked_at=now,
        )
        s2_warn = PrtgSensor(
            prtg_sensor_id="8003",
            prtg_device_id=dev2.id,
            sensor_name="CPU Load",
            raw_status="Warning",
            normalized_status="warning",
            last_checked_at=now,
        )
        db.session.add_all([s1_up, s1_unusual, s2_warn])
        db.session.commit()

        with flask_app.test_client() as client:
            login_resp = client.post(
                "/api/v1/auth/login",
                json={"username": "admin_m4", "password": "AdminPass!123"},
            )
            token = login_resp.get_json()["data"]["access_token"]
            yield {
                "client": client,
                "headers": {"Authorization": f"Bearer {token}"},
                "regions": regions,
                "sch1_id": sch1.id,
                "sch2_id": sch2.id,
                "dev2_id": dev2.id,
                "s1_up_id": s1_up.id,
                "s1_unusual_id": s1_unusual.id,
                "s2_warn_id": s2_warn.id,
            }

        db.session.remove()
        db.drop_all()


def test_opsi_a_unusual_sensor_does_not_downgrade_healthy_school(admin_api):
    client = admin_api["client"]
    headers = admin_api["headers"]

    dash_resp = client.get("/api/v1/dashboard", headers=headers)
    assert dash_resp.status_code == 200
    counts = dash_resp.get_json()["data"]["counts"]
    assert counts["online"] == 1
    assert counts["warning"] == 1
    assert counts["offline"] == 0

    # Raw "Unusual" status MUST still be visible in sensor list
    sensors_resp = client.get(f"/api/v1/schools/{admin_api['sch1_id']}/sensors", headers=headers)
    assert sensors_resp.status_code == 200
    body = sensors_resp.get_json()["data"]
    assert body["network_status"] == "online"
    raw_statuses = {s["raw_status"] for s in body["sensors"]}
    assert "Unusual" in raw_statuses

    # Summary per region works
    summary_resp = client.get("/api/v1/monitoring/summary", headers=headers)
    assert summary_resp.status_code == 200
    assert len(summary_resp.get_json()["data"]["by_region"]) == 2


def test_admin_user_creation_enforces_adr012_region_rules_and_writes_audit_log(admin_api):
    client = admin_api["client"]
    headers = admin_api["headers"]
    bantul_id = admin_api["regions"]["bantul"].id
    sleman_id = admin_api["regions"]["sleman"].id

    # Koordinator with 2 regions MUST fail 422 (ADR-012: exactly 1 region)
    bad_koor = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "username": "koor_bad",
            "full_name": "Koordinator Dua Wilayah",
            "password": "Password!123",
            "role_code": "koordinator_teknisi",
            "region_ids": [bantul_id, sleman_id],
        },
    )
    assert bad_koor.status_code == 422

    # Supervisor Wilayah with 0 regions MUST fail 422
    bad_sup = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "username": "sup_bad",
            "full_name": "Supervisor Tanpa Wilayah",
            "password": "Password!123",
            "role_code": "supervisor_wilayah",
            "region_ids": [],
        },
    )
    assert bad_sup.status_code == 422

    # Teknisi with 2 regions MUST succeed 201 (ADR-012: multi-region allowed for Teknisi)
    ok_tek = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "username": "tek_diy",
            "full_name": "Teknisi Multi Wilayah",
            "password": "Password!123",
            "role_code": "teknisi",
            "region_ids": [bantul_id, sleman_id],
            "is_on_duty": True,
        },
    )
    assert ok_tek.status_code == 201
    tek_data = ok_tek.get_json()["data"]
    assert len(tek_data["regions"]) == 2
    assert tek_data["is_on_duty"] is True

    # Audit log recorded user.create
    audit_resp = client.get("/api/v1/audit-logs?action=user.create", headers=headers)
    assert audit_resp.status_code == 200
    assert audit_resp.get_json()["data"]["pagination"]["total"] >= 1


def test_admin_recovery_policy_validation_versioning_and_device_merge(admin_api):
    client = admin_api["client"]
    headers = admin_api["headers"]
    sch1_id = admin_api["sch1_id"]

    # Sensor belonging to a DIFFERENT school MUST be rejected 422
    wrong_sensor = client.put(
        f"/api/v1/schools/{sch1_id}/recovery-policy",
        headers=headers,
        json={"required_sensor_ids": [admin_api["s2_warn_id"]]},
    )
    assert wrong_sensor.status_code == 422

    # Valid recovery policy creation (version 1)
    v1 = client.put(
        f"/api/v1/schools/{sch1_id}/recovery-policy",
        headers=headers,
        json={
            "required_sensor_ids": [admin_api["s1_up_id"]],
            "required_consecutive_normal": 3,
            "recheck_interval_seconds": 60,
        },
    )
    assert v1.status_code == 200
    assert v1.get_json()["data"]["version"] == 1
    assert v1.get_json()["data"]["configured"] is True

    # Update increments version to 2; if Unusual sensor is made REQUIRED, school becomes warning
    v2 = client.put(
        f"/api/v1/schools/{sch1_id}/recovery-policy",
        headers=headers,
        json={
            "required_sensor_ids": [admin_api["s1_up_id"], admin_api["s1_unusual_id"]],
            "required_consecutive_normal": 2,
            "recheck_interval_seconds": 60,
        },
    )
    assert v2.status_code == 200
    assert v2.get_json()["data"]["version"] == 2

    detail_after = client.get(f"/api/v1/schools/{sch1_id}", headers=headers)
    assert detail_after.get_json()["data"]["network_status"] == "warning"

    # Admin merges dev2 into sch1 (ADR-005 multi-device correction)
    merge = client.patch(
        f"/api/v1/schools/{sch1_id}",
        headers=headers,
        json={"attach_prtg_device_ids": [admin_api["dev2_id"]]},
    )
    assert merge.status_code == 200
    assert len(merge.get_json()["data"]["prtg_device_ids"]) == 2


def test_openapi_spec_and_swagger_ui_available(admin_api):
    client = admin_api["client"]
    spec_resp = client.get("/api/v1/openapi.json")
    assert spec_resp.status_code == 200
    spec = spec_resp.get_json()
    assert spec["openapi"] == "3.0.3"
    assert "/auth/login" in spec["paths"]
    assert "/schools/{school_id}/recovery-policy" in spec["paths"]

    docs_resp = client.get("/api/v1/docs")
    assert docs_resp.status_code == 200
    assert "SwaggerUIBundle" in docs_resp.get_data(as_text=True)
