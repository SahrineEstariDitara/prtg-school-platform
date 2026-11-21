# PRTG School Platform — Shared Backend & Existing Web Dashboard

Backend Python Flask untuk **Dashboard Analitik PRTG Sekolah** yang sedang direfactor bertahap (`M0`–`M5`) menjadi *shared backend* bagi Website Monitoring Existing dan Aplikasi Mobile React Native (Expo).

---

## Prasyarat

- Python 3.11+ (terverifikasi pada Python 3.14)
- Akses jaringan ke server PRTG (untuk mode operasional; tidak dibutuhkan untuk menjalankan automated tests)

## 1. Instalasi Dependensi

```powershell
python -m pip install -r requirements.txt
```

## 2. Konfigurasi Environment (`.env`)

Salin `.env.example` menjadi `.env`, lalu isi kredensial aktual (PRTG, Login Dashboard, Flask Secret Key, GreenAPI):

```powershell
Copy-Item .env.example .env
```

> **Penting:** File `.env` sudah masuk `.gitignore` dan **tidak boleh** di-commit ke Git.

## 3. Menjalankan Aplikasi

### Dashboard Web Existing (port default `5001`)
```powershell
python dashboard_sekolah.py
```
Buka `http://localhost:5001` dan login menggunakan `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` dari `.env`.

### Diagnosa Koneksi PRTG (Read-Only, Tanpa Kirim WhatsApp)
```powershell
python prtg_sekolah.py cek
```

## 4. Menjalankan Automated Tests (Baseline Regression Suite)

Semua test berjalan terisolasi menggunakan mock PRTG dan file JSON temporer (tidak menyentuh PRTG live maupun data JSON operasional):

```powershell
python -m pytest tests -v
```
