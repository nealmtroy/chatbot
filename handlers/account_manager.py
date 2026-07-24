import os
import re
import io
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
USER_BUFFERS = {}  # key: (acc_id, chat_id), val: {"messages": [...], "first_seen": float}
USER_TASKS = {}    # key: (acc_id, chat_id), val: asyncio.Task


def to_math_bold_italic(text: str) -> str:
    out = []
    for c in text:
        o = ord(c)
        if 65 <= o <= 90:  # A-Z
            out.append(chr(o - 65 + 0x1D4D0))
        elif 97 <= o <= 122:  # a-z
            out.append(chr(o - 97 + 0x1D4EA))
        else:
            out.append(c)
    return "".join(out)


def parse_amount_from_text(text: str, default_amount: int = 100000) -> int:
    text_clean = text.lower().replace(".", "").replace(",", "")
    # Match patterns like "100k", "50k", "100.000", "50.000", "100ribu", "50 ribu"
    match_k = re.search(r'(\d+)\s*(k|ribu|rb)', text_clean)
    if match_k:
        val = int(match_k.group(1))
        if val < 1000:
            return val * 1000
        return val
    # Direct number match
    match_num = re.search(r'(\d{4,6})', text_clean)
    if match_num:
        return int(match_num.group(1))
    return default_amount


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

    # Add message to the user buffer
    import time
    now = time.time()
    if user_key not in USER_BUFFERS:
        USER_BUFFERS[user_key] = {
            "messages": [message_text],
            "first_seen": now
        }
    else:
        USER_BUFFERS[user_key]["messages"].append(message_text)

    # Manage debounce task
    first_seen = USER_BUFFERS[user_key]["first_seen"]
    if now - first_seen < 8.0:
        if user_key in USER_TASKS:
            USER_TASKS[user_key].cancel()
        task = asyncio.create_task(process_user_buffer(account, event, user_key, user_name, sender))
        USER_TASKS[user_key] = task
    else:
        if user_key not in USER_TASKS:
            task = asyncio.create_task(process_user_buffer(account, event, user_key, user_name, sender))
            USER_TASKS[user_key] = task


async def process_user_buffer(account, event, user_key, user_name, sender):
    acc_id = account["id"]
    user_id_tg = sender.id
    
    try:
        # Wait for the user to finish sending messages (2.0s debounce)
        await asyncio.sleep(2.0)
        
        # Pop the buffer and clean task reference
        buffer_data = USER_BUFFERS.pop(user_key, None)
        USER_TASKS.pop(user_key, None)
        
        if not buffer_data or not buffer_data["messages"]:
            return
            
        # Combine all buffered messages
        combined_message = "\n".join(buffer_data["messages"])
        logger.info("[%s] Aggregated messages for %s (tg=%s): %r", account["name"], user_name, user_id_tg, combined_message)

        # Mark as processing to prevent overlapping AI runs
        USER_PROCESSING.add(user_key)
        try:
            # Natural typing delay (1-3 seconds since we already waited 2 seconds)
            think = random.randint(1, 3)
            await asyncio.sleep(think)
            
            try:
                await event.client.send_read_acknowledge(event.chat_id, event.message, clear_mentions=True)
            except Exception as e:
                logger.warning("mark-read gagal: %s", e)

            # Get conversation history directly from Telegram (last 40 messages)
            tg_history = await event.client.get_messages(event.chat_id, limit=40)
            conversation_history = []
            
            # Exclude the newly buffered messages from history (since they are treated as user_input)
            num_buffered = len(buffer_data["messages"])
            for msg in reversed(tg_history[num_buffered:]):
                role = "assistant" if msg.out else "user"
                content = (msg.text or "").strip()
                if not content:
                    continue
                
                # Skip any assistant message that contains system/rule keywords to prevent repeating leaked rules
                if role == "assistant" and ("ATURAN KETAT" in content or "JANGAN PERNAH" in content or "Tirulah alur" in content):
                    continue
                    
                conversation_history.append({"role": role, "content": content})

            # Get latest payment status to make the AI context-aware of payment state
            system_instruction = None
            try:
                from vip_bot.config import load_config
                from vip_bot.db_store import PaymentStore
                
                vip_config = load_config()
                payment_store = PaymentStore(vip_config)
                latest_payment = await asyncio.to_thread(payment_store.latest_payment_for_user, user_id_tg)
                
                if latest_payment:
                    status = latest_payment.get("status")
                    amount_str = f"Rp {int(latest_payment.get('amount') or 0):,}".replace(",", ".")
                    pkg_name = latest_payment.get("package_name") or "VIP"
                    
                    if status == "pending":
                        system_instruction = (
                            f"[SYSTEM INFO: Status pembayaran user saat ini adalah PENDING / BELUM DIBAYAR "
                            f"untuk nominal {amount_str} ({pkg_name}). "
                            f"Jika user bertanya 'sudah masuk belum', 'sudah bayar', atau mengaku sudah membayar padahal belum terdeteksi, "
                            f"jelaskan dengan santai/casual bahwa pembayarannya masih belum terdeteksi oleh sistem "
                            f"dan minta mereka menunggu sebentar atau pastikan nominal transfer sudah pas. "
                            f"Jika user meminta dikirimkan ulang QRIS-nya atau ingin dibuatkan QRIS baru, "
                            f"kamu harus menyisipkan tag [qris] di akhir pesan balasanmu (contoh: 'ini qris nya kakk [qris]').]"
                        )
                    elif status in {"paid", "processing_paid", "processing_delivery"}:
                        system_instruction = (
                            f"[SYSTEM INFO: Status pembayaran user saat ini adalah PAID / SUDAH DIBAYAR LUNAS "
                            f"untuk nominal {amount_str} ({pkg_name}). "
                            f"Jika user menanyakan status atau konfirmasi, beri tahu dengan senang/flirty bahwa pembayaran "
                            f"sudah sukses terverifikasi! (Jika ini VCS, katakan bahwa kamu akan segera hubungi/panggil mereka).]"
                        )
                    elif status in {"timeout", "expired"}:
                        system_instruction = (
                            f"[SYSTEM INFO: Status pembayaran user saat ini adalah EXPIRED / KEDALUWARSA. "
                            f"Jika user menanyakan status pembayaran atau ingin bayar, beri tahu mereka bahwa QRIS sebelumnya "
                            f"sudah kedaluwarsa/mati, dan minta mereka bilang jika ingin dikirimkan QRIS baru lagi. "
                            f"Jika user setuju meminta/mengirimkan QRIS baru, kamu harus menyisipkan tag [qris] di akhir pesan balasanmu (contoh: 'ini qris baru nya kakk [qris]').]"
                        )
                else:
                    # No payment history at all
                    system_instruction = (
                        "[SYSTEM INFO: User saat ini BELUM PERNAH membuat tagihan atau QRIS sama sekali. "
                        "Jika user mengaku sudah membayar atau mengonfirmasi pembayaran, "
                        "katakan dengan santai/casual bahwa mereka belum minta QRIS-nya sama sekali "
                        "dan tawarkan untuk mengirimkan QRIS jika mereka mau melakukan pembayaran. "
                        "Jika user setuju atau memilih paket pembayaran (seperti VCS atau VIP) dan ingin dikirimkan QRIS, "
                        "kamu harus menyisipkan tag [qris] di akhir pesan balasanmu (contoh: 'ini qris nya kakk [qris]').]"
                    )
            except Exception as e:
                logger.warning("Gagal menyematkan status pembayaran ke prompt: %s", e)

            # --- Generate Response directly from DigitalTwinAgent ---
            if clients.digital_twin_agent is None:
                logger.error("DigitalTwinAgent is not initialized!")
                return

            ai_response = None
            try:
                async with event.client.action(event.chat_id, "typing"):
                    ai_response = await asyncio.to_thread(
                        clients.digital_twin_agent.generate_response,
                        user_input=combined_message,
                        conversation_history=conversation_history,
                        system_instruction=system_instruction,
                        account=account
                    )
            except Exception as e:
                logger.error("Gagal generate_response dari DigitalTwinAgent untuk user %s: %s", user_name, e)
                return

            if not ai_response:
                logger.warning(f"Gagal mendapatkan respon dari DigitalTwinAgent untuk user {user_name}")
                return

            # Format lines into bubbles (keep pricelist intact as a single bubble)
            is_pricelist = any(kw in ai_response.lower() for kw in ["pricelist", "daftar harga", "vcs —", "vcs -", "vip group"])
            if is_pricelist:
                overrides = {}
                if account:
                    if account.get("name"):
                        overrides["bot_name"] = account["name"]
                    if account.get("city"):
                        overrides["origin"] = account["city"]
                    if account.get("age"):
                        overrides["age"] = f"{account['age']} thn"
                    if account.get("vip_price"):
                        overrides["vip_price"] = f"{int(account['vip_price']) // 1000}K"
                pricelist_text = clients.digital_twin_agent.template_mgr.get_pricelist_template(overrides)
                bot_name_config = overrides.get("bot_name", clients.digital_twin_agent.template_mgr.config.get("bot_name", "Intan"))
                active_bot_name = account.get("name", "Intan").capitalize()
                stylized_active_name = to_math_bold_italic(active_bot_name)
                
                if bot_name_config.lower() == "intan" and active_bot_name.lower() != "intan":
                    pricelist_text = re.sub(re.escape(bot_name_config), stylized_active_name, pricelist_text, flags=re.IGNORECASE)
                    pricelist_text = re.sub(r'Intan', stylized_active_name, pricelist_text, flags=re.IGNORECASE)
                    
                raw_lines = [pricelist_text.strip()]
            else:
                raw_lines = [l.strip() for l in ai_response.split("\n") if l.strip()]

            # Check if any line in the response contains the QRIS trigger pattern
            has_qris_trigger = False
            qris_index = -1
            
            qris_pattern = re.compile(r'[\(\[][^\)\]]*qris[^\)\]]*[\)\]]', re.IGNORECASE)
            
            cleaned_lines = []
            for idx, line in enumerate(raw_lines):
                if line.lower().startswith("reply:"):
                    line = line[6:].strip()
                
                if line:
                    match = qris_pattern.search(line)
                    if match:
                        has_qris_trigger = True
                        qris_index = idx
                        line = qris_pattern.sub("", line).strip()
                        line = re.sub(r'\s+', ' ', line)
                    
                    if line:
                        cleaned_lines.append(line)
                    else:
                        if qris_index == -1 or qris_index == idx:
                            qris_index = len(cleaned_lines)

            if not cleaned_lines and not has_qris_trigger:
                cleaned_lines = [ai_response]

            # Handle QRIS generation if triggered
            if has_qris_trigger:
                combined_text = " ".join(cleaned_lines) + " " + combined_message
                amount = parse_amount_from_text(combined_text, default_amount=100000)
                
                is_vcs = any("vcs" in l.lower() for l in cleaned_lines) or "vcs" in combined_message.lower()
                note_prefix = "VCS" if is_vcs else "VIP"
                
                logger.info("Triggered QRIS creation for user tg=%s, amount=%s, type=%s", user_id_tg, amount, note_prefix)
                
                from vip_bot.config import load_config
                from vip_bot.db_store import PaymentStore
                from vip_bot.helpers import create_qris_with_retries_sync, public_invoice_id, SociaBuzzError
                
                vip_config = load_config()
                payment_store = PaymentStore(vip_config)
                
                try:
                    (
                        _session,
                        buyer_name,
                        buyer_email,
                        order_id,
                        payment_url,
                        qris,
                        qr_bytes,
                        checkout_amount,
                    ) = await asyncio.to_thread(
                        create_qris_with_retries_sync,
                        vip_config,
                        sender,
                        amount,
                        note_prefix
                    )
                    
                    socia_invoice_id = qris.get("inv_id")
                    if not socia_invoice_id:
                        raise SociaBuzzError(f"QRIS response missing inv_id: {qris}")
                    
                    buyer_invoice_id = public_invoice_id()
                    
                    # Send bubbles before QRIS
                    send_limit = min(qris_index, len(cleaned_lines)) if qris_index >= 0 else len(cleaned_lines)
                    for i in range(send_limit):
                        line = cleaned_lines[i]
                        delay = round(min(max(len(line) * 0.04, 0.4), 1.5), 1)
                        async with event.client.action(event.chat_id, "typing"):
                            await asyncio.sleep(delay)
                        await event.respond(line)
                        logger.info("[%s] reply ke %s: %s", account["name"], user_name, line)
                    
                    # Send actual QRIS image file
                    qr_file = io.BytesIO(qr_bytes)
                    qr_file.name = f"{buyer_invoice_id}.png"
                    
                    logger.info("[%s] sending QRIS file to %s...", account["name"], user_name)
                    
                    package = {"code": note_prefix.lower(), "name": note_prefix, "amount": checkout_amount}
                    
                    invoice_msg = await event.client.send_file(
                        event.chat_id,
                        file=qr_file,
                        caption=None
                    )
                    
                    await asyncio.to_thread(
                        payment_store.create_payment,
                        user=sender,
                        public_invoice_id=buyer_invoice_id,
                        order_id=order_id,
                        payment_url=payment_url,
                        inv_id=socia_invoice_id,
                        amount=checkout_amount,
                        buyer_name=buyer_name,
                        buyer_email=buyer_email,
                        qris_data=qris,
                        qris_chat_id=event.chat_id,
                        qris_message_id=invoice_msg.id,
                        package=package
                    )
                    
                    try:
                        from vip_bot.helpers import send_log, telegram_user_link, format_custom_qris_expiry, format_rupiah
                        expires_countdown = qris.get("data", {}).get("countdown") or ""
                        await send_log(
                            event.client,
                            vip_config,
                            payment_store,
                            (
                                "<b>AI CREATED QRIS</b>\n\n"
                                "<blockquote>"
                                f"<b>Userbot Account</b>: {account['name']}\n"
                                f"<b>User</b>: {telegram_user_link(sender)} (<code>{user_id_tg}</code>)\n"
                                f"<b>Invoice</b>: <code>{buyer_invoice_id}</code>\n"
                                f"<b>Package</b>: {note_prefix}\n"
                                f"<b>Amount</b>: {format_rupiah(checkout_amount)}\n"
                                f"<b>Expires</b>: {format_custom_qris_expiry(expires_countdown)}"
                                "</blockquote>"
                            )
                        )
                    except Exception as log_exc:
                        logger.warning("Gagal mengirim log QRIS ke channel: %s", log_exc)
                    
                    # Send bubbles after QRIS
                    if qris_index >= 0 and qris_index < len(cleaned_lines):
                        for i in range(qris_index, len(cleaned_lines)):
                            line = cleaned_lines[i]
                            delay = round(min(max(len(line) * 0.04, 0.4), 1.5), 1)
                            async with event.client.action(event.chat_id, "typing"):
                                await asyncio.sleep(delay)
                            await event.respond(line)
                            logger.info("[%s] reply ke %s: %s", account["name"], user_name, line)
                        
                except Exception as exc:
                    logger.exception("Failed to generate and send QRIS for user tg=%s: %s", user_id_tg, exc)
                    for line in cleaned_lines:
                        delay = round(min(max(len(line) * 0.04, 0.4), 1.5), 1)
                        async with event.client.action(event.chat_id, "typing"):
                            await asyncio.sleep(delay)
                        await event.respond(line)
            else:
                # Send standard message bubbles
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
        except Exception as e:
            logger.exception("Exception in process_user_buffer for account %s: %s", account.get("name"), e)
        finally:
            USER_PROCESSING.discard(user_key)
            
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception("Unexpected error in process_user_buffer: %s", e)


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

    # Clean update_state on start to prevent PersistentTimestampOutdatedError infinite loop
    if os.path.exists(session_path):
        try:
            import sqlite3
            conn = sqlite3.connect(session_path)
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='update_state'")
            if c.fetchone():
                c.execute("DELETE FROM update_state")
                conn.commit()
                logger.info("Cleared update_state for session %s to prevent PersistentTimestampOutdatedError", session_name)
            conn.close()
        except Exception as se:
            logger.warning("Gagal membersihkan update_state untuk session %s: %s", session_name, se)

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


async def start_account_by_id(account_id: int):
    # Stop first if already running
    if account_id in CLIENTS:
        await stop_account_by_id(account_id)
        
    account = db.get_account(account_id)
    if not account:
        logger.error("Account ID %s tidak ditemukan di DB.", account_id)
        return None
        
    return await start_account(account)


async def stop_account_by_id(account_id: int):
    client = CLIENTS.pop(account_id, None)
    AUTO_REPLY.pop(account_id, None)
    if client:
        try:
            await client.disconnect()
            logger.info("✅ Account ID %s disconnected.", account_id)
        except Exception as e:
            logger.error("Error disconnecting account ID %s: %s", account_id, e)

