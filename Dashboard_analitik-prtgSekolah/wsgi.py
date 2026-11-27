"""WSGI entrypoint for Waitress / Gunicorn production deployment (SAD §10.1).

Usage:
- Windows (Waitress single-process): waitress-serve --host=127.0.0.1 --port=5001 wsgi:app
- Linux (Gunicorn + separate worker): gunicorn -w 4 -b 127.0.0.1:5001 wsgi:app
"""

from __future__ import annotations

from dashboard_sekolah import app

__all__ = ["app"]
