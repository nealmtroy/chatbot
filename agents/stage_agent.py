"""
stage_agent.py - LLM-Powered Sales Funnel Stage Detection.

Menggantikan keyword matching di user_tracker dengan analisis LLM yang memahami
KESELURUHAN alur percakapan untuk menentukan stage user di sales funnel.

Posisi di pipeline: setelah Memory Agent, sebelum Personality Agent.
Stage yang dihasilkan mempengaruhi seberapa agresif prompt penjualan.

Optimasi:
  - Skip LLM kalau stage sudah terminal (member, vcs_booked, lost)
  - Skip LLM kalau history kosong (user baru, otomatis greeted)
  - Prompt compact (~200 token system) untuk hemat API cost
"""
import json
import re
import asyncio
import logging
from typing import Dict, Any, Optional

from core import clients, db
from .base import ContextData, MemoryData, StageResult

logger = logging.getLogger("StageAgent")

# Stage yang tidak perlu di-analisis ulang via LLM
TERMINAL_STAGES = {"member", "vcs_offered", "vcs_booked"}

# Stage order untuk validasi (stage hanya boleh naik, kecuali lost)
STAGE_ORDER = {
    "new": 0,
    "greeted": 1,
    "interested": 2,
    "asked_price": 3,
    "payment_pending": 4,
    "member": 5,
    "vcs_offered": 6,
    "vcs_booked": 7,
    "lost": 99,  # lost bisa dari mana saja
}

VALID_STAGES = set(STAGE_ORDER.keys())

STAGE_ANALYSIS_PROMPT = """\
Kamu adalah Sales Funnel Analyzer untuk akun chatbot cewek di Telegram.
Tugas: tentukan stage user di funnel penjualan berdasarkan percakapan.

STAGE (berurutan dari awal ke akhir):
- new: baru pertama kali chat, belum ada interaksi berarti
- greeted: sudah saling sapa / basa-basi / small talk
- interested: menunjukkan ketertarikan nyata terhadap konten (minta pap, tanya isi VIP, penasaran konten, minta kirim media, topik seksual mengarah ke konten)
- asked_price: secara eksplisit menanyakan HARGA / BIAYA / TARIF VIP. HANYA stage ini kalau user benar-benar nanya harga bukan hal lain
- payment_pending: sudah dikasih QRIS / metode bayar (JANGAN set ini, ini diatur sistem)
- member: sudah bayar (JANGAN set ini, ini diatur sistem)
- lost: user menuduh scam, marah besar, mengancam block, ghosting, atau jelas tidak tertarik

ATURAN PENTING:
1. Stage hanya boleh NAIK dari stage saat ini (kecuali ke "lost")
2. PENGECUALIAN: jika stage saat ini "payment_pending" dan user jelas sudah pindah topik/ngobrol santai/tidak membahas pembayaran, boleh turunkan ke stage yang sesuai (greeted/interested)
3. Lihat KESELURUHAN alur, bukan cuma 1 pesan
4. "berapa umur kamu", "berapa tahun" = small talk, BUKAN asked_price
5. User yang cuma horny/sange tanpa konteks harga = interested, BUKAN asked_price
6. JANGAN pernah output payment_pending atau member
7. Kalau ragu, pertahankan stage saat ini

Jawab HANYA dalam JSON (tanpa markdown):
{"stage": "...", "reason": "penjelasan singkat 1 kalimat"}"""


def _build_history_summary(messages: list, max_msgs: int = 8) -> str:
    """Format history ringkas untuk prompt LLM."""
    recent = messages[-max_msgs:] if len(messages) > max_msgs else messages
    if not recent:
        return "(belum ada percakapan)"
    lines = []
    for msg in recent:
        role_label = "User" if msg.get("role") == "user" else "Bot"
        content = msg.get("content", "")[:120]  # Truncate pesan panjang
        lines.append(f"{role_label}: {content}")
    return "\n".join(lines)


def _parse_stage_json(raw_text: str) -> Optional[Dict[str, str]]:
    """Parse JSON dari response LLM, handle berbagai format output."""
    # Hapus <think> tag kalau ada
    raw_text = re.sub(r'<think>.*?</think>', '', raw_text, flags=re.DOTALL)
    if '<think>' in raw_text:
        raw_text = raw_text.split('<think>')[0]
    raw_text = raw_text.strip()

    # Coba parse langsung
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    # Coba extract JSON dari teks (model kadang bungkus markdown)
    json_match = re.search(r'\{[^}]+\}', raw_text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    logger.warning(f"Gagal parse stage JSON dari LLM: '{raw_text[:200]}'")
    return None


async def _call_stage_llm(messages: list, max_retries: int = 2):
    """Panggil LLM untuk stage analysis dengan retry minimal."""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            response = await clients.client.chat.completions.create(
                model=clients.active_model,
                messages=messages,
                temperature=0.2,       # Deterministik untuk consistency
                max_tokens=100,        # Output pendek, hemat token
            )
            return response
        except Exception as e:
            last_err = e
            logger.warning(f"StageAgent LLM call #{attempt} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(1)
    logger.error(f"StageAgent gagal call LLM setelah {max_retries}x: {last_err}")
    return None


class StageAgent:
    """
    3. Stage Agent (NEW)
    Tujuan: Menganalisis keseluruhan trajectory percakapan untuk menentukan
    stage user di sales funnel secara akurat menggunakan LLM.

    Menggantikan keyword matching yang bisa false-positive.
    Dioptimasi: skip LLM call untuk stage terminal dan history kosong.
    """

    async def process(self, context: ContextData, memory: MemoryData) -> StageResult:
        current_stage = memory.user_stage
        user_db_id = context.user_db_id
        account_id = context.account.get("id", 0)

        # --- Optimasi 1: Skip kalau stage sudah terminal ---
        if current_stage in TERMINAL_STAGES:
            logger.debug(f"StageAgent skip: stage '{current_stage}' sudah terminal")
            return StageResult(
                previous_stage=current_stage,
                new_stage=current_stage,
                reasoning=f"Stage '{current_stage}' sudah terminal, tidak perlu analisis ulang.",
                should_update=False,
            )

        # --- Optimasi 2: Skip kalau client belum siap ---
        if not clients.client:
            logger.warning("StageAgent skip: AI client belum terinisialisasi")
            return StageResult(
                previous_stage=current_stage,
                new_stage=current_stage,
                reasoning="AI client tidak tersedia.",
                should_update=False,
            )

        # --- Optimasi 3: Kalau history kosong dan stage new, otomatis greeted ---
        if not context.last_messages and current_stage == "new":
            new_stage = "greeted"
            if user_db_id:
                db.advance_stage(user_db_id, new_stage)
            return StageResult(
                previous_stage="new",
                new_stage=new_stage,
                reasoning="Percakapan pertama, otomatis greeted.",
                should_update=True,
            )

        # --- Build LLM prompt ---
        profile_str = memory.profile_summary or "(belum diketahui)"
        history_str = _build_history_summary(context.last_messages)

        user_prompt = (
            f"Stage saat ini: {current_stage}\n"
            f"Profil user: {profile_str}\n"
            f"Pesan terakhir user: \"{context.message_text}\"\n\n"
            f"History percakapan:\n{history_str}"
        )

        messages = [
            {"role": "system", "content": STAGE_ANALYSIS_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        # --- Call LLM ---
        response = await _call_stage_llm(messages)
        if response is None:
            return StageResult(
                previous_stage=current_stage,
                new_stage=current_stage,
                reasoning="LLM call gagal, pertahankan stage saat ini.",
                should_update=False,
            )

        # --- Parse response ---
        try:
            raw_text = response.choices[0].message.content.strip()
        except (AttributeError, IndexError):
            raw_text = ""

        parsed = _parse_stage_json(raw_text)
        if not parsed or "stage" not in parsed:
            logger.warning(f"StageAgent: response tidak valid, raw='{raw_text[:200]}'")
            return StageResult(
                previous_stage=current_stage,
                new_stage=current_stage,
                reasoning="Response LLM tidak bisa di-parse, pertahankan stage.",
                should_update=False,
            )

        suggested_stage = parsed["stage"].strip().lower()
        reason = parsed.get("reason", "Tidak ada alasan.")

        # --- Validasi stage ---
        if suggested_stage not in VALID_STAGES:
            logger.warning(f"StageAgent: LLM suggest stage invalid '{suggested_stage}'")
            return StageResult(
                previous_stage=current_stage,
                new_stage=current_stage,
                reasoning=f"LLM suggest stage invalid: '{suggested_stage}'",
                should_update=False,
            )

        # Cegah set payment_pending atau member dari LLM (ini urusan sistem)
        if suggested_stage in ("payment_pending", "member", "vcs_offered", "vcs_booked"):
            logger.info(f"StageAgent: LLM suggest '{suggested_stage}' tapi itu system-only, skip")
            return StageResult(
                previous_stage=current_stage,
                new_stage=current_stage,
                reasoning=f"LLM suggest '{suggested_stage}' tapi stage itu hanya bisa di-set oleh sistem.",
                should_update=False,
            )

        # Stage hanya boleh naik (kecuali lost, atau downgrade dari payment_pending)
        if suggested_stage != "lost" and current_stage != "payment_pending":
            cur_order = STAGE_ORDER.get(current_stage, 0)
            new_order = STAGE_ORDER.get(suggested_stage, 0)
            if new_order <= cur_order:
                # LLM suggest stage yang sama atau lebih rendah
                return StageResult(
                    previous_stage=current_stage,
                    new_stage=current_stage,
                    reasoning=f"LLM suggest '{suggested_stage}' tapi tidak lebih tinggi dari '{current_stage}'. {reason}",
                    should_update=False,
                )

        # --- Update DB ---
        should_update = suggested_stage != current_stage
        if should_update and user_db_id:
            db.advance_stage(user_db_id, suggested_stage)
            logger.info(f"StageAgent: stage updated {current_stage} -> {suggested_stage} | {reason}")

        return StageResult(
            previous_stage=current_stage,
            new_stage=suggested_stage if should_update else current_stage,
            reasoning=reason,
            should_update=should_update,
        )
