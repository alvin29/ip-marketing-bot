"""Sync KB data updates from local CSV to Google Sheet.

Use case: pricing/tier rename updates from a new CSV version. Container column
is NEVER touched (preserves bulk_classify work). Items added via /kb_add that
don't exist in CSV are left alone.

Run:
    .venv/bin/python sync_kb_data.py            # dry-run, shows diff only
    .venv/bin/python sync_kb_data.py --apply    # actually writes to Sheet
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import config
from sheets_client import SheetsClient, KB_HEADERS

CSV_PATH = Path("knowledge_base/02_RULES_DATABASE.csv")

# Columns we WILL update.
# - "container" excluded: bulk_classify-filled value, don't clobber.
# - "hs_code_hint" excluded: bulk_classify refined these to 8-10 digit;
#   the new CSV has older less-specific values that would regress.
UPDATABLE_FIELDS = [
    "item", "tier", "price_per_cbm",
    "brand_surcharge_possible", "ovw_rate", "acceptance_status",
    "special_notes", "packing_requirement",
    "indonesia_regulation", "china_export_note",
]


def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def main() -> int:
    apply = "--apply" in sys.argv

    # Load new CSV
    with CSV_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        csv_rows = [r for r in reader if r.get("item")]
    print(f"Loaded {len(csv_rows)} rows from new CSV.")

    # Load Sheet
    sheets = SheetsClient()
    ws = sheets._tab(config.SHEET_TAB_KB)
    sheet_records = ws.get_all_records()
    print(f"Loaded {len(sheet_records)} rows from Sheet KB.")

    # Build index: lowercase item name → sheet row index (1-based, +1 for header)
    sheet_idx: dict[str, int] = {}
    for i, rec in enumerate(sheet_records, start=2):  # row 2 = first data row
        name = str(rec.get("item", "")).strip().lower()
        if name:
            sheet_idx[name] = i

    # Diff each CSV row against Sheet
    changes_to_write: list[dict] = []  # gspread batch_update payload
    summary_changes: list[str] = []
    not_in_sheet: list[str] = []

    for csv_row in csv_rows:
        item_name = str(csv_row.get("item", "")).strip()
        key = item_name.lower()
        if key not in sheet_idx:
            not_in_sheet.append(item_name)
            continue

        row_num = sheet_idx[key]
        sheet_row = sheet_records[row_num - 2]
        per_row_changes = []

        for field in UPDATABLE_FIELDS:
            new_val = str(csv_row.get(field, "")).strip()
            old_val = str(sheet_row.get(field, "")).strip()
            if new_val != old_val:
                col_num = KB_HEADERS.index(field) + 1
                col_letter = _col_letter(col_num)
                changes_to_write.append({
                    "range": f"{col_letter}{row_num}",
                    "values": [[new_val]],
                })
                per_row_changes.append(f"{field}: {old_val!r} → {new_val!r}")

        if per_row_changes:
            summary_changes.append(
                f"  Row {row_num} ({item_name}):\n    " + "\n    ".join(per_row_changes)
            )

    print(f"\n=== Diff summary ===")
    print(f"Total rows changed: {len(summary_changes)}")
    print(f"Total cell updates: {len(changes_to_write)}")
    if not_in_sheet:
        print(f"\nItems in CSV but NOT in Sheet ({len(not_in_sheet)}):")
        for it in not_in_sheet[:10]:
            print(f"  - {it}")
        if len(not_in_sheet) > 10:
            print(f"  ... and {len(not_in_sheet) - 10} more")

    show_all = "--verbose" in sys.argv or "--apply" in sys.argv
    n_show = len(summary_changes) if show_all else 5
    print(f"\n=== Changed rows (showing {n_show}) ===")
    for chg in summary_changes[:n_show]:
        print(chg)
    if not show_all and len(summary_changes) > 5:
        print(f"\n  ... and {len(summary_changes) - 5} more rows (use --verbose)")

    if not apply:
        print("\n(DRY-RUN) Add --apply to actually write to Sheet.")
        return 0

    if not changes_to_write:
        print("\nNothing to write.")
        return 0

    print(f"\nWriting {len(changes_to_write)} cell updates to Sheet...")
    ws.batch_update(changes_to_write, value_input_option="USER_ENTERED")
    print("✓ Sheet updated.")
    sheets.get_kb_rows(force_refresh=True)
    print("✓ Cache refreshed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
