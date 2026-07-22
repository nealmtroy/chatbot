"""
user_tracker.py - Stage machine & profile enrichment per-account.

Tugas:
  1. Deteksi stage percakapan dari teks user (rule-based, ringan, no API).
     Stage menentukan seberapa agresif AI harus push jualan VIP/VCS.
  2. Ekstrak profil user (nama/umur/kota) dari chat — rule-based + helper
     buat ekstrak via AI (dipanggil dari ai_engine kalau perlu).
  3. Simpan ke DB (db.py) supaya lintas sesi/restart tetap ingat.

Sales funnel (stage) — makin ke kanan makin "panas":
  new            -> baru chat
  greeted        -> udah sapa
  interested     -> minta pap / nanya isi / penasaran
  asked_price    -> nanya harga
  payment_pending-> dikasih QRIS, nunggu bayar
  member         -> udah bayar / join VIP
  vcs_offered    -> ditawarin VCS
  vcs_booked     -> VCS booked
  lost           -> ghost / tuduh scam / blokir
"""
import re
import logging
from . import db

logger = logging.getLogger("UserTracker")


def update_stage_from_message(account_id, user_db_id, text: str):
    """
    Legacy fallback — hanya membaca stage dari DB.

    Stage detection utama sekarang ditangani oleh StageAgent (LLM-powered)
    di dalam DigitalClonePipeline. Fungsi ini tetap ada untuk backward
    compatibility dengan code path yang tidak lewat pipeline (misal media path).
    """
    u = db.get_user(user_db_id)
    return u["stage"] if u else "new"


# ---------------------------------------------------------------------------
# Profile enrichment (rule-based)
# ---------------------------------------------------------------------------
_AGE_RE = re.compile(r"\b(umur|usia|berapa tahun)\b.*?(\d{2})\b|\b(\d{2})\s*(tahun|thn)\b")
_CITY_PATTERNS = {
    "jakarta": r"\b(jakarta|jkt|bekasi|tangerang|depok|bogor|jabodetabek)\b",
    "bandung": r"\b(bandung|bdg)\b",
    "surabaya": r"\b(surabaya|sby)\b",
    "semarang": r"\b(semarang|smg)\b",
    "yogyakarta": r"\b(jogja|yogyakarta|jogja)\b",
    "medan": r"\b(medan)\b",
    "makassar": r"\b(makassar|mks)\b",
    "surakarta": r"\b(solo|surakarta)\b",
    "denpasar": r"\b(bali|denpasar)\b",
}

# Template kalimat perkenalan user: "aku budi, umur 20, dari jakarta"
_INTRO_RE = re.compile(
    r"(?:aku|gue|gua|saya|name is|nama)\s*[:]?\s*([a-z]{2,20})"
    r"(?:[,\s]+umur|\s*,\s*(\d{2})\s*(?:tahun|thn)?)?"
    r"(?:[,\s]+(?:dari|asli|tinggal)\s*([a-z ]{2,25}))?",
    re.IGNORECASE,
)


def extract_profile(text: str, current: dict) -> dict:
    """Ekstrak profil (name/age/city) dari 1 pesan user. Return dict field yg berubah."""
    changes = {}
    t = text or ""

    # Umur
    m = _AGE_RE.search(t.lower())
    if m:
        age = m.group(2) or m.group(3)
        if age:
            try:
                age_i = int(age)
                if 12 <= age_i <= 99:
                    changes["age"] = age_i
            except ValueError:
                pass

    # Kota
    for city, pat in _CITY_PATTERNS.items():
        if re.search(pat, t.lower()):
            changes["city"] = city
            break

    # Nama (hanya kalau belum ada & bentuk perkenalan)
    intro = _INTRO_RE.search(t)
    if intro and not current.get("name"):
        nm = intro.group(1)
        if nm and nm.lower() not in ("aku", "gue", "gua", "saya", "kamu", "lu", "lo"):
            changes["name"] = nm.capitalize()

    return changes


def enrich_from_message(user_db_id, text: str):
    """Update profil user dari teks, return dict perubahan."""
    u = db.get_user(user_db_id)
    if not u:
        return {}
    changes = extract_profile(text, u)
    if changes:
        db.update_user(user_db_id, **changes)
        logger.info("user %s profil update: %s", user_db_id, changes)
    return changes


# ---------------------------------------------------------------------------
# Helper buat AI extraction (dipanggil ai_engine bila perlu)
# ---------------------------------------------------------------------------
def profile_summary(user_db_id) -> str:
    u = db.get_user(user_db_id)
    if not u:
        return ""
    parts = []
    if u.get("name"):
        parts.append(f"nama: {u['name']}")
    if u.get("age"):
        parts.append(f"umur: {u['age']}")
    if u.get("city"):
        parts.append(f"kota: {u['city']}")
    if u.get("stage"):
        parts.append(f"stage: {u['stage']}")
    if u.get("total_spent"):
        parts.append(f"udah belanjah: Rp{u['total_spent']}")
    return "; ".join(parts)


if __name__ == "__main__":
    import os as _os
    db.DB_FILE = _os.getenv("DB_FILE", "test_chatbot.db")
    db.init_db()
    acc = db.list_accounts()[0]
    u = db.get_or_create_user(acc["id"], 999)
    print("stage:", update_stage_from_message(acc["id"], u["id"], "berapa harga vip nya?"))
    print("profile:", enrich_from_message(u["id"], "aku rudi umur 22 dari jakarta"))
    print("summary:", profile_summary(u["id"]))
