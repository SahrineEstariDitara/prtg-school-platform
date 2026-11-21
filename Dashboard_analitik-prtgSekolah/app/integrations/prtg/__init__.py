"""Server-side PRTG Network Monitor API integration adapter."""

from .adapter import PRTGAdapter, PRTGRecheckResult, PRTGTelemetryBatch
from .normalizer import (
    is_telemetry_stale,
    normalize_prtg_status,
    parse_prtg_duration_hours,
)

__all__ = [
    "PRTGAdapter",
    "PRTGRecheckResult",
    "PRTGTelemetryBatch",
    "is_telemetry_stale",
    "normalize_prtg_status",
    "parse_prtg_duration_hours",
]
