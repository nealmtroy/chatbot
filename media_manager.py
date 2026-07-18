"""
media_manager.py - Scan Saved Messages, extract Telegram file_id, save to media_config.json

Usage:
    python media_manager.py                      # scan all saved messages, auto-classify by caption
    python media_manager.py --limit 100          # scan last 100 messages
    python media_manager.py --dry-run            # print without saving
    python media_manager.py --set-cat <id> <cat> # manually set category for a file_id
                                                 # cat: pap | video | vip_preview

Classification logic (auto):
    - Caption contains keyword → category
    - Default: pap

Self-destruct: media sent via media_handler uses ttl_seconds (Telegram auto-delete timer)
    - pap: ttl_seconds = 5 (1x liat, langsung hilang)
    - vip_preview: ttl_seconds = 5 (preview, langsung hilang)
"""
import os
import sys
import json
import asyncio
import logging
from datetime import datetime

from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from telethon.tl.functions.messages import GetHistoryRequest

# Load env
from env_loader import load_env
load_env()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MediaManager")

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION = os.getenv("TELEGRAM_SESSION", "ai_userbot_session")

MEDIA_CONFIG = "media_config.json"

# Classification keywords (auto)
CATEGORY_KEYWORDS = {
    "pap": ["pap", "colmek", "foto", "photo", "seksi", "syur", "bugil", "cd", "bh", "tanktop"],
    "video": ["video", "coly", "colmek video", "vcs", "rekaman", "record", "bf", "ngewe"],
    "vip_preview": ["vip", "preview", "grup vip", "konten vip", "koleksi"],
}

# Self-destruct timer per category (seconds) — Telegram auto-delete
TTL_SECONDS = {
    "pap": 5,
    "video": 5,
    "vip_preview": 5,
}


def classify(text: str) -> str:
    """Classify media into category based on caption/text."""
    text_lower = (text or "").lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return category
    return "pap"  # default


def extract_file_id(message):
    """Extract Telegram file_id from a message with media."""
    media = message.media
    if isinstance(media, MessageMediaPhoto):
        photo = media.photo
        return {
            "type": "photo",
            "id": photo.id,
            "access_hash": photo.access_hash,
            "file_reference": photo.file_reference.hex() if photo.file_reference else None,
            "caption": message.message or "",
        }
    elif isinstance(media, MessageMediaDocument):
        doc = media.document
        is_video = doc.mime_type.startswith("video/")
        return {
            "type": "video" if is_video else "document",
            "id": doc.id,
            "access_hash": doc.access_hash,
            "file_reference": doc.file_reference.hex() if doc.file_reference else None,
            "mime_type": doc.mime_type,
            "caption": message.message or "",
        }
    return None


async def scan_saved_messages(client, limit=None, dry_run=False):
    """Scan Saved Messages (InputPeerSelf) and extract media."""
    from telethon.tl.types import InputPeerSelf

    logger.info("Scanning Saved Messages...")
    me = await client.get_me()
    logger.info(f"Logged in as: {me.first_name} (ID: {me.id})")

    all_messages = []
    offset_id = 0
    offset_date = None
    limit_per_req = 100

    while True:
        if limit and len(all_messages) >= limit:
            break
        history = await client(GetHistoryRequest(
            peer=InputPeerSelf(),
            offset_id=offset_id,
            offset_date=offset_date,
            add_offset=0,
            limit=limit_per_req,
            max_id=0,
            min_id=0,
            hash=0,
        ))
        if not history.messages:
            break
        for msg in history.messages:
            all_messages.append(msg)
            if limit and len(all_messages) >= limit:
                break
        offset_id = history.messages[-1].id
        if len(history.messages) < limit_per_req:
            break

    logger.info(f"Fetched {len(all_messages)} messages from Saved Messages")

    # Process media
    media_entries = []
    for msg in all_messages:
        if msg.media and (isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument))):
            entry = extract_file_id(msg)
            if entry:
                entry["category"] = classify(entry["caption"])
                entry["date"] = msg.date.isoformat() if msg.date else None
                media_entries.append(entry)

    logger.info(f"Found {len(media_entries)} media entries")

    # Group by category
    result = {"pap": [], "video": [], "vip_preview": []}
    for entry in media_entries:
        cat = entry["category"]
        if cat in result:
            result[cat].append({
                "type": entry["type"],
                "id": entry["id"],
                "access_hash": entry["access_hash"],
                "file_reference": entry["file_reference"],
                "mime_type": entry.get("mime_type"),
                "caption": entry["caption"],
                "date": entry["date"],
            })

    if dry_run:
        logger.info("[DRY RUN] Would save:")
        for cat, items in result.items():
            logger.info(f"  {cat}: {len(items)} items")
        return result

    # Merge with existing (avoid duplicates by id)
    existing = load_config()
    for cat in result:
        existing_ids = {item["id"] for item in existing.get(cat, [])}
        for item in result[cat]:
            if item["id"] not in existing_ids:
                existing.setdefault(cat, []).append(item)
                existing_ids.add(item["id"])

    save_config(existing)
    logger.info(f"Saved to {MEDIA_CONFIG}")
    for cat, items in existing.items():
        logger.info(f"  {cat}: {len(items)} total items")
    return existing


def load_config():
    if os.path.exists(MEDIA_CONFIG):
        with open(MEDIA_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"pap": [], "video": [], "vip_preview": []}


def save_config(data):
    with open(MEDIA_CONFIG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def set_category_for_id(file_id: int, category: str):
    """Manually set category for a file_id (from inspect_saved output)."""
    if category not in ("pap", "video", "vip_preview"):
        logger.error(f"Invalid category: {category}. Use pap/video/vip_preview")
        return False
    config = load_config()
    # Search all categories for this id
    found = None
    for cat in config:
        for item in config[cat]:
            if item["id"] == file_id:
                found = item
                break
        if found:
            break
    if not found:
        logger.error(f"file_id {file_id} not found in any category. Run media_manager.py first.")
        return False
    # Remove from old category
    for cat in config:
        config[cat] = [i for i in config[cat] if i["id"] != file_id]
    # Add to new category
    config.setdefault(category, []).append(found)
    save_config(config)
    logger.info(f"Moved file_id {file_id} → {category}")
    return True


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--set-cat", nargs=2, metavar=("ID", "CAT"),
                        help="Set category for file_id. CAT: pap|video|vip_preview")
    args = parser.parse_args()

    if not API_ID or not API_HASH:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")
        sys.exit(1)

    client = TelegramClient(SESSION, int(API_ID), API_HASH)
    await client.start()
    try:
        if args.set_cat:
            file_id = int(args.set_cat[0])
            category = args.set_cat[1]
            # Need to load config first (scan if empty)
            if not os.path.exists(MEDIA_CONFIG):
                logger.info("media_config.json empty, scanning first...")
                await scan_saved_messages(client, dry_run=False)
            set_category_for_id(file_id, category)
        else:
            await scan_saved_messages(client, limit=args.limit, dry_run=args.dry_run)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
