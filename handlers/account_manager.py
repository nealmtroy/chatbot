import os
import re
import asyncio
import random
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from core import db, clients

logger = logging.getLogger("AccountManager")

# Map account_id -> Telethon client
CLIENTS = {}
AUTO_REPLY = {}  # account_id -> bool
USER_PROCESSING = set()  # set of (acc_id, chat_id)


async def handle_message(account, event):
    """Handler for 1 userbot account. `account` is a dict from db.list_accounts()."""
    acc_id = account["id"]

    # --- Owner commands (messages sent by the session user itself) ---
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
        return  # Do not auto-reply to our own outgoing messages

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

    user_key = (acc_id, event.chat_id)
    if user_key in USER_PROCESSING:
        logger.info("[%s] user %s (tg=%s) sedang diproses AI, skip task duplikat.", account["name"], user_name, user_id_tg)
        return

    USER_PROCESSING.add(user_key)
    try:
        # Natural typing delay (3-5 seconds)
        think = random.randint(3, 5)
        await asyncio.sleep(think)
        try:
            await event.client.send_read_acknowledge(event.chat_id, event.message, clear_mentions=True)
        except Exception as e:
            logger.warning("mark-read gagal: %s", e)

        # Get conversation history directly from Telegram (last 6 messages)
        tg_history = await event.client.get_messages(event.chat_id, limit=6)
        conversation_history = []
        
        # Exclude the current incoming message which is the first one in the list (index 0)
        for msg in reversed(tg_history[1:]):
            role = "assistant" if msg.out else "user"
            content = msg.text or ""
            if content:
                conversation_history.append({"role": role, "content": content})

        # --- Generate Response directly from DigitalTwinAgent ---
        if clients.digital_twin_agent is None:
            logger.error("DigitalTwinAgent is not initialized!")
            return

        async with event.client.action(event.chat_id, "typing"):
            ai_response = await asyncio.to_thread(
                clients.digital_twin_agent.generate_response,
                user_input=message_text,
                conversation_history=conversation_history
            )

        if not ai_response:
            logger.warning(f"Gagal mendapatkan respon dari DigitalTwinAgent untuk user {user_name}")
            return

        # Format lines into bubbles
        raw_lines = [l.strip() for l in ai_response.split("\n") if l.strip()]
        cleaned_lines = []
        for line in raw_lines:
            if line.lower().startswith("reply:"):
                line = line[6:].strip()
            if line:
                cleaned_lines.append(line)

        if not cleaned_lines:
            cleaned_lines = [ai_response]

        # Send as message bubbles with natural delays
        for line in cleaned_lines:
            delay = round(min(max(len(line) * 0.04, 0.4), 1.5), 1)
            async with event.client.action(event.chat_id, "typing"):
                await asyncio.sleep(delay)
            try:
                await event.respond(line)
                logger.info("[%s] reply ke %s: %s", account["name"], user_name, line)
            except FloodWaitError as e:
                logger.warning("FloodWait %ss", e.seconds)
                await asyncio.sleep(e.seconds)
                try:
                    await event.respond(line)
                except Exception as ex:
                    logger.error("gagal kirim bubble setelah floodwait: %s", ex)
            except Exception as ex:
                logger.error("gagal kirim bubble: %s", ex)
    finally:
        USER_PROCESSING.discard(user_key)


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
        async def _on_msg(evt):
            await handle_message(account, evt)

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
