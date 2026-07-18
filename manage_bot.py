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
import logging
import sys
import re
from env_loader import load_env

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    PhoneNumberInvalidError,
)

# Pastikan folder ini bisa di-import (db, env_loader, dll) walau di-run dari root
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

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

import db

logger = logging.getLogger("ManageBot")
load_env()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_USER_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()
}
if OWNER_ID:
    ADMIN_USER_IDS.add(OWNER_ID)
MANAGE_TOKEN = os.getenv("MANAGE_BOT_TOKEN", "")

LOGIN_CLIENTS = {}  # user_id -> Telethon client (temporary saat alur login OTP/2FA)

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
) = range(10)

# --- Keyboards ---
MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📱 Daftar Akun", "➕ Tambah Akun"],
        ["📊 Statistik", "👥 List User"],
        ["⚙️ Edit Profil", "🔄 Switch On/Off"],
        ["💰 Konfirmasi Bayar", "🖼️ Tambah Media"],
        ["ℹ️ Bantuan"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    [["❌ Batal / Kembali"]],
    resize_keyboard=True,
)


def _admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if user_id not in ADMIN_USER_IDS:
            msg = update.message or (update.callback_query.message if update.callback_query else None)
            if msg:
                await msg.reply_text("❌ Akses ditolak. Anda bukan Admin.")
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


# --- START & CANCEL ---
@_admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    await _cleanup_login_client(user_id)
    context.user_data.clear()
    txt = (
        "🤖 *PANEL UTAMA ADMIN CHATBOT*\n\n"
        "Selamat datang! Pilih menu pada tombol keyboard di bawah untuk mengelola akun chatbot, statistik, profil, media, dan pembayaran."
    )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD)
    return ConversationHandler.END


@_admin_only
async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    await _cleanup_login_client(user_id)
    context.user_data.clear()
    await update.message.reply_text(
        "🔙 Proses dibatalkan. Kembali ke Menu Utama.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return ConversationHandler.END


@_admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "ℹ️ *PANDUAN PENGGUNAAN PANEL ADMIN*\n\n"
        "• *📱 Daftar Akun*: Menampilkan seluruh akun yang terdaftar beserta statistik ringkas.\n"
        "• *➕ Tambah Akun*: Panduan interaktif mendaftarkan akun userbot baru.\n"
        "• *📊 Statistik*: Statistik penjualan & breakdown stage user per akun.\n"
        "• *👥 List User*: Melihat daftar calon pembeli/member per akun & stage.\n"
        "• *⚙️ Edit Profil*: Mengubah Kota, Umur, Bio, atau Nama akun secara langsung.\n"
        "• *🔄 Switch On/Off*: Mengaktifkan / mematikan akun dengan 1-klik button.\n"
        "• *💰 Konfirmasi Bayar*: Konfirmasi pembayaran manual & ubah status user jadi Member.\n"
        "• *🖼️ Tambah Media*: Upload foto/video PAP / VIP Preview untuk bahan auto-reply."
    )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD)


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
        client = TelegramClient(session_file, api_id, api_hash)
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
    accs = db.list_accounts(active_only=False)
    if not accs:
        await update.message.reply_text("Belum ada akun.", reply_markup=MAIN_MENU_KEYBOARD)
        return

    btns = [[InlineKeyboardButton(f"📊 #{a['id']} {a['name']}", callback_data=f"st_acc_{a['id']}")] for a in accs]
    btns.append([InlineKeyboardButton("🌐 Semua Akun", callback_data="st_acc_all")])
    await update.message.reply_text(
        "📊 *STATISTIK PENJUALAN CHATBOT*\n\nPilih akun untuk melihat rincian statistik:",
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
    accs = db.list_accounts(active_only=False)
    if not accs:
        await update.message.reply_text("Belum ada akun.", reply_markup=MAIN_MENU_KEYBOARD)
        return

    btns = [[InlineKeyboardButton(f"👥 #{a['id']} {a['name']}", callback_data=f"lu_acc_{a['id']}")] for a in accs]
    await update.message.reply_text(
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
    accs = db.list_accounts(active_only=False)
    if not accs:
        await update.message.reply_text("Belum ada akun.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    btns = [[InlineKeyboardButton(f"#{a['id']} {a['name']}", callback_data=f"pc_acc_{a['id']}")] for a in accs]
    await update.message.reply_text(
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
    accs = db.list_accounts(active_only=False)
    if not accs:
        await update.message.reply_text("Belum ada akun.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    btns = [[InlineKeyboardButton(f"#{a['id']} {a['name']}", callback_data=f"am_acc_{a['id']}")] for a in accs]
    await update.message.reply_text(
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
    }
)


# --- APPLICATION BUILDER ---
def build_application():
    app = Application.builder().token(MANAGE_TOKEN).build()

    fallback_handlers = [
        MessageHandler(
            filters.Regex(
                "^(❌ Batal / Kembali|📱 Daftar Akun|➕ Tambah Akun|📊 Statistik|👥 List User|⚙️ Edit Profil|🔄 Switch On/Off|💰 Konfirmasi Bayar|🖼️ Tambah Media|ℹ️ Bantuan)$"
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
                CallbackQueryHandler(cb_addmedia_intent, pattern="^am_int_"),
                MessageHandler((filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, addmedia_file_received),
            ],
        },
        fallbacks=fallback_handlers,
        per_message=False,
    )

    # Handlers pendaftaran
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("accounts", menu_accounts))
    app.add_handler(CommandHandler("stats", menu_stats))
    app.add_handler(CommandHandler("listusers", menu_list_users))
    app.add_handler(CommandHandler("toggle", menu_toggle))

    app.add_handler(conv_add_acc)
    app.add_handler(conv_edit_prof)
    app.add_handler(conv_payconfirm)
    app.add_handler(conv_addmedia)

    # Menu Text Buttons (ReplyKeyboardMarkup)
    app.add_handler(MessageHandler(filters.Regex("^📱 Daftar Akun$"), menu_accounts))
    app.add_handler(MessageHandler(filters.Regex("^📊 Statistik$"), menu_stats))
    app.add_handler(MessageHandler(filters.Regex("^👥 List User$"), menu_list_users))
    app.add_handler(MessageHandler(filters.Regex("^⚙️ Edit Profil$"), menu_edit_profile))
    app.add_handler(MessageHandler(filters.Regex("^🔄 Switch On/Off$"), menu_toggle))
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

    return app


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
