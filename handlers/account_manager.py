"""
account_manager.py - Multi-Client Runner untuk telegram-chatbot.

Setiap account (Alya/Intan/Vanya/...) dijalankan sebagai Telethon client
terpisah dalam 1 proses asyncio. Tiap pesan masuk di-route ke handler yang
tahu account_id-nya, sehingga:
  - Persona/knowledge/media beda per account (diambil dari DB).
  - User tracking (stage/profile) terpisah per pasangan (account, user).
  - Bisa tambah account lewat DB tanpa ubah kode.

Flow pesan masuk per account:
  1. Cek perintah owner (.ai on/off/status, .revisi) — owner = sesi sendiri.
  2. Cek intent media (pap/video/vip_preview) -> kirim media self-destruct.
  3. Simpan ke history DB, update stage & profile user.
  4. Panggil ai_engine.generate_ai_reply(account, user, text) -> bubble chat.
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
from . import media_handler

logger = logging.getLogger("AccountManager")

# Map account_id -> Telethon client (diisi pas login, dipakai payment_monitor)
CLIENTS = {}

AUTO_REPLY = {}  # account_id -> bool (toggle per account)
MAX_HISTORY = 20


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
            await event.edit("🤖 [%s] status: %s | model: %s" % (account["name"], st, ai_engine.active_model))
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

    # --- User tracking: get-or-create di DB ---
    u = db.get_or_create_user(acc_id, user_id_tg, user_name, getattr(sender, "username", ""))
    user_db_id = u["id"]

    # Jeda random biar natural (10 - 30 detik)
    think = random.randint(10, 30)
    await asyncio.sleep(think)
    try:
        await event.client.send_read_acknowledge(event.chat_id, event.message, clear_mentions=True)
    except Exception as e:
        logger.warning("mark-read gagal: %s", e)

    # --- Media intent dulu ---
    intent = media_handler.detect_intent(message_text)
    if intent:
        sent = await media_handler.send_media_by_intent(
            event.client, event, message_text,
            account_id=acc_id, user_db_id=user_db_id,
            user_name=user_name, max_history=MAX_HISTORY,
        )
        if sent:
            db.add_message(acc_id, user_db_id, "user", message_text)
            db.evict_history(user_db_id, MAX_HISTORY)
            # Media request = user jelas interested, advance langsung
            db.advance_stage(user_db_id, "interested")
            return

    # --- Simpan user msg & enrich profil ---
    db.add_message(acc_id, user_db_id, "user", message_text)
    db.evict_history(user_db_id, MAX_HISTORY)
    # Stage detection sekarang ditangani oleh StageAgent di dalam pipeline
    user_tracker.enrich_from_message(user_db_id, message_text)

    # --- AI reply ---
    async with event.client.action(event.chat_id, "typing"):
        reply_text, bubbles = await ai_engine.generate_ai_reply(
            account, user_db_id, user_name, message_text, max_history=MAX_HISTORY
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

    if should_create_qris:
        await payment_handler.create_and_send_qris(
            client=event.client,
            event=event,
            account=account,
            user_db_id=user_db_id,
            tg_user_id=user_id_tg,
        )


async def _handle_revisi(account, event):
    parts = event.text.strip().split(maxsplit=1)
    new_response = parts[1] if len(parts) > 1 else ""
    if not new_response:
        await event.edit("❌ masukkan jawaban perbaikannya")
        return
    replied = await event.get_reply_message()
    if not replied:
        await event.edit("❌ reply pesan yg mau direvisi")
        return
    user_text = ""
    if not replied.out:
        user_text = replied.text
    if not user_text:
        # cari pesan user terakhir di history
        sender = await replied.get_sender()
        first_name = getattr(sender, "first_name", "") or ""
        username = getattr(sender, "username", "") or ""
        hist = db.get_history(db.get_or_create_user(account["id"], replied.sender_id, first_name, username)["id"], MAX_HISTORY)
        for m in reversed(hist):
            if m["role"] == "user":
                user_text = m["content"]
                break
    if not user_text:
        await event.edit("❌ gak nemu pesan pemicu")
        return
    db.add_correction(account["id"], user_text, new_response)
    await event.edit("✅ revisi disimpan: `%s`" % user_text)


async def run_account(account):
    """Jalankan 1 client account, blok sampai disconnect."""
    api_id = account.get("api_id") or int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = account.get("api_hash") or os.getenv("TELEGRAM_API_HASH", "")
    from core.utils import get_session_path
    session_path = get_session_path(account["session_file"])
    client = TelegramClient(session_path, api_id, api_hash)
    acc = dict(account)
    try:
        @client.on(events.NewMessage)
        async def _h(event):
            await handle_message(acc, event)

        await client.connect()
        if not await client.is_user_authorized():
            logger.error("❌ Account [%s] session '%s' belum login / tidak authorized! Silakan login dulu.", acc["name"], acc["session_file"])
            await client.disconnect()
            return None

        await client.start()
        me = await client.get_me()
        AUTO_REPLY[acc["id"]] = True
        CLIENTS[acc["id"]] = client
        logger.info("✅ Account [%s] login sebagai @%s", acc["name"], me.username or me.first_name)
        await client.run_until_disconnected()
    except Exception as e:
        logger.error("❌ Account [%s] gagal jalan: %s", acc["name"], e)
        # jangan crash seluruh bot kalau 1 account error
        return None
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


RUNNING_TASKS = {}  # account_id -> asyncio.Task


async def start_account_by_id(account_id: int):
    """Start 1 account dynamically without restarting the bot process."""
    account = db.get_account(account_id)
    if not account or not account.get("active"):
        logger.warning("Account #%s tidak aktif / tidak ditemukan.", account_id)
        return False

    task = RUNNING_TASKS.get(account_id)
    if task and not task.done():
        logger.info("Account #%s (%s) sudah berjalan.", account_id, account.get("name"))
        return True

    new_task = asyncio.create_task(run_account(account))
    RUNNING_TASKS[account_id] = new_task
    logger.info("🚀 Account [%s] (#%s) berhasil di-start otomatis tanpa restart!", account.get("name"), account_id)
    return True


async def stop_account_by_id(account_id: int):
    """Stop 1 account dynamically without restarting the bot process."""
    client = CLIENTS.pop(account_id, None)
    if client:
        try:
            await client.disconnect()
        except Exception as e:
            logger.warning("Gagal disconnect account #%s: %s", account_id, e)

    task = RUNNING_TASKS.pop(account_id, None)
    if task and not task.done():
        task.cancel()

    logger.info("🛑 Account #%s dihentikan secara otomatis.", account_id)
    return True


async def run_all():
    """Jalankan semua account aktif sebagai background task. Return map account_id->client."""
    accounts = db.list_accounts(active_only=True)
    if not accounts:
        logger.error("Gak ada account aktif di DB!")
        return CLIENTS
    logger.info("Menjalankan %d account...", len(accounts))
    for a in accounts:
        aid = a["id"]
        if aid not in RUNNING_TASKS or RUNNING_TASKS[aid].done():
            RUNNING_TASKS[aid] = asyncio.create_task(run_account(a))
    # Beri waktu sebentar biar account sempat login & isi CLIENTS
    await asyncio.sleep(3)
    logger.info("Account yang berhasil login: %d/%d", len(CLIENTS), len(accounts))
    return CLIENTS

