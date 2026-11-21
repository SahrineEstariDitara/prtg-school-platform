"""Monitoring & school endpoints with server-side region scope enforcement (FR-003..005, FR-024)."""

from __future__ import annotations

from flask import g, request
from sqlalchemy import select

from app.api.v1 import api_v1
from app.common.errors import BadRequestError, ForbiddenError, NotFoundError
from app.common.rbac import (
    ensure_region_in_scope,
    require_permission,
    resolve_user_region_ids,
)
from app.common.responses import success_response
from app.infrastructure.database import db
from app.modules.monitoring.service import MonitoringService
from app.modules.schools.models import School


def _parse_int_query(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise BadRequestError(f"Parameter '{name}' harus berupa angka integer.") from exc


@api_v1.route("/dashboard", methods=["GET"])
@require_permission("monitoring.view_scope")
def get_dashboard():
    user = g.current_user
    allowed_regions = resolve_user_region_ids(user)
    requested_region = _parse_int_query("region_id")
    if requested_region is not None:
        ensure_region_in_scope(user, requested_region)
        target_regions = [requested_region]
    else:
        target_regions = allowed_regions

    svc = MonitoringService()
    summary = svc.get_dashboard_summary(db.session, region_ids=target_regions)
    return success_response(summary)


@api_v1.route("/monitoring/summary", methods=["GET"])
@require_permission("monitoring.view_scope")
def get_monitoring_summary():
    user = g.current_user
    allowed_regions = resolve_user_region_ids(user)
    requested_region = _parse_int_query("region_id")
    if requested_region is not None:
        ensure_region_in_scope(user, requested_region)
        target_regions = [requested_region]
    else:
        target_regions = allowed_regions

    svc = MonitoringService()
    return success_response(svc.get_region_summary(db.session, region_ids=target_regions))


@api_v1.route("/schools", methods=["GET"])
@require_permission("monitoring.view_scope")
def list_schools_endpoint():
    user = g.current_user
    allowed_regions = resolve_user_region_ids(user)
    requested_region = _parse_int_query("region_id")
    if requested_region is not None:
        ensure_region_in_scope(user, requested_region)
        target_regions = [requested_region]
    else:
        target_regions = allowed_regions

    page = _parse_int_query("page", 1) or 1
    per_page = _parse_int_query("per_page", 20) or 20
    network_status = (request.args.get("network_status") or "").strip() or None
    query = (request.args.get("q") or "").strip() or None

    svc = MonitoringService()
    result = svc.list_schools(
        db.session,
        region_ids=target_regions,
        network_status=network_status,
        query=query,
        page=page,
        per_page=per_page,
    )
    return success_response(result)


@api_v1.route("/schools/<int:school_id>", methods=["GET"])
@require_permission("monitoring.view_scope")
def get_school_detail_endpoint(school_id: int):
    user = g.current_user
    school = db.session.scalar(select(School).where(School.id == school_id))
    if school is None:
        raise NotFoundError("Sekolah tidak ditemukan.")

    ensure_region_in_scope(user, school.region_id)

    svc = MonitoringService()
    detail = svc.get_school_detail(db.session, school_id)
    if detail is None:
        raise NotFoundError("Sekolah tidak ditemukan.")
    return success_response(detail)


@api_v1.route("/schools/<int:school_id>/sensors", methods=["GET"])
@require_permission("monitoring.view_scope")
def get_school_sensors_endpoint(school_id: int):
    user = g.current_user
    school = db.session.scalar(select(School).where(School.id == school_id))
    if school is None:
        raise NotFoundError("Sekolah tidak ditemukan.")

    ensure_region_in_scope(user, school.region_id)

    svc = MonitoringService()
    detail = svc.get_school_detail(db.session, school_id)
    if detail is None:
        raise NotFoundError("Sekolah tidak ditemukan.")

    sensors_flat = []
    for dev in detail["devices"]:
        for sn in dev["sensors"]:
            sensors_flat.append(
                {
                    **sn,
                    "device_id": dev["id"],
                    "prtg_device_id": dev["prtg_device_id"],
                    "device_name": dev["raw_name"],
                }
            )

    return success_response(
        {
            "school_id": school.id,
            "school_name": school.name,
            "network_status": detail["network_status"],
            "is_stale": detail["is_stale"],
            "last_checked_at": detail["last_checked_at"],
            "sensors": sensors_flat,
        }
    )
