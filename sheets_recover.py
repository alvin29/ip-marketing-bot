"""Recover from the bad insert_cols migration: delete extra empty columns."""
from __future__ import annotations

import sys

import config
from sheets_client import SheetsClient, KB_HEADERS


def main() -> int:
    client = SheetsClient()
    ws = client._tab(config.SHEET_TAB_KB)

    headers = ws.row_values(1)
    print(f"Found {len(headers)} non-empty header cells: {headers[:5]} ... {headers[-3:]}")

    if headers[:3] != ["item", "tier", "container"]:
        print(f"Unexpected header start: {headers[:5]}")
        return 1

    # Find where price_per_cbm landed
    try:
        ppc_idx = headers.index("price_per_cbm")
    except ValueError:
        print("price_per_cbm not found in headers!")
        return 1

    print(f"price_per_cbm is at column {ppc_idx + 1} (1-indexed)")
    print(f"Need to delete columns 4 through {ppc_idx} to bring price_per_cbm to col 4")

    if ppc_idx == 3:
        print("Already correct — nothing to delete.")
    else:
        # Delete columns 4 through ppc_idx (inclusive, 1-indexed).
        # gspread delete_columns: start_index and end_index are 1-indexed inclusive.
        n_to_delete = ppc_idx - 3
        print(f"Deleting {n_to_delete} empty columns starting at col 4...")
        ws.delete_columns(4, ppc_idx)

    # Verify
    new_headers = ws.row_values(1)
    print(f"\nFinal headers: {new_headers}")
    if new_headers == KB_HEADERS:
        print(f"✓ Schema matches expected {len(KB_HEADERS)} columns.")
        return 0
    else:
        print(f"⚠️  Still mismatch — expected {KB_HEADERS}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
