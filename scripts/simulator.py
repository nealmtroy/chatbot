import os
import sys

# Pastikan folder root dan folder scripts ada di sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)
import asyncio
import random
import logging
from core.env_loader import load_env

# Reconfigure stdout to use UTF-8 to prevent UnicodeEncodeError in Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Import Modul AI Engine dari Proyek
from core import db, ai_engine, clients

import tester
import reviewer

# Load environment variables
load_env()

SELECTED_PROVIDER = clients.SELECTED_PROVIDER

def get_active_model():
    if clients.client is None:
        clients.init()
    return clients.active_model

logger = logging.getLogger("AI-Simulator")


async def run_simulation(scenario_id="1", turns=8):
    active_model = get_active_model()
    scenario = tester.SCENARIOS.get(scenario_id)
    if not scenario:
        print(f"[!] Skenario ID {scenario_id} tidak ditemukan.")
        return

    scenario_name = scenario["name"]
    scenario_desc = scenario["description"]
    mock_user_name = scenario["tester_name"]
    initial_message = scenario["initial_message"]
    tester_prompt = scenario["system_prompt"]

    print("=" * 60)
    print(f"     SIMULASI PERCAKAPAN: {scenario_name.upper()}")
    print(f"     Deskripsi: {scenario_desc}")
    print(f"     Provider: {SELECTED_PROVIDER} | Model: {active_model}")
    print("=" * 60)
    print("[*] Menginisialisasi sistem percakapan...")

    # Load account pertama dari DB
    db.init_db()
    accounts = db.list_accounts(active_only=True)
    if not accounts:
        acc_id = db.add_account(
            name="Alya",
            session_file="alya.session",
            api_id=12345,
            api_hash="mock_hash",
            persona_file="prompts/persona.txt",
            knowledge_file="knowledge.json"
        )
        account = db.get_account(acc_id)
    else:
        account = accounts[0]

    # Gunakan ID mock acak (bukan ID Telegram asli pemilik) agar tidak bentrok
    # dengan sesi nyata / data riil.
    mock_user_tg_id = random.randint(1000000000, 9999999999)
    user = db.get_or_create_user(
        account_id=account["id"],
        tg_user_id=mock_user_tg_id,
        first_name=mock_user_name,
        username=mock_user_name.lower()
    )
    user_db_id = user["id"]

    # Cache history untuk Tester
    tester_history = [{"role": "system", "content": tester_prompt}]

    # Pesan pembuka dari Tester
    current_message = initial_message

    # Kumpulkan transkrip untuk di-review
    chat_transcript = ""

    print(f"\n[Mulai Chatting...]\n")

    for turn in range(1, turns + 1):
        # --- TURN TESTER ---
        print(f"\033[94m{mock_user_name}:\033[0m {current_message}")
        chat_transcript += f"{mock_user_name}: {current_message}\n"

        # Simpan ke DB
        db.add_message(account["id"], user_db_id, "user", current_message)

        # --- TURN AI BOT ---
        acc_name = account.get("name", "AI")
        # Jeda simulasi mengetik (dikurangi sedikit agar tidak terlalu lama)
        await asyncio.sleep(2.0)

        # generate_ai_reply mengembalikan (reply_text_utuh, bubbles_list)
        reply_text, bubbles = await ai_engine.generate_ai_reply(
            account=account,
            user_db_id=user_db_id,
            user_name=mock_user_name,
            message_text=current_message,
            max_history=20
        )

        if not reply_text:
            print(f"\033[91m{acc_name}:\033[0m (Tidak merespon/Error)")
            chat_transcript += f"{acc_name}: (Tidak merespon/Error)\n"
            break

        # Simpan balasan AI ke DB
        db.add_message(account["id"], user_db_id, "assistant", reply_text)

        # Cetak balasan AI (simulasi multi-bubble)
        print(f"\033[95m{acc_name}:\033[0m", end="")
        chat_transcript += f"{acc_name}: {reply_text}\n"
        for i, bubble in enumerate(bubbles):
            if i > 0:
                print("      ", end="")
            print(f" {bubble['text']}")

        # Simpan obrolan ini ke history Tester (urutan yang BENAR):
        # pesan tester = "user", balasan Alya = "assistant"
        tester_history.append({"role": "user", "content": current_message})
        tester_history.append({"role": "assistant", "content": reply_text})

        # Tester memikirkan balasan berikutnya berdasarkan respons Alya
        await asyncio.sleep(2.0)
        current_message = await tester.generate_tester_reply(active_model, tester_history)

    print("\n" + "=" * 60)
    print(f"               SIMULASI SELESAI ({scenario_name})")
    print("=" * 60)

    # Jalankan Reviewer AI untuk mengevaluasi transkrip percakapan
    await reviewer.run_conversation_reviewer(active_model, chat_transcript, scenario_name)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Simulasi Percakapan Alya & Tester")
    parser.add_argument("--scenario", type=str, choices=list(tester.SCENARIOS.keys()), help="ID skenario yang ingin dijalankan")
    parser.add_argument("--turns", type=int, default=6, help="Jumlah putaran (turns) percakapan")
    args = parser.parse_args()

    async def main():
        if args.scenario:
            await run_simulation(scenario_id=args.scenario, turns=args.turns)
        else:
            print("[*] Menjalankan semua skenario pengujian secara otomatis...")
            for sid in sorted(tester.SCENARIOS.keys(), key=int):
                await run_simulation(scenario_id=sid, turns=args.turns)
                print("\n" + "=" * 60 + "\n")

    # Jalankan simulasi async
    asyncio.run(main())
