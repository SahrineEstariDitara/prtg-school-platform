# PRTG School Platform — Focused MVP 1.0

Monorepo untuk **PRTG School Platform**, menggabungkan:
1. **Shared Backend & Existing Web Dashboard** (`Dashboard_analitik-prtgSekolah/`): Python Flask + PostgreSQL 17 + SQLAlchemy/Alembic + REST API v1 (`/api/v1/*`) + OpenAPI 3.0.3 (`/api/v1/docs`) + 23 rute web monitoring existing yang tetap dipertahankan.
2. **Cross-Platform Mobile App** (`apps/mobile/`): Expo SDK 58 + React Native + TypeScript + Expo Router (`src/app/`) untuk 5 role tetap (`admin`, `supervisor_utama`, `supervisor_wilayah`, `koordinator_teknisi`, `teknisi`), offline draft queue (`expo-sqlite`), secure token storage (`expo-secure-store`), dan push notification FCM (`expo-notifications`).
3. **Dokumentasi Arsitektur, Keputusan & Bukti Akademik** (`docs/`): PRD, SAD, Decision Log (`ADR-001`–`ADR-016`), Audit M1, Plan M0–M7, Laporan UAT `TC-01`–`TC-14`, dan Portofolio CPMK TIO5023.

---

## Arsitektur Monorepo

```text
Mobile_PRTG_Koversi/
├── Dashboard_analitik-prtgSekolah/   # Shared Backend (Flask + PostgreSQL + REST API v1 + Web Existing)
│   ├── app/
│   │   ├── api/v1/                   # Endpoints REST API v1 + OpenAPI 3.0.3 & Swagger UI
│   │   ├── common/                   # Logging X-Request-ID, RBAC server-side, responses, errors, audit
│   │   ├── config/                   # Settings berbasis .env + redaksi rahasia (sanitize_text)
│   │   ├── infrastructure/           # Database SQLAlchemy, seed, seed_uat, FCM push adapter, worker
│   │   ├── integrations/prtg/        # PRTGAdapter terpusat (server-side only) + normalizer
│   │   └── modules/                  # Domain modular: auth, users, regions, schools, monitoring, incidents, assignments, responses, verification, notifications
│   ├── migrations/                   # Skrip migrasi Alembic (reversible upgrade/downgrade)
│   ├── tests/                        # Automated test suite (M2–M7: Web regression + API + E2E TC-01..TC-14)
│   ├── dashboard_sekolah.py          # Entry point Flask Web + REST API v1 (port default 5001)
│   └── prtg_sekolah.py               # Bot diagnosa & notifikasi PRTG existing
├── apps/mobile/                      # Mobile App MVP (Expo React Native + TypeScript, SAD §9.2)
│   ├── src/app/                      # Expo Router (native Stack & role-aware Tabs)
│   ├── src/navigation/               # Konfigurasi 5 role tetap + deep-link auth guard
│   ├── src/features/                 # 11 modul domain operasional
│   ├── src/shared/                   # Design system primitives, API client, SQLite/SecureStore, types, validators
│   ├── src/native/                   # Android notification channel config
│   ├── __tests__/                    # Jest + React Native Testing Library suite
│   ├── app.config.ts                 # Dynamic Expo config (auto-detect google-services.json)
│   └── eas.json                      # Profil EAS Build (development, preview APK, production)
└── docs/                             # Spesifikasi, ADR, Laporan UAT & Portofolio CPMK TIO5023
```

---

## 1. Setup Lokal Shared Backend (Flask + PostgreSQL 17)

### Prasyarat
- Python 3.11+
- PostgreSQL 17 (buat 2 database: `prtg_school_db` dan `prtg_school_test`)

### Instalasi & Migrasi Database
```powershell
cd Dashboard_analitik-prtgSekolah
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Isi kredensial PRTG, PostgreSQL, JWT_SECRET_KEY, dan ADMIN_SEED_PASSWORD pada file .env
```

Jalankan migrasi Alembic, seed awal, seed akun demo UAT (opsional), dan sinkronisasi PRTG:
```powershell
$env:FLASK_APP="app:create_app"
python -m flask db upgrade
python -m app.infrastructure.database.seed
# Opsional (khusus dev/demo UAT 5 role):
python app/infrastructure/database/seed_uat.py
# Sinkronisasi katalog sekolah/device/sensor dari PRTG ke PostgreSQL:
python -c "from app import create_app; from app.infrastructure.database import db; from app.modules.schools.sync_service import SchoolSyncService; a=create_app(); ctx=a.app_context(); ctx.push(); print(SchoolSyncService().sync_from_prtg(db.session))"
```

### Menjalankan Server Backend & Swagger UI
```powershell
python dashboard_sekolah.py
```
- **Website Monitoring Existing:** `http://localhost:5001/`
- **Swagger UI / OpenAPI 3.0.3:** `http://localhost:5001/api/v1/docs` & `http://localhost:5001/api/v1/openapi.json`

---

## 2. Setup Lokal & Build APK Android (`apps/mobile`)

### Prasyarat
- Node.js LTS (terverifikasi pada `v24.22.3` + `npm 11.19.1`)
- Android Studio + Android SDK (untuk Android Emulator & build lokal; disarankan JDK 17/21 untuk local Gradle build)

### Menjalankan di Android Emulator / HP Fisik
```powershell
cd apps/mobile
npm install --legacy-peer-deps
Copy-Item .env.example .env
```
- **Android Emulator:** gunakan default `EXPO_PUBLIC_API_URL=http://10.0.2.2:5001/api/v1`.
- **HP Android Fisik via USB:** jalankan `adb reverse tcp:5001 tcp:5001`, lalu ubah `.env` menjadi `EXPO_PUBLIC_API_URL=http://localhost:5001/api/v1` (atau IP LAN komputer backend).
- Jalankan Metro/Expo:
  ```powershell
  npm run start
  ```

### Build APK Android (Internal / Demonstrasi)

#### Jalur A — EAS Build Cloud (Paling Praktis, Tidak Butuh JDK Lokal)
```powershell
cd apps/mobile
# Sesuaikan EXPO_PUBLIC_API_URL pada profil preview/production di eas.json terlebih dahulu
npx eas-cli login
npx eas-cli init
npx eas-cli build --platform android --profile preview
```
Profil `preview` pada `apps/mobile/eas.json` dikonfigurasi dengan `"buildType": "apk"` dan `"distribution": "internal"` untuk menghasilkan file `.apk` siap pasang di perangkat Android fisik. `app.config.ts` otomatis mengizinkan HTTP cleartext hanya bila `EXPO_PUBLIC_API_URL` memakai `http://`, dan otomatis menonaktifkannya (`false`) saat memakai `https://` produksi.

#### Jalur B — Build APK Lokal (Android SDK + JDK 17/21)
```powershell
cd apps/mobile
$env:ANDROID_HOME="$env:LOCALAPPDATA\Android\Sdk"
# Pastikan JAVA_HOME mengarah ke JDK 17 atau JDK 21 (bukan JDK 25)
npx expo prebuild --platform android --clean
cd android
.\gradlew.bat assembleRelease
# Output APK: apps/mobile/android/app/build/outputs/apk/release/app-release.apk
# Instalasi ke HP fisik via USB:
& "$env:ANDROID_HOME\platform-tools\adb.exe" install -r app\build\outputs\apk\release\app-release.apk
```

> **Push Notification FCM Live:** Unduh `google-services.json` dari Firebase Console untuk package `id.jogjaprov.prtgschool` dan simpan di `apps/mobile/google-services.json` (file sudah masuk `.gitignore` dan otomatis terdeteksi oleh `app.config.ts`). Pada sisi backend, set `FCM_ENABLED=true` dan `FCM_SERVICE_ACCOUNT_PATH=...` di `.env`.

---

## 3. Panduan Deployment Produksi Shared Backend (HTTPS Reverse Proxy)

Sesuai `SAD §10.1`–`§10.4` (`NFR-01`):
1. **Jangan jalankan server dev Flask langsung di produksi.** Gunakan WSGI server (`wsgi:app`) dan jalankan worker operasional terpisah agar sinkronisasi PRTG & recheck recovery berjalan sekali (tidak terduplikasi antar-worker web):
   ```powershell
   # Linux (Gunicorn + 4 workers web + 1 worker operasional terpisah)
   gunicorn -w 4 -b 127.0.0.1:5001 "wsgi:app"
   python -m app.infrastructure.workers

   # Windows (Waitress web + worker terpisah, atau set ENABLE_WSGI_EMBEDDED_WORKER=true)
   waitress-serve --host=127.0.0.1 --port=5001 wsgi:app
   python -m app.infrastructure.workers
   ```
2. **Pasang HTTPS Reverse Proxy (Nginx / Caddy)** di depan port `5001` dengan sertifikat TLS valid:
   ```nginx
   server {
       listen 443 ssl http2;
       server_name prtg-school.example.go.id;

       client_max_body_size 8M; # Menampung upload foto bukti maks 5 MB + overhead multipart

       location / {
           proxy_pass http://127.0.0.1:5001;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_read_timeout 60s;
       }
   }
   ```
3. **Hardening & Environment Produksi:**
   - Wajib gunakan `FLASK_SECRET_KEY` & `JWT_SECRET_KEY` acak ≥ 32 karakter berbeda dari dev.
   - Jangan pernah mengekspos kredensial PRTG atau PostgreSQL ke aplikasi mobile.
   - Aktifkan worker operasional (`ENABLE_OPERATIONAL_WORKER=true`) hanya pada 1 proses worker tunggal (atau jalankan sebagai cron/service terpisah agar tidak terjadi duplikasi polling).
4. **Backup, Restore & Rollback Database (`SAD §10.4`):**
   ```powershell
   # Backup harian terkompresi sebelum migrasi atau rilis
   pg_dump -U postgres -h localhost -p 5433 -Fc prtg_school_db -f backup_prtg_school.dump

   # Uji restore ke database staging/recovery
   pg_restore -U postgres -h localhost -p 5433 -d prtg_school_restore --clean --if-exists backup_prtg_school.dump

   # Rollback migrasi Alembic (gunakan ID revisi eksplisit)
   flask db downgrade <revision_id>
   ```

---

## 4. Menjalankan Seluruh Automated Tests

```powershell
# 1. Backend Regression + API + Operational Workflow + UAT TC-01..TC-14
python -m pytest Dashboard_analitik-prtgSekolah/tests -v

# 2. Mobile TypeScript Strict Check + Jest/RNTL + Expo Android Hermes Bundle
cd apps/mobile
npm run typecheck
npm test -- --watchAll=false
npx expo export --platform android
```

---

## 5. Daftar Dokumen Kunci

- [docs/PRTG_DECISION_LOG.md](docs/PRTG_DECISION_LOG.md) — Keputusan arsitektur `ADR-001`–`ADR-016` & state machine kanonis
- [docs/PRTG_IMPLEMENTATION_PLAN_M0_M7.md](docs/PRTG_IMPLEMENTATION_PLAN_M0_M7.md) — Roadmap & status penyelesaian `M0`–`M7`
- [docs/PRTG_M1_AUDIT_REPORT.md](docs/PRTG_M1_AUDIT_REPORT.md) — Hasil audit 23 rute web & baseline live PRTG
- [docs/PRTG_UAT_REPORT_TC01_TC14.md](docs/PRTG_UAT_REPORT_TC01_TC14.md) — Laporan UAT `TC-01`–`TC-14` & matriks `AC-01`–`AC-14`
- [docs/CPMK_TIO5023_PORTFOLIO.md](docs/CPMK_TIO5023_PORTFOLIO.md) — Paket bukti akademik `CPMK-TIO5023-1` s.d. `4`
