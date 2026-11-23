# PRTG School Platform — Shared Backend & Existing Web Dashboard

Backend Python Flask + PostgreSQL 17 untuk **PRTG School Platform** yang berfungsi sebagai *shared backend* bagi **Website Monitoring Existing (23 rute)** dan **Aplikasi Mobile React Native Expo (`apps/mobile`)** melalui **REST API v1 (`/api/v1/*`)**.

---

## Prasyarat
- Python 3.11+ (terverifikasi pada Python 3.14)
- PostgreSQL 17 (`prtg_school_db` & `prtg_school_test`)
- Akses jaringan ke server PRTG (untuk mode operasional; tidak dibutuhkan untuk automated tests)

## 1. Instalasi & Konfigurasi (`.env`)
```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```
Isi kredensial PRTG, PostgreSQL, `FLASK_SECRET_KEY`, `JWT_SECRET_KEY`, dan `ADMIN_SEED_PASSWORD` pada `.env` (file `.env` diabaikan oleh `.gitignore`).

## 2. Migrasi Database & Seeding
```powershell
$env:FLASK_APP="app:create_app"
python -m flask db upgrade
python -m app.infrastructure.database.seed
# Opsional: buat 5 akun demo lintas role & wilayah untuk UAT/demo lokal
python app/infrastructure/database/seed_uat.py
```

## 3. Menjalankan Server & Dokumentasi API
```powershell
python dashboard_sekolah.py
```
- **Web Dashboard Existing:** `http://localhost:5001/`
- **OpenAPI 3.0.3 JSON:** `http://localhost:5001/api/v1/openapi.json`
- **Swagger UI:** `http://localhost:5001/api/v1/docs`
- **Diagnosa PRTG CLI (Read-Only):** `python prtg_sekolah.py cek`

## 4. Automated Test Suite (M2–M7)
Mencakup baseline regression web existing, integrasi PostgreSQL & PRTGAdapter, kontrak API & negative RBAC, workflow M5 (correlation, locking, photo upload, 3x consecutive PRTG recheck, offline sync, push FCM), dan suite formal UAT `TC-01`–`TC-14`:
```powershell
python -m pytest tests -v
```

Untuk panduan deployment produksi di balik HTTPS Reverse Proxy (Nginx/Caddy + Gunicorn/Waitress), backup/restore PostgreSQL, dan build APK Android, lihat [README.md](../README.md) di root monorepo.
