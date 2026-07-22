"""
env_loader.py - Load .env terpusat buat telegram-chatbot.

Chatbot ada di sociabuzz-pay/telegram-chatbot/, tapi env disatukan di
sociabuzz-pay/.env (parent). Jadi load_dotenv() harus baca parent dulu,
baru local (kalau ada) sebagai override.

Pemakaian: ganti `from dotenv import load_dotenv; load_dotenv()`
menjadi `from env_loader import load_env; load_env()`.
"""
import os
from dotenv import load_dotenv

_PARENT_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
_LOCAL_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_env():
    """Load parent .env dulu (sociabuzz-pay), lalu local .env sebagai override."""
    # Parent dulu (set semua var sociabuzz-pay + SOCIABUZZ_USERNAME, dll)
    if os.path.exists(_PARENT_ENV):
        load_dotenv(_PARENT_ENV, override=False)
    # Local (chatbot/.env) override kalau ada var yg beda
    if os.path.exists(_LOCAL_ENV):
        load_dotenv(_LOCAL_ENV, override=True)
    return _PARENT_ENV
