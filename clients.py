"""
clients.py - Inisialisasi client AI (Groq / OpenRouter) secara terpusat.

Modul ini menangani pembuatan client dan pemilihan model berdasarkan
variabel env SELECTED_PROVIDER. Dipakai bersama oleh ai_engine, reviewer,
tester, dan simulator agar logika provider tidak diduplikasi di 4 file.
"""
import os
import sys
import logging
from env_loader import load_env

load_env()

logger = logging.getLogger("AI-Clients")

SELECTED_PROVIDER = os.getenv("SELECTED_PROVIDER", "GROQ").upper()

# Nilai default model
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3-32b")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")

# Client dan model aktif (diekspos ke modul lain)
client = None
active_model = None
error_message = None


def _init_groq():
    global client, active_model, error_message
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        error_message = "GROQ_API_KEY tidak ditemukan di file .env!"
        logger.error(error_message)
        return
    from groq import AsyncGroq
    client = AsyncGroq(api_key=api_key)
    active_model = GROQ_MODEL
    logger.info(f"Menggunakan Groq dengan model: {active_model}")


def _init_openrouter():
    global client, active_model, error_message
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        error_message = "OPENROUTER_API_KEY tidak ditemukan di file .env!"
        logger.error(error_message)
        return
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    active_model = OPENROUTER_MODEL
    logger.info(f"Menggunakan OpenRouter dengan model: {active_model}")


def init():
    """Inisialisasi client berdasarkan SELECTED_PROVIDER. Panggil sekali di startup."""
    if SELECTED_PROVIDER == "OPENROUTER":
        _init_openrouter()
    else:
        _init_groq()


def require_client():
    """Cek apakah client sudah siap. Keluar dengan pesan error bila gagal."""
    if client is None:
        msg = error_message or f"Client untuk provider {SELECTED_PROVIDER} tidak diinisialisasi."
        print(f"[!] ERROR: {msg}")
        sys.exit(1)


# JANGAN auto-init di sini. Biarkan main.py yang panggil clients.init()
# setelah env sudah pasti ke-load. Kalau auto-init, bisa jalan sebelum
# load_env() dan bikin client = None.
