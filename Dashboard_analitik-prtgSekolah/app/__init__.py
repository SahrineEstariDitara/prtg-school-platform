"""PRTG School Platform — Shared Backend Package & Flask App Factory (SAD §4.4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from flask import Flask

from app.api.v1 import api_v1
from app.common.logging import init_request_logging
from app.config import settings
from app.infrastructure.database import db, migrate
from app.infrastructure.database import models as _models  # noqa: F401 — registers all models for Alembic

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def register_api_blueprints(flask_app: Flask) -> None:
    """Mount `/api/v1` blueprint idempotently on any Flask app instance."""
    if "api_v1" not in flask_app.blueprints:
        flask_app.register_blueprint(api_v1)


def init_db_on_app(
    flask_app: Flask,
    *,
    database_url: Optional[str] = None,
) -> bool:
    """Attach SQLAlchemy + Flask-Migrate to an existing Flask app if DATABASE_URL is set."""
    uri = (database_url if database_url is not None else settings.database_url).strip()
    if not uri:
        return False

    flask_app.config["SQLALCHEMY_DATABASE_URI"] = uri
    flask_app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    flask_app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {"pool_pre_ping": True})

    if "sqlalchemy" not in flask_app.extensions:
        db.init_app(flask_app)
    if "migrate" not in flask_app.extensions:
        migrate.init_app(flask_app, db, directory=str(MIGRATIONS_DIR))
    return True


def create_app(test_config: Optional[Mapping[str, Any]] = None) -> Flask:
    """Create a configured Flask application instance (for CLI, Alembic, tests, & API v1)."""
    flask_app = Flask(__name__)
    flask_app.secret_key = settings.flask_secret_key
    flask_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

    if settings.database_url:
        flask_app.config["SQLALCHEMY_DATABASE_URI"] = settings.database_url

    if test_config:
        flask_app.config.update(test_config)

    if flask_app.config.get("SQLALCHEMY_DATABASE_URI"):
        db.init_app(flask_app)
        migrate.init_app(flask_app, db, directory=str(MIGRATIONS_DIR))

    init_request_logging(flask_app)
    register_api_blueprints(flask_app)
    return flask_app
