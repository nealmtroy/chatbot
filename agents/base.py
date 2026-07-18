from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class ContextData:
    sender: str
    user_db_id: int
    user_id_tg: int
    chat_type: str
    time: str
    date: str
    message_text: str
    last_messages: List[Dict[str, str]] = field(default_factory=list)
    account: Dict[str, Any] = field(default_factory=dict)

@dataclass
class MemoryData:
    nickname: str = "Teman"
    relationship: str = "Penggemar"
    favorite_topics: List[str] = field(default_factory=list)
    chat_habit: str = "Santai"
    facts: List[str] = field(default_factory=list)
    chat_examples: List[Dict[str, Any]] = field(default_factory=list)
    user_stage: str = "new"
    profile_summary: str = ""

@dataclass
class PersonalityData:
    account_name: str
    persona_prompt: str
    larangan_prompt: str
    sales_prompt: str
    slang_prompt: str
    corrections: List[Dict[str, str]] = field(default_factory=list)
    system_prompt: str = ""

@dataclass
class ResponseDraft:
    raw_text: str
    messages_payload: List[Dict[str, str]] = field(default_factory=list)

@dataclass
class CriticResult:
    criticized_text: str
    bubbles: List[Dict[str, Any]] = field(default_factory=list)
    edits_applied: List[str] = field(default_factory=list)
    is_valid: bool = True

@dataclass
class ConfidenceResult:
    score: float  # 0.0 to 100.0
    status: str   # "auto_send", "draft_send", "hold"
    reason: str

@dataclass
class StageResult:
    previous_stage: str   # stage sebelum analisis
    new_stage: str        # stage hasil analisis LLM
    reasoning: str        # penjelasan dari LLM
    should_update: bool   # True kalau stage berubah

@dataclass
class PipelineOutput:
    context: ContextData
    memory: MemoryData
    stage: StageResult
    personality: PersonalityData
    draft: ResponseDraft
    critic: CriticResult
    confidence: ConfidenceResult
    final_text: str
    bubbles: List[Dict[str, Any]]
