# 🌅 MORNING CHECKLIST — IP Marketing Bot

**Tujuan:** Bot fully alive dengan Sheets backend ON. Estimasi waktu: **5 menit**.

---

## 1️⃣ Buka Render Dashboard

https://dashboard.render.com → service `ip-marketing-bot`

Sidebar kiri → klik **"Environment"**.

---

## 2️⃣ Cek + Fix Environment Variables

Scroll list env vars. Pastikan **2 env var ini** ada dan benar:

### `GSHEET_ID`
- Kalau **gak ada / kosong** → klik "Add Environment Variable":
  - Key: `GSHEET_ID`
  - Value: `11o8DCv30AkKKsJqC1rCqm2ZHrKo5n_xSfRaY0MgApX0`
  - Save

### `ENVIRONMENT`
- Sekarang nilainya kemungkinan `development`. Edit jadi `production`.

(Tinggal klik edit di sebelah key → ganti value → Save.)

---

## 3️⃣ Cek + Upload Secret File

Di halaman Environment yang sama, scroll bawah cari section **"Secret Files"**.

Pastikan ada **1 file dengan nama `service_account.json`**.

Kalau gak ada:
1. Klik **"Add Secret File"**
2. **Filename:** `service_account.json` (persis, tanpa folder/path)
3. **Contents:** buka file lokal `/Users/alvinleonardo/Projects/ip-marketing-bot/secrets/service_account.json` di TextEdit, Cmd+A → Cmd+C → paste ke kolom Contents
4. Save

---

## 4️⃣ Render auto-redeploy

Setelah save perubahan apa pun di Environment tab, Render otomatis trigger redeploy.

Tunggu **~3 menit**, status di pojok kanan atas berubah dari `Deploying` → **`Live`** (hijau).

---

## 5️⃣ Verify dari Telegram

Di grup admin (IP BOT TESTING), kirim:

```
/diag
```

Bot kasih response berupa diagnostic. Yang harus muncul:

```
🔍 BOT DIAGNOSTIC

Sheets backend:
  ✓ ONLINE (sheet=IP Marketing Bot DataBase)
  KB rows: 200+ (bisa lebih kalau workflow semalam tambahin)

Env vars:
  TELEGRAM_BOT_TOKEN : ✓
  ANTHROPIC_API_KEY : ✓
  ANTHROPIC_MODEL : claude-sonnet-4-6
  ANTHROPIC_MODEL_FAST : claude-haiku-4-5
  ENVIRONMENT : production    ← bukan "development"
  MARKETING_CHAT_IDS : 1
  ADMIN_CHAT_IDS : 1
  ADMIN_USER_IDS : 3
  BOT_USERNAME : Ip_marketing_bot
  GSHEET_ID : ✓ set
  credentials file : ✓ exists
  KB_REFRESH_SECONDS : 600
...
```

✅ Kalau semua ✓ → bot fully alive. **Done.**

❌ Kalau ada ✗ → cek mana yang masih kurang, ulang step 2 atau 3.

---

## 6️⃣ Test flow lengkap (1 menit)

Di grup IP Marketing:
```
/quote pensil 2 CBM
```

Cek:
- Bot balas dengan format 4-baris (Klasifikasi/Harga/Container/HS Code)
- Mirror muncul di grup admin dengan tombol 👍👎📝
- Coba klik 👎 → bot minta /correct
- (Optional) Reply mirror + `/correct sebenernya ...` → AI proposal muncul → klik ✅ → Sheet ke-update + koreksi dikirim ke IP Marketing

Kalau semua jalan → 100% READY.

---

## 7️⃣ Stop bot lokal di laptop Bapak

Kalau masih ada Terminal di laptop Bapak yang jalanin `python main.py`, tekan **Ctrl+C** untuk stop. Render udah handle 24/7.

(Kalo gak di-stop, akan konflik polling dengan Render bot dan dua-duanya crash.)

---

## 📋 Yang udah dikerjain semalam (tanpa Bapak)

Liat `PROJECT_STATUS.md` untuk detail lengkap. Singkatnya:

- Workflow multi-agent generate **30+ KB item baru** (verified 3-lens: tier+HS+container)
- **Master prompt polish** — beberapa edit surgical
- **New bot commands** — `/diag` (di atas tadi), `/find`, `/health`, `/tier`, dll (jumlah final tergantung workflow output)
- **Startup diagnostics** lebih jelas di Render logs
- Semua ke-commit + push otomatis, Render udah re-deploy

---

## 🆘 Kalau bot crash / log error

1. Cek Render Logs tab — kirim screenshot ke saya
2. Kalau parah, bisa rollback ke commit sebelumnya:
   - Render dashboard → service → **"Manual Deploy"** → pilih commit lama
3. Atau jalanin local bot sementara:
   ```bash
   cd /Users/alvinleonardo/Projects/ip-marketing-bot
   caffeinate -i .venv/bin/python main.py
   ```

---

**Estimasi:** 5 menit kerja, dari Bapak bangun sampai bot fully alive.
