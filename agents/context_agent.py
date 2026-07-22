import datetime
import logging
from typing import Dict, Any, List
from core import db
from .base import ContextData

logger = logging.getLogger("ContextAgent")

class ContextAgent:
    """
    1. Context Agent
    Tujuan: Mengumpulkan seluruh konteks yang dibutuhkan sebelum AI berpikir.
    - Pesan yang baru diterima
    - Informasi pengirim
    - Waktu
    - Tanggal
    - Jenis chat (private / group)
    - Riwayat percakapan terbaru
    """

    def process(
        self,
        account: Dict[str, Any],
        user_db_id: int,
        user_name: str,
        message_text: str,
        chat_type: str = "private",
        max_history: int = 20
    ) -> ContextData:
        now = datetime.datetime.now()
        time_str = now.strftime("%H:%M")
        date_str = now.strftime("%Y-%m-%d")

        user_history: List[Dict[str, str]] = []
        user_id_tg = 0
        if user_db_id:
            try:
                user_history = db.get_history(user_db_id, max_history)
                u = db.get_user(user_db_id)
                if u:
                    user_id_tg = u.get("tg_user_id", 0)
            except Exception as e:
                logger.warning(f"Gagal mengambil user/history dari DB: {e}")

        context = ContextData(
            sender=user_name or "Teman",
            user_db_id=user_db_id,
            user_id_tg=user_id_tg,
            chat_type=chat_type,
            time=time_str,
            date=date_str,
            message_text=message_text,
            last_messages=user_history,
            account=account or {}
        )

        logger.debug(f"ContextAgent collected context for sender={user_name}, time={time_str}")
        return context
