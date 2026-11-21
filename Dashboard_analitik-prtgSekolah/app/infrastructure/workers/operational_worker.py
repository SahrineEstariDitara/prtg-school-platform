"""Background operational worker for PRTG sync, incident correlation, and multi-check recovery."""

from __future__ import annotations

import threading
import time
from typing import Optional

from flask import Flask

from app.common.logging import format_structured_log, get_logger
from app.config import settings
from app.infrastructure.database import db
from app.integrations.prtg import PRTGAdapter
from app.modules.incidents.service import correlate_down_schools
from app.modules.schools.sync_service import SchoolSyncService
from app.modules.verification.service import run_pending_recovery_rechecks

_logger = get_logger("prtg.worker")
_worker_lock = threading.Lock()


def run_single_operational_cycle(
    flask_app: Flask,
    *,
    prtg_adapter: Optional[PRTGAdapter] = None,
) -> dict:
    """Execute 1 full operational cycle: PRTG sync -> incident correlation -> pending rechecks."""
    adapter = prtg_adapter or PRTGAdapter()
    with flask_app.app_context():
        batch = adapter.fetch_all_sensors()
        sync_summary = None
        corr_summary = None
        if batch.is_valid:
            sync_summary = SchoolSyncService().sync_snapshot(
                db.session, batch.sensors, checked_at=batch.checked_at
            )
            corr_summary = correlate_down_schools(db.session, now=batch.checked_at)
        else:
            _logger.warning(
                format_structured_log(
                    "WARNING",
                    "prtg_sync_failed",
                    error=batch.error_message or "unknown",
                )
            )

        rechecks = run_pending_recovery_rechecks(db.session, prtg_adapter=adapter)
        return {
            "prtg_valid": batch.is_valid,
            "schools_created": sync_summary.schools_created if sync_summary else 0,
            "incidents_created": corr_summary.incidents_created if corr_summary else 0,
            "rechecks_evaluated": len(rechecks),
        }


def start_operational_worker(flask_app: Flask) -> Optional[threading.Thread]:
    """Start daemon background thread when ENABLE_OPERATIONAL_WORKER=true."""
    if not settings.enable_operational_worker:
        return None

    with _worker_lock:
        if flask_app.extensions.get("prtg_operational_worker_started"):
            return None

        def _loop() -> None:
            sync_interval = max(settings.interval_pencatat, 15)
            last_sync = 0.0
            while True:
                now_mono = time.monotonic()
                try:
                    with flask_app.app_context():
                        if now_mono - last_sync >= sync_interval:
                            batch = PRTGAdapter().fetch_all_sensors()
                            if batch.is_valid:
                                SchoolSyncService().sync_snapshot(
                                    db.session, batch.sensors, checked_at=batch.checked_at
                                )
                                correlate_down_schools(db.session, now=batch.checked_at)
                            last_sync = now_mono
                        run_pending_recovery_rechecks(db.session)
                except Exception as exc:
                    _logger.error(
                        format_structured_log(
                            "ERROR",
                            "operational_worker_error",
                            exception_type=type(exc).__name__,
                            message=str(exc),
                        )
                    )
                time.sleep(15)

        t = threading.Thread(target=_loop, name="prtg-operational-worker", daemon=True)
        t.start()
        flask_app.extensions["prtg_operational_worker_started"] = True
        return t
