"""M2 security, configuration loading, and structured logging tests."""

from __future__ import annotations

from pathlib import Path
import re

from app.config import reload_settings, sanitize_text


BACKEND_ROOT = Path(__file__).resolve().parents[1]

ACTIVE_PYTHON_FILES = [
    BACKEND_ROOT / "dashboard_sekolah.py",
    BACKEND_ROOT / "prtg_sekolah.py",
    BACKEND_ROOT / "tab_prtg.py",
    BACKEND_ROOT / "get_group_id.py",
    BACKEND_ROOT / "app" / "config" / "settings.py",
    BACKEND_ROOT / "app" / "common" / "logging.py",
]

HARDCODED_SECRET_RE = re.compile(
    r"""^\s*(?:PRTG_PASSWORD|PRTG_PASSHASH|DASHBOARD_PASSWORD|GREENAPI_TOKEN|app\.secret_key)\s*=\s*['"][^'"]+['"]""",
    re.MULTILINE,
)


def test_no_hardcoded_secrets_in_active_python_files():
    """Ensure active Python source files contain zero literal passwords/tokens."""
    offenders = []
    for path in ACTIVE_PYTHON_FILES:
        content = path.read_text(encoding="utf-8")
        matches = HARDCODED_SECRET_RE.findall(content)
        if matches:
            offenders.append((path.name, len(matches)))
    assert offenders == [], f"Found hardcoded secrets in active files: {offenders}"


def test_env_example_keys_are_recognized_by_settings(tmp_path: Path, monkeypatch):
    """Verify every key in `.env.example` maps cleanly into `app.config.settings`."""
    env_example = BACKEND_ROOT / ".env.example"
    assert env_example.is_file()

    keys = []
    for raw_line in env_example.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())

    expected_keys = {
        "PRTG_SERVER",
        "PRTG_USERNAME",
        "PRTG_PASSHASH",
        "PRTG_PASSWORD",
        "PRTG_VERIFY_SSL",
        "PRTG_TIMEOUT",
        "PRTG_COUNT",
        "PRTG_STALE_THRESHOLD_SECONDS",
        "FLASK_HOST",
        "FLASK_PORT",
        "FLASK_SECRET_KEY",
        "DASHBOARD_USERNAME",
        "DASHBOARD_PASSWORD",
        "INTERVAL_PENCATAT",
        "GREENAPI_API_URL",
        "GREENAPI_ID_INSTANCE",
        "GREENAPI_TOKEN",
        "WA_TARGET",
        "JADWAL_WA",
        "CHECK_INTERVAL",
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "ADMIN_SEED_USERNAME",
        "ADMIN_SEED_PASSWORD",
        "ADMIN_SEED_FULL_NAME",
        "JWT_SECRET_KEY",
        "JWT_ACCESS_EXPIRES_MINUTES",
        "JWT_REFRESH_EXPIRES_DAYS",
        "UPLOAD_DIR",
        "MAX_ATTACHMENT_BYTES",
        "FCM_ENABLED",
        "FCM_SERVICE_ACCOUNT_PATH",
        "ENABLE_OPERATIONAL_WORKER",
    }
    assert set(keys) == expected_keys

    # Reload from an isolated temporary env file and confirm typed parsing
    custom_env = tmp_path / ".env.test"
    custom_env.write_text(
        "\n".join(
            [
                "PRTG_SERVER=https://prtg.example.org/",
                "PRTG_USERNAME=env-user",
                "PRTG_PASSWORD=super-secret-pass",
                "PRTG_VERIFY_SSL=true",
                "PRTG_TIMEOUT=45",
                "FLASK_PORT=5099",
                "JADWAL_WA=08:00,14:00",
            ]
        ),
        encoding="utf-8",
    )
    loaded = reload_settings(custom_env, override=True)
    assert loaded.prtg_server == "https://prtg.example.org"
    assert loaded.prtg_username == "env-user"
    assert loaded.prtg_verify_ssl is True
    assert loaded.prtg_timeout == 45
    assert loaded.flask_port == 5099
    assert loaded.jadwal_wa == ["08:00", "14:00"]


def test_sanitize_text_masks_credentials_and_url_tokens():
    """Verify passwords, DB URL passwords, tokens, and query secrets are redacted."""
    raw = (
        "Failed GET http://prtg.local/api/table.json?username=u&password=MySecret99&passhash=123456 "
        "and POST https://api.greenapi.com/waInstance1/sendMessage/abcdef1234567890 "
        "DB=postgresql+psycopg://postgres:DbSecretPass!@localhost:5433/prtg_school_db"
    )
    cleaned = sanitize_text(raw, extra_secrets=["MySecret99"])
    assert "MySecret99" not in cleaned
    assert "123456" not in cleaned
    assert "abcdef1234567890" not in cleaned
    assert "DbSecretPass!" not in cleaned
    assert "[REDACTED]" in cleaned


def test_http_response_includes_request_id_header(client):
    """NFR-09: every HTTP response must expose a correlation X-Request-ID header."""
    resp = client.get("/login")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID", "").startswith("req-")

    custom = client.get("/login", headers={"X-Request-ID": "trace-custom-001"})
    assert custom.headers.get("X-Request-ID") == "trace-custom-001"
