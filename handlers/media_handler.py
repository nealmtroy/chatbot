"""
media_handler.py - Send media dari DB per-account berdasarkan intent.

Fungsi utama:
    detect_intent(text) -> str | None
    send_media_by_intent(client, event, user_text, account_id, user_db_id, user_name, max_history)

Media disimpan di tabel `media` (db.py) per account_id + intent.
Self-destruct timer (ttl) tetap dipertahankan biar "1x view" dan hilang.
"""
import os
import json
import random
import asyncio
import logging

from core import db, ai_engine

logger = logging.getLogger("MediaHandler")

INTENT_KEYWORDS = {
    "pap": ["pap", "colmek", "foto", "photo", "seksi", "syur", "bugil", "cd", "bh", "tanktop", "payudara", "toket"],
    "video": ["video", "coly", "colmek video", "vcs", "rekaman", "record", "bf", "ngewe", "vid"],
    "vip_preview": ["preview", "preview vip", "liat isi", "konten vip", "koleksi pribadi"],
}

# Self-destruct timer per category (seconds)
TTL_SECONDS = {
    "pap": 5,
    "video": 5,
    "vip_preview": 5,
}

# Kategori yg perlu follow-up AI setelah kirim media
MEDIA_FOLLOWUP_INTENTS = {
    "pap": True,
    "video": False,
    "vip_preview": False,
}


def detect_intent(text: str) -> str | None:
    """Detect media intent dari user text. Priority: video > vip_preview > pap."""
    text_lower = (text or "").lower()
    for intent in ["video", "vip_preview", "pap"]:
        for kw in INTENT_KEYWORDS.get(intent, []):
            if kw in text_lower:
                return intent
    return None


def build_input_media(entry: dict):
    """Build Telethon InputPhoto / InputDocument dari row media DB."""
    from telethon.tl.types import InputPhoto, InputDocument
    if entry["media_type"] == "photo":
        if not entry.get("access_hash"):
            return None
        return InputPhoto(
            id=entry["tg_id"],
            access_hash=entry["access_hash"],
            file_reference=bytes.fromhex(entry["file_reference"]) if entry.get("file_reference") else b"",
        )
    elif entry["media_type"] in ("video", "document"):
        if not entry.get("access_hash"):
            return None
        return InputDocument(
            id=entry["tg_id"],
            access_hash=entry["access_hash"],
            file_reference=bytes.fromhex(entry["file_reference"]) if entry.get("file_reference") else b"",
        )
    return None


async def send_media_by_intent(client, event, user_text: str, account_id: int = 0,
                                user_db_id: int = None, user_name: str = "",
                                max_history: int = 20) -> bool:
    """Detect intent, kirim 1 media random per account, return True kalau terkirim."""
    intent = detect_intent(user_text)
    if not intent:
        return False

    entry = db.get_random_media(account_id, intent)
    if not entry:
        await event.respond("dih kakak, stoknya lagi kosong nih wkwk nanti aku tambahin ya 🤭")
        return False

    input_media = build_input_media(entry)
    if not input_media:
        logger.error("Cannot build input media for entry: %s", entry)
        return False

    try:
        ttl = TTL_SECONDS.get(intent, 5)
        await client.send_file(
            event.chat_id,
            input_media,
            caption=entry.get("caption", "") or None,
            ttl=ttl,
        )
        logger.info("[acc %s] Sent %s media (id=%s, ttl=%ss)", account_id, intent, entry["tg_id"], ttl)

        if MEDIA_FOLLOWUP_INTENTS.get(intent):
            await asyncio.sleep(1.0)
            try:
                from db import get_account
                account = get_account(account_id) or {"id": account_id, "name": "", "persona_file": "prompts/persona.txt"}
                followup = await ai_engine.generate_media_followup(
                    account, user_db_id, user_name, user_text, max_history, intent,
                )
                if followup:
                    await event.respond(followup)
                    logger.info("[acc %s] follow-up: %s", account_id, followup[:60])
            except Exception as fu_err:
                logger.error("follow-up gagal: %s", fu_err)

        return True
    except Exception as e:
        logger.error("gagal kirim media: %s", e)
        return False
