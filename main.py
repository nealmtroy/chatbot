"""
main.py - Single Entry Point untuk VIP Automation System.

Menjalankan seluruh komponen sebagai 1 sistem utuh:
  1. Shared AI Client (Groq/OpenRouter)
  2. Database Persistence & Migrations
  3. Multi-Account AI Auto-Responder Userbots (account_manager.run_all)
  4. VIP Payment Bot & SociaBuzz QRIS Engine (vip_bot.start_bot)
  5. Owner/Admin Management Bot (manage_bot.run_manage_bot)

Cara Jalanin:
    python main.py
"""

import sys
import os
import asyncio
import logging

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from core.env_loader import load_env
load_env()

from core import db, clients
from handlers import account_manager, manage_bot
import vip_bot

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger("VIPAutomationSystem")


async def main():
    logger.info("==========================================")
    logger.info("🚀 VIP AUTOMATION SYSTEM BOOTING...")
    logger.info("==========================================")

    # 1. Inisialisasi AI Client (Groq/OpenRouter)
    clients.init()
    if clients.client is None:
        logger.warning("AI client tidak ke-init (cek API key di .env). Auto-responder AI tidak aktif.")

    # 2. Inisialisasi & Migrasi Database
    db.init_db()
    db.migrate_from_json_legacy()

    # 3. Jalankan Multi-Account AI Userbots
    logger.info("Starting Multi-Account AI Userbots...")
    accounts = await account_manager.run_all()

    # 4. Jalankan VIP Payment Bot (SociaBuzz QRIS Engine & Supabase Sync)
    logger.info("Starting VIP Payment Bot Engine...")
    asyncio.create_task(vip_bot.start_bot())

    # 5. Jalankan Management Bot (Owner/Admin Controller)
    logger.info("Starting Owner/Admin Management Bot...")
    await manage_bot.start_manage_bot()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("VIP Automation System dihentikan oleh user.")
    except Exception as e:
        logger.exception("Fatal error pada VIP Automation System: %s", e)
