import os
import sys

# Ensure UTF-8 stdout on Windows console for emojis
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from src.agent import DigitalTwinAgent

def input_whatsapp_export_mode(agent: DigitalTwinAgent):
    print("\n" + "=" * 60)
    print(" 📱 WHATSAPP EXPORT CHAT HISTORY BUILDER 📱 ")
    print("=" * 60)
    
    partner_name = input("Masukkan Nama Teman Chat (misal: dika / hasan): ").strip()
    if not partner_name:
        partner_name = "dika"

    print(f"\nSip! Lu sekarang bisa input/paste riwayat chat dengan '{partner_name}'.")
    print("Gunakan format:")
    print(f"  {partner_name}: <pesan teman>")
    print("  reply: <balasan lu>")
    print("  reply: <balasan lu ke-2 (jika ada)>\n")
    print("💡 Catatan: Tekan ENTER di baris kosong (2x Enter) jika selesai menuliskannya.\n")

    lines = []
    print("--- MULAI KETIK / PASTE CHAT ---")
    while True:
        try:
            line = input().strip()
            if not line:
                if len(lines) > 0:
                    break
                else:
                    continue
            lines.append(line)
        except (KeyboardInterrupt, EOFError):
            break

    if not lines:
        print("⚠️ Tidak ada input chat yang dimasukkan.\n")
        return

    raw_text = "\n".join(lines)
    agent.add_raw_chat_block(partner_name, raw_text)
    print(f"\n✅ Berhasil menyimpan riwayat chat '{partner_name}' ke Vector DB!\n")

def chat_mode(agent: DigitalTwinAgent):
    print("\n" + "=" * 60)
    print(" 💬 CHAT DENGAN AI KEMBARAN LU 💬 ")
    print("=" * 60)
    print("Ketik 'q' atau 'exit' untuk kembali ke menu utama.\n")

    conversation_history = []

    while True:
        try:
            user_input = input("[USER]: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit", "q"]:
                break

            print("-" * 50)
            ai_response = agent.generate_response(user_input, conversation_history)
            print("-" * 50)

            print("[AI KEMBARAN LU]:")
            response_lines = [l.strip() for l in ai_response.split("\n") if l.strip()]
            for line in response_lines:
                if line.lower().startswith("reply:"):
                    line = line[6:].strip()
                print(f"Reply: {line}")
            print()

            conversation_history.append({"role": "user", "content": user_input})
            conversation_history.append({"role": "assistant", "content": ai_response})
            if len(conversation_history) > 6:
                conversation_history = conversation_history[-6:]

        except KeyboardInterrupt:
            break

def main():
    data_dir = os.path.join(PROJECT_DIR, "data")

    try:
        agent = DigitalTwinAgent(data_dir)
    except Exception as e:
        print(f"❌ Error inisialisasi Agent: {e}")
        sys.exit(1)

    while True:
        print("\n" + "=" * 60)
        print("    🤖 DIGITAL TWIN AI - WHATSAPP CHAT FLOW MANAGER 🤖    ")
        print("=" * 60)
        print("1. 📱 Input / Paste WhatsApp Export Chat (Format: dika: ... / reply: ...)")
        print("2. 💬 Chat / Tes AI Kembaran Lu")
        print("3. 🗑️ Hapus & Reset Semua Data Chat")
        print("4. 🚪 Keluar")
        
        choice = input("\nPilih menu (1-4): ").strip()

        if choice == "1":
            input_whatsapp_export_mode(agent)
        elif choice == "2":
            chat_mode(agent)
        elif choice == "3":
            confirm = input("⚠️ Yakin mau hapus semua data chat history? (y/N): ").strip().lower()
            if confirm == "y":
                agent.clear_data()
                print("🗑️ Semua data chat history & Vector DB telah dihapus bersih!")
        elif choice in ["4", "q", "exit"]:
            print("Dadaah bro! 👋")
            break
        else:
            print("Pilihan tidak valid, coba lagi.")

if __name__ == "__main__":
    main()
