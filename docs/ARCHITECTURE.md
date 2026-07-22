# ARCHITECTURE — telegram-chatbot multi-account

```
                         ┌──────────────────────────────────────────┐
                         │              main.py (asyncio)            │
                         │  - init AI client (clients.py)            │
                         │  - init + migrate DB (db.py)              │
                         │  - run_all() + run_manage_bot()           │
                         └──────┬───────────────────────┬───────────┘
                                │                       │
               ┌────────────────▼───┐            ┌──────▼───────┐
               │  account_manager   │            │ manage_bot   │
               │  (userbot accounts)│            │ (owner BOT)  │
               │  handle_message()  │            │ /accounts... │
               └─────────┬──────────┘            └──────────────┘
                         │
                         ▼
               ┌─────────────────────┐
               │   db.py             │
               │  accounts | users   │
               │  messages | payments│
               │  corrections|media  │
               └─────────────────────┘
```

## Modul Pembayaran Private API
- Logika pembuatan QRIS & polling status SociaBuzz sebelumnya sudah dilepas.
- Mekanisme pembayaran akan dipanggil via Private API eksternal.

## Cara jalanin
```
python main.py
```
Userbot + manage bot jalan dalam 1 proses asyncio.
