# Panduan AI Agent — PRTG School Platform

> **Wajib dibaca:** [`docs/PRTG_SYSTEM_ARCHITECTURE_AI_CONTEXT.md`](docs/PRTG_SYSTEM_ARCHITECTURE_AI_CONTEXT.md).
> Ini adalah **arsitektur TARGET** dari SAD-PRTG-001 v0.2, 10 November 2026, **bukan hasil audit implementasi repository**.

## Tujuan

Pertahankan website monitoring existing dan kembangkan backend bersama untuk PRTG School Mobile (React Native + TypeScript, Android/iOS). Backend mengatur monitoring terstandardisasi, incident, assignment, response, supervisor verification, notification, RBAC, audit, dan API. PRTG tetap source of truth untuk network telemetry.

## Instruksi mutlak

1. **Audit source code sebelum mengubahnya.** Jangan mengasumsikan framework, database, route, atau struktur folder sesuai PDF.
2. **Jangan akses PRTG langsung dari mobile/browser** dan jangan ekspos credential/secret ke client.
3. **Jangan campur network status, incident status, dan sync status.** `stale`/`unknown` tidak berarti `online`.
4. **Gunakan business logic/backend bersama** untuk website dan mobile; pertahankan perilaku website existing melalui regression tests.
5. **Periksa permission dan scope di server**: lima role tetap (Admin, Supervisor Utama, Supervisor Wilayah, Koordinator Teknisi, Teknisi). **Admin tidak memiliki `incidents.verify`**; verification hanya supervisor yang berwenang.
6. **Satu rangkaian gangguan/sekolah = satu incident utama aktif**, dengan banyak sensor terkait; jangan menggabungkan gangguan baru dengan incident yang sudah resolved.
7. **Technician submit → supervisor review/approve → PRTG recheck sensor wajib → resolved HANYA jika semuanya valid, segar, normal.** Jika unknown/stale/timeout/policy missing, jangan otomatis tutup.
8. **Offline hanya untuk cache dan draft.** Operasi resmi (assignment, acknowledgement status, final submission, approval, closure) harus online. Gunakan client operation ID, idempotency, conflict response.
9. **Pertahankan histori.** Audit assignment/reassignment, response, verification, perubahan akses, dan recheck; jangan overwrite/delete historis.
10. **Jangan meluaskan MVP** ke AI diagnosis, GPS, chat, SLA kompleks, custom permissions, atau analytics lanjut tanpa persetujuan.
11. **Tidak boleh mengarang SLO atau interval polling.** Tentukan setelah baseline dan audit PRTG API.
12. **Ikuti framework existing.** Contoh `/api/v1`, tree folder, model konseptual pada dokumen adalah TARGET, bukan kepastian implementasi.

## Saat menangani tugas coding

- Inspeksi route/model/service/adapter/test yang relevan, lalu bedakan **fakta as-is** vs **rancangan target** vs **keputusan terbuka**.
- Usulkan perubahan terkecil; uji authorization (terutama larangan Admin verify), stale/unknown, dedup incident, idempotency offline, recovery policy, dan regresi website sesuai area yang disentuh.
- Laporkan file diubah, tes, migration/rollback, risiko, dan hal yang membutuhkan keputusan manusia.
- Jika belum ada PRD v0.2 atau source terkait, nyatakan ketidakpastian, jangan mengarang detail teknis final.

## Referensi lengkap

Lihat Markdown konteks lengkap untuk diagram Mermaid, contoh JSON, endpoint konseptual, model data, tabel role, alur workflow, M0–M7, ADR, dan daftar keputusan terbuka.
