"""Structured request correlation and sanitized logging utilities (NFR-09)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import sys
import time
import uuid

from flask import Flask, g, request

from app.config import sanitize_text

_REQUEST_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9._-]{4,64}$")


def generate_request_id() -> str:
    """Create a compact unique request correlation ID."""
    return f"req-{uuid.uuid4().hex[:12]}"


class _SafeStreamHandler(logging.StreamHandler):
    """StreamHandler resilient against Windows cp1252 console encoding errors."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except UnicodeEncodeError:
            msg = self.format(record)
            enc = getattr(self.stream, "encoding", None) or "utf-8"
            safe = msg.encode(enc, errors="replace").decode(enc, errors="replace")
            self.stream.write(safe + self.terminator)
            self.flush()


def get_logger(name: str = "prtg.app") -> logging.Logger:
    """Return a configured structured logger without duplicate handlers."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = _SafeStreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def format_structured_log(level: str, event: str, **fields: object) -> str:
    """Build a sanitized single-line JSON log payload."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "level": level.upper(),
        "event": event,
    }
    for key, value in fields.items():
        if isinstance(value, str):
            payload[key] = sanitize_text(value)
        else:
            payload[key] = value
    return json.dumps(payload, ensure_ascii=False)


def init_request_logging(app: Flask) -> None:
    """Attach X-Request-ID correlation and structured HTTP access logs to Flask."""
    if app.extensions.get("prtg_request_logging_initialized"):
        return

    http_logger = get_logger("prtg.http")

    @app.before_request
    def _attach_request_context() -> None:
        incoming = (request.headers.get("X-Request-ID") or "").strip()
        if incoming and _REQUEST_ID_SAFE_RE.match(incoming):
            g.request_id = incoming
        else:
            g.request_id = generate_request_id()
        g.request_start = time.perf_counter()

    @app.after_request
    def _log_request_and_attach_header(response):
        req_id = getattr(g, "request_id", None) or generate_request_id()
        response.headers["X-Request-ID"] = req_id

        start = getattr(g, "request_start", None)
        duration_ms = round((time.perf_counter() - start) * 1000, 2) if start is not None else 0.0

        http_logger.info(
            format_structured_log(
                "INFO",
                "http_request",
                request_id=req_id,
                method=request.method,
                path=request.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        )
        return response

    app.extensions["prtg_request_logging_initialized"] = True
