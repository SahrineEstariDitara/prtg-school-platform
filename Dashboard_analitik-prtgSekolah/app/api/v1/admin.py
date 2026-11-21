"""Admin endpoints: roles, users, regions, school mapping, recovery policy, audit logs (FR-021..026)."""

from __future__ import annotations

from datetime import timezone

from flask import g, request
from sqlalchemy import select

from app.api.v1 import api_v1
from app.common.errors import BadRequestError, NotFoundError
from app.common.rbac import (
    ensure_region_in_scope,
    require_auth,
    require_permission,
    resolve_user_region_ids,
)
from app.common.responses import success_response
from app.infrastructure.database import db
from app.infrastructure.database.audit_models import AuditLog
from app.modules.auth.models import Role
from app.modules.regions.service import create_region, list_regions, update_region
from app.modules.schools.admin_service import (
    get_recovery_policy,
    update_school,
    upsert_recovery_policy,
)
from app.modules.schools.models import School
from app.modules.users.service import create_user, list_users, update_user


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequestError("Body JSON wajib berupa objek.")
    return data


@api_v1.route("/roles", methods=["GET"])
@require_auth
def get_roles_endpoint():
    roles = db.session.scalars(select(Role).order_by(Role.id.asc())).all()
    return success_response(
        [
            {"id": r.id, "code": r.code, "name": r.name, "description": r.description}
            for r in roles
        ]
    )


@api_v1.route("/users", methods=["GET"])
@require_permission("users.manage")
def list_users_endpoint():
    role_code = (request.args.get("role_code") or "").strip() or None
    query = (request.args.get("q") or "").strip() or None
    raw_rid = (request.args.get("region_id") or "").strip()
    region_id = None
    if raw_rid:
        try:
            region_id = int(raw_rid)
        except ValueError as exc:
            raise BadRequestError("region_id harus berupa integer.") from exc

    raw_active = (request.args.get("is_active") or "").strip().lower()
    is_active = None
    if raw_active in ("true", "1"):
        is_active = True
    elif raw_active in ("false", "0"):
        is_active = False

    users = list_users(
        db.session,
        role_code=role_code,
        region_id=region_id,
        is_active=is_active,
        query=query,
    )
    return success_response({"items": users, "total": len(users)})


@api_v1.route("/users", methods=["POST"])
@require_permission("users.manage")
def create_user_endpoint():
    created = create_user(db.session, _json_body(), actor_id=g.current_user.id)
    return success_response(created, status_code=201)


@api_v1.route("/users/<int:user_id>", methods=["PATCH"])
@require_permission("users.manage")
def update_user_endpoint(user_id: int):
    updated = update_user(db.session, user_id, _json_body(), actor_id=g.current_user.id)
    return success_response(updated)


@api_v1.route("/regions", methods=["GET"])
@require_auth
def list_regions_endpoint():
    allowed = resolve_user_region_ids(g.current_user)
    include_inactive = (request.args.get("include_inactive") or "").lower() in ("1", "true")
    if allowed is not None:
        include_inactive = False
    regions = list_regions(
        db.session,
        allowed_region_ids=allowed,
        include_inactive=include_inactive,
    )
    return success_response({"items": regions, "total": len(regions)})


@api_v1.route("/regions", methods=["POST"])
@require_permission("regions.manage")
def create_region_endpoint():
    created = create_region(db.session, _json_body(), actor_id=g.current_user.id)
    return success_response(created, status_code=201)


@api_v1.route("/regions/<int:region_id>", methods=["PATCH"])
@require_permission("regions.manage")
def update_region_endpoint(region_id: int):
    updated = update_region(db.session, region_id, _json_body(), actor_id=g.current_user.id)
    return success_response(updated)


@api_v1.route("/schools/<int:school_id>", methods=["PATCH"])
@require_permission("regions.manage")
def update_school_endpoint(school_id: int):
    updated = update_school(db.session, school_id, _json_body(), actor_id=g.current_user.id)
    return success_response(updated)


@api_v1.route("/schools/<int:school_id>/recovery-policy", methods=["GET"])
@require_permission("monitoring.view_scope")
def get_school_recovery_policy_endpoint(school_id: int):
    school = db.session.scalar(select(School).where(School.id == school_id))
    if school is None:
        raise NotFoundError("Sekolah tidak ditemukan.")
    ensure_region_in_scope(g.current_user, school.region_id)
    return success_response(get_recovery_policy(db.session, school_id))


@api_v1.route("/schools/<int:school_id>/recovery-policy", methods=["PUT"])
@require_permission("system_config.manage")
def upsert_school_recovery_policy_endpoint(school_id: int):
    updated = upsert_recovery_policy(
        db.session,
        school_id,
        _json_body(),
        actor_id=g.current_user.id,
    )
    return success_response(updated)


@api_v1.route("/audit-logs", methods=["GET"])
@require_permission("audit.view")
def list_audit_logs_endpoint():
    try:
        page = max(int(request.args.get("page", 1)), 1)
        per_page = max(min(int(request.args.get("per_page", 50)), 200), 1)
    except ValueError as exc:
        raise BadRequestError("page dan per_page harus berupa integer.") from exc

    entity_type = (request.args.get("entity_type") or "").strip() or None
    action = (request.args.get("action") or "").strip() or None

    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)

    rows = db.session.scalars(stmt).all()
    total = len(rows)
    start = (page - 1) * per_page
    sliced = rows[start : start + per_page]

    items = [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "before": row.before_json,
            "after": row.after_json,
            "request_id": row.request_id,
            "ip_address": row.ip_address,
            "created_at": (
                row.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if row.created_at
                else None
            ),
        }
        for row in sliced
    ]
    return success_response(
        {"items": items, "pagination": {"page": page, "per_page": per_page, "total": total}}
    )
