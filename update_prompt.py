"""One-shot transformation of the master prompt:
   1. "Pak/Bu" / "pak" / "Pak" → "kak" (word boundaries)
   2. Replace CARA OUTPUT section with new 3-line template
   3. Add CONTAINER classification section
   4. Trim some verbose tone-instruction sections (lebih to-the-point)
"""
from __future__ import annotations

import re
from pathlib import Path

p = Path("knowledge_base/11_COPY_PASTE_PROMPT.md")
text = p.read_text(encoding="utf-8")
original_len = len(text)

# ─── 1. Replace honorifics ───────────────────────────────────────────
# Order matters: longer patterns first.
text = text.replace("Pak/Bu", "kak")
text = text.replace("pak/bu", "kak")
text = text.replace("Pak/bu", "kak")
text = re.sub(r"\bPak\b", "Kak", text)
text = re.sub(r"\bpak\b", "kak", text)
# Standalone "Bu" usage (rare) — only catch capitalized boundary
text = re.sub(r"\bBu\b(?!\s*=)", "Kak", text)

# ─── 2. Replace the entire CARA OUTPUT section ──────────────────────
old_output = """# CARA OUTPUT (PENTING)

Untuk setiap inquiry pricing, hasilkan DUA bagian:

**[UNTUK MARKETING]** — catatan internal:
- Klasifikasi tier + reasoning
- Risk flags
- Confidence level (HIGH/MEDIUM/LOW)
- Perlu escalate ke Alvin? (YA/TIDAK)

**[FORWARD-READY]** — pesan polished untuk customer (marketing tinggal copy-forward):
- Opening: [PILIH OPENING STYLE — contoh: "Halo kak 🙏 Penawaran berikut sudah kami cek dan sesuaikan dengan regulasi terkini ya kak. Kami pastikan transparan tanpa biaya tersembunyi:"]
- Breakdown pricing transparan
- Catatan compliance (tunjukkan expertise)
- Flag risiko (kalau ada)
- Next step jelas
- Closing: "Kalau ada concern atau pertanyaan, jangan sungkan kabari kami ya kak. Salam, Import Partner"
"""

new_output = """# OUTPUT FORMAT (WAJIB — IKUTI PERSIS)

Setiap response **wajib** pakai format ini, **3 baris pokok** dulu:

```
Klasifikasi : <tier — contoh: Umum 1 / Umum 2 / Lartas 1 / Lartas Berat / Garment / Hot Items / dll>
Harga       : Rp <harga>/CBM
Container   : <Umum atau Mix>
```

Lalu kalau perlu, tambah maks **2-3 baris pendek** untuk flag penting:
- Brand surcharge (kalau brand terdaftar)
- OVW warning (kalau barang berat)
- MSDS / cert wajib (Hot Items, Super Sensitive)
- Escalate ke owner (kalau ragu, item tidak dikenal, borderline)
- Risk flag lain

**Contoh response yang BENAR:**

```
Klasifikasi : Lartas 1
Harga       : Rp 5.500.000/CBM
Container   : Umum

Note: Mainan wajib SNI (Permenperin 24/2013). Kalau ada brand resmi, +Rp 500.000/CBM brand surcharge.
```

**Aturan tone:**
- Panggilan: pakai **"kak"** (gender-neutral). JANGAN pakai pak/bu.
- TO THE POINT. Jangan panjang-panjang.
- JANGAN bikin 2-layer [UNTUK MARKETING] + [FORWARD-READY] lagi — itu format lama, sudah deprecated.
- Bahasa konsultatif, manusiawi (bukan AI-ish formal).
"""

if old_output in text:
    text = text.replace(old_output, new_output)
    print("✓ CARA OUTPUT section replaced")
else:
    print("⚠️  CARA OUTPUT section pattern not found — may need manual fix")

# ─── 3. Add CONTAINER section after STRUKTUR HARGA ───────────────────
old_struct_anchor = """Rokok             : Rp 30.000.000  (atau Rp 100.000/slop — conditional NPPBKC)
```

# FORMULA UNIVERSAL"""

new_struct_anchor = """Rokok             : Rp 30.000.000  (atau Rp 100.000/slop — conditional NPPBKC)
```

# CONTAINER CLASSIFICATION (WAJIB)

Setiap item harus diklasifikasikan ke **salah satu** container type:

- **Umum** : barang biasa, bisa masuk container campuran dengan barang lain
- **Mix**  : barang special, **harus dipisah** dari container umum. Termasuk:
  - Dangerous Goods (DG): aki, baterai, aerosol, chemical
  - Hot Items / Super Sensitive
  - Frozen / fresh (kalau pernah ada)
  - Bahan kimia, B3, pestisida
  - Vape, arak, rokok

Cek kolom `container` di database untuk klasifikasi resmi.
Kalau item belum punya value `container` di DB, infer dari tier:
- Tier Umum 1/2, Lartas 1, Semi Garment, Garment, Tekstil → biasanya **Umum**
- Tier Lartas Berat, Hot Items, Super Sensitive, Rokok → biasanya **Mix**

# FORMULA UNIVERSAL"""

if old_struct_anchor in text:
    text = text.replace(old_struct_anchor, new_struct_anchor)
    print("✓ CONTAINER section added")
else:
    print("⚠️  STRUKTUR HARGA anchor not found")

# ─── 4. Trim the GAYA KOMUNIKASI section (it's super long now) ──────
# We replace the whole "GAYA KOMUNIKASI (CRITICAL — TULIS KAYAK MANUSIA, BUKAN AI)"
# block with a much shorter version focused on the new template.
start_marker = "# GAYA KOMUNIKASI (CRITICAL — TULIS KAYAK MANUSIA, BUKAN AI)"
end_marker = "# GAYA KOMUNIKASI — RINGKAS"

i_start = text.find(start_marker)
i_end = text.find(end_marker)
if i_start != -1 and i_end != -1 and i_end > i_start:
    replacement = (
        "# GAYA KOMUNIKASI\n\n"
        "**Rule pokok:** TO THE POINT. Response harus singkat, 3 baris template + maks "
        "2-3 line catatan kalau perlu. Hindari paragraph panjang.\n\n"
        "**Panggilan:** \"kak\" (gender-neutral). Hindari pak/bu/bro.\n\n"
        "**Yang JANGAN dilakukan:**\n"
        "- Jangan bikin 2-layer [UNTUK MARKETING] + [FORWARD-READY] format lama\n"
        "- Jangan kasih sejarah regulasi panjang (sebut singkat aja, contoh \"SNI wajib\")\n"
        "- Jangan claim trust (\"kami transparan\") — tunjukin lewat angka jelas\n"
        "- Jangan compound sentences rumit — pecah jadi kalimat pendek\n"
        "- Jangan over-share info yang customer ga tanya\n"
        "- Jangan tabel/bullet berlebihan\n\n"
        "**Yang DILAKUKAN:**\n"
        "- Format 3-baris template (Klasifikasi / Harga / Container) selalu\n"
        "- Note maks 2-3 baris kalau perlu flag\n"
        "- Bahasa manusiawi, konsultatif, gak formal kaku\n"
        "- Tunjukan expertise lewat detail singkat (\"hati-hati OVW gede\") bukan paragraph\n\n"
    )
    text = text[:i_start] + replacement + text[i_end:]
    print("✓ GAYA KOMUNIKASI section trimmed")
else:
    print(f"⚠️  GAYA KOMUNIKASI markers not found: start={i_start}, end={i_end}")

p.write_text(text, encoding="utf-8")
print(f"\nDone. {original_len:,} → {len(text):,} chars ({len(text) - original_len:+,})")
