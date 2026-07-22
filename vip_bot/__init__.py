import asyncio
import logging
from telethon import TelegramClient
from vip_bot.config import load_config
from vip_bot.db_store import PaymentStore
from vip_bot.helpers import send_log
from vip_bot.loops import polling_loop, broadcast_loop
from vip_bot.handlers import register_handlers

LOGGER = logging.getLogger("telegram_vip_bot")


_GLOBAL_QRIS_SEMAPHORE = None
_GLOBAL_USER_LOCKS = {}
_GLOBAL_WITHDRAWAL_STATES = {}
_RUNNING_BOT_TOKENS = set()


async def start_single_bot(bot_token, session_name, config, store, qris_semaphore, user_locks, withdrawal_states):
    from core.utils import get_session_path
    session_path = get_session_path(session_name)
    client = TelegramClient(session_path, config.api_id, config.api_hash)
    register_handlers(client, config, store, qris_semaphore, user_locks, withdrawal_states)
    try:
        await client.start(bot_token=bot_token)
        me = await client.get_me()
        bot_username = getattr(me, "username", "") or ""
        bot_name = getattr(me, "first_name", "") or bot_username
        if bot_username:
            store.upsert_payment_bot(bot_token, bot_name, bot_username)
        await send_log(client, config, store, f"<b>VIP Payment Bot @{bot_username} started</b>")
        LOGGER.info("VIP Payment Bot @%s started successfully!", bot_username)
        asyncio.create_task(polling_loop(client, config, store))
        asyncio.create_task(broadcast_loop(client, config, store))
        await client.run_until_disconnected()
    except Exception as exc:
        LOGGER.error("Failed to run payment bot session '%s': %s", session_name, exc)


async def start_payment_bot_now(bot_token: str):
    token = str(bot_token).strip()
    if not token or token in _RUNNING_BOT_TOKENS:
        return
    _RUNNING_BOT_TOKENS.add(token)
    config = load_config()
    store = PaymentStore(config)
    global _GLOBAL_QRIS_SEMAPHORE
    if _GLOBAL_QRIS_SEMAPHORE is None:
        _GLOBAL_QRIS_SEMAPHORE = asyncio.Semaphore(config.qris_create_concurrency)
    session_name = f"vip_bot_{abs(hash(token)) % 10000}"
    LOGGER.info("Hot-reloading & launching new VIP Payment Bot instantly: %s...", token[:10])
    await start_single_bot(
        token, session_name, config, store, _GLOBAL_QRIS_SEMAPHORE, _GLOBAL_USER_LOCKS, _GLOBAL_WITHDRAWAL_STATES
    )


async def start_bot():
    config = load_config()
    store = PaymentStore(config)
    global _GLOBAL_QRIS_SEMAPHORE
    if _GLOBAL_QRIS_SEMAPHORE is None:
        _GLOBAL_QRIS_SEMAPHORE = asyncio.Semaphore(config.qris_create_concurrency)

    bot_tokens = set()
    if config.bot_token:
        bot_tokens.add(config.bot_token.strip())

    try:
        db_bots = store.list_payment_bots()
        for b in db_bots:
            if b.get("bot_token"):
                bot_tokens.add(b["bot_token"].strip())
    except Exception as exc:
        LOGGER.warning("Could not fetch payment bots from DB: %s", exc)

    if not bot_tokens:
        LOGGER.warning("No payment bot tokens found in config or database.")
        return

    LOGGER.info("Starting %d Multi-Bot Payment Client(s)...", len(bot_tokens))
    tasks = []
    for idx, token in enumerate(bot_tokens):
        if token in _RUNNING_BOT_TOKENS:
            continue
        _RUNNING_BOT_TOKENS.add(token)
        session_name = "vip_bot" if idx == 0 else f"vip_bot_{idx}"
        task = asyncio.create_task(
            start_single_bot(token, session_name, config, store, _GLOBAL_QRIS_SEMAPHORE, _GLOBAL_USER_LOCKS, _GLOBAL_WITHDRAWAL_STATES)
        )
        tasks.append(task)

    if tasks:
        await asyncio.gather(*tasks)


def run():
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        pass
