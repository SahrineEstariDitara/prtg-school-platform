---
title: "Panduan Lengkap Menjalankan Aplikasi PRTG School Platform (Backend, Mobile & Android Emulator)"
document_id: "RUNBOOK-PRTG-001"
version: "1.0"
date: "2026-11-27"
language: "id-ID"
---

# Panduan Lengkap Menjalankan PRTG School Platform

Dokumen ini berisi panduan langkah demi langkah secara detail untuk menyiapkan lingkungan, menjalankan **Shared Backend (Flask + PostgreSQL + Web Dashboard)**, menyiapkan **Android Studio Emulator (AVD)**, mem-build dan menjalankan **Aplikasi Mobile (Expo React Native)**, menguji alur 5 role, menghentikan layanan dengan bersih, hingga mengatasi kendala (*troubleshooting*) umum di Windows.

---

## 1. Prasyarat & Spesifikasi Lingkungan (Windows)

Pastikan seluruh komponen berikut sudah terpasang sebelum menjalankan proyek:

| Komponen | Versi / Lokasi yang Direkomendasikan | Keterangan |
| :--- | :--- | :--- |
| **Python** | Python 3.11+ (teruji di Python 3.14) | Hindari Windows Store stub; gunakan path instalasi Python asli (mis. `C:\Users\acer\AppData\Local\Python\bin\python.exe`). |
| **PostgreSQL** | PostgreSQL 17 (port `5433`) | Siapkan 2 database: `prtg_school_db` (operasional/demo) dan `prtg_school_test` (automated test). |
| **Node.js & npm** | Node.js LTS (`v22+` / `v24+`) | Di PowerShell Windows, gunakan **`npm.cmd`** dan **`npx.cmd`** untuk menghindari blokir *Execution Policy* `.ps1`. |
| **Java JDK** | **JDK 17** (`Eclipse Adoptium Temurin 17`) | **Penting:** Jangan pakai JDK 25 bawaan Android Studio JBR untuk build Gradle React Native. Install via `winget install EclipseAdoptium.Temurin.17.JDK`. |
| **Android Studio & SDK** | Android Studio + Android SDK Platform-Tools + Emulator | Lokasi default SDK: `C:\Users\<user>\AppData\Local\Android\Sdk`. |
| **Ruang Disk & RAM** | Free disk `C:` **≥ 20 GB** (≥ 25 GB untuk build lokal), RAM **16 GB** | Emulator membutuhkan minimal 12 GB free untuk partisi *userdata* + ruang untuk cache Gradle & Windows. |

### Pengaturan Environment Variable (Sekali Setup Permanen)
Agar `java` (JDK 17) dan `adb` selalu dikenali di PowerShell, Git Bash, maupun Android Studio:

1. Tekan tombol **Windows** -> cari **`Edit environment variables for your account`** -> buka.
2. Pada bagian **User variables**, tambahkan variabel baru:
   * **`JAVA_HOME`**: `C:\Program Files\Eclipse Adoptium\jdk-17.0.21.10002-hotspot` *(sesuaikan versi folder JDK 17 Anda)*
   * **`ANDROID_HOME`**: `C:\Users\<user>\AppData\Local\Android\Sdk`
3. Edit variabel **`Path`** -> tambahkan 2 entri baru:
   * `%JAVA_HOME%\bin`
   * `%ANDROID_HOME%\platform-tools`
4. Klik **OK** lalu tutup dan buka ulang VS Code / terminal Anda.

> **Jika ingin set sementara per sesi PowerShell:**
> ```powershell
> $env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.21.10002-hotspot"
> $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
> $env:Path = "$env:JAVA_HOME\bin;$env:ANDROID_HOME\platform-tools;$env:Path"
>
> # Verifikasi:
> java -version    # Harus tampil: openjdk version "17.x.x"
> adb version      # Harus tampil versi Android Debug Bridge
> ```

---

## 2. Langkah 1 — Setup & Menjalankan Shared Backend

Shared Backend berada di folder `Dashboard_analitik-prtgSekolah/`. Server ini melayani **Website Monitoring Existing** sekaligus **REST API v1 (`/api/v1/*`)** untuk aplikasi mobile di port **`5001`**.

### A. Setup Pertama Kali (Lewati jika sudah pernah dilakukan)
Buka **Terminal PowerShell 1**:

```powershell
cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\Dashboard_analitik-prtgSekolah"

# 1. Install seluruh dependensi Python
C:\Users\acer\AppData\Local\Python\bin\python.exe -m pip install -r requirements.txt

# 2. Salin template .env (jika belum ada) lalu isi kredensial PRTG, PostgreSQL, JWT, & Admin
Copy-Item .env.example .env

# 3. Jalankan migrasi tabel PostgreSQL (Alembic)
$env:FLASK_APP = "app:create_app"
C:\Users\acer\AppData\Local\Python\bin\python.exe -m flask db upgrade

# 4. Seed data baseline (5 role tetap, wilayah DIY, akun admin awal)
C:\Users\acer\AppData\Local\Python\bin\python.exe -m app.infrastructure.database.seed

# 5. Seed 5 akun demo lintas role & wilayah untuk UAT/demo
C:\Users\acer\AppData\Local\Python\bin\python.exe app/infrastructure/database/seed_uat.py
```

### B. Menjalankan Server Backend Setiap Kali Demo / Testing
Di **Terminal PowerShell 1**:

```powershell
cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\Dashboard_analitik-prtgSekolah"

# 1. (Disarankan) Jalankan 1 siklus worker untuk sinkronisasi PRTG & korelasi incident aktif
C:\Users\acer\AppData\Local\Python\bin\python.exe -m app.infrastructure.workers --once

# 2. Jalankan server Flask + REST API v1 + Web Dashboard pada port 5001
C:\Users\acer\AppData\Local\Python\bin\python.exe dashboard_sekolah.py
```

### C. Menjalankan Backend Mode Produksi (WSGI + Worker Terpisah)
Untuk deployment server produksi (di balik Nginx/Caddy HTTPS):

```powershell
# Terminal A — Web & REST API Server (Waitress di Windows / Gunicorn di Linux)
waitress-serve --host=127.0.0.1 --port=5001 wsgi:app
# (Di Linux): gunicorn -w 4 -b 127.0.0.1:5001 "wsgi:app"

# Terminal B — Background Operational Worker (PRTG sync, correlation, multi-check recovery)
C:\Users\acer\AppData\Local\Python\bin\python.exe -m app.infrastructure.workers
```

### D. Verifikasi Backend Aktif
Buka di browser PC Anda:
* **Swagger UI / OpenAPI 3.0.3:** `http://localhost:5001/api/v1/docs`
* **Website Monitoring Existing:** `http://localhost:5001/`

> **Penting:** Biarkan terminal backend ini tetap terbuka selama Anda menjalankan dan menguji aplikasi mobile.

---

## 3. Langkah 2 — Menyiapkan & Menyalakan Emulator di Android Studio

### A. Membuat Virtual Device (AVD) Baru
Jika daftar **Device Manager** di Android Studio masih kosong (*"No devices connected"*):

1. Buka **Android Studio** -> buka menu **Device Manager** (ikon HP di sisi kanan atau **More Actions > Virtual Device Manager**).
2. Klik **`Add a new device...`** (atau ikon **`+`** di pojok kiri atas) -> pilih **Create Virtual Device**.
3. Pada kategori **Phone**, pilih profil HP (mis. **Pixel 7** atau **Medium Phone**) -> klik **Next**.
4. Pada pemilihan **System Image**, pilih image yang sudah terunduh (mis. **API `37.0` — `Google Play`, `x86_64`**) agar tidak perlu mengunduh ulang ~2.6 GB -> klik **Next**.
5. Klik **Additional settings / Show Advanced Settings**:
   * Atur **Internal Storage** ke **`4096 MB` – `6144 MB`** (`4–6 GB`) agar hemat ruang disk `C:`.
6. Klik **Finish**.

### B. Menyalakan Emulator & Verifikasi ADB
1. Pada daftar Device Manager, klik tombol **▶ (Start / Launch)** di samping nama emulator.
2. Tunggu hingga proses booting selesai dan layar utama (*Home Screen*) Android tampil penuh.
3. Cek dari PowerShell apakah emulator sudah terbaca oleh ADB:
   ```powershell
   & "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" devices
   ```
   Pastikan output menampilkan baris seperti:
   ```text
   List of devices attached
   emulator-5554   device
   ```

### C. Alternatif: Menggunakan HP Android Fisik via Kabel USB
Jika ingin testing langsung di HP fisik (lebih ringan di RAM & bisa langsung pakai kamera asli):
1. Aktifkan **Developer Options** & **USB Debugging** di HP Android, lalu sambungkan ke PC via kabel USB.
2. Jalankan port forwarding ADB agar HP bisa mengakses `localhost:5001` PC:
   ```powershell
   & "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" reverse tcp:5001 tcp:5001
   ```
3. Ubah `apps/mobile/.env` menjadi:
   ```env
   EXPO_PUBLIC_API_URL=http://localhost:5001/api/v1
   ```
   *(Atau gunakan IP LAN Wi-Fi komputer Anda, mis. `http://192.168.1.10:5001/api/v1`, jika menguji tanpa kabel USB).*

---

## 4. Langkah 3 — Setup, Build & Menjalankan Aplikasi Mobile (`apps/mobile`)

### A. Konfigurasi Awal Mobile
Pastikan file `apps/mobile/.env` sudah ada (salin dari `apps/mobile/.env.example`):

```env
# Khusus Android Emulator (10.0.2.2 otomatis diarahkan ke localhost:5001 PC Windows)
EXPO_PUBLIC_API_URL=http://10.0.2.2:5001/api/v1
```

Install dependensi Node.js (jika baru clone repo atau setelah perubahan `package.json`):

```powershell
cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile"
npm.cmd install --legacy-peer-deps
```

### B. Cara 1 — Build & Install APK Debug ke Emulator (Pertama Kali / Setelah Ubah Modul Native)
Di **Terminal PowerShell 2** (pastikan `JAVA_HOME` JDK 17 dan `ANDROID_HOME` aktif serta emulator menyala):

```powershell
cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile"

# 1. Generate folder native android (menerapkan plugin cleartext & short-path Windows di app.config.ts)
$env:EXPO_NO_TELEMETRY = "1"
$env:USERPROFILE = "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile\.expo"
npx.cmd expo prebuild --platform android --clean

# 2. Build APK Debug (khusus arsitektur x86_64 emulator agar lebih cepat)
cd android
.\gradlew.bat app:assembleDebug -x lint -x test --configure-on-demand --build-cache -PreactNativeDevServerPort=8081 -PreactNativeArchitectures=x86_64 --no-daemon

# 3. Install APK Debug ke emulator yang sedang menyala
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" install -r "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile\android\app\build\outputs\apk\debug\app-debug.apk"
```

> **Catatan:** Anda juga bisa memakai perintah satu baris `npx.cmd expo run:android` dari folder `apps/mobile`. File hasil build debug berada di `apps/mobile/android/app/build/outputs/apk/debug/app-debug.apk`.

### C. Cara 2 — Menjalankan Harian (Cepat — Jika APK Debug Sudah Terpasang di Emulator)
Jika aplikasi **PRTG School Mobile** sudah pernah terpasang di emulator dan Anda tidak mengubah dependensi native, Anda **tidak perlu build Gradle ulang**:

```powershell
cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile"
npx.cmd expo start --dev-client
```

Setelah server Metro siap, tekan tombol **`a`** di terminal (atau buka ikon **PRTG School Mobile** di emulator) untuk langsung memuat aplikasi dengan *Fast Refresh / Hot Reload*.

### D. Cara 3 — Build APK Release Mandiri (Tanpa Butuh Metro Bundler)
Gunakan cara ini jika ingin membagikan file `.apk` final yang bisa langsung dipasang di HP/emulator tanpa perlu menjalankan `expo start`:

#### Opsi 1 — Build APK Lokal (Gradle Release)
```powershell
cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile\android"
.\gradlew.bat app:assembleRelease --no-daemon
```
Hasil APK tersimpan di:
`apps\mobile\android\app\build\outputs\apk\release\app-release.apk`

Install ke emulator / HP via USB:
```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" install -r "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile\android\app\build\outputs\apk\release\app-release.apk"
```

#### Opsi 2 — Build APK via EAS Build Cloud (Tidak Butuh JDK 17 Lokal)
1. Sesuaikan `EXPO_PUBLIC_API_URL` pada profil `preview` atau `production` di `apps/mobile/eas.json`:
   * Emulator: `"http://10.0.2.2:5001/api/v1"`
   * HP fisik LAN: `"http://<IP-LAN-PC>:5001/api/v1"`
   * Produksi HTTPS: `"https://<domain-anda>/api/v1"`
2. Jalankan build cloud:
   ```powershell
   cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile"
   npx.cmd eas-cli login
   npx.cmd eas-cli init
   npx.cmd eas-cli build --platform android --profile preview
   ```
3. Unduh file `.apk` dari tautan EAS, lalu drag-and-drop ke layar emulator atau kirim ke HP Android.

---

## 5. Langkah 4 — Akun Demo 5 Role & Checklist Pengujian di Emulator

Setelah aplikasi terbuka di emulator, bagian bawah layar Login akan menampilkan alamat backend aktif (mis. `Backend API: http://10.0.2.2:5001/api/v1`).

### Daftar Akun Demo (`seed_uat.py`)

| Username | Password | Role | Scope Wilayah | Fokus Pengujian |
| :--- | :--- | :--- | :--- | :--- |
| `admin` | *(sesuai `ADMIN_SEED_PASSWORD` di `.env`)* | `admin` | Global (DIY) | Kelola user, wilayah, recovery policy sensor, audit log. **Tidak boleh** ada tombol verifikasi incident. |
| `spv_utama` | `UatDemo!2026` | `supervisor_utama` | Global (DIY) | Monitoring semua wilayah, override assignment, verifikasi Approve/Reject & PRTG recheck. |
| `spv_bantul` | `UatDemo!2026` | `supervisor_wilayah` | Bantul (1 wilayah) | Hanya melihat sekolah/incident Bantul; verifikasi Approve/Reject & recheck. |
| `koor_bantul` | `UatDemo!2026` | `koordinator_teknisi` | Bantul (1 wilayah) | Pantau antrean `unassigned`, beban aktif teknisi, Assign/Reassign manual dengan alasan wajib. |
| `teknisi_bantul` | `UatDemo!2026` | `teknisi` | Bantul & Kota Jogja | Acknowledge (online), catat tindakan + foto bukti, draft offline SQLite, submit resolusi. |
| `teknisi_sleman` | `UatDemo!2026` | `teknisi` | Sleman | Uji isolasi wilayah (`TC-02`) & isolasi draft offline per akun. |

### Skenario Uji End-to-End di Emulator
1. **Uji RBAC & Scope (`TC-01`–`TC-03`):**
   * Login `admin` -> pastikan tab **Admin** muncul, buka salah satu detail incident dan pastikan panel **Verifikasi Supervisor (Approve/Reject)** tidak muncul.
   * Login `spv_bantul` -> pastikan data sekolah/incident hanya menampilkan wilayah **Bantul**.
2. **Uji Monitoring & Pemisahan Status (`TC-04`):**
   * Buka tab **Sekolah** -> cari sekolah -> buka detail sekolah -> pastikan badge **Jaringan** (`Online/Offline/Warning/Unknown`), badge **Incident**, dan banner **Data PRTG Terkini / Stale** tampil terpisah.
3. **Uji Penugasan & Alur Teknisi (`TC-06`–`TC-08`):**
   * Login `koor_bantul` -> buka tab **Tim & Tugas** -> uji manual Assign/Reassign dengan mengisi alasan wajib.
   * Login `teknisi_bantul` -> buka detail tugas -> klik **Acknowledge Tugas (Online)** -> klik **Catat Tindakan & Foto Bukti** -> lampirkan foto (drag file `.jpg` dari PC ke layar emulator agar masuk ke Galeri emulator, atau gunakan kamera virtual).
4. **Uji Mode Offline & Auto-Sync SQLite (`TC-12`):**
   * Di layar **Catat Tindakan**, buka toolbar kanan emulator **`...` (Extended Controls) > Cellular > Data status: Denied** dan matikan **Wi-Fi** di status bar Android emulator.
   * Simpan tindakan -> pastikan banner **Mode Offline Terbatas** muncul dan draft tersimpan di tab **Draft Offline** (`Sync: Menunggu Sinkronisasi`).
   * Nyalakan kembali **Wi-Fi** / ubah Data status kembali ke **Home** -> amati draft otomatis tersinkronisasi ke server (`Sync: Tersinkron`) tanpa duplikasi.
5. **Uji Verifikasi Supervisor & PRTG Recheck (`TC-09`–`TC-11`):**
   * Dari akun teknisi klik **Ajukan Penyelesaian (Online)**.
   * Login `spv_bantul` -> buka tab **Verifikasi** -> uji **Reject** (wajib mengisi alasan) -> ajukan ulang -> uji **Approve & Recheck PRTG** -> pantau status `awaiting_recovery` dan progres `streak / N`.
6. **Uji Konsistensi Web Dashboard (`TC-14`):**
   * Buka `http://localhost:5001/offline` di browser PC dan pastikan badge **Incident DB Aktif** serta status sekolah selaras dengan aplikasi mobile.

---

## 6. Langkah 5 — Cara Menghentikan Emulator & Project dengan Bersih

1. **Matikan Emulator Android** (pilih salah satu):
   * Di **Android Studio > Device Manager**: klik **Stop (■)** atau menu `⋮` -> **Stop**.
   * Di jendela Emulator: klik ikon **Power** -> **Power off**, atau klik tanda **`X`** (tunggu hingga *Saving state* selesai).
   * Via PowerShell (jika macet):
     ```powershell
     & "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" emu kill
     ```
2. **Matikan Metro Bundler & Server Backend:**
   * Klik masing-masing terminal PowerShell (`expo start` dan `dashboard_sekolah.py`), lalu tekan **`Ctrl + C`**.
3. **Lepaskan RAM Gradle Daemon (Opsional):**
   ```powershell
   cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile\android"
   .\gradlew.bat --stop
   ```

---

## 7. Langkah 6 — Troubleshooting Error Umum di Windows

| Gejala / Error | Penyebab | Solusi |
| :--- | :--- | :--- |
| `npx.ps1 cannot be loaded because running scripts is disabled` | PowerShell *Execution Policy* memblokir skrip `.ps1`. | Gunakan **`npx.cmd`** dan **`npm.cmd`**, atau jalankan `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`. |
| `ERROR: JAVA_HOME is not set` | Variabel `JAVA_HOME` belum terbaca di terminal (atau memakai sintaks `$env:` di Git Bash). | Di **PowerShell** gunakan `$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17..."`. Di **Git Bash** gunakan `export JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-17..."`. |
| `The emulator process for AVD ... has terminated` | Ruang kosong disk `C:` kurang dari `12 GB` untuk partisi *userdata* emulator. | Kosongkan `C:` hingga minimal `18–20 GB` free (bersihkan `AppData\Local\Temp`, `npm cache`, `pip cache`), atau pindahkan AVD ke `D:\Android\avd` dengan env `ANDROID_AVD_HOME=D:\Android\avd`. |
| `ninja: error: Filename longer than 260 characters` | Batas panjang path Windows (`MAX_PATH`) pada `ninja.exe` di Android SDK CMake. | Sudah diperbaiki otomatis melalui plugin `PRTG_WIN_CMAKE_SHORT_PATH` & `PRTG_APP_CMAKE_SHORT_PATH` pada `apps/mobile/app.config.ts` (staging pendek `D:\c\` + `-DCMAKE_OBJECT_PATH_MAX=230`). Jalankan `npx.cmd expo prebuild --platform android --clean` jika `android/` belum terbarui. |
| `EPERM: operation not permitted, open 'C:\Users\...\ .expo\...'` | Izin tulis cache global Expo di folder user. | Set `$env:EXPO_NO_TELEMETRY="1"` dan `$env:USERPROFILE="D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile\.expo"` sebelum menjalankan perintah Expo. |
| Aplikasi di emulator gagal login (`Tidak dapat terhubung ke server backend`) | Backend belum berjalan di port `5001` atau `.env` memakai `localhost` bukan `10.0.2.2`. | Pastikan `dashboard_sekolah.py` aktif di port `5001` dan `apps/mobile/.env` berisi `EXPO_PUBLIC_API_URL=http://10.0.2.2:5001/api/v1`. |
| `Set-Location: A positional parameter cannot be found ... 'Magang\...'` | Path folder mengandung spasi (`Project Magang`) tanpa tanda kutip. | Selalu apit path dengan tanda kutip ganda: `cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile"`. |

---

## 8. Langkah 7 — Menjalankan Seluruh Automated Tests

Untuk memverifikasi ulang kualitas kode sebelum commit, build APK, atau deploy:

```powershell
# 1. Automated Test Backend (Web regression + PostgreSQL + API/RBAC + Workflow M5 + UAT TC-01..TC-14)
cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\Dashboard_analitik-prtgSekolah"
C:\Users\acer\AppData\Local\Python\bin\python.exe -m pytest -v

# 2. Automated Test Mobile (TypeScript strict check + Jest/RNTL + Android Hermes Bundle)
cd "D:\Pemrograman\Project Magang\Mobile_PRTG_Koversi\apps\mobile"
npm.cmd run typecheck
npm.cmd test -- --watchAll=false
```
