import os
import re
import sys

# Pastikan folder root dan folder scripts ada di sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)
import json
import logging
import datetime
from core.env_loader import load_env

# Load environment variables
load_env()

# Inisialisasi client AI terpusat (Groq / OpenRouter)
from core import clients, db

logger = logging.getLogger("AI-Reviewer")

def get_client():
    if clients.client is None:
        clients.init()
    return clients.client

class ReviewerClientProxy:
    def __getattr__(self, name):
        c = get_client()
        if c is None:
            raise RuntimeError(f"Client Reviewer gagal diinisialisasi: {clients.error_message}")
        return getattr(c, name)

# Re-ekspos agar kompatibel
reviewer_client = ReviewerClientProxy()


def load_file_content(filepath):
    """
    Membaca isi file teks/JSON jika ada
    """
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            print(f"[!] Warning: Gagal membaca file {filepath}: {e}")
    return "(File tidak ditemukan atau kosong)"


def _validate_entry(entry):
    """Validasi satu entri knowledge: harus dict dg 'keywords' (list) & 'fact' (str)."""
    if not isinstance(entry, dict):
        return None
    keywords = entry.get("keywords")
    fact = entry.get("fact")
    if not isinstance(keywords, list) or not keywords:
        return None
    if not isinstance(fact, str) or not fact.strip():
        return None
    return {
        "keywords": [str(k).strip() for k in keywords if str(k).strip()],
        "fact": fact.strip(),
    }


def _backup_knowledge(knowledge_path):
    """Buat backup knowledge.json sebelum dimodifikasi."""
    try:
        if os.path.exists(knowledge_path):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{knowledge_path}.bak_{ts}"
            with open(knowledge_path, "r", encoding="utf-8") as src, \
                 open(backup_path, "w", encoding="utf-8") as dst:
                dst.write(src.read())
            logger.info(f"Backup knowledge.json -> {backup_path}")
    except Exception as e:
        print(f"[!] Warning: Gagal membuat backup knowledge.json: {e}")


async def run_conversation_reviewer(model_name, transcript, scenario_name=None):
    """
    Menilai transkrip percakapan menggunakan Reviewer AI secara dinamis
    berdasarkan pedoman persona, sales, slang, dan database pengetahuan.
    """
    print("\n[*] Menjalankan Reviewer AI untuk menganalisis obrolan...")

    # Load pedoman secara dinamis
    persona_content = load_file_content("prompts/persona.txt")
    sales_content = load_file_content("prompts/sales.txt")
    slang_content = load_file_content("prompts/slang.txt")
    knowledge_content = load_file_content("knowledge.json")

    acc = db.get_conn().execute("SELECT name FROM accounts LIMIT 1").fetchone() if hasattr(db, 'get_conn') else None
    acc_name = acc["name"] if acc and "name" in acc.keys() else "AI Persona"

    dynamic_reviewer_prompt = f"""Anda adalah seorang Ahli Evaluasi Percakapan (Conversation Reviewer AI) untuk Bot Telegram Dewasa/Flirty Indonesia.
Tugas Anda adalah menganalisis transkrip chat antara User/Tester dan {acc_name} (AI Bot) secara objektif dan mendalam berdasarkan pedoman resmi berikut.

[PEDOMAN PERSONA {acc_name.upper()}]
{persona_content}

[PEDOMAN SALES {acc_name.upper()}]
{sales_content}

[PEDOMAN PENANGANAN SLANG {acc_name.upper()}]
{slang_content}

[DATABASE PENGETAHUAN RESMI {acc_name.upper()} (KNOWLEDGE.JSON)]
{knowledge_content}

Kriteria Evaluasi Anda:
1. Kepatuhan Persona & Gaya Chat:
   - Apakah {acc_name} menggunakan huruf kecil semua (lowercase) untuk seluruh chat? (Kecuali kata tawa seperti WKWKWK).
   - Apakah ada kata kaku/formal yang dilarang di [PEDOMAN PERSONA {acc_name.upper()}] yang terdeteksi? (Contoh: "Anda", "Saya", "baik", "terima kasih", "mohon maaf", "dapat", "tertarik", "pakaian dalam", "sekarang juga", "tawaran spesial", "apakah", "yakin", atau kata formal lainnya). Laporkan daftar kata kaku yang terdeteksi beserta baris chatnya.
   - Apakah {acc_name} menggunakan singkatan chat gaul Indonesia yang tepat?
   - Apakah {acc_name} menghindari tanda titik "." di akhir kalimat?
   - Apakah {acc_name} membatasi penggunaan emoji (maksimal 1-2 per percakapan, atau tanpa emoji)?
   - Apakah panggilan yang digunakan benar ("aku" untuk diri sendiri, "kak/kakak/kamu" untuk user, tidak memakai "bro/bang/mas")?

2. Kepatuhan Sales:
   - Apakah {acc_name} menawarkan VIP (50k) dan VCS (100k) secara natural pada momen yang pas?
   - Apakah {acc_name} menumpuk semua info harga sekaligus dalam satu bubble (sangat dilarang)?
   - Apakah {acc_name} merespons kata "join" dengan langsung menawarkan VIP seharga 50k tanpa nanya "join apa"?

3. Penanganan Slang & Kasus Sensitif:
   - Apakah {acc_name} membalas "open bo" dengan penolakan polos/lucu dan mengaku masih perawan (2 bubble terpisah)?
   - Apakah {acc_name} membalas diajak "vcs" atau "ngewe" secara flirty/nakal sesuai pedoman slang?

4. Konsistensi Pengetahuan & Celah Pengetahuan (Knowledge Gaps):
   - Apakah jawaban {acc_name} konsisten dengan database pengetahuan resmi (knowledge.json)?
   - JIKA user menanyakan hal-hal yang TIDAK ADA di database pengetahuan resmi (misal: tentang keluarga, hewan peliharaan, makanan kesukaan, rutinitas harian, dll.):
     * Apakah {acc_name} menjawabnya dengan natural dan tetap dalam persona?
     * Apakah responnya berhasil mengarahkan topik obrolan kembali ke hal-hal santai/genit tanpa terlihat seperti menghindar secara kaku?

============================================================
           LAPORAN EVALUASI PERCAKAPAN {acc_name.upper()}
============================================================
Skenario Pengujian: {scenario_name if scenario_name else "Tidak ditentukan"}

### 1. SKOR ALAMI OBROLAN: [Skor/10]

### 2. KEPATUHAN PERSONA & GAYA CHAT:
- [Analisis penggunaan huruf kecil, tanda baca, singkatan, dan larangan kata kaku. Tuliskan kata kaku yang terdeteksi jika ada, atau "Tidak ada"]

### 3. KEPATUHAN SALES & HARGA:
- [Ulas apakah cara {acc_name} jualan VIP/VCS sudah natural, tidak spammer, dan tidak menumpuk info harga]

### 4. PENANGANAN SLANG & KASUS SENSITIF:
- [Ulas apakah Alya menangani ajakan VCS/Ngewe/BO sesuai pedoman slang]

### 5. DETEKSI CELAH PENGETAHUAN & USULAN DATA BARU:
- [Sebutkan pertanyaan/topik dari user yang tidak ada di knowledge.json]
- [Wajib tuliskan entri JSON baru dalam blok kode ```json ... ``` yang konkret dan siap dimasukkan ke knowledge.json untuk menutup celah ini. Contoh format:
  ```json
  {{
      "keywords": ["keyword1", "keyword2"],
      "fact": "Fakta baru..."
  }}
  ```

### 6. TINDAKAN PERBAIKAN SPESIFIK (OPSI REVISI PROMPT):
- [Berikan saran perbaikan konkret untuk prompt/database kata jika diperlukan]
============================================================
"""

    try:
        messages = [
            {"role": "system", "content": dynamic_reviewer_prompt},
            {"role": "user", "content": f"Berikut adalah transkrip obrolan Alya & Tester untuk dievaluasi:\n\n{transcript}"}
        ]
        response = await reviewer_client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.3,
            max_tokens=2500
        )
        content = response.choices[0].message.content
        if content:
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            print("\n" + content + "\n")

            # --- SIMPAN LOG REVIEWER ---
            try:
                log_dir = "reviewer_logs"
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir)

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

                # Bersihkan nama skenario untuk nama file
                scenario_slug = "review"
                if scenario_name:
                    scenario_slug = re.sub(r'[^a-zA-Z0-9_\\-]', '_', scenario_name.strip().lower())
                    scenario_slug = re.sub(r'_+', '_', scenario_slug).strip('_')

                log_filename = f"{timestamp}_{scenario_slug}.md"
                log_filepath = os.path.join(log_dir, log_filename)

                with open(log_filepath, "w", encoding="utf-8") as lf:
                    lf.write(f"# Review Evaluasi Percakapan - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                    lf.write(f"**Skenario**: {scenario_name if scenario_name else 'Tidak ditentukan'}\n\n")
                    lf.write("## Transkrip Percakapan:\n")
                    lf.write("```text\n")
                    lf.write(transcript)
                    lf.write("```\n\n")
                    lf.write("## Hasil Evaluasi Reviewer:\n\n")
                    lf.write(content)
                    lf.write("\n")

                print(f"[+] Laporan review berhasil disimpan ke: {log_filepath}")
            except Exception as log_err:
                print(f"[!] Warning: Gagal menyimpan log reviewer: {log_err}")

            # Mencoba mengekstrak usulan JSON baru untuk memperbarui knowledge.json secara otomatis
            try:
                new_entries = []

                # 1. Cari blok ```json ... ``` atau ``` ... ```
                json_blocks = re.findall(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
                for block in json_blocks:
                    cleaned_block = block.strip()
                    if (cleaned_block.startswith('[') and cleaned_block.endswith(']')) or (cleaned_block.startswith('{') and cleaned_block.endswith('}')):
                        try:
                            parsed_data = json.loads(cleaned_block)
                            if isinstance(parsed_data, dict):
                                new_entries.append(parsed_data)
                            elif isinstance(parsed_data, list):
                                new_entries.extend(parsed_data)
                        except Exception:
                            pass

                # 2. Jika tidak ada di blok kode, cari raw objek JSON { ... } di seluruh teks
                if not new_entries:
                    raw_matches = re.findall(r'(\{[\s\S]*?\})', content)
                    for match in raw_matches:
                        cleaned_match = match.strip()
                        if "keywords" in cleaned_match and "fact" in cleaned_match:
                            try:
                                parsed_data = json.loads(cleaned_match)
                                if isinstance(parsed_data, dict) and "keywords" in parsed_data and "fact" in parsed_data:
                                    new_entries.append(parsed_data)
                            except Exception:
                                pass

                # 3. Validasi skema sebelum ditulis ke knowledge.json
                valid_entries = []
                for entry in new_entries:
                    valid = _validate_entry(entry)
                    if valid:
                        valid_entries.append(valid)

                if valid_entries:
                    # Muat data knowledge.json saat ini
                    knowledge_path = "knowledge.json"
                    existing_knowledge = []
                    if os.path.exists(knowledge_path):
                        try:
                            with open(knowledge_path, "r", encoding="utf-8") as kf:
                                existing_knowledge = json.load(kf)
                        except Exception as ke:
                            print(f"[!] Warning: Gagal membaca {knowledge_path} untuk auto-update: {ke}")

                    # Backup dulu sebelum dimodifikasi
                    _backup_knowledge(knowledge_path)

                    # Gabungkan entri baru (yang lolos validasi) bila belum ada
                    added_count = 0
                    for entry in valid_entries:
                        exists = False
                        for ext in existing_knowledge:
                            if ext.get("fact", "").strip().lower() == entry["fact"].strip().lower():
                                exists = True
                                break
                        if not exists:
                            existing_knowledge.append(entry)
                            added_count += 1

                    if added_count > 0:
                        with open(knowledge_path, "w", encoding="utf-8") as kf:
                            json.dump(existing_knowledge, kf, ensure_ascii=False, indent=4)
                        print(f"\n[+] AUTO-UPDATE KNOWLEDGE: Berhasil menambahkan {added_count} fakta baru ke knowledge.json!")
                    else:
                        print(f"\n[*] AUTO-UPDATE KNOWLEDGE: Tidak ada fakta baru yang valid/baru ditemukan.")
            except Exception as parse_err:
                print(f"[!] Warning: Gagal memproses auto-update knowledge.json: {parse_err}")
        else:
            print("[!] Gagal: Reviewer AI mengembalikan konten kosong.")
    except Exception as e:
        print(f"[!] Gagal menjalankan evaluasi Reviewer: {e}")
