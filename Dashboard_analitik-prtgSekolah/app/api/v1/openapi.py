"""OpenAPI 3.0.3 specification and Swagger UI documentation for REST API v1 (NFR-08)."""

from __future__ import annotations

from flask import Response, jsonify

from app.api.v1 import api_v1


def build_openapi_spec() -> dict:
    """Return OpenAPI 3.0.3 contract for PRTG School Platform API v1."""
    bearer_sec = [{"BearerAuth": []}]
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "PRTG School Platform — REST API v1",
            "version": "1.0.0-m5",
            "description": (
                "Shared Backend REST API v1 untuk PRTG School Mobile (React Native Expo) "
                "dan Website Monitoring Existing. Mencakup Auth, Monitoring, Incident Workflow, "
                "Assignment, Response + Upload Foto, Supervisor Verification + PRTG Multi-Check Recheck, "
                "Offline Sync Idempotency, Push FCM, dan Administrasi."
            ),
        },
        "servers": [{"url": "/api/v1", "description": "Shared Flask Backend API v1"}],
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
            "schemas": {
                "Meta": {
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string", "example": "req-a1b2c3d4e5f6"},
                        "timestamp": {"type": "string", "format": "date-time"},
                    },
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "example": False},
                        "error": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string", "example": "FORBIDDEN"},
                                "message": {"type": "string"},
                                "details": {"type": "array", "items": {"type": "object"}},
                            },
                        },
                        "meta": {"$ref": "#/components/schemas/Meta"},
                    },
                },
                "LoginRequest": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string", "format": "password"},
                    },
                },
                "CreateUserRequest": {
                    "type": "object",
                    "required": ["username", "full_name", "password", "role_code"],
                    "properties": {
                        "username": {"type": "string"},
                        "full_name": {"type": "string"},
                        "email": {"type": "string", "nullable": True},
                        "password": {"type": "string", "format": "password"},
                        "role_code": {
                            "type": "string",
                            "enum": [
                                "admin",
                                "supervisor_utama",
                                "supervisor_wilayah",
                                "koordinator_teknisi",
                                "teknisi",
                            ],
                        },
                        "region_ids": {"type": "array", "items": {"type": "integer"}},
                        "is_active": {"type": "boolean", "default": True},
                        "is_on_duty": {"type": "boolean", "default": False},
                    },
                },
                "UpsertRecoveryPolicyRequest": {
                    "type": "object",
                    "required": ["required_sensor_ids"],
                    "properties": {
                        "required_sensor_ids": {"type": "array", "items": {"type": "integer"}},
                        "required_consecutive_normal": {"type": "integer", "default": 3, "minimum": 1},
                        "recheck_interval_seconds": {"type": "integer", "default": 60, "minimum": 10},
                        "expected_status": {"type": "string", "enum": ["online", "warning"], "default": "online"},
                    },
                },
            },
        },
        "paths": {
            "/auth/login": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Login & dapatkan JWT access + refresh token (FR-001)",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/LoginRequest"}}
                        },
                    },
                    "responses": {
                        "200": {"description": "Login berhasil"},
                        "401": {"description": "Kredensial salah"},
                        "403": {"description": "Akun nonaktif"},
                    },
                }
            },
            "/auth/refresh": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Rotasi refresh token & terbitkan access token baru",
                    "responses": {"200": {"description": "Token diperbarui"}, "401": {"description": "Refresh token invalid/dicabut"}},
                }
            },
            "/auth/logout": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Cabut refresh token server-side",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Logout berhasil"}},
                }
            },
            "/me": {
                "get": {
                    "tags": ["Auth"],
                    "summary": "Profil user, role, permissions, dan region scope (FR-002)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Profil user"}},
                }
            },
            "/dashboard": {
                "get": {
                    "tags": ["Monitoring"],
                    "summary": "Ringkasan monitoring sesuai role & region scope (FR-003, FR-024)",
                    "security": bearer_sec,
                    "parameters": [{"name": "region_id", "in": "query", "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Ringkasan dashboard"}, "403": {"description": "Di luar scope wilayah"}},
                }
            },
            "/monitoring/summary": {
                "get": {
                    "tags": ["Monitoring"],
                    "summary": "Ringkasan monitoring per wilayah",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Breakdown per wilayah"}},
                }
            },
            "/schools": {
                "get": {
                    "tags": ["Schools"],
                    "summary": "Daftar sekolah terfilter & terpaginasi (FR-004)",
                    "security": bearer_sec,
                    "parameters": [
                        {"name": "q", "in": "query", "schema": {"type": "string"}},
                        {"name": "region_id", "in": "query", "schema": {"type": "integer"}},
                        {"name": "network_status", "in": "query", "schema": {"type": "string", "enum": ["online", "offline", "warning", "unknown"]}},
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
                        {"name": "per_page", "in": "query", "schema": {"type": "integer", "default": 20}},
                    ],
                    "responses": {"200": {"description": "Daftar sekolah"}, "403": {"description": "Di luar scope wilayah"}},
                }
            },
            "/schools/{school_id}": {
                "get": {
                    "tags": ["Schools"],
                    "summary": "Detail sekolah, device, sensor, recovery policy, dan active incident (FR-005)",
                    "security": bearer_sec,
                    "parameters": [{"name": "school_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Detail sekolah"}, "403": {"description": "Di luar scope"}, "404": {"description": "Tidak ditemukan"}},
                },
                "patch": {
                    "tags": ["Admin"],
                    "summary": "Admin update sekolah / merge device PRTG (FR-023, ADR-005)",
                    "security": bearer_sec,
                    "parameters": [{"name": "school_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Sekolah diperbarui"}},
                },
            },
            "/schools/{school_id}/sensors": {
                "get": {
                    "tags": ["Schools"],
                    "summary": "Daftar sensor PRTG milik sekolah beserta status freshness & required flag",
                    "security": bearer_sec,
                    "parameters": [{"name": "school_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Daftar sensor"}},
                }
            },
            "/schools/{school_id}/recovery-policy": {
                "get": {
                    "tags": ["Schools"],
                    "summary": "Lihat konfigurasi sensor wajib pemulihan sekolah (FR-019)",
                    "security": bearer_sec,
                    "parameters": [{"name": "school_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Recovery policy"}},
                },
                "put": {
                    "tags": ["Admin"],
                    "summary": "Admin atur sensor wajib & jumlah pembacaan stabil sekolah (ADR-009)",
                    "security": bearer_sec,
                    "parameters": [{"name": "school_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/UpsertRecoveryPolicyRequest"}}
                        },
                    },
                    "responses": {"200": {"description": "Recovery policy disimpan"}, "422": {"description": "Validasi gagal"}},
                },
            },
            "/roles": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "Daftar 5 role tetap sistem",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Daftar role"}},
                }
            },
            "/users": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "Daftar pengguna (khusus Admin, FR-021)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Daftar user"}, "403": {"description": "Khusus Admin"}},
                },
                "post": {
                    "tags": ["Admin"],
                    "summary": "Buat pengguna baru + role & region scope (FR-021, FR-022, ADR-012)",
                    "security": bearer_sec,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/CreateUserRequest"}}
                        },
                    },
                    "responses": {"201": {"description": "User dibuat"}, "409": {"description": "Duplikat"}, "422": {"description": "Aturan role/region gagal"}},
                },
            },
            "/users/{user_id}": {
                "patch": {
                    "tags": ["Admin"],
                    "summary": "Ubah akun, status aktif, role, region scope, atau is_on_duty",
                    "security": bearer_sec,
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "User diperbarui"}},
                }
            },
            "/regions": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "Daftar wilayah sesuai scope user",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Daftar wilayah"}},
                },
                "post": {
                    "tags": ["Admin"],
                    "summary": "Admin tambah wilayah baru (FR-023)",
                    "security": bearer_sec,
                    "responses": {"201": {"description": "Wilayah dibuat"}},
                },
            },
            "/regions/{region_id}": {
                "patch": {
                    "tags": ["Admin"],
                    "summary": "Admin ubah wilayah",
                    "security": bearer_sec,
                    "parameters": [{"name": "region_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Wilayah diperbarui"}},
                }
            },
            "/audit-logs": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "Audit log administratif (khusus Admin, FR-026)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Daftar audit log"}},
                }
            },
            "/incidents": {
                "get": {
                    "tags": ["Incidents"],
                    "summary": "Daftar incident sesuai role & region scope (FR-007)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Daftar incident"}},
                }
            },
            "/incidents/{incident_id}": {
                "get": {
                    "tags": ["Incidents"],
                    "summary": "Detail incident lengkap (affected sensors, assignments, responses, verifications, rechecks)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Detail incident"}, "403": {"description": "Di luar scope"}},
                }
            },
            "/incidents/{incident_id}/history": {
                "get": {
                    "tags": ["Incidents"],
                    "summary": "Timeline histori lifecycle incident (FR-020)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Histori incident"}},
                }
            },
            "/schools/{school_id}/incidents": {
                "get": {
                    "tags": ["Incidents"],
                    "summary": "Seluruh histori incident suatu sekolah (termasuk incident lama & baru)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Histori incident sekolah"}},
                }
            },
            "/technicians": {
                "get": {
                    "tags": ["Assignments"],
                    "summary": "Daftar teknisi aktif beserta duty & active workload (Koordinator/Supervisor)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Daftar teknisi"}},
                }
            },
            "/incidents/{incident_id}/assign": {
                "post": {
                    "tags": ["Assignments"],
                    "summary": "Manual assign/reassign teknisi dengan alasan wajib (FR-010)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Assignment berhasil"}, "403": {"description": "Dilarang"}, "422": {"description": "Alasan kosong / teknisi tidak valid"}},
                }
            },
            "/assignments/{assignment_id}": {
                "patch": {
                    "tags": ["Assignments"],
                    "summary": "Reassign teknisi berdasarkan ID assignment (FR-010)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Reassignment berhasil"}},
                }
            },
            "/incidents/{incident_id}/acknowledge": {
                "post": {
                    "tags": ["Responses"],
                    "summary": "Teknisi assigned konfirmasi penerimaan tugas secara online (FR-012)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Acknowledged"}, "403": {"description": "Bukan teknisi assigned"}},
                }
            },
            "/incidents/{incident_id}/responses": {
                "post": {
                    "tags": ["Responses"],
                    "summary": "Catat tindakan teknisi + opsional upload foto bukti maks 5 MB (FR-013)",
                    "security": bearer_sec,
                    "responses": {"201": {"description": "Response tercatat"}, "403": {"description": "Bukan teknisi assigned"}},
                }
            },
            "/attachments/{response_id}": {
                "get": {
                    "tags": ["Responses"],
                    "summary": "Unduh foto bukti terotorisasi sesuai region scope (NFR-10)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Binary gambar"}, "403": {"description": "Di luar scope"}},
                }
            },
            "/incidents/{incident_id}/resolution-submissions": {
                "post": {
                    "tags": ["Responses"],
                    "summary": "Teknisi assigned ajukan penyelesaian ke pending_verification (FR-016)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Menunggu verifikasi"}, "422": {"description": "Belum ada response log"}},
                }
            },
            "/incidents/{incident_id}/verification/approve": {
                "post": {
                    "tags": ["Verification"],
                    "summary": "Supervisor approve laporan & picu PRTG recheck sensor wajib (FR-017..019; Admin 403)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Masuk awaiting_recovery atau resolved"}, "403": {"description": "Admin/non-supervisor ditolak"}},
                }
            },
            "/incidents/{incident_id}/verification/reject": {
                "post": {
                    "tags": ["Verification"],
                    "summary": "Supervisor reject laporan dengan alasan wajib -> kembali in_progress (FR-017, ADR-008)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Dikembalikan ke in_progress"}, "403": {"description": "Admin/non-supervisor ditolak"}},
                }
            },
            "/incidents/{incident_id}/verification/recheck": {
                "post": {
                    "tags": ["Verification"],
                    "summary": "Picu PRTG recheck live untuk incident awaiting_recovery",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Hasil recheck tersimpan"}},
                }
            },
            "/sync/operations": {
                "post": {
                    "tags": ["Offline Sync"],
                    "summary": "Sinkronisasi idempoten batch draft respons offline dengan client_operation_id (FR-015)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Hasil per-item (synced/conflict/failed)"}},
                }
            },
            "/devices/register": {
                "post": {
                    "tags": ["Notifications"],
                    "summary": "Daftarkan/perbarui FCM atau APNs push token perangkat (FR-025)",
                    "security": bearer_sec,
                    "responses": {"201": {"description": "Token terdaftar"}},
                }
            },
            "/notifications": {
                "get": {
                    "tags": ["Notifications"],
                    "summary": "In-app notification inbox fallback untuk user aktif (FR-011)",
                    "security": bearer_sec,
                    "responses": {"200": {"description": "Daftar notifikasi"}},
                }
            },
        },
    }


@api_v1.route("/openapi.json", methods=["GET"])
def openapi_json():
    return jsonify(build_openapi_spec())


@api_v1.route("/docs", methods=["GET"])
def swagger_ui():
    html = """<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PRTG School Platform — REST API v1 Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: '/api/v1/openapi.json',
      dom_id: '#swagger-ui',
      persistAuthorization: true
    });
  </script>
</body>
</html>"""
    return Response(html, mimetype="text/html")
