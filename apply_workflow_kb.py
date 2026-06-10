"""Apply workflow-produced KB additions to both local CSV and Google Sheet.

Usage:
    .venv/bin/python apply_workflow_kb.py path/to/kb_items.json

Input JSON format (array of objects):
    [{
      "name": "...",
      "tier": "...",
      "container": "Umum"|"Mix",
      "hs_code": "...",
      "reasoning": "...",
      "confidence": "high"|"medium"|"low"
    }, ...]

Skips:
- Items with confidence "low"
- Items whose name already exists in Sheet (case-insensitive)
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import config
from sheets_client import SheetsClient, KB_HEADERS

CSV_PATH = Path("knowledge_base/02_RULES_DATABASE.csv")

# Default values for fields not in workflow output
DEFAULTS = {
    "brand_surcharge_possible": "N",
    "ovw_rate": "5000",
    "acceptance_status": "auto",
    "packing_requirement": "Standard",
    "indonesia_regulation": "Standard",
    "china_export_note": "Standard",
}

# Tier → price_per_cbm canonical mapping
TIER_PRICES = {
    "Umum 1": "4500000",
    "Umum 2": "5500000",
    "Lartas Ringan": "6000000",
    "Lartas Berat": "6500000",
    "Semi Garment": "7500000",
    "Kosmetik Makanan": "8000000",
    "Kosmetik & Makanan": "8000000",
    "Garment": "9000000",
    "Hot Items": "10000000",
    "Tekstil": "12500000",
    "Super Sensitive": "20000000",
    "Rokok": "0",
    "REJECT": "0",
}


def build_row(item: dict) -> dict[str, str]:
    """Convert workflow item to a KB row dict matching KB_HEADERS."""
    tier = (item.get("tier") or "").strip()
    container = (item.get("container") or "").strip()
    if container not in ("Umum", "Mix"):
        container = "Umum"

    price = TIER_PRICES.get(tier, "")
    notes = (item.get("reasoning") or "").strip()
    if len(notes) > 200:
        notes = notes[:200]

    return {
        "item": (item.get("name") or "").strip(),
        "tier": tier,
        "container": container,
        "price_per_cbm": price,
        "hs_code_hint": (item.get("hs_code") or "").strip(),
        "brand_surcharge_possible": DEFAULTS["brand_surcharge_possible"],
        "ovw_rate": DEFAULTS["ovw_rate"],
        "acceptance_status": DEFAULTS["acceptance_status"],
        "special_notes": f"Added overnight workflow. {notes}",
        "packing_requirement": DEFAULTS["packing_requirement"],
        "indonesia_regulation": DEFAULTS["indonesia_regulation"],
        "china_export_note": DEFAULTS["china_export_note"],
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: apply_workflow_kb.py <kb_items.json>")
        return 1

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"File not found: {json_path}")
        return 1

    items = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        print("Expected JSON array of items.")
        return 1

    print(f"Loaded {len(items)} items from {json_path}")

    # Filter: confidence != low, tier present
    candidates = []
    for it in items:
        if (it.get("confidence") or "").lower() == "low":
            continue
        if not it.get("name") or not it.get("tier"):
            continue
        candidates.append(it)
    print(f"After filter (confidence>=medium, has name+tier): {len(candidates)}")

    # Dedup against existing Sheet rows
    sheets = SheetsClient()
    existing_records = sheets._tab(config.SHEET_TAB_KB).get_all_records()
    existing_names = {str(r.get("item", "")).strip().lower() for r in existing_records}
    print(f"Existing KB rows in Sheet: {len(existing_records)}")

    fresh = []
    skipped_dup = 0
    for c in candidates:
        if c["name"].strip().lower() in existing_names:
            skipped_dup += 1
            continue
        fresh.append(c)
    print(f"Skipped {skipped_dup} duplicates; {len(fresh)} fresh items to add")

    if not fresh:
        print("Nothing to add.")
        return 0

    # Build rows
    rows = [build_row(it) for it in fresh]

    # Append to local CSV
    with CSV_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[h for h in KB_HEADERS if h != "container"])
        # NOTE: local CSV does not have container column historically — write 11-col rows
        for row in rows:
            csv_row = {k: row.get(k, "") for k in writer.fieldnames}
            writer.writerow(csv_row)
    print(f"✓ Appended {len(rows)} rows to {CSV_PATH}")

    # Append to Sheet (12-col with container)
    ws = sheets._tab(config.SHEET_TAB_KB)
    sheet_rows = [[row.get(h, "") for h in KB_HEADERS] for row in rows]
    try:
        ws.append_rows(sheet_rows, value_input_option="USER_ENTERED")
        print(f"✓ Appended {len(rows)} rows to Sheet")
    except Exception as exc:
        print(f"⚠️ Sheet append failed: {exc}")
        return 1

    sheets.get_kb_rows(force_refresh=True)
    print(f"✓ Cache refreshed. KB now has {sheets.kb_row_count()} rows.")

    # Summary by tier
    from collections import Counter
    tier_counts = Counter(r["tier"] for r in rows)
    print("\nAdded by tier:")
    for tier, n in sorted(tier_counts.items(), key=lambda x: -x[1]):
        print(f"  {tier}: {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
