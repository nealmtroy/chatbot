import re
import logging
from typing import Dict, Any, List
from .base import ContextData, ResponseDraft, CriticResult

logger = logging.getLogger("CriticAgent")

EMOJI_PATTERN = re.compile(
    r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
    r'\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F'
    r'\U0001F900-\U0001F9FF\U00002600-\U000026FF\u200d\ufe0f]+'
)

CS_BANNED_PATTERNS = [
    r'ada yang (bisa|perlu) aku bantu\??',
    r'bisa aku bantu\??',
    r'ada yang mau ditanyakan\??',
    r'senang bertemu\s*.*',
    r'selamat datang\s*.*',
    r'butuh bantuan\??',
    r'biar aku bantu\s*.*',
    r'konsultasi\s*.*',
]

AI_REFUSAL_PATTERNS = [
    r'obrolan kayak gitu',
    r'nggak nyaman',
    r'tidak nyaman',
    r'ngobrol santai biasa',
    r'sebagai ai',
    r'sebagai asisten',
    r'saya tidak bisa',
    r'aku tidak bisa memenuhi',
    r'kebijakan keamanan',
    r'bahas apa nih\??',
    r'kasar gitu',
    r'ngomong kasar',
]

def _strip_think(text: str) -> str:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    if '<think>' in text:
        text = text.split('<think>')[0]
    return text.strip()

def force_lowercase_except_laughter(text: str) -> str:
    laugh_markers = ["WKWK", "HAHA", "HEHE", "HIHI"]
    lines = text.split("\n")
    processed_lines = []
    for line in lines:
        words = line.split(" ")
        processed_words = []
        for w in words:
            clean_w = ''.join(c for c in w if c.isalnum()).upper()
            is_laugh = any(laugh in clean_w for laugh in laugh_markers) and len(clean_w) >= 3

            if is_laugh and any(c.isupper() for c in w):
                processed_words.append(w)
            else:
                processed_words.append(w.lower())
        processed_lines.append(" ".join(processed_words))
    return "\n".join(processed_lines)

def strip_formatting_and_limit_emojis(text: str) -> str:
    text = text.replace("~", "").replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r'</?(b|i|code|pre|a|em|strong)>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^\s*[*•-]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', text)

    emojis = EMOJI_PATTERN.findall(text)
    if len(emojis) > 1:
        first_emoji = emojis[0]
        token = "___EMOJI_FIRST_TOKEN___"
        text = text.replace(first_emoji, token, 1)
        text = EMOJI_PATTERN.sub('', text)
        text = text.replace(token, first_emoji)

    return text

class CriticAgent:
    """
    5. Critic Agent
    Tujuan: Menjadi editor terakhir sebelum pesan dikirim.
    Pemeriksaan & Tindakan:
    - Stripping tag <think>
    - Protect [ACTION: ...] tag agar tidak terdistorsi/terlower-case
    - Cegat & auto-rewrite jika terdeteksi refusal AI / CS (seperti 'obrolan kayak gitu nggak nyaman')
    - Batasi kebiasaan kapitalisasi (lowercase kecuali tawa)
    - Batasi emoji (maksimal 1 per percakapan)
    - Pembagian bubble chat & estimasi typing delay (maksimal 3 bubble)
    """

    def _intercept_refusal_rewrite(self, user_msg: str) -> str:
        msg_lower = user_msg.lower()
        if any(w in msg_lower for w in ["ngewe", "entot", "wikwik", "kontol", "ngaceng", "memek", "pepek", "sange"]):
            return "dih nakal banget sih kakk wkwk 🫣 di grup vip aku ada video ngewe dan colmek aku loh"
        if "vcs" in msg_lower:
            return "vc biasa dulu dong kak, kalo langsung vcs entar kakak ketagihan lagi 🤭"
        if "open" in msg_lower or "bo" in msg_lower:
            return "ga open bo kakk, cuma ada grup vip koleksi pribadi aku aja 🤭"
        if any(w in msg_lower for w in ["colmek", "coli", "pap"]):
            return "pernahh kakk wkwk, kemarin malah aku colmek sampe lemes banget 🤭"
        return "halo kakk, kenapa? 🤭"

    def process(self, draft: ResponseDraft, context: ContextData) -> CriticResult:
        edits = []
        text = draft.raw_text

        if not text:
            return CriticResult(
                criticized_text="halo kakk, kenapa?",
                bubbles=[{"text": "halo kakk, kenapa?", "delay": 0.5}],
                edits_applied=["empty_fallback"],
                is_valid=True
            )

        # 0. Protect [ACTION: ...] tags from lowercasing and markdown stripping
        action_tags = re.findall(r'\[ACTION:\s*[A-Z0-9_]+\]', text, flags=re.IGNORECASE)
        token_map = {}
        for idx, tag in enumerate(action_tags):
            token = f"ACTIONTOKENXYZ{idx}"
            token_map[token] = tag.upper()
            text = text.replace(tag, token, 1)

        # 1. Strip <think> tag
        stripped_think = _strip_think(text)
        if stripped_think != text:
            edits.append("stripped_think_tags")
            text = stripped_think

        # 2. Check AI refusal & CS patterns
        text_lower = text.lower()
        is_refusal = any(re.search(pat, text_lower) for pat in AI_REFUSAL_PATTERNS)
        if is_refusal:
            logger.warning(f"CriticAgent intercepted AI refusal in draft: '{text}'")
            text = self._intercept_refusal_rewrite(context.message_text)
            edits.append("intercepted_and_rewrote_ai_refusal")

        # 3. Case normalization (lowercase kecuali tawa)
        cased_text = force_lowercase_except_laughter(text)
        if cased_text != text:
            edits.append("enforced_lowercase_except_laughter")
            text = cased_text

        # 4. Strip formatting & limit emojis
        formatted_text = strip_formatting_and_limit_emojis(text)
        if formatted_text != text:
            edits.append("stripped_markdown_and_limited_emojis")
            text = formatted_text

        # 5. Remove user telegram name repetition if LLM outputted it
        user_name = context.sender
        if user_name and len(user_name) >= 3:
            old = text
            text = re.sub(rf'\b{re.escape(user_name)}\b', 'kak', text, flags=re.IGNORECASE).strip()
            text = re.sub(r'halo\s+kak\b', 'halo kakk', text, flags=re.IGNORECASE).strip()
            if text != old:
                edits.append("replaced_user_name_with_kak")

        # 6. Remove formal CS phrases
        for pattern in CS_BANNED_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                text = re.sub(pattern, '', text, flags=re.IGNORECASE).strip()
                edits.append(f"removed_cs_phrase:{pattern}")

        text = re.sub(r'\s+', ' ', text).strip()
        text = strip_formatting_and_limit_emojis(text)

        if not text:
            text = "halo kakk, kenapa?"
            edits.append("fallback_to_default_greeting")

        # 7. Bubble splitting & delay calculation (max 3 bubbles)
        reply_lines = [line.strip() for line in text.split("\n") if line.strip()]
        MAX_BUBBLES = 3
        if len(reply_lines) > MAX_BUBBLES:
            reply_lines = reply_lines[:MAX_BUBBLES]
            edits.append("truncated_excess_bubbles")

        bubbles = []
        emoji_used = False

        for i, line in enumerate(reply_lines):
            if emoji_used:
                cleaned = EMOJI_PATTERN.sub('', line).strip()
                if cleaned:
                    line = cleaned
                else:
                    continue

            if EMOJI_PATTERN.search(line):
                emoji_used = True

            if i == 0:
                delay = 0.5
            else:
                delay = max(0.6, min(len(line) * 0.05, 3.0))

            bubbles.append({
                "text": line,
                "delay": delay
            })

        logger.debug(f"CriticAgent finalized text ({len(bubbles)} bubbles, edits={edits})")

        # Rebuild criticized_text from bubbles agar konsisten
        # (bubble bisa skip line karena emoji dedup di atas)
        final_text = "\n".join(b["text"] for b in bubbles) if bubbles else text

        # Restore protected action tags
        for token, orig_tag in token_map.items():
            final_text = final_text.replace(token.lower(), orig_tag).replace(token, orig_tag)
            for b in bubbles:
                b["text"] = b["text"].replace(token.lower(), orig_tag).replace(token, orig_tag)

        return CriticResult(
            criticized_text=final_text,
            bubbles=bubbles,
            edits_applied=edits,
            is_valid=True
        )
