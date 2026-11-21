"""Structured API error classes and Flask blueprint error handlers (SAD §6.6)."""

from __future__ import annotations

from typing import Any, Optional

from flask import Blueprint
from werkzeug.exceptions import HTTPException

from app.common.logging import format_structured_log, get_logger
from app.common.responses import error_response


class APIError(Exception):
    """Base structured exception translated directly into JSON error responses."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


class BadRequestError(APIError):
    def __init__(self, message: str = "Format request tidak valid.", details=None) -> None:
        super().__init__("BAD_REQUEST", message, 400, details)


class UnauthorizedError(APIError):
    def __init__(self, message: str = "Autentikasi diperlukan atau token tidak valid.") -> None:
        super().__init__("UNAUTHORIZED", message, 401)


class ForbiddenError(APIError):
    def __init__(self, message: str = "Anda tidak memiliki akses ke resource ini.") -> None:
        super().__init__("FORBIDDEN", message, 403)


class NotFoundError(APIError):
    def __init__(self, message: str = "Resource tidak ditemukan.") -> None:
        super().__init__("NOT_FOUND", message, 404)


class ConflictError(APIError):
    def __init__(self, message: str = "Terjadi konflik data atau status.", details=None) -> None:
        super().__init__("CONFLICT", message, 409, details)


class ValidationError(APIError):
    def __init__(self, message: str = "Validasi aturan bisnis gagal.", details=None) -> None:
        super().__init__("VALIDATION_ERROR", message, 422, details)


def register_api_error_handlers(bp: Blueprint) -> None:
    """Ensure every exception inside `/api/v1/*` returns canonical JSON + semantic HTTP code."""
    logger = get_logger("prtg.api.error")

    @bp.errorhandler(APIError)
    def _handle_api_error(exc: APIError):
        return error_response(
            exc.code,
            exc.message,
            status_code=exc.status_code,
            details=exc.details,
        )

    @bp.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException):
        status_code = exc.code or 500
        code_map = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
        }
        return error_response(
            code_map.get(status_code, "HTTP_ERROR"),
            exc.description or "Permintaan tidak dapat diproses.",
            status_code=status_code,
        )

    @bp.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        logger.error(
            format_structured_log(
                "ERROR",
                "unhandled_api_exception",
                exception_type=type(exc).__name__,
                message=str(exc),
            )
        )
        return error_response(
            "INTERNAL_SERVER_ERROR",
            "Terjadi kesalahan internal server.",
            status_code=500,
        )
