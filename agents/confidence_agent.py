import re
import logging
from typing import Dict, Any, Optional
from .base import ContextData, MemoryData, PersonalityData, ResponseDraft, CriticResult, ConfidenceResult

logger = logging.getLogger("ConfidenceAgent")

FORMAL_BANNED_WORDS = [
    "saya", "mohon maaf", "dapatkah", "bantuan", "konsultasi",
    "sebagai ai", "sebagai asisten", "apakah ada yang bisa", "terima kasih banyak",
    "nggak nyaman buat aku", "obrolan kayak gitu"
]

class ConfidenceAgent:
    """
    6. Confidence Agent
    Tujuan: Mengukur tingkat keyakinan AI terhadap jawaban.
    Threshold:
    - >= 90%: auto_send (Kirim otomatis)
    - 70-89%: draft_send (Kirim draft / warning flag)
    - < 70%: hold (Tahan jawaban, minta approval user)
    """

    def __init__(self, auto_send_threshold: float = 90.0, draft_threshold: float = 70.0):
        self.auto_send_threshold = auto_send_threshold
        self.draft_threshold = draft_threshold

    def process(
        self,
        context: ContextData,
        memory: MemoryData,
        personality: PersonalityData,
        draft: ResponseDraft,
        critic: CriticResult
    ) -> ConfidenceResult:
        if not draft.raw_text or not critic.criticized_text:
            return ConfidenceResult(
                score=0.0,
                status="hold",
                reason="Teks jawaban kosong atau invalid."
            )

        # 1. Check for AI Refusal / Formal CS patterns in raw draft or criticized text
        text_lower = (draft.raw_text + " " + critic.criticized_text).lower()
        found_formal = [w for w in FORMAL_BANNED_WORDS if w in text_lower]

        if found_formal:
            logger.warning(f"ConfidenceAgent detected AI refusal/formal phrases: {found_formal}")
            return ConfidenceResult(
                score=0.0,
                status="hold",
                reason=f"Terdeteksi frasa penolakan AI / kaku: {', '.join(found_formal)}"
            )

        score = 60.0  # Base confidence — perlu bonus dari context supaya naik
        reasons = []

        # 2. Fact matching bonus (+15%)
        if memory.facts:
            score += 15.0
            reasons.append("Sesuai dengan fakta knowledge base.")

        # 3. Correction matching bonus (+10%)
        if personality.corrections:
            score += 10.0
            reasons.append("Menggunakan acuan koreksi .revisi DB.")

        # 4. Intercepted rewrite check
        if "intercepted_and_rewrote_ai_refusal" in critic.edits_applied:
            score = 95.0
            reasons.append("Teks telah dikoreksi oleh CriticAgent menjadi balasan clone asli.")

        # 5. Natural short length bonus (+5%)
        if len(critic.criticized_text.split()) <= 25:
            score += 5.0
            reasons.append("Panjang kalimat ringkas dan natural.")

        # 6. History context bonus (+10%) — lebih percaya diri kalau ada history
        if context.last_messages and len(context.last_messages) >= 4:
            score += 10.0
            reasons.append("Cukup konteks dari history percakapan.")

        # Limit score range to 0 - 100
        score = max(0.0, min(100.0, score))

        # Decision based on thresholds
        if score >= self.auto_send_threshold:
            status = "auto_send"
        elif score >= self.draft_threshold:
            status = "draft_send"
        else:
            status = "hold"

        reason_str = " | ".join(reasons) if reasons else "Skor evaluasi clone standar."
        logger.debug(f"ConfidenceAgent computed score={score:.1f}%, status={status}, reason={reason_str}")

        return ConfidenceResult(
            score=score,
            status=status,
            reason=reason_str
        )
