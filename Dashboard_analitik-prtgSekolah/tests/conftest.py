"""Shared pytest fixtures for M2 baseline regression suite.

All tests run completely isolated from live PRTG/GreenAPI and real JSON files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Ensure deterministic dummy env vars before importing backend modules
os.environ.setdefault("PRTG_SERVER", "http://prtg.test.local:8080")
os.environ.setdefault("PRTG_USERNAME", "test-prtg-user")
os.environ.setdefault("PRTG_PASSWORD", "test-prtg-secret-999")
os.environ.setdefault("PRTG_PASSHASH", "")
os.environ.setdefault("PRTG_VERIFY_SSL", "false")
os.environ.setdefault("FLASK_SECRET_KEY", "test-flask-secret-key-0123456789abcdef")
os.environ.setdefault("DASHBOARD_USERNAME", "test-admin")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-pass-12345")
os.environ.setdefault("GREENAPI_API_URL", "https://greenapi.test.local")
os.environ.setdefault("GREENAPI_ID_INSTANCE", "999000111")
os.environ.setdefault("GREENAPI_TOKEN", "test-greenapi-token-xyz")
os.environ.setdefault("WA_TARGET", "120363000000000000@g.us")

import dashboard_sekolah as ds  # noqa: E402
import prtg_sekolah as ps  # noqa: E402
import tab_prtg  # noqa: E402


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect all JSON/log files to tmp_path and clear in-memory caches."""
    monkeypatch.setattr(ds, "DASHBOARD_USERNAME", "test-admin")
    monkeypatch.setattr(ds, "DASHBOARD_PASSWORD", "test-pass-12345")
    monkeypatch.setattr(ds, "RIWAYAT_FILE", str(tmp_path / "riwayat_sekolah.json"))
    monkeypatch.setattr(ds, "RIWAYAT_ONLINE_FILE", str(tmp_path / "riwayat_online_v2.json"))
    monkeypatch.setattr(ds, "GANGGUAN_FILE", str(tmp_path / "gangguan_sekolah.json"))
    monkeypatch.setattr(ds, "PENGATURAN_FILE", str(tmp_path / "pengaturan_sekolah.json"))
    monkeypatch.setattr(ds, "REKAP_ONLINE_FILE", str(tmp_path / "rekap_online_harian.json"))
    monkeypatch.setattr(ps, "NOTIFIKASI_STATE_FILE", str(tmp_path / "notifikasi_prtg_state.json"))
    monkeypatch.setattr(ps, "LOG_FILE", str(tmp_path / "bot_log.txt"))
    monkeypatch.setattr(tab_prtg, "FILE_PILIHAN", str(tmp_path / "sensor_terpilih.json"))

    # Start with clean empty pengaturan file
    (tmp_path / "pengaturan_sekolah.json").write_text(
        json.dumps({"wilayah": {}, "kata_sekolah": []}),
        encoding="utf-8",
    )

    # Reset all in-memory caches
    ds._cache_pengaturan.update(mtime=None, data=None)
    ds._cache_ekstra.update(mtime=None, data=None)
    ds._cache_kamus.update(sumber=None, grup={}, nama={})
    ds._cache_jalur.update(waktu=0.0, coba=0.0, data={})
    ds._cache_info.update(data={}, ip={})
    ds._cache_online_data.update(waktu=0.0, data=[], sensors_count=0)
    ds._cache_down_data.update(waktu=0.0, data=[])
    tab_prtg._cache.clear()

    return tmp_path


@pytest.fixture
def sample_down_sensors() -> list[dict]:
    """Synthetic PRTG DOWN sensors covering multiple DIY regions & categories."""
    return [
        {
            "objid": 1001,
            "parentid": 21755,
            "group": "DIY / Bantul",
            "device": "SMA N 3 BANTUL (10.123.0.154)",
            "sensor": "Ping",
            "status": "Down",
            "downtimesince": "3 h 30 m",
            "downtimesince_raw": "3.5",
            "message": "Request timed out",
        },
        {
            "objid": 1002,
            "parentid": 25254,
            "group": "DIY / Sleman",
            "device": "SIM063 SMA Negeri 01 Gamping",
            "sensor": "Ping",
            "status": "Down",
            "downtimesince": "1 d 6 h",
            "downtimesince_raw": "30",
            "message": "No response",
        },
        {
            "objid": 1003,
            "parentid": 4423,
            "group": "DIY / Gunung Kidul",
            "device": "SMK NEGERI 1 GEDANGSARI (103.210.35.105)",
            "sensor": "Ping",
            "status": "Down",
            "downtimesince": "3 d 2 h",
            "downtimesince_raw": "74",
            "message": "Link down",
        },
        {
            "objid": 1099,
            "parentid": 99999,
            "group": "Core Backbone",
            "device": "CORE-ROUTER-DC-01",
            "sensor": "Ping",
            "status": "Down",
            "downtimesince": "2 h",
            "downtimesince_raw": "2",
            "message": "Should be ignored by is_sekolah",
        },
    ]


@pytest.fixture
def sample_online_sensors() -> list[dict]:
    """Synthetic PRTG UP sensors for schools across DIY regions."""
    return [
        {
            "objid": 2001,
            "parentid": 3118,
            "group": "DIY / Bantul",
            "device": "SMA NEGERI 2 BANTUL (103.210.35.35)",
            "sensor": "Ping",
            "status": "Up",
            "lastcheck": "10 s ago",
            "uptimesince": "30 m",
            "message": "OK",
        },
        {
            "objid": 2002,
            "parentid": 3995,
            "group": "DIY / Kota Jogja",
            "device": "SMA NEGERI 4 YOGYAKARTA (103.210.35.82)",
            "sensor": "Ping",
            "status": "Up",
            "lastcheck": "15 s ago",
            "uptimesince": "5 d 4 h",
            "message": "OK",
        },
        {
            "objid": 2003,
            "parentid": 25128,
            "group": "DIY / Kulon Progo",
            "device": "SMK N 2 Pengasih",
            "sensor": "Ping",
            "status": "Up",
            "lastcheck": "20 s ago",
            "uptimesince": "1 d 10 h",
            "message": "OK",
        },
    ]


@pytest.fixture
def mock_prtg(
    isolated_env,
    monkeypatch: pytest.MonkeyPatch,
    sample_down_sensors: list[dict],
    sample_online_sensors: list[dict],
):
    """Patch PRTG network calls in dashboard_sekolah to return synthetic data."""
    monkeypatch.setattr(ds, "get_down_devices", lambda: list(sample_down_sensors))
    monkeypatch.setattr(ds, "get_online_devices", lambda: list(sample_online_sensors))
    monkeypatch.setattr(ds, "ambil_jalur_grup", lambda paksa=False: {})
    return isolated_env


@pytest.fixture
def client(isolated_env):
    """Flask test client without an active login session."""
    ds.app.config["TESTING"] = True
    with ds.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def logged_in_client(mock_prtg):
    """Flask test client authenticated with test dashboard credentials."""
    ds.app.config["TESTING"] = True
    with ds.app.test_client() as test_client:
        resp = test_client.post(
            "/login",
            data={"username": "test-admin", "password": "test-pass-12345"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        yield test_client
