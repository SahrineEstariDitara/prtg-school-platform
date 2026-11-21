"""M3 unit tests for PRTGAdapter status normalization, freshness, and recheck safety."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import requests

from app.integrations.prtg import (
    PRTGAdapter,
    is_telemetry_stale,
    normalize_prtg_status,
    parse_prtg_duration_hours,
)


def test_normalize_prtg_status_covers_all_live_audit_variants():
    assert normalize_prtg_status("Up") == "online"
    assert normalize_prtg_status("Down") == "offline"
    assert normalize_prtg_status("Down (Acknowledged)") == "offline"
    assert normalize_prtg_status("Down (Partial)") == "offline"
    assert normalize_prtg_status("Warning") == "warning"
    # M1 live audit: 1,205 Unusual sensors -> mapped safely to warning (not online)
    assert normalize_prtg_status("Unusual") == "warning"
    # M1 live audit: PRTG emits double space in "Paused  (paused)" -> unknown (never online)
    assert normalize_prtg_status("Paused  (paused)") == "unknown"
    assert normalize_prtg_status("Paused  (paused by parent)") == "unknown"
    assert normalize_prtg_status("Paused") == "unknown"
    assert normalize_prtg_status("Unknown") == "unknown"
    assert normalize_prtg_status("") == "unknown"
    assert normalize_prtg_status("", raw_status_code=3) == "online"
    assert normalize_prtg_status("", raw_status_code=5) == "offline"


def test_is_telemetry_stale_checks_freshness_threshold():
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(seconds=60)
    stale = now - timedelta(seconds=240)

    assert is_telemetry_stale(None, now=now, threshold_seconds=180) is True
    assert is_telemetry_stale(fresh, now=now, threshold_seconds=180) is False
    assert is_telemetry_stale(stale, now=now, threshold_seconds=180) is True


def test_parse_prtg_duration_hours():
    assert parse_prtg_duration_hours("2 d 3 h 30 m") == 51.5
    assert parse_prtg_duration_hours("") is None


def test_recheck_sensors_success_returns_normalized_map():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "sensors": [
            {"objid": 12136, "sensor": "Ping", "status": "Up", "status_raw": 3},
            {"objid": 12138, "sensor": "Traffic", "status": "Paused  (paused)", "status_raw": 7},
        ]
    }
    mock_session.get.return_value = mock_resp

    adapter = PRTGAdapter(
        server="http://prtg.test.local:8080",
        username="u",
        password="secret-password",
        max_retries=0,
        session=mock_session,
    )
    res = adapter.recheck_sensors(["12136", "12138", "99999"])

    assert res.is_valid is True
    assert res.sensors_by_id["12136"]["normalized_status"] == "online"
    assert res.sensors_by_id["12138"]["normalized_status"] == "unknown"
    assert res.missing_sensor_ids == ["99999"]


def test_recheck_sensors_timeout_returns_invalid_and_masks_secret():
    """TC-04 / NFR-05: PRTG API failure must return is_valid=False, never fake online."""
    mock_session = MagicMock()
    mock_session.get.side_effect = requests.exceptions.Timeout(
        "Timed out calling http://prtg.test.local/api/table.json?password=SuperSecret99"
    )

    adapter = PRTGAdapter(
        server="http://prtg.test.local:8080",
        username="u",
        password="SuperSecret99",
        max_retries=1,
        retry_backoff_seconds=0.0,
        session=mock_session,
    )
    res = adapter.recheck_sensors(["12136"])

    assert res.is_valid is False
    assert res.sensors_by_id == {}
    assert res.missing_sensor_ids == ["12136"]
    assert res.error_message is not None
    assert "SuperSecret99" not in res.error_message
