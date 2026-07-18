"""
inspect_saved.py - Scan Saved Messages and print all media file_ids to terminal

Usage:
    python inspect_saved.py
"""
import os
import sys

# Pastikan folder root dan folder scripts ada di sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)
import asyncio
import logging

from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from telethon.tl.functions.messages import GetHistoryRequest

from env_loader import load_env
load_env()

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger("Inspect")

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION = os.getenv("TELEGRAM_SESSION", "ai_userbot_session")


def classify(text: str) -> str:
    text_lower = (text or "").lower()
    pap_kw = ["pap", "colmek", "foto", "photo", "seksi", "syur", "bugil", "cd", "bh", "tanktop"]
    vid_kw = ["video", "coly", "vcs", "rekaman", "record", "bf", "ngewe"]
    vip_kw = ["vip", "preview", "grup", "konten", "koleksi"]
    for kw in pap_kw:
        if kw in text_lower:
            return "pap"
    for kw in vid_kw:
        if kw in text_lower:
            return "video"
    for kw in vip_kw:
        if kw in text_lower:
            return "vip_preview"
    return "uncategorized"


def extract_info(msg):
    media = msg.media
    if isinstance(media, MessageMediaPhoto):
        return ("photo", media.photo.id, media.photo.access_hash, media.photo.file_reference.hex() if media.photo.file_reference else None, msg.message or "")
    elif isinstance(media, MessageMediaDocument):
        doc = media.document
        mime = doc.mime_type or ""
        typ = "video" if mime.startswith("video/") else "document"
        return (typ, doc.id, doc.access_hash, doc.file_reference.hex() if doc.file_reference else None, msg.message or "")
    return None


async def main():
    if not API_ID or not API_HASH:
        print("[!] TELEGRAM_API_ID / TELEGRAM_API_HASH belum di-set di .env")
        sys.exit(1)

    client = TelegramClient(SESSION, int(API_ID), API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"Logged in as: {me.first_name} (ID: {me.id})")
    print("=" * 70)

    from telethon.tl.types import InputPeerSelf
    offset_id = 0
    offset_date = None
    count = 0
    media_count = 0

    while True:
        history = await client(GetHistoryRequest(
            peer=InputPeerSelf(),
            offset_id=offset_id, offset_date=offset_date,
            add_offset=0, limit=100, max_id=0, min_id=0, hash=0,
        ))
        if not history.messages:
            break
        for msg in history.messages:
            count += 1
            if msg.media and isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument)):
                info = extract_info(msg)
                if info:
                    media_count += 1
                    typ, mid, access_hash, file_ref, caption = info
                    cat = classify(caption)
                    print(f"\n[{media_count}] type={typ} | category={cat}")
                    print(f"  id={mid}")
                    print(f"  access_hash={access_hash}")
                    print(f"  file_reference={file_ref}")
                    print(f"  caption='{caption[:60]}'")
        offset_id = history.messages[-1].id
        if len(history.messages) < 100:
            break

    print("\n" + "=" * 70)
    print(f"Total messages scanned: {count}")
    print(f"Total media found: {media_count}")


if __name__ == "__main__":
    asyncio.run(main())
