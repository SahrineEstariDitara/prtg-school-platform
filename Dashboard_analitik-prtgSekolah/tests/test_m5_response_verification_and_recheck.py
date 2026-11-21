"""M5 E2E tests: response, protected photo upload, Admin verify block, reject, and multi-check PRTG recheck (TC-03, TC-08..TC-11)."""

from __future__ import annotations

from datetime import datetime, timezone
import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app import create_app
from app.config import settings
from app.infrastructure.database import db
from app.infrastructure.database.seed import seed_baseline_data
from app.integrations.prtg import PRTGAdapter, PRTGRecheckResult
from app.modules.auth.models import (
    ROLE_SUPERVISOR_UTAMA,
    ROLE_SUPERVISOR_WILAYAH,
    ROLE_TEKNISI,
    Role,
)
from app.modules.incidents.models import (
    INCIDENT_ACKNOWLEDGED,
    INCIDENT_AWAITING_RECOVERY,
    INCIDENT_IN_PROGRESS,
    INCIDENT_PENDING_VERIFICATION,
    INCIDENT_RESOLVED,
    Incident,
)
from app.modules.incidents.service import correlate_down_schools
from app.modules.regions.models import Region, UserRegion
from app.modules.schools.admin_service import upsert_recovery_policy
from app.modules.schools.models import PrtgDevice, PrtgSensor, School
from app.modules.users.models import User
from app.modules.verification.models import PrtgRecheckLog, VerificationRecord
from app.modules.verification.service import run_pending_recovery_rechecks


def _headers(client, username: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": "Password!123"})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.get_json()['data']['access_token']}"}


@pytest.fixture
def wf_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_uri = settings.test_database_url
    if not test_uri:
        pytest.skip("TEST_DATABASE_URL belum dikonfigurasi.")

    # Redirect uploads directory to tmp_path so tests never leave files in repo
    monkeypatch.setattr(
        "app.modules.responses.service._upload_root",
        lambda: tmp_path / "uploads",
    )

    flask_app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": test_uri})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        seed_baseline_data(
            db.session,
            admin_username="admin_wf",
            admin_password="Password!123",
            admin_full_name="Admin WF",
        )
        roles = {r.code: r for r in db.session.scalars(select(Role)).all()}
        regions = {r.code: r for r in db.session.scalars(select(Region)).all()}

        def mk_user(username, role_code, region_codes, *, duty=False):
            u = User(
                username=username,
                full_name=f"User {username}",
                password_hash=generate_password_hash("Password!123"),
                role_id=roles[role_code].id,
                is_active=True,
                is_on_duty=duty,
            )
            db.session.add(u)
            db.session.flush()
            for rc in region_codes:
                db.session.add(UserRegion(user_id=u.id, region_id=regions[rc].id))
            return u

        admin_u = db.session.scalar(select(User).where(User.username == "admin_wf"))
        mk_user("sup_utama", ROLE_SUPERVISOR_UTAMA, [])
        mk_user("sup_bantul", ROLE_SUPERVISOR_WILAYAH, ["bantul"])
        mk_user("sup_sleman", ROLE_SUPERVISOR_WILAYAH, ["sleman"])
        tek_assigned = mk_user("tek_assigned", ROLE_TEKNISI, ["bantul"], duty=True)
        mk_user("tek_other", ROLE_TEKNISI, ["bantul"], duty=False)

        now = datetime.now(timezone.utc)
        sch = School(
            name="SMA Negeri 2 Bantul",
            normalized_name="SMA NEGERI 2 BANTUL",
            region_id=regions["bantul"].id,
        )
        db.session.add(sch)
        db.session.flush()

        dev = PrtgDevice(prtg_device_id="3118", school_id=sch.id, raw_name="SMA N 2 Bantul")
        db.session.add(dev)
        db.session.flush()

        s_ping = PrtgSensor(
            prtg_sensor_id="311801",
            prtg_device_id=dev.id,
            sensor_name="Ping",
            raw_status="Down",
            normalized_status="offline",
            last_checked_at=now,
        )
        db.session.add(s_ping)
        db.session.commit()

        correlate_down_schools(db.session, now=now)
        inc = db.session.scalar(select(Incident).where(Incident.school_id == sch.id))
        assert inc is not None and inc.current_assignee_id == tek_assigned.id

        with flask_app.test_client() as client:
            yield {
                "client": client,
                "admin_u": admin_u,
                "school": sch,
                "sensor": s_ping,
                "incident_id": inc.id,
            }

        db.session.remove()
        db.drop_all()


def test_full_e2e_workflow_including_photo_reject_missing_policy_prtg_error_and_3x_stable_closure(
    wf_env, monkeypatch: pytest.MonkeyPatch
):
    """Covers TC-03, TC-08, TC-09, TC-10, TC-11, AC-03, AC-08..AC-11, ADR-007..ADR-010."""
    client = wf_env["client"]
    inc_id = wf_env["incident_id"]
    tek_h = _headers(client, "tek_assigned")
    tek_other_h = _headers(client, "tek_other")
    sup_bantul_h = _headers(client, "sup_bantul")
    sup_sleman_h = _headers(client, "sup_sleman")
    admin_h = _headers(client, "admin_wf")

    # 1. Non-assigned technician CANNOT acknowledge (403)
    assert client.post(f"/api/v1/incidents/{inc_id}/acknowledge", headers=tek_other_h).status_code == 403

    # 2. Assigned technician acknowledges -> acknowledged
    ack = client.post(f"/api/v1/incidents/{inc_id}/acknowledge", headers=tek_h)
    assert ack.status_code == 200
    assert ack.get_json()["data"]["incident_status"] == INCIDENT_ACKNOWLEDGED

    # 3. Cannot submit resolution before adding at least 1 response log (422)
    assert (
        client.post(f"/api/v1/incidents/{inc_id}/resolution-submissions", headers=tek_h).status_code
        == 422
    )

    # 4. Upload invalid non-image file MUST fail 422
    bad_upload = client.post(
        f"/api/v1/incidents/{inc_id}/responses",
        headers=tek_h,
        data={
            "action_summary": "Cek kabel FO",
            "attachment": (io.BytesIO(b"not-a-real-image"), "fake.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert bad_upload.status_code == 422

    # 5. Valid JPEG upload -> 201 + transitions acknowledged -> in_progress (TC-08)
    valid_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
    good_resp = client.post(
        f"/api/v1/incidents/{inc_id}/responses",
        headers=tek_h,
        data={
            "action_summary": "Restart ONT & ganti patchcord",
            "result_notes": "Lampu LOS padam, koneksi mulai normal",
            "attachment": (io.BytesIO(valid_jpeg), "bukti.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert good_resp.status_code == 201
    resp_id = good_resp.get_json()["data"]["id"]
    assert good_resp.get_json()["data"]["has_attachment"] is True

    # Authorized supervisor in Bantul can download photo; Sleman supervisor gets 403 (NFR-10)
    assert client.get(f"/api/v1/attachments/{resp_id}", headers=sup_bantul_h).status_code == 200
    assert client.get(f"/api/v1/attachments/{resp_id}", headers=sup_sleman_h).status_code == 403

    # 6. Technician submits resolution -> pending_verification
    sub = client.post(
        f"/api/v1/incidents/{inc_id}/resolution-submissions",
        headers=tek_h,
        json={"summary_notes": "Sudah pulih di lokasi"},
    )
    assert sub.status_code == 200
    assert sub.get_json()["data"]["incident_status"] == INCIDENT_PENDING_VERIFICATION

    # 7. Admin CANNOT approve or reject (TC-03 / AC-03 -> 403 FORBIDDEN!)
    assert client.post(f"/api/v1/incidents/{inc_id}/verification/approve", headers=admin_h).status_code == 403
    assert (
        client.post(
            f"/api/v1/incidents/{inc_id}/verification/reject",
            headers=admin_h,
            json={"reason": "Admin mencoba reject"},
        ).status_code
        == 403
    )

    # Cross-region supervisor (Sleman) CANNOT verify Bantul incident -> 403
    assert client.post(f"/api/v1/incidents/{inc_id}/verification/approve", headers=sup_sleman_h).status_code == 403

    # 8. Supervisor Bantul REJECTS with reason -> returns to `in_progress` + saves VerificationRecord (TC-09, ADR-008)
    rej = client.post(
        f"/api/v1/incidents/{inc_id}/verification/reject",
        headers=sup_bantul_h,
        json={"reason": "Foto lampu router kurang jelas, mohon cek ulang"},
    )
    assert rej.status_code == 200
    assert rej.get_json()["data"]["incident_status"] == INCIDENT_IN_PROGRESS

    # 9. Technician adds follow-up response and re-submits
    assert (
        client.post(
            f"/api/v1/incidents/{inc_id}/responses",
            headers=tek_h,
            json={"action_summary": "Verifikasi ulang koneksi stabil di sekolah"},
        ).status_code
        == 201
    )
    assert (
        client.post(f"/api/v1/incidents/{inc_id}/resolution-submissions", headers=tek_h).status_code
        == 200
    )

    # 10. Supervisor approves while SchoolRecoveryPolicy is NOT configured -> MUST stay `awaiting_recovery` (AC-11!)
    appr_no_policy = client.post(
        f"/api/v1/incidents/{inc_id}/verification/approve",
        headers=sup_bantul_h,
        json={"reason": "Laporan diterima"},
    )
    assert appr_no_policy.status_code == 200
    ap_data = appr_no_policy.get_json()["data"]
    assert ap_data["incident_status"] == INCIDENT_AWAITING_RECOVERY
    assert ap_data["resolved"] is False
    assert "RECOVERY_POLICY_NOT_CONFIGURED" in ap_data["recheck"]["error_message"]

    # 11. Admin configures SchoolRecoveryPolicy with required_consecutive_normal=3 (ADR-009)
    upsert_recovery_policy(
        db.session,
        wf_env["school"].id,
        {
            "required_sensor_ids": [wf_env["sensor"].id],
            "required_consecutive_normal": 3,
            "recheck_interval_seconds": 60,
        },
        actor_id=wf_env["admin_u"].id,
    )

    # 12. Simulate PRTG API timeout during recheck -> MUST NOT close, streak stays 0 (TC-11)
    timeout_adapter = MagicMock(spec=PRTGAdapter)
    timeout_adapter.recheck_sensors.return_value = PRTGRecheckResult(
        is_valid=False,
        checked_at=datetime.now(timezone.utc),
        missing_sensor_ids=["311801"],
        error_message="Timeout connecting to PRTG",
    )
    res_timeout = run_pending_recovery_rechecks(db.session, prtg_adapter=timeout_adapter, force=True)
    assert len(res_timeout) == 1
    assert res_timeout[0]["incident_status"] == INCIDENT_AWAITING_RECOVERY
    assert res_timeout[0]["streak"] == 0

    # 13. Simulate 3 consecutive healthy PRTG rechecks -> 1/3, 2/3, then 3/3 = RESOLVED (TC-10, AC-10)!
    ok_adapter = MagicMock(spec=PRTGAdapter)
    ok_adapter.recheck_sensors.side_effect = lambda _ids: PRTGRecheckResult(
        is_valid=True,
        checked_at=datetime.now(timezone.utc),
        sensors_by_id={
            "311801": {"objid": 311801, "status": "Up", "status_raw": 3, "normalized_status": "online"}
        },
    )

    c1 = run_pending_recovery_rechecks(db.session, prtg_adapter=ok_adapter, force=True)[0]
    assert c1["incident_status"] == INCIDENT_AWAITING_RECOVERY and c1["streak"] == 1 and c1["resolved"] is False

    c2 = run_pending_recovery_rechecks(db.session, prtg_adapter=ok_adapter, force=True)[0]
    assert c2["incident_status"] == INCIDENT_AWAITING_RECOVERY and c2["streak"] == 2 and c2["resolved"] is False

    c3 = run_pending_recovery_rechecks(db.session, prtg_adapter=ok_adapter, force=True)[0]
    assert c3["incident_status"] == INCIDENT_RESOLVED and c3["streak"] == 3 and c3["resolved"] is True

    # Audit check: 2 VerificationRecords (reject + approve) and 5 PrtgRecheckLogs (missing_policy + timeout + 3 normal)
    ver_records = db.session.scalars(
        select(VerificationRecord).where(VerificationRecord.incident_id == inc_id)
    ).all()
    assert len(ver_records) == 2
    recheck_logs = db.session.scalars(
        select(PrtgRecheckLog).where(PrtgRecheckLog.incident_id == inc_id)
    ).all()
    assert len(recheck_logs) == 5

    # 14. New outage after resolved -> MUST create a brand-new incident AND preserve both in school history (ADR-007)
    sensor_row = db.session.scalar(select(PrtgSensor).where(PrtgSensor.id == wf_env["sensor"].id))
    assert sensor_row is not None
    sensor_row.normalized_status = "offline"
    sensor_row.raw_status = "Down"
    db.session.commit()

    new_corr = correlate_down_schools(db.session)
    assert new_corr.incidents_created == 1

    school_hist = client.get(f"/api/v1/schools/{wf_env['school'].id}/incidents", headers=sup_bantul_h)
    assert school_hist.status_code == 200
    assert school_hist.get_json()["data"]["pagination"]["total"] == 2
