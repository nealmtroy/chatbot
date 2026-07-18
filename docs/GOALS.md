# GOALS — telegram-chatbot (multi-account scalibility)

> Status: **IMPLEMENTED (core)** — semua pilar di bawah sudah jadi kode jalan.
> Diperbarui: 2026-07-17

## Visi
telegram-chatbot bukan lagi 1 userbot pribadi, tapi **platform multi-persona
yang bisa dijalankan banyak akun sekaligus**, tiap akun punya memory per-user,
dan dikelola lewat Telegram bot (gak perlu SSH tiap mau tambah account).

Target jualan tetap: **Grup VIP (Rp50k) + VCS (Rp100k)**.

## Goals Utama (yg diminta user)
1. **Skalabilitas multi-account**
   - Alya, Intan, Vanya, dst jalan barengan dalam 1 proses.
   - Tambah account = insert DB, gak ubah kode.
2. **Per-account memory / user tracking**
   - Tiap pasangan (account, user) punya profil: nama, umur, kota.
   - Punya "stage" funnel penjualan biar AI tau harus push ke mana.
   - History chat persisten lintas restart.
3. **Dipakai orang lain (bukan cuma pribadi)**
   - Owner kelola account lewat Telegram bot: add account, set profil,
     lihat stats, konfirmasi bayar, tambah media.
   - (Auth = OWNER_ID di .env, gak ada web panel.)

## Pilar Arsitektur (sudah dibangun)
- **P1 Account Registry** — tabel `accounts` di SQLite.
- **P2 User Tracking / CRM** — tabel `users` (stage + profil), fungsi di `user_tracker.py`.
- **P3 Persistent DB** — `db.py` (SQLite, WAL, thread-safe). Ganti JSON flat.
- **P4 Multi-Client Runner** — `account_manager.py` jalanin N Telethon client async.
- **P5 Telegram Manage Bot** — `manage_bot.py` (python-telegram-bot).

## Sales Funnel (STAGES)
```
new → greeted → interested → asked_price → payment_pending
    → member → vcs_offered → vcs_booked
lost (ghost/scam/blokir)
```
Naik stage otomatis dari kata kunci chat (rule-based, di `user_tracker.STAGE_RULES`).
Stage cuma maju, gak mundur (kecuali manual via manage bot).

## Metrik Sukses (cuan)
- Tiap account track `total_spent` per user → mudah hitung cuan harian.
- `/stats <id>` di manage bot kasih breakdown stage + total Rp.

## Roadmap Lanjutan (belum dibuat, opsional)
- [ ] Auto QRIS generate (integrasi Xendit/Tripay) → `/payconfirm` jadi otomatis.
- [ ] Daily report ke owner (cuan + user baru) via manage bot.
- [ ] Migrasi SQLite → PostgreSQL kalau user ramai (>1 juta pesan).
- [ ] Per-account persona file beda (prompts/persona_intan.txt, dll).
- [ ] Anti-ban: rotasi session, delay adaptif, whitelist nomor.
- [ ] Webhook payment confirmation (biar gak manual `/payconfirm`).
