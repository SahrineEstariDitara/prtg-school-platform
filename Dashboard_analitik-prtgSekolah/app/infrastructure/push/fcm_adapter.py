"""Firebase Cloud Messaging (FCM) push adapter with safe dry-run mode (ADR-015, NFR-10)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import uuid

from app.config import sanitize_text, settings


@dataclass(frozen=True)
class PushResult:
    provider: str
    status: str  # "sent" | "dry_run" | "failed"
    message_id: Optional[str] = None
    error_message: Optional[str] = None


class FCMAdapter:
    """Server-side push delivery adapter; supports dry-run when FCM credentials are not enabled."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        service_account_path: Optional[str] = None,
    ) -> None:
        self.enabled = settings.fcm_enabled if enabled is None else bool(enabled)
        self.service_account_path = (
            settings.fcm_service_account_path
            if service_account_path is None
            else service_account_path
        )
        self._firebase_app = None

    def _ensure_firebase(self):
        if self._firebase_app is not None:
            return self._firebase_app
        import firebase_admin  # type: ignore[import-not-found]
        from firebase_admin import credentials  # type: ignore[import-not-found]

        if not firebase_admin._apps:
            cred = credentials.Certificate(self.service_account_path)
            self._firebase_app = firebase_admin.initialize_app(cred)
        else:
            self._firebase_app = firebase_admin.get_app()
        return self._firebase_app

    def send_push(
        self,
        *,
        token: str,
        platform: str,
        title: str,
        body: str,
        data: Optional[dict[str, str]] = None,
    ) -> PushResult:
        """Send minimal push notification payload; never exposes secrets."""
        provider = "apns" if platform == "ios" else "fcm"
        if not self.enabled:
            return PushResult(
                provider=provider,
                status="dry_run",
                message_id=f"dryrun-{uuid.uuid4().hex[:12]}",
            )

        if not self.service_account_path:
            return PushResult(
                provider=provider,
                status="failed",
                error_message="FCM_SERVICE_ACCOUNT_PATH belum dikonfigurasi.",
            )

        try:
            self._ensure_firebase()
            from firebase_admin import messaging  # type: ignore[import-not-found]

            str_data = {str(k): str(v) for k, v in (data or {}).items()}
            message = messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body),
                data=str_data,
            )
            msg_id = messaging.send(message)
            return PushResult(provider=provider, status="sent", message_id=str(msg_id))
        except Exception as exc:
            return PushResult(
                provider=provider,
                status="failed",
                error_message=sanitize_text(f"{type(exc).__name__}: {exc}"),
            )
