import os
import json
import re
import logging
from typing import Dict, Any, List, Tuple
import db
import user_tracker
from .base import ContextData, MemoryData

logger = logging.getLogger("MemoryAgent")

# Cache per-file: {filepath: {"data": ..., "mtime": ...}}
_knowledge_file_cache: Dict[str, Any] = {}
_history_file_cache: Dict[str, Any] = {}


class MemoryAgent:
    """
    2. Memory Agent
    Tujuan: Mengingat seluruh informasi penting yang pernah diketahui mengenai
    lawan bicara maupun pemilik akun (short-term & long-term memory).
    Membaca fakta & pola bahasa dari knowledge.json, chat_history.json, dan DB.
    """

    def retrieve_relevant_knowledge(self, message_text: str, knowledge_file: str, account_id: int) -> List[str]:
        all_entries = []
        if os.path.exists(knowledge_file):
            try:
                mtime = os.path.getmtime(knowledge_file)
                cached = _knowledge_file_cache.get(knowledge_file)
                if cached and cached["mtime"] == mtime:
                    file_entries = cached["data"]
                else:
                    with open(knowledge_file, "r", encoding="utf-8") as f:
                        file_entries = json.load(f)
                    _knowledge_file_cache[knowledge_file] = {"data": file_entries, "mtime": mtime}
                all_entries.extend(file_entries)
            except Exception as e:
                logger.debug(f"Knowledge file read error: {e}")

        try:
            all_entries.extend(db.get_knowledge(account_id))
        except Exception:
            pass

        if not all_entries:
            return []

        matched_facts = []
        message_lower = message_text.lower()
        for item in all_entries:
            for kw in item.get("keywords", []):
                kw_clean = kw.lower().strip()
                if kw_clean and re.search(rf'\b{re.escape(kw_clean)}\b', message_lower):
                    matched_facts.append(item.get("fact"))
                    break

        return matched_facts

    def retrieve_relevant_chat_examples(self, message_text: str, history_file: str = "chat_history.json") -> Tuple[List[Dict[str, Any]], List[str]]:
        candidates = [
            history_file,
            os.path.join("telegram-chatbot", history_file)
        ]
        entries = []
        target_path = None
        for path in candidates:
            if os.path.exists(path) and os.path.isfile(path):
                target_path = path
                break

        if target_path:
            try:
                mtime = os.path.getmtime(target_path)
                cached = _history_file_cache.get(target_path)
                if cached and cached["mtime"] == mtime:
                    entries = cached["data"]
                else:
                    with open(target_path, "r", encoding="utf-8") as f:
                        entries = json.load(f)
                    _history_file_cache[target_path] = {"data": entries, "mtime": mtime}
            except Exception as e:
                logger.debug(f"Error reading chat_history.json: {e}")

        matched_examples = []
        extra_facts = []
        msg_lower = message_text.lower()

        import re
        for item in entries:
            keywords = item.get("keywords", [])
            for kw in keywords:
                kw_clean = kw.lower().strip()
                if kw_clean and re.search(rf'\b{re.escape(kw_clean)}\b', msg_lower):
                    matched_examples.append(item)
                    for f in item.get("facts", []):
                        if f not in extra_facts:
                            extra_facts.append(f)
                    break

        return matched_examples, extra_facts

    def process(self, context: ContextData) -> MemoryData:
        account_id = context.account.get("id", 0)
        knowledge_file = context.account.get("knowledge_file", "knowledge.json")
        user_db_id = context.user_db_id

        user_info = {}
        stage = "new"
        if user_db_id:
            try:
                user_info = db.get_user(user_db_id) or {}
                stage = user_info.get("stage", "new")
            except Exception as e:
                logger.warning(f"Gagal membaca user info: {e}")

        profile_line = user_tracker.profile_summary(user_db_id) if (user_tracker and user_db_id) else ""
        matched_facts = self.retrieve_relevant_knowledge(context.message_text, knowledge_file, account_id)
        matched_examples, extra_facts = self.retrieve_relevant_chat_examples(context.message_text)

        # Merge extra facts from chat history
        for ef in extra_facts:
            if ef not in matched_facts:
                matched_facts.append(ef)

        # Inisialisasi memory data
        memory = MemoryData(
            nickname=context.sender or "Teman",
            relationship="Pelanggan/Teman" if stage != "new" else "Kenalan Baru",
            favorite_topics=["Crypto", "VIP Group", "Ngonversasi"] if stage in ["interested", "bought"] else ["Obrolan Santai"],
            chat_habit="Santai, kasual, gaya chat anak muda",
            facts=matched_facts,
            chat_examples=matched_examples,
            user_stage=stage,
            profile_summary=profile_line
        )

        logger.debug(f"MemoryAgent retrieved {len(matched_facts)} facts and {len(matched_examples)} chat examples for user {user_db_id}")
        return memory

    def update_memory_after_conversation(self, context: ContextData, reply_text: str):
        """Memperbarui memori di DB setelah percakapan selesai.
        
        Catatan: stage detection sudah ditangani oleh StageAgent di pipeline,
        jadi tidak perlu update stage dari reply text di sini.
        """
        if not context.user_db_id:
            return
        # Profile enrichment dari reply (kalau AI menyebut info user)
        try:
            user_tracker.enrich_from_message(context.user_db_id, reply_text)
        except Exception as e:
            logger.debug(f"Enrich dari reply gagal (normal): {e}")
