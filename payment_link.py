"""
payment_link.py - Bridge QRIS VIP dari sociabuzz-pay ke telegram-chatbot.

Reuse murni dari sociabuzz_client.py (API client SociaBuzz, gak butuh Supabase)
untuk membuat QRIS. QRIS dikirim lewat CHAT PRIBADI userbot (Alya/Intan),
bukan bot terpisah.

Flow:
  1. create_vip_qris(user, amount, note) -> panggil sociabuzz_client.create_qris
  2. return dict {qr_bytes, socia_inv_id, checkout_amount, caption}
  3. chatbot kirim image ke user, simpan ke db.payments (status=pending)
  4. payment_monitor.py poll status tiap inv_id -> kalau paid -> invite VIP

Env yang dibutuhkan (di .env chatbot):
  SOCIABUZZ_USERNAME = username Sociabuzz target TRIBE (misal 'boboinaja')
  SOCIABUZZ_COOKIE   = (opsional) cookie browser kalau CSRF butuh
  SOCIABUZZ_PAY_PATH = path absolut ke folder sociabuzz-pay (biar import client)
"""
import os
import sys
import logging
import asyncio
import re

logger = logging.getLogger("PaymentLink")

SOCIABUZZ_USERNAME = os.getenv("SOCIABUZZ_USERNAME", "")
SOCIABUZZ_COOKIE = os.getenv("SOCIABUZZ_COOKIE", "")
SOCIABUZZ_PAY_PATH = os.getenv("SOCIABUZZ_PAY_PATH", "")

# Inject path sociabuzz-pay biar bisa import sociabuzz_client.
# Default: parent folder dari telegram-chatbot (karena chatbot ada di
# sociabuzz-pay/telegram-chatbot/). Bisa override via env SOCIABUZZ_PAY_PATH.
if SOCIABUZZ_PAY_PATH:
    _pay_dir = SOCIABUZZ_PAY_PATH
else:
    _pay_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pay_dir and _pay_dir not in sys.path:
    sys.path.insert(0, _pay_dir)

try:
    import sociabuzz_client  # noqa
    _CLIENT_OK = True
except Exception as e:  # pragma: no cover
    logger.warning("sociabuzz_client gak bisa diimport: %s", e)
    sociabuzz_client = None
    _CLIENT_OK = False


def client_ready():
    return bool(_CLIENT_OK and SOCIABUZZ_USERNAME)


def _random_buyer_identity():
    """Identity acak buat SociaBuzz (biar gak pakai nama asli user)."""
    import secrets
    import random
    FIRST = ["Agus", "Andi", "Bambang", "Budi", "Dedi", "Eka", "Fajar", "Hendra",
             "Joko", "Rizki", "Sari", "Siti", "Taufik", "Wahyu", "Yudha"]
    LAST = ["Saputra", "Pratama", "Santoso", "Wijaya", "Nugroho", "Kurniawan",
            "Hidayat", "Setiawan", "Permana", "Ramadhan", "Maulana", "Lestari"]
    first = secrets.choice(FIRST)
    last = secrets.choice(LAST)
    suffix = random.randint(1000, 999999)
    return f"{first} {last}", f"{first.lower()}.{last.lower()}{suffix}@gmail.com"


def create_qris_sync(user_id, amount, note="VIP"):
    """
    Buat QRIS lewat SociaBuzz (blocking). Return dict atau raise.

    user_id: telegram user id (cuma buat note)
    amount : int Rupiah (misal 50000)
    """
    if not client_ready():
        raise RuntimeError("SociaBuzz client belum siap (cek SOCIABUZZ_USERNAME / path)")

    session = sociabuzz_client.new_session(SOCIABUZZ_COOKIE)
    name, email = _random_buyer_identity()
    order_id, payment_url, _ = sociabuzz_client.create_donation_order(
        session, SOCIABUZZ_USERNAME, amount, name, email, note=f"VIP-{user_id}"
    )
    qris = sociabuzz_client.create_qris(session, order_id, payment_url, amount)
    socia_inv_id = qris.get("inv_id") or ""
    qr_bytes = sociabuzz_client.download_qr_response(session, qris).content
    payload = qris.get("data", {})
    raw_amt = str(payload.get("amount") or amount)
    digits = re.sub(r"[^0-9]", "", raw_amt)
    checkout_amount = int(digits) if digits else int(amount)
    return {
        "qr_bytes": qr_bytes,
        "socia_inv_id": socia_inv_id,
        "checkout_amount": checkout_amount,
        "caption": f"💳 QRIS VIP - Rp{amount:,}\nScan & bayar dalam 15 menit. Status otomatis cek.",
    }


async def create_qris(user_id, amount, note="VIP"):
    """Wrapper async (jalankan blocking call di thread)."""
    return await asyncio.to_thread(create_qris_sync, user_id, amount, note)


def check_status_sync(socia_inv_id):
    """Cek status pembayaran SociaBuzz. Return 'paid'|'pending'|'failed_or_expired'|'unknown'."""
    if not client_ready():
        return "unknown"
    session = sociabuzz_client.new_session(SOCIABUZZ_COOKIE)
    status, _, _ = sociabuzz_client.check_pending(session, socia_inv_id)
    return status


async def check_status(socia_inv_id):
    return await asyncio.to_thread(check_status_sync, socia_inv_id)
