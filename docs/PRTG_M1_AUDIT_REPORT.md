---
title: "PRTG School Platform — Laporan Audit Backend & PRTG (Milestone M1)"
document_id: "AUDIT-PRTG-M1"
version: "1.0"
date: "2026-11-21"
status: "Verified against Live PRTG & Source Code"
references:
  - "docs/PRTG_DECISION_LOG.md (ADR-PRTG-001)"
  - "docs/PRTG_IMPLEMENTATION_PLAN_M0_M7.md (PLAN-PRTG-001)"
language: "id-ID"
---

# Laporan Audit Teknis Backend & PRTG (Milestone M1)

Dokumen ini mencatat hasil audit file-per-file terhadap codebase existing (`Dashboard_analitik-prtgSekolah/`) serta hasil baseline *read-only* terhadap server PRTG live (`2026-11-21`). Seluruh temuan di sini menjadi landasan langsung bagi **M3 (PostgreSQL & Shared Business Services)**.

---

## 1. Inventarisasi Rute Web Existing (21 Rute)

Seluruh rute berikut **wajib dipertahankan** (backward-compatible) selama migrasi M3–M5 dan dicakup oleh regression tests (`tests/test_web_routes_regression.py`).

### A. Rute di `dashboard_sekolah.py` (17 Rute)

| No | Path | Method | Auth | Output | Sumber Data Utama |
|---|---|---|---|---|---|
| 1 | `/favicon.svg` | GET | Publik | `image/svg+xml` | Inline SVG |
| 2 | `/favicon.ico` | GET | Publik | `image/svg+xml` | Alias `/favicon.svg` |
| 3 | `/login` | GET, POST | Publik | HTML / Redirect `302` | `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` (`.env`) |
| 4 | `/logout` | GET | Publik | Redirect `302` | Flask `session.clear()` |
| 5 | `/` | GET | `@login_required` | HTML (`TEMPLATE`) | PRTG down + online cache + `riwayat_sekolah.json` + `gangguan_sekolah.json` |
| 6 | `/offline` | GET | `@login_required` | HTML (`TEMPLATE`) | Alias `/` (mendukung query `?w=<wilayah>`) |
| 7 | `/offline/unduh/<jenis>` | GET | `@login_required` | PDF / XLSX | PRTG down + filter `?w=` & `?kat=` |
| 8 | `/online` | GET | `@login_required` | HTML (`ONLINE_TEMPLATE`) | PRTG online cache + `riwayat_online_v2.json` |
| 9 | `/online/unduh/<jenis>` | GET | `@login_required` | PDF / XLSX | PRTG online + filter `?w=`, `?up=`, `?q=` |
| 10 | `/pratinjau-wa` | GET | `@api_login_required` | JSON (`401` jika belum login) | Format pesan gangguan sekolah |
| 11 | `/kirim-wa-sekolah` | POST | `@api_login_required` | JSON (`200`/`502`) | GreenAPI `send_wa()` |
| 12 | `/pratinjau-wa-online` | GET | `@api_login_required` | JSON | Format pesan sekolah online |
| 13 | `/kirim-wa-online` | POST | `@api_login_required` | JSON (`200`/`502`) | GreenAPI `send_wa()` |
| 14 | `/sekolah/unduh/<jenis>` | GET | `@login_required` | PDF / XLSX | `bangun_resume(sid)` dari `gangguan_sekolah.json` |
| 15 | `/debug-wilayah` | GET | `@login_required` | HTML/JSON diagnostik | `diagnosa_belum()` |
| 16 | `/debug-wilayah-semua` | GET | `@login_required` | HTML/JSON diagnostik | Semua sekolah offline + sumber deteksi wilayah |
| 17 | `/debug-wilayah-online` | GET | `@login_required` | HTML/JSON diagnostik | Semua sekolah online + sumber deteksi wilayah |
| 18 | `/rekap/<mode>` | GET | `@login_required` | HTML | `mode` ∈ `{offline, online}`, filter `?w=`, `?p=`, `?kat=` |
| 19 | `/rekap/<mode>/unduh/<jenis>` | GET | `@login_required` | PDF / XLSX | Ekspor rekap periode |

### B. Rute yang Didaftarkan oleh `tab_prtg.py` via `tab_prtg.pasang()` (4 Rute)

| No | Path | Method | Auth | Output | Sumber Data Utama |
|---|---|---|---|---|---|
| 20 | `/sekolah` | GET | `@login_required` | HTML (Tab Resume) | `bangun_resume(sid)` + PRTG cache |
| 21 | `/sekolah/tab/<tab>` | GET | `@login_required` | HTML (`overview`, `alarms`, `log`) | PRTG `/api/table.json` + `sensor_terpilih.json` |
| 22 | `/sekolah/sensor/<sensor_id>` | GET | `@login_required` | HTML (Live, 2/30/365 Hari, Historic, Log) | PRTG `/api/historicdata.json` + `/api/table.json` |
| 23 | `/sekolah/pilih-sensor` | POST | `@login_required` | Redirect `302` | Menyimpan maks 2 sensor grafik ke `sensor_terpilih.json` |

---

## 2. Baseline Live PRTG API (Verifikasi `2026-11-21`)

Pengukuran dilakukan secara *read-only* menggunakan `prtg_sekolah.py cek` (tanpa mengirim WhatsApp) ke endpoint `/api/table.json?content=sensors&count=50000`:

| Metrik Baseline | Nilai Terukur | Implikasi Arsitektur (M3–M5) |
|---|---|---|
| **HTTP Status & Waktu Respons** | `HTTP 200`, ~`1 detik` | Aman untuk background polling tiap `60 detik` (`INTERVAL_PENCATAT`); tetap wajib pakai timeout terkontrol (`30 detik`) dan cache/snapshot DB (`NFR-11`). |
| **Total Sensor Terlihat Akun PRTG** | **11.704 sensor** | Besar karena mencakup infrastruktur non-sekolah (`AGGREGATE`, `BANDWITH DESA`, `BORDER ROUTER`, `CCTV & SW - ATCS`, `KEPATIHAN`, dll.). Filter `is_sekolah()` pada layer ingest sangat krusial. |
| **Total Sensor Down Saat Audit** | **372 sensor** pada **49 device** (**10 device** teridentifikasi sekolah) | Menunjukkan rasio sekolah vs non-sekolah di PRTG cukup selektif; auto-discovery ke PostgreSQL harus menyaring hanya device sekolah. |

### Distribusi Status Raw PRTG & Pemetaan Normalisasi Target (`PRTGAdapter` — M3)

| Raw Status dari PRTG Live | Jumlah Saat Audit | Normalized `network_status` (Target M3) | Catatan Penting |
|---|---:|---|---|
| `Up` | 7.864 | `online` | Normal / sehat |
| `Warning` | 10 | `warning` | Bukan `online` penuh; evaluasi terhadap policy sekolah |
| `Unusual` | 1.205 | `warning` *(perlu konfirmasi jika policy mengizinkan)* | Jumlah cukup besar (1.205 sensor); harus di-trim/normalisasi case-insensitive |
| `Down` *(termasuk `Down (Acknowledged)`, `Down (Partial)`)* | 372 | `offline` | Memicu korelasi/pembuatan Incident |
| `Paused  (paused)` *(spasi ganda!)*, `Paused  (paused by parent)`, `Paused` | 2.252 | `unknown` | **Temuan penting:** PRTG mengirim spasi ganda `"Paused  (paused)"` — normalizer wajib melakukan `re.sub(r"\s+", " ", raw.strip().lower())`. Sensor paused **tidak boleh** dianggap `online`! |
| `Unknown` | 1 | `unknown` | Tidak dianggap `online`; memblokir closure jika sensor wajib |

---

## 3. Peta Ekstraksi Pure Logic untuk M3 (Selaras SAD §4.4)

| Modul Target (`app/`) | Fungsi Existing yang Diekstrak | Status Web Existing |
|---|---|---|
| `app/config/settings.py` | Seluruh konstanta env PRTG, Flask, GreenAPI | **Sudah selesai di M2** |
| `app/common/logging.py` | `X-Request-ID`, structured JSON access log, `sanitize_text` | **Sudah selesai di M2** |
| `app/integrations/prtg/` | `get_prtg_auth_params`, `get_down_devices`, `get_online_devices`, `_tabel_prtg`, `_prtg` (`tab_prtg.py`), `parse_duration_to_hours`, `get_sensor_down_hours`, + fungsi baru `recheck_sensors(sensor_ids)` | Dipanggil oleh web existing & worker M3 |
| `app/modules/schools/` | `is_sekolah`, `filter_sekolah`, `nama_bersih`, `nama_tampil`, `normalisasi_nama_sekolah`, `tentukan_id`, `cari_ip` | Dipakai untuk auto-discovery `schools ↔ prtg_devices` |
| `app/modules/regions/` | `WILAYAH_URUT`, `WILAYAH_KATA`, `WILAYAH_KATA_NAMA`, `normalisasi`, `wilayah_valid`, `tentukan_wilayah_info`, `baca_pengaturan` | Seed 6 wilayah DIY + pemetaan wilayah di PostgreSQL |
| `app/modules/monitoring/` | `build_down_device_details`, `prepare_details`, `prepare_online_details`, `hitung_per_wilayah`, `ringkas_kategori`, freshness/stale logic | Shared service untuk web existing & `/api/v1/dashboard` |
| `app/web/` | Seluruh HTML `render_template_string`, `buat_grafik*`, `buat_pdf*`, `buat_xlsx*`, dan `tab_prtg.py` | Tetap di presentation layer web existing |

---

## 4. Analisis Kasus Multi-Device per Sekolah (Data Aktual `gangguan_sekolah.json`)

Dari inspeksi `gangguan_sekolah.json`, ditemukan beberapa kasus nyata di mana **1 sekolah yang sama memiliki >1 device PRTG**:

| Sekolah | Device ID #1 | Device ID #2 | Strategi Penanganan di M3 (`FR-007`) |
|---|---|---|---|
| **SMA Negeri 1 Turi** (Sleman) | `#16278` (`SMA N 1 TURI (103.210.35.72)`) | `#25256` (`SMA N 1 TURI`) | Normalisasi nama + wilayah sama → auto-link ke 1 `School` yang sama. |
| **SMA Negeri 1 Gamping** (Sleman) | `#16272` (`SMA N 1 GAMPING (103.210.35.67)`) | `SIM063` (`SIM063 SMA Negeri 01 Gamping`) & `#3672` | Tingkatkan `normalisasi_nama_sekolah()` agar menyamakan `SMA N 1`, `SMA NEGERI 01`, dan menghapus angka `0` di depan nomor sekolah (`01` → `1`). |
| **SMK Negeri 1 Gedangsari** (Gunung Kidul) | `#4423` (`SMK NEGERI 1 GEDANGSARI`) | `#9971` (`M.SMKN-1-GEDANGSARI CCR2004-1G-12S+2XS - 1G`) | Auto-seed membuat 2 entri awal jika pola prefix router (`M.SMKN-...`) berbeda; Admin dapat menggabungkan (`PATCH /api/v1/schools/{id}`) `prtg_device` `#9971` ke `School` SMKN 1 Gedangsari agar hanya memicu 1 incident aktif. |
| **Perangkat Infrastruktur Non-Sekolah** | `#2016` (`DR SMA SMK`), `#7514` (`BR.SMA-SMK`), `#25264` (`GW Sekolah`) | — | Lolos filter kata `sma`/`smk`/`sekolah` karena nama mengandung kata tersebut. Perlu ditambahkan ke daftar `kata_bukan_sekolah` / flag `is_active=false` oleh Admin di M3–M4. |
