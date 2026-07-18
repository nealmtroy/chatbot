import asyncio
import os
import sys

# Ensure root directory and scripts directory are in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from env_loader import load_env
load_env()

import clients
import db
import ai_engine

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
            reply_text, bubbles = await ai_engine.generate_ai_reply(
                account=account,
                user_db_id=user_db_id,
                user_name="Penguji",
                message_text=user_input
            )

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
            print(f"\n[!] Terjadi kesalahan: {e}\n")

    print("\n[*] Selesai. Sesi chat interaktif ditutup.")

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Selesai. Sesi chat interaktif ditutup.")
