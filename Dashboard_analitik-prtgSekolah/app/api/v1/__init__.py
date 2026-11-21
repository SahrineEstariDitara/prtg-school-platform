"""REST API v1 Blueprint (`/api/v1/*`) — aligned with SAD §4.4 & §6."""

from __future__ import annotations

from flask import Blueprint

from app.common.errors import register_api_error_handlers

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")
register_api_error_handlers(api_v1)

# Register all route modules on api_v1
from . import admin as _admin  # noqa: E402, F401
from . import auth as _auth  # noqa: E402, F401
from . import incidents as _incidents  # noqa: E402, F401
from . import monitoring as _monitoring  # noqa: E402, F401
from . import openapi as _openapi  # noqa: E402, F401
from . import sync_and_notifications as _sync_notif  # noqa: E402, F401

__all__ = ["api_v1"]
