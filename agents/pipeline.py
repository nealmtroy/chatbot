import logging
from typing import Dict, Any, Tuple, List
from .base import PipelineOutput
from .context_agent import ContextAgent
from .memory_agent import MemoryAgent
from .stage_agent import StageAgent
from .personality_agent import PersonalityAgent
from .response_agent import ResponseAgent
from .critic_agent import CriticAgent
from .confidence_agent import ConfidenceAgent

logger = logging.getLogger("DigitalClonePipeline")

class DigitalClonePipeline:
    """
    High Level Pipeline Flow (7 Agents):
        Pesan Masuk
             │
             ▼
    1. Context Agent     ← kumpulkan konteks (pesan, history, waktu)
             │
             ▼
    2. Memory Agent      ← ingatan, fakta, profil user
             │
             ▼
    3. Stage Agent       ← analisis stage via LLM (sales funnel)
             │
             ▼
    4. Personality Agent ← susun persona + prompt (pakai stage terbaru)
             │
             ▼
    5. Response Agent    ← generate reply via LLM
             │
             ▼
    6. Critic Agent      ← edit, format, filter
             │
             ▼
    7. Confidence Agent  ← scoring & keputusan kirim
             │
             ▼
       Kirim ke Telegram
    """

    def __init__(self, prompts_dir: str = "prompts"):
        self.prompts_dir = prompts_dir
        self.context_agent = ContextAgent()
        self.memory_agent = MemoryAgent()
        self.stage_agent = StageAgent()
        self.personality_agent = PersonalityAgent()
        self.response_agent = ResponseAgent()
        self.critic_agent = CriticAgent()
        self.confidence_agent = ConfidenceAgent()

    async def execute(
        self,
        account: Dict[str, Any],
        user_db_id: int,
        user_name: str,
        message_text: str,
        chat_type: str = "private",
        max_history: int = 20
    ) -> PipelineOutput:
        acc_name = account.get("name", "Bot")
        logger.info(f"=== [PIPELINE START] Acc: {acc_name} | User: {user_name} (id={user_db_id}) | Msg: '{message_text}' ===")

        # Step 1: Context Agent
        context = self.context_agent.process(
            account=account,
            user_db_id=user_db_id,
            user_name=user_name,
            message_text=message_text,
            chat_type=chat_type,
            max_history=max_history
        )
        logger.info(
            f"[1. Context Agent] Sender: {context.sender} | Chat: {context.chat_type} | "
            f"Time: {context.time} | Date: {context.date} | History: {len(context.last_messages)} msgs"
        )

        # Step 2: Memory Agent
        memory = self.memory_agent.process(context)
        logger.info(
            f"[2. Memory Agent] Nickname: {memory.nickname} | Stage: {memory.user_stage} | "
            f"Facts Matched: {len(memory.facts)} | Habit: {memory.chat_habit}"
        )

        # Step 3: Stage Agent (LLM-powered stage detection)
        stage_result = await self.stage_agent.process(context, memory)
        logger.info(
            f"[3. Stage Agent] {stage_result.previous_stage} -> {stage_result.new_stage} | "
            f"Updated: {stage_result.should_update} | Reason: {stage_result.reasoning}"
        )

        # Update memory.user_stage agar PersonalityAgent pakai stage terbaru
        if stage_result.should_update:
            memory.user_stage = stage_result.new_stage

        # Step 4: Personality Agent
        personality = self.personality_agent.process(context, memory, prompts_dir=self.prompts_dir)
        logger.info(
            f"[4. Personality Agent] Acc: {personality.account_name} | System Prompt Len: {len(personality.system_prompt)} chars | "
            f"Corrections Count: {len(personality.corrections)}"
        )

        # Step 5: Response Agent
        draft = await self.response_agent.process(context, memory, personality, max_history=max_history)
        logger.info(f"[5. Response Agent] Raw LLM Draft: '{draft.raw_text}'")

        # Step 6: Critic Agent
        critic = self.critic_agent.process(draft, context)
        logger.info(
            f"[6. Critic Agent] Final Text: '{critic.criticized_text}' | "
            f"Bubbles: {len(critic.bubbles)} | Edits Applied: {critic.edits_applied}"
        )

        # Step 7: Confidence Agent
        confidence = self.confidence_agent.process(context, memory, personality, draft, critic)
        logger.info(
            f"[7. Confidence Agent] Score: {confidence.score:.1f}% | "
            f"Status: {confidence.status} | Reason: {confidence.reason}"
        )

        # Memory Update post conversation
        if critic.criticized_text:
            self.memory_agent.update_memory_after_conversation(context, critic.criticized_text)

        output = PipelineOutput(
            context=context,
            memory=memory,
            stage=stage_result,
            personality=personality,
            draft=draft,
            critic=critic,
            confidence=confidence,
            final_text=critic.criticized_text,
            bubbles=critic.bubbles
        )

        logger.info(f"=== [PIPELINE END] Result: {confidence.status} ({confidence.score:.1f}%) | Text: '{critic.criticized_text}' ===")
        return output
