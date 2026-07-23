import os
import re
import asyncio
from typing import List, Dict, Union, Tuple
from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI
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
    base_val = os.getenv(base_env_name, "").strip()
    if base_val:
        keys.append((base_env_name, base_val))
    
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

        for p_name, cfg in PROVIDERS_CONFIG.items():
            base_env = cfg["api_key_env"]
            api_keys = _get_api_keys_for_provider(base_env)
            
            if not api_keys and p_name == "ollama" and os.getenv(cfg["model_env"]):
                api_keys = [("OLLAMA_LOCAL", "ollama")]

            if api_keys:
                models_env = os.getenv(cfg["model_env"], "")
                models = [m.strip() for m in models_env.split(",") if m.strip()]
                if not models:
                    models = cfg["default_models"]

                for key_name, api_key in api_keys:
                    sync_client = OpenAI(
                        base_url=cfg["base_url"],
                        api_key=api_key
                    )
                    async_client = AsyncOpenAI(
                        base_url=cfg["base_url"],
                        api_key=api_key
                    )
                    for model in models:
                        self.provider_targets.append({
                            "provider": p_name,
                            "key_name": key_name,
                            "client": sync_client,
                            "async_client": async_client,
                            "model": model
                        })

        if not self.provider_targets:
            print("⚠️ Warning: Tidak ada API Key LLM Provider yang aktif di .env!")

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

    def generate_response(self, user_input: str, conversation_history: list = None) -> str:
        """Generate response matching exact WhatsApp export conversation flow and typing style (Sync)."""
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
            "ATURAN KETAT:\n"
            "1. JANGAN PERNAH terdengar seperti AI formal, bot, atau customer service.\n"
            "2. JANGAN PERNAH MENULISKAN PROSES BERPIKIR / ANALISIS / CHAIN-OF-THOUGHT DI HASIL AKHIR BALASAN.\n"
            "3. Tirulah alur respon 'reply' sesuai contoh riwayat obrolan di atas.\n"
            "4. Jika pada contoh riwayat obrolan user menggunakan 2 balasan, "
            "kamu JUGA harus memisahkan balasanmu menjadi 2 baris (enter) sesuai ritme khas user mebutuhkannya.\n"
            "5. Jawaban harus santai, natural, dan sesuai gaya penulisan asli user.\n"
            "6. Jika user menanyakan daftar harga / pricelist / list harga, KIRIMKAN TEMPLATE PRICELIST RESMI di atas secara persis."
        )

        messages = [{"role": "system", "content": system_prompt}]
        
        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": user_input})

        total_targets = len(self.provider_targets)
        if total_targets == 0:
            return "Error: Tidak ada provider LLM aktif di .env."

        print(f"🤖 [DEBUG MULTI-PROVIDER LLM] Menyiapkan pemanggilan ({total_targets} target provider/key/model aktif)...")

        last_exception = None
        for idx, target in enumerate(self.provider_targets, 1):
            provider_name = target["provider"].upper()
            key_name = target["key_name"]
            model_name = target["model"]
            client = target["client"]

            print(f"   ⏳ [TRY TARGET {idx}/{total_targets}] [{provider_name}] ({key_name}) -> Model '{model_name}'...")
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
                    print(f"   🧠 [DEBUG THINKING/REASONING]:")
                    for line in debug_thinking.split("\n"):
                        if line.strip():
                            print(f"      💭 {line.strip()}")
                
                print(f"   ✅ [SUCCESS] Provider [{provider_name}] ({key_name}) Model '{model_name}' berhasil me-respond!\n")
                return clean_answer
            except Exception as e:
                last_exception = e
                print(f"   ❌ [FAILED] Provider [{provider_name}] ({key_name}) Model '{model_name}' error: {e}")
                if idx < total_targets:
                    next_target = self.provider_targets[idx]
                    print(f"   🔄 [AUTO-FALLBACK] Beralih ke [{next_target['provider'].upper()}] ({next_target['key_name']}) - Model '{next_target['model']}'...")
                else:
                    print(f"   ❌ [ERROR] Semua provider/key/model dalam daftar fallback gagal dipanggil.\n")

        if last_exception:
            raise last_exception
        return "Gagal mendapatkan respon dari AI."

    async def generate_response_async(self, user_input: str, conversation_history: list = None) -> str:
        """Generate response matching exact WhatsApp export conversation flow asynchronously."""
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
            "ATURAN KETAT:\n"
            "1. JANGAN PERNAH terdengar seperti AI formal, bot, atau customer service.\n"
            "2. JANGAN PERNAH MENULISKAN PROSES BERPIKIR / ANALISIS / CHAIN-OF-THOUGHT DI HASIL AKHIR BALASAN.\n"
            "3. Tirulah alur respon 'reply' sesuai contoh riwayat obrolan di atas.\n"
            "4. Jika pada contoh riwayat obrolan user menggunakan 2 balasan, "
            "kamu JUGA harus memisahkan balasanmu menjadi 2 baris (enter) sesuai ritme khas user mebutuhkannya.\n"
            "5. Jawaban harus santai, natural, dan sesuai gaya penulisan asli user.\n"
            "6. Jika user menanyakan daftar harga / pricelist / list harga, KIRIMKAN TEMPLATE PRICELIST RESMI di atas secara persis."
        )

        messages = [{"role": "system", "content": system_prompt}]
        
        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": user_input})

        return await self.call_llm_messages_async(messages)

    async def call_llm_messages_async(self, messages: list, temperature: float = 0.7, max_tokens: int = 500) -> str:
        """Call LLM with payload messages using multi-provider fallback asynchronously."""
        total_targets = len(self.provider_targets)
        if total_targets == 0:
            return ""

        last_exception = None
        for idx, target in enumerate(self.provider_targets, 1):
            provider_name = target["provider"].upper()
            key_name = target["key_name"]
            model_name = target["model"]
            async_client = target["async_client"]

            try:
                response = await async_client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                raw_answer = response.choices[0].message.content.strip()
                _, clean_answer = self._extract_thinking_and_clean_answer(raw_answer)
                return clean_answer
            except Exception as e:
                last_exception = e
                print(f"   ❌ [FALLBACK ASYNC] Provider [{provider_name}] ({key_name}) Model '{model_name}' error: {e}")

        if last_exception:
            print(f"   ❌ Semua provider fallback gagal dipanggil: {last_exception}")
        return ""
