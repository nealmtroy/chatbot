import os
import re
import time
import random
import httpx
import logging
from collections import defaultdict

logger = logging.getLogger("DigitalTwinAgent")
from typing import List, Dict, Union, Tuple
from dotenv import load_dotenv
from openai import OpenAI
from src.rag_engine import ChromaRAGEngine
from src.templates import TemplateManager

load_dotenv()

PROVIDERS_CONFIG = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "default_models": ["google/gemini-2.5-flash"]
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "default_models": ["llama-3.3-70b-versatile"]
    },
    "sambanova": {
        "base_url": "https://api.sambanova.ai/v1",
        "api_key_env": "SAMBANOVA_API_KEY",
        "model_env": "SAMBANOVA_MODEL",
        "default_models": ["Meta-Llama-3.3-70B-Instruct", "DeepSeek-R1-Distill-Llama-70B"]
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "default_models": ["deepseek-chat"]
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_models": ["gpt-4o-mini"]
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "api_key_env": "OLLAMA_API_KEY",
        "model_env": "OLLAMA_MODEL",
        "default_models": ["llama3.2"]
    }
}

def _get_api_keys_for_provider(base_env_name: str) -> List[Tuple[str, str]]:
    """
    Find all API keys matching base_env_name (e.g. OPENROUTER_API_KEY)
    or indexed variants (e.g. OPENROUTER_API_KEY_1, OPENROUTER_API_KEY_2).
    Returns list of (key_name, key_value).
    """
    keys = []
    # 1. Base key first
    base_val = os.getenv(base_env_name, "").strip()
    if base_val:
        keys.append((base_env_name, base_val))
    
    # 2. Look for indexed or named variants (e.g. OPENROUTER_API_KEY_1, OPENROUTER_API_KEY_2)
    suffix_candidates = []
    prefix = base_env_name + "_"
    for env_k, env_v in os.environ.items():
        if env_k.startswith(prefix) and env_v.strip():
            suffix_candidates.append((env_k, env_v.strip()))
    
    def natural_sort_key(item):
        k = item[0]
        match = re.search(r'(\d+)$', k)
        num = int(match.group(1)) if match else 999
        return (num, k)

    suffix_candidates.sort(key=natural_sort_key)
    
    for k, v in suffix_candidates:
        if not any(v == existing_val for _, existing_val in keys):
            keys.append((k, v))

    return keys

class DigitalTwinAgent:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.provider_targets = []
        self.cooldowns = {}

        proxy_url = os.getenv("PROXY_URL", "").strip()
        http_client = None
        if proxy_url:
            http_client = httpx.Client(proxy=proxy_url)

        # Load active providers and multiple keys from .env
        for p_name, cfg in PROVIDERS_CONFIG.items():
            base_env = cfg["api_key_env"]
            api_keys = _get_api_keys_for_provider(base_env)
            
            # Special handling for local Ollama
            if not api_keys and p_name == "ollama" and os.getenv(cfg["model_env"]):
                api_keys = [("OLLAMA_LOCAL", "ollama")]

            if api_keys:
                models_env = os.getenv(cfg["model_env"], "")
                models = [m.strip() for m in models_env.split(",") if m.strip()]
                if not models:
                    models = cfg["default_models"]

                for key_name, api_key in api_keys:
                    is_local = "localhost" in cfg["base_url"] or "127.0.0.1" in cfg["base_url"]
                    client = OpenAI(
                        base_url=cfg["base_url"],
                        api_key=api_key,
                        max_retries=0,
                        http_client=None if (is_local or not http_client) else http_client
                    )
                    for model in models:
                        self.provider_targets.append({
                            "provider": p_name,
                            "key_name": key_name,
                            "client": client,
                            "model": model
                        })

        if not self.provider_targets:
            raise ValueError(
                "Tidak ada API Key LLM Provider yang aktif di .env!\n"
                "Harap isi minimal salah satu API Key di file .env (misal: OPENROUTER_API_KEY, OPENROUTER_API_KEY_1, GROQ_API_KEY, dll)."
            )

        self.rag = ChromaRAGEngine(data_dir)
        self.template_mgr = TemplateManager(data_dir)

    def add_raw_chat_block(self, partner_name: str, raw_text: str, summary: str = ""):
        """Parse raw WhatsApp export block and save as a chat session."""
        messages = self.rag.parse_raw_chat_block(partner_name, raw_text)
        self.rag.add_session(partner_name, messages, summary)

    def add_session(self, partner_name: str, messages: List[Dict[str, str]], summary: str = ""):
        """Add a structured chat session."""
        self.rag.add_session(partner_name, messages, summary)

    def clear_data(self):
        """Clear all stored chat data."""
        self.rag.clear_all()

    def _extract_thinking_and_clean_answer(self, text: str) -> tuple[str, str]:
        """Extract thinking/reasoning into debug string and return clean final answer."""
        thinking_parts = []
        clean_text = text.strip()

        # 1. Extract <think>...</think> tags if present
        think_matches = re.findall(r'<think>(.*?)</think>', clean_text, flags=re.DOTALL)
        if think_matches:
            for tm in think_matches:
                thinking_parts.append(tm.strip())
            clean_text = re.sub(r'<think>.*?</think>', '', clean_text, flags=re.DOTALL).strip()

        # 2. Extract "Possible response:" logic
        if "possible response:" in clean_text.lower():
            parts = re.split(r'possible response:\s*', clean_text, flags=re.IGNORECASE)
            if len(parts) > 1 and parts[-1].strip():
                thinking_parts.append(parts[0].strip())
                clean_text = parts[-1].strip()

        # 3. Extract reasoning monologue lines
        reasoning_keywords = [
            "okay, let's see", "looking at the chat history", "in the previous interactions",
            "the user's current question", "based on the history", "check the pricelist",
            "so, the appropriate response", "the user is asking",
            "let's see.", "let's analyze", "looking at session"
        ]

        lines = clean_text.split("\n")
        filtered_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if any(stripped.lower().startswith(kw) for kw in reasoning_keywords):
                thinking_parts.append(stripped)
            else:
                filtered_lines.append(stripped)

        final_answer = "\n".join(filtered_lines).strip()
        if final_answer.startswith('"') and final_answer.endswith('"') and len(final_answer) > 2:
            final_answer = final_answer[1:-1].strip()

        debug_thinking = "\n".join(thinking_parts).strip()
        return debug_thinking, (final_answer if final_answer else clean_text)

    def _is_invalid_reasoning(self, text: str) -> bool:
        """
        Check if the text is a reasoning/analysis monologue instead of a valid casual reply.
        Returns True if it contains analytical English patterns or reasoning leaks.
        """
        text_lower = text.lower()
        
        # 1. Clear indicators of reasoning/thinking monologue
        reasoning_signals = [
            "the user is asking",
            "the standard response",
            "checking the system info",
            "should be stating",
            "the template says",
            "reply short and natural",
            "previous interactions",
            "chat history",
            "appropriate response",
            "this is a typo",
            "probably a typo",
            "shorthand for",
            "stating the price",
            "casual follow-up",
            "look at the history",
            "based on the history",
            "let's analyze",
            "the response should",
            "in the previous chat",
            "user hasn't made any payment",
            "system instruction",
            "system info",
            "the context is",
            "suggesting that"
        ]
        
        if any(sig in text_lower for sig in reasoning_signals):
            return True
            
        # 2. Check if the text is purely fluent English-instruction-style output
        english_indicators = ["should be", "probably", "assume", "response", "therefore", "instead of", "suggests", "concerning"]
        if any(indicator in text_lower for indicator in english_indicators):
            indo_slang = ["kakk", "kak", "krak", "sange", "vcs", "ga", "ada", "aku", "kamu", "nih", "ya", "sih", "dong", "deh", "bisa", "harga", "qris", "bayar"]
            has_indo = any(slang in text_lower for slang in indo_slang)
            if not has_indo:
                return True
                
        return False

    def generate_response(self, user_input: str, conversation_history: list = None, system_instruction: str = None) -> str:
        """Generate response matching exact WhatsApp export conversation flow and typing style."""
        rag_context = self.rag.get_context_for_prompt(user_input, top_k=3)
        formatted_rag_context = self.template_mgr.replace_placeholders(rag_context)
        
        bot_name = self.template_mgr.config.get("bot_name", "Intan")
        pricelist_template = self.template_mgr.get_pricelist_template()

        system_prompt = (
            f"Kamu adalah AI Persona / Digital Twin dengan nama '{bot_name}'.\n"
            "TUGAS UTAMA: Jawab pertanyaan atau ajakan obrolan dengan MENIRU 100% GAYA BAHASA, "
            "KOSA KATA, DAN RITME NGETIK (Reply) berdasarkan referensi riwayat chat export WhatsApp berikut.\n\n"
            f"{formatted_rag_context}\n\n"
            f"TEMPLATE PRICELIST RESMI KAMU:\n{pricelist_template}\n\n"
        )
        
        if system_instruction:
            system_prompt += f"{system_instruction}\n\n"

        system_prompt += (
            "ATURAN KETAT:\n"
            "1. JANGAN PERNAH terdengar seperti AI formal, bot, atau customer service.\n"
            "2. JANGAN PERNAH MENULISKAN PROSES BERPIKIR / ANALISIS / CHAIN-OF-THOUGHT DI HASIL AKHIR BALASAN.\n"
            "3. Tirulah alur respon 'reply' sesuai contoh riwayat obrolan di atas.\n"
            "4. Jika pada contoh riwayat obrolan user menggunakan 2 balasan, "
            "kamu JUGA harus memisahkan balasanmu menjadi 2 baris (enter) sesuai ritme khas user mebutuhkannya.\n"
            "5. Jawaban harus santai, natural, dan sesuai gaya penulisan asli user.\n"
            "6. Hanya jika user secara spesifik menanyakan pricelist lengkap, daftar harga lengkap, atau list harga keseluruhan, kirimkan TEMPLATE PRICELIST RESMI di atas secara persis. Jika user hanya menanyakan harga produk spesifik (misal: 'vcs berapa', 'vip berapa', 'harga vcs'), jawablah secara singkat, santai, dan natural sesuai contoh riwayat chat (RAG) (misal: 'vcs 100k kakk' atau 'vip cuma 50k kakk').\n"
            "7. Jika user setuju/ingin melakukan pembayaran (misal memilih 'vcs' atau 'vip'), atau meminta dikirimkan QRIS baru/ulang, kamu WAJIB menyisipkan tag [qris] di akhir baris kalimat balasan tempat kamu mengirimkan QRIS (contoh: \"oke vcs 100k, ini qris baru nya kakk [qris]\" atau \"ini qrisnya kakk [qris]\"). Tag ini akan dideteksi sistem untuk menghasilkan QRIS secara otomatis."
        )

        messages = [{"role": "system", "content": system_prompt}]
        
        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": user_input})

        # 1. Group active targets by provider, keeping the priority order from PROVIDERS_CONFIG
        targets_by_provider = defaultdict(list)
        for target in self.provider_targets:
            targets_by_provider[target["provider"]].append(target)
            
        ordered_providers = [p for p in PROVIDERS_CONFIG.keys() if p in targets_by_provider]
        
        # 2. Shuffle targets within each provider to load-balance
        shuffled_targets = []
        for provider in ordered_providers:
            provider_list = list(targets_by_provider[provider])
            random.shuffle(provider_list)
            shuffled_targets.extend(provider_list)
            
        # 3. Filter targets currently in cooldown
        current_time = time.time()
        available_targets = []
        cooled_down_targets = []
        for target in shuffled_targets:
            cooldown_key = (target["provider"], target["key_name"], target["model"])
            if current_time < self.cooldowns.get(cooldown_key, 0):
                cooled_down_targets.append(target)
            else:
                available_targets.append(target)
                
        # If all targets are in cooldown, use all targets as a fallback
        final_targets = available_targets if available_targets else cooled_down_targets
        total_targets = len(final_targets)
        
        logger.info(f"🤖 [DEBUG MULTI-PROVIDER LLM] Menyiapkan pemanggilan ({total_targets} target provider/key/model aktif)...")

        last_exception = None
        for idx, target in enumerate(final_targets, 1):
            provider_name = target["provider"].upper()
            key_name = target["key_name"]
            model_name = target["model"]
            client = target["client"]
            cooldown_key = (target["provider"], target["key_name"], target["model"])

            logger.info(f"   ⏳ [TRY TARGET {idx}/{total_targets}] [{provider_name}] ({key_name}) -> Model '{model_name}'...")
            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=500,
                )
                raw_answer = response.choices[0].message.content.strip()
                debug_thinking, clean_answer = self._extract_thinking_and_clean_answer(raw_answer)
                
                if debug_thinking:
                    logger.info(f"   🧠 [DEBUG THINKING/REASONING]:")
                    for line in debug_thinking.split("\n"):
                        if line.strip():
                            logger.info(f"      💭 {line.strip()}")
                
                # Check for reasoning leaks in final answer
                if self._is_invalid_reasoning(clean_answer):
                    raise ValueError(f"Reasoning leak detected in final answer: {clean_answer!r}")
                
                logger.info(f"   ✅ [SUCCESS] Provider [{provider_name}] ({key_name}) Model '{model_name}' berhasil me-respond!\n")
                
                # Clear cooldown on success
                if cooldown_key in self.cooldowns:
                    del self.cooldowns[cooldown_key]
                    
                return clean_answer
            except Exception as e:
                last_exception = e
                # Set cooldown based on error type
                error_str = str(e).lower()
                if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
                    cooldown_dur = 60
                    cooldown_msg = "Rate limit detected. Cooldown 60s."
                else:
                    cooldown_dur = 30
                    cooldown_msg = "Error detected. Cooldown 30s."
                    
                self.cooldowns[cooldown_key] = time.time() + cooldown_dur
                logger.warning(f"   ❌ [FAILED] Provider [{provider_name}] ({key_name}) Model '{model_name}' error: {e}. ({cooldown_msg})")
                
                if idx < total_targets:
                    next_target = final_targets[idx]
                    logger.info(f"   🔄 [AUTO-FALLBACK] Beralih ke [{next_target['provider'].upper()}] ({next_target['key_name']}) - Model '{next_target['model']}'...")
                else:
                    logger.error(f"   ❌ [ERROR] Semua provider/key/model dalam daftar fallback gagal dipanggil.\n")

        if last_exception:
            raise last_exception
        return "Gagal mendapatkan respon dari AI."
