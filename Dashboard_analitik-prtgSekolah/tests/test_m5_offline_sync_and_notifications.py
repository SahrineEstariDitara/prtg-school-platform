"""M5 tests: offline draft idempotent sync, conflict detection, and push device registration/inbox (TC-12, TC-13)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app import create_app
from app.config import settings
from app.infrastructure.database import db
from app.infrastructure.database.seed import seed_baseline_data
from app.modules.auth.models import ROLE_TEKNISI, Role
from app.modules.incidents.models import INCIDENT_ACKNOWLEDGED, INCIDENT_RESOLVED, Incident
from app.modules.incidents.service import correlate_down_schools
from app.modules.regions.models import Region, UserRegion
from app.modules.responses.models import ResponseLog
from app.modules.schools.models import PrtgDevice, PrtgSensor, School
from app.modules.users.models import User


@pytest.fixture
def sync_env():
    test_uri = settings.test_database_url
    if not test_uri:
        pytest.skip("TEST_DATABASE_URL belum dikonfigurasi.")

    flask_app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": test_uri})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        seed_baseline_data(db.session, admin_username="", admin_password="")
        roles = {r.code: r for r in db.session.scalars(select(Role)).all()}
        bantul = db.session.scalar(select(Region).where(Region.code == "bantul"))
        assert bantul is not None

        tek = User(
            username="tek_sync",
            full_name="Teknisi Sync",
            password_hash=generate_password_hash("Password!123"),
            role_id=roles[ROLE_TEKNISI].id,
            is_active=True,
            is_on_duty=True,
        )
        db.session.add(tek)
        db.session.flush()
        db.session.add(UserRegion(user_id=tek.id, region_id=bantul.id))

        now = datetime.now(timezone.utc)
        sch = School(
            name="SMP Negeri 1 Bantul",
            normalized_name="SMP NEGERI 1 BANTUL",
            region_id=bantul.id,
        )
        db.session.add(sch)
        db.session.flush()

        dev = PrtgDevice(prtg_device_id="7701", school_id=sch.id, raw_name=sch.name)
        db.session.add(dev)
        db.session.flush()
        db.session.add(
            PrtgSensor(
                prtg_sensor_id="770101",
                prtg_device_id=dev.id,
                sensor_name="Ping",
                raw_status="Down",
                normalized_status="offline",
                last_checked_at=now,
            )
        )
        db.session.commit()

        with flask_app.test_client() as client:
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "tek_sync", "password": "Password!123"},
            )
            token = login.get_json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # Register push token BEFORE correlation so dry_run push is logged (TC-13)
            reg = client.post(
                "/api/v1/devices/register",
                headers=headers,
                json={"platform": "android", "token": "fcm-device-token-android-0001"},
            )
            assert reg.status_code == 201

            correlate_down_schools(db.session, now=now)
            inc = db.session.scalar(select(Incident).where(Incident.school_id == sch.id))
            assert inc is not None

            # Acknowledge online before offline field work
            ack = client.post(f"/api/v1/incidents/{inc.id}/acknowledge", headers=headers)
            assert ack.status_code == 200
            assert ack.get_json()["data"]["incident_status"] == INCIDENT_ACKNOWLEDGED

            yield {"client": client, "headers": headers, "incident_id": inc.id}

        db.session.remove()
        db.drop_all()


def test_offline_draft_sync_is_idempotent_rejects_disallowed_ops_and_detects_conflicts(sync_env):
    """TC-12 & AC-13: UUID idempotency prevents duplicates; closed incident triggers conflict, not overwrite."""
    client = sync_env["client"]
    headers = sync_env["headers"]
    inc_id = sync_env["incident_id"]

    op_uuid = "draft-op-1111-2222-3333"
    batch_payload = {
        "operations": [
            {
                "client_operation_id": op_uuid,
                "operation_type": "response_log.create",
                "payload": {
                    "incident_id": inc_id,
                    "action_summary": "Pengecekan kabel FO lapangan (draft offline)",
                    "result_notes": "Konektor diperbaiki",
                },
            },
            {
                "client_operation_id": "offline-approve-blocked-001",
                "operation_type": "verification.approve",
                "payload": {"incident_id": inc_id},
            },
        ]
    }

    first = client.post("/api/v1/sync/operations", headers=headers, json=batch_payload)
    assert first.status_code == 200
    res1 = first.get_json()["data"]["results"]
    assert res1[0]["sync_status"] == "synced"
    assert res1[0]["deduplicated"] is False
    # Offline approval MUST be rejected (ADR-009)
    assert res1[1]["sync_status"] == "failed"

    # Re-sending the EXACT SAME client_operation_id MUST NOT duplicate ResponseLog
    retry = client.post(
        "/api/v1/sync/operations",
        headers=headers,
        json={"operations": [batch_payload["operations"][0]]},
    )
    assert retry.status_code == 200
    res_retry = retry.get_json()["data"]["results"][0]
    assert res_retry["sync_status"] == "synced"
    assert res_retry["deduplicated"] is True

    logs = db.session.scalars(select(ResponseLog).where(ResponseLog.incident_id == inc_id)).all()
    assert len(logs) == 1

    # Simulate incident closed on server before another offline draft arrives -> MUST return `conflict`
    inc = db.session.scalar(select(Incident).where(Incident.id == inc_id))
    assert inc is not None
    inc.incident_status = INCIDENT_RESOLVED
    db.session.commit()

    conflict_resp = client.post(
        "/api/v1/sync/operations",
        headers=headers,
        json={
            "operations": [
                {
                    "client_operation_id": "draft-op-after-resolved-9999",
                    "operation_type": "response_log.create",
                    "payload": {
                        "incident_id": inc_id,
                        "action_summary": "Draft terlambat setelah resolved",
                    },
                }
            ]
        },
    )
    assert conflict_resp.status_code == 200
    assert conflict_resp.get_json()["data"]["results"][0]["sync_status"] == "conflict"


def test_device_token_registration_and_notification_inbox_logging(sync_env):
    """TC-13 / FR-011 / FR-025: push routed to registered technician device and visible in inbox."""
    client = sync_env["client"]
    headers = sync_env["headers"]

    inbox = client.get("/api/v1/notifications", headers=headers)
    assert inbox.status_code == 200
    items = inbox.get_json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["event_type"] == "incident.assigned"
    assert items[0]["provider_status"] == "dry_run"
    assert items[0]["incident_id"] == sync_env["incident_id"]
