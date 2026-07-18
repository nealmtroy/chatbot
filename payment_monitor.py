"""
payment_monitor.py - Poll status QRIS VIP dan kirim invite grup saat lunas.

Berjalan sebagai task terpisah di main.py. Tiap loop:
  1. ambil db.pending_payments()
  2. untuk tiap payment, cek status SociaBuzz (payment_link.check_status)
  3. kalau 'paid': bikin invite link ke account.vip_chat_id, kirim ke user,
     update stage -> member, tambah total_spent
  4. kalau 'failed_or_expired': hapus QRIS message, mark expired

Invite link dibuat dari userbot (butuh account jadi member+admin di grup VIP,
atau pakai ExportChatInviteLinkRequest). Fallback: kalau gagal bikin link,
kirim pesan ke user untuk hubungi owner.
"""
import asyncio
import logging

import db
import user_tracker
import payment_link
from telethon.tl.functions.messages import ExportChatInviteRequest

logger = logging.getLogger("PaymentMonitor")

POLL_INTERVAL = int(__import__("os").getenv("PAYMENT_POLL_INTERVAL", "10"))


async def _make_invite(client, vip_chat_id):
    """Bikin invite link 1x pakai ke grup VIP. Return link atau ''."""
    if not vip_chat_id:
        return ""
    try:
        # vip_chat_id bisa "-100xxx" (supergroup) atau username
        result = await client(ExportChatInviteRequest(peer=vip_chat_id))
        return getattr(result, "link", "") or ""
    except Exception as e:
        logger.error("Gagal bikin invite link ke %s: %s", vip_chat_id, e)
        return ""


async def _deliver_paid(client, account, payment):
    """Kirim invite VIP ke user + update DB."""
    u = db.get_user(payment["user_id"])
    tg_uid = payment["tg_user_id"]
    invite = await _make_invite(client, account.get("vip_chat_id") or "")

    if invite:
        msg = (
            f"✅ *Pembayaran diterima!*\n\n"
            f"Link akses grup VIP kamu:\n{invite}\n\n"
            f"Link berlaku & bisa dipakai 1x. Makasih ya! 😘"
        )
        try:
            await client.send_message(int(tg_uid), msg, parse_mode="markdown")
        except Exception as e:
            logger.error("Gagal kirim invite ke %s: %s", tg_uid, e)
        db.update_payment(payment["id"], status="paid", invite_link=invite)
        db.add_spent(payment["user_id"], payment["amount"])
        db.advance_stage(payment["user_id"], "member")
        logger.info("PAID: user %s -> member, invite terkirim", tg_uid)
    else:
        # fallback: suruh hubungi owner
        try:
            await client.send_message(
                int(tg_uid),
                "✅ Pembayaran kamu lunas! Tapi link grup lagi gagal dibuat otomatis, "
                "hubungi owner buat invite manual ya.",
            )
        except Exception:
            pass
        db.update_payment(payment["id"], status="paid")
        db.add_spent(payment["user_id"], payment["amount"])
        db.advance_stage(payment["user_id"], "member")
        logger.warning("PAID tapi invite gagal: user %s (akun %s butuh jadi admin grup %s)",
                       tg_uid, account.get("name"), account.get("vip_chat_id"))


async def _handle_expired(client, payment):
    """Hapus pesan QRIS + mark expired."""
    try:
        if payment.get("qris_chat_id") and payment.get("qris_message_id"):
            await client.delete_messages(
                int(payment["qris_chat_id"]), [int(payment["qris_message_id"])], revoke=True
            )
    except Exception as e:
        logger.warning("Gagal hapus QRIS msg: %s", e)
    db.update_payment(payment["id"], status="expired")
    logger.info("EXPIRED: payment #%s (user %s)", payment["id"], payment["tg_user_id"])


async def monitor_loop(clients_map):
    """
    clients_map: dict account_id -> Telethon client (dari account_manager).
    """
    while True:
        try:
            pending = db.pending_payments(limit=100)
            for p in pending:
                acc = db.get_account(p["account_id"])
                client = clients_map.get(p["account_id"])
                if not acc or not client:
                    continue
                status = await payment_link.check_status(p.get("socia_inv_id") or "")
                if status == "paid":
                    await _deliver_paid(client, acc, p)
                elif status == "failed_or_expired":
                    await _handle_expired(client, p)
                elif status == "unknown":
                    # unknown bisa karena API error sementara, cek umur dulu
                    from datetime import datetime, timezone, timedelta
                    try:
                        created = datetime.fromisoformat(p["created_at"])
                        if datetime.now(timezone.utc) - created > timedelta(minutes=30):
                            logger.info("Payment #%s sudah >30 menit dengan status unknown, expire.", p["id"])
                            await _handle_expired(client, p)
                    except Exception:
                        pass  # skip, cek lagi loop berikutnya
        except Exception as e:
            logger.exception("Payment monitor error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


def start_monitor(clients_map):
    return monitor_loop(clients_map)
