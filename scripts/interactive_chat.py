import asyncio
import os
import sys

# Ensure root directory and scripts directory are in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from core.env_loader import load_env
load_env()

from core import clients, db, ai_engine

async def main():
    # 1. Inisialisasi client AI
    clients.init()
    if clients.client is None:
        print("[!] ERROR: Gagal menginisialisasi AI client. Pastikan API key terisi di .env!")
        return

    # 2. Inisialisasi database
    db.init_db()
    db.migrate_from_json_legacy()

    # 3. Ambil daftar akun
    accounts = db.list_accounts(active_only=False)
    if not accounts:
        # Fallback account jika database kosong
        account = {
            "id": 1,
            "name": "Alya",
            "persona_file": "prompts/persona.txt",
            "knowledge_file": "knowledge.json",
            "vip_chat_id": "0",
            "vip_price": 50000
        }
        print("[*] Database akun kosong. Menggunakan akun fallback default: Alya")
    else:
        print("\n=== PILIH AKUN PERSONA UNTUK DITEST ===")
        for i, acc in enumerate(accounts):
            print(f"{i + 1}. {acc.get('name')} (ID: {acc.get('id')})")
        
        choice = input("Pilih nomor (default 1): ").strip()
        idx = int(choice) - 1 if choice.isdigit() and 0 <= int(choice) - 1 < len(accounts) else 0
        account = accounts[idx]
        print(f"[*] Mengaktifkan chat persona: {account.get('name')}")

    # Buat/ambil user mock di DB agar sinkron dengan CRM stage dan chat history
    mock_tg_user_id = 999999
    user = db.get_or_create_user(
        account_id=account["id"],
        tg_user_id=mock_tg_user_id,
        first_name="Penguji",
        username="penguji"
    )
    user_db_id = user["id"]

    # Reset riwayat chat & stage mock user untuk pengetesan yang fresh
    try:
        conn = db.get_conn()
        conn.execute("DELETE FROM messages WHERE user_id=?", (user_db_id,))
        conn.execute("UPDATE users SET stage='new', interested=0, total_spent=0 WHERE id=?", (user_db_id,))
        conn.commit()
        print("[*] Berhasil mereset riwayat obrolan dan stage ke 'new' untuk pengetesan.")
    except Exception as e:
        print(f"[!] Gagal mereset data di database: {e}")

    print("\n" + "=" * 60)
    print(f" Chat interaktif dengan AI {account.get('name')} telah aktif!")
    print(f" Gaya Bicara: {account.get('persona_file')}")
    print(" Ketik '.exit' atau gunakan Ctrl+C untuk keluar.")
    print("=" * 60 + "\n")

    while True:
        try:
            user_input = input("\033[94mAnda:\033[0m ").strip()
            if not user_input:
                continue
            if user_input.lower() == ".exit":
                break

            print("[*] AI sedang berpikir...")
            
            # Panggil pipeline digital clone
            output = await ai_engine.generate_ai_reply(
                account=account,
                user_db_id=user_db_id,
                user_name="Penguji",
                message_text=user_input,
                return_full_output=True
            )

            if not output:
                print("[!] AI tidak memberikan jawaban.")
                continue

            reply_text = output.final_text
            bubbles = output.bubbles

            # Clean action tags for display and detect simulated action
            import re
            full_raw = reply_text + " " + " ".join(b["text"] for b in bubbles)
            simulated_action = None
            if "[ACTION:" in full_raw.upper():
                simulated_action = "[SIMULASI ACTION] " + re.search(r'\[ACTION:\s*([A-Z0-9_]+)\]', full_raw, re.I).group(1)
                reply_text = re.sub(r'\[ACTION:\s*[A-Z0-9_]+\]', '', reply_text, flags=re.IGNORECASE).strip()
                cleaned_bubbles = []
                for b in bubbles:
                    clean_b_text = re.sub(r'\[ACTION:\s*[A-Z0-9_]+\]', '', b["text"], flags=re.IGNORECASE).strip()
                    if clean_b_text:
                        b["text"] = clean_b_text
                        cleaned_bubbles.append(b)
                bubbles = cleaned_bubbles

            # Tampilkan Thinking Process / Alur Multi-Agent
            print("\n" + "-" * 50)
            print("\033[93m[AI THINKING FLOW / PIPELINE LOGIC]\033[0m")
            print(f"1. Context Agent     : Pengirim='{output.context.sender}', Chat='{output.context.chat_type}', Waktu='{output.context.time}'")
            print(f"2. Memory Agent      : Hubungan='{output.memory.relationship}', Habit='{output.memory.chat_habit}'")
            if output.memory.facts:
                print(f"   -> Fakta Terpilih : {output.memory.facts}")
            if output.memory.chat_examples:
                print("   -> Contoh Chat Terpilih:")
                for ex in output.memory.chat_examples:
                    print(f"      * User: '{ex.get('user')}' -> Replies: {ex.get('replies')}")
            print(f"3. Stage Agent       : {output.stage.previous_stage} -> {output.stage.new_stage} (Sebab: {output.stage.reasoning})")
            print(f"4. Personality Agent : Persona='{output.personality.account_name}', Koreksi Terpakai={len(output.personality.corrections)}")
            print(f"5. Response Agent    : Raw Draft='{output.draft.raw_text}'")
            print(f"6. Critic Agent      : Edits={output.critic.edits_applied}")
            print(f"7. Confidence Agent  : Skor={output.confidence.score:.1f}%, Status='{output.confidence.status}' (Sebab: {output.confidence.reason})")
            if simulated_action:
                print(f"\033[92m8. System Action     : {simulated_action}\033[0m")
            print("-" * 50 + "\n")

            # Simpan history ke DB agar context_agent bisa membaca chat sebelumnya
            db.add_message(account["id"], user_db_id, "user", user_input)
            if reply_text:
                db.add_message(account["id"], user_db_id, "assistant", reply_text)

            # Dapatkan stage terbaru
            updated_user = db.get_or_create_user(account["id"], mock_tg_user_id)
            stage = updated_user.get("stage", "new")

            print(f"\n\033[95m{account.get('name')} [Stage: {stage}]:\033[0m")
            for bubble in bubbles:
                print(f" 💬 {bubble['text']}")
            print()

        except KeyboardInterrupt:
            break
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"\n[!] Terjadi kesalahan: {e}\n")

    print("\n[*] Selesai. Sesi chat interaktif ditutup.")

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Selesai. Sesi chat interaktif ditutup.")
