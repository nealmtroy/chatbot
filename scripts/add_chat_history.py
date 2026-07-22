import os
import sys
import json
import asyncio
import re

# Ensure root directory is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from core.env_loader import load_env
load_env()

from core import clients

CLASSIFY_SYSTEM_PROMPT = """\
Kamu adalah asisten pengolah data chat history untuk model chatbot Telegram cewek muda gaul, santai, dan flirty.
Diberikan input chat dari User dan balasan dari Assistant (bisa terdiri dari beberapa baris bubble chat).
Tugas kamu adalah mengekstrak informasi berikut dalam format JSON:
1. "keywords": list kata kunci pencarian yang sangat relevan dengan pesan User (buat minimal 4-6 variasi kata kunci gaul/kasual termasuk kata aslinya, singkatan gaul, typos, kata dasar). Semua lowercase.
2. "category": nama kategori singkat (1-2 kata, lowercase, snake_case atau hyphenated, misalnya: "preview", "pricing", "vcs_options").
3. "facts": list fakta singkat yang terkait dengan tanya-jawab tersebut untuk ditambahkan ke ingatan bot (misalnya: "harga vip 50k", "akses grup vip permanen").

Contoh Input:
User: "liatt dong hehehehe"
Assistant:
- "ada di group vip ak kakk, km mau joinnn kah?"
- "murah ko cuma 50rb aja"

Contoh Output JSON:
{
  "keywords": ["liat", "lihat", "liat dong", "penasaran", "preview", "liat vip"],
  "category": "vip_preview",
  "facts": ["akses vip ada foto dan video pribadi seharga 50k"]
}

Jawab HANYA dengan JSON valid (tanpa penjelasan tambahan, tanpa markdown block):
"""

def clean_json_response(text: str) -> str:
    text = text.strip()
    match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text

async def get_llm_metadata(user_msg, replies):
    if clients.client is None:
        clients.init()
    
    assistant_text = "\n".join([f'- "{r}"' for r in replies])
    user_prompt = f'User: "{user_msg}"\nAssistant:\n{assistant_text}'
    
    messages = [
        {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    
    try:
        response = await clients.client.chat.completions.create(
            model=clients.active_model,
            messages=messages,
            temperature=0.2,
            max_tokens=300
        )
        raw_output = response.choices[0].message.content.strip()
        cleaned = clean_json_response(raw_output)
        return json.loads(cleaned)
    except Exception as e:
        print(f"[!] Gagal menghubungi LLM atau parsing JSON: {e}")
        return None

async def main():
    print("=" * 60)
    print("      INTERAKTIF TAMBAH DATA CHAT_HISTORY.JSON")
    print("=" * 60)
    
    # 1. Init AI client
    clients.init()
    if clients.client is None:
        print("[!] ERROR: Gagal menginisialisasi AI client. Periksa .env")
        return
        
    history_file = os.path.join(PROJECT_DIR, "chat_history.json")
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            try:
                history_data = json.load(f)
            except Exception:
                history_data = []
    else:
        history_data = []

    while True:
        print("\n--- Entri Baru ---")
        user_msg = input("\033[94mPesan User:\033[0m ").strip()
        if not user_msg:
            print("Pesan user tidak boleh kosong.")
            continue
            
        print("Masukkan Balasan Assistant (Tekan Enter pada baris kosong jika selesai):")
        replies = []
        bubble_num = 1
        while True:
            rep = input(f"Bubble {bubble_num}: ").strip()
            if not rep:
                break
            replies.append(rep)
            bubble_num += 1
            
        if not replies:
            print("Balasan assistant minimal harus 1 bubble.")
            continue
            
        print("\n[*] Menghubungi AI untuk memproses kategori, kata kunci, dan fakta...")
        metadata = await get_llm_metadata(user_msg, replies)
        if not metadata:
            print("[!] Gagal generate metadata otomatis.")
            metadata = {"keywords": [], "category": "misc", "facts": []}
            
        # Tampilkan hasil LLM
        print("\nHasil Generate AI:")
        print(f"Keywords: {metadata.get('keywords')}")
        print(f"Category: {metadata.get('category')}")
        print(f"Facts   : {metadata.get('facts')}")
        
        while True:
            choice = input("\n[S] Simpan | [E] Edit | [B] Batal: ").strip().upper()
            if choice == "S":
                new_entry = {
                    "keywords": metadata.get("keywords", []),
                    "user": user_msg,
                    "replies": replies,
                    "category": metadata.get("category", "misc"),
                    "facts": metadata.get("facts", [])
                }
                history_data.append(new_entry)
                # Tulis kembali ke file
                with open(history_file, "w", encoding="utf-8") as f:
                    json.dump(history_data, f, ensure_ascii=False, indent=2)
                print("✅ Berhasil disimpan ke chat_history.json!")
                break
            elif choice == "E":
                print("\n--- Mode Edit ---")
                kw_input = input(f"Keywords (csv, default {','.join(metadata.get('keywords', []))}): ").strip()
                if kw_input:
                    metadata["keywords"] = [k.strip() for k in kw_input.split(",") if k.strip()]
                
                cat_input = input(f"Category (default '{metadata.get('category')}'): ").strip()
                if cat_input:
                    metadata["category"] = cat_input
                    
                fact_input = input(f"Facts (csv, default {','.join(metadata.get('facts', []))}): ").strip()
                if fact_input:
                    metadata["facts"] = [f.strip() for f in fact_input.split(",") if f.strip()]
                
                print("\nHasil Setelah Diedit:")
                print(f"Keywords: {metadata.get('keywords')}")
                print(f"Category: {metadata.get('category')}")
                print(f"Facts   : {metadata.get('facts')}")
            elif choice == "B":
                print("❌ Batal menyimpan entri ini.")
                break
                
        cont = input("\nTambah entri lagi? (y/n, default y): ").strip().lower()
        if cont == "n":
            break
            
    print("\n[*] Selesai. Terima kasih!")

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Program dihentikan.")
