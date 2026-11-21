"""Pure PRTG telemetry normalization and freshness helpers (FR-006, NFR-05)."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Optional

from app.modules.schools.models import (
    NETWORK_STATUS_OFFLINE,
    NETWORK_STATUS_ONLINE,
    NETWORK_STATUS_UNKNOWN,
    NETWORK_STATUS_WARNING,
)

# PRTG status_raw numeric codes (documented PRTG API values)
_CODE_ONLINE = {3}
_CODE_WARNING = {4, 10}  # 4=Warning, 10=Unusual
_CODE_OFFLINE = {5, 13, 14}  # 5=Down, 13=Down (Acknowledged), 14=Down (Partial)
_CODE_UNKNOWN = {1, 2, 6, 7, 8, 9, 11, 12}  # Unknown, Scanning, No Probe, Paused variants


def normalize_prtg_status(
    raw_status: object,
    raw_status_code: Optional[int] = None,
) -> str:
    """Normalize raw PRTG status text/code into canonical network_status.

    Handles live PRTG anomalies discovered in M1 audit:
    - Double spaces in paused labels (e.g. "Paused  (paused)") -> "unknown"
    - "Unusual" (1,205 live sensors) -> "warning" (safe default; never fake "online")
    - "Down (Acknowledged)" / "Down (Partial)" -> "offline"
    - Empty / unrecognized / paused -> "unknown"
    """
    text = re.sub(r"\s+", " ", str(raw_status or "").strip().lower())

    if text:
        if text == "up" or text.startswith("up "):
            return NETWORK_STATUS_ONLINE
        if text.startswith("down"):
            return NETWORK_STATUS_OFFLINE
        if text.startswith("warning") or text.startswith("unusual"):
            return NETWORK_STATUS_WARNING
        if text.startswith("paused") or text.startswith("unknown") or text.startswith("no probe"):
            return NETWORK_STATUS_UNKNOWN

    if raw_status_code is not None:
        try:
            code = int(raw_status_code)
        except (TypeError, ValueError):
            code = None
        if code in _CODE_ONLINE:
            return NETWORK_STATUS_ONLINE
        if code in _CODE_OFFLINE:
            return NETWORK_STATUS_OFFLINE
        if code in _CODE_WARNING:
            return NETWORK_STATUS_WARNING
        if code in _CODE_UNKNOWN:
            return NETWORK_STATUS_UNKNOWN

    return NETWORK_STATUS_UNKNOWN


def is_telemetry_stale(
    last_checked_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    threshold_seconds: int = 180,
) -> bool:
    """Return True when telemetry timestamp is missing or older than threshold_seconds."""
    if last_checked_at is None:
        return True

    ref_now = now or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)

    checked = last_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)

    age_seconds = (ref_now - checked).total_seconds()
    return age_seconds > max(int(threshold_seconds), 1)


def parse_prtg_duration_hours(text: object, raw: object = None) -> Optional[float]:
    """Parse PRTG duration string ('1 d 6 h', '3 h 30 m', '45 m') to hours."""
    cleaned = str(text or "").strip().lower()
    if not cleaned:
        return None

    days = hours = minutes = seconds = 0
    matched = False

    for unit, pattern in (
        ("d", r"(\d+)\s*d"),
        ("h", r"(\d+)\s*h"),
        ("m", r"(\d+)\s*m"),
        ("s", r"(\d+)\s*s"),
    ):
        m = re.search(pattern, cleaned)
        if m:
            matched = True
            val = int(m.group(1))
            if unit == "d":
                days = val
            elif unit == "h":
                hours = val
            elif unit == "m":
                minutes = val
            elif unit == "s":
                seconds = val

    if not matched:
        return None

    text_hours = (days * 24) + hours + (minutes / 60.0) + (seconds / 3600.0)

    if raw is None:
        return text_hours

    try:
        raw_val = float(str(raw).strip().replace(",", "."))
    except ValueError:
        return text_hours

    if raw_val <= 0:
        return text_hours

    only_days = ("h" not in cleaned) and ("m" not in cleaned) and ("s" not in cleaned)
    tolerance = 24.5 if only_days else 0.1
    candidates = (raw_val * 24.0, raw_val, raw_val / 3600.0)
    close = [c for c in candidates if abs(c - text_hours) <= tolerance]
    if not close:
        return text_hours
    return min(close, key=lambda c: abs(c - text_hours))
