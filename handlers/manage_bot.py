"""
manage_bot.py - Telegram BOT Interaktif (ReplyKeyboardMarkup & Inline Keyboard)
untuk admin mengelola account chatbot Telegram.

Fitur Utama via Keyboards:
  - 📱 Daftar Akun     : List account aktif, status, statistik ringkas
  - ➕ Tambah Akun     : Dialog interaktif step-by-step tambah akun baru
  - 📊 Statistik       : Lihat statistik jualan/stage per akun
  - 👥 List User       : Filter & lihat daftar calon pembeli/member
  - ⚙️ Edit Profil     : Ubah profil akun (kota, umur, bio, nama)
  - 🔄 Switch On/Off   : Saklar aktif/nonaktifkan akun 1-klik (Inline Button)
  - 💰 Konfirmasi Bayar: Verifikasi pembayaran manual & naikkan stage ke member
  - 🖼️ Tambah Media    : Upload media PAP / Video / VIP Preview per akun
  - ℹ️ Bantuan         : Informasi cara penggunaan
"""

import os
import sys
import re
import html
import logging
import warnings
from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", category=PTBUserWarning)

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    PhoneNumberInvalidError,
)

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

import io
import asyncio
from core.env_loader import load_env
import sociabuzz_client
from core import db
from vip_bot import db_store, helpers

logger = logging.getLogger("ManageBot")
load_env()

OWNER_IDS = set()
for raw in (os.getenv("OWNER_IDS", "") + "," + os.getenv("OWNER_ID", "")).split(","):
    if raw.strip().lstrip("-").isdigit():
        OWNER_IDS.add(int(raw.strip()))

ADMIN_IDS = set()
for raw in (os.getenv("ADMIN_IDS", "") + "," + os.getenv("ADMIN_USER_IDS", "")).split(","):
    if raw.strip().lstrip("-").isdigit():
        ADMIN_IDS.add(int(raw.strip()))

ADMIN_IDS.update(OWNER_IDS)

MANAGE_TOKEN = os.getenv("MANAGE_BOT_TOKEN", "")

LOGIN_CLIENTS = {}  # user_id -> Telethon client (temporary saat alur login OTP/2FA)


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS or user_id in OWNER_IDS


async def _cleanup_login_client(user_id: int):
    client = LOGIN_CLIENTS.pop(user_id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass

# --- State Conversions ---
(
    ADD_NAME,
    ADD_PHONE,
    ADD_OTP,
    ADD_2FA,
    ADD_CITY,
    ADD_AGE,
    SET_PROF_VAL,
    CONFIRM_PAY_USER,
    CONFIRM_PAY_AMOUNT,
    WAIT_MEDIA_FILE,
    PKG_CODE,
    PKG_NAME,
    PKG_CHAT_ID,
    PKG_AMOUNT,
    SET_LOG_CHAT,
    WITHDRAW_AMOUNT,
    WITHDRAW_INFO,
    BOT_TOKEN,
    BOT_NAME,
    PKG_EDIT_SELECT,
    PKG_EDIT_FIELD,
    PKG_EDIT_VAL,
) = range(22)

# --- Keyboards ---
OWNER_MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["📦 Paket VIP", "🤖 Bot Payment"],
        ["📢 Log Chat ID", "📱 Daftar Akun"],
        ["➕ Tambah Akun", "📊 Statistik"],
        ["👥 List User", "⚙️ Edit Profil"],
        ["🔄 Switch On/Off", "💰 Konfirmasi Bayar"],
        ["🖼️ Tambah Media", "ℹ️ Bantuan"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

ADMIN_MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["📱 Daftar Akun", "➕ Tambah Akun"],
        ["👥 List User", "⚙️ Edit Profil"],
        ["💰 Konfirmasi Bayar", "🖼️ Tambah Media"],
        ["ℹ️ Bantuan"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

MAIN_MENU_KEYBOARD = OWNER_MAIN_MENU


def get_user_keyboard(user_id: int):
    if is_owner(user_id):
        return OWNER_MAIN_MENU
    return ADMIN_MAIN_MENU


CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    [["❌ Batal / Kembali"]],
    resize_keyboard=True,
)


def _owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not is_owner(user_id):
            msg = update.message or (update.callback_query.message if update.callback_query else None)
            if msg:
                await msg.reply_text("❌ Akses ditolak. Fitur ini khusus untuk Owner.")
            if update.callback_query:
                await update.callback_query.answer("Akses ditolak (Owner Only)", show_alert=True)
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


def _admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not is_admin(user_id):
            msg = update.message or (update.callback_query.message if update.callback_query else None)
            if msg:
                await msg.reply_text("❌ Akses ditolak. Anda bukan Admin/Owner.")
            if update.callback_query:
                await update.callback_query.answer("Akses ditolak", show_alert=True)
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


def _fmt_account(a):
    status = "🟢 AKTIF" if a["active"] else "🔴 NONAKTIF"
    return (
        f"*{status}* | *#{a['id']} {a['name']}*\n"
        f"📍 Kota: `{a.get('city') or '-'}` | 🎂 Umur: `{a.get('age') or '-'}`\n"
        f"📂 Session: `{a['session_file']}`\n"
        f"📝 Bio: _{a.get('bio') or '-'}_"
    )


# --- NAVIGATION & CANCEL CHECKER ---
MENU_TEXT_MAP = {}  # Diisi di bawah setelah handler fungsi didefinisikan


async def check_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mengecek apakah teks yang dikirim user adalah tombol Menu Utama atau Batal.
    Jika ya, keluar dari conversation saat ini & langsung pindah ke menu yang dipilih.
    """
    if not update.message or not update.message.text:
        return None

    text = update.message.text.strip()
    user_id = update.effective_user.id if update.effective_user else 0

    if text in MENU_TEXT_MAP:
        await _cleanup_login_client(user_id)
        context.user_data.clear()
        handler = MENU_TEXT_MAP[text]
        await handler(update, context)
        return ConversationHandler.END

    if text in ("❌ Batal / Kembali", "/cancel", "/start"):
        await cancel_flow(update, context)
        return ConversationHandler.END

    return None


# --- 💸 REFERRAL & PROFILE & WITHDRAWALS ---
async def cmd_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    full_name = update.effective_user.full_name if update.effective_user else ""
    username = update.effective_user.username or ""
    start_text = update.message.text if update.message else ""
    if start_text and "ref_" in start_text:
        from vip_bot.helpers import parse_referral_payload, format_referral_code
        from vip_bot.config import load_config
        vip_cfg = load_config()
        store = db_store.PaymentStore(vip_cfg)
        payload = start_text.replace("/start", "").strip()
        code = parse_referral_payload(payload)
        if code and code != format_referral_code(user_id):
            referrer = store.get_user_by_referral_code(code)
            if referrer:
                dummy_user = type("DummyUser", (), {"id": user_id, "username": username, "full_name": full_name, "first_name": full_name, "last_name": ""})()
                store.create_referral_if_absent(referrer, dummy_user)

    if is_admin(user_id):
        return await cmd_start(update, context)

    txt = (
        f"👋 Halo *{full_name}*!\n\n"
        f"Selamat datang di VIP Automation System Bot.\n"
        f"Gunakan menu di bawah ini untuk melihat status keanggotaan dan link referral Anda."
    )
    kb = ReplyKeyboardMarkup([["👤 Profile & Referral", "💳 Tarik Saldo"], ["ℹ️ Bantuan"]], resize_keyboard=True)
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=kb)
    return ConversationHandler.END


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username or "bot"
    txt = referral_engine.get_user_profile_summary(user_id, bot_username=bot_username)
    
    btns = [
        [InlineKeyboardButton("💳 Tarik Saldo Komisi", callback_data="wd_start")],
    ]
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


async def cb_withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    sp_user = supabase_client.get_user(user_id) if supabase_client.is_configured() else None
    balance = int(sp_user.get("balance") or 0) if sp_user else 0

    if balance < 10000:
        await query.message.reply_text("❌ Minimal saldo penarikan komisi adalah Rp 10.000")
        return ConversationHandler.END

    txt = (
        f"💳 *PENARIKAN SALDO KOMISI*\n\n"
        f"Saldo Aktif: *Rp {balance:,}*\n\n"
        f"Masukkan **Jumlah Penarikan (Rp)** (contoh: `{balance}`):"
    )
    await query.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return WITHDRAW_AMOUNT


async def withdraw_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Jumlah harus berupa angka. Silakan masukkan lagi:")
        return WITHDRAW_AMOUNT

    context.user_data["wd_amount"] = amount
    txt = "Ketikkan **Nomor E-Wallet / Rekening Bank & Atas Nama** (contoh: `DANA 08123456789 a.n Budi`):"
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return WITHDRAW_INFO


async def withdraw_info_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    user_id = update.effective_user.id if update.effective_user else 0
    amount = context.user_data.get("wd_amount", 0)
    info = update.message.text.strip()

    ok, msg, w_data = referral_engine.create_withdrawal_request(user_id, amount, info)
    await update.message.reply_text(msg, reply_markup=get_user_keyboard(user_id))

    if ok and w_data:
        log_chat_id = db.get_setting("log_chat_id", os.getenv("LOG_CHAT_ID", ""))
        if log_chat_id:
            w_id = w_data.get("id", 0)
            txt_admin = (
                f"💸 *PENGAJUAN TARIK SALDO BARU*\n\n"
                f"• ID Penarikan: #{w_id}\n"
                f"• User ID: `{user_id}` ({update.effective_user.full_name})\n"
                f"• Jumlah: *Rp {amount:,}*\n"
                f"• Tujuan: `{info}`\n\n"
                f"Pilih tindakan admin di bawah ini:"
            )
            btns = [
                [
                    InlineKeyboardButton("✅ Setujui (Berhasil)", callback_data=f"wd_acc_{w_id}"),
                    InlineKeyboardButton("❌ Tolak", callback_data=f"wd_rej_{w_id}"),
                ]
            ]
            try:
                await context.bot.send_message(log_chat_id, txt_admin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
            except Exception as ex:
                logger.warning("Gagal kirim log penarikan saldo: %s", ex)

    context.user_data.clear()
    return ConversationHandler.END


@_admin_only
async def cb_withdraw_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_")
    action = parts[1]  # acc / rej
    w_id = int(parts[2])

    if action == "acc":
        supabase_client.update_withdrawal(w_id, "success", f"Disetujui oleh admin {query.from_user.id}")
        await query.edit_message_text(f"{query.message.text}\n\n✅ *STATUS: BERHASIL (APPROVED BY ADMIN)*", parse_mode="Markdown")
    else:
        supabase_client.update_withdrawal(w_id, "rejected", f"Ditolak oleh admin {query.from_user.id}")
        await query.edit_message_text(f"{query.message.text}\n\n❌ *STATUS: DITOLAK (REJECTED BY ADMIN)*", parse_mode="Markdown")


# --- 🎟️ CUSTOM INVOICE QRIS COMMAND ---
@_admin_only
async def cmd_custom_qris(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Format salah. Penggunaan: `/custom 50000`", parse_mode="Markdown")
        return

    amount = int(context.args[0])
    sb_username = os.getenv("SOCIABUZZ_USERNAME", "").strip()
    if not sb_username or sociabuzz_client is None:
        await update.message.reply_text("❌ SociaBuzz client / username belum diatur di .env!")
        return

    msg = await update.message.reply_text("⏳ Membuat QRIS Custom...")
    try:
        def _build():
            sess = sociabuzz_client.new_session(cookie_header=os.getenv("SOCIABUZZ_COOKIE", ""))
            order_id, payment_url, _ = sociabuzz_client.create_donation_order(
                sess, sb_username, amount, "Custom Invoice", "admin@chatbot.local", f"Custom Payment {amount}"
            )
            qris_data = sociabuzz_client.create_qris(sess, order_id, payment_url, amount)
            qr_resp = sociabuzz_client.download_qr_response(sess, qris_data)
            return qris_data, qr_resp.content

        qris_data, qr_bytes = await asyncio.to_thread(_build)
        inv_id = qris_data.get("inv_id", "")
        qr_file = io.BytesIO(qr_bytes)
        qr_file.name = f"custom_qris_{amount}.png"

        caption = (
            f"💳 **QRIS CUSTOM INVOICE**\n\n"
            f"Nominal: **Rp {amount:,}**\n"
            f"ID Invoice: `{inv_id}`\n\n"
            f"Scan QRIS di atas untuk melakukan pembayaran."
        )
        await context.bot.send_photo(update.effective_chat.id, photo=qr_file, caption=caption, parse_mode="Markdown")
        await msg.delete()
    except Exception as ex:
        logger.error("Gagal buat Custom QRIS: %s", ex)
        await msg.edit_text(f"❌ Gagal membuat QRIS Custom: {ex}")


# --- START & CANCEL ---
@_admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    await _cleanup_login_client(user_id)
    context.user_data.clear()
    kb = get_user_keyboard(user_id)
    role_str = "Owner 👑" if is_owner(user_id) else "Admin 🛠️"
    txt = (
        f"🤖 *PANEL UTAMA VIP AUTOMATION SYSTEM*\n\n"
        f"Selamat datang kakk! Anda terhubung sebagai: *{role_str}*\n\n"
        f"Pilih menu pada tombol keyboard di bawah untuk mengelola sistem, paket VIP, dan akun bot."
    )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=kb)
    return ConversationHandler.END


@_admin_only
async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    await _cleanup_login_client(user_id)
    context.user_data.clear()
    kb = get_user_keyboard(user_id)
    await update.message.reply_text(
        "🔙 Proses dibatalkan. Kembali ke Menu Utama.",
        reply_markup=kb,
    )
    return ConversationHandler.END


@_admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    role_str = "Owner 👑" if is_owner(user_id) else "Admin 🛠️"
    txt = (
        f"ℹ️ *PANDUAN PENGGUNAAN PANEL ({role_str})*\n\n"
        "• *📱 Daftar Akun*: Menampilkan seluruh akun terdaftar beserta status & paketnya.\n"
        "• *➕ Tambah Akun*: Panduan interaktif mendaftarkan akun userbot baru.\n"
        "• *📊 Statistik*: Statistik penjualan & breakdown stage user per akun.\n"
        "• *👥 List User*: Melihat daftar calon pembeli/member per akun & stage.\n"
        "• *⚙️ Edit Profil*: Mengubah Kota, Umur, Bio, atau Nama akun secara langsung.\n"
        "• *🔄 Switch On/Off*: Mengaktifkan / mematikan akun dengan 1-klik button.\n"
        "• *💰 Konfirmasi Bayar*: Konfirmasi pembayaran manual & ubah status user jadi Member.\n"
        "• *🖼️ Tambah Media*: Upload foto/video PAP / VIP Preview untuk bahan auto-reply.\n"
    )
    if is_owner(user_id):
        txt += (
            "\n👑 *AKSES EKSKLUSIF OWNER:*\n"
            "• *📦 Paket VIP*: Mengelola paket jualan (List, Add, Delete, Bind to Bot).\n"
            "• *📢 Log Chat ID*: Melihat & mengubah ID Channel Log Transaksi.\n"
        )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=get_user_keyboard(user_id))


# --- 📱 DAFTAR AKUN ---
@_admin_only
async def menu_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accs = db.list_accounts(active_only=False)
    if not accs:
        await update.message.reply_text(
            "Belum ada akun terdaftar. Klik tombol di bawah untuk menambah.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    lines = ["📱 *DAFTAR AKUN CHATBOT*\n"]
    for a in accs:
        lines.append(_fmt_account(a))
        try:
            users = db.list_users(a["id"], limit=10000)
            members = sum(1 for u in users if u["stage"] in ("member", "vcs_booked", "vcs_offered"))
            spent = sum(u["total_spent"] for u in users)
            lines.append(f"   📊 User: {len(users)} | Member: {members} | Revenue: Rp{spent:,}\n")
        except Exception:
            pass

    btns = [
        [InlineKeyboardButton("➕ Tambah Akun Baru", callback_data="add_acc_init")],
        [InlineKeyboardButton("🔄 Switch On/Off Akun", callback_data="menu_toggle")],
        [InlineKeyboardButton("⚙️ Edit Profil Akun", callback_data="menu_edit_prof")],
    ]
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns),
    )


# --- ➕ TAMBAH AKUN FLOW (ConversationHandler + Telethon Interactive Login) ---
def _slugify(text: str) -> str:
    """Mengubah teks nama menjadi format slug/session_file (lowercase, alphanumeric & underscore)."""
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', text.strip()).strip('_').lower()
    return slug or "account"


def _generate_unique_session_name(name: str) -> str:
    """Generate nama session file unik berbasis slug dari nama akun."""
    slug = _slugify(name)
    base_session = f"{slug}_session"
    conn = db.get_conn()
    existing = {row["session_file"] for row in conn.execute("SELECT session_file FROM accounts").fetchall()}
    if base_session not in existing:
        return base_session
    counter = 2
    while f"{slug}_{counter}_session" in existing:
        counter += 1
    return f"{slug}_{counter}_session"
def _generate_auto_package_code(name: str) -> str:
    """Generate kode paket unik berbasis slug dari nama paket."""
    slug = _slugify(name)
    if not slug or slug == "account":
        slug = "pkg"
    pkgs = db.list_packages(active_only=False)
    existing_codes = {p["code"] for p in pkgs} if pkgs else set()
    if slug not in existing_codes:
        return slug
    counter = 1
    while f"{slug}_{counter}" in existing_codes:
        counter += 1
    return f"{slug}_{counter}"

@_admin_only
async def add_acc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    await _cleanup_login_client(user_id)
    context.user_data.clear()
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if update.callback_query:
        await update.callback_query.answer()

    txt = (
        "➕ *TAMBAH & LOGIN AKUN BARU (Step 1/5)*\n\n"
        "Silakan masukkan *Nama Akun* (contoh: `Intan` atau `Vanya`):"
    )
    await msg.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return ADD_NAME


@_admin_only
async def add_acc_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if await check_navigation(update, context) == ConversationHandler.END:
        await _cleanup_login_client(user_id)
        return ConversationHandler.END

    name = update.message.text.strip()
    session_file = _generate_unique_session_name(name)
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")

    if not api_id or not api_hash:
        await update.message.reply_text(
            "❌ `TELEGRAM_API_ID` atau `TELEGRAM_API_HASH` belum diisi di file `.env`! Silakan isi `.env` dulu.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return ConversationHandler.END

    context.user_data["add_name"] = name
    context.user_data["add_session"] = session_file
    context.user_data["add_api_id"] = api_id
    context.user_data["add_api_hash"] = api_hash

    txt = (
        f"✅ Nama Akun: *{name}*\n"
        f"📂 Session File: `{session_file}.session` (auto-generated)\n\n"
        "2️⃣ *Nomor Telepon Telegram (Step 2/5)*\n"
        "Masukkan nomor telepon akun Telegram (format internasional, contoh: `+6281234567890`):"
    )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return ADD_PHONE


@_admin_only
async def add_acc_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if await check_navigation(update, context) == ConversationHandler.END:
        await _cleanup_login_client(user_id)
        return ConversationHandler.END

    phone = update.message.text.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        await update.message.reply_text(
            "❌ Format nomor telepon salah! Harus diawali tanda `+` (contoh: `+6281234567890`). Silakan masukkan lagi:"
        )
        return ADD_PHONE

    session_file = context.user_data.get("add_session")
    api_id = context.user_data.get("add_api_id")
    api_hash = context.user_data.get("add_api_hash")

    await update.message.reply_text("⏳ Menghubungkan ke Telegram & meminta kode OTP...")

    try:
        await _cleanup_login_client(user_id)
        from core.utils import get_session_path
        session_path = get_session_path(session_file)
        client = TelegramClient(session_path, api_id, api_hash)
        await client.connect()
        send_code = await client.send_code_request(phone)
        LOGIN_CLIENTS[user_id] = client

        context.user_data["add_phone"] = phone
        context.user_data["phone_code_hash"] = send_code.phone_code_hash

        txt = (
            f"📲 Kode OTP telah dikirimkan oleh Telegram ke nomor *{phone}*.\n\n"
            "3️⃣ *Kode OTP (Step 3/5)*\n"
            "Silakan masukkan Kode OTP yang Anda terima (contoh: `12345`):"
        )
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
        return ADD_OTP
    except PhoneNumberInvalidError:
        await update.message.reply_text("❌ Nomor telepon tidak valid di Telegram. Silakan masukkan nomor lain:")
        return ADD_PHONE
    except Exception as e:
        logger.error("Gagal send_code_request: %s", e)
        await update.message.reply_text(
            f"❌ Gagal mengirim OTP: {e}\nSilakan coba lagi atau periksa nomor HP Anda.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        await _cleanup_login_client(user_id)
        context.user_data.clear()
        return ConversationHandler.END


@_admin_only
async def add_acc_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if await check_navigation(update, context) == ConversationHandler.END:
        await _cleanup_login_client(user_id)
        return ConversationHandler.END

    otp_code = update.message.text.strip().replace(" ", "").replace("-", "")
    client = LOGIN_CLIENTS.get(user_id)
    if not client:
        await update.message.reply_text("❌ Sesi login kedaluwarsa. Silakan ulangi alur Tambah Akun.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    phone = context.user_data.get("add_phone")
    phone_code_hash = context.user_data.get("phone_code_hash")

    await update.message.reply_text("⏳ Verifikasi Kode OTP...")

    try:
        await client.sign_in(phone=phone, code=otp_code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        await client.disconnect()
        LOGIN_CLIENTS.pop(user_id, None)

        acc_name = me.first_name or context.user_data.get("add_name")
        txt = (
            f"🎉 *LOGIN BERHASIL!* Akun Telegram: *{acc_name}* (@{me.username or '-'})\n\n"
            "4️⃣ *Kota Domisili (Step 4/5)* (Opsional)\n"
            "Masukkan nama kota (contoh: `Bandung`), atau ketik `-` untuk lewati:"
        )
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
        return ADD_CITY

    except SessionPasswordNeededError:
        txt = (
            "🔐 *Akun dilindungi 2-Step Verification (2FA)*\n\n"
            "Silakan masukkan Password 2FA akun Telegram Anda:"
        )
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
        return ADD_2FA
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        await update.message.reply_text("❌ Kode OTP salah atau sudah kedaluwarsa. Silakan masukkan lagi Kode OTP:")
        return ADD_OTP
    except Exception as e:
        logger.error("Gagal OTP sign_in: %s", e)
        await update.message.reply_text(f"❌ Gagal verifikasi OTP: {e}\nSilakan ulangi alur.", reply_markup=MAIN_MENU_KEYBOARD)
        await _cleanup_login_client(user_id)
        context.user_data.clear()
        return ConversationHandler.END


@_admin_only
async def add_acc_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if await check_navigation(update, context) == ConversationHandler.END:
        await _cleanup_login_client(user_id)
        return ConversationHandler.END

    password = update.message.text.strip()
    client = LOGIN_CLIENTS.get(user_id)
    if not client:
        await update.message.reply_text("❌ Sesi login kedaluwarsa. Silakan ulangi alur Tambah Akun.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    await update.message.reply_text("⏳ Verifikasi Password 2FA...")

    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        await client.disconnect()
        LOGIN_CLIENTS.pop(user_id, None)

        acc_name = me.first_name or context.user_data.get("add_name")
        txt = (
            f"🎉 *LOGIN BERHASIL!* Akun Telegram: *{acc_name}* (@{me.username or '-'})\n\n"
            "4️⃣ *Kota Domisili (Step 4/5)* (Opsional)\n"
            "Masukkan nama kota (contoh: `Bandung`), atau ketik `-` untuk lewati:"
        )
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
        return ADD_CITY
    except PasswordHashInvalidError:
        await update.message.reply_text("❌ Password 2FA salah. Silakan masukkan lagi Password 2FA Anda:")
        return ADD_2FA
    except Exception as e:
        logger.error("Gagal 2FA sign_in: %s", e)
        await update.message.reply_text(f"❌ Gagal verifikasi 2FA: {e}\nSilakan ulangi alur.", reply_markup=MAIN_MENU_KEYBOARD)
        await _cleanup_login_client(user_id)
        context.user_data.clear()
        return ConversationHandler.END


@_admin_only
async def add_acc_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if await check_navigation(update, context) == ConversationHandler.END:
        await _cleanup_login_client(user_id)
        return ConversationHandler.END

    city = update.message.text.strip()
    if city == "-":
        city = ""

    context.user_data["add_city"] = city
    txt = (
        "5️⃣ *Umur Akun / Persona (Step 5/5)* (Opsional)\n"
        "Masukkan umur (contoh: `21`), atau ketik `-` untuk lewati:"
    )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return ADD_AGE


@_admin_only
async def add_acc_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if await check_navigation(update, context) == ConversationHandler.END:
        await _cleanup_login_client(user_id)
        return ConversationHandler.END

    text = update.message.text.strip()
    age = None
    if text != "-":
        try:
            age = int(text)
        except ValueError:
            await update.message.reply_text("❌ Umur harus berupa angka atau `-`. Masukkan lagi:")
            return ADD_AGE

    name = context.user_data.get("add_name")
    session_file = context.user_data.get("add_session")
    api_id = context.user_data.get("add_api_id")
    api_hash = context.user_data.get("add_api_hash")
    city = context.user_data.get("add_city", "")

    try:
        aid = db.add_account(name, session_file, api_id, api_hash, city=city, age=age)
        txt = (
            f"🎉 *AKUN '{name}' TERHUBUNG & LOGIN BERHASIL! (# {aid})*\n\n"
            f"📌 *Data Akun:*\n"
            f"• Nama: {name}\n"
            f"• Session: `{session_file}.session`\n"
            f"• Kota: {city or '-'}\n"
            f"• Umur: {age or '-'}\n\n"
            f"✅ File session telah terbuat dan ter-login di server!\n"
            f"Silakan restart service bot `sociabuzz-pay` agar akun ini langsung aktif melayani chat."
        )
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD)
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal membuat akun: {e}", reply_markup=MAIN_MENU_KEYBOARD)

    await _cleanup_login_client(user_id)
    context.user_data.clear()
    return ConversationHandler.END


# --- 🔄 SWITCH ON/OFF (Inline Buttons 1-Click) ---
@_admin_only
async def menu_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accs = db.list_accounts(active_only=False)
    if not accs:
        msg = update.message or (update.callback_query.message if update.callback_query else None)
        await msg.reply_text("Belum ada akun.", reply_markup=MAIN_MENU_KEYBOARD)
        return

    btns = []
    for a in accs:
        st = "🟢 AKTIF" if a["active"] else "🔴 NONAKTIF"
        action = "Matikan ❌" if a["active"] else "Aktifkan ✅"
        btns.append([InlineKeyboardButton(f"#{a['id']} {a['name']} ({st}) ➔ {action}", callback_data=f"tog_{a['id']}")])

    markup = InlineKeyboardMarkup(btns)
    txt = "🔄 *SWITCH ON/OFF AKUN CHATBOT*\n\nKlik tombol akun di bawah ini untuk mengaktifkan atau mematikan secara instant:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(txt, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=markup)


@_admin_only
async def cb_toggle_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    aid = int(query.data.split("_")[1])
    a = db.get_account(aid)
    if not a:
        await query.answer("Akun tidak ditemukan!", show_alert=True)
        return

    new_active = 0 if a["active"] else 1
    db.get_conn().execute("UPDATE accounts SET active=? WHERE id=?", (new_active, aid))
    db.get_conn().commit()

    # refresh list
    accs = db.list_accounts(active_only=False)
    btns = []
    for ac in accs:
        st = "🟢 AKTIF" if ac["active"] else "🔴 NONAKTIF"
        action = "Matikan ❌" if ac["active"] else "Aktifkan ✅"
        btns.append([InlineKeyboardButton(f"#{ac['id']} {ac['name']} ({st}) ➔ {action}", callback_data=f"tog_{ac['id']}")])

    status_str = "AKTIF 🟢" if new_active else "NONAKTIF 🔴"
    txt = (
        f"✅ Status akun *#{aid} {a['name']}* berhasil diubah menjadi *{status_str}*.\n"
        f"_(Catatan: Restart bot `sociabuzz-pay` di VPS jika mengubah status akun Telethon)._\n\n"
        f"🔄 *SWITCH ON/OFF AKUN CHATBOT*:"
    )
    await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


# --- ⚙️ EDIT PROFIL AKUN (Inline + Conversation) ---
@_admin_only
async def menu_edit_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accs = db.list_accounts(active_only=False)
    if not accs:
        msg = update.message or (update.callback_query.message if update.callback_query else None)
        await msg.reply_text("Belum ada akun.", reply_markup=MAIN_MENU_KEYBOARD)
        return

    btns = [[InlineKeyboardButton(f"#{a['id']} {a['name']}", callback_data=f"ep_acc_{a['id']}")] for a in accs]
    txt = "⚙️ *EDIT PROFIL AKUN*\n\nPilih akun yang ingin diubah profilnya:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


@_admin_only
async def cb_edit_prof_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    aid = int(query.data.split("_")[2])
    context.user_data["edit_aid"] = aid
    a = db.get_account(aid)

    btns = [
        [
            InlineKeyboardButton("📛 Nama", callback_data="ep_f_name"),
            InlineKeyboardButton("🏙️ Kota", callback_data="ep_f_city"),
        ],
        [
            InlineKeyboardButton("🎂 Umur", callback_data="ep_f_age"),
            InlineKeyboardButton("📝 Bio", callback_data="ep_f_bio"),
        ],
    ]
    txt = (
        f"⚙️ *EDIT PROFIL AKUN #{a['id']} ({a['name']})*\n\n"
        f"• Nama: `{a['name']}`\n"
        f"• Kota: `{a.get('city') or '-'}`\n"
        f"• Umur: `{a.get('age') or '-'}`\n"
        f"• Bio: _{a.get('bio') or '-'}_\n\n"
        f"Pilih field yang ingin diubah:"
    )
    await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


@_admin_only
async def cb_edit_prof_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split("_")[2]  # name, city, age, bio
    context.user_data["edit_field"] = field
    aid = context.user_data.get("edit_aid")
    a = db.get_account(aid)

    txt = (
        f"✏️ *Ubah {field.upper()} untuk Akun #{aid} ({a['name']})*\n\n"
        f"Silakan ketik nilai baru untuk *{field}* (atau klik tombol Batal):"
    )
    await query.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return SET_PROF_VAL


@_admin_only
async def edit_prof_val_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    text = update.message.text.strip()
    aid = context.user_data.get("edit_aid")
    field = context.user_data.get("edit_field")

    kw = {}
    if field == "age":
        try:
            kw["age"] = int(text)
        except ValueError:
            await update.message.reply_text("❌ Umur harus berupa angka. Masukkan lagi:")
            return SET_PROF_VAL
    else:
        kw[field] = text

    db.set_account_profile(aid, **kw)
    await update.message.reply_text(
        f"✅ Profil akun *#{aid}* berhasil diubah:\n*{field}* ➔ `{text}`",
        parse_mode="Markdown",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    context.user_data.clear()
    return ConversationHandler.END


# --- 📊 STATISTIK (Inline Interactive) ---
@_admin_only
async def menu_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    msg = update.message or (query.message if query else None)

    accs = db.list_accounts(active_only=False)
    if not accs:
        if msg:
            await msg.reply_text("Belum ada akun.", reply_markup=get_user_keyboard(update.effective_user.id))
        return

    btns = [[InlineKeyboardButton(f"📊 #{a['id']} {a['name']}", callback_data=f"st_acc_{a['id']}")] for a in accs]
    btns.append([InlineKeyboardButton("🌐 Semua Akun", callback_data="st_acc_all")])
    txt = "📊 *STATISTIK PENJUALAN CHATBOT*\n\nPilih akun untuk melihat rincian statistik:"
    if msg:
        await msg.reply_text(
            txt,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns),
        )


@_admin_only
async def cb_stats_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.split("_")[2]

    if target == "all":
        accs = db.list_accounts(active_only=False)
        total_u, total_m, total_rev = 0, 0, 0
        for a in accs:
            users = db.list_users(a["id"], limit=10000)
            total_u += len(users)
            total_m += sum(1 for u in users if u["stage"] in ("member", "vcs_booked", "vcs_offered"))
            total_rev += sum(u["total_spent"] for u in users)
        txt = (
            f"🌐 *TOTAL STATISTIK SEMUA AKUN*\n\n"
            f"• Total Akun: {len(accs)}\n"
            f"• Total Calon Pembeli: {total_u:,}\n"
            f"• Total VIP Member: {total_m:,}\n"
            f"• Total Revenue: *Rp{total_rev:,}*\n"
        )
    else:
        aid = int(target)
        a = db.get_account(aid)
        users = db.list_users(aid, limit=10000)
        members = [u for u in users if u["stage"] in ("member", "vcs_offered", "vcs_booked")]
        vcs = [u for u in users if u["stage"] in ("vcs_offered", "vcs_booked")]
        spent = sum(u["total_spent"] for u in users)
        by_stage = {}
        for u in users:
            by_stage[u["stage"]] = by_stage.get(u["stage"], 0) + 1

        txt = (
            f"📊 *STATISTIK AKUN #{aid} ({a['name']})*\n\n"
            f"• Total User Chat: {len(users)}\n"
            f"• Member VIP: {len(members)}\n"
            f"• Booking VCS: {len(vcs)}\n"
            f"• Total Cuan: *Rp{spent:,}*\n\n"
            f"📈 *Breakdown Stage:*\n"
        )
        for s, c in sorted(by_stage.items(), key=lambda x: -x[1]):
            txt += f"  • `{s}`: {c} user\n"

    btns = [[InlineKeyboardButton("🔙 Kembali ke List Akun", callback_data="st_back")]]
    await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


# --- 👥 LIST USER (Filter Stage & Account) ---
@_admin_only
async def menu_list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    msg = update.message or (query.message if query else None)

    accs = db.list_accounts(active_only=False)
    if not accs:
        if msg:
            await msg.reply_text("Belum ada akun.", reply_markup=get_user_keyboard(update.effective_user.id))
        return

    btns = [[InlineKeyboardButton(f"👥 #{a['id']} {a['name']}", callback_data=f"lu_acc_{a['id']}")] for a in accs]
    if msg:
        await msg.reply_text(
            "👥 *DAFTAR USER / CALON PEMBELI*\n\nPilih akun yang ingin dilihat daftarnya:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns),
        )


@_admin_only
async def cb_list_users_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    aid = int(query.data.split("_")[2])
    context.user_data["lu_aid"] = aid
    a = db.get_account(aid)

    stages = ["Semua", "member", "asked_price", "payment_pending", "vcs_booked", "interested", "greeted", "new"]
    btns = []
    row = []
    for st in stages:
        row.append(InlineKeyboardButton(st.capitalize(), callback_data=f"lu_st_{st}"))
        if len(row) == 2:
            btns.append(row)
            row = []
    if row:
        btns.append(row)

    txt = f"👥 *FILTER STAGE USER AKUN #{aid} ({a['name']})*\n\nPilih stage user yang ingin ditampilkan:"
    await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


@_admin_only
async def cb_list_users_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    st_filter = query.data.split("_")[2]
    stage = None if st_filter == "Semua" else st_filter
    aid = context.user_data.get("lu_aid", 1)
    a = db.get_account(aid)

    users = db.list_users(aid, stage=stage, limit=100)
    if not users:
        await query.edit_message_text(
            f"❌ Tidak ada user di akun #{aid} ({a['name']}) dengan stage `{st_filter}`.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Pilih Stage Lain", callback_data=f"lu_acc_{aid}")]])
        )
        return

    lines = [f"👥 *USER AKUN #{aid} ({a['name']}) - Stage: {st_filter}*\n"]
    for u in users[:30]:
        name = u.get("name") or u.get("first_name") or "Tanpa Nama"
        username = f"@{u['username']}" if u.get("username") else f"tg{u['tg_user_id']}"
        lines.append(f"• `{u['tg_user_id']}` {name} ({username}) | `{u['stage']}` | Rp{u['total_spent']:,}")

    if len(users) > 30:
        lines.append(f"\n...dan {len(users)-30} user lainnya.")

    btns = [[InlineKeyboardButton("🔙 Pilih Stage Lain", callback_data=f"lu_acc_{aid}")]]
    await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


# --- 💰 KONFIRMASI BAYAR (ConversationHandler) ---
@_admin_only
async def payconfirm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    query = update.callback_query
    if query:
        await query.answer()

    msg = update.message or (query.message if query else None)

    accs = db.list_accounts(active_only=False)
    if not accs:
        if msg:
            await msg.reply_text("Belum ada akun.", reply_markup=get_user_keyboard(update.effective_user.id))
        return ConversationHandler.END

    btns = [[InlineKeyboardButton(f"#{a['id']} {a['name']}", callback_data=f"pc_acc_{a['id']}")] for a in accs]
    if msg:
        await msg.reply_text(
            "💰 *KONFIRMASI PEMBAYARAN MANUAL*\n\nPilih Akun Chatbot tujuan pembayaran:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns),
        )
    return CONFIRM_PAY_USER


@_admin_only
async def cb_payconfirm_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    aid = int(query.data.split("_")[2])
    context.user_data["pc_aid"] = aid

    txt = (
        f"💰 *Konfirmasi Bayar Akun #{aid}*\n\n"
        "Silakan ketik *Telegram User ID* pembeli (contoh: `123456789`):"
    )
    await query.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return CONFIRM_PAY_USER


@_admin_only
async def payconfirm_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    text = update.message.text.strip()
    try:
        tg_uid = int(text)
    except ValueError:
        await update.message.reply_text("❌ Telegram User ID harus berupa angka. Silakan masukkan lagi:")
        return CONFIRM_PAY_USER

    context.user_data["pc_tg_uid"] = tg_uid
    txt = (
        f"✅ User ID: `{tg_uid}`\n\n"
        "Masukkan *Nominal Pembayaran (Rp)* (contoh: `50000`):"
    )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return CONFIRM_PAY_AMOUNT


@_admin_only
async def payconfirm_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    text = update.message.text.strip()
    try:
        amount = int(text)
    except ValueError:
        await update.message.reply_text("❌ Nominal harus berupa angka. Masukkan lagi:")
        return CONFIRM_PAY_AMOUNT

    aid = context.user_data.get("pc_aid", 1)
    tg_uid = context.user_data.get("pc_tg_uid")

    u = db.get_or_create_user(aid, tg_uid)
    db.add_spent(u["id"], amount)
    db.advance_stage(u["id"], "member")
    db.update_user(u["id"], interested=1)

    txt = (
        f"✅ *PEMBAYARAN DIKONFIRMASI!*\n\n"
        f"• Akun: #{aid}\n"
        f"• Telegram User ID: `{tg_uid}`\n"
        f"• Nominal: *Rp{amount:,}*\n"
        f"• Status Stage: `member` 🎉"
    )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD)
    context.user_data.clear()
    return ConversationHandler.END


# --- 🖼️ TAMBAH MEDIA (ConversationHandler & Forward) ---
@_admin_only
async def addmedia_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    query = update.callback_query
    if query:
        await query.answer()

    msg = update.message or (query.message if query else None)

    accs = db.list_accounts(active_only=False)
    if not accs:
        if msg:
            await msg.reply_text("Belum ada akun.", reply_markup=get_user_keyboard(update.effective_user.id))
        return ConversationHandler.END

    btns = [[InlineKeyboardButton(f"#{a['id']} {a['name']}", callback_data=f"am_acc_{a['id']}")] for a in accs]
    if msg:
        await msg.reply_text(
            "🖼️ *TAMBAH MEDIA CHATBOT*\n\nPilih Akun tempat media ingin ditambahkan:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns),
        )
    return WAIT_MEDIA_FILE


@_admin_only
async def cb_addmedia_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    aid = int(query.data.split("_")[2])
    context.user_data["am_aid"] = aid

    btns = [
        [InlineKeyboardButton("📸 PAP (Foto/Selfie)", callback_data="am_int_pap")],
        [InlineKeyboardButton("🎥 Video", callback_data="am_int_video")],
        [InlineKeyboardButton("🔒 VIP Preview", callback_data="am_int_vip_preview")],
    ]
    txt = f"🖼️ *Tambah Media untuk Akun #{aid}*\n\nPilih Intent / Jenis Media:"
    await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    return WAIT_MEDIA_FILE


@_admin_only
async def cb_addmedia_intent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    intent = query.data.split("_")[2]
    context.user_data["am_intent"] = intent
    aid = context.user_data.get("am_aid")

    txt = (
        f"📥 *Forward File Media Now (Intent: {intent.upper()})*\n\n"
        f"Silakan **Forward Foto, Video, atau Dokumen** ke chat bot ini sekarang."
    )
    await query.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return WAIT_MEDIA_FILE


@_admin_only
async def addmedia_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    msg = update.message
    file_id = None
    mtype = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
        mtype = "photo"
    elif msg.video:
        file_id = msg.video.file_id
        mtype = "video"
    elif msg.document:
        file_id = msg.document.file_id
        mtype = "document"

    if not file_id:
        await update.message.reply_text("❌ Mohon kirimkan Foto, Video, atau Dokumen. Coba lagi:")
        return WAIT_MEDIA_FILE

    aid = context.user_data.get("am_aid", 1)
    intent = context.user_data.get("am_intent", "pap")

    db.add_media(aid, intent, mtype, file_id, 0, caption="")
    total = db.count_media(aid, intent)

    txt = (
        f"✅ *MEDIA BERHASIL DITAMBAHKAN!*\n\n"
        f"• Akun: #{aid}\n"
        f"• Intent: `{intent}`\n"
        f"• Tipe Media: `{mtype}`\n"
        f"• Total Media `{intent}`: *{total} item*"
    )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD)
    context.user_data.clear()
    return ConversationHandler.END


# --- 👑 OWNER EXCLUSIVE HANDLERS ---
@_owner_only
async def menu_owner_log_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = db.get_setting("log_chat_id", os.getenv("LOG_CHAT_ID", "(belum di-set)"))
    txt = (
        f"📢 *LOG CHAT TRANSACTION ID*\n\n"
        f"ID Log Chat saat ini: `{current}`\n\n"
        f"Ketik **ID Log Chat baru** (contoh: `-1001234567890`), atau ketik `-` untuk lewati:"
    )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return SET_LOG_CHAT


@_owner_only
async def save_owner_log_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    text = update.message.text.strip()
    if text != "-":
        db.set_setting("log_chat_id", text)
        await update.message.reply_text(
            f"✅ Log Chat ID berhasil disimpan: `{text}`",
            parse_mode="Markdown",
            reply_markup=get_user_keyboard(update.effective_user.id),
        )
    else:
        await update.message.reply_text("Log Chat ID tidak diubah.", reply_markup=get_user_keyboard(update.effective_user.id))

    context.user_data.clear()
    return ConversationHandler.END


@_owner_only
async def menu_owner_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pkgs = db.list_packages(active_only=True)
    txt = "📦 <b>MANAGEMENT PAKET VIP PENJUALAN</b>\n\n"
    if not pkgs:
        txt += "<i>Belum ada paket VIP terdaftar. Klik tombol di bawah untuk menambah paket baru.</i>\n\n"
    else:
        txt += "<b>Daftar Paket VIP Aktif:</b>\n"
        for idx, p in enumerate(pkgs, 1):
            p_code = html.escape(p['code'])
            p_name = html.escape(p['name'])
            p_amount = int(p.get('amount') or 0)
            p_chat = html.escape(str(p.get('vip_chat_id') or '-'))
            txt += (
                f"{idx}. <b>{p_name}</b> (Kode: <code>{p_code}</code>)\n"
                f"   • Harga: <b>Rp {p_amount:,}</b> | Chat ID: <code>{p_chat}</code>\n\n"
            )

    txt += "Pilih tindakan di bawah ini:"

    btns = [
        [InlineKeyboardButton("➕ Tambah Paket VIP Baru", callback_data="pkg_add_init")],
        [InlineKeyboardButton("✏️ Edit Paket VIP", callback_data="pkg_edit_init")],
        [InlineKeyboardButton("❌ Hapus Paket VIP", callback_data="pkg_del_init")],
        [InlineKeyboardButton("🔙 Kembali ke Bot Payment", callback_data="bot_menu_show")],
    ]
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))


@_owner_only
async def pkg_edit_init(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    pkgs = db.list_packages(active_only=True)
    if not pkgs:
        txt = "Tidak ada Paket VIP aktif untuk di-edit."
        if query:
            await query.message.reply_text(txt)
        else:
            await update.message.reply_text(txt)
        return ConversationHandler.END

    btns = [
        [InlineKeyboardButton(f"✏️ {p['name']} (Rp {int(p.get('amount') or 0):,})", callback_data=f"pe_sel_{p['code']}")]
        for p in pkgs
    ]
    btns.append([InlineKeyboardButton("❌ Batal", callback_data="nav_cancel")])

    txt = "✏️ <b>PILIH PAKET VIP YANG INGIN DI-EDIT:</b>"
    if query:
        await query.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))
    return PKG_EDIT_SELECT


@_owner_only
async def pkg_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.replace("pe_sel_", "")
    pkg = db.get_package(code)
    if not pkg:
        await query.edit_message_text("❌ Paket tidak ditemukan.")
        return ConversationHandler.END

    context.user_data["edit_pkg_code"] = code
    context.user_data["edit_pkg_data"] = pkg

    txt = (
        f"✏️ <b>EDIT PAKET: {html.escape(pkg['name'])}</b>\n\n"
        f"• Kode: <code>{html.escape(pkg['code'])}</code>\n"
        f"• Nama: <b>{html.escape(pkg['name'])}</b>\n"
        f"• Harga: <b>Rp {int(pkg.get('amount') or 0):,}</b>\n"
        f"• VIP Chat ID: <code>{html.escape(str(pkg.get('vip_chat_id') or '-'))}</code>\n\n"
        "Pilih bagian yang ingin diubah:"
    )

    btns = [
        [InlineKeyboardButton("📝 Ubah Nama Paket", callback_data="pe_field_name")],
        [InlineKeyboardButton("💰 Ubah Harga (Rp)", callback_data="pe_field_amount")],
        [InlineKeyboardButton("📢 Ubah VIP Chat ID", callback_data="pe_field_chat_id")],
        [InlineKeyboardButton("❌ Batal", callback_data="nav_cancel")],
    ]
    await query.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))
    return PKG_EDIT_FIELD


@_owner_only
async def pkg_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.replace("pe_field_", "")
    context.user_data["edit_pkg_field"] = field

    prompts = {
        "name": "Masukkan **Nama Paket Baru**:",
        "amount": "Masukkan **Harga Paket Baru (Rp)** (contoh: `75000`):",
        "chat_id": "Masukkan **VIP Chat/Channel ID Baru** (contoh: `-1001234567890`):",
    }
    prompt_txt = prompts.get(field, "Masukkan nilai baru:")
    await query.message.reply_text(prompt_txt, parse_mode="Markdown", reply_markup=CANCEL_KEYBOARD)
    return PKG_EDIT_VAL


@_owner_only
async def pkg_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    val_text = update.message.text.strip()
    code = context.user_data.get("edit_pkg_code")
    field = context.user_data.get("edit_pkg_field")
    pkg = context.user_data.get("edit_pkg_data", {})

    if not code or not pkg:
        await update.message.reply_text("❌ Sesi edit telah kadaluarsa.")
        return ConversationHandler.END

    name = pkg.get("name", "")
    amount = pkg.get("amount", 0)
    vip_chat_id = pkg.get("vip_chat_id", "")

    if field == "name":
        name = val_text
    elif field == "amount":
        try:
            amount = int(val_text)
        except ValueError:
            await update.message.reply_text("❌ Nominal harus berupa angka. Silakan masukkan lagi:")
            return PKG_EDIT_VAL
    elif field == "chat_id":
        vip_chat_id = "" if val_text == "-" else val_text

    db.add_package(code, name, vip_chat_id, amount)
    from vip_bot.config import load_config
    vip_cfg = load_config()
    store = db_store.PaymentStore(vip_cfg)
    store.upsert_package(code, name, vip_chat_id, amount)

    txt = (
        f"✅ <b>PAKET VIP BERHASIL DIPERBARUI!</b>\n\n"
        f"• Kode: <code>{html.escape(code)}</code>\n"
        f"• Nama: <b>{html.escape(name)}</b>\n"
        f"• Harga: <b>Rp {amount:,}</b>\n"
        f"• VIP Chat ID: <code>{html.escape(str(vip_chat_id or '-'))}</code>"
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=get_user_keyboard(update.effective_user.id))
    context.user_data.clear()
    return ConversationHandler.END


@_owner_only
async def pkg_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    txt = (
        "➕ <b>TAMBAH PAKET VIP BARU (Step 1/3)</b>\n\n"
        "Masukkan <b>Nama Paket VIP</b> (contoh: <code>Paket VIP 1 Bulan</code> atau <code>VIP Silver</code>):"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(txt, parse_mode="HTML", reply_markup=CANCEL_KEYBOARD)
    else:
        await update.message.reply_text(txt, parse_mode="HTML", reply_markup=CANCEL_KEYBOARD)
    return PKG_NAME


@_owner_only
async def pkg_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    name = update.message.text.strip()
    auto_code = _generate_auto_package_code(name)
    context.user_data["pkg_name"] = name
    context.user_data["pkg_code"] = auto_code

    txt = (
        f"Nama Paket: <b>{html.escape(name)}</b>\n"
        f"Kode Auto: <code>{html.escape(auto_code)}</code>\n\n"
        "2️⃣ <b>ID Channel/Group VIP Telegram (Step 2/3)</b>\n"
        "Masukkan <b>VIP Chat/Channel ID</b> tujuan (contoh: <code>-1001234567890</code>), atau ketik <code>-</code> jika belum ada:"
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=CANCEL_KEYBOARD)
    return PKG_CHAT_ID


@_owner_only
async def pkg_add_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    chat_id = update.message.text.strip()
    if chat_id == "-":
        chat_id = ""
    context.user_data["pkg_chat_id"] = chat_id

    txt = (
        "3️⃣ <b>Nominal Harga Paket (Step 3/3)</b>\n\n"
        "Masukkan <b>Harga Pembayaran (Rp)</b> (contoh: <code>25000</code>):"
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=CANCEL_KEYBOARD)
    return PKG_AMOUNT


@_owner_only
async def pkg_add_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    text = update.message.text.strip()
    try:
        amount = int(text)
    except ValueError:
        await update.message.reply_text("❌ Nominal harus berupa angka. Silakan masukkan lagi:")
        return PKG_AMOUNT

    code = context.user_data.get("pkg_code")
    name = context.user_data.get("pkg_name")
    vip_chat_id = context.user_data.get("pkg_chat_id", "")

    db.add_package(code, name, vip_chat_id, amount)
    from vip_bot.config import load_config
    vip_cfg = load_config()
    store = db_store.PaymentStore(vip_cfg)
    store.upsert_package(code, name, vip_chat_id, amount)

    txt = (
        f"🎉 <b>PAKET VIP BERHASIL DIBUAT!</b>\n\n"
        f"• Kode (Auto): <code>{html.escape(code)}</code>\n"
        f"• Nama: <b>{html.escape(name)}</b>\n"
        f"• VIP Chat ID: <code>{html.escape(str(vip_chat_id or '-'))}</code>\n"
        f"• Nominal: <b>Rp {amount:,}</b>"
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=get_user_keyboard(update.effective_user.id))
    context.user_data.clear()
    return ConversationHandler.END


@_owner_only
async def pkg_del_init(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pkgs = db.list_packages(active_only=True)
    if not pkgs:
        await query.edit_message_text("Tidak ada paket VIP aktif untuk dihapus.")
        return

    btns = [[InlineKeyboardButton(f"❌ {p['code']} - {p['name']}", callback_data=f"pkg_del_{p['code']}")] for p in pkgs]
    await query.edit_message_text(
        "❌ *PILIH PAKET VIP UNTUK DIHAPUS:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns),
    )


@_owner_only
async def cb_pkg_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.replace("pkg_del_", "")
    db.delete_package(code)
    await query.edit_message_text(f"✅ Paket VIP `{code}` telah dihapus/dinonaktifkan.")


@_owner_only
async def pkg_bind_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    accs = db.list_accounts(active_only=True)
    if not accs:
        await query.edit_message_text("Belum ada akun bot aktif.")
        return

    btns = [[InlineKeyboardButton(f"🤖 #{a['id']} {a['name']} (Pkg: {a.get('package_code') or 'default'})", callback_data=f"pb_acc_{a['id']}")] for a in accs]
    await query.edit_message_text(
        "🤖 *HUBUNGKAN PAKET VIP KE BOT*\n\nPilih bot yang ingin di-set paket VIP nya:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns),
    )


@_owner_only
async def cb_bind_bot_select_pkg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    aid = int(query.data.split("_")[2])
    pkgs = db.list_packages(active_only=True)
    if not pkgs:
        await query.edit_message_text("Belum ada paket VIP. Buat paket dulu lewat menu Paket VIP.")
        return

    btns = [[InlineKeyboardButton(f"📦 {p['code']} (Rp {p['amount']:,})", callback_data=f"pb_set_{aid}_{p['code']}")] for p in pkgs]
    await query.edit_message_text(
        f"Pilih Paket VIP untuk Bot #{aid}:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns),
    )


@_owner_only
async def cb_bind_bot_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    aid = int(parts[2])
    p_code = parts[3]
    db.set_account_package(aid, p_code)
    await query.edit_message_text(f"✅ Bot #{aid} berhasil dihubungkan ke Paket VIP `{p_code}`!")


@_owner_only
async def menu_owner_payment_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from vip_bot.config import load_config
    vip_cfg = load_config()
    store = db_store.PaymentStore(vip_cfg)
    bots = store.list_payment_bots()

    btns = []

    # 1. Default Bot
    default_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("MANAGE_BOT_TOKEN") or ""
    if default_token:
        btns.append([InlineKeyboardButton("🤖 Default Payment Bot (Utama)", callback_data="bot_sel_default")])

    # 2. Additional Payment Bots from Database
    if bots:
        for b in bots:
            b_name = b.get("bot_name") or "Payment Bot"
            b_uname = f" (@{b['bot_username']})" if b.get("bot_username") else ""
            btns.append([InlineKeyboardButton(f"🤖 {b_name}{b_uname}", callback_data=f"bot_sel_{b['id']}")])

    # Management buttons
    btns.append([InlineKeyboardButton("➕ Tambah Bot Payment Baru", callback_data="bot_add_init")])
    btns.append([InlineKeyboardButton("❌ Hapus / Nonaktifkan Bot", callback_data="bot_del_init")])

    txt = (
        "🤖 <b>MANAGEMENT BOT PAYMENT</b>\n\n"
        "Silakan klik salah satu <b>Bot Payment</b> di bawah ini untuk melihat detail & mengelola Paket VIP yang terhubung:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))


@_owner_only
async def cb_bot_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    target_id = query.data.replace("bot_sel_", "")
    bot_info = None
    bot_username = ""

    from vip_bot.config import load_config
    vip_cfg = load_config()
    store = db_store.PaymentStore(vip_cfg)

    if target_id == "default":
        bot_info = {"bot_name": "Default Payment Bot", "bot_username": "default", "active": True}
        bot_username = ""
    else:
        bots = store.list_payment_bots(include_inactive=True)
        for b in bots:
            if str(b.get("id")) == str(target_id):
                bot_info = b
                bot_username = b.get("bot_username", "")
                break

    if not bot_info:
        await query.edit_message_text("❌ Bot Payment tidak ditemukan.")
        return

    # List packages bound to this bot
    pkgs = store.list_packages(bot_username=bot_username)
    if not pkgs:
        pkgs = db.list_packages(active_only=True, bot_username=bot_username)

    b_name = html.escape(bot_info.get("bot_name") or "Payment Bot")
    b_uname = html.escape(bot_info.get("bot_username") or "-")

    txt = (
        f"🤖 <b>MANAJEMEN BOT PAYMENT: {b_name}</b> (@{b_uname})\n\n"
        f"<b>📦 Paket VIP Terhubung ke Bot Ini:</b>\n"
    )

    if not pkgs:
        txt += "<i>Belum ada Paket VIP yang terhubung khusus ke bot ini.</i>\n\n"
    else:
        for idx, p in enumerate(pkgs, 1):
            p_code = html.escape(p["code"])
            p_name = html.escape(p["name"])
            p_amount = int(p.get("amount") or 0)
            p_chat = html.escape(str(p.get("vip_chat_id") or "-"))
            txt += (
                f"{idx}. <b>{p_name}</b> (Kode: <code>{p_code}</code>)\n"
                f"   • Harga: <b>Rp {p_amount:,}</b> | Chat ID: <code>{p_chat}</code>\n\n"
            )

    txt += "Pilih tindakan di bawah ini:"

    context.user_data["current_bot_target"] = target_id
    context.user_data["current_bot_username"] = bot_username

    btns = [
        [InlineKeyboardButton(f"➕ Tambah Paket VIP ({b_name})", callback_data="pkg_add_init")],
        [InlineKeyboardButton("✏️ Edit Paket VIP", callback_data="pkg_edit_init")],
        [InlineKeyboardButton("❌ Hapus Paket VIP", callback_data="pkg_del_init")],
        [InlineKeyboardButton("🔙 Kembali ke Daftar Bot", callback_data="bot_menu_show")],
    ]

    await query.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))


@_owner_only
async def pkg_menu_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await menu_owner_packages(update, context)


@_owner_only
async def bot_menu_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await menu_owner_payment_bots(update, context)


@_owner_only
async def bot_add_init(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    context.user_data.clear()
    txt = (
        "➕ <b>TAMBAH BOT PAYMENT BARU (Step 1/2)</b>\n\n"
        "Kirimkan <b>Token Bot</b> yang didapatkan dari @BotFather:\n"
        "<i>(contoh: <code>123456789:ABCdefGHIjklmnOPQRstUVwxYZ</code>)</i>"
    )
    if query:
        await query.message.reply_text(txt, parse_mode="HTML", reply_markup=CANCEL_KEYBOARD)
    else:
        await update.message.reply_text(txt, parse_mode="HTML", reply_markup=CANCEL_KEYBOARD)
    return BOT_TOKEN


@_owner_only
async def bot_add_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    token = update.message.text.strip()
    if ":" not in token or len(token) < 20:
        await update.message.reply_text("❌ Format token tidak valid. Token BotFather biasanya mengandung titik dua (`:`).\nSilakan kirimkan lagi:")
        return BOT_TOKEN

    bot_username = ""
    try:
        temp_app = Application.builder().token(token).build()
        await temp_app.initialize()
        bot_info = await temp_app.bot.get_me()
        bot_username = bot_info.username or ""
        await temp_app.shutdown()
    except Exception as exc:
        logger.warning("Could not verify bot token via API: %s", exc)

    context.user_data["bot_token"] = token
    context.user_data["bot_username"] = bot_username

    txt = (
        f"Token: <code>{html.escape(token[:10])}...</code>\n"
        f"Username: @{html.escape(bot_username or '-')}\n\n"
        "➕ <b>STEP 2/2: NAMA BOT PAYMENT</b>\n\n"
        "Masukkan <b>Nama Tampilan Bot</b> (contoh: <code>Bot Payment VIP 2</code>):"
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=CANCEL_KEYBOARD)
    return BOT_NAME


@_owner_only
async def bot_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_navigation(update, context) == ConversationHandler.END:
        return ConversationHandler.END

    bot_name = update.message.text.strip()
    token = context.user_data.get("bot_token")
    bot_username = context.user_data.get("bot_username", "")

    from vip_bot.config import load_config
    vip_cfg = load_config()
    store = db_store.PaymentStore(vip_cfg)
    store.upsert_payment_bot(token, bot_name, bot_username)

    # Hot-reload & jalankan bot payment baru secara instan di background!
    import vip_bot
    asyncio.create_task(vip_bot.start_payment_bot_now(token))

    txt = (
        f"🎉 <b>BOT PAYMENT BERHASIL DITAMBAHKAN & LANGSUNG AKTIF!</b>\n\n"
        f"• Nama: <b>{html.escape(bot_name)}</b>\n"
        f"• Username: @{html.escape(bot_username or '-')}\n"
        f"• Status: <b>✅ Aktif (Berjalan Saat Ini)</b>\n\n"
        f"Bot payment ini <b>langsung dinyalakan & siap melayani pembeli</b> detik ini juga tanpa perlu restart sistem."
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=get_user_keyboard(update.effective_user.id))
    context.user_data.clear()
    return ConversationHandler.END


@_owner_only
async def bot_list_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await menu_owner_payment_bots(update, context)


@_owner_only
async def bot_del_init(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    from vip_bot.config import load_config
    vip_cfg = load_config()
    store = db_store.PaymentStore(vip_cfg)
    bots = store.list_payment_bots()
    if not bots:
        txt = "Tidak ada Bot Payment tambahan untuk dihapus."
        if query:
            await query.edit_message_text(txt)
        else:
            await update.message.reply_text(txt)
        return

    btns = [
        [InlineKeyboardButton(f"❌ {b.get('bot_name') or 'Payment Bot'} (@{b.get('bot_username') or '-'})", callback_data=f"bot_del_{b['id']}")]
        for b in bots
    ]
    txt = "❌ <b>PILIH BOT PAYMENT UNTUK DINONAKTIFKAN:</b>"
    if query:
        await query.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))


@_owner_only
async def cb_bot_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot_id = query.data.replace("bot_del_", "")
    from vip_bot.config import load_config
    vip_cfg = load_config()
    store = db_store.PaymentStore(vip_cfg)
    bots = store.list_payment_bots(include_inactive=True)
    target_bot = None
    for b in bots:
        if str(b.get("id")) == str(bot_id):
            target_bot = b
            break
    if target_bot:
        store.delete_payment_bot(target_bot["bot_token"])
        await query.edit_message_text(
            f"✅ Bot Payment <b>{html.escape(target_bot.get('bot_name') or '')}</b> (@{html.escape(target_bot.get('bot_username') or '-')}) telah dinonaktifkan.",
            parse_mode="HTML",
        )
    else:
        await query.edit_message_text("❌ Bot Payment tidak ditemukan.")


# Inisialisasi peta navigasi menu utama
MENU_TEXT_MAP.update(
    {
        "📱 Daftar Akun": menu_accounts,
        "📊 Statistik": menu_stats,
        "👥 List User": menu_list_users,
        "⚙️ Edit Profil": menu_edit_profile,
        "🔄 Switch On/Off": menu_toggle,
        "ℹ️ Bantuan": cmd_help,
        "➕ Tambah Akun": add_acc_start,
        "💰 Konfirmasi Bayar": payconfirm_start,
        "🖼️ Tambah Media": addmedia_start,
        "📦 Paket VIP": menu_owner_packages,
        "🤖 Bot Payment": menu_owner_payment_bots,
        "📢 Log Chat ID": menu_owner_log_chat,
    }
)


# --- APPLICATION BUILDER ---
def build_application():
    app = Application.builder().token(MANAGE_TOKEN).build()

    fallback_handlers = [
        MessageHandler(
            filters.Regex(
                "^(❌ Batal / Kembali|📱 Daftar Akun|➕ Tambah Akun|📊 Statistik|👥 List User|⚙️ Edit Profil|🔄 Switch On/Off|💰 Konfirmasi Bayar|🖼️ Tambah Media|ℹ️ Bantuan|📦 Paket VIP|📢 Log Chat ID)$"
            ),
            check_navigation,
        ),
        CommandHandler("cancel", cancel_flow),
        CommandHandler("start", cmd_start),
    ]

    # Conversation: Tambah Akun
    conv_add_acc = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^➕ Tambah Akun$"), add_acc_start),
            CallbackQueryHandler(add_acc_start, pattern="^add_acc_init$"),
            CommandHandler("addaccount", add_acc_start),
        ],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_name)],
            ADD_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_phone)],
            ADD_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_otp)],
            ADD_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_2fa)],
            ADD_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_city)],
            ADD_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_age)],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Conversation: Edit Profil
    conv_edit_prof = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_edit_prof_field, pattern="^ep_f_"),
            CommandHandler("setprofile", menu_edit_profile),
        ],
        states={
            SET_PROF_VAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_prof_val_save)],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Conversation: Konfirmasi Bayar
    conv_payconfirm = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^💰 Konfirmasi Bayar$"), payconfirm_start),
            CallbackQueryHandler(cb_payconfirm_acc, pattern="^pc_acc_"),
            CommandHandler("payconfirm", payconfirm_start),
        ],
        states={
            CONFIRM_PAY_USER: [
                CallbackQueryHandler(cb_payconfirm_acc, pattern="^pc_acc_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, payconfirm_user_id),
            ],
            CONFIRM_PAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, payconfirm_amount)],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Conversation: Tambah Media
    conv_addmedia = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🖼️ Tambah Media$"), addmedia_start),
            CallbackQueryHandler(cb_addmedia_acc, pattern="^am_acc_"),
            CommandHandler("addmedia", addmedia_start),
        ],
        states={
            WAIT_MEDIA_FILE: [
                CallbackQueryHandler(cb_addmedia_acc, pattern="^am_acc_"),
                CallbackQueryHandler(cb_addmedia_intent, pattern="^am_int_"),
                MessageHandler((filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, addmedia_file_received),
            ],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Conversation: Owner Tambah Paket
    conv_pkg_add = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(pkg_add_start, pattern="^pkg_add_init$"),
            CommandHandler("addpackage", pkg_add_start),
        ],
        states={
            PKG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, pkg_add_name)],
            PKG_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, pkg_add_chat_id)],
            PKG_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, pkg_add_amount)],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Conversation: Owner Log Chat ID
    conv_log_chat = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📢 Log Chat ID$"), menu_owner_log_chat),
            CommandHandler("setlogchat", menu_owner_log_chat),
        ],
        states={
            SET_LOG_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_owner_log_chat)],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Conversation: Tarik Saldo Komisi
    conv_withdraw = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_withdraw_start, pattern="^wd_start$"),
            MessageHandler(filters.Regex("^💳 Tarik Saldo$"), cb_withdraw_start),
            CommandHandler("withdraw", cb_withdraw_start),
        ],
        states={
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount_received)],
            WITHDRAW_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_info_received)],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Conversation: Owner Tambah Bot Payment
    conv_bot_add = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(bot_add_init, pattern="^bot_add_init$"),
            CommandHandler("botadd", bot_add_init),
        ],
        states={
            BOT_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_add_token)],
            BOT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_add_name)],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Conversation: Owner Edit Paket VIP
    conv_pkg_edit = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(pkg_edit_init, pattern="^pkg_edit_init$"),
        ],
        states={
            PKG_EDIT_SELECT: [CallbackQueryHandler(pkg_edit_select, pattern="^pe_sel_")],
            PKG_EDIT_FIELD: [CallbackQueryHandler(pkg_edit_field, pattern="^pe_field_")],
            PKG_EDIT_VAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, pkg_edit_value)],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Handlers pendaftaran
    app.add_handler(CommandHandler("start", cmd_user_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("accounts", menu_accounts))
    app.add_handler(CommandHandler("stats", menu_stats))
    app.add_handler(CommandHandler("listusers", menu_list_users))
    app.add_handler(CommandHandler("toggle", menu_toggle))
    app.add_handler(CommandHandler("packages", menu_owner_packages))
    app.add_handler(CommandHandler("custom", cmd_custom_qris))
    app.add_handler(CommandHandler("profile", cmd_profile))

    app.add_handler(conv_add_acc)
    app.add_handler(conv_edit_prof)
    app.add_handler(conv_payconfirm)
    app.add_handler(conv_addmedia)
    app.add_handler(conv_pkg_add)
    app.add_handler(conv_pkg_edit)
    app.add_handler(conv_log_chat)
    app.add_handler(conv_withdraw)
    app.add_handler(conv_bot_add)

    # Menu Text Buttons (ReplyKeyboardMarkup)
    app.add_handler(MessageHandler(filters.Regex("^📱 Daftar Akun$"), menu_accounts))
    app.add_handler(MessageHandler(filters.Regex("^📊 Statistik$"), menu_stats))
    app.add_handler(MessageHandler(filters.Regex("^👥 List User$"), menu_list_users))
    app.add_handler(MessageHandler(filters.Regex("^⚙️ Edit Profil$"), menu_edit_profile))
    app.add_handler(MessageHandler(filters.Regex("^🔄 Switch On/Off$"), menu_toggle))
    app.add_handler(MessageHandler(filters.Regex("^📦 Paket VIP$"), menu_owner_packages))
    app.add_handler(MessageHandler(filters.Regex("^🤖 Bot Payment$"), menu_owner_payment_bots))
    app.add_handler(MessageHandler(filters.Regex("^📢 Log Chat ID$"), menu_owner_log_chat))
    app.add_handler(MessageHandler(filters.Regex("^👤 Profile & Referral$"), cmd_profile))
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Bantuan$"), cmd_help))
    app.add_handler(MessageHandler(filters.Regex("^❌ Batal / Kembali$"), cancel_flow))

    # Callback Query Handlers (Inline Buttons)
    app.add_handler(CallbackQueryHandler(menu_toggle, pattern="^menu_toggle$"))
    app.add_handler(CallbackQueryHandler(menu_edit_profile, pattern="^menu_edit_prof$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_account, pattern="^tog_"))
    app.add_handler(CallbackQueryHandler(cb_edit_prof_acc, pattern="^ep_acc_"))
    app.add_handler(CallbackQueryHandler(cb_stats_show, pattern="^st_acc_"))
    app.add_handler(CallbackQueryHandler(menu_stats, pattern="^st_back$"))
    app.add_handler(CallbackQueryHandler(cb_list_users_acc, pattern="^lu_acc_"))
    app.add_handler(CallbackQueryHandler(cb_list_users_show, pattern="^lu_st_"))
    app.add_handler(CallbackQueryHandler(pkg_menu_show, pattern="^pkg_menu_show$"))
    app.add_handler(CallbackQueryHandler(bot_menu_show, pattern="^bot_menu_show$"))
    app.add_handler(CallbackQueryHandler(cb_bot_select, pattern="^bot_sel_"))
    app.add_handler(CallbackQueryHandler(pkg_del_init, pattern="^pkg_del_init$"))
    app.add_handler(CallbackQueryHandler(cb_pkg_delete, pattern="^pkg_del_"))
    app.add_handler(CallbackQueryHandler(pkg_bind_bot, pattern="^pkg_bind_bot$"))
    app.add_handler(CallbackQueryHandler(cb_bind_bot_select_pkg, pattern="^pb_acc_"))
    app.add_handler(CallbackQueryHandler(cb_bind_bot_save, pattern="^pb_set_"))
    app.add_handler(CallbackQueryHandler(bot_list_show, pattern="^bot_list_show$"))
    app.add_handler(CallbackQueryHandler(bot_del_init, pattern="^bot_del_init$"))
    app.add_handler(CallbackQueryHandler(cb_bot_delete, pattern="^bot_del_"))
    app.add_handler(CallbackQueryHandler(cb_withdraw_admin_action, pattern="^wd_(acc|rej)_"))

    return app


async def start_manage_bot():
    if not MANAGE_TOKEN:
        logger.warning("MANAGE_BOT_TOKEN kosong, manage bot gak jalan.")
        return
    app = build_application()
    logger.info("Manage bot (ReplyKeyboardMarkup & Inline Interactive) jalan...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def run_manage_bot():
    if not MANAGE_TOKEN:
        logger.warning("MANAGE_BOT_TOKEN kosong, manage bot gak jalan.")
        return
    app = build_application()
    logger.info("Manage bot (ReplyKeyboardMarkup & Inline Interactive) jalan...")
    app.run_polling(drop_pending_updates=True, close_loop=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    run_manage_bot()
