"""
clients.py - Inisialisasi Multi-Provider & Multi-Key AI Engine terpusat.

Modul ini menginisialisasi DigitalTwinAgent dari ai-testing yang mendukung
Multi-Provider LLM Fallback (OpenRouter, Groq, SambaNova, DeepSeek, OpenAI, Ollama)
dan ChromaDB RAG engine.
"""
import os
import sys
import logging
from .env_loader import load_env
from src.agent import DigitalTwinAgent

load_env()

logger = logging.getLogger("AI-Clients")

SELECTED_PROVIDER = os.getenv("SELECTED_PROVIDER", "SAMBANOVA").upper()

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")

client = None
active_model = None
error_message = None
digital_twin_agent = None


def init():
    """Inisialisasi DigitalTwinAgent & client multi-provider fallback. Panggil di startup."""
    global client, active_model, error_message, digital_twin_agent
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    
    try:
        digital_twin_agent = DigitalTwinAgent(data_dir)
        if digital_twin_agent.provider_targets:
            first_target = digital_twin_agent.provider_targets[0]
            client = first_target["async_client"]
            active_model = first_target["model"]
            logger.info(f"✅ Multi-Provider LLM Engine diinisialisasi ({len(digital_twin_agent.provider_targets)} target aktif). Target utama: [{first_target['provider'].upper()}] - '{active_model}'")
        else:
            error_message = "Tidak ada API key LLM aktif di .env"
            logger.warning(error_message)
    except Exception as e:
        error_message = f"Gagal inisialisasi Multi-Provider LLM Engine: {e}"
        logger.error(error_message)


async def call_llm_multi_provider(messages: list, temperature: float = 0.7, max_tokens: int = 500) -> str:
    """Panggil LLM dengan Multi-Provider Fallback otomatis."""
    global digital_twin_agent
    if digital_twin_agent is None:
        init()
    
    if digital_twin_agent and digital_twin_agent.provider_targets:
        return await digital_twin_agent.call_llm_messages_async(messages, temperature=temperature, max_tokens=max_tokens)
    return ""


def require_client():
    """Cek apakah client sudah siap. Keluar dengan pesan error bila gagal."""
    if client is None and (digital_twin_agent is None or not digital_twin_agent.provider_targets):
        msg = error_message or f"Client untuk provider {SELECTED_PROVIDER} tidak diinisialisasi."
        print(f"[!] ERROR: {msg}")
        sys.exit(1)
