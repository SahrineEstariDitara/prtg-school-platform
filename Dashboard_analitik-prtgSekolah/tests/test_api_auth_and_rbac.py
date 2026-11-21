"""M4 API authentication, refresh rotation, and RBAC/scope negative tests (TC-01..TC-03)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app import create_app
from app.common.rbac import user_has_permission
from app.config import settings
from app.infrastructure.database import db
from app.infrastructure.database.seed import seed_baseline_data
from app.modules.auth.models import (
    ROLE_ADMIN,
    ROLE_KOORDINATOR_TEKNISI,
    ROLE_SUPERVISOR_UTAMA,
    ROLE_SUPERVISOR_WILAYAH,
    ROLE_TEKNISI,
    Role,
)
from app.modules.regions.models import Region, UserRegion
from app.modules.schools.models import PrtgDevice, PrtgSensor, School
from app.modules.users.models import User


def _login(client, username: str, password: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["data"]


@pytest.fixture
def api_ctx():
    test_uri = settings.test_database_url
    if not test_uri:
        pytest.skip("TEST_DATABASE_URL belum dikonfigurasi.")

    flask_app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": test_uri})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        seed_baseline_data(
            db.session,
            admin_username="admin_u",
            admin_password="Password!123",
            admin_full_name="Admin Sistem",
        )

        roles = {r.code: r for r in db.session.scalars(select(Role)).all()}
        regions = {r.code: r for r in db.session.scalars(select(Region)).all()}

        def mk_user(username: str, role_code: str, region_codes: list[str], *, active: bool = True):
            u = User(
                username=username,
                full_name=f"User {username}",
                password_hash=generate_password_hash("Password!123"),
                role_id=roles[role_code].id,
                is_active=active,
                is_on_duty=(role_code == ROLE_TEKNISI),
            )
            db.session.add(u)
            db.session.flush()
            for rcode in region_codes:
                db.session.add(UserRegion(user_id=u.id, region_id=regions[rcode].id))
            return u

        mk_user("sup_utama", ROLE_SUPERVISOR_UTAMA, [])
        mk_user("sup_bantul", ROLE_SUPERVISOR_WILAYAH, ["bantul"])
        mk_user("koor_bantul", ROLE_KOORDINATOR_TEKNISI, ["bantul"])
        mk_user("tek_multi", ROLE_TEKNISI, ["bantul", "kota_jogja"])
        mk_user("inactive_tek", ROLE_TEKNISI, ["bantul"], active=False)

        # Create 1 school in Bantul and 1 school in Sleman
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

        for idx, sch in enumerate((sch_bantul, sch_sleman), start=1):
            dev = PrtgDevice(
                prtg_device_id=str(1000 + idx),
                school_id=sch.id,
                raw_name=sch.name,
            )
            db.session.add(dev)
            db.session.flush()
            db.session.add(
                PrtgSensor(
                    prtg_sensor_id=str(2000 + idx),
                    prtg_device_id=dev.id,
                    sensor_name="Ping",
                    raw_status="Up",
                    normalized_status="online",
                )
            )
        db.session.commit()

        with flask_app.test_client() as client:
            yield {
                "client": client,
                "regions": regions,
                "sch_bantul_id": sch_bantul.id,
                "sch_sleman_id": sch_sleman.id,
            }

        db.session.remove()
        db.drop_all()


def test_login_refresh_rotation_and_logout_revocation(api_ctx):
    client = api_ctx["client"]

    # Wrong password -> 401
    bad = client.post("/api/v1/auth/login", json={"username": "admin_u", "password": "wrong"})
    assert bad.status_code == 401
    assert bad.get_json()["success"] is False

    # Inactive user -> 403
    inactive = client.post(
        "/api/v1/auth/login", json={"username": "inactive_tek", "password": "Password!123"}
    )
    assert inactive.status_code == 403

    # Valid login -> access + refresh tokens
    tokens = _login(client, "admin_u", "Password!123")
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    # Refresh rotates token and revokes old refresh token
    refreshed = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.get_json()["data"]
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # Reusing old refresh token MUST fail 401
    reused = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401

    # Logout revokes current refresh token
    out = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
        json={"refresh_token": new_tokens["refresh_token"]},
    )
    assert out.status_code == 200
    assert out.get_json()["data"]["refresh_token_revoked"] is True

    after_logout = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert after_logout.status_code == 401


def test_me_returns_exact_role_permissions_and_admin_cannot_verify(api_ctx):
    """TC-01 & TC-03 / AC-03: verify 5-role permissions and strict Admin non-verifier boundary."""
    client = api_ctx["client"]

    expected = {
        "admin_u": (ROLE_ADMIN, "global", False, True),
        "sup_utama": (ROLE_SUPERVISOR_UTAMA, "global", True, False),
        "sup_bantul": (ROLE_SUPERVISOR_WILAYAH, "regional", True, False),
        "koor_bantul": (ROLE_KOORDINATOR_TEKNISI, "regional", False, False),
        "tek_multi": (ROLE_TEKNISI, "regional", False, False),
    }

    for username, (role_code, scope_type, can_verify, can_manage_users) in expected.items():
        data = _login(client, username, "Password!123")
        me_resp = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert me_resp.status_code == 200
        me = me_resp.get_json()["data"]
        assert me["role"]["code"] == role_code
        assert me["scope_type"] == scope_type
        assert ("incidents.verify" in me["permissions"]) is can_verify
        assert ("users.manage" in me["permissions"]) is can_manage_users

    # Server-side RBAC helper check: Admin MUST NEVER have incidents.verify
    admin_user = db.session.scalar(select(User).where(User.username == "admin_u"))
    assert user_has_permission(admin_user, "incidents.verify") is False


def test_cross_region_access_and_non_admin_mutations_are_forbidden(api_ctx):
    """TC-02 & TC-03: scoped users blocked from other regions; non-admins blocked from admin endpoints."""
    client = api_ctx["client"]
    sleman_id = api_ctx["regions"]["sleman"].id
    bantul_school_id = api_ctx["sch_bantul_id"]
    sleman_school_id = api_ctx["sch_sleman_id"]

    sup_bantul = _login(client, "sup_bantul", "Password!123")
    headers = {"Authorization": f"Bearer {sup_bantul['access_token']}"}

    # Allowed: own region school
    ok = client.get(f"/api/v1/schools/{bantul_school_id}", headers=headers)
    assert ok.status_code == 200

    # Forbidden: Sleman school detail, sensors, or filter ?region_id=<sleman>
    assert client.get(f"/api/v1/schools/{sleman_school_id}", headers=headers).status_code == 403
    assert client.get(f"/api/v1/schools/{sleman_school_id}/sensors", headers=headers).status_code == 403
    assert client.get(f"/api/v1/schools?region_id={sleman_id}", headers=headers).status_code == 403
    assert client.get(f"/api/v1/dashboard?region_id={sleman_id}", headers=headers).status_code == 403

    # Non-admin attempting user creation or recovery policy update -> 403
    assert client.post("/api/v1/users", headers=headers, json={}).status_code == 403
    assert (
        client.put(
            f"/api/v1/schools/{bantul_school_id}/recovery-policy",
            headers=headers,
            json={"required_sensor_ids": [1]},
        ).status_code
        == 403
    )
    assert client.get("/api/v1/audit-logs", headers=headers).status_code == 403
