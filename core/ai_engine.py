import os
import re
import json
import asyncio
import logging
from .env_loader import load_env

load_env()

from . import clients
from . import db
from . import user_tracker

logger = logging.getLogger("AI-Engine")

PROMPTS_DIR = "prompts"
KNOWLEDGE_FILE = "knowledge.json"
CORRECTIONS_FILE = "corrections.json"

_knowledge_cache = {"data": None, "mtime": 0.0}


def load_prompt_file(filename):
    filepath = os.path.join(PROMPTS_DIR, filename)
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"Gagal membaca prompt {filename}: {e}")
    return ""


def save_correction(account_id_or_user, user_text=None, assistant_text=None):
    """Simpan koreksi ke DB / file."""
    if assistant_text is None:
        user_text, assistant_text = account_id_or_user, user_text
        account_id = 0
    else:
        account_id = account_id_or_user
    try:
        db.add_correction(account_id, user_text, assistant_text)
    except Exception as e:
        logger.warning(f"Gagal simpan koreksi ke DB: {e}")
    if CORRECTIONS_FILE:
        try:
            data = []
            if os.path.exists(CORRECTIONS_FILE):
                with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            updated = False
            for item in data:
                if item.get("user", "").lower() == user_text.lower():
                    item["assistant"] = assistant_text
                    updated = True
                    break
            if not updated:
                data.append({"user": user_text, "assistant": assistant_text})
            with open(CORRECTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception:
            pass
    return True


def _strip_think(text: str) -> str:
    if not text:
        return ""
    if clients.digital_twin_agent:
        _, clean = clients.digital_twin_agent._extract_thinking_and_clean_answer(text)
        return clean
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    return text


def force_lowercase_except_laughter(text: str) -> str:
    if not text:
        return ""
    return text.lower()


async def generate_ai_reply(account, user_db_id, user_name, message_text, max_history=20, return_full_output=False):
    """
    Menghasilkan balasan AI MENGGUNAKAN CARA KERJA & LOGIKA `ai-testing` (DigitalTwinAgent)
    secara langsung:
      - ChromaDB RAG Vector search
      - Dynamic Persona & Pricelist Template (persona.json)
      - Multi-Provider & Multi-Key LLM Fallback
      - Format reply & bubble sesuai alur WhatsApp export chat
    """
    if clients.digital_twin_agent is None or not clients.digital_twin_agent.provider_targets:
        logger.error("DigitalTwinAgent / Multi-Provider LLM tidak terinisialisasi di .env")
        return (None, []) if not return_full_output else None

    # Load conversation history dari DB
    user_history = db.get_history(user_db_id, limit=max_history) if user_db_id else []
    conversation_history = []
    for msg in user_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if content and content != message_text:
            conversation_history.append({"role": role, "content": content})

    # Call DigitalTwinAgent.generate_response_async (SAMA PERSIS LOGIKA ai-testing)
    ai_response = await clients.digital_twin_agent.generate_response_async(
        user_input=message_text,
        conversation_history=conversation_history
    )

    if not ai_response:
        logger.warning(f"Gagal mendapatkan respon dari DigitalTwinAgent untuk user {user_name}")
        return (None, []) if not return_full_output else None

    # Parse baris balasan menjadi bubbles (sesuai ritme ngetik reply ai-testing)
    raw_lines = [l.strip() for l in ai_response.split("\n") if l.strip()]
    cleaned_lines = []
    for line in raw_lines:
        if line.lower().startswith("reply:"):
            line = line[6:].strip()
        if line:
            cleaned_lines.append(line)

    reply_text = "\n".join(cleaned_lines) if cleaned_lines else ai_response
    bubbles = []
    for line in cleaned_lines:
        delay = round(min(max(len(line) * 0.04, 0.4), 1.5), 1)
        bubbles.append({"text": line, "delay": delay})

    if not bubbles and reply_text:
        bubbles = [{"text": reply_text, "delay": 0.8}]

    if return_full_output:
        class SimpleOutput:
            def __init__(self, text, bubs):
                self.final_text = text
                self.bubbles = bubs
                class MockConf:
                    score = 100.0
                    status = "pass"
                self.confidence = MockConf()
        return SimpleOutput(reply_text, bubbles)

    return reply_text, bubbles


async def generate_media_followup(account, user_db_id, user_name, message_text, max_history, intent):
    """Generate 1 bubble follow-up persuasif setelah kirim media."""
    if clients.digital_twin_agent is None or not clients.digital_twin_agent.provider_targets:
        return ""

    bot_name = clients.digital_twin_agent.template_mgr.config.get("bot_name", "Intan")
    intent_label = {
        "pap": "baru aja kirim foto pap (topless/colmek) ke user",
        "video": "baru aja kirim video colmek ke user",
        "vip_preview": "baru aja kirim preview isi grup VIP ke user",
    }.get(intent, "baru aja kirim media ke user")

    system_prompt = (
        f"Kamu adalah {bot_name}, {intent_label}.\n"
        "Sekarang buat SATU bubble chat pendek (maksimal 1 kalimat, maksimal 12 kata) "
        "untuk ngeyakinin user buat join grup VIP. Wajib sebutin bahwa di grup VIP masih "
        "banyak lagi, ada video colmek dan video ngewe punya kamu. Gaya flirty, casual, "
        "tanpa tanda titik. Cuma 1 bubble aja.\n"
        "Contoh gaya: 'di grup vip ak masi byk lg loh, ada vid colmek sam vid ngewe ak 🫣'\n"
    )

    user_history = db.get_history(user_db_id, max_history) if user_db_id else []
    messages = [{"role": "system", "content": system_prompt}]
    for msg in user_history[-max_history:]:
        messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
    messages.append({"role": "user", "content": message_text})

    response_text = await clients.call_llm_multi_provider(messages, temperature=0.7, max_tokens=200)
    if not response_text:
        return ""

    _, clean_text = clients.digital_twin_agent._extract_thinking_and_clean_answer(response_text)
    lines = [l.strip() for l in clean_text.split("\n") if l.strip()]
    if not lines:
        return ""
    line = lines[0]
    if line.lower().startswith("reply:"):
        line = line[6:].strip()
    return line
