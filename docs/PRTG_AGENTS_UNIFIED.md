# AGENTS.md — PRTG School Platform (PRD + SAD v0.2)

> **BACA DAHULU:**
> - `docs/PRTG_PRD_AI_AGENT_CONTEXT.md` — kebutuhan produk, 27 FR, 12 NFR, 14 AC, 14 TC, lima role, kebijakan MVP.
> - `docs/PRTG_SYSTEM_ARCHITECTURE_AI_CONTEXT.md` — arsitektur target, domain, API konseptual, integrasi, keamanan, refactoring, deployment.
>
> Keduanya **DRAFT v0.2 (10 November 2026)** dan **belum membuktikan kondisi repository existing**. Audit dulu sebelum melakukan perubahan.

## Kontrak Kerja

1. **Baca requirement yang terkait** dan pertahankan label `FR`, `NFR`, `AC`, `TC`. PRD menjelaskan *apa dan mengapa*, SAD menjelaskan *rancangan bagaimana*; kode hasil audit menunjukkan *apa yang sudah ada*.
2. **Jangan mengarang kondisi existing** (framework, DB, route, API, integration, folder, secret, deployment, fitur). Jika belum ada bukti, tulis `unverified`.
3. **PRTG satu-satunya sumber kebenaran telemetry.** Mobile/browser tidak mengakses PRTG secara langsung. `network_status` terpisah dari `incident_status` dan offline `sync_status`.
4. **Jaga website existing**, gunakan shared backend, refactoring kecil, baseline regression tests, rollback/migration yang aman.
5. **RBAC server-side** berdasarkan role, permission, region scope, account status dan assignment. Lima role tetap: Admin, Supervisor Utama, Supervisor Wilayah, Koordinator Teknisi, Teknisi. **Admin dilarang approve/reject incident**.
6. **Incident correlation:** satu incident utama aktif per rangkaian gangguan pada sekolah, sensor terkait dalam relasi; hindari duplikasi dan preserve history.
7. **Assignment:** region → active+duty → least active workload; tie-breaker belum diputuskan. Manual override hanya role sesuai scope dan wajib simpan alasan/history.
8. **Closure:** technician submit → supervisor review/approve → PRTG recheck *seluruh sensor wajib* → resolved hanya jika normal dan valid. Unknown/stale/API failed/policy missing bukan alasan auto-close.
9. **Offline terbatas:** cache dan draft respons saja; validasi online untuk operasi server; ID unik/idempotency, konflik tak boleh diam-diam menimpa server.
10. **Push:** route berdasarkan scope dan assignment, deduplicate, simpan log, jangan mengandalkan push sebagai sumber data resmi.
11. **Jangan memperluas MVP** ke AI diagnosis, GPS, chat, SLA kompleks, analitik advanced, custom permission, ekspor mobile kompleks.
12. **Jangan menentukan angka performance, interval PRTG, OS minimum, Expo/bare, DB/storage tanpa audit/baseline.**

## Perbedaan istilah yang wajib di-review

- PRD menyebut `awaiting_recovery`, SAD menyebut `pending_recovery`: belum ada keputusan canonical enum.
- PRD menyebut `reopened / kebijakan incident baru`, SAD mengutamakan incident baru sesudah resolved: minta policy final.
- Izin respons/submit supervisor tertentu bersifat **opsional dalam baseline PRD**, jangan aktifkan tanpa spesifikasi RBAC.

## Format setiap hasil kerja

Sebutkan requirement terkait, temuan source existing, perubahan file, alasan, tes yang benar-benar dijalankan, risiko migrasi/regresi dan rollback, hasil validasi acceptance, serta keputusan yang masih perlu manusia. Jangan klaim test lulus jika belum dieksekusi.
