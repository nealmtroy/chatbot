# ARCHITECTURE — telegram-chatbot multi-account

```
                         ┌──────────────────────────────────────────┐
                         │              main.py (asyncio)            │
                         │  - init AI client (clients.py)            │
                         │  - init + migrate DB (db.py)              │
                         │  - run_all() + run_manage_bot()           │
                         │  - start_monitor(clients_map)  ◀─ PAYMENT │
                         └──────┬──────────────┬───────────┬─────────┘
                               │              │           │
              ┌────────────────▼───┐    ┌─────▼────────┐  │
              │  account_manager   │    │ manage_bot   │  │
              │  (userbot accounts)│    │ (owner BOT)   │  │
              │  handle_message()  │    │ /accounts ... │  │
              │  + TRIGGER QRIS ───┼──┐ │ /payconfirm   │  │
              └─────────┬──────────┘  │ └───────────────┘  │
                        │             │                     │
                        ▼             ▼                     │
              ┌──────────────────────────┐                 │
              │  payment_link.py         │  (bridge)       │
              │  reuse sociabuzz_client  │                 │
              │  create_qris() → bytes   │                 │
              └───────────┬──────────────┘                 │
                          │ (kirim QRIS lewat chat pribadi) │
                          ▼                                 │
              ┌──────────────────────────┐                 │
              │  payment_monitor.py      │  (poll loop)    │
              │  cek status SociaBuzz    │                 │
              │  paid → invite VIP       │                 │
              └───────────┬──────────────┘                 │
                          ▼                                 │
                    ┌─────────────────────┐                 │
                    │   db.py (payments)   │◀────────────────┘
                    │  accounts | users    │   update stage
                    │  messages | payments  │   + invite_link
                    │  corrections|media    │
                    └─────────────────────┘
```

## Modul baru (integrasi QRIS VIP)
| File | Tanggung jawab |
|------|----------------|
| `payment_link.py` | Bridge ke `sociabuzz-pay/sociabuzz_client.py`. Bikin QRIS lewat SociaBuzz API, return PNG bytes. |
| `payment_monitor.py` | Loop poll status tiap payment pending. Kalau lunas → bikin invite grup VIP (`ExportChatInviteRequest`), kirim ke user, naikkan stage → `member`, tambah `total_spent`. |
| `db.payments` | Tabel pembayaran (status pending/paid/expired). Anti-QRIS-dobel via `active_payment_for_user`. |

## Flow penjualan end-to-end (GOAL)
```
User DM Alya → ngobrol → tanya harga
  → user_tracker naik stage "asked_price"
  → account_manager._maybe_send_qris():
       payment_link.create_qris() → kirim PNG QRIS ke user (chat pribadi)
       db.add_payment(status=pending) + stage → payment_pending
User scan & bayar QRIS
  → payment_monitor tiap 10s cek SociaBuzz
  → status "paid" → ExportChatInviteRequest ke account.vip_chat_id
  → kirim invite ke user, stage → member, total_spent += 50000
```

## Config (.env chatbot)
```
SOCIABUZZ_USERNAME=   # username Sociabuzz tujuan (KOSONG = QRIS off)
SOCIABUZZ_PAY_PATH=   # path ke folder sociabuzz-pay (biar import client)
PAYMENT_POLL_INTERVAL=10
```
Per-account: `accounts.vip_chat_id` (grup tujuan) + `accounts.vip_price` (Rp).

## Cara jalanin
```
python main.py
```
Userbot + manage bot + payment monitor jalan di 1 proses. QRIS dikirim lewat
chat pribadi userbot (Alya/Intan), invite otomatis pas lunas.
