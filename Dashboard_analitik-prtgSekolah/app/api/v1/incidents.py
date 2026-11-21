"""Incident, assignment, technician response, attachment, and supervisor verification endpoints (M5)."""

from __future__ import annotations

from flask import g, request, send_file

from app.api.v1 import api_v1
from app.common.errors import BadRequestError, ValidationError
from app.common.rbac import require_permission
from app.common.responses import success_response
from app.infrastructure.database import db
from app.modules.assignments.service import (
    list_technicians_workload,
    manual_assign_incident,
    reassign_by_assignment_id,
)
from app.modules.incidents.service import (
    get_incident_detail,
    get_incident_history,
    list_incidents,
    list_school_incidents,
)
from app.modules.responses.service import (
    acknowledge_incident,
    create_response_log,
    get_attachment_file,
    submit_resolution,
)
from app.modules.verification.service import (
    approve_resolution,
    reject_resolution,
    trigger_manual_recheck,
)


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequestError("Body JSON wajib berupa objek.")
    return data


def _parse_int_query(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise BadRequestError(f"Parameter '{name}' harus berupa angka integer.") from exc


@api_v1.route("/incidents", methods=["GET"])
@require_permission("incidents.view_scope")
def list_incidents_endpoint():
    region_id = _parse_int_query("region_id")
    school_id = _parse_int_query("school_id")
    page = _parse_int_query("page", 1) or 1
    per_page = _parse_int_query("per_page", 20) or 20
    status = (request.args.get("status") or "").strip() or None
    assigned_to_me = (request.args.get("assigned_to_me") or "").strip().lower() in ("1", "true")

    result = list_incidents(
        db.session,
        g.current_user,
        region_id=region_id,
        status=status,
        school_id=school_id,
        assigned_to_me=assigned_to_me,
        page=page,
        per_page=per_page,
    )
    return success_response(result)


@api_v1.route("/incidents/<int:incident_id>", methods=["GET"])
@require_permission("incidents.view_scope")
def get_incident_detail_endpoint(incident_id: int):
    return success_response(get_incident_detail(db.session, g.current_user, incident_id))


@api_v1.route("/incidents/<int:incident_id>/history", methods=["GET"])
@require_permission("incidents.view_scope")
def get_incident_history_endpoint(incident_id: int):
    items = get_incident_history(db.session, g.current_user, incident_id)
    return success_response({"incident_id": incident_id, "items": items})


@api_v1.route("/schools/<int:school_id>/incidents", methods=["GET"])
@require_permission("incidents.view_scope")
def list_school_incidents_endpoint(school_id: int):
    page = _parse_int_query("page", 1) or 1
    per_page = _parse_int_query("per_page", 20) or 20
    return success_response(
        list_school_incidents(db.session, g.current_user, school_id, page=page, per_page=per_page)
    )


@api_v1.route("/technicians", methods=["GET"])
@require_permission("assignments.manage")
def list_technicians_endpoint():
    region_id = _parse_int_query("region_id")
    items = list_technicians_workload(db.session, g.current_user, region_id=region_id)
    return success_response({"items": items, "total": len(items)})


@api_v1.route("/incidents/<int:incident_id>/assign", methods=["POST"])
@require_permission("assignments.manage")
def assign_incident_endpoint(incident_id: int):
    body = _json_body()
    try:
        tech_id = int(body.get("technician_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("technician_id wajib berupa integer.") from exc

    res = manual_assign_incident(
        db.session,
        incident_id=incident_id,
        technician_id=tech_id,
        actor=g.current_user,
        reason=str(body.get("reason") or ""),
    )
    return success_response(res)


@api_v1.route("/assignments/<int:assignment_id>", methods=["PATCH"])
@require_permission("assignments.manage")
def update_assignment_endpoint(assignment_id: int):
    body = _json_body()
    try:
        tech_id = int(body.get("technician_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("technician_id wajib berupa integer.") from exc

    res = reassign_by_assignment_id(
        db.session,
        assignment_id=assignment_id,
        technician_id=tech_id,
        actor=g.current_user,
        reason=str(body.get("reason") or ""),
    )
    return success_response(res)


@api_v1.route("/incidents/<int:incident_id>/acknowledge", methods=["POST"])
@require_permission("responses.create")
def acknowledge_incident_endpoint(incident_id: int):
    res = acknowledge_incident(db.session, incident_id=incident_id, actor=g.current_user)
    return success_response(res)


@api_v1.route("/incidents/<int:incident_id>/responses", methods=["POST"])
@require_permission("responses.create")
def create_response_endpoint(incident_id: int):
    uploaded_file = None
    if request.content_type and "multipart/form-data" in request.content_type.lower():
        action_summary = str(request.form.get("action_summary") or "")
        result_notes = request.form.get("result_notes")
        client_op_id = request.form.get("client_operation_id")
        uploaded_file = request.files.get("attachment")
    else:
        body = _json_body()
        action_summary = str(body.get("action_summary") or "")
        result_notes = body.get("result_notes")
        client_op_id = body.get("client_operation_id")

    payload, deduplicated = create_response_log(
        db.session,
        incident_id=incident_id,
        actor=g.current_user,
        action_summary=action_summary,
        result_notes=result_notes,
        uploaded_file=uploaded_file,
        client_operation_id=client_op_id,
    )
    return success_response(
        {**payload, "deduplicated": deduplicated},
        status_code=200 if deduplicated else 201,
    )


@api_v1.route("/attachments/<int:response_id>", methods=["GET"])
@require_permission("incidents.view_scope")
def download_attachment_endpoint(response_id: int):
    abs_path, mime = get_attachment_file(db.session, response_id=response_id, actor=g.current_user)
    return send_file(abs_path, mimetype=mime, as_attachment=False)


@api_v1.route("/incidents/<int:incident_id>/resolution-submissions", methods=["POST"])
@require_permission("resolution.submit")
def submit_resolution_endpoint(incident_id: int):
    body = request.get_json(silent=True) or {}
    res = submit_resolution(
        db.session,
        incident_id=incident_id,
        actor=g.current_user,
        summary_notes=body.get("summary_notes") if isinstance(body, dict) else None,
    )
    return success_response(res)


@api_v1.route("/incidents/<int:incident_id>/verification/approve", methods=["POST"])
@require_permission("incidents.verify")
def approve_verification_endpoint(incident_id: int):
    body = request.get_json(silent=True) or {}
    res = approve_resolution(
        db.session,
        incident_id=incident_id,
        supervisor=g.current_user,
        reason=body.get("reason") if isinstance(body, dict) else None,
    )
    return success_response(res)


@api_v1.route("/incidents/<int:incident_id>/verification/reject", methods=["POST"])
@require_permission("incidents.verify")
def reject_verification_endpoint(incident_id: int):
    body = _json_body()
    res = reject_resolution(
        db.session,
        incident_id=incident_id,
        supervisor=g.current_user,
        reason=str(body.get("reason") or ""),
    )
    return success_response(res)


@api_v1.route("/incidents/<int:incident_id>/verification/recheck", methods=["POST"])
@require_permission("incidents.verify")
def manual_recheck_endpoint(incident_id: int):
    res = trigger_manual_recheck(
        db.session,
        incident_id=incident_id,
        supervisor=g.current_user,
    )
    return success_response(res)
