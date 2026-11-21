---
title: "PRTG School Platform — Audit As-Is & Architecture Decision Log (ADR)"
document_id: "ADR-PRTG-001"
version: "1.0"
date: "2026-11-21"
status: "Approved — Locked for MVP 1.0 Implementation"
based_on:
  - "docs/PRTG_PRD_AI_AGENT_CONTEXT.md (PRD-PRTG-001 v0.2)"
  - "docs/PRTG_SYSTEM_ARCHITECTURE_AI_CONTEXT.md (SAD-PRTG-001 v0.2)"
  - "Repository Audit: Dashboard_analitik-prtgSekolah/"
language: "id-ID"
---

# PRTG School Platform — Audit As-Is & Decision Log (ADR)

Dokumen ini mengunci hasil **audit codebase existing** (`Dashboard_analitik-prtgSekolah/`) serta seluruh **keputusan produk, arsitektur, dan teknis** yang sebelumnya berstatus `OPEN`/`TERBUKA` pada PRD v0.2 dan SAD v0.2. Dokumen ini menjadi acuan resmi implementasi **Focused MVP 1.0** (`M0`–`M7`).

---

## 1. Hasil Audit Codebase Existing (*As-Is*)

Audit dilakukan pada direktori `Dashboard_analitik-prtgSekolah/` (verified `2026-11-21`):

| Komponen | Fakta *As-Is* yang Ditemukan | Implikasi Migrasi |
|---|---|---|
| **Baseline File Aktif** | `dashboard_sekolah.py` (~7.892 baris), `tab_prtg.py` (~3.104 baris), `prtg_sekolah.py` (~1.260 baris) | `dashboard_sekolah.py` ditetapkan sebagai **baseline resmi** (bukan `dashboard_sekolah_revisi.py`). |
| **Framework & Runtime** | Python + **Flask 3.1.3** + **Waitress** (port `5001`), `requests 2.34.2`, `openpyxl 3.1.5`, `reportlab 5.0.1` | Pertahankan Python Flask secara inkremental (Blueprint modular). Isi `requirements.txt` yang sebelumnya `0 byte`. |
| **Rendering Web** | Monolitik inline HTML/CSS/JS via `render_template_string` | Pertahankan rute web existing (`/`, `/offline`, `/online`, `/sekolah`, `/sekolah/tab/<tab>`, `/sekolah/sensor/<sensor_id>`, `/rekap/<mode>`, serta seluruh rute unduh PDF/XLSX) dengan regression test. |
| **Penyimpanan Data** | **Tidak ada RDBMS** — memakai file JSON lokal (`gangguan_sekolah.json`, `riwayat_sekolah.json`, `riwayat_online_v2.json`, `rekap_online_harian.json`, `pengaturan_sekolah.json`, `sensor_terpilih.json`, `notifikasi_prtg_state.json`) | Perkenalkan **PostgreSQL** sebagai operational database bersama untuk seluruh modul baru (RBAC, Incident, Assignment, Response, Verification, Audit, Sync, Push). |
| **Autentikasi & Otorisasi** | 1 akun hardcoded berbasis Flask session cookie; belum ada RBAC, region scope, atau API token | Bangun sistem user, 5 role tetap, region scope, dan JWT auth untuk `/api/v1/*`; migrasikan login web secara kompatibel. |
| **Keamanan & Secret** | Kredensial PRTG, password login dashboard, `app.secret_key`, dan token GreenAPI WA masih **hardcoded** di `prtg_sekolah.py` dan `dashboard_sekolah.py`; PRTG diakses via HTTP (`VERIFY_SSL=False`) | **Wajib M2:** Pindahkan seluruh kredensial dan konfigurasi ke `.env` (`os.environ`) + sediakan `.env.example` tanpa secret; pastikan `.env` masuk `.gitignore`. |
| **Wilayah (Regions)** | 6 wilayah DIY sudah baku di kode: `Bantul`, `Gunung Kidul`, `Kulon Progo`, `Sleman`, `Kota Jogja`, `Khusus` (+ fallback `Belum Dikelompokkan`) | Jadikan 6 wilayah tersebut sebagai seed awal tabel `regions`. |
| **Identitas Sekolah & PRTG** | Kode existing mengelompokkan per *device* (`#parentid` atau kode `SIM/LM`). Di `gangguan_sekolah.json` ditemukan 1 sekolah memiliki >1 device PRTG (mis. `#4423` & `#9971` SMKN 1 Gedangsari; `#16278` & `#25256` SMAN 1 Turi) | Model data baru memakai relasi `schools (1) -> prtg_devices (N) -> prtg_sensors (N)` agar memenuhi `FR-007` (1 incident aktif per sekolah). |
| **Pencatatan Gangguan Lama** | `catat_gangguan()` otomatis menutup catatan di `gangguan_sekolah.json` jika hilang dari list down PRTG ≥ 90 detik; interval background thread `INTERVAL_PENCATAT = 60` detik | Pisahkan tegas: riwayat downtime web existing ≠ tabel `incidents` operasional. Incident hanya boleh `resolved` lewat approval supervisor + PRTG recheck stabil. |
| **Sensor Terpilih Web** | `sensor_terpilih.json` di `tab_prtg.py` menyimpan maks 2 sensor per device untuk tampilan grafik | Tetap dipertahankan untuk grafik web; **terpisah** dari tabel `school_recovery_policies` (sensor wajib penutupan incident). |
| **Bot WhatsApp GreenAPI** | Terintegrasi di `prtg_sekolah.py` dan rute `/kirim-wa-sekolah`, `/kirim-wa-online` | Dipertahankan tetap berfungsi pada web existing (di luar sistem push notification FCM mobile). |

---

## 2. Daftar Keputusan Terkunci (Architecture Decision Records)

| ID | Topik | Keputusan Resmi (Locked) | Referensi |
|---|---|---|---|
| **ADR-001** | Source of Truth Telemetry | **PRTG** tetap satu-satunya source of truth status jaringan/sensor. Mobile tidak boleh mengakses PRTG secara langsung. | PRD §0.3, SAD ADR-001 |
| **ADR-002** | Baseline & Stack Shared Backend | Pertahankan `dashboard_sekolah.py` sebagai baseline dan refactor bertahap di **Python Flask** modular (Blueprints: Web Existing + REST `/api/v1`) dengan **SQLAlchemy + Alembic** dan dokumentasi **OpenAPI**. | PRD FR-027, SAD ADR-002 |
| **ADR-003** | Database Operasional | Menggunakan **PostgreSQL** untuk integritas transaksi, row-level locking (`SELECT FOR UPDATE`) pada assignment, audit trail, dan idempotency offline sync. | NFR-03, NFR-04 |
| **ADR-004** | Akses PRTG & Deployment | Saat development mengakses server PRTG secara langsung (dengan mock adapter untuk automated tests). Saat production, dashboard web + REST API v1 dideploy bersama di balik reverse proxy **HTTPS/TLS**. | NFR-01, FR-006 |
| **ADR-005** | Pemetaan Sekolah ↔ PRTG | Relasi `regions (1) → schools (N) → prtg_devices (N) → prtg_sensors (N)`. Auto-discovery menggunakan logic `is_sekolah()` & `normalisasi_nama_sekolah()` dari kode existing, dengan fitur Admin untuk merge/re-assign device ke sekolah yang tepat. | FR-004, FR-005, FR-007 |
| **ADR-006** | Pemisahan State Domain | Pisahkan secara ketat: (1) `network_status` (`online`, `offline`, `warning`, `unknown`) + indikator `is_stale`/`last_checked_at`; (2) `incident_status`; (3) `sync_status` (`draft`, `pending_sync`, `synced`, `conflict`, `failed`). | FR-008, NFR-05 |
| **ADR-007** | Canonical Incident Enum & Policy Re-occurrence | Gunakan **`awaiting_recovery`** sebagai nama resmi (menggantikan `pending_recovery` pada SAD). Gangguan baru setelah incident `resolved` **selalu membuat Incident baru** (tidak auto-reopen), dan seluruh incident tercatat dalam riwayat sekolah (`school_id`). | PRD §14, FR-007, FR-020 |
| **ADR-008** | Alur Supervisor Reject | Jika Supervisor berwenang menolak (*reject*) pengajuan resolusi, alasan wajib diisi, `VerificationRecord(decision='rejected')` dicatat, dan `incident_status` kembali ke **`in_progress`**. | FR-017, AC-09, TC-09 |
| **ADR-009** | Recovery Policy & Pembacaan Stabil | `SchoolRecoveryPolicy` disimpan di PostgreSQL (terpisah dari `sensor_terpilih.json`). Closure hanya terjadi jika **seluruh sensor wajib normal secara berturut-turut** selama beberapa kali pembacaan stabil. Default mengikuti interval existing (`60 detik`) dengan **`required_consecutive_normal = 3`** (dapat dikonfigurasi Admin per sekolah). Jika policy belum diatur, closure **diblokir**. | FR-018, FR-019, AC-10, AC-11 |
| **ADR-010** | Eksekusi Recheck Asinkron | Saat Supervisor melakukan `approve`, backend melakukan recheck ke-1 secara live (cepat, non-blocking). Jika normal namun butuh `N > 1` cek stabil (atau belum normal), status masuk `awaiting_recovery` dan **background recheck worker** melanjutkan pengecekan tiap interval hingga mencapai `N/N` berturut-turut untuk otomatis menjadi `resolved`. Setiap cek tersimpan di `prtg_recheck_logs`. | FR-018, FR-019, NFR-05 |
| **ADR-011** | Lima Role Tetap & RBAC Ketat | Lima role tetap: `admin`, `supervisor_utama`, `supervisor_wilayah`, `koordinator_teknisi`, `teknisi`. **Admin dilarang memiliki `incidents.verify`.** Izin opsional pada PRD dikunci ketat: hanya `teknisi` yang di-assign yang boleh acknowledge, mencatat response, dan submit resolusi; hanya `supervisor_utama`/`supervisor_wilayah` (sesuai scope) yang boleh approve/reject. | FR-002, AC-01–AC-03, AC-09 |
| **ADR-012** | Aturan Multi-Region per Role | `admin` & `supervisor_utama` = **global**; `supervisor_wilayah` = **tepat 1 wilayah**; `koordinator_teknisi` = **tepat 1 wilayah**; `teknisi` = **multi-region (≥ 1 wilayah)**. Validasi ditegakkan di backend. | FR-002, FR-023, TC-02 |
| **ADR-013** | Auto-Assignment & Tie-Breaker | Filter kandidat: wilayah sekolah cocok → akun aktif & `is_on_duty = true` → hitung jumlah incident aktif terendah → tie-breaker: **paling lama belum menerima tugas** (`last_assigned_at ASC NULLS FIRST`). Jika kosong → antrean `unassigned` + notifikasi ke Koordinator/Supervisor wilayah. Dilindungi transaksi & locking PostgreSQL. | FR-009, FR-010, AC-06, AC-07, NFR-03 |
| **ADR-014** | Mobile Stack & Offline Storage | **Expo (React Native + TypeScript + Expo Router + Development Build)**. Local cache & offline draft queue menggunakan **`expo-sqlite`** (dengan `client_operation_id` UUID & idempotency). Token auth disimpan di **`expo-secure-store`**. | NFR-06, NFR-07, FR-014, FR-015 |
| **ADR-015** | Push Notification & Attachment | Push notification menggunakan **Firebase Cloud Messaging (FCM)** untuk Android (siap diperluas ke APNs iOS) dengan deduplication & `notification_logs`. Foto bukti teknisi disimpan di folder lokal server terproteksi (`uploads/`, diakses lewat endpoint terotorisasi, maks **5 MB**, JPG/PNG/WebP). | FR-011, FR-013, FR-025, NFR-10 |
| **ADR-016** | Strategi Pengujian Mobile | Pengujian tahap awal menggunakan **Android Emulator**, dilanjutkan verifikasi pada **perangkat fisik Android** (foreground/background/terminated, push FCM, offline→online sync, upload foto) dan build APK internal. | NFR-06, PRD §10.3, CPMK-TIO5023-4 |

---

## 3. Canonical Incident State Machine (Final)

Daftar nilai resmi `incident_status` pada MVP 1.0:

1. `detected` — Gangguan baru terdeteksi dari PRTG, sedang diproses auto-assignment.
2. `unassigned` — Tidak ditemukan teknisi eligible (`is_on_duty` di wilayah sekolah); masuk antrean Koordinator/Supervisor.
3. `assigned` — Sudah ditetapkan ke seorang Teknisi (otomatis atau manual), menunggu konfirmasi.
4. `acknowledged` — Teknisi yang ditugaskan telah mengonfirmasi penerimaan tugas secara online.
5. `in_progress` — Teknisi sedang melakukan penanganan / mencatat response log (termasuk pasca-reject supervisor).
6. `pending_verification` — Teknisi telah mengajukan resolution submission; menunggu review Supervisor berwenang.
7. `awaiting_recovery` — Laporan telah disetujui Supervisor, menunggu PRTG recheck sensor wajib mencapai `N` kali normal berturut-turut (atau menunggu konfigurasi recovery policy / pemulihan sensor).
8. `resolved` — Seluruh sensor wajib lulus PRTG recheck stabil & valid; incident ditutup permanen (gangguan baru di masa depan membentuk incident baru).

### Tabel Transisi Legal

| Dari State | Ke State | Pemicu / Aktor | Syarat / Guard Wajib |
|---|---|---|---|
| *(baru)* | `detected` | Sistem (PRTG Ingest) | Sekolah belum punya incident aktif (`status != 'resolved'`) |
| `detected` | `assigned` | Sistem (Auto-Assignment) | Ada teknisi eligible (region + aktif + `is_on_duty`) |
| `detected` | `unassigned` | Sistem (Auto-Assignment) | Tidak ada teknisi eligible; kirim notifikasi ke Koordinator/Supervisor |
| `unassigned` / `assigned` / `acknowledged` / `in_progress` | `assigned` | Koordinator / Supervisor (sesuai scope) | Manual assign/reassign; alasan wajib diisi; simpan histori assignment lama |
| `assigned` | `acknowledged` | Teknisi (assigned) | Online, actor == current assignee |
| `acknowledged` | `in_progress` | Teknisi (assigned) | Otomatis saat mencatat `ResponseLog` pertama atau aksi mulai penanganan |
| `acknowledged` / `in_progress` | `pending_verification` | Teknisi (assigned) | Online, minimal sudah ada 1 catatan tindakan (`ResponseLog`) |
| `pending_verification` | `in_progress` | Supervisor Wilayah (scope) / Supervisor Utama | **Reject** + alasan wajib; catat `VerificationRecord(rejected)` |
| `pending_verification` | `awaiting_recovery` | Supervisor Wilayah (scope) / Supervisor Utama | **Approve** + catat `VerificationRecord(approved)` + picu PRTG recheck ke-1 |
| `awaiting_recovery` | `resolved` | Sistem (PRTG Recheck Worker) | `SchoolRecoveryPolicy` terkonfigurasi **AND** seluruh sensor wajib valid, segar, normal `N` kali berturut-turut |
| `awaiting_recovery` | `in_progress` | Koordinator / Supervisor (opsional jika sensor tetap down lama) | Opsional eskalasi kerja ulang; tercatat di `IncidentHistory` |

> **Larangan Mutlak:** Tidak ada jalur langsung `pending_verification → resolved` tanpa PRTG recheck, dan tidak ada jalur `detected/in_progress → resolved` otomatis hanya karena PRTG terlihat online tanpa workflow verifikasi.

---

## 4. Matriks RBAC & Scope Final (Server-Side)

| Permission / Aksi | Admin | Supervisor Utama | Supervisor Wilayah (1 region) | Koordinator Teknisi (1 region) | Teknisi (multi-region) |
|---|---|---|---|---|---|
| Monitoring global (`monitoring.view_all`) | YA | YA | TIDAK | TIDAK | TIDAK |
| Monitoring sesuai region (`monitoring.view_scope`) | YA | YA | YA (1 region) | YA (1 region) | YA (region miliknya) |
| CRUD User, status aktif, assign 5 role (`users.manage`, `roles.assign`) | YA | TIDAK | TIDAK | TIDAK | TIDAK |
| Kelola Region, Sekolah, Mapping Device, Recovery Policy (`regions.manage`, `system_config.manage`) | YA | TIDAK | TIDAK | TIDAK | TIDAK |
| Lihat Incident (`incidents.view_scope`) | Global (read-only) | Global | 1 region | 1 region | Assigned / region miliknya |
| Assign / Reassign Teknisi (`assignments.manage`) | TIDAK | Global | 1 region | 1 region | TIDAK |
| Atur status `is_on_duty` teknisi (`duty.manage`) | YA | YA | 1 region | 1 region | Self-toggle *(jika diizinkan)* |
| Acknowledge, Response Log, Draft Offline, Submit Resolusi (`responses.create`, `resolution.submit`) | TIDAK | TIDAK | TIDAK | TIDAK | **Hanya jika assigned** |
| Approve / Reject Verifikasi (`incidents.verify`) | **DILARANG** | Global | 1 region | **DILARANG** | **DILARANG** |
| Lihat Audit Administratif (`audit.view`) | Global | Terbatas (operasional) | Terbatas (1 region) | Terbatas (1 region) | TIDAK |

---

## 5. Kesiapan Environment Lokal

Berdasarkan pengecekan pada `2026-11-21`:

- **Sudah tersedia:** Python + `Flask 3.1.3`, `requests 2.34.2`, `openpyxl 3.1.5`, `reportlab 5.0.1`, dan `Git`.
- **Belum terpasang (diperlukan bertahap):**
  1. **PostgreSQL 16/17** — dibutuhkan mulai fase **M3**.
  2. **Node.js LTS (v20/v22)** — dibutuhkan mulai fase **M6**.
  3. **Android Studio (Android SDK & Emulator)** — dibutuhkan mulai fase **M6**.
