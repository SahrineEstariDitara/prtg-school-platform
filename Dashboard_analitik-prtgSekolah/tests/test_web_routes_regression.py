"""Baseline regression tests for existing Flask web routes (M2)."""

from __future__ import annotations

import requests

import dashboard_sekolah as ds


def test_unauthenticated_requests_are_blocked(client):
    for route in ("/", "/offline", "/online", "/rekap/offline", "/rekap/online"):
        resp = client.get(route, follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    api_resp = client.get("/pratinjau-wa")
    assert api_resp.status_code == 401
    assert api_resp.get_json()["sukses"] is False


def test_login_rejects_wrong_password_and_accepts_valid_credentials(client):
    bad = client.post(
        "/login",
        data={"username": "test-admin", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert bad.status_code == 200
    assert "salah" in bad.get_data(as_text=True).lower()

    ok = client.post(
        "/login",
        data={"username": "test-admin", "password": "test-pass-12345"},
        follow_redirects=False,
    )
    assert ok.status_code == 302

    out = client.get("/logout", follow_redirects=False)
    assert out.status_code == 302
    assert "/login" in out.headers["Location"]


def test_offline_and_online_dashboards_render_and_filter_by_region(logged_in_client):
    offline_all = logged_in_client.get("/offline")
    assert offline_all.status_code == 200
    body_all = offline_all.get_data(as_text=True)
    assert "SMA N 3 BANTUL" in body_all
    assert "SMK NEGERI 1 GEDANGSARI" in body_all

    offline_bantul = logged_in_client.get("/offline?w=Bantul")
    assert offline_bantul.status_code == 200
    body_bantul = offline_bantul.get_data(as_text=True)
    assert "SMA N 3 BANTUL" in body_bantul

    online_all = logged_in_client.get("/online")
    assert online_all.status_code == 200
    body_online = online_all.get_data(as_text=True)
    assert "SMA NEGERI 2 BANTUL" in body_online
    assert "SMA NEGERI 4 YOGYAKARTA" in body_online


def test_prtg_failure_returns_502_not_fake_zero_downtime(logged_in_client, monkeypatch):
    """NFR-05 / TC-04: PRTG API failure must return 502 error page, never fake healthy 200."""
    def _boom():
        raise requests.exceptions.Timeout("PRTG connection timed out")

    monkeypatch.setattr(ds, "get_down_devices", _boom)
    resp = logged_in_client.get("/offline")
    assert resp.status_code == 502
    assert "Data PRTG belum bisa diambil" in resp.get_data(as_text=True)


def test_exports_pdf_and_xlsx_succeed(logged_in_client):
    pdf_off = logged_in_client.get("/offline/unduh/pdf")
    assert pdf_off.status_code == 200
    assert pdf_off.mimetype == "application/pdf"
    assert pdf_off.data.startswith(b"%PDF")

    xlsx_off = logged_in_client.get("/offline/unduh/xlsx")
    assert xlsx_off.status_code == 200
    assert "spreadsheetml" in xlsx_off.mimetype
    assert len(xlsx_off.data) > 500

    pdf_on = logged_in_client.get("/online/unduh/pdf")
    assert pdf_on.status_code == 200
    assert pdf_on.mimetype == "application/pdf"
    assert pdf_on.data.startswith(b"%PDF")

    xlsx_on = logged_in_client.get("/online/unduh/xlsx")
    assert xlsx_on.status_code == 200
    assert "spreadsheetml" in xlsx_on.mimetype
    assert len(xlsx_on.data) > 500


def test_wa_preview_and_rekap_pages(logged_in_client):
    wa_off = logged_in_client.get("/pratinjau-wa")
    assert wa_off.status_code == 200
    assert wa_off.get_json()["sukses"] is True

    wa_on = logged_in_client.get("/pratinjau-wa-online")
    assert wa_on.status_code == 200
    assert wa_on.get_json()["sukses"] is True

    rekap_off = logged_in_client.get("/rekap/offline")
    assert rekap_off.status_code == 200

    rekap_on = logged_in_client.get("/rekap/online")
    assert rekap_on.status_code == 200


def test_web_dashboards_render_db_active_incident_pill_and_wsgi_entrypoint(
    logged_in_client, monkeypatch
):
    """FR-027 + WSGI readiness: web dashboard displays DB active incidents when available and wsgi.app exists."""
    monkeypatch.setattr(ds, "_ringkasan_incident_aktif_db", lambda: 7)
    off = logged_in_client.get("/offline")
    assert off.status_code == 200
    assert "Incident DB Aktif: <b>7</b>" in off.get_data(as_text=True)

    on = logged_in_client.get("/online")
    assert on.status_code == 200
    assert "Incident DB Aktif: <b>7</b>" in on.get_data(as_text=True)

    import wsgi

    assert wsgi.app is ds.app
