import asyncio
import logging
from typing import Dict, Any, Optional
from core import clients
from .base import ContextData, MemoryData, PersonalityData, ResponseDraft

logger = logging.getLogger("ResponseAgent")

async def _call_api_with_retry(messages, max_retries=3):
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
            logger.warning(f"ResponseAgent API call #{attempt} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
    logger.error(f"ResponseAgent failed to call API after {max_retries} attempts: {last_err}")
    return None

class ResponseAgent:
    """
    4. Response Agent
    Tujuan: Menyusun jawaban berdasarkan seluruh context, memory, dan personality rules.
    Satu-satunya agent yang benar-benar menghasilkan isi balasan melalui LLM API.
    """

    async def process(
        self,
        context: ContextData,
        memory: MemoryData,
        personality: PersonalityData,
        max_history: int = 20
    ) -> ResponseDraft:
        if not clients.client:
            logger.error("Client AI tidak terinisialisasi.")
            return ResponseDraft(raw_text="")

        messages = []
        for msg in context.last_messages[-max_history:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Check if the last message in history is the current message.
        has_current = False
        if messages:
            last_msg = messages[-1]
            if last_msg["role"] == "user" and last_msg["content"] == context.message_text:
                has_current = True

        if not has_current:
            messages.append({"role": "user", "content": context.message_text})

        messages.insert(0, {"role": "system", "content": personality.system_prompt})

        response = await _call_api_with_retry(messages)
        if response is None:
            return ResponseDraft(raw_text="", messages_payload=messages)

        try:
            raw_text = response.choices[0].message.content.strip()
        except (AttributeError, IndexError, KeyError) as e:
            logger.error(f"Format respons API tidak terduga: {e}")
            raw_text = ""

        draft = ResponseDraft(raw_text=raw_text, messages_payload=messages)
        logger.debug(f"ResponseAgent generated raw draft length: {len(raw_text)}")
        return draft
