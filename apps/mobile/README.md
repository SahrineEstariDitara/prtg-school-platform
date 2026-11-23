# PRTG School Mobile (`apps/mobile`)

Aplikasi mobile cross-platform **PRTG School Platform — Focused MVP 1.0** berbasis **Expo + React Native + TypeScript + Expo Router**.

## Arsitektur Direktori (SAD §9.2)

- `src/app/`: Expo Router native Stack & role-aware Tabs (`(tabs)/`, `schools/`, `incidents/`, `admin/`, `notifications`, `offline-queue`).
- `src/navigation/`: Konfigurasi kemampuan 5 role tetap (`admin`, `supervisor_utama`, `supervisor_wilayah`, `koordinator_teknisi`, `teknisi`) & deep-link auth guard.
- `src/features/`: 11 modul domain (`auth`, `dashboard`, `schools`, `monitoring`, `incidents`, `assignments`, `responses`, `verification`, `notifications`, `administration`, `offline-sync`).
- `src/shared/`: Design system primitives (`components/`), `hooks/`, `api/` (JWT auto-refresh + `X-Request-ID`), `storage/` (`expo-secure-store` + `expo-sqlite`), `theme/`, `types/`, dan `validation/`.
- `src/native/`: Konfigurasi channel notifikasi Android (`prtg-incidents`).

## Cara Menjalankan (Android Emulator & HP Fisik)

1. Salin `.env.example` menjadi `.env`:
   ```powershell
   Copy-Item .env.example .env
   ```
   - **Android Emulator:** gunakan default `EXPO_PUBLIC_API_URL=http://10.0.2.2:5001/api/v1`.
   - **HP Android Fisik via USB:** jalankan `adb reverse tcp:5001 tcp:5001` lalu set `EXPO_PUBLIC_API_URL=http://localhost:5001/api/v1` (atau gunakan IP LAN komputer backend).
2. Pastikan Shared Backend Flask + PostgreSQL berjalan pada port `5001`.
3. Jalankan Metro / Expo:
   ```powershell
   npm run start
   ```
   Untuk pengujian native FCM push (`expo-notifications`), tambahkan `google-services.json` dari Firebase Console di `apps/mobile/google-services.json` (otomatis terdeteksi oleh `app.config.ts`) lalu bangun Development Build (`npx expo prebuild` & `npx expo run:android`).

## Verifikasi Otomatis

```powershell
npm run typecheck
npm test
npx expo export --platform android
```
