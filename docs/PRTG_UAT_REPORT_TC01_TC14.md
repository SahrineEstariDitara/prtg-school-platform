# Laporan Pengujian & UAT Formal — PRTG School Platform (`TC-01` s.d. `TC-14` & `AC-01` s.d. `AC-14`)

**Versi:** MVP 1.0 (Milestone M7)  
**Tanggal Verifikasi:** 2026-11-23  
**Cakupan:** Shared Backend (`Dashboard_analitik-prtgSekolah/`), Website Monitoring Existing (23 rute), dan Aplikasi Mobile Expo React Native + TypeScript (`apps/mobile/`).

> **Catatan Keamanan:** Seluruh hasil log dan bukti eksekusi di dokumen ini telah disanitasi — tidak memuat kredensial PRTG, password akun, token JWT, maupun rahasia `.env`.

---

## 1. Ringkasan Hasil Automated Verification

| Suite Pengujian | Perintah Eksekusi | Hasil |
|---|---|---|
| **Backend + Web Regression + API + M5 Workflow + M7 UAT (`TC-01`–`TC-14`)** | `python -m pytest Dashboard_analitik-prtgSekolah/tests -v` | **40/40 PASSED** |
| **Mobile TypeScript Strict Check** | `cd apps/mobile && npm run typecheck` (`tsc --noEmit`) | **0 error** |
| **Mobile Jest + RNTL Suite** | `cd apps/mobile && npm test -- --watchAll=false` | **5/5 suites, 16/16 PASSED** |
| **Expo Android Hermes Bundle** | `cd apps/mobile && npx expo export --platform android` | **SUKSES** (1.933 modul → `entry.hbc` `4.8MB`) |
| **Expo Android Native Prebuild** | `cd apps/mobile && npx expo prebuild --platform android --no-install --clean` | **SUKSES** (`applicationId: id.jogjaprov.prtgschool`, Hermes & New Arch aktif) |
| **Idempotent UAT Demo Seeder** | `python Dashboard_analitik-prtgSekolah/app/infrastructure/database/seed_uat.py` | **SUKSES** (5 akun demo lintas role & wilayah dibuat idempoten) |

---

## 2. Matriks Acceptance Criteria Resmi (`AC-01` s.d. `AC-14`)

| ID | Kriteria Penerimaan PRD (§10.1) | Status | Bukti Kode & Automated Test |
|---|---|---|---|
| **AC-01** | Setelah login hanya menu & data sesuai role/region scope yang terlihat | **LULUS** | `app/common/rbac.py`, `apps/mobile/src/navigation/roleConfig.ts`, `test_api_auth_and_rbac.py`, `roleConfig.test.ts`, `test_m7_uat_e2e_tc01_tc14.py` |
| **AC-02** | Admin kelola akun dan 5 role tetap, serta melihat monitoring global | **LULUS** | `app/api/v1/admin.py`, `apps/mobile/src/features/administration/UsersAdminScreen.tsx`, `test_api_monitoring_and_admin.py` |
| **AC-03** | Admin **tidak bisa** approve/reject resolution submission | **LULUS** | Backend menolak `403` (`incidents.verify` absen dari role `admin`); Mobile `canUserVerifyIncident()` selalu `false` untuk `admin` (`roleConfig.test.ts`, `test_m7_uat_e2e_tc01_tc14.py`) |
| **AC-04** | Dashboard status dari PRTG via backend; `stale`/`unknown` ditampilkan | **LULUS** | `PRTGAdapter`, `MonitoringService`, `FreshnessBanner` (`StatusBadge.test.tsx`, `test_prtg_adapter.py`) |
| **AC-05** | Gangguan multi-sensor 1 sekolah dikorelasikan ke 1 incident utama (dedup) | **LULUS** | Partial unique index `uq_incidents_one_active_per_school` + `correlate_down_schools()` (`test_m5_incident_and_assignment.py`, `test_m7_uat_e2e_tc01_tc14.py`) |
| **AC-06** | Auto-assignment hanya teknisi sesuai wilayah, aktif/duty, dan beban terendah | **LULUS** | `app/modules/assignments/service.py` (`SELECT FOR UPDATE` + tie-breaker `last_assigned_at ASC NULLS FIRST` + fallback `unassigned`) |
| **AC-07** | Assign/reassign oleh Koordinator/Supervisor berwenang & alasan tersimpan | **LULUS** | `POST /api/v1/incidents/{id}/assign`, `AssignTechnicianScreen.tsx`, histori assignment lama dipertahankan |
| **AC-08** | Teknisi bisa acknowledge, catat tindakan (+ foto bukti), & ajukan penyelesaian | **LULUS** | `app/modules/responses/service.py`, `ResponseFormScreen.tsx`, `IncidentDetailScreen.tsx`, magic-header photo validation ≤ 5 MB |
| **AC-09** | Verification hanya Supervisor dengan scope yang sesuai | **LULUS** | `require_permission("incidents.verify")` + `ensure_region_in_scope()` + `VerificationPanel.tsx` |
| **AC-10** | Approval laporan memicu backend PRTG recheck sensor wajib | **LULUS** | `approve_resolution()` masuk `awaiting_recovery` & langsung memicu `PRTGAdapter.recheck_sensors()` |
| **AC-11** | Incident tidak `resolved` bila sensor wajib belum normal, unknown, policy kosong, atau PRTG gagal | **LULUS** | Guard 1 (policy kosong) & Guard 2 (timeout/offline/unknown → reset streak ke `0`, tetap `awaiting_recovery`) |
| **AC-12** | Website dan mobile menampilkan monitoring/incident konsisten lewat shared backend | **LULUS** | `dashboard_sekolah.py` + `MonitoringService` bersama; teruji di `test_web_routes_regression.py` & `test_m7_uat_e2e_tc01_tc14.py` |
| **AC-13** | Draft offline tersinkron tanpa hilang/duplikasi pada skenario uji | **LULUS** | `expo-sqlite` `offline_drafts` + `client_operation_id` UUID + `POST /api/v1/sync/operations` + retensi `conflict` (`syncEngine.test.ts`, `test_m5_offline_sync_and_notifications.py`) |
| **AC-14** | Perubahan role, assignment, verifikasi, konfigurasi tertelusur via audit/history | **LULUS** | `AuditLog` (`GET /api/v1/audit-logs`), `IncidentHistory`, `VerificationRecord`, `PrtgRecheckLog` |

---

## 3. Laporan Eksekusi 14 Test Cases Minimum (`TC-01` s.d. `TC-14`)

| ID | Skenario Uji | Hasil Aktual Tersanitasi | Status |
|---|---|---|---|
| **TC-01** | Login setiap 5 role (`admin`, `supervisor_utama`, `supervisor_wilayah`, `koordinator_teknisi`, `teknisi`) | Semua 5 akun memperoleh HTTP `200`, token JWT valid, `/api/v1/me` mengembalikan `role`, `permissions`, dan `scope_type` tepat; `roleConfig.test.ts` memastikan tab mobile dinamis per role. | **PASS** |
| **TC-02** | Akses lintas wilayah | `spv_bantul` mengakses sekolah Bantul → `200`; mengakses sekolah Sleman → `403 FORBIDDEN`. | **PASS** |
| **TC-03** | Batas role Admin | `admin` akses `/api/v1/dashboard` & `/api/v1/users` → `200`; akses `/verification/approve` & `/verification/reject` → `403 FORBIDDEN`; UI mobile menyembunyikan `VerificationPanel`. | **PASS** |
| **TC-04** | Ingest status PRTG & simulasi API gagal | Status PRTG dinormalisasi (`online`/`offline`/`warning`/`unknown`), `Unusual` diselaraskan (Opsi A); simulasi timeout menghasilkan `is_valid=False` / `is_stale=True` tanpa pernah menjadi `online` palsu. | **PASS** |
| **TC-05** | Korelasi incident multi-sensor | 2 sensor offline pada 1 sekolah hanya menghasilkan 1 incident aktif; eksekusi korelasi ulang menghasilkan `incidents_created=0` (deduplicated); gangguan setelah `resolved` membuat incident baru. | **PASS** |
| **TC-06** | Auto-assignment eligibility & fallback | Wilayah Bantul memilih teknisi aktif + `on_duty` dengan workload terendah & `last_assigned_at` tertua; wilayah Gunung Kidul tanpa teknisi `on_duty` otomatis masuk status `unassigned`. | **PASS** |
| **TC-07** | Manual assign/reassign | Alasan kosong ditolak `422`; alasan valid berhasil `200`, mengakhiri assignment lama (`is_active=False`) tanpa menghapus histori (`len(assignments) == 2`). | **PASS** |
| **TC-08** | Response submission & lampiran foto | Acknowledge → `200`; upload multipart foto PNG valid → `201`; download terotorisasi `/api/v1/attachments/{id}` → `200`; resolution submission → `200` (`pending_verification`). | **PASS** |
| **TC-09** | Supervisor rejection | Reject tanpa alasan → `422`; reject dengan alasan valid → `200`, status kembali `in_progress` dan `VerificationRecord` tersimpan. | **PASS** |
| **TC-10** | Recovery validation 3x normal berturut-turut | Recheck #1 → `awaiting_recovery (streak=1)`, #2 → `awaiting_recovery (streak=2)`, #3 → `resolved (streak=3)`. | **PASS** |
| **TC-11** | PRTG unavailable & missing recovery policy | Approve saat policy kosong tetap `awaiting_recovery`; recheck saat PRTG timeout tetap `awaiting_recovery` dengan `streak=0`. | **PASS** |
| **TC-12** | Offline draft sync & conflict detection | Pengiriman ulang `client_operation_id` sama mengembalikan `deduplicated=True` dengan `result.id` identik; draft terlambat ke incident `resolved` mengembalikan `sync_status="conflict"` dan tetap dipertahankan di SQLite mobile. *(Catatan: UAT M7 ini sekaligus menemukan dan memperbaiki ketidakselarasan field `sync_status`/`conflict_reason`/`result.id` pada `syncEngine.ts` mobile!)* | **PASS** |
| **TC-13** | Push device registration & inbox routing | `POST /api/v1/devices/register` → `201`; `GET /api/v1/notifications` mencatat notifikasi terdeduplikasi sesuai audiens wilayah/assignee; mobile deep-link guard memverifikasi auth sebelum navigasi. | **PASS** |
| **TC-14** | Konsistensi Web Existing & Mobile API | Seluruh 16 baseline regression test website existing (login, `/offline`, `/online`, `/sekolah`, `/rekap/*`, PDF/XLSX export) lulus `100%`; `MonitoringService` menghasilkan angka identik antara Web dan `/api/v1/dashboard`. | **PASS** |

---

## 4. Checklist Pengujian Perangkat Nyata (Android Emulator & HP Fisik)

Gunakan 6 akun demo hasil `seed_uat.py` (password default dev: `UatDemo!2026`, dapat diubah via `UAT_DEMO_PASSWORD`) untuk merekam bukti visual/video demonstrasi:

| No | Skenario Perangkat Android | Target Perangkat | Status Manual |
|---|---|---|---|
| 1 | Login 5 role & verifikasi perbedaan tab navigasi (`TC-01`) | Android Emulator & HP Fisik | `[ ]` Siap diuji di AVD/HP |
| 2 | Mode Airplane → simpan draft tindakan → matikan Airplane → auto-sync (`TC-12`) | Android Emulator & HP Fisik | `[ ]` Siap diuji di AVD/HP |
| 3 | Kamera/Galeri lampiran foto bukti & zoom lightbox (`TC-08`) | HP Android Fisik | `[ ]` Siap diuji di HP |
| 4 | Supervisor Reject + Approve + progres `awaiting_recovery (1/3..3/3)` (`TC-09`–`TC-11`) | Android Emulator & HP Fisik | `[ ]` Siap diuji di AVD/HP |
| 5 | Notifikasi foreground/background/cold-start deep link (`TC-13`) | HP Android Fisik (dengan `google-services.json`) | `[ ]` Siap diuji di HP |
