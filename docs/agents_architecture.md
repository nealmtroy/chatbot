# AI Telegram Digital Clone Architecture

## Overview

Tujuan sistem ini adalah membuat AI yang dapat membalas pesan Telegram dengan gaya bicara pengguna seakurat mungkin.

Fokus utama bukan hanya menghasilkan jawaban yang benar, tetapi menghasilkan jawaban yang terasa benar-benar berasal dari pengguna.

---

# High Level Flow

```text
                Pesan Masuk
                     │
                     ▼
            Context Agent
                     │
                     ▼
            Memory Agent
                     │
                     ▼
         Personality Agent
                     │
                     ▼
           Response Agent
                     │
                     ▼
            Critic Agent
                     │
                     ▼
         Confidence Agent
                     │
                     ▼
          Kirim ke Telegram
```

---

# 1. Context Agent

## Tujuan

Mengumpulkan seluruh konteks yang dibutuhkan sebelum AI berpikir.

## Input

- Pesan yang baru diterima
- Informasi pengirim
- Waktu
- Tanggal
- Jenis chat
- Riwayat percakapan terbaru

## Output

Contoh:

```json
{
  "sender": "Andi",
  "chat_type": "private",
  "time": "20:31",
  "last_messages": [
    "...",
    "...",
    "..."
  ]
}
```

Agent ini tidak membuat keputusan.

Hanya mengumpulkan context.

---

# 2. Memory Agent

## Tujuan

Mengingat seluruh informasi penting yang pernah diketahui mengenai lawan bicara maupun pemilik akun.

Contoh memory:

- Nama panggilan
- Hubungan
- Topik favorit
- Kebiasaan ngobrol
- Informasi yang pernah diceritakan
- Fakta-fakta penting

Contoh:

```text
Nama:
Andi

Hubungan:
Teman SMP

Topik:
- Crypto
- Telegram
- Game

Cara ngobrol:
Santai
```

Memory dapat terus diperbarui seiring percakapan berlangsung.

---

# 3. Personality Agent

## Tujuan

Menjaga agar semua jawaban tetap terdengar seperti pemilik akun.

Agent ini mengevaluasi:

- gaya bahasa
- panjang kalimat
- penggunaan emoji
- kata favorit
- kebiasaan mengetik
- sapaan
- tingkat formalitas

Contoh:

Jawaban AI

```text
Baik, saya akan datang nanti malam.
```

Setelah Personality Agent

```text
Gas bro ntar gua cabut.
```

Agent ini bertugas menjaga konsistensi karakter.

---

# 4. Response Agent

## Tujuan

Menyusun jawaban berdasarkan seluruh context dan memory.

Input:

- Context
- Memory
- Personality Rules

Output:

```text
Gas bro ntar gua cabut.
```

Response Agent adalah satu-satunya agent yang benar-benar menghasilkan isi balasan.

---

# 5. Critic Agent

## Tujuan

Menjadi editor terakhir sebelum pesan dikirim.

Yang diperiksa:

- terlalu panjang
- terlalu pendek
- terdengar seperti AI
- terlalu formal
- tidak konsisten
- mengulang kalimat
- jawaban tidak masuk akal

Jika ditemukan masalah,
Critic Agent memperbaiki jawaban sebelum diteruskan.

---

# 6. Confidence Agent

## Tujuan

Mengukur tingkat keyakinan AI terhadap jawaban.

Contoh:

```text
Confidence

97%
```

Jika confidence tinggi

```text
Kirim otomatis.
```

Jika confidence rendah

```text
Tahan jawaban.

Minta approval user.
```

Threshold dapat diatur.

Misalnya:

- ≥90% otomatis kirim
- 70-89% kirim draft
- <70% jangan kirim

---

# Message Flow

```text
Pesan Masuk

↓

Context Agent

↓

Memory Agent

↓

Personality Agent

↓

Response Agent

↓

Critic Agent

↓

Confidence Agent

↓

Telegram
```

---

# Tujuan Setiap Agent

| Agent | Fungsi |
|---------|---------|
| Context Agent | Mengumpulkan konteks percakapan |
| Memory Agent | Mengingat informasi penting |
| Personality Agent | Menjaga gaya bicara tetap konsisten |
| Response Agent | Membuat jawaban |
| Critic Agent | Mengevaluasi dan memperbaiki jawaban |
| Confidence Agent | Menentukan apakah jawaban aman dikirim |

---

# Design Principle

- Setiap agent hanya memiliki satu tanggung jawab (Single Responsibility Principle).
- Agent tidak saling mengambil alih pekerjaan agent lain.
- Seluruh proses berjalan secara berurutan (pipeline).
- Memory terus diperbarui setelah setiap percakapan.
- Personality bersifat tetap agar karakter AI selalu konsisten.
- Confidence menjadi lapisan terakhir sebelum pesan dikirim.

---

# Goal

Membangun AI Telegram yang mampu menghasilkan balasan yang terasa natural, konsisten, dan semirip mungkin dengan cara pengguna berbicara sehari-hari melalui kombinasi context, memory, personality, evaluasi kualitas, dan confidence sebelum pesan dikirim.