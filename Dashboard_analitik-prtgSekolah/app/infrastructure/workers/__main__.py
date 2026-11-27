"""Dedicated operational worker CLI entrypoint (`python -m app.infrastructure.workers`).

Run as a standalone background process alongside multi-worker WSGI (Gunicorn/Waitress)
so PRTG sync, incident correlation, and recovery rechecks run reliably once without
duplicating threads per web worker (NFR-01, SAD §10.1).
"""

from __future__ import annotations

import argparse
import signal
import time

from app import create_app
from app.common.logging import format_structured_log, get_logger
from app.config import settings
from app.infrastructure.workers.operational_worker import run_single_operational_cycle

_logger = get_logger("prtg.worker.cli")
_stop_requested = False


def _handle_stop_signal(_signum, _frame) -> None:
    global _stop_requested
    _stop_requested = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRTG School Operational Background Worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sync/correlation/recheck cycle and exit.",
    )
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, _handle_stop_signal)
    signal.signal(signal.SIGTERM, _handle_stop_signal)

    flask_app = create_app()
    sync_interval = max(settings.interval_pencatat, 15)

    if args.once:
        summary = run_single_operational_cycle(flask_app)
        _logger.info(format_structured_log("INFO", "worker_cycle_done", **summary))
        return 0

    _logger.info(
        format_structured_log("INFO", "worker_started", sync_interval_seconds=sync_interval)
    )
    while not _stop_requested:
        try:
            summary = run_single_operational_cycle(flask_app)
            _logger.info(format_structured_log("INFO", "worker_cycle_done", **summary))
        except Exception as exc:
            _logger.error(
                format_structured_log(
                    "ERROR",
                    "worker_cycle_error",
                    exception_type=type(exc).__name__,
                    message=str(exc),
                )
            )

        # Sleep in 1s increments so SIGTERM/SIGINT stops promptly
        for _ in range(sync_interval):
            if _stop_requested:
                break
            time.sleep(1)

    _logger.info(format_structured_log("INFO", "worker_stopped"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
