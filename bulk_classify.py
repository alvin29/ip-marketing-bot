"""Bulk-fill `container` column + refine `hs_code_hint` for all KB rows.

For each row, Claude returns:
- container: Umum or Mix (mandatory)
- hs_code: most specific HS code (8-10 digit when possible)

Rows that already have BOTH a non-empty container AND a specific HS code
(≥6 chars with dot, e.g. "9506.61") are skipped. Override with --force.

Run:
    .venv/bin/python bulk_classify.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Any

from anthropic import AsyncAnthropic

import config
from knowledge_loader import get_full_system_prompt
from sheets_client import SheetsClient, KB_HEADERS

BATCH_SIZE = 20
CONCURRENCY = 2

# Slim system prompt — just enough context, no full master prompt.
SLIM_SYSTEM = """Kamu adalah klasifikator import IP (Indonesia LCL forwarder dari China).
Tugas: untuk setiap item barang, kasih container type + HS code spesifik.

CONTAINER:
- "Umum": barang biasa, bisa dicampur di container reguler (mainan, baju, kosmetik kemasan, plastik, elektronik consumer, stationery, sepatu, kursi, payung, dll)
- "Mix": barang berat/sensitif/DG yang harus dipisah:
  - Aki, baterai (lithium/lead), powerbank
  - Aerosol, chemical, asam, bubuk kimia, vape liquid
  - Pestisida, B3, freon, DEG
  - Heavy metal raw (pipa baja, plat besi, kawat raw)
  - Rokok, arak, drone, walkie-talkie
  - Oli, pelumas, grease (liquid hazmat)

TIER REFERENCE (untuk konteks):
- Umum 1: plastik, stationery, kacamata fashion, casing HP
- Umum 2: furniture light, keramik, balon
- Lartas 1: mainan SNI, sepeda, sepatu, kitchenware, kayu
- Lartas Berat: motor, oli, baut, pipa baja, laptop, powerbank
- Semi Garment: handuk, kaos kaki, underwear, bantal
- Kosmetik Makanan: skincare, supplement, makanan, minuman
- Garment: baju, celana, jaket
- Hot Items: aki, baterai, lem, freon, pestisida (selalu Mix)
- Tekstil: kain roll, gordyn roll
- Super Sensitive: aerosol, chemical, vape, drone (selalu Mix)

HS CODE:
- Output 6-10 digit dengan titik separator (Indonesia BTKI / WCO 2022 standard)
- Contoh format: "9506.61", "8517.61.00", "3304.99.00"
- Kalau bener-bener gak yakin, kasih 4-digit (e.g. "3923")
- JANGAN tulis "Various" atau "Chapter XX" — selalu kasih digit
"""


# Strong HS code = has a dot and ≥6 chars (e.g. "9506.61", "8517.61.00")
_STRONG_HS = re.compile(r"^\d{4}\.\d{2,}")


def is_strong_hs(s: str) -> bool:
    s = (s or "").strip()
    return bool(_STRONG_HS.match(s)) and "Various" not in s and "Chapter" not in s


def needs_update(row: dict[str, str], force: bool) -> bool:
    if force:
        return True
    container_ok = (row.get("container") or "").strip() in ("Umum", "Mix")
    hs_ok = is_strong_hs(row.get("hs_code_hint", ""))
    return not (container_ok and hs_ok)


PROMPT_TEMPLATE = """Untuk tiap item di bawah, output JSON dengan:
- container: "Umum" atau "Mix"
- hs_code: HS code paling spesifik (8-10 digit dengan titik kalau bisa, contoh "9506.61" atau "8517.61.00"). Pakai BTKI Indonesia atau WCO HS 2022.

Aturan container:
- Umum: barang biasa yang bisa dicampur di container reguler (mainan, stationery, baju, kosmetik kemasan kecil, dll)
- Mix: barang berat/sensitif/DG/aerosol/baterai/kimia yang harus dipisah (Hot Items, Super Sensitive, Lartas Berat untuk heavy industrial, aki, bahan kimia)

Items:
{items_block}

Output PERSIS format ini (no markdown fence, no extra text):
{{
  "results": [
    {{"row_id": <int>, "container": "Umum" | "Mix", "hs_code": "<digit.digit>"}},
    ...
  ]
}}
"""


async def classify_batch(
    client: AsyncAnthropic,
    system_prompt: str,
    batch: list[tuple[int, dict[str, str]]],
) -> dict[int, dict[str, str]]:
    items_block = "\n".join(
        f"{rid}: {row.get('item', '?')} [tier={row.get('tier', '?')}, "
        f"current_hs={row.get('hs_code_hint', '')}, "
        f"notes={(row.get('special_notes') or '')[:80]}]"
        for rid, row in batch
    )
    user_msg = PROMPT_TEMPLATE.format(items_block=items_block)

    resp = await client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=3000,
        system=[
            {"type": "text", "text": system_prompt,
             "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "\n".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"  ⚠️  Bad JSON from Claude: {exc}\n  Raw: {raw[:400]}")
        return {}

    out: dict[int, dict[str, str]] = {}
    for r in data.get("results", []):
        rid = int(r.get("row_id", -1))
        if rid < 0:
            continue
        container = str(r.get("container", "")).strip()
        hs = str(r.get("hs_code", "")).strip()
        if container in ("Umum", "Mix"):
            out[rid] = {"container": container, "hs_code": hs}
    return out


async def main() -> int:
    force = "--force" in sys.argv

    print(f"Connecting to Sheet...")
    sheets = SheetsClient()
    ws = sheets._tab(config.SHEET_TAB_KB)

    rows = sheets.get_kb_rows(force_refresh=True)
    print(f"Loaded {len(rows)} rows from KB.")

    # Filter rows needing update. Row index in Sheet = i + 2 (header=row 1, data starts row 2)
    targets = [
        (i + 2, row) for i, row in enumerate(rows) if needs_update(row, force)
    ]
    print(f"Need update: {len(targets)} rows (skip strong rows already filled)")

    if not targets:
        print("Nothing to do.")
        return 0

    # Compute column indices in Sheet (1-indexed)
    container_col = KB_HEADERS.index("container") + 1
    hs_col = KB_HEADERS.index("hs_code_hint") + 1

    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    system_prompt = SLIM_SYSTEM

    # Batch
    batches = [targets[i : i + BATCH_SIZE] for i in range(0, len(targets), BATCH_SIZE)]
    print(f"Sending {len(batches)} batches (size {BATCH_SIZE}) to Claude with "
          f"concurrency {CONCURRENCY}...")

    sem = asyncio.Semaphore(CONCURRENCY)

    async def worker(idx, batch):
        async with sem:
            # Stagger to avoid token-per-minute burst limits
            await asyncio.sleep(idx * 1.5)
            print(f"  [{idx + 1}/{len(batches)}] classifying {len(batch)} items...")
            try:
                result = await classify_batch(client, system_prompt, batch)
                print(f"  [{idx + 1}/{len(batches)}] got {len(result)} results")
                return result
            except Exception as exc:
                print(f"  [{idx + 1}/{len(batches)}] FAILED: {exc}")
                return {}

    all_results: dict[int, dict[str, str]] = {}
    batch_outs = await asyncio.gather(*[worker(i, b) for i, b in enumerate(batches)])
    for r in batch_outs:
        all_results.update(r)

    print(f"\nTotal classified: {len(all_results)} rows.")

    # Batch update sheet
    print("Writing back to Sheet...")
    updates: list[dict[str, Any]] = []
    for rid, fields in all_results.items():
        # Build A1 ranges
        # container column
        col_letter_c = _col_letter(container_col)
        col_letter_h = _col_letter(hs_col)
        updates.append({
            "range": f"{col_letter_c}{rid}",
            "values": [[fields["container"]]],
        })
        # Only overwrite hs_code if Claude provided one
        if fields["hs_code"]:
            updates.append({
                "range": f"{col_letter_h}{rid}",
                "values": [[fields["hs_code"]]],
            })

    # gspread batch_update accepts a list of {range, values}
    ws.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"✓ Wrote {len(updates)} cell updates.")

    sheets.get_kb_rows(force_refresh=True)
    print("✓ Cache refreshed.")
    return 0


def _col_letter(n: int) -> str:
    """1 → A, 2 → B, ..., 27 → AA, etc."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
