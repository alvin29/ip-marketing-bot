# IP Marketing Bot

Telegram bot AI klasifikator untuk tim marketing **Import Partner**. Bot terima inquiry (text atau **foto produk**) dari grup IP Marketing, klasifikasi tier + harga + container + HS code, mirror ke grup admin dengan feedback buttons, dan support AI-mediated KB update (Sonnet propose, Haiku verify, admin approve, auto-sync ke Sheet).

**Status:** deployed di Render.com (Singapore, Background Worker). See `PROJECT_STATUS.md` for the current state and `MORNING_CHECKLIST.md` for operator runbook.

## Architecture

```
Marketing group         ──text or photo /quote──►   Bot (Render polling)
                                                            ↓
                                                    Claude Sonnet 4.6 (vision-capable)
                                                            ↓
                                                4-line response: Klasifikasi/Harga/Container/HS Code
                                                            ↓
                                          Marketing group  ◄────── reply
                                                  +
                                          Admin group   ◄────── mirror with 👍👎📝 buttons
                                                            ↓
                                          /correct → Haiku propose KB row → admin ✅ → Sheet auto-update
                                                                                              ↓
                                                                                       Bot reload + send
                                                                                       "🔄 KOREKSI" back
                                                                                       to Marketing as reply
```

Google Sheet has 4 tabs (knowledge_base, conversations, pending_items, feedback_log). KB cached 10 minutes; force refresh with `/reload_kb`.

## Output format (current)

```
Klasifikasi : Lartas Ringan
Harga       : Rp 6.000.000/CBM
Container   : Umum
HS Code     : 9503

Note: SNI Mainan wajib (Permenperin 24/2013).
```

Tone "kak" (gender-neutral), to-the-point, no questions back to customer.

## Phase progression

- **Phase 1** (done): build core bot, single group, 2-layer response
- **Phase 2** (done): two-group mirror, AI feedback loop, container column, classifier role shift, master prompt v2, Lartas Ringan rename, volume discount, Render deploy
- **Phase 3** (done): data merge (file 8 prices), production deploy
- **Phase 4** (in progress, overnight): vision support, KB additions, prompt polish, new admin commands

## Setup — Google Cloud (sekali aja)

Bot butuh akses ke Google Sheet via **service account** (akun bot untuk Google API). Tanpa ini, bot tetap jalan pakai local CSV fallback, tapi feedback button + KB writes dimatikan.

### A. Bikin service account di Google Cloud

1. Buka https://console.cloud.google.com/
2. Bikin project baru (atau pakai yang udah ada). Nama bebas, misal "IP Marketing Bot"
3. Aktifkan 2 API:
   - https://console.cloud.google.com/apis/library/sheets.googleapis.com → **Enable**
   - https://console.cloud.google.com/apis/library/drive.googleapis.com → **Enable**
4. Buka **IAM & Admin** → **Service Accounts** → **Create Service Account**
   - Name: `ip-marketing-bot`
   - Skip role assignment (gak perlu)
   - Click **Done**
5. Klik service account yang baru dibuat → tab **KEYS** → **Add Key** → **Create new key** → **JSON** → Create
6. File JSON ke-download. Pindahin ke folder bot:
   ```bash
   mv ~/Downloads/ip-marketing-bot-xxx.json /Users/alvinleonardo/Projects/ip-marketing-bot/secrets/service_account.json
   ```
7. Catat email service account-nya, format: `ip-marketing-bot@<project>.iam.gserviceaccount.com`

### B. Bikin Google Sheet

1. Buka https://sheets.google.com → bikin Sheet baru, nama "IP Marketing Bot DB"
2. **Share** sheet ke email service account (langkah A.7), kasih akses **Editor**
3. Copy Sheet ID dari URL — bagian yang panjang antara `/d/` dan `/edit`:
   ```
   https://docs.google.com/spreadsheets/d/1AbCd...xyz123/edit
                                          └──── ini ────┘
   ```

### C. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env`, isi:

```
TELEGRAM_BOT_TOKEN=...
ANTHROPIC_API_KEY=...
ALLOWED_CHAT_IDS=-5048019997
ADMIN_USER_IDS=8529714860,<jenny_user_id>,<hanzel_user_id>
BOT_USERNAME=Ip_marketing_bot
GSHEET_ID=1AbCd...xyz123
GOOGLE_CREDENTIALS_FILE=secrets/service_account.json
KB_REFRESH_SECONDS=600
```

User ID didapat dari DM `@userinfobot` di Telegram (Jenny + Hanzel tinggal kirim 1 pesan ke bot itu, dapet user ID-nya).

### D. Bootstrap Sheet (sekali aja)

```bash
.venv/bin/python sheets_setup.py
```

Akan:
- Bikin 4 tab (knowledge_base / conversations / pending_items / feedback_log)
- Seed knowledge_base dari `knowledge_base/02_RULES_DATABASE.csv` (180 row)
- Freeze header row di tiap tab

Setelah sukses, buka Sheet di browser → udah ada 4 tab dengan data lengkap.

### E. Jalanin bot

```bash
.venv/bin/python main.py
```

Cek log — kalau ada `Sheets backend OK — sheet=... kb_rows=180` artinya jalan.

## Commands

| Command | Siapa | Fungsi |
|---|---|---|
| `/start` | Whitelist | Welcome |
| `/help` | Whitelist | Panduan |
| `/quote <inquiry>` | Whitelist | Generate quote 2-layer |
| `@<bot> <inquiry>` | Whitelist | Sama (mention mode) |
| `/status` | Admin | Cek bot online + KB row count |
| `/pause` / `/resume` | Admin | Pause / resume bot |
| `/reload_kb` | Admin | Paksa refresh KB dari Sheet |
| `/kb_add` | Admin | Tambah item baru ke KB |
| `/feedback <teks>` | Admin | Reply ke response bot lalu tulis koreksi |

Plus **inline buttons** (👍 / 👎 / 📝) di setiap response — admin tinggal klik.

## Workflow: knowledge base growth

```
Hari 1
    KB = 180 item.

Hari 3
    Marketing: "/quote thermos stainless 2 CBM"
    Bot:       Klasifikasi best-effort + tambah flag [NEEDS_KB_ENTRY: thermos stainless]
    System:    Auto-tulis row di tab pending_items dengan context inquiry.

Hari 4
    Alvin buka tab pending_items → lihat 5 item baru.
    Untuk yang valid: ketik di Telegram
        /kb_add thermos stainless | Lartas 1 | 5500000 | 7323 | N | 5000 | auto | Light kitchenware | Standard | Permendag 22/2025 | Standard
    Bot:       Row ditambah ke knowledge_base. Cache di-flush. Bot tau dari sekarang.

Hari 30
    KB = 500 item. Pending_items dikosongin tiap minggu.
```

## Workflow: feedback dari Alvin / Jenny / Hanzel

```
1. Bot kasih jawaban.
2. Di bawah jawaban ada tombol [👍 Bagus] [👎 Salah] [📝 Catatan].
3a. Admin klik 👍   → rating disimpan di feedback_log + di kolom rating tab conversations.
3b. Admin klik 👎   → rating 👎 disimpan. Bot ngingetin untuk kasih koreksi via /feedback.
4. Untuk koreksi panjang:
   - Reply ke response bot di Telegram
   - Ketik: /feedback <teks koreksi>
   - Auto-tulis ke feedback_log + kolom corrected_response di tab conversations.
5. Tiap minggu, Alvin scroll tab conversations → filter rating=👎 → review koreksi → update KB / master prompt.
```

## File structure

```
ip-marketing-bot/
├── main.py                 # Entry — polling mode
├── bot_handler.py          # Telegram handlers, whitelist, feedback flow
├── claude_client.py        # Anthropic wrapper, latency, flag detection
├── knowledge_loader.py     # Build system prompt (Sheets > CSV fallback)
├── sheets_client.py        # Google Sheets API wrapper
├── sheets_setup.py         # One-shot bootstrap script
├── config.py               # Env config
├── smoke_test.py           # Connectivity check
├── requirements.txt
├── .env.example
├── .gitignore
├── secrets/                # Service account JSON (gitignored)
├── knowledge_base/         # Master prompt + CSV seed
│   ├── 11_COPY_PASTE_PROMPT.md
│   └── 02_RULES_DATABASE.csv
└── logs/                   # JSONL fallback log (gitignored)
```

## Troubleshooting

**Bot start tapi log bilang `Sheets backend NOT configured`:**
- Cek `.env` punya `GSHEET_ID` dan `GOOGLE_CREDENTIALS_FILE`
- Cek `secrets/service_account.json` exist

**Log bilang `PermissionError` saat baca Sheet:**
- Bapak belum share Sheet ke email service account
- Atau service account belum Editor role

**Feedback button gak respond:**
- Cek user yang klik ada di `ADMIN_USER_IDS`. Selain admin akan dapet alert "Cuma admin yang bisa kasih feedback."

**`/quote` jalan tapi nothing di Sheet:**
- Cek `Sheets backend OK` di startup log. Kalau gak ada, fallback CSV yang dipakai.

**Bot mati setelah claude code session ditutup:**
- Normal untuk Phase 1. Restart manual: `cd ip-marketing-bot && .venv/bin/python main.py`
- Untuk Phase 2: deploy ke Render.com (24/7).
