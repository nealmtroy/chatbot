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

    USER_PROCESSING.add(user_key)
    try:
        # Natural typing delay (3-5 seconds)
        think = random.randint(3, 5)
        await asyncio.sleep(think)
        try:
            await event.client.send_read_acknowledge(event.chat_id, event.message, clear_mentions=True)
        except Exception as e:
            logger.warning("mark-read gagal: %s", e)

        # Get conversation history directly from Telegram (last 40 messages)
        tg_history = await event.client.get_messages(event.chat_id, limit=40)
        conversation_history = []
        
        # Exclude the current incoming message which is the first one in the list (index 0)
        for msg in reversed(tg_history[1:]):
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
                        f"dan minta mereka menunggu sebentar atau pastikan nominal transfer sudah pas.]"
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
                        f"sudah kedaluwarsa/mati, dan minta mereka bilang jika ingin dikirimkan QRIS baru lagi.]"
                    )
            else:
                # No payment history at all
                system_instruction = (
                    "[SYSTEM INFO: User saat ini BELUM PERNAH membuat tagihan atau QRIS sama sekali. "
                    "Jika user mengaku sudah membayar atau mengonfirmasi pembayaran, "
                    "katakan dengan santai/casual bahwa mereka belum minta QRIS-nya sama sekali "
                    "dan tawarkan untuk mengirimkan QRIS jika mereka mau melakukan pembayaran.]"
                )
        except Exception as e:
            logger.warning("Gagal menyematkan status pembayaran ke prompt: %s", e)

        # --- Generate Response directly from DigitalTwinAgent ---
        if clients.digital_twin_agent is None:
            logger.error("DigitalTwinAgent is not initialized!")
            return

        async with event.client.action(event.chat_id, "typing"):
            ai_response = await asyncio.to_thread(
                clients.digital_twin_agent.generate_response,
                user_input=message_text,
                conversation_history=conversation_history,
                system_instruction=system_instruction
            )

        if not ai_response:
            logger.warning(f"Gagal mendapatkan respon dari DigitalTwinAgent untuk user {user_name}")
            return

        # Format lines into bubbles
        raw_lines = [l.strip() for l in ai_response.split("\n") if l.strip()]
        
        # Check if any line in the response contains the QRIS trigger pattern
        has_qris_trigger = False
        qris_index = -1
        
        # Regex to match placeholders like (media qris), (kirim gambar qris), [foto qris], etc.
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
            combined_text = " ".join(cleaned_lines) + " " + message_text
            amount = parse_amount_from_text(combined_text, default_amount=100000)
            
            # Determine if it's VCS or VIP
            is_vcs = any("vcs" in l.lower() for l in cleaned_lines) or "vcs" in message_text.lower()
            note_prefix = "VCS" if is_vcs else "VIP"
            
            logger.info("Triggered QRIS creation for user tg=%s, amount=%s, type=%s", user_id_tg, amount, note_prefix)
            
            from vip_bot.config import load_config
            from vip_bot.db_store import PaymentStore
            from vip_bot.helpers import create_qris_with_retries_sync, public_invoice_id, SociaBuzzError
            
            vip_config = load_config()
            payment_store = PaymentStore(vip_config)
            
            try:
                # Generate QRIS using the thread executor
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
                
                # Register payment to Postgres/Supabase database
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
                
                # Send log to logging channel
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
                # Fallback to plain text bubbles if QRIS generation fails
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
