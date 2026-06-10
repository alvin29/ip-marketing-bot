# IP Marketing Bot — Project Status

**Last updated:** 10 Juni 2026 (overnight session — Bapak tidur, saya kerjain)
**Owner:** Alvin Leonardo (@alvin29 GitHub, leonardoalvin29@yahoo.com)
**Repo:** https://github.com/alvin29/ip-marketing-bot (private)

---

## TL;DR

Bot Telegram untuk **Import Partner** marketing team — terima inquiry (text atau **foto produk** sekarang), klasifikasi tier + harga + container + HS code, mirror ke grup admin untuk feedback, AI-mediated KB update.

**Hosted at:** Render.com Background Worker, region Singapore, plan Starter ($7/mo).

**Status sekarang:**
- ✅ Code: deployed di Render (commit `4d4d985`)
- ✅ Telegram bot: live di grup `IP Marketing` (`-5203757660`) + grup admin `IP BOT TESTING` (`-5048019997`)
- ⚠️ **Sheets backend: OFF (CSV fallback)** — butuh Bapak fix env vars di Render pagi (lihat `MORNING_CHECKLIST.md`)
- 🔄 Overnight workflow lagi jalan untuk: 30+ KB items baru, prompt polish, new commands

---

## Apa yang Bapak harus lakuin pagi

**Liat `MORNING_CHECKLIST.md`.** Singkatnya: 5 menit kerja, 1-2 click di Render dashboard:
1. Set env var `GSHEET_ID` di Render
2. Upload `service_account.json` sebagai Secret File
3. Set `ENVIRONMENT` ke `production`
4. Render auto-redeploy → bot fully alive

---

## Phase progress

| Phase | Date | Status |
|---|---|---|
| **Phase 1** — Build core (build infrastructure, Telegram bot, 2-layer response, single group) | 6-7 Juni | ✅ Done |
| **Phase 2** — 2-group mirror, AI feedback loop, container kolom, bulk classify, master prompt rewrite to classifier role | 7-9 Juni | ✅ Done |
| **Phase 3** — Render deploy, data merge (Lartas Ringan rename, new pricing), prompt v2/v3 | 9 Juni | ✅ Done |
| **Phase 4** — Vision support, overnight improvements (KB items, prompt polish, new commands) | 10 Juni overnight | 🔄 In progress |

---

## Architecture sekarang

```
                                      ┌──────────────────────────────────┐
                                      │  Render Background Worker         │
                                      │  ip-marketing-bot (Singapore)     │
                                      │  Auto-redeploy on git push        │
                                      └────────────┬─────────────────────┘
                                                   │
                                  Telegram long-polling
                                                   │
            ┌──────────────────────────────────────┴──────────────────────────────┐
            │                                                                     │
   Grup "IP Marketing"                                          Grup "IP BOT TESTING"
   (-5203757660)                                                (-5048019997)
   Marketing inquiry                                            Admin feedback + KB loop
            │                                                                     │
   /quote <text>             ──── Claude Sonnet 4.6 ────►        📥 Mirror with 👍👎📝
   /quote <photo> + caption                                              │
            │                                                            ▼
   Bot reply (4-line format)                                    Admin /correct <teks>
                                                                        │
                                                                        ▼
                                                              ──► Claude Haiku (cheap)
                                                              propose KB update
                                                                        │
                                                                        ▼
                                                              🔄 Usulan + ✅❌ buttons
                                                                        │
                                                              Klik ✅:
                                                                        ▼
                                                                Sheet KB auto-update
                                                                        │
                                                                        ▼
                                                              Bot re-generate
                                                              kirim "🔄 KOREKSI" 
                                                              ke grup Marketing
                                                              (as reply)
```

---

## Tech stack

| Layer | Tech |
|---|---|
| Runtime | Python 3.12 |
| Telegram | python-telegram-bot 22.7 (long polling) |
| AI | Anthropic SDK 0.40 — Sonnet 4.6 for inquiry+regen, Haiku 4.5 for KB proposals (cost: ~$3-9/mo expected) |
| Vision | Claude Sonnet 4.6 multimodal (added Phase 4) |
| Knowledge store | Google Sheets (4 tabs: knowledge_base, conversations, pending_items, feedback_log) |
| Local fallback | CSV (knowledge_base/02_RULES_DATABASE.csv) — used when Sheets backend OFF |
| Logs | Render dashboard + local JSONL fallback |
| Auth | Google Service Account JSON (mounted as Render Secret File) |
| Deploy | Render Background Worker, native Python buildpack, auto-deploy on push |

---

## Stakeholders

| Role | Person | Telegram User ID |
|---|---|---|
| Owner | Alvin | `8529714860` |
| Admin | Jenny | `8629873571` |
| Admin | Hanzel (@hanzsept) | `889867061` |
| Marketing | TBD (Phase 5 rollout) | — |

| Group | Chat ID | Purpose |
|---|---|---|
| IP Marketing | `-5203757660` | Marketing inquiry |
| IP BOT TESTING | `-5048019997` | Admin feedback + AI loop |
| IP Marketing Bot Admin (created) | not yet detected in logs | Empty / not bot-added |

---

## Bot commands

| Command | Audience | Function |
|---|---|---|
| `/start`, `/help` | Whitelist | Welcome + panduan |
| `/quote <text>` | Whitelist | Klasifikasi text inquiry |
| `/quote` + photo caption | Whitelist | **NEW Phase 4** — vision classification dari foto produk |
| `/correct <text>` (reply mirror) | Admin | Trigger AI KB-update proposal |
| `/status` | Admin | Online status, model, KB row count |
| `/diag` | Admin | **NEW** — Env vars + Sheets connectivity + state |
| `/today` | Admin | Rekap hari ini dari Sheet conversations tab |
| `/reload_kb` | Admin | Paksa refresh KB cache dari Sheet |
| `/kb_add` | Admin | Manual add KB row (skip AI proposal) |
| `/pause`, `/resume` | Admin | Pause/resume inquiry handling |

(Overnight workflow mungkin tambah `/find`, `/health`, `/tier`, dll — akan di-merge setelah workflow selesai.)

---

## Bot output format (post-Phase 2)

Bot WAJIB pakai format 4-baris ini, max 1 baris note:

```
Klasifikasi : Lartas Ringan
Harga       : Rp 6.000.000/CBM
Container   : Umum
HS Code     : 9503

Note: SNI Mainan wajib (Permenperin 24/2013).
```

**Tone:** "kak" (gender-neutral), to-the-point. JANGAN tanya balik. JANGAN 2-layer format lama.

**Unknown items:**
- HS Code clear → klasifikasi normal + flag pending_items
- HS Code ambiguous → `(perlu konfirmasi tier)` + flag NEEDS_KB_ENTRY. JANGAN ngarang harga.

---

## Vision support (Phase 4 — baru)

Marketing bisa kirim **foto** ke grup IP Marketing dengan caption `/quote ...`:

```
[foto sepatu]
caption: /quote 5 CBM dari Yiwu
```

Bot identifikasi barang dari foto + klasifikasi:
```
Klasifikasi : Lartas Ringan
Harga       : Rp 6.000.000/CBM
Container   : Umum
HS Code     : 6403.99

Note: Permendag 23/2025 alas kaki + brand surcharge kalau ada brand.
```

**Trigger discipline:** photo TANPA caption `/quote` atau `@bot` → bot diem (gak waste API cost).

**Mirror ke admin:** foto asli + bot reply + feedback buttons (👍👎📝).

---

## File structure

```
ip-marketing-bot/
├── main.py                       # Entry point — polling mode, enhanced startup logs
├── bot_handler.py                # Telegram handlers, mirror, feedback flow, photo handler
├── claude_client.py              # Anthropic wrapper — generate() + generate_from_image()
├── knowledge_loader.py           # System prompt builder, Sheets>CSV fallback
├── sheets_client.py              # Google Sheets API, KB read/write, conversations log
├── sheets_setup.py               # One-shot bootstrap script for new Sheets
├── sheets_migrate_container.py   # One-shot: add container column (already applied)
├── sheets_recover.py             # Recovery util for sheet schema fixes
├── sync_kb_data.py               # Reusable: sync local CSV updates to Sheet
├── bulk_classify.py              # Bulk container + HS code refinement via AI
├── smoke_test.py                 # Connectivity check (Telegram + Anthropic + Sheets)
├── config.py                     # Env vars + helpers, supports JSON-as-env OR file path
├── requirements.txt              # No pandas (dropped); +gspread,google-auth
├── render.yaml                   # Render deploy config (plan=starter, region=sin)
├── .env.example                  # Template; .env gitignored
├── .gitignore                    # .env, secrets/, logs JSONL excluded
├── MORNING_CHECKLIST.md          # 5-min playbook for Bapak (NEW)
├── PROJECT_STATUS.md             # This file
├── README.md                     # Project intro + setup
│
├── knowledge_base/
│   ├── 11_COPY_PASTE_PROMPT.md   # Master prompt — classifier role, HS code patokan
│   └── 02_RULES_DATABASE.csv     # 180+ items seed (also synced to Sheet)
│
├── secrets/                      # GITIGNORED
│   └── service_account.json      # Google service account JSON
│
└── logs/                         # GITIGNORED
    └── conversations.jsonl       # Local fallback log (Sheet is source of truth)
```

---

## Env vars (Render dashboard)

| Key | Source | Status |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Manual fill | ✓ set |
| `ANTHROPIC_API_KEY` | Manual fill | ✓ set |
| `ANTHROPIC_MODEL` | render.yaml | `claude-sonnet-4-6` |
| `ANTHROPIC_MODEL_FAST` | render.yaml | `claude-haiku-4-5` |
| `LOG_LEVEL` | render.yaml | `INFO` |
| `ENVIRONMENT` | render.yaml | **kemungkinan `development`** ← fix pagi |
| `MARKETING_CHAT_IDS` | Manual | `-5203757660` |
| `ADMIN_CHAT_IDS` | Manual | `-5048019997` |
| `ADMIN_USER_IDS` | Manual | `8529714860,8629873571,889867061` |
| `BOT_USERNAME` | Manual | `Ip_marketing_bot` |
| `GSHEET_ID` | Manual | **kemungkinan MISSING** ← fix pagi |
| `GOOGLE_CREDENTIALS_FILE` | render.yaml | `/etc/secrets/service_account.json` |
| `KB_REFRESH_SECONDS` | render.yaml | `600` |
| `PYTHON_VERSION` | render.yaml | `3.12.7` |

**Secret Files (Render Environment tab):**
- `service_account.json` — **kemungkinan MISSING** ← fix pagi

Run `/diag` di grup admin untuk cek status real-time.

---

## Feedback loop (yang udah jalan)

```
1. Marketing /quote → bot reply di IP Marketing (no buttons)
2. Auto-mirror ke IP BOT TESTING dengan 👍👎📝 buttons
3. Admin (Alvin/Jenny/Hanzel) klik:
   - 👍: log rating, no further action
   - 👎/📝: log rating, prompt /correct
4. Admin reply mirror message + /correct <text>
   → Claude Haiku synthesize KB proposal
   → 🔄 USULAN UPDATE KB muncul dengan ✅❌
5. Klik ✅:
   → Sheet KB ditambahin/diupdate
   → Cache invalidated, reload from Sheet
   → Bot re-run inquiry pakai KB baru
   → Kirim "🔄 KOREKSI JAWABAN" ke grup IP Marketing as reply
6. Klik ❌: log rejection, no KB change
```

---

## Cost projection

| Operation | Model | Cost per call |
|---|---|---|
| /quote text | Sonnet 4.6 (cached) | $0.001-0.003 |
| /quote photo | Sonnet 4.6 (cached + vision) | $0.008-0.015 |
| /correct (KB propose) | Haiku 4.5 (cached) | $0.0003-0.0008 |
| Re-generate after approve | Sonnet 4.6 (cached) | $0.001-0.003 |
| Bot infra (Render Starter) | — | $7/month fixed |

Expected /quote volume: ~50-100/day → ~$3-9/month Anthropic + $7/month Render = ~$10-16/month total.

---

## Yang tertangguh untuk pagi (lihat MORNING_CHECKLIST.md)

1. **Set `GSHEET_ID` env var di Render** (kemungkinan kosong)
2. **Upload `service_account.json` sebagai Secret File**
3. **Set `ENVIRONMENT` ke `production`** (sekarang masih `development`)
4. **Test `/diag`** dari grup admin — semua harus ✓
5. **Test `/quote`** dan **photo + caption /quote** dari grup IP Marketing
6. **Stop bot lokal** di Terminal laptop (Ctrl+C) supaya gak konflik dengan Render bot

---

## Yang masih dikerjain workflow overnight

1. **30+ KB item baru** — verified 3-lens (tier+HS+container correctness)
2. **Master prompt polish** — surgical edits untuk sharpness
3. **3-5 new admin commands** — `/find`, `/health`, `/tier`, `/search_hs`, `/pending`, `/digest` (jumlah final tergantung output)
4. **Synthesis doc** — risk-categorized action list

Begitu workflow selesai, saya akan:
- Apply SAFE_AUTO changes ke code + Sheet
- Document REVIEW_FIRST items di file terpisah (untuk Bapak liat pagi)
- Commit + push (Render auto-redeploy)

---

## Konteks bisnis

- **IP** = Import Partner — perusahaan Alvin
- **Bisnis**: jasa konsolidasi LCL (Less than Container Load) pengiriman China → Indonesia
- **Customer**: importir kecil-menengah Indonesia
- **Marketing workflow**: customer ngobrol di WhatsApp → marketing konsultasi bot Telegram → marketing copy jawaban ke WA customer
- **Tier pricing (per CBM)**: Umum 1 (Rp 4.5jt), Umum 2 (5.5jt), Lartas Ringan (6jt), Lartas Berat (6.5jt), Semi Garment (7.5jt), Kosmetik & Makanan (8jt), Garment (9jt+volume discount), Hot Items (8-10jt), Tekstil (12.5jt), Super Sensitive (20jt), Rokok (conditional only)
- **Container**: Umum (regular consumer) atau Mix (DG/heavy/sensitive/battery/chemical)

---

## Commits

```
4d4d985 Overnight pre-workflow: vision support, /diag command, clearer startup logs
64ba62c Phase 2+3: Render deploy + classifier shift + data merge
48d0d27 Add Fly.io deploy: Dockerfile, fly.toml, env-var credentials fallback
70cf71b Initial commit: IP Marketing Bot with 2-group flow, AI feedback loop, Sheets backend
```

(More overnight commits will be added by workflow synthesizer.)

Branch: `main`
Remote: `origin` → `https://github.com/alvin29/ip-marketing-bot.git`
