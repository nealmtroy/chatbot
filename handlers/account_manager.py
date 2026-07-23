"""
account_manager.py - Multi-Client Runner untuk telegram-chatbot.

Setiap account (Alya/Intan/Vanya/...) dijalankan sebagai Telethon client
terpisah dalam 1 proses asyncio. Tiap pesan masuk di-route ke handler yang
tahu account_id-nya:
  - User tracking (stage/profile) terpisah per pasangan (account, user).
  - Bisa tambah account lewat DB tanpa ubah kode.

Flow pesan masuk per account:
  1. Cek perintah owner (.ai on/off/status, .revisi) — owner = sesi sendiri.
  2. Simpan ke history DB, update stage & profile user.
  3. Panggil ai_engine.generate_ai_reply(account, user, text) -> bubble chat.
"""
import os
import re
import asyncio
import random
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from core.env_loader import load_env

load_env()

from core import db, user_tracker, ai_engine

logger = logging.getLogger("AccountManager")

# Map account_id -> Telethon client (diisi pas login, dipakai payment_monitor)
CLIENTS = {}

AUTO_REPLY = {}  # account_id -> bool (toggle per account)
MAX_HISTORY = 20

USER_PROCESSING = set()  # set of (acc_id, user_db_id)


async def handle_message(account, event):
    """Handler untuk 1 account. `account` = dict dari db.get_account()."""
    acc_id = account["id"]

    # --- Perintah owner (pesan keluar dari sesi sendiri) ---
    if event.out:
        text = event.text.strip().lower()
        if text == ".ai off":
            AUTO_REPLY[acc_id] = False
            await event.edit("🤖 auto-reply dimatikan (akun %s)" % account["name"])
            return
        if text == ".ai on":
            AUTO_REPLY[acc_id] = True
            await event.edit("🤖 auto-reply nyala (akun %s)" % account["name"])
            return
        if text == ".ai status":
            st = "Aktif ✅" if AUTO_REPLY.get(acc_id, True) else "Nonaktif ❌"
            await event.edit("🤖 [%s] status: %s" % (account["name"], st))
            return
        # .revisi (reply ke pesan user)
        if text.startswith(".revisi ") or text.startswith("/revisi "):
            await _handle_revisi(account, event)
            return
        return  # jangan auto-reply ke pesan kita sendiri

    if not AUTO_REPLY.get(acc_id, True):
        return
    if not event.is_private:
        return

    sender = await event.get_sender()
    if not sender or getattr(sender, "bot", False) or sender.id == 777000:
        return

    user_id_tg = sender.id
    user_name = sender.first_name or "Teman"
    message_text = (event.text or "").strip()
    if not message_text:
        return

    logger.info("[%s] pesan dari %s (tg=%s): %s", account["name"], user_name, user_id_tg, message_text)

    # --- User tracking: get-or-create di DB & simpan pesan segera ---
    u = db.get_or_create_user(acc_id, user_id_tg, user_name, getattr(sender, "username", ""))
    user_db_id = u["id"]
    user_key = (acc_id, user_db_id)

    db.add_message(acc_id, user_db_id, "user", message_text)
    db.evict_history(user_db_id, MAX_HISTORY)
    user_tracker.enrich_from_message(user_db_id, message_text)

    # Debounce: Jika user ini sedang dalam proses pemrosesan AI, tidak perlu spawn task duplikat
    if user_key in USER_PROCESSING:
        logger.info("[%s] user %s (tg=%s) sedang diproses AI, skip task duplikat.", account["name"], user_name, user_id_tg)
        return

    USER_PROCESSING.add(user_key)
    try:
        # Jeda 3-5 detik biar natural & menampung jika user mengirim beberapa pesan cepat beruntun
        think = random.randint(3, 5)
        await asyncio.sleep(think)
        try:
            await event.client.send_read_acknowledge(event.chat_id, event.message, clear_mentions=True)
        except Exception as e:
            logger.warning("mark-read gagal: %s", e)

        # Ambil pesan user paling mutakhir dari history
        latest_history = db.get_history(user_db_id, limit=1)
        latest_user_text = latest_history[-1]["content"] if latest_history else message_text

        # --- AI reply ---
        async with event.client.action(event.chat_id, "typing"):
            reply_text, bubbles = await ai_engine.generate_ai_reply(
                account, user_db_id, user_name, latest_user_text, max_history=MAX_HISTORY
            )

        if not reply_text:
            return

        should_create_qris = "[ACTION:CREATE_QRIS]" in reply_text.upper()

        # Clean any action tags from reply_text & bubbles if present
        if "[ACTION:" in reply_text.upper():
            reply_text = re.sub(r'\[ACTION:\s*[A-Z0-9_]+\]', '', reply_text, flags=re.IGNORECASE).strip()
            cleaned_bubbles = []
            for b in bubbles:
                clean_b_text = re.sub(r'\[ACTION:\s*[A-Z0-9_]+\]', '', b["text"], flags=re.IGNORECASE).strip()
                if clean_b_text:
                    b["text"] = clean_b_text
                    cleaned_bubbles.append(b)
            bubbles = cleaned_bubbles

        if reply_text:
            db.add_message(acc_id, user_db_id, "assistant", reply_text)
            db.evict_history(user_db_id, MAX_HISTORY)

            for bubble in bubbles:
                async with event.client.action(event.chat_id, "typing"):
                    await asyncio.sleep(bubble["delay"])
                try:
                    await event.respond(bubble["text"])
                    logger.info("[%s] reply ke %s: %s", account["name"], user_name, bubble["text"])
                except FloodWaitError as e:
                    logger.warning("FloodWait %ss", e.seconds)
                    await asyncio.sleep(e.seconds)
                    try:
                        await event.respond(bubble["text"])
                    except Exception as ex:
                        logger.error("gagal kirim bubble setelah floodwait: %s", ex)
                except Exception as ex:
                    logger.error("gagal kirim bubble: %s", ex)
    finally:
        USER_PROCESSING.discard(user_key)


async def _handle_revisi(account, event):
    """Fitur owner reply ke pesan user dengan '.revisi <balasan ideal>'."""
    reply_msg = await event.get_reply_message()
    if not reply_msg:
        await event.edit("⚠️ Harap gunakan '.revisi <balasan ideal>' dengan cara REPLY ke pesan user.")
        return

    user_text = (reply_msg.text or "").strip()
    full_cmd = event.text.strip()
    parts = full_cmd.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await event.edit("⚠️ Masukkan teks balasan ideal. Contoh: '.revisi halo kak, ada yang bisa dibantu?'")
        return

    assistant_text = parts[1].strip()
    if not user_text:
        await event.edit("⚠️ Pesan user yang di-reply tidak memiliki teks.")
        return

    acc_id = account["id"]
    ai_engine.save_correction(acc_id, user_text, assistant_text)
    await event.edit("✅ Koreksi tersimpan untuk [%s]!\n📌 User: \"%s\"\n📌 Ideal: \"%s\"" % (account["name"], user_text, assistant_text))


async def start_account(account):
    session_name = account.get("session_name") or account.get("session_file")
    api_id = account.get("api_id") or os.getenv("TELEGRAM_API_ID")
    api_hash = account.get("api_hash") or os.getenv("TELEGRAM_API_HASH")

    if not session_name or not api_id or not api_hash:
        logger.error("Account %s: data session/api_id/api_hash tidak lengkap.", account.get("name"))
        return None

    sessions_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    session_path = os.path.join(sessions_dir, session_name if session_name.endswith(".session") else session_name + ".session")

    client = TelegramClient(session_path, int(api_id), api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("Account %s (%s): belum terotorisasi.", account["name"], session_name)
            return None

        acc_id = account["id"]
        CLIENTS[acc_id] = client
        AUTO_REPLY[acc_id] = True

        @client.on(events.NewMessage)
        def _on_msg(evt):
            asyncio.create_task(handle_message(account, evt))

        logger.info("✅ Account %s (%s) aktif & listening.", account["name"], session_name)
        return client
    except Exception as e:
        logger.error("Account %s gagal start: %s", account.get("name"), e)
        return None


async def run_all():
    accounts = db.list_accounts(active_only=True)
    if not accounts:
        logger.warning("Tidak ada akun aktif di DB.")
        return []

    started = []
    for acc in accounts:
        cl = await start_account(acc)
        if cl:
            started.append(cl)
    return started
