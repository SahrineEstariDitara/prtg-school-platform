"""Server-side PRTG HTTP API adapter with controlled timeout, retry, and recheck (SAD §6.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Iterable, Optional

import requests
import urllib3

from app.config import sanitize_text, settings
from app.integrations.prtg.normalizer import normalize_prtg_status, parse_prtg_duration_hours

PRTG_DOWN_STATUS_CODES = (5, 13, 14)
PRTG_ONLINE_STATUS_CODES = (3,)
_TRANSIENT_HTTP_STATUS = {502, 503, 504}


@dataclass(frozen=True)
class PRTGTelemetryBatch:
    """Normalized response batch from a PRTG telemetry read."""

    is_valid: bool
    checked_at: datetime
    sensors: list[dict] = field(default_factory=list)
    error_message: Optional[str] = None


@dataclass(frozen=True)
class PRTGRecheckResult:
    """Structured result for live PRTG verification recheck of required sensors."""

    is_valid: bool
    checked_at: datetime
    sensors_by_id: dict[str, dict] = field(default_factory=dict)
    missing_sensor_ids: list[str] = field(default_factory=list)
    error_message: Optional[str] = None


class PRTGAdapter:
    """Centralized server-side client for PRTG `/api/table.json`."""

    def __init__(
        self,
        *,
        server: Optional[str] = None,
        username: Optional[str] = None,
        passhash: Optional[str] = None,
        password: Optional[str] = None,
        verify_ssl: Optional[bool] = None,
        timeout: Optional[int] = None,
        count: Optional[int] = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.server = (server if server is not None else settings.prtg_server).rstrip("/")
        self.username = username if username is not None else settings.prtg_username
        self.passhash = passhash if passhash is not None else settings.prtg_passhash
        self.password = password if password is not None else settings.prtg_password
        self.verify_ssl = settings.prtg_verify_ssl if verify_ssl is None else bool(verify_ssl)
        self.timeout = settings.prtg_timeout if timeout is None else int(timeout)
        self.count = settings.prtg_count if count is None else int(count)
        self.max_retries = max(int(max_retries), 0)
        self.retry_backoff_seconds = max(float(retry_backoff_seconds), 0.0)
        self._session = session or requests.Session()

        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _auth_params(self) -> dict[str, str]:
        params = {"username": str(self.username).strip()}
        if self.passhash and not self.passhash.startswith("ISI_"):
            params["passhash"] = str(self.passhash).strip()
        elif self.password and not self.password.startswith("ISI_"):
            params["password"] = str(self.password).strip()
        return params

    def _request_table(self, params: dict) -> dict:
        if not self.server:
            raise ValueError("PRTG_SERVER belum dikonfigurasi.")

        url = f"{self.server}/api/table.json"
        merged_params = {**params, **self._auth_params()}

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.get(
                    url,
                    params=merged_params,
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                )
                if resp.status_code in _TRANSIENT_HTTP_STATUS and attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError("Format JSON PRTG tidak valid (bukan dict).")
                return data
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * (attempt + 1))
                    continue
                raise
            except Exception:
                raise

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _decorate_sensor(raw: dict) -> dict:
        status_raw = raw.get("status_raw")
        try:
            code = int(status_raw) if status_raw is not None and str(status_raw).strip() != "" else None
        except ValueError:
            code = None

        normalized = normalize_prtg_status(raw.get("status"), code)
        downtime_hours = parse_prtg_duration_hours(
            raw.get("downtimesince"), raw.get("downtimesince_raw")
        )
        uptime_hours = parse_prtg_duration_hours(raw.get("uptimesince"))

        return {
            **raw,
            "raw_status_code": code,
            "normalized_status": normalized,
            "downtime_hours": downtime_hours,
            "uptime_hours": uptime_hours,
        }

    def fetch_sensors(
        self,
        *,
        filter_status: Optional[Iterable[int]] = None,
        extra_params: Optional[dict] = None,
    ) -> PRTGTelemetryBatch:
        """Fetch sensors from PRTG and normalize statuses; returns is_valid=False on failure."""
        checked_at = datetime.now(timezone.utc)
        params: dict = {
            "content": "sensors",
            "columns": (
                "objid,parentid,group,device,sensor,status,status_raw,"
                "lastcheck,downtimesince,downtimesince_raw,uptimesince,message"
            ),
            "count": self.count,
        }
        if filter_status is not None:
            params["filter_status"] = list(filter_status)
        if extra_params:
            params.update(extra_params)

        try:
            data = self._request_table(params)
            sensors_raw = data.get("sensors", [])
            if not isinstance(sensors_raw, list):
                return PRTGTelemetryBatch(
                    is_valid=False,
                    checked_at=checked_at,
                    error_message="Payload 'sensors' PRTG bukan list.",
                )
            decorated = [self._decorate_sensor(s) for s in sensors_raw if isinstance(s, dict)]
            return PRTGTelemetryBatch(is_valid=True, checked_at=checked_at, sensors=decorated)
        except Exception as exc:
            return PRTGTelemetryBatch(
                is_valid=False,
                checked_at=checked_at,
                error_message=sanitize_text(f"{type(exc).__name__}: {exc}"),
            )

    def fetch_all_sensors(self) -> PRTGTelemetryBatch:
        return self.fetch_sensors()

    def fetch_down_sensors(self) -> PRTGTelemetryBatch:
        return self.fetch_sensors(filter_status=PRTG_DOWN_STATUS_CODES)

    def fetch_online_sensors(self) -> PRTGTelemetryBatch:
        return self.fetch_sensors(filter_status=PRTG_ONLINE_STATUS_CODES)

    def fetch_devices(self) -> list[dict]:
        data = self._request_table(
            {
                "content": "devices",
                "columns": "objid,parentid,group,device,host,status,status_raw",
                "count": self.count,
            }
        )
        devices = data.get("devices", [])
        return devices if isinstance(devices, list) else []

    def recheck_sensors(self, prtg_sensor_ids: Iterable[str | int]) -> PRTGRecheckResult:
        """Live PRTG recheck for required recovery sensors (FR-018, FR-019, NFR-05).

        Never returns is_valid=True on network error, timeout, or invalid payload.
        """
        checked_at = datetime.now(timezone.utc)
        target_ids = sorted({str(sid).strip() for sid in prtg_sensor_ids if str(sid).strip()})
        if not target_ids:
            return PRTGRecheckResult(
                is_valid=False,
                checked_at=checked_at,
                error_message="Daftar sensor wajib untuk recheck kosong.",
            )

        batch = self.fetch_sensors(extra_params={"filter_objid": target_ids})
        if not batch.is_valid:
            return PRTGRecheckResult(
                is_valid=False,
                checked_at=batch.checked_at,
                missing_sensor_ids=target_ids,
                error_message=batch.error_message,
            )

        found: dict[str, dict] = {}
        for sensor in batch.sensors:
            oid = str(sensor.get("objid", "")).strip()
            if oid in target_ids:
                found[oid] = sensor

        missing = [sid for sid in target_ids if sid not in found]
        return PRTGRecheckResult(
            is_valid=True,
            checked_at=batch.checked_at,
            sensors_by_id=found,
            missing_sensor_ids=missing,
        )
