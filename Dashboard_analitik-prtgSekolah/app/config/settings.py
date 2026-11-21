"""Centralized environment configuration for PRTG School Platform (M2-M3).

Loads settings from `.env` (without committing secrets to source code) and
provides redaction helpers so credentials/tokens/DB passwords never leak to logs.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets as _secrets
from typing import Iterable, Optional

try:
    from dotenv import load_dotenv as _dotenv_load
except ImportError:
    _dotenv_load = None


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = BASE_DIR / ".env"

_REDACTED = "[REDACTED]"

_QUERY_SECRET_PATTERNS = (
    re.compile(r"([?&](?:password|passhash|token|api_token)=)([^&\s]+)", re.IGNORECASE),
    re.compile(r"(/sendMessage/)([^/\s?]+)", re.IGNORECASE),
    re.compile(r"(/getChats/)([^/\s?]+)", re.IGNORECASE),
    re.compile(r"((?:postgres(?:ql)?(?:\+\w+)?)://[^:\s/@]+:)([^@\s]+)(@)", re.IGNORECASE),
)


def load_env_file(env_file: Optional[Path | str] = None, *, override: bool = False) -> bool:
    """Load key=value pairs from `.env` into `os.environ`."""
    path = Path(env_file) if env_file is not None else DEFAULT_ENV_FILE
    if not path.is_file():
        return False

    if _dotenv_load is not None:
        return bool(_dotenv_load(dotenv_path=path, override=override))

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and (override or key not in os.environ):
            os.environ[key] = value
    return True


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "y", "on"):
        return True
    if lowered in ("0", "false", "no", "n", "off"):
        return False
    return default


def _env_csv_list(name: str, default: Iterable[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return list(default)
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or list(default)


@dataclass(frozen=True)
class Settings:
    """Typed runtime settings loaded from environment variables."""

    # PRTG integration
    prtg_server: str
    prtg_username: str
    prtg_passhash: str
    prtg_password: str
    prtg_verify_ssl: bool
    prtg_timeout: int
    prtg_count: int
    prtg_stale_threshold_seconds: int

    # Flask web dashboard (existing)
    flask_host: str
    flask_port: int
    flask_secret_key: str
    dashboard_username: str
    dashboard_password: str
    interval_pencatat: int

    # GreenAPI WhatsApp (existing bot)
    greenapi_api_url: str
    greenapi_id_instance: str
    greenapi_token: str
    wa_target: str
    jadwal_wa: list[str]
    check_interval: int

    # PostgreSQL operational DB (M3)
    database_url: str
    test_database_url: str

    # Initial admin seed (M3)
    admin_seed_username: str
    admin_seed_password: str
    admin_seed_full_name: str

    # JWT authentication (M4)
    jwt_secret_key: str
    jwt_access_expires_minutes: int
    jwt_refresh_expires_days: int

    # Operational workflow, uploads & push (M5)
    upload_dir: str
    max_attachment_bytes: int
    fcm_enabled: bool
    fcm_service_account_path: str
    enable_operational_worker: bool

    def secret_values(self) -> tuple[str, ...]:
        """Return non-empty secret values to redact from logs."""
        candidates = (
            self.prtg_password,
            self.prtg_passhash,
            self.dashboard_password,
            self.flask_secret_key,
            self.greenapi_token,
            self.admin_seed_password,
            self.jwt_secret_key,
        )
        return tuple(
            v for v in candidates
            if v and len(v) >= 4 and not v.startswith("ISI_")
        )


def _build_settings() -> Settings:
    secret_key = _env_str("FLASK_SECRET_KEY", "")
    if not secret_key or secret_key.startswith("ISI_"):
        secret_key = _secrets.token_hex(32)

    jwt_secret = _env_str("JWT_SECRET_KEY", "")
    if not jwt_secret or jwt_secret.startswith("ISI_"):
        jwt_secret = secret_key

    return Settings(
        prtg_server=_env_str("PRTG_SERVER", "").rstrip("/"),
        prtg_username=_env_str("PRTG_USERNAME", ""),
        prtg_passhash=_env_str("PRTG_PASSHASH", ""),
        prtg_password=_env_str("PRTG_PASSWORD", ""),
        prtg_verify_ssl=_env_bool("PRTG_VERIFY_SSL", False),
        prtg_timeout=_env_int("PRTG_TIMEOUT", 30),
        prtg_count=_env_int("PRTG_COUNT", 50000),
        prtg_stale_threshold_seconds=_env_int("PRTG_STALE_THRESHOLD_SECONDS", 180),
        flask_host=_env_str("FLASK_HOST", "0.0.0.0"),
        flask_port=_env_int("FLASK_PORT", 5001),
        flask_secret_key=secret_key,
        dashboard_username=_env_str("DASHBOARD_USERNAME", ""),
        dashboard_password=_env_str("DASHBOARD_PASSWORD", ""),
        interval_pencatat=_env_int("INTERVAL_PENCATAT", 60),
        greenapi_api_url=_env_str("GREENAPI_API_URL", "").rstrip("/"),
        greenapi_id_instance=_env_str("GREENAPI_ID_INSTANCE", ""),
        greenapi_token=_env_str("GREENAPI_TOKEN", ""),
        wa_target=_env_str("WA_TARGET", ""),
        jadwal_wa=_env_csv_list("JADWAL_WA", ("07:00", "11:00", "15:00")),
        check_interval=_env_int("CHECK_INTERVAL", 300),
        database_url=_env_str("DATABASE_URL", ""),
        test_database_url=_env_str("TEST_DATABASE_URL", ""),
        admin_seed_username=_env_str("ADMIN_SEED_USERNAME", "admin"),
        admin_seed_password=_env_str("ADMIN_SEED_PASSWORD", ""),
        admin_seed_full_name=_env_str("ADMIN_SEED_FULL_NAME", "Administrator Sistem"),
        jwt_secret_key=jwt_secret,
        jwt_access_expires_minutes=_env_int("JWT_ACCESS_EXPIRES_MINUTES", 30),
        jwt_refresh_expires_days=_env_int("JWT_REFRESH_EXPIRES_DAYS", 7),
        upload_dir=_env_str("UPLOAD_DIR", "uploads"),
        max_attachment_bytes=_env_int("MAX_ATTACHMENT_BYTES", 5 * 1024 * 1024),
        fcm_enabled=_env_bool("FCM_ENABLED", False),
        fcm_service_account_path=_env_str("FCM_SERVICE_ACCOUNT_PATH", ""),
        enable_operational_worker=_env_bool("ENABLE_OPERATIONAL_WORKER", False),
    )


def reload_settings(env_file: Optional[Path | str] = None, *, override: bool = False) -> Settings:
    """Reload `.env` and refresh the module-level `settings` singleton."""
    global settings
    load_env_file(env_file, override=override)
    settings = _build_settings()
    return settings


def sanitize_text(text: object, extra_secrets: Iterable[str] = ()) -> str:
    """Mask known secrets, DB URL passwords, and credential URL parameters."""
    output = str(text)
    if not output:
        return output

    all_secrets = list(settings.secret_values())
    for extra in extra_secrets:
        if extra and len(extra) >= 4:
            all_secrets.append(extra)

    for secret in sorted(set(all_secrets), key=len, reverse=True):
        output = output.replace(secret, _REDACTED)

    for pattern in _QUERY_SECRET_PATTERNS:
        if pattern.groups == 3:
            output = pattern.sub(rf"\1{_REDACTED}\3", output)
        else:
            output = pattern.sub(rf"\1{_REDACTED}", output)

    return output


load_env_file(DEFAULT_ENV_FILE, override=False)
settings: Settings = _build_settings()
