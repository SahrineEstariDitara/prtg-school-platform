"""Central SQLAlchemy model registry (imported by Alembic and app factory)."""

from app.infrastructure.database.audit_models import AuditLog
from app.modules.assignments.models import IncidentAssignment
from app.modules.auth.models import RefreshToken, Role
from app.modules.incidents.models import Incident, IncidentHistory, IncidentSensor
from app.modules.notifications.models import DeviceToken, NotificationLog
from app.modules.regions.models import Region, UserRegion
from app.modules.responses.models import ResponseLog, SyncOperation
from app.modules.schools.models import (
    PrtgDevice,
    PrtgSensor,
    School,
    SchoolRecoveryPolicy,
    SchoolRecoveryPolicySensor,
)
from app.modules.users.models import User
from app.modules.verification.models import PrtgRecheckLog, VerificationRecord

ALL_MODELS = (
    Role,
    RefreshToken,
    Region,
    User,
    UserRegion,
    School,
    PrtgDevice,
    PrtgSensor,
    SchoolRecoveryPolicy,
    SchoolRecoveryPolicySensor,
    Incident,
    IncidentSensor,
    IncidentHistory,
    IncidentAssignment,
    ResponseLog,
    SyncOperation,
    VerificationRecord,
    PrtgRecheckLog,
    DeviceToken,
    NotificationLog,
    AuditLog,
)

__all__ = [
    "ALL_MODELS",
    "AuditLog",
    "DeviceToken",
    "Incident",
    "IncidentAssignment",
    "IncidentHistory",
    "IncidentSensor",
    "NotificationLog",
    "PrtgDevice",
    "PrtgRecheckLog",
    "PrtgSensor",
    "RefreshToken",
    "Region",
    "ResponseLog",
    "Role",
    "School",
    "SchoolRecoveryPolicy",
    "SchoolRecoveryPolicySensor",
    "SyncOperation",
    "User",
    "UserRegion",
    "VerificationRecord",
]
