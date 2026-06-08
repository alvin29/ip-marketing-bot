"""One-shot setup: create the 4 tabs in the Google Sheet and seed the KB from CSV.

Run once after creating the Sheet & service account:
    python sheets_setup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import config
from sheets_client import SheetsClient


def main() -> int:
    if not config.sheets_enabled():
        print("ERROR: Google Sheets not configured.")
        print(f"  GSHEET_ID set?            {bool(config.GSHEET_ID)}")
        print(f"  Credentials file exists?  {config.GOOGLE_CREDENTIALS_FILE.exists()}")
        print(f"    expected at: {config.GOOGLE_CREDENTIALS_FILE}")
        print("Fill in .env and place the service account JSON, then retry.")
        return 1

    print(f"Connecting to Sheet {config.GSHEET_ID}...")
    client = SheetsClient()
    print(f"  ✓ opened: {client._sheet.title}")

    seed_csv = Path(__file__).parent / "knowledge_base" / "02_RULES_DATABASE.csv"
    print(f"Bootstrapping tabs (seed CSV: {seed_csv.name})...")
    status = client.bootstrap_sheet(csv_seed_path=seed_csv)
    for tab, st in status.items():
        print(f"  • {tab}: {st}")

    rows = client.kb_row_count()
    print(f"\nKB now has {rows} rows.")
    print("Setup complete. You can now `python main.py`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
