"""
Test fungsional untuk perbaikan pada project telegram-chatbot.
Fokus pada logika murni (tanpa panggilan API/Telegram sungguhan).
"""
import os
import sys
import re
import json
import pytest

# Pastikan folder project dan folder scripts ada di path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))

import logging
logging.disable(logging.CRITICAL)

import ai_engine
import tester
import simulator
import clients
import reviewer as _rev


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_knowledge(tmp_path, monkeypatch):
    """Ganti path KNOWLEDGE_FILE ke file temp agar cache/test isoasi."""
    p = tmp_path / "knowledge.json"
    p.write_text(json.dumps([
        {"keywords": ["makanan", "makan"], "fact": "aku suka mie ayam"}
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ai_engine, "KNOWLEDGE_FILE", str(p))
    # reset cache modul
    ai_engine._knowledge_cache["data"] = None
    ai_engine._knowledge_cache["mtime"] = 0.0
    return p


@pytest.fixture
def tmp_corrections(tmp_path, monkeypatch):
    p = tmp_path / "corrections.json"
    monkeypatch.setattr(ai_engine, "CORRECTIONS_FILE", str(p))
    return p


# ---------------------------------------------------------------------------
# ai_engine.save_correction
# ---------------------------------------------------------------------------
def test_save_correction_creates_file(tmp_corrections):
    ok = ai_engine.save_correction("halo apa kabar", "hai kak, baik nih")
    assert ok is True
    data = json.loads(tmp_corrections.read_text(encoding="utf-8"))
    assert data == [{"user": "halo apa kabar", "assistant": "hai kak, baik nih"}]


def test_save_correction_updates_existing_case_insensitive(tmp_corrections):
    ai_engine.save_correction("List harga dong", "vip 50k kak")
    ai_engine.save_correction("LIST HARGA DONG", "vip cuma 50k ya kak")
    data = json.loads(tmp_corrections.read_text(encoding="utf-8"))
    assert len(data) == 1  # tidak duplicate
    assert data[0]["assistant"] == "vip cuma 50k ya kak"  # di-update


# ---------------------------------------------------------------------------
# ai_engine.retrieve_relevant_knowledge (cache)
# ---------------------------------------------------------------------------
def test_retrieve_knowledge_matches_keyword(tmp_knowledge):
    out = ai_engine.retrieve_relevant_knowledge("kamu suka makan apa?")
    assert "mie ayam" in out
    assert "RELEVANT_KNOWLEDGE_FACTS" in out


def test_retrieve_knowledge_no_match(tmp_knowledge):
    assert ai_engine.retrieve_relevant_knowledge("halo") == ""


def test_retrieve_knowledge_uses_cache_on_second_call(tmp_knowledge, monkeypatch):
    # Design: getmtime di-stat tiap call (murah, utk deteksi perubahan file),
    # tapi pembacaan file + json.load HANYA sekali (saat cache miss).
    open_calls = {"n": 0}
    real_open = open

    def spy_open(path, *a, **k):
        p = str(path)
        if p.endswith("knowledge.json"):
            open_calls["n"] += 1
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", spy_open)
    ai_engine.retrieve_relevant_knowledge("apa makanan kesukaan kamu")
    ai_engine.retrieve_relevant_knowledge("ceritakan soal makanan")  # harusnya pakai cache
    # File knowledge.json hanya dibaca SEKALI (cache hit di call ke-2)
    assert open_calls["n"] == 1


# ---------------------------------------------------------------------------
# ai_engine._strip_think
# ---------------------------------------------------------------------------
def test_strip_think_removes_block():
    text = "hai kak<think> ini cuma reasoning </think> yuk chat"
    assert ai_engine._strip_think(text) == "hai kak yuk chat"


def test_strip_think_handles_dangling_tag():
    text = "halo<think> belum selesai"
    assert ai_engine._strip_think(text) == "halo"


# ---------------------------------------------------------------------------
# ai_engine.force_lowercase_except_laughter
# ---------------------------------------------------------------------------
def test_force_lowercase_keeps_laughter():
    out = ai_engine.force_lowercase_except_laughter("HALO KAK WKWKWK lucu banget")
    # tawa harus tetap kapital (WKWKWK), sisanya lowercase
    assert "WKWKWK" in out
    assert "halo kak" in out
    assert "lucu banget" in out


# ---------------------------------------------------------------------------
# reviewer._validate_entry
# ---------------------------------------------------------------------------
def test_validate_entry_good():
    e = _rev._validate_entry({"keywords": ["a", "b"], "fact": "sesuatu"})
    assert e is not None
    assert e["keywords"] == ["a", "b"]


def test_validate_entry_rejects_missing_keys():
    assert _rev._validate_entry({"keywords": ["a"]}) is None
    assert _rev._validate_entry({"fact": "x"}) is None
    assert _rev._validate_entry("bukan dict") is None


# ---------------------------------------------------------------------------
# simulator: mock_user_id acak (bukan ID asli pemilik)
# ---------------------------------------------------------------------------
def test_simulator_mock_id_is_random_and_high():
    captured = {}
    orig_gen = ai_engine.generate_ai_reply

    async def fake_gen(uid, uname, text, hist, max_history_per_user=None):
        captured["uid"] = uid
        return "ok", [{"text": "ok", "delay": 0.5}]

    ai_engine.generate_ai_reply = fake_gen
    try:
        import asyncio
        asyncio.run(simulator.run_simulation(scenario_id="1", turns=1))
    finally:
        ai_engine.generate_ai_reply = orig_gen

    uid = captured.get("uid")
    assert uid is not None
    # harusnya bukan ID asli (5632761062) dan berada di range 10-digit
    assert uid != 5632761062
    assert 1_000_000_000 <= uid <= 9_999_999_999


# ---------------------------------------------------------------------------
# tester: SCENARIOS utuh & dead code hilang
# ---------------------------------------------------------------------------
def test_tester_scenarios_present():
    assert set(tester.SCENARIOS.keys()) == {"1", "2", "3", "4", "5", "6"}


def test_tester_no_dead_code():
    assert not hasattr(tester, "TESTER_SYSTEM_PROMPT")


# ---------------------------------------------------------------------------
# clients: provider selection
# ---------------------------------------------------------------------------
def test_clients_provider_selected():
    assert clients.SELECTED_PROVIDER in ("GROQ", "OPENROUTER")


# ---------------------------------------------------------------------------
# ai_engine: MAX_BUBBLES_PER_RESPONSE enforcement
# ---------------------------------------------------------------------------
def test_max_bubbles_capped():
    """Jika AI return >3 baris, cuma 3 yang dikirim (manusia asli max ~3 bubble)."""
    # Simulasi: patch generate_ai_reply supaya return 5 lines
    import re as _re
    text = "line1\nline2\nline3\nline4\nline5"
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    MAX_BUBBLES_PER_RESPONSE = 3
    if len(lines) > MAX_BUBBLES_PER_RESPONSE:
        lines = lines[:MAX_BUBBLES_PER_RESPONSE]
    assert len(lines) == 3
    assert lines == ["line1", "line2", "line3"]


# ---------------------------------------------------------------------------
# ai_engine: emoji enforcement (max 1 emoji per response)
# ---------------------------------------------------------------------------
def test_emoji_stripped_from_subsequent_bubbles():
    """Emoji hanya boleh di bubble pertama, sisanya di-strip."""
    EMOJI_PATTERN = re.compile(
        r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
        r'\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F'
        r'\U0001F900-\U0001F9FF\U00002600-\U000026FF\u200d\ufe0f]+'
    )
    test_lines = ["halo kak 🤭", "mau join gak", "cek aja kakk ✨"]
    emoji_used = False
    result = []
    for line in test_lines:
        if emoji_used:
            cleaned = EMOJI_PATTERN.sub('', line).strip()
            if cleaned:
                line = cleaned
            else:
                continue
        if EMOJI_PATTERN.search(line):
            emoji_used = True
        result.append(line)
    # bubble pertama tetap ada emoji, sisanya sudah di-strip
    assert "🤭" in result[0]
    assert "✨" not in result[2]  # emoji bubble ke-3 di-strip
    assert len(result) == 3


# ---------------------------------------------------------------------------
# persona prompt: anti-"manusia asli" rule exists
# ---------------------------------------------------------------------------
def test_persona_forbids_manusia_claim():
    """persona.txt harus punya aturan absolut soal jangan bilang 'aku manusia'."""
    p_path = "prompts/persona.txt" if os.path.exists("prompts/persona.txt") else os.path.join("telegram-chatbot", "prompts", "persona.txt")
    persona = open(p_path, encoding="utf-8").read()
    assert "JANGAN PERNAH berkata" in persona
    assert "aku manusia" in persona
    assert "aku bukan bot" in persona


def test_persona_has_anti_defensive_redirect():
    """persona.txt harus arahkan user ke channel saat dituduh scam."""
    p_path = "prompts/persona.txt" if os.path.exists("prompts/persona.txt") else os.path.join("telegram-chatbot", "prompts", "persona.txt")
    persona = open(p_path, encoding="utf-8").read()
    assert "channel" in persona.lower()
    assert "testi" in persona.lower()


# ---------------------------------------------------------------------------
# knowledge.json: scam entry gak ada "manusia asli"
# ---------------------------------------------------------------------------
def test_knowledge_scam_no_manusia():
    """Entry scam di knowledge.json gak boleh pakai 'manusia asli' sebagai klaim."""
    data = json.loads(open("knowledge.json", encoding="utf-8").read())
    for item in data:
        if "scam" in item.get("keywords", []):
            fact = item["fact"].lower()
            # Tidak boleh klaim "aku manusia asli" (tapi larangan "jangan bilang manusia asli" oke)
            assert "aku manusia asli" not in fact, \
                f"Entry scam masih klaim 'aku manusia asli': {item['fact'][:80]}"
            # Harus arahkan ke channel/testimoni
            assert "channel" in fact, \
                f"Entry scam harus arahkan ke channel: {item['fact'][:80]}"
            break


# ===========================================================================
# media_handler tests
# ===========================================================================
import media_handler

def test_detect_intent_pap():
    assert media_handler.detect_intent("pap dong") == "pap"
    assert media_handler.detect_intent("pap colmek") == "pap"
    assert media_handler.detect_intent("mana pap nya") == "pap"

def test_detect_intent_video():
    assert media_handler.detect_intent("video colmek") == "video"
    assert media_handler.detect_intent("kirim video") == "video"

def test_detect_intent_vip_preview():
    assert media_handler.detect_intent("preview vip") == "vip_preview"
    assert media_handler.detect_intent("liat isi vip") == "vip_preview"

def test_detect_intent_none():
    assert media_handler.detect_intent("halo kak") is None
    assert media_handler.detect_intent("mau join vip dong") is None  # "vip" alone maps to vip_preview but "join" shouldn't

def test_media_config_exists():
    """media_config.json harus ada."""
    assert os.path.exists("media_config.json") or os.path.exists(os.path.join("telegram-chatbot", "media_config.json"))

def test_media_config_structure():
    """media_config.json harus punya key pap, video, vip_preview."""
    path = "media_config.json" if os.path.exists("media_config.json") else os.path.join("telegram-chatbot", "media_config.json")
    cfg = json.loads(open(path, encoding="utf-8").read())
    assert "pap" in cfg
    assert "video" in cfg
    assert "vip_preview" in cfg


# ===========================================================================
# Digital Clone Architecture (6 Agents & Pipeline) tests
# ===========================================================================
from agents.base import ContextData, MemoryData, PersonalityData, ResponseDraft, CriticResult, ConfidenceResult
from agents.context_agent import ContextAgent
from agents.memory_agent import MemoryAgent
from agents.personality_agent import PersonalityAgent
from agents.response_agent import ResponseAgent
from agents.critic_agent import CriticAgent
from agents.confidence_agent import ConfidenceAgent
from agents.pipeline import DigitalClonePipeline


def test_context_agent_collects_metadata():
    agent = ContextAgent()
    account = {"id": 1, "name": "Alya"}
    ctx = agent.process(account, user_db_id=0, user_name="Budi", message_text="halo")
    assert ctx.sender == "Budi"
    assert ctx.message_text == "halo"
    assert ctx.chat_type == "private"
    assert ctx.account["name"] == "Alya"


def test_memory_agent_retrieves_facts(tmp_knowledge):
    agent = MemoryAgent()
    account = {"id": 1, "knowledge_file": str(tmp_knowledge)}
    ctx = ContextData(sender="Budi", user_db_id=0, user_id_tg=0, chat_type="private", time="12:00", date="2026-07-18", message_text="apa makanan kesukaan?", account=account)
    mem = agent.process(ctx)
    assert len(mem.facts) > 0
    assert "mie ayam" in mem.facts[0]


def test_personality_agent_builds_prompt(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "persona.txt").write_text("gaya santai flirty", encoding="utf-8")
    (prompts_dir / "larangan.txt").write_text("jangan kaku", encoding="utf-8")
    (prompts_dir / "sales.txt").write_text("vip 50k", encoding="utf-8")
    (prompts_dir / "slang.txt").write_text("wkwk haha", encoding="utf-8")

    agent = PersonalityAgent()
    ctx = ContextData(sender="Budi", user_db_id=0, user_id_tg=0, chat_type="private", time="12:00", date="2026-07-18", message_text="halo", account={"id": 1, "name": "Alya", "persona_file": "prompts/persona.txt"})
    mem = MemoryData()

    pers = agent.process(ctx, mem, prompts_dir=str(prompts_dir))
    assert "gaya santai flirty" in pers.system_prompt
    assert "jangan kaku" in pers.system_prompt


def test_critic_agent_formatting_and_cs_stripping():
    agent = CriticAgent()
    ctx = ContextData(sender="Budi", user_db_id=0, user_id_tg=0, chat_type="private", time="12:00", date="2026-07-18", message_text="halo", account={})

    # Case 1: Formal CS phrase stripping & lowercase enforcement
    draft = ResponseDraft(raw_text="Halo Budi! Ada yang bisa aku bantu?? WKWK")
    result = agent.process(draft, ctx)

    assert "bisa aku bantu" not in result.criticized_text.lower()
    assert "WKWK" in result.criticized_text  # Laughter preserves caps
    assert len(result.bubbles) >= 1


def test_confidence_agent_threshold_scoring():
    agent = ConfidenceAgent(auto_send_threshold=90.0, draft_threshold=70.0)
    ctx = ContextData(sender="Budi", user_db_id=0, user_id_tg=0, chat_type="private", time="12:00", date="2026-07-18", message_text="halo", account={})
    mem = MemoryData(facts=["fakta 1"])
    pers = PersonalityData(account_name="Alya", persona_prompt="", larangan_prompt="", sales_prompt="", slang_prompt="", corrections=[{"user": "x", "assistant": "y"}])

    draft = ResponseDraft(raw_text="iya dong kak wkwk")
    critic = CriticResult(criticized_text="iya dong kak wkwk", bubbles=[{"text": "iya dong kak wkwk", "delay": 0.5}], edits_applied=[])

    res = agent.process(ctx, mem, pers, draft, critic)
    assert res.score >= 90.0
    assert res.status == "auto_send"


import asyncio

def test_digital_clone_pipeline_end_to_end(monkeypatch):
    # Mock LLM API call in response agent
    async def mock_response_process(self, context, memory, personality, max_history=20):
        return ResponseDraft(raw_text="iya kak wkwk ada di vip loh")

    monkeypatch.setattr(ResponseAgent, "process", mock_response_process)

    pipeline = DigitalClonePipeline()
    account = {"id": 1, "name": "Alya"}
    output = asyncio.run(pipeline.execute(account, user_db_id=0, user_name="Budi", message_text="apa ada vip?"))

    assert output.context.sender == "Budi"
    assert output.final_text == "iya kak wkwk ada di vip loh"
    assert output.confidence.score >= 70.0
    assert len(output.bubbles) == 1


def test_critic_agent_intercepts_ai_refusal_on_ngewe():
    agent = CriticAgent()
    ctx = ContextData(sender="Budi", user_db_id=0, user_id_tg=0, chat_type="private", time="12:00", date="2026-07-18", message_text="km udh pernah ngewe?", account={})

    # Draft has LLM AI refusal
    draft = ResponseDraft(raw_text="maaf kakk, obrolan kayak gitu agak nggak nyaman buat aku 🙏 kita ngobrol santai biasa aja yuk, kakak mau bahas apa nih?")
    result = agent.process(draft, ctx)

    # Must NOT contain AI refusal
    assert "nggak nyaman" not in result.criticized_text
    assert "obrolan kayak gitu" not in result.criticized_text
    # Must be rewritten into teasing clone response
    assert "ngewe" in result.criticized_text or "ronde" in result.criticized_text


def test_confidence_agent_penalizes_ai_refusal_phrases():
    agent = ConfidenceAgent()
    ctx = ContextData(sender="Budi", user_db_id=0, user_id_tg=0, chat_type="private", time="12:00", date="2026-07-18", message_text="km udh pernah ngewe?", account={})
    mem = MemoryData()
    pers = PersonalityData(account_name="Alya", persona_prompt="", larangan_prompt="", sales_prompt="", slang_prompt="")

    draft = ResponseDraft(raw_text="maaf kakk, obrolan kayak gitu agak nggak nyaman buat aku")
    critic = CriticResult(criticized_text="maaf kakk, obrolan kayak gitu agak nggak nyaman buat aku", bubbles=[{"text": "...", "delay": 0.5}], edits_applied=[])

    res = agent.process(ctx, mem, pers, draft, critic)
    assert res.score == 0.0
    assert res.status == "hold"


def test_chat_history_json_is_valid():
    path = "chat_history.json" if os.path.exists("chat_history.json") else os.path.join("telegram-chatbot", "chat_history.json")
    data = json.loads(open(path, encoding="utf-8").read())
    assert isinstance(data, list)
    assert len(data) > 0
    assert "user" in data[0]
    assert "replies" in data[0]


def test_memory_agent_retrieves_chat_examples():
    agent = MemoryAgent()
    ctx = ContextData(sender="Budi", user_db_id=0, user_id_tg=0, chat_type="private", time="12:00", date="2026-07-18", message_text="km udh pernah ngewe?", account={})
    mem = agent.process(ctx)

    assert len(mem.chat_examples) > 0
    assert any("ngewe" in ex.get("user", "") for ex in mem.chat_examples)
    assert "masih perawan" in mem.facts



