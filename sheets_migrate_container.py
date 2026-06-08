"""One-shot migration: insert `container` column into knowledge_base tab.

Idempotent — safe to re-run. Checks if column already exists before inserting.
"""
from __future__ import annotations

import sys

import config
from sheets_client import SheetsClient, KB_HEADERS


def main() -> int:
    client = SheetsClient()
    ws = client._tab(config.SHEET_TAB_KB)

    headers = ws.row_values(1)
    print(f"Current headers: {headers}")

    if "container" in headers:
        print("✓ container column already exists — nothing to do.")
        return 0

    # Container should be column C (index 3, 1-indexed) — right after tier.
    # gspread insert_cols inserts BEFORE the given column.
    print("Inserting empty column at position 3 (after 'tier')...")
    n_rows = ws.row_count
    blank = [[""] for _ in range(n_rows)]
    ws.insert_cols(blank, col=3)

    # Set the header cell
    ws.update_cell(1, 3, "container")
    print("✓ Inserted 'container' column. Existing 180 rows have empty values.")
    print("  Bapak edit langsung di Sheet, atau lewat /correct flow.")

    # Verify
    new_headers = ws.row_values(1)
    print(f"\nNew headers: {new_headers}")
    if new_headers == KB_HEADERS:
        print("✓ Schema match KB_HEADERS.")
        return 0
    else:
        print(f"⚠️  Schema mismatch — expected {KB_HEADERS}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
