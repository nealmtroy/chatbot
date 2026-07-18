"""
main.py - Entry point telegram-chatbot multi-account.

Menjalankan:
  1. Semua account userbot (Alya/Intan/Vanya/...) via account_manager.run_all()
  2. Manage bot (Telegram bot untuk owner) via manage_bot.run_manage_bot()
  3. Payment monitor (poll QRIS VIP, kirim invite grup saat lunas) via payment_monitor

Semua berjalan dalam 1 proses asyncio. DB SQLite (db.py) jadi sumber data tunggal
untuk account registry, user tracking, history, media, corrections, payments.

Cara jalanin:
    python main.py
"""
import asyncio
import logging
from env_loader import load_env

load_env()

import db
import clients
import account_manager
import manage_bot
import payment_monitor

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("Main")


async def main():
    # 0. Init AI client (Groq/OpenRouter) — satu shared client
    clients.init()
    if clients.client is None:
        logger.error("AI client gak ke-init (cek API key di .env). Userbot tetap jalan tapi gak bisa reply AI.")

    # 1. Init + migrasi DB
    db.init_db()
    db.migrate_from_json_legacy()

    # 2. Jalankan userbot accounts (return map account_id -> client)
    logger.info("=== telegram-chatbot multi-account START ===")
    accounts = await account_manager.run_all()

    # 3. Manage bot + payment monitor (monitor butuh clients_map)
    await asyncio.gather(
        manage_bot.run_manage_bot(),
        payment_monitor.start_monitor(accounts),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Dihentikan oleh user.")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
