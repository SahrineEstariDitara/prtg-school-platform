---
title: "PRTG School Platform — Rencana Implementasi Bertahap (M0–M7)"
document_id: "PLAN-PRTG-001"
version: "1.0"
date: "2026-11-21"
status: "Ready for Execution"
references:
  - "docs/PRTG_PRD_AI_AGENT_CONTEXT.md (PRD-PRTG-001 v0.2)"
  - "docs/PRTG_SYSTEM_ARCHITECTURE_AI_CONTEXT.md (SAD-PRTG-001 v0.2)"
  - "docs/PRTG_DECISION_LOG.md (ADR-PRTG-001 v1.0)"
language: "id-ID"
---

# Rencana Implementasi Bertahap — PRTG School Platform (M0–M7)

Dokumen ini menjabarkan peta eksekusi dari **M0 hingga M7** berdasarkan seluruh keputusan yang sudah dikunci di `docs/PRTG_DECISION_LOG.md`. Prinsip utamanya adalah **incremental refactoring**: website monitoring existing (`dashboard_sekolah.py`) tetap hidup di setiap tahap, tidak ada total rewrite tanpa test baseline, dan setiap milestone memiliki *exit criteria* yang dapat diverifikasi.

---

## 1. Arsitektur Target Repositori (Selaras SAD §4.4 & §9.2)

Struktur bertahap yang **diselaraskan 1:1 dengan SAD §4.4 (Backend) dan SAD §9.2 (Mobile)** sambil tetap mempertahankan file existing di `Dashboard_analitik-prtgSekolah/` tanpa rename/move paksa (sesuai SAD §0.1):

```text
Mobile_PRTG_Koversi/
├── .gitignore                          # Abaikan .agents/skills/, .env, .venv/, uploads/, backup lama
├── docs/                               # PRD, SAD, Decision Log, Implementation Plan, M1 Audit
├── Dashboard_analitik-prtgSekolah/     # Shared Backend (Flask) + Existing Web Dashboard
│   ├── .env.example                    # Template konfigurasi (tanpa secret asli)
│   ├── requirements.txt                # Dependensi Python terkunci
│   ├── README.md                       # Panduan setup, run, & testing
│   ├── dashboard_sekolah.py            # Entry web existing (dipertahankan & kompatibel)
│   ├── tab_prtg.py                     # Tab PRTG existing (dipertahankan)
│   ├── prtg_sekolah.py                 # Bot WA & PRTG helper existing (memakai app.config)
│   ├── app/                            # Padanan Python dari `src/` pada SAD §4.4
│   │   ├── __init__.py                 # Flask App Factory + Extensions (M3+)
│   │   ├── api/v1/                     # REST API routes, controllers, serializers, OpenAPI (M4–M5)
│   │   ├── web/                        # Blueprint rute web existing (migrasi bertahap M3+)
│   │   ├── modules/                    # 10 domain modul persis SAD §4.4 (M3–M5)
│   │   │   ├── auth/                   # Login, refresh token, /me
│   │   │   ├── users/                  # CRUD user, role binding, is_on_duty
│   │   │   ├── regions/                # 6 wilayah DIY & scope user_regions
│   │   │   ├── schools/                # Master sekolah, mapping prtg_devices, recovery policy
│   │   │   ├── monitoring/             # Dashboard summary, sensor status, freshness/stale
│   │   │   ├── incidents/              # Correlation, dedup, state machine, history
│   │   │   ├── assignments/            # Auto-assignment, tie-breaker, manual/reassign + locking
│   │   │   ├── responses/              # Acknowledge, response log, upload foto, offline sync
│   │   │   ├── verification/           # Supervisor approve/reject, PRTG multi-check recheck
│   │   │   └── notifications/          # Audience routing, dedup, device tokens, logs
│   │   ├── integrations/
│   │   │   └── prtg/                   # PRTG API adapter (auth, normalisasi, timeout, recheck)
│   │   ├── infrastructure/             # PostgreSQL/SQLAlchemy, Alembic, repositories, FCM push, workers
│   │   ├── common/                     # Middleware, request_id/logging, errors, validation, RBAC, response envelope
│   │   └── config/                     # Environment configuration (.env loader & secret redaction)
│   ├── migrations/                     # Alembic DB migrations (M3+)
│   ├── uploads/                        # Penyimpanan bukti foto terproteksi (gitignored, M5+)
│   └── tests/                          # pytest: web regression, unit, integration, API, RBAC
└── apps/
    └── mobile/                         # Expo React Native + TypeScript — persis SAD §9.2 (M6)
        ├── src/
        │   ├── app/                    # Expo Router (native Stack/Tabs) + providers/bootstrap
        │   ├── navigation/             # Role-aware config (5 role), route guards, deep-link auth guard
        │   ├── features/               # 11 modul persis SAD §9.2
        │   │   ├── auth/
        │   │   ├── dashboard/
        │   │   ├── schools/
        │   │   ├── monitoring/
        │   │   ├── incidents/
        │   │   ├── assignments/
        │   │   ├── responses/
        │   │   ├── verification/
        │   │   ├── notifications/
        │   │   ├── administration/
        │   │   └── offline-sync/
        │   ├── shared/                 # 7 folder persis SAD §9.2
        │   │   ├── components/         # Design system primitives (re-export RN/UI kit sesuai vercel-react-native-skills)
        │   │   ├── hooks/
        │   │   ├── api/                # HTTP client, JWT refresh interceptor, X-Request-ID
        │   │   ├── storage/            # expo-sqlite (cache + draft queue) & expo-secure-store (token)
        │   │   ├── theme/
        │   │   ├── types/
        │   │   └── validation/
        │   └── native/                 # Expo config plugins / konfigurasi native (FCM channels, fonts)
        ├── android/                    # Di-generate Expo CNG (`npx expo prebuild`) untuk Dev Build & APK
        ├── ios/                        # Di-generate Expo CNG saat build iOS
        └── __tests__/                  # Unit & integration tests mobile
```

**Catatan Penyelarasan Mobile (`SAD §9.2` + `vercel-react-native-skills`):**
- **`navigation-native-navigators`**: `src/app/` memakai native stack & native tabs bawaan Expo Router (bukan JS navigator).
- **`imports-design-system-folder`**: `src/shared/components/` menjadi pintu tunggal re-export komponen UI & design system primitives.
- **Expo CNG (`android/` & `ios/`)**: tidak di-maintain manual sejak awal; di-generate otomatis lewat `npx expo prebuild` saat masuk tahap M6 (Development Build & APK).

---

## 2. Skema Database PostgreSQL Target (Dibangun di M3)

| No | Tabel | Kolom Utama & Constraint Penting | Kebutuhan |
|---|---|---|---|
| 1 | `roles` | `id`, `code` (UNIQUE: `admin`, `supervisor_utama`, `supervisor_wilayah`, `koordinator_teknisi`, `teknisi`), `name` | FR-002, FR-022 |
| 2 | `regions` | `id`, `code` (UNIQUE), `name` (`Bantul`, `Gunung Kidul`, `Kulon Progo`, `Sleman`, `Kota Jogja`, `Khusus`), `is_active` | FR-023 |
| 3 | `users` | `id`, `username` (UNIQUE), `email` (UNIQUE nullable), `full_name`, `password_hash`, `role_id` (FK), `is_active`, `is_on_duty`, `last_assigned_at`, `created_at`, `updated_at` | FR-001, FR-009, FR-021 |
| 4 | `user_regions` | `user_id` (FK), `region_id` (FK), PK `(user_id, region_id)` + validasi app/DB (koordinator & supervisor wilayah maks 1 baris; teknisi boleh >1) | FR-002, ADR-012 |
| 5 | `schools` | `id`, `school_code` (UNIQUE nullable), `name`, `normalized_name`, `region_id` (FK), `is_active`, `created_at`, `updated_at` | FR-004, FR-005 |
| 6 | `prtg_devices` | `id`, `prtg_device_id` (UNIQUE), `school_id` (FK), `raw_name`, `ip_address`, `prtg_group_path`, `last_seen_at` | FR-006, ADR-005 |
| 7 | `prtg_sensors` | `id`, `prtg_sensor_id` (UNIQUE), `prtg_device_id` (FK), `sensor_name`, `raw_status`, `raw_status_code`, `normalized_status` (`online`/`offline`/`warning`/`unknown`), `downtime_hours`, `last_message`, `last_checked_at` | FR-005, FR-006 |
| 8 | `school_recovery_policies` | `id`, `school_id` (FK, UNIQUE), `version`, `required_consecutive_normal` (default `3`), `recheck_interval_seconds` (default `60`), `updated_by` (FK), `updated_at` | FR-019, ADR-009 |
| 9 | `school_recovery_policy_sensors` | `policy_id` (FK), `prtg_sensor_id` (FK ke `prtg_sensors`), `expected_status` (default `online`), PK `(policy_id, prtg_sensor_id)` | FR-018, FR-019 |
| 10 | `incidents` | `id`, `school_id` (FK), `incident_status` (enum 8 state), `current_assignee_id` (FK nullable), `recovery_stable_streak` (default `0`), `detected_at`, `acknowledged_at`, `submitted_at`, `resolved_at`, `created_at`, `updated_at` + **Partial Unique Index:** `UNIQUE (school_id) WHERE incident_status != 'resolved'` | FR-007, FR-008, NFR-03 |
| 11 | `incident_sensors` | `incident_id` (FK), `prtg_sensor_id` (FK), `first_detected_at`, `last_seen_down_at`, PK `(incident_id, prtg_sensor_id)` | FR-007 |
| 12 | `incident_assignments` | `id`, `incident_id` (FK), `technician_id` (FK), `assigned_by_id` (FK nullable untuk auto), `assignment_type` (`auto`/`manual`/`reassign`), `reason`, `is_active`, `assigned_at`, `ended_at` | FR-009, FR-010, FR-020 |
| 13 | `response_logs` | `id`, `incident_id` (FK), `actor_id` (FK), `action_summary`, `result_notes`, `attachment_path`, `attachment_mime`, `attachment_size`, `client_operation_id` (UNIQUE nullable), `created_at` | FR-013, FR-015 |
| 14 | `verification_records` | `id`, `incident_id` (FK), `supervisor_id` (FK), `decision` (`approved`/`rejected`), `reason`, `policy_version`, `created_at` | FR-017, FR-020 |
| 15 | `prtg_recheck_logs` | `id`, `incident_id` (FK), `verification_record_id` (FK nullable), `check_sequence`, `all_required_normal` (bool), `is_valid_response` (bool), `streak_after_check`, `snapshot_json` (JSONB), `error_message`, `checked_at` | FR-018, FR-019, FR-020 |
| 16 | `incident_histories` | `id`, `incident_id` (FK), `event_type`, `from_status`, `to_status`, `actor_id` (FK nullable), `metadata_json` (JSONB), `request_id`, `created_at` | FR-020, NFR-04 |
| 17 | `device_tokens` & `notification_logs` | Token FCM/APNs per user+platform & log pengiriman dengan `dedup_key` | FR-011, FR-025 |
| 18 | `audit_logs` & `sync_operations` | Audit administratif (CRUD user, role, region, policy) & tracking idempotency offline sync (`client_operation_id` UNIQUE, `status`, `conflict_reason`) | FR-015, FR-026 |

---

## 3. Peta Milestone Implementasi (M0–M7)

### M0 — Source Baseline & Environment Hygiene *(Prasyarat: Python existing saja — **SELESAI**)*

**Tujuan:** Mengamankan baseline yang bisa dijalankan, direproduksi, dan bersih dari risiko kebocoran kredensial.

- [x] **M0.1** Verifikasi izin penggunaan source & simpan snapshot baseline `dashboard_sekolah.py`, `tab_prtg.py`, `prtg_sekolah.py`.
- [x] **M0.2** Lengkapi `Dashboard_analitik-prtgSekolah/requirements.txt` (Flask, requests, openpyxl, reportlab, waitress, python-dotenv, pytest) dan tambahkan panduan setup singkat di `README.md`.
- [x] **M0.3** Perbarui `.gitignore` root agar mengabaikan `.env`, `venv/`, `__pycache__/`, `*.pyc`, `bot_log.txt`, `uploads/`, dan file sementara lainnya (tetap mempertahankan `.agents/skills/`).
- [x] **M0.4** Jalankan baseline `dashboard_sekolah.py` secara lokal dan dokumentasikan perilaku existing (rute aktif, output online/offline, filter wilayah, ekspor PDF/XLSX).

**Exit Criteria M0:** Repository bersih, `requirements.txt` valid, baseline web berjalan tanpa error, dan `.gitignore` melindungi file sensitif.

---

### M1 — Backend Deep Audit & Contract Mapping *(Prasyarat: Python existing — **SELESAI**, lihat `docs/PRTG_M1_AUDIT_REPORT.md`)*

**Tujuan:** Menyusun peta teknis rinci antara fungsi existing di `dashboard_sekolah.py` dengan target service layer.

- [x] **M1.1** Inventarisasi seluruh 23 rute web di `dashboard_sekolah.py` & `tab_prtg.py` beserta input query/form dan output HTML/JSON/file.
- [x] **M1.2** Audit live PRTG API (`/api/table.json` untuk `sensors` dan `devices`): verifikasi kode status raw PRTG (`Up`, `Warning`, `Unusual`, `Down`, `Paused  (paused)`, `Unknown`), jumlah total sensor (11.704), dan waktu respons (~1 detik, baseline NFR-11).
- [x] **M1.3** Pemetaan fungsi pure logic yang aman diekstrak ke shared service: `is_sekolah`, `nama_bersih`, `nama_tampil`, `normalisasi_nama_sekolah`, `tentukan_wilayah_info`, `build_down_device_details`, `parse_duration_to_hours`, `get_sensor_down_hours`.
- [x] **M1.4** Daftar kasus duplikasi device PRTG per sekolah dari data nyata untuk strategi seeding `schools ↔ prtg_devices`.

**Exit Criteria M1:** Inventory rute, mapping kode status PRTG aktual, dan angka baseline sensor/sekolah terdokumentasi di `docs/PRTG_M1_AUDIT_REPORT.md`.

---

### M2 — Stabilization, Secret Extraction & Baseline Regression Tests *(Prasyarat: Python existing — **SELESAI**)*

**Tujuan:** Menutup risiko keamanan & membangun jaring pengaman (*regression suite*) **sebelum** memindahkan logic atau mengubah arsitektur.

- [x] **M2.1** Pindahkan semua kredensial hardcoded (`PRTG_SERVER`, `PRTG_USERNAME`, `PRTG_PASSHASH`, `PRTG_PASSWORD`, `VERIFY_SSL`, `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, `FLASK_SECRET_KEY`, `GREENAPI_*`, `WA_TARGET`) ke `.env` via `python-dotenv` (`app/config/settings.py`), sediakan `.env.example`.
- [x] **M2.2** Tambahkan structured logging & `X-Request-ID` (correlation ID) di `app/common/logging.py` tanpa mencetak secret/token (`NFR-09`).
- [x] **M2.3** Buat folder `tests/` dengan `pytest` + mock response PRTG untuk menguji perilaku existing:
  - Login/logout web existing
  - Perhitungan sekolah online/offline, kategori `< 24 jam`, `24 - 48 jam`, `> 48 jam`
  - Deteksi 6 wilayah DIY + override `pengaturan_sekolah.json`
  - Rute `/offline`, `/online`, `/sekolah`, `/rekap/offline`, `/rekap/online`
  - Ekspor PDF/XLSX tidak error & simulasi PRTG timeout → `502`
- [x] **M2.4** Verifikasi semua baseline regression test hijau (`16 passed` dalam `0.88s`).

**Exit Criteria M2:** Nol kredensial hardcoded di source code; baseline regression suite lulus 100%; perilaku website existing tidak berubah.

---

### M3 — PostgreSQL Setup & Shared Business Services *(Prasyarat: PostgreSQL 17 — **SELESAI**)*

**Tujuan:** Membangun fondasi database relasional dan memisahkan business logic monitoring/PRTG dari tampilan HTML Flask.

- [x] **M3.1** Setup PostgreSQL 17 lokal (`prtg_school_db` & `prtg_school_test` pada port `5433`) + tambahkan `SQLAlchemy`, `Flask-SQLAlchemy`, `Flask-Migrate` (Alembic), `psycopg[binary]` ke `requirements.txt`.
- [x] **M3.2** Bangun Flask App Factory (`app/__init__.py`) yang tetap kompatibel dengan seluruh 23 rute web existing di `dashboard_sekolah.py` & `tab_prtg.py`.
- [x] **M3.3** Implementasikan seluruh 20 tabel operational SQLAlchemy (terbagi di `app/modules/<domain>/models.py` & `app/infrastructure/database/`) + migration Alembic awal (`upgrade → downgrade → upgrade` terverifikasi) + partial unique index `uq_incidents_one_active_per_school` untuk 1 incident aktif per sekolah.
- [x] **M3.4** Buat script seed idempoten (`app/infrastructure/database/seed.py`): 5 role tetap (`roles`), 6 wilayah DIY + 1 fallback (`regions`), dan 1 akun `admin` awal (password dari `.env`, ter-hash).
- [x] **M3.5** Ekstrak `PRTGAdapter` (`app/integrations/prtg/`) dengan:
  - Server-side auth, timeout terkontrol, retry terbatas
  - Normalisasi status (`online`, `offline`, `warning`, `unknown`) termasuk handling spasi ganda `"Paused  (paused)"`
  - Pencatatan `last_checked_at` & indikator `is_stale` (gagal PRTG ≠ `online`)
  - Fungsi `recheck_sensors(sensor_ids)` untuk verifikasi live
- [x] **M3.6** Implementasikan `SchoolSyncService` (`app/modules/schools/sync_service.py` — teruji live sync: **160 sekolah, 163 device, 4.322 sensor** ke PostgreSQL) dan `MonitoringService` (`app/modules/monitoring/service.py`) bersama yang siap dipakai web existing & API v1.
- [x] **M3.7** Jalankan unit & integration tests PostgreSQL + regression tests website existing (**26/26 PASSED** dalam `2.36s`).

**Exit Criteria M3:** Migration PostgreSQL sukses & reversible, PRTGAdapter + MonitoringService teruji (termasuk kasus PRTG timeout → `unknown`/`stale` dan partial unique index incident), website existing tetap konsisten (`TC-04`, `TC-14`).

---

### M4 — REST API v1, JWT Authentication & Server-Side RBAC *(Prasyarat: M3 selesai — **SELESAI**)*

**Tujuan:** Menyediakan kontrak REST/JSON berversi (`/api/v1`) yang aman, tervalidasi, dan terdokumentasi OpenAPI untuk mobile & web.

- [x] **M4.1** Setup envelope respons standar (`success`, `data`, `meta.request_id`, `meta.timestamp`) dan error handler HTTP semantik (`400`, `401`, `403`, `404`, `409`, `422`, `429`, `500` — tidak pernah HTTP 200 untuk error) di `app/common/responses.py` & `app/common/errors.py`.
- [x] **M4.2** Implementasikan Auth endpoints (`FR-001`, `FR-002`) + tabel `refresh_tokens` (migrasi Alembic `0002`):
  - `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh` (rotation), `POST /api/v1/auth/logout` (server-side revocation), `GET /api/v1/me` (role, permission, scope wilayah).
- [x] **M4.3** Implementasikan middleware RBAC + Region Scope server-side (`app/common/rbac.py`, `NFR-02`): cek akun aktif → role/permission → region scope → tolak lintas wilayah `403` & Admin tanpa `incidents.verify`.
- [x] **M4.4** Implementasikan endpoint Monitoring & Sekolah (`app/api/v1/monitoring.py`, `FR-003`–`FR-005`, `FR-024`) + penyelarasan **Opsi A** untuk PRTG `Unusual` (`145 online / 15 offline` dari 160 sekolah live):
  - `GET /api/v1/dashboard`, `GET /api/v1/monitoring/summary`, `GET /api/v1/schools`, `GET /api/v1/schools/{id}`, `GET /api/v1/schools/{id}/sensors`.
- [x] **M4.5** Implementasikan endpoint Administrasi Admin (`app/api/v1/admin.py`, `FR-021`–`FR-023`, `FR-026`):
  - `GET/POST/PATCH /api/v1/users` (enforce aturan `ADR-012`: 1 region untuk Koordinator & Supervisor Wilayah, multi-region untuk Teknisi)
  - `GET /api/v1/roles`, `GET/POST/PATCH /api/v1/regions`
  - `PATCH /api/v1/schools/{id}` (merge/re-assign `prtg_devices`, ubah wilayah)
  - `GET/PUT /api/v1/schools/{id}/recovery-policy` (sensor wajib & `required_consecutive_normal`)
  - `GET /api/v1/audit-logs` (semua mutasi admin tercatat otomatis)
- [x] **M4.6** Generate dokumentasi **OpenAPI 3.0.3 & Swagger UI** (`/api/v1/openapi.json`, `/api/v1/docs`) dan jalankan API contract tests + **negative authorization tests** (`33/33 PASSED`, mencakup `TC-01`, `TC-02`, `TC-03`).

**Exit Criteria M4:** OpenAPI aktif, semua endpoint auth/monitoring/admin lolos contract & negative RBAC test (termasuk Admin tidak memiliki `incidents.verify` dan user lintas wilayah mendapat `403`).

---

### M5 — Operational Workflow, Recovery Worker, Offline Sync & Push *(Prasyarat: M4 selesai — **SELESAI**)*

**Tujuan:** Mengimplementasikan seluruh logika inti operasional incident dari deteksi hingga penutupan terverifikasi.

- [x] **M5.1** **Incident Correlation & Deduplication (`app/modules/incidents/service.py`, `FR-007`, `FR-008`, `TC-05`):**
  - `correlate_down_schools()` membentuk tepat 1 incident aktif per sekolah (menautkan sensor baru ke incident aktif tanpa duplikasi).
  - Gangguan baru setelah incident sebelumnya `resolved` selalu membuat incident baru dan tampil bersama di `GET /api/v1/schools/{id}/incidents`.
- [x] **M5.2** **Auto & Manual Assignment (`app/modules/assignments/service.py`, `FR-009`, `FR-010`, `AC-06`, `AC-07`, `TC-06`, `TC-07`):**
  - Auto-assignment: region → aktif & `is_on_duty` → active workload terendah → tie-breaker `last_assigned_at ASC NULLS FIRST` → fallback `unassigned` + push ke Koordinator/Supervisor.
  - Proteksi race condition dengan transaksi PostgreSQL + `SELECT FOR UPDATE`.
  - Endpoint `POST /api/v1/incidents/{id}/assign`, `PATCH /api/v1/assignments/{id}`, & `GET /api/v1/technicians` (wajib alasan, pertahankan histori assignment lama).
- [x] **M5.3** **Technician Response & Attachments (`app/modules/responses/service.py`, `FR-012`, `FR-013`, `FR-016`, `AC-08`, `TC-08`):**
  - `POST /api/v1/incidents/{id}/acknowledge` (online, khusus assignee)
  - `POST /api/v1/incidents/{id}/responses` + upload foto bukti ke `uploads/` (validasi magic header JPG/PNG/WebP, maks 5 MB, unduh terotorisasi `GET /api/v1/attachments/{id}`)
  - `POST /api/v1/incidents/{id}/resolution-submissions` (wajib ≥ 1 response log → `pending_verification`)
- [x] **M5.4** **Supervisor Verification & Multi-Check PRTG Recheck (`app/modules/verification/service.py`, `FR-017`–`FR-020`, `AC-03`, `AC-09`–`AC-11`, `TC-03`, `TC-09`–`TC-11`):**
  - `POST /api/v1/incidents/{id}/verification/reject` (alasan wajib → kembali `in_progress`, catat `VerificationRecord`).
  - `POST /api/v1/incidents/{id}/verification/approve` (catat `VerificationRecord`, recheck ke-1 live ke PRTG → `awaiting_recovery`).
  - **Background Recovery Recheck Worker (`app/infrastructure/workers/operational_worker.py`):** recheck `awaiting_recovery` hingga mencapai `N` kali berturut-turut normal → `resolved`; jika policy kosong/PRTG timeout/offline → blokir closure & reset streak ke `0`.
  - Admin, Koordinator, dan Teknisi **ditolak `403`** pada seluruh endpoint verifikasi.
- [x] **M5.5** **Offline Sync Idempotency (`POST /api/v1/sync/operations`, `FR-014`, `FR-015`, `AC-13`, `TC-12`):**
  - Idempotency `client_operation_id` (UUID) mencegah duplikasi `ResponseLog`; operasi non-draft (mis. offline approve) ditolak; incident closed/reassigned mengembalikan `conflict` tanpa menimpa server.
- [x] **M5.6** **Push Notification FCM (`app/infrastructure/push/fcm_adapter.py` & `app/modules/notifications/service.py`, `FR-011`, `FR-025`, `TC-13`):**
  - `POST /api/v1/devices/register` & `GET /api/v1/notifications`, audience routing per region/assignment, deduplication `dedup_key`, mode `dry_run` + siap FCM live, log ke `notification_logs`.
- [x] **M5.7** **Web Consistency Integration & OpenAPI Update (`FR-027`, `AC-12`, `TC-14`):**
  - Helper `_ringkasan_incident_aktif_db()` + `start_operational_worker(app)` di `dashboard_sekolah.py`; seluruh 17 endpoint baru terdokumentasi di OpenAPI `/api/v1/docs`; seluruh **38/38 automated tests** lulus.

**Exit Criteria M5:** Seluruh workflow E2E backend lulus automated tests (38/38 PASSED termasuk concurrency locking, idempotency sync, PRTG timeout, missing recovery policy, 3x consecutive stable recheck, dan Admin non-verifier).

---

### M6 — Mobile App MVP (Expo React Native + TypeScript) *(Prasyarat: Install Node.js LTS + Android Studio & Emulator)*

**Tujuan:** Membangun aplikasi mobile cross-platform (Android/iOS codebase bersama, diuji di Android Emulator → HP fisik) mengikuti best practices `vercel-react-native-skills`.

- [ ] **M6.1** **Inisialisasi & Arsitektur Proyek (SAD §9.2):**
  - Setup `apps/mobile` dengan Expo + TypeScript + Expo Router (`src/app/`) + Development Build (`expo-dev-client`).
  - Susun direktori persis SAD §9.2: `src/app/`, `src/navigation/`, 11 modul `src/features/`, 7 modul `src/shared/` (termasuk design system primitives di `src/shared/components/`), dan `src/native/`.
  - Pasang paket inti: `expo-secure-store`, `expo-sqlite`, `expo-image`, `@shopify/flash-list`, `react-native-reanimated`, `react-native-gesture-handler`, `expo-notifications`, `expo-image-picker`, `@react-native-community/netinfo`.
  - Konfigurasi API client di `src/shared/api/` berbasis env (`EXPO_PUBLIC_API_URL`), auto refresh token, dan interceptor `X-Request-ID`.
- [ ] **M6.2** **Auth & Role-Aware Navigation (`FR-001`, `FR-002`, `AC-01`, `TC-01`):**
  - Layar Login + simpan token di `expo-secure-store`.
  - Navigasi tab/stack dinamis sesuai 5 role (Admin, Supervisor Utama, Supervisor Wilayah, Koordinator Teknisi, Teknisi).
- [ ] **M6.3** **Layar Monitoring & Sekolah (`FR-003`–`FR-005`, `AC-04`):**
  - Dashboard ringkasan sesuai scope (dahulukan gangguan aktif / tugas tertunda).
  - Daftar sekolah pakai `FlashList` (memoized item, pencarian, filter wilayah & status).
  - Detail sekolah & daftar sensor: tampilkan badge teks + ikon untuk `network_status` terpisah dari `incident_status`, waktu `last_checked_at`, dan banner `stale`/`unknown`.
- [ ] **M6.4** **Layar Incident, Assignment & Teknisi (`FR-007`–`FR-016`):**
  - Daftar & detail incident + timeline `IncidentHistory` & `VerificationRecord`.
  - Layar Assignment/Reassignment (Koordinator & Supervisor) dengan input alasan dan info beban aktif teknisi.
  - Alur Teknisi: tombol Acknowledge (online), form Response Log ringkas + lampiran foto (`expo-image`), tombol Resolution Submission (online).
- [ ] **M6.5** **Layar Verifikasi Supervisor & Administrasi Admin (`FR-017`–`FR-024`):**
  - Review Supervisor: lihat laporan + foto bukti + status sensor wajib live → tombol Approve / Reject (modal alasan wajib) + indikator progres `awaiting_recovery (1/3, 2/3, 3/3)`.
  - Administrasi Admin: kelola user (CRUD, aktif/nonaktif, assign 5 role, atur region), konfigurasi recovery policy sensor wajib, lihat audit log — **tanpa** tombol verifikasi incident.
- [ ] **M6.6** **Offline Cache, Draft Queue & Safe Sync (`FR-014`, `FR-015`, `AC-13`, `TC-12`):**
  - Skema lokal `expo-sqlite` untuk cache dashboard/incident + tabel `offline_drafts` (`client_operation_id`, `sync_status`: `draft`/`pending_sync`/`synced`/`conflict`/`failed`).
  - Deteksi online kembali via `NetInfo` + AppState foreground resume → sync queue otomatis dengan tampilan status & rekonsiliasi konflik (draft tidak hilang).
- [ ] **M6.7** **Push Notification FCM & App Lifecycle (`FR-011`, `FR-025`, `TC-13`):**
  - Registrasi FCM token saat login; penanganan notifikasi saat foreground, background, dan terminated (deep link ke detail incident setelah cek auth/scope ulang).

**Exit Criteria M6:** Aplikasi berjalan mulus di Android Emulator & HP Android fisik untuk kelima role, offline draft sync terverifikasi, push FCM berfungsi.

---

### M7 — UAT, Regression Testing, Build & Bukti Akademik CPMK *(Prasyarat: M5 & M6 selesai)*

**Tujuan:** Memvalidasi seluruh acceptance criteria PRD, menyiapkan kandidat rilis, dan melengkapi bukti akademik CPMK TIO5023.

- [ ] **M7.1** **Eksekusi Penuh `TC-01` s.d. `TC-14` & Checklist `AC-01` s.d. `AC-14`:**
  - Dokumentasikan hasil uji aktual (screenshot/log tersanitasi) untuk seluruh 14 test case.
- [ ] **M7.2** **Regression Test Website Existing (`TC-14`, `AC-12`):**
  - Verifikasi dashboard web lama (angka online/offline, durasi, filter 6 wilayah, pencarian, tab PRTG, ekspor PDF/XLSX, kirim WA) tetap normal dan konsisten dengan mobile.
- [ ] **M7.3** **Build & Distribusi:**
  - Build **APK Android** internal/demonstrasi (via EAS Build / local Gradle).
  - Siapkan panduan deployment backend Flask + PostgreSQL di balik HTTPS reverse proxy (`README.md` + `.env.example`).
- [ ] **M7.4** **Kelengkapan Bukti Akademik CPMK TIO5023:**
  - `CPMK-TIO5023-1`: Diagram konteks/runtime + penjelasan Android lifecycle, foreground/background, FCM, `expo-secure-store`, rujukan kode.
  - `CPMK-TIO5023-2`: Diagram arsitektur backend/mobile, struktur repo, `PRTGAdapter`, OpenAPI contract, separation of concerns.
  - `CPMK-TIO5023-3`: PRD, user flow 5 role, wireframe/UI final, hasil usability testing & revisi.
  - `CPMK-TIO5023-4`: Source code, APK Android, laporan eksekusi `TC-01`–`TC-14`, dan video/demo E2E dari deteksi PRTG sampai supervisor verification + PRTG recheck.

**Exit Criteria M7:** Semua 14 AC terpenuhi, 14 TC lulus, regression web hijau, APK Android siap demo, dan seluruh bukti CPMK terdokumentasi.

---

## 4. Ringkasan Peta Kebutuhan ke Milestone

| Milestone | Requirement (`FR`) yang Dipenuhi | Acceptance & Test Utama |
|---|---|---|
| **M0–M2** | Baseline & keamanan (`NFR-01`, `NFR-08`, `NFR-09`) | Regression baseline web |
| **M3** | `FR-006`, `FR-008`, `FR-027` (fondasi DB & adapter) | `AC-04`, `TC-04` |
| **M4** | `FR-001`–`FR-005`, `FR-021`–`FR-024`, `FR-026` | `AC-01`–`AC-04`, `TC-01`–`TC-04` |
| **M5** | `FR-007`, `FR-009`–`FR-020`, `FR-025`, `FR-027` | `AC-05`–`AC-14`, `TC-05`–`TC-14` |
| **M6** | Seluruh layar & offline client (`FR-001`–`FR-025`, `NFR-06`, `NFR-07`) | Uji perangkat Android (Emulator + Fisik) |
| **M7** | Validasi penuh `FR-001`–`FR-027` & `NFR-01`–`NFR-12` | `AC-01`–`AC-14`, `TC-01`–`TC-14`, CPMK 1–4 |
