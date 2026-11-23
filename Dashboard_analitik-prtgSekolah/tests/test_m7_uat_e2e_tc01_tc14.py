"""M7 Formal UAT E2E Suite covering all 14 minimum PRD test cases (TC-01..TC-14) & AC-01..AC-14."""

from __future__ import annotations

from datetime import datetime, timezone
import io
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app import create_app
from app.config import settings
from app.infrastructure.database import db
from app.infrastructure.database.seed import seed_baseline_data
from app.infrastructure.database.seed_uat import DEFAULT_UAT_PASSWORD, seed_uat_demo_accounts
from app.integrations.prtg.adapter import PRTGRecheckResult
from app.modules.incidents.service import correlate_down_schools
from app.modules.monitoring.service import MonitoringService
from app.modules.regions.models import Region
from app.modules.schools.models import PrtgDevice, PrtgSensor, School
from app.modules.users.models import User

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _login(client, username: str, password: str = DEFAULT_UAT_PASSWORD) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login gagal untuk {username}: {resp.get_json()}"
    token = resp.get_json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def uat_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    test_uri = settings.test_database_url
    if not test_uri:
        pytest.skip("TEST_DATABASE_URL belum dikonfigurasi.")

    monkeypatch.setattr(
        "app.modules.responses.service._upload_root",
        lambda: tmp_path / "uat_uploads",
    )

    flask_app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": test_uri})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        seed_baseline_data(
            db.session,
            admin_username="admin_uat",
            admin_password=DEFAULT_UAT_PASSWORD,
            admin_full_name="Admin UAT",
        )
        summary_1 = seed_uat_demo_accounts(db.session, demo_password=DEFAULT_UAT_PASSWORD)
        summary_2 = seed_uat_demo_accounts(db.session, demo_password=DEFAULT_UAT_PASSWORD)
        assert summary_1.users_created == 5
        assert summary_2.users_created == 0  # Idempotent check

        regions = {r.code: r for r in db.session.scalars(select(Region)).all()}
        now = datetime(2026, 11, 23, 8, 0, 0, tzinfo=timezone.utc)

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
        sch_gk = School(
            name="SMA Negeri 1 Wonosari",
            normalized_name="SMA NEGERI 1 WONOSARI",
            region_id=regions["gunung_kidul"].id,
        )
        db.session.add_all([sch_bantul, sch_sleman, sch_gk])
        db.session.flush()

        d_bantul = PrtgDevice(prtg_device_id="9001", school_id=sch_bantul.id, raw_name="SMAN 1 Bantul")
        d_sleman = PrtgDevice(prtg_device_id="9002", school_id=sch_sleman.id, raw_name="SMAN 1 Sleman")
        d_gk = PrtgDevice(prtg_device_id="9003", school_id=sch_gk.id, raw_name="SMAN 1 Wonosari")
        db.session.add_all([d_bantul, d_sleman, d_gk])
        db.session.flush()

        sn_b1 = PrtgSensor(
            prtg_sensor_id="8001",
            prtg_device_id=d_bantul.id,
            sensor_name="Ping Gateway",
            raw_status="Down",
            normalized_status="offline",
            last_checked_at=now,
        )
        sn_b2 = PrtgSensor(
            prtg_sensor_id="8002",
            prtg_device_id=d_bantul.id,
            sensor_name="Uplink FO",
            raw_status="Down",
            normalized_status="offline",
            last_checked_at=now,
        )
        sn_s1 = PrtgSensor(
            prtg_sensor_id="8003",
            prtg_device_id=d_sleman.id,
            sensor_name="Ping Sleman",
            raw_status="Up",
            normalized_status="online",
            last_checked_at=now,
        )
        sn_gk1 = PrtgSensor(
            prtg_sensor_id="8004",
            prtg_device_id=d_gk.id,
            sensor_name="Ping Wonosari",
            raw_status="Down",
            normalized_status="offline",
            last_checked_at=now,
        )
        db.session.add_all([sn_b1, sn_b2, sn_s1, sn_gk1])
        db.session.commit()

        with flask_app.test_client() as client:
            yield {
                "client": client,
                "sch_bantul": sch_bantul,
                "sch_sleman": sch_sleman,
                "sch_gk": sch_gk,
                "sn_b1": sn_b1,
                "sn_b2": sn_b2,
                "regions": regions,
                "now": now,
            }

        db.session.remove()
        db.drop_all()


def test_uat_tc01_tc02_tc03_roles_scope_and_admin_boundary(uat_env):
    """TC-01 (5-role login), TC-02 (cross-region 403), TC-03 (Admin non-verifier & config)."""
    client = uat_env["client"]
    sch_bantul = uat_env["sch_bantul"]
    sch_sleman = uat_env["sch_sleman"]

    # TC-01: Login seluruh 5 role tetap
    role_accounts = [
        ("admin_uat", "admin", "global"),
        ("spv_utama", "supervisor_utama", "global"),
        ("spv_bantul", "supervisor_wilayah", "regional"),
        ("koor_bantul", "koordinator_teknisi", "regional"),
        ("teknisi_bantul", "teknisi", "regional"),
    ]
    for username, expected_role, expected_scope in role_accounts:
        hdrs = _login(client, username)
        me = client.get("/api/v1/me", headers=hdrs).get_json()["data"]
        assert me["role"]["code"] == expected_role
        assert me["scope_type"] == expected_scope

    # TC-02: Supervisor Wilayah Bantul ditolak 403 saat mengakses sekolah Sleman
    spv_bantul_hdr = _login(client, "spv_bantul")
    assert client.get(f"/api/v1/schools/{sch_bantul.id}", headers=spv_bantul_hdr).status_code == 200
    assert client.get(f"/api/v1/schools/{sch_sleman.id}", headers=spv_bantul_hdr).status_code == 403

    # TC-03: Admin boleh monitoring global & kelola user/policy, tetapi DITOLAK 403 untuk verify
    admin_hdr = _login(client, "admin_uat")
    assert client.get("/api/v1/dashboard", headers=admin_hdr).status_code == 200
    assert client.get("/api/v1/users", headers=admin_hdr).status_code == 200
    correlate_down_schools(db.session, now=uat_env["now"])
    inc_id = client.get(
        f"/api/v1/incidents?school_id={sch_bantul.id}", headers=admin_hdr
    ).get_json()["data"]["items"][0]["id"]
    assert (
        client.post(f"/api/v1/incidents/{inc_id}/verification/approve", headers=admin_hdr).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/incidents/{inc_id}/verification/reject",
            headers=admin_hdr,
            json={"reason": "coba reject"},
        ).status_code
        == 403
    )


def test_uat_tc04_to_tc14_full_operational_lifecycle_and_consistency(uat_env):
    """TC-04..TC-14: PRTG status, correlation, assignment, response, reject/approve, recheck, offline sync, push, web consistency."""
    client = uat_env["client"]
    sch_bantul = uat_env["sch_bantul"]
    sch_gk = uat_env["sch_gk"]
    sn_b1 = uat_env["sn_b1"]
    sn_b2 = uat_env["sn_b2"]

    admin_hdr = _login(client, "admin_uat")
    spv_bantul_hdr = _login(client, "spv_bantul")
    koor_bantul_hdr = _login(client, "koor_bantul")
    tek_bantul_hdr = _login(client, "teknisi_bantul")

    # TC-04: Data PRTG terbaca & stale flag terpisah (tidak pernah online palsu)
    school_detail = client.get(f"/api/v1/schools/{sch_bantul.id}", headers=admin_hdr).get_json()["data"]
    assert school_detail["network_status"] == "offline"
    assert "is_stale" in school_detail

    # TC-05 & TC-06: Korelasi 2 sensor offline di Bantul -> tepat 1 incident aktif & auto-assign teknisi_bantul;
    # Gunung Kidul tanpa teknisi on-duty -> masuk unassigned queue
    corr = correlate_down_schools(db.session, now=uat_env["now"])
    assert corr.incidents_created == 2
    corr_repeat = correlate_down_schools(db.session, now=uat_env["now"])
    assert corr_repeat.incidents_created == 0  # Deduplicated

    bantul_inc = client.get(
        f"/api/v1/incidents?school_id={sch_bantul.id}", headers=spv_bantul_hdr
    ).get_json()["data"]["items"][0]
    gk_inc = client.get(
        f"/api/v1/incidents?school_id={sch_gk.id}", headers=admin_hdr
    ).get_json()["data"]["items"][0]
    assert bantul_inc["incident_status"] == "assigned"
    assert bantul_inc["current_assignee"]["username"] == "teknisi_bantul"
    assert gk_inc["incident_status"] == "unassigned"
    inc_id = bantul_inc["id"]

    # TC-07: Manual assign/reassign oleh Koordinator dengan alasan wajib & histori tetap tersimpan
    tek_bantul_user = db.session.scalar(select(User).where(User.username == "teknisi_bantul"))
    assert tek_bantul_user is not None
    assert (
        client.post(
            f"/api/v1/incidents/{inc_id}/assign",
            headers=koor_bantul_hdr,
            json={"technician_id": tek_bantul_user.id, "reason": ""},
        ).status_code
        == 422
    )
    reassign_resp = client.post(
        f"/api/v1/incidents/{inc_id}/assign",
        headers=koor_bantul_hdr,
        json={"technician_id": tek_bantul_user.id, "reason": "Konfirmasi penugasan lapangan UAT"},
    )
    assert reassign_resp.status_code == 200
    detail_after_assign = client.get(
        f"/api/v1/incidents/{inc_id}", headers=koor_bantul_hdr
    ).get_json()["data"]
    assert len(detail_after_assign["assignments"]) == 2  # Histori assignment lama dipertahankan

    # TC-08: Teknisi acknowledge (online), upload response + foto bukti valid, submit resolution
    assert client.post(f"/api/v1/incidents/{inc_id}/acknowledge", headers=tek_bantul_hdr).status_code == 200
    resp_upload = client.post(
        f"/api/v1/incidents/{inc_id}/responses",
        headers=tek_bantul_hdr,
        data={
            "action_summary": "Penggantian patch cord FO di ruang server",
            "result_notes": "Indikator PON hijau stabil",
            "attachment": (io.BytesIO(PNG_1X1), "bukti_uat.png"),
        },
        content_type="multipart/form-data",
    )
    assert resp_upload.status_code == 201
    response_id = resp_upload.get_json()["data"]["id"]
    assert client.get(f"/api/v1/attachments/{response_id}", headers=spv_bantul_hdr).status_code == 200
    assert (
        client.post(f"/api/v1/incidents/{inc_id}/resolution-submissions", headers=tek_bantul_hdr).status_code
        == 200
    )

    # TC-09: Supervisor reject dengan alasan wajib -> kembali in_progress
    assert (
        client.post(
            f"/api/v1/incidents/{inc_id}/verification/reject",
            headers=spv_bantul_hdr,
            json={"reason": ""},
        ).status_code
        == 422
    )
    rej = client.post(
        f"/api/v1/incidents/{inc_id}/verification/reject",
        headers=spv_bantul_hdr,
        json={"reason": "Mohon sertakan verifikasi ping dari ruang lab"},
    )
    assert rej.status_code == 200
    assert rej.get_json()["data"]["incident_status"] == "in_progress"

    # TC-12 (Bagian 1): Offline draft sync idempoten sebelum submit ulang
    sync_payload = {
        "operations": [
            {
                "client_operation_id": "op-uat-tc12-unique-1",
                "operation_type": "response_log.create",
                "payload": {
                    "incident_id": inc_id,
                    "action_summary": "Tes ping ruang lab 0% packet loss",
                    "result_notes": "Disimpan saat offline di lapangan",
                },
            }
        ]
    }
    sync_1 = client.post("/api/v1/sync/operations", headers=tek_bantul_hdr, json=sync_payload).get_json()["data"]["results"][0]
    sync_2 = client.post("/api/v1/sync/operations", headers=tek_bantul_hdr, json=sync_payload).get_json()["data"]["results"][0]
    assert sync_1["sync_status"] == "synced" and sync_1["deduplicated"] is False
    assert sync_2["sync_status"] == "synced" and sync_2["deduplicated"] is True
    assert sync_1["result"]["id"] == sync_2["result"]["id"]

    # Submit ulang penyelesaian
    assert (
        client.post(f"/api/v1/incidents/{inc_id}/resolution-submissions", headers=tek_bantul_hdr).status_code
        == 200
    )

    # TC-11: Blokir closure saat Recovery Policy belum diatur & saat PRTG API unavailable
    approve_no_policy = client.post(
        f"/api/v1/incidents/{inc_id}/verification/approve",
        headers=spv_bantul_hdr,
        json={"reason": "Bukti lengkap"},
    )
    assert approve_no_policy.status_code == 200
    assert approve_no_policy.get_json()["data"]["incident_status"] == "awaiting_recovery"

    # Admin atur Recovery Policy (wajib 2 sensor, 3x normal berturut-turut)
    pol_resp = client.put(
        f"/api/v1/schools/{sch_bantul.id}/recovery-policy",
        headers=admin_hdr,
        json={
            "required_sensor_ids": [sn_b1.id, sn_b2.id],
            "required_consecutive_normal": 3,
            "recheck_interval_seconds": 60,
            "expected_status": "online",
        },
    )
    assert pol_resp.status_code == 200

    # Simulasikan PRTG timeout -> recheck gagal, streak tetap 0, tetap awaiting_recovery
    failed_result = PRTGRecheckResult(
        is_valid=False,
        checked_at=datetime.now(timezone.utc),
        sensors_by_id={},
        missing_sensor_ids=["8001", "8002"],
        error_message="PRTG timeout UAT",
    )
    with patch(
        "app.integrations.prtg.adapter.PRTGAdapter.recheck_sensors",
        return_value=failed_result,
    ):
        rc_fail = client.post(
            f"/api/v1/incidents/{inc_id}/verification/recheck", headers=spv_bantul_hdr
        )
        assert rc_fail.status_code == 200
        assert rc_fail.get_json()["data"]["incident_status"] == "awaiting_recovery"
        assert rc_fail.get_json()["data"]["streak"] == 0

    # TC-10: 3x PRTG live recheck normal berturut-turut -> incident resolved
    healthy_result = PRTGRecheckResult(
        is_valid=True,
        checked_at=datetime.now(timezone.utc),
        sensors_by_id={
            "8001": {"prtg_sensor_id": "8001", "normalized_status": "online", "raw_status": "Up"},
            "8002": {"prtg_sensor_id": "8002", "normalized_status": "online", "raw_status": "Up"},
        },
        missing_sensor_ids=[],
        error_message=None,
    )
    with patch("app.integrations.prtg.adapter.PRTGAdapter.recheck_sensors", return_value=healthy_result):
        r1 = client.post(f"/api/v1/incidents/{inc_id}/verification/recheck", headers=spv_bantul_hdr).get_json()["data"]
        r2 = client.post(f"/api/v1/incidents/{inc_id}/verification/recheck", headers=spv_bantul_hdr).get_json()["data"]
        r3 = client.post(f"/api/v1/incidents/{inc_id}/verification/recheck", headers=spv_bantul_hdr).get_json()["data"]
        assert r1["incident_status"] == "awaiting_recovery" and r1["streak"] == 1
        assert r2["incident_status"] == "awaiting_recovery" and r2["streak"] == 2
        assert r3["incident_status"] == "resolved" and r3["streak"] == 3

    # TC-12 (Bagian 2): Draft offline terlambat ke incident yang sudah resolved -> conflict (tidak menimpa)
    late_sync = client.post(
        "/api/v1/sync/operations",
        headers=tek_bantul_hdr,
        json={
            "operations": [
                {
                    "client_operation_id": "op-uat-tc12-late-conflict",
                    "operation_type": "response_log.create",
                    "payload": {
                        "incident_id": inc_id,
                        "action_summary": "Catatan terlambat setelah resolved",
                    },
                }
            ]
        },
    ).get_json()["data"]["results"][0]
    assert late_sync["sync_status"] == "conflict"

    # TC-13: Registrasi FCM device token & in-app notification inbox
    reg = client.post(
        "/api/v1/devices/register",
        headers=tek_bantul_hdr,
        json={"platform": "android", "token": "fcm-uat-demo-token-android-123456"},
    )
    assert reg.status_code == 201
    inbox = client.get("/api/v1/notifications", headers=tek_bantul_hdr).get_json()["data"]
    assert inbox["pagination"]["total"] >= 1

    # TC-14: Konsistensi shared backend service (Website & REST API v1)
    api_dash = client.get("/api/v1/dashboard", headers=admin_hdr).get_json()["data"]
    shared_dash = MonitoringService().get_dashboard_summary(db.session)
    assert api_dash["total_schools"] == shared_dash["total_schools"] == 3
    assert api_dash["counts"] == shared_dash["counts"]
    assert api_dash["active_incidents"] == shared_dash["active_incidents"] == 1  # Hanya Gunung Kidul yang masih aktif
