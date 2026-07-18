"""
db.py - Persistent SQLite layer untuk telegram-chatbot multi-account.

Menggantikan penyimpanan JSON flat (chat_history.json, knowledge.json,
corrections.json, media_config.json) dengan skema relasional yang aman untuk
multi-account & multi-client concurrency.

Skema utama:
  - accounts      : registry tiap personel (Alya, Intan, Vanya, ...)
  - users         : profil calon pembeli per-account (CRM/stage tracking)
  - messages       : history chat per (account, user) — pengganti chat_history.json
  - corrections   : koreksi owner (fitur .revisi) — pengganti corrections.json
  - media         : katalog media per intent per-account — pengganti media_config.json
  - knowledge     : fakta RAG per-account (opsional, tetap bisa pakai knowledge.json)

Semua akses DB di-guard dengan connection per-thread (sqlite3 bukan async-safe
default), sehingga aman dipakai dari berbagai coroutine Telethon.
"""
import os
import json
import sqlite3
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger("DB")

DB_FILE = os.getenv("DB_FILE", "chatbot.db")

_local = threading.local()


def get_conn():
    """Return sqlite connection untuk thread ini (cached)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")      # concurrency read/write
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db():
    """Buat tabel jika belum ada. Idempoten."""
    conn = get_conn()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,                 -- "Alya", "Intan", ...
            session_file TEXT NOT NULL UNIQUE,         -- path ke .session Telethon
            api_id      INTEGER NOT NULL,
            api_hash    TEXT NOT NULL,
            persona_file TEXT NOT NULL DEFAULT 'prompts/persona.txt',
            knowledge_file TEXT NOT NULL DEFAULT 'knowledge.json',
            city        TEXT DEFAULT '',
            age         INTEGER DEFAULT NULL,
            bio         TEXT DEFAULT '',
            vip_chat_id TEXT DEFAULT '',               -- chat/group VIP tujuan invite (place QRIS paid -> invite)
            vip_price   INTEGER DEFAULT 50000,         -- harga VIP per account (Rp)
            active      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            tg_user_id      INTEGER NOT NULL,
            first_name      TEXT DEFAULT '',
            username        TEXT DEFAULT '',
            name            TEXT DEFAULT '',          -- profil yg diekstrak AI
            age             INTEGER DEFAULT NULL,
            city            TEXT DEFAULT '',
            stage           TEXT NOT NULL DEFAULT 'new',  -- lihat STAGES
            interested      INTEGER NOT NULL DEFAULT 0,
            total_spent     INTEGER NOT NULL DEFAULT 0,
            tags            TEXT DEFAULT '',          -- csv
            first_seen      TEXT NOT NULL,
            last_seen       TEXT NOT NULL,
            note            TEXT DEFAULT '',
            UNIQUE(account_id, tg_user_id),
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            role        TEXT NOT NULL,                -- 'user' | 'assistant'
            content     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS corrections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL DEFAULT 0,   -- 0 = global
            user_text   TEXT NOT NULL,
            assistant_text TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS media (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL,
            intent      TEXT NOT NULL,                -- 'pap' | 'video' | 'vip_preview'
            media_type  TEXT NOT NULL,                -- 'photo' | 'video' | 'document'
            tg_id       INTEGER NOT NULL,
            access_hash INTEGER NOT NULL,
            file_reference TEXT NOT NULL DEFAULT '',
            caption     TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS knowledge (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL DEFAULT 0,
            keywords    TEXT NOT NULL,
            fact        TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS payments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,             -- akun chatbot (Alya/Intan)
            user_id         INTEGER NOT NULL,             -- row di tabel users
            tg_user_id      INTEGER NOT NULL,             -- telegram user id pembeli
            package_code    TEXT DEFAULT 'vip',
            amount          INTEGER NOT NULL,             -- nominal QRIS (Rp)
            socia_inv_id    TEXT DEFAULT '',              -- invoice id dari SociaBuzz
            qris_chat_id    TEXT DEFAULT '',              -- chat tempat QRIS dikirim
            qris_message_id TEXT DEFAULT '',              -- message id QRIS (biar bisa dihapus)
            status          TEXT NOT NULL DEFAULT 'pending',  -- pending|paid|expired|failed
            invite_link     TEXT DEFAULT '',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_users_account ON users(account_id);
        CREATE INDEX IF NOT EXISTS idx_users_tgid ON users(tg_user_id);
        CREATE INDEX IF NOT EXISTS idx_messages_account_user ON messages(account_id, user_id);
        CREATE INDEX IF NOT EXISTS idx_media_account_intent ON media(account_id, intent);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(tg_user_id, status);
        """
    )
    conn.commit()
    logger.info("DB siap: %s", DB_FILE)


# ---------------------------------------------------------------------------
# STAGE machine constants
# ---------------------------------------------------------------------------
STAGES = [
    "new",            # belum pernah chat / greeting
    "greeted",        # udah sapa, warming up
    "interested",     # nanya isi / minta pap / penasaran
    "asked_price",    # nanya harga VIP/VCS
    "payment_pending",# dikasih QRIS, nunggu bayar
    "member",         # udah join / bayar VIP
    "vcs_offered",    # udah ditawarin VCS
    "vcs_booked",     # VCS terbooking
    "lost",           # ghost / scam-accuse / block
]
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# ACCOUNTS
# ---------------------------------------------------------------------------
def add_account(name, session_file, api_id, api_hash, persona_file="prompts/persona.txt",
                knowledge_file="knowledge.json", city="", age=None, bio="", active=1):
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO accounts (name, session_file, api_id, api_hash, persona_file,
                                  knowledge_file, city, age, bio, active, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (name, session_file, api_id, api_hash, persona_file, knowledge_file,
         city, age, bio, active, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_account(account_id):
    row = get_conn().execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return dict(row) if row else None


def get_account_by_name(name):
    row = get_conn().execute("SELECT * FROM accounts WHERE name LIKE ?", (name,)).fetchone()
    return dict(row) if row else None


def list_accounts(active_only=True):
    q = "SELECT * FROM accounts"
    if active_only:
        q += " WHERE active=1"
    rows = get_conn().execute(q).fetchall()
    return [dict(r) for r in rows]


def set_account_profile(account_id, city=None, age=None, bio=None, name=None):
    sets, vals = [], []
    if city is not None:
        sets.append("city=?"); vals.append(city)
    if age is not None:
        sets.append("age=?"); vals.append(age)
    if bio is not None:
        sets.append("bio=?"); vals.append(bio)
    if name is not None:
        sets.append("name=?"); vals.append(name)
    if not sets:
        return
    vals.append(account_id)
    get_conn().execute(f"UPDATE accounts SET {','.join(sets)} WHERE id=?", vals)
    get_conn().commit()


# ---------------------------------------------------------------------------
# USERS (CRM / stage tracking)
# ---------------------------------------------------------------------------
def get_or_create_user(account_id, tg_user_id, first_name="", username=""):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE account_id=? AND tg_user_id=?",
        (account_id, tg_user_id),
    ).fetchone()
    if row:
        return dict(row)
    now = _now()
    cur = conn.execute(
        """INSERT INTO users (account_id, tg_user_id, first_name, username, first_seen, last_seen)
           VALUES (?,?,?,?,?,?)""",
        (account_id, tg_user_id, first_name, username, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def update_user(user_id, **fields):
    allowed = {"name", "age", "city", "stage", "interested", "total_spent",
               "tags", "note", "first_name", "username"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    sets.append("last_seen=?")
    vals.append(_now())
    vals.append(user_id)
    get_conn().execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", vals)
    get_conn().commit()


def advance_stage(user_id, new_stage):
    """Naikkan stage hanya kalau lebih maju (gak mundur otomatis)."""
    u = get_conn().execute("SELECT stage FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        return
    cur = u["stage"]
    if STAGE_ORDER.get(new_stage, 0) > STAGE_ORDER.get(cur, 0):
        update_user(user_id, stage=new_stage)
        logger.info("user %s stage %s -> %s", user_id, cur, new_stage)


def add_spent(user_id, amount):
    u = get_conn().execute("SELECT total_spent FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        return
    update_user(user_id, total_spent=u["total_spent"] + amount)


def get_user(user_id):
    row = get_conn().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_users(account_id, stage=None, limit=50):
    q = "SELECT * FROM users WHERE account_id=?"
    vals = [account_id]
    if stage:
        q += " AND stage=?"
        vals.append(stage)
    q += " ORDER BY last_seen DESC LIMIT ?"
    vals.append(limit)
    rows = get_conn().execute(q, vals).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# MESSAGES (history)
# ---------------------------------------------------------------------------
def add_message(account_id, user_id, role, content):
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (account_id, user_id, role, content, created_at) VALUES (?,?,?,?,?)",
        (account_id, user_id, role, content, _now()),
    )
    conn.commit()


def get_history(user_id, limit=20):
    """Ambil history chat (role,content) untuk 1 user, paling baru di akhir."""
    rows = get_conn().execute(
        """SELECT role, content FROM (
             SELECT role, content, id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?
           ) sub ORDER BY id ASC""",
        (user_id, limit * 2),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def evict_history(user_id, keep=20):
    """Hapus pesan lama tiap user biar DB gak bengkak (simpan keep*2 terakhir)."""
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) c FROM messages WHERE user_id=?", (user_id,)).fetchone()["c"]
    if count > keep * 2:
        to_del = conn.execute(
            "SELECT id FROM messages WHERE user_id=? ORDER BY id ASC LIMIT ?",
            (user_id, count - keep * 2),
        ).fetchall()
        ids = [r["id"] for r in to_del]
        conn.executemany("DELETE FROM messages WHERE id=?", [(i,) for i in ids])
        conn.commit()


# ---------------------------------------------------------------------------
# CORRECTIONS
# ---------------------------------------------------------------------------
def add_correction(account_id, user_text, assistant_text):
    conn = get_conn()
    conn.execute(
        "INSERT INTO corrections (account_id, user_text, assistant_text, created_at) VALUES (?,?,?,?)",
        (account_id, user_text, assistant_text, _now()),
    )
    conn.commit()


def get_corrections(account_id, limit=15):
    rows = get_conn().execute(
        """SELECT user_text, assistant_text FROM corrections
           WHERE account_id=? OR account_id=0
           ORDER BY id DESC LIMIT ?""",
        (account_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# MEDIA
# ---------------------------------------------------------------------------
def add_media(account_id, intent, media_type, tg_id, access_hash, file_reference="", caption=""):
    cur = get_conn().execute(
        """INSERT INTO media (account_id, intent, media_type, tg_id, access_hash, file_reference, caption, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (account_id, intent, media_type, tg_id, access_hash, file_reference, caption, _now()),
    )
    get_conn().commit()
    return cur.lastrowid


def get_random_media(account_id, intent):
    rows = get_conn().execute(
        "SELECT * FROM media WHERE account_id=? AND intent=?", (account_id, intent)
    ).fetchall()
    if not rows:
        return None
    import random
    return dict(random.choice(rows))


def count_media(account_id, intent=None):
    if intent:
        return get_conn().execute(
            "SELECT COUNT(*) c FROM media WHERE account_id=? AND intent=?", (account_id, intent)
        ).fetchone()["c"]
    return get_conn().execute(
        "SELECT COUNT(*) c FROM media WHERE account_id=?", (account_id,)
    ).fetchone()["c"]


# ---------------------------------------------------------------------------
# KNOWLEDGE
# ---------------------------------------------------------------------------
def add_knowledge(account_id, keywords, fact):
    get_conn().execute(
        "INSERT INTO knowledge (account_id, keywords, fact) VALUES (?,?,?)",
        (account_id, keywords, fact),
    )
    get_conn().commit()


def get_knowledge(account_id):
    rows = get_conn().execute(
        "SELECT keywords, fact FROM knowledge WHERE account_id=? OR account_id=0",
        (account_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # keywords disimpan sebagai TEXT di DB, perlu di-parse ke list
        if isinstance(d.get("keywords"), str):
            d["keywords"] = [k.strip() for k in d["keywords"].split(",") if k.strip()]
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# PAYMENTS (QRIS VIP - integrasi sociabuzz-pay)
# ---------------------------------------------------------------------------
def add_payment(account_id, user_id, tg_user_id, amount, package_code="vip",
                socia_inv_id="", qris_chat_id="", qris_message_id=""):
    now = _now()
    cur = get_conn().execute(
        """INSERT INTO payments (account_id, user_id, tg_user_id, package_code,
                                 amount, socia_inv_id, qris_chat_id, qris_message_id,
                                 status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (account_id, user_id, tg_user_id, package_code, amount, socia_inv_id,
         qris_chat_id, qris_message_id, "pending", now, now),
    )
    get_conn().commit()
    return cur.lastrowid


def get_payment(payment_id):
    row = get_conn().execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    return dict(row) if row else None


def active_payment_for_user(tg_user_id):
    """Cari payment pending terbaru untuk 1 user (biar gak bikin QRIS dobel)."""
    row = get_conn().execute(
        "SELECT * FROM payments WHERE tg_user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
        (tg_user_id,),
    ).fetchone()
    return dict(row) if row else None


def pending_payments(limit=50):
    rows = get_conn().execute(
        "SELECT * FROM payments WHERE status='pending' ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_payment(payment_id, **fields):
    allowed = {"status", "socia_inv_id", "qris_chat_id", "qris_message_id",
               "invite_link", "amount", "package_code"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(payment_id)
    get_conn().execute(f"UPDATE payments SET {','.join(sets)} WHERE id=?", vals)
    get_conn().commit()


# ---------------------------------------------------------------------------
# MIGRASI dari JSON lama (best-effort, idempoten)
# ---------------------------------------------------------------------------
def migrate_from_json_legacy():
    """
    Pindahkan data lama ke schema baru.
    - chat_history.json -> messages + users (account default = 1 / Alya)
    - corrections.json  -> corrections(account_id=account_default)
    - media_config.json -> media(account_id=account_default)
    Idempoten: cek dulu kalau sudah ada isi, skip.
    """
    conn = get_conn()
    # Pastikan account default ada
    acc = get_conn().execute("SELECT * FROM accounts WHERE name LIKE 'Alya'").fetchone()
    if not acc:
        # ambil creds dari env
        import os as _os
        from dotenv import load_dotenv as _ld
        _ld()
        api_id = int(_os.getenv("TELEGRAM_API_ID", "0"))
        api_hash = _os.getenv("TELEGRAM_API_HASH", "")
        acc_id = add_account(
            name="Alya", session_file="ai_userbot_session",
            api_id=api_id, api_hash=api_hash,
            city="Bandung", age=21, bio="mahasiswi dkv",
        )
    else:
        acc_id = acc["id"]

    # chat_history.json
    if os.path.exists("chat_history.json"):
        existing = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        if existing == 0:
            with open("chat_history.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            for tg_uid, msgs in data.items():
                try:
                    tg_uid = int(tg_uid)
                except ValueError:
                    continue
                u = get_or_create_user(acc_id, tg_uid)
                for m in msgs:
                    add_message(acc_id, u["id"], m.get("role", "user"), m.get("content", ""))
            logger.info("Migrasi chat_history.json selesai (%d user)", len(data))

    # corrections.json
    if os.path.exists("corrections.json"):
        existing = conn.execute("SELECT COUNT(*) c FROM corrections").fetchone()["c"]
        if existing == 0:
            with open("corrections.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            for c in data:
                add_correction(acc_id, c.get("user", ""), c.get("assistant", ""))
            logger.info("Migrasi corrections.json selesai")

    # media_config.json
    if os.path.exists("media_config.json"):
        existing = conn.execute("SELECT COUNT(*) c FROM media WHERE account_id=?", (acc_id,)).fetchone()["c"]
        if existing == 0:
            with open("media_config.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            for intent, items in data.items():
                for it in items:
                    add_media(
                        acc_id, intent, it.get("type", "photo"),
                        it.get("id", 0), it.get("access_hash", 0),
                        it.get("file_reference", ""), it.get("caption", ""),
                    )
            logger.info("Migrasi media_config.json selesai")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    migrate_from_json_legacy()
    print("accounts:", len(list_accounts()))
