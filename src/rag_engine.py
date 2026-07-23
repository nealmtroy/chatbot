import json
import os
from typing import List, Dict, Any, Union

try:
    import chromadb
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    print("[WARNING] Module 'chromadb' tidak ditemukan. Fitur Vector DB RAG berjalan dalam fallback mode.")

class ChromaRAGEngine:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "chroma_db")
        self.history_file = os.path.join(data_dir, "my_chat_history.json")
        
        self.sessions = []
        self.chroma_client = None
        self.collection = None

        if CHROMADB_AVAILABLE:
            try:
                self.chroma_client = chromadb.PersistentClient(path=self.db_path)
                self.collection = self.chroma_client.get_or_create_collection(
                    name="digital_twin_kb",
                    metadata={"hnsw:space": "cosine"}
                )
            except Exception as e:
                print(f"[!] Error inisialisasi ChromaDB: {e}")
        
        self._load_and_index_sessions()

    def _load_and_index_sessions(self):
        """Load conversation sessions from my_chat_history.json and index into ChromaDB."""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    self.sessions = json.load(f)
            except Exception as e:
                print(f"[!] Error loading {self.history_file}: {e}")
                self.sessions = []
        else:
            self.sessions = []

        self.reindex_all()

    def parse_raw_chat_block(self, partner_name: str, raw_text: str) -> List[Dict[str, str]]:
        """Parse raw WhatsApp style text block."""
        lines = [line.strip() for line in raw_text.strip().split("\n") if line.strip()]
        parsed_messages = []
        partner_name_lower = partner_name.lower()

        for line in lines:
            if ":" in line:
                sender_part, text_part = line.split(":", 1)
                sender = sender_part.strip()
                text = text_part.strip()
                if sender.lower() == partner_name_lower or sender.lower() != "reply":
                    parsed_messages.append({"sender": partner_name, "text": text})
                else:
                    parsed_messages.append({"sender": "reply", "text": text})
            else:
                parsed_messages.append({"sender": "reply", "text": line})

        return parsed_messages

    def add_session(self, partner_name: str, messages: List[Dict[str, str]], summary: str = ""):
        """Add a complete continuous chat session and index to ChromaDB."""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    self.sessions = json.load(f)
            except Exception:
                pass

        session_id = f"session_{len(self.sessions) + 1}"
        session_data = {
            "id": session_id,
            "partner_name": partner_name,
            "summary": summary,
            "messages": messages
        }
        self.sessions.append(session_data)

        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(self.sessions, f, indent=2, ensure_ascii=False)

        if self.collection:
            transcript_lines = [f"Contact: {partner_name}"]
            if summary:
                transcript_lines.append(f"Summary/Context: {summary}")
            for msg in messages:
                sender = msg.get("sender", "reply")
                text = msg.get("text", "")
                transcript_lines.append(f"{sender}: {text}")

            full_transcript = "\n".join(transcript_lines)
            try:
                self.collection.upsert(
                    documents=[full_transcript],
                    metadatas=[{
                        "partner_name": partner_name,
                        "summary": summary,
                        "json_data": json.dumps(session_data, ensure_ascii=False)
                    }],
                    ids=[session_id]
                )
            except Exception as e:
                print(f"[!] Error upsert ChromaDB: {e}")

    def reindex_all(self):
        """Re-index all sessions into ChromaDB."""
        if not self.collection:
            return

        documents = []
        metadatas = []
        ids = []

        for idx, session in enumerate(self.sessions, 1):
            session_id = session.get("id", f"session_{idx}")
            partner_name = session.get("partner_name", "Teman")
            summary = session.get("summary", "")
            messages = session.get("messages", [])

            if not messages and ("partner_msgs" in session or "partner_msg" in session):
                partner_msgs = session.get("partner_msgs", session.get("partner_msg", []))
                if isinstance(partner_msgs, str): partner_msgs = [partner_msgs]
                my_replies = session.get("my_replies", session.get("my_reply", []))
                if isinstance(my_replies, str): my_replies = [my_replies]

                messages = []
                for pm in partner_msgs:
                    messages.append({"sender": partner_name, "text": pm})
                for mr in my_replies:
                    messages.append({"sender": "reply", "text": mr})

            transcript_lines = [f"Contact: {partner_name}"]
            if summary:
                transcript_lines.append(f"Summary/Context: {summary}")
            
            for msg in messages:
                sender = msg.get("sender", "reply")
                text = msg.get("text", "")
                transcript_lines.append(f"{sender}: {text}")

            full_transcript = "\n".join(transcript_lines)
            documents.append(full_transcript)
            metadatas.append({
                "partner_name": partner_name,
                "summary": summary,
                "json_data": json.dumps(session, ensure_ascii=False)
            })
            ids.append(session_id)

        if documents and self.collection:
            try:
                self.collection.upsert(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids
                )
            except Exception as e:
                print(f"[!] Error reindex ChromaDB: {e}")

    def clear_all(self):
        """Clear all stored sessions and reset ChromaDB index."""
        self.sessions = []
        if os.path.exists(self.history_file):
            try:
                os.remove(self.history_file)
            except Exception:
                pass
        
        if self.chroma_client:
            try:
                self.chroma_client.delete_collection("digital_twin_kb")
            except Exception:
                pass
            try:
                self.collection = self.chroma_client.get_or_create_collection(
                    name="digital_twin_kb",
                    metadata={"hnsw:space": "cosine"}
                )
            except Exception:
                pass

    def search_vector_db(self, query: str, top_k: int = 5, distance_threshold: float = 0.65) -> List[Dict[str, Any]]:
        """Perform semantic vector search using ChromaDB embeddings."""
        if not self.collection or self.collection.count() == 0:
            return []

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=min(top_k, self.collection.count()),
                include=["documents", "metadatas", "distances"]
            )
        except Exception as e:
            print(f"[!] Query ChromaDB error: {e}")
            return []

        matched_results = []
        if results and "metadatas" in results and results["metadatas"]:
            metas = results["metadatas"][0]
            docs = results["documents"][0]
            dists = results["distances"][0] if ("distances" in results and results["distances"]) else [0.0] * len(docs)
            for meta, doc, dist in zip(metas, docs, dists):
                if dist <= distance_threshold:
                    matched_results.append({
                        "metadata": meta,
                        "document": doc,
                        "distance": dist
                    })

        return matched_results

    def get_context_for_prompt(self, query: str, top_k: int = 3) -> str:
        """Build structured context string for LLM system prompt."""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    disk_sessions = json.load(f)
                if len(disk_sessions) != len(self.sessions):
                    self.sessions = disk_sessions
                    self.reindex_all()
            except Exception:
                pass

        matches = self.search_vector_db(query, top_k=top_k, distance_threshold=0.65)
        
        if not matches:
            simple_matches = []
            q_lower = query.lower()
            for s in self.sessions:
                for msg in s.get("messages", []):
                    if any(w in msg.get("text", "").lower() for w in q_lower.split() if len(w) > 3):
                        simple_matches.append(s)
                        break

            if simple_matches:
                context_parts = ["=== RIWAYAT CHAT EXPORT (SIMPLE MATCH) ==="]
                for idx, s in enumerate(simple_matches[:top_k], 1):
                    partner = s.get("partner_name", "Teman")
                    context_parts.append(f"--- Session #{idx} ({partner}) ---")
                    for msg in s.get("messages", []):
                        context_parts.append(f"{msg.get('sender', 'reply')}: {msg.get('text', '')}")
                    context_parts.append("")
                return "\n".join(context_parts)

            return "Belum ada riwayat chat export yang relevan. Jawablah sesuai gaya penulisan santai dan logis."

        context_parts = ["=== RIWAYAT CHAT EXPORT (WHATSAPP STYLE CONVERSATION FLOW) ==="]
        for idx, item in enumerate(matches, 1):
            doc = item["document"]
            context_parts.append(f"--- Session #{idx} ---")
            context_parts.append(doc.strip())
            context_parts.append("")

        return "\n".join(context_parts)
