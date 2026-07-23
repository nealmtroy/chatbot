import os
import re
import json
import asyncio
import logging
from .env_loader import load_env

# Load environment variables
load_env()

# Inisialisasi client AI terpusat (Multi-Provider Fallback + ChromaDB RAG)
from . import clients
from . import db
from . import user_tracker

logger = logging.getLogger("AI-Engine")

# Directory paths
PROMPTS_DIR = "prompts"
KNOWLEDGE_FILE = "knowledge.json"
CORRECTIONS_FILE = "corrections.json"

# --- Cache knowledge.json (reload otomatis kalau file berubah) ---
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
    """Simpan koreksi (.revisi) ke DB / file. Backward-compat dengan pemanggil lama (2 atau 3 argumen)."""
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


def retrieve_relevant_knowledge(message_text, knowledge_file=None, account_id=0):
    """
    Memindai kata kunci pada pesan masuk dan mengembalikan fakta relevan dari
    knowledge.json (per-account) + tabel knowledge di DB + ChromaDB Vector RAG dari ai-testing.
    """
    if knowledge_file is None:
        knowledge_file = KNOWLEDGE_FILE

    all_entries = []
    if os.path.exists(knowledge_file):
        try:
            mtime = os.path.getmtime(knowledge_file)
            if _knowledge_cache["data"] is not None and _knowledge_cache.get("file") == knowledge_file and _knowledge_cache["mtime"] == mtime:
                file_entries = _knowledge_cache["data"]
            else:
                with open(knowledge_file, "r", encoding="utf-8") as f:
                    file_entries = json.load(f)
                _knowledge_cache["data"] = file_entries
                _knowledge_cache["mtime"] = mtime
                _knowledge_cache["file"] = knowledge_file
            all_entries.extend(file_entries)
        except (OSError, FileNotFoundError, json.JSONDecodeError) as e:
            logger.debug("knowledge file %s: %s", knowledge_file, e)

    try:
        all_entries.extend(db.get_knowledge(account_id))
    except Exception:
        pass

    matched_facts = []
    message_lower = message_text.lower()
    for item in all_entries:
        for kw in item.get("keywords", []):
            kw_clean = kw.lower().strip()
            if kw_clean and kw_clean in message_lower:
                matched_facts.append(item.get("fact"))
                break

    rag_text = ""
    # ChromaDB Vector Search RAG dari DigitalTwinAgent
    if clients.digital_twin_agent and hasattr(clients.digital_twin_agent, "rag"):
        try:
            rag_context = clients.digital_twin_agent.rag.get_context_for_prompt(message_text, top_k=2)
            if "Belum ada riwayat chat export" not in rag_context:
                rag_text = f"\n[CHROMADB_VECTOR_RAG_CONTEXT]\n{rag_context}\n"
        except Exception as e:
            logger.debug(f"ChromaDB RAG search notice: {e}")

    retrieved_parts = []
    if matched_facts:
        logger.info("retrieve %d fakta relevan", len(matched_facts))
        fact_text = "\n".join([f"- {fact}" for fact in matched_facts])
        retrieved_parts.append(f"\n[RELEVANT_KNOWLEDGE_FACTS]\nGunakan informasi fakta berikut sebagai referensi pengetahuan kamu:\n{fact_text}\n")
    
    if rag_text:
        retrieved_parts.append(rag_text)

    return "".join(retrieved_parts)


from agents.critic_agent import (
    force_lowercase_except_laughter,
    _strip_think,
    strip_formatting_and_limit_emojis,
    EMOJI_PATTERN,
)


async def _call_api_with_retry(messages, max_retries=3):
    """Panggil API dengan Multi-Provider Fallback (OpenRouter, Groq, SambaNova, DeepSeek, OpenAI, Ollama)."""
    # 1. Coba multi-provider fallback engine terlebih dahulu
    try:
        clean_ans = await clients.call_llm_multi_provider(messages, temperature=0.7, max_tokens=500)
        if clean_ans and clean_ans.strip():
            # mock OpenAI response format agar kompatibel
            class MockMessage:
                def __init__(self, content):
                    self.content = content
            class MockChoice:
                def __init__(self, content):
                    self.message = MockMessage(content)
            class MockResponse:
                def __init__(self, content):
                    self.choices = [MockChoice(content)]
            return MockResponse(clean_ans)
    except Exception as e:
        logger.warning(f"Multi-provider LLM call error, mencoba fallback direct client: {e}")

    # 2. Fallback direct client
    if clients.client is None:
        logger.error("Clients client is None")
        return None

    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            response = await clients.client.chat.completions.create(
                model=clients.active_model,
                messages=messages,
                temperature=0.7,
                presence_penalty=0.6,
                frequency_penalty=0.5,
                max_tokens=500
            )
            return response
        except Exception as e:
            last_err = e
            logger.warning(f"Percobaan API #{attempt} gagal: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
    logger.error(f"Gagal memanggil API setelah {max_retries} percobaan: {last_err}")
    return None


from agents.pipeline import DigitalClonePipeline

# Pipeline instance
_digital_clone_pipeline = DigitalClonePipeline(prompts_dir=PROMPTS_DIR)


async def generate_ai_reply(account, user_db_id, user_name, message_text, max_history=20, return_full_output=False):
    """
    Menghasilkan balasan dari AI untuk 1 user di 1 account tertentu menggunakan
    DigitalClonePipeline (6 Specialized Agents + Multi-Provider Fallback + RAG Vector DB).
    """
    if not clients.client and (clients.digital_twin_agent is None or not clients.digital_twin_agent.provider_targets):
        logger.error("Client AI tidak terinisialisasi di .env")
        return (None, []) if not return_full_output else None

    output = await _digital_clone_pipeline.execute(
        account=account,
        user_db_id=user_db_id,
        user_name=user_name,
        message_text=message_text,
        chat_type="private",
        max_history=max_history
    )

    conf = output.confidence
    logger.info(f"AI reply generated via DigitalClonePipeline (Score: {conf.score:.1f}%, Status: {conf.status})")

    if conf.status == "hold":
        logger.warning(f"Confidence score low ({conf.score:.1f}%): {conf.reason}")

    if return_full_output:
        return output

    return output.final_text, output.bubbles


async def generate_media_followup(account, user_db_id, user_name, message_text, max_history, intent):
    """
    Generate 1 bubble follow-up persuasif via AI setelah kirim media.
    Per-account: baca persona dari DB, history dari DB.
    """
    if not clients.client and (clients.digital_twin_agent is None or not clients.digital_twin_agent.provider_targets):
        return ""

    account_id = account["id"]
    persona_file = account.get("persona_file", "prompts/persona.txt")
    persona_prompt = load_prompt_file(os.path.basename(persona_file))
    sales_prompt = load_prompt_file("sales.txt")
    slang_prompt = load_prompt_file("slang.txt")

    intent_label = {
        "pap": "baru aja kirim foto pap (topless/colmek) ke user",
        "video": "baru aja kirim video colmek ke user",
        "vip_preview": "baru aja kirim preview isi grup VIP ke user",
    }.get(intent, "baru aja kirim media ke user")

    system_prompt = (
        persona_prompt + "\n\n" + sales_prompt + "\n\n" + slang_prompt + "\n\n"
        "[TUGAS FOLLOW-UP MEDIA]\n"
        f"Kamu {intent_label}.\n"
        "Sekarang buat SATU bubble chat pendek (maksimal 1 kalimat, maksimal 12 kata) "
        "untuk ngeyakinin user buat join grup VIP. Wajib sebutin bahwa di grup VIP masih "
        "banyak lagi, ada video colmek dan video ngewe punya kamu. Gaya flirty ala "
        f"{account.get('name', 'pemilik akun')}, "
        "casual, boleh ada 1 emoji di akhir kalau natural. JANGAN bilang 'aku manusia' / "
        "'aku bot' / 'aku asli'. JANGAN jelasin panjang lebar. Cuma 1 bubble aja.\n"
        "Contoh gaya: 'di grup vip ak masi byk lg loh, ada vid colmek sam vid ngewe ak 🫣'\n"
    )

    user_history = db.get_history(user_db_id, max_history) if user_db_id else []
    messages = [{"role": "system", "content": system_prompt}]
    for msg in user_history[-max_history:]:
        messages.append(msg)
    messages.append({"role": "user", "content": message_text})

    response = await _call_api_with_retry(messages)
    if response is None:
        return ""

    try:
        text = response.choices[0].message.content.strip()
    except (AttributeError, IndexError, KeyError):
        return ""

    text = _strip_think(text)
    text = force_lowercase_except_laughter(text)

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return ""
    return lines[0]
