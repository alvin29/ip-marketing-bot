"""Google Sheets backend — knowledge base + conversation log + feedback + pending items.

Sheet tabs:
- knowledge_base    : the editable rules DB (currently 180 rows, grows over time)
- conversations     : every inquiry/response auto-logged
- pending_items     : items the bot flagged as not yet in KB (for Alvin to classify)
- feedback_log      : every feedback action from admins (ratings, corrections, notes)

The bot reads KB on a TTL cache (KB_REFRESH_SECONDS) and writes to the other tabs
fire-and-forget — failures are logged but don't break the user-facing flow.
"""
from __future__ import annotations

import csv
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Header rows for each tab — created automatically on bootstrap.
KB_HEADERS = [
    "item", "tier", "container", "price_per_cbm", "hs_code_hint",
    "brand_surcharge_possible", "ovw_rate", "acceptance_status",
    "special_notes", "packing_requirement", "indonesia_regulation",
    "china_export_note",
]

CONV_HEADERS = [
    "ts_utc", "conv_id", "chat_id", "user_id", "username", "first_name",
    "inquiry", "response", "latency_ms", "model",
    "rating", "feedback_note", "corrected_response", "kb_updated",
]

PENDING_HEADERS = [
    "ts_utc", "conv_id", "detected_item", "context", "status", "notes",
]

FEEDBACK_HEADERS = [
    "ts_utc", "conv_id", "admin_user_id", "admin_name", "kind", "value",
]


@dataclass
class Conversation:
    conv_id: str
    chat_id: int
    user_id: int
    username: str | None
    first_name: str | None
    inquiry: str
    response: str
    latency_ms: int
    model: str


class SheetsClient:
    """Thread-safe wrapper around the bot's Google Sheet.

    Read path (KB) is cached with a TTL.
    Write path (append) is serialized via a single lock.
    """

    def __init__(
        self,
        sheet_id: str = config.GSHEET_ID,
        credentials_file: Path = config.GOOGLE_CREDENTIALS_FILE,
        kb_refresh_seconds: int = config.KB_REFRESH_SECONDS,
    ) -> None:
        self.sheet_id = sheet_id
        self.credentials_file = credentials_file
        self.kb_refresh_seconds = kb_refresh_seconds

        creds = Credentials.from_service_account_file(
            str(credentials_file), scopes=SCOPES
        )
        self._gc = gspread.authorize(creds)
        self._sheet = self._gc.open_by_key(sheet_id)

        self._lock = threading.RLock()
        self._kb_cache: list[dict[str, str]] | None = None
        self._kb_cache_ts: float = 0.0

    # ─── Tab helpers ─────────────────────────────────────────────────────

    def _tab(self, name: str):
        try:
            return self._sheet.worksheet(name)
        except gspread.WorksheetNotFound:
            raise RuntimeError(
                f"Sheet tab {name!r} not found. Run sheets_setup.py first."
            )

    # ─── Bootstrap (create tabs & seed KB) ───────────────────────────────

    def bootstrap_sheet(self, csv_seed_path: Path | None = None) -> dict[str, str]:
        """Create the 4 tabs if missing. Seed KB from CSV if KB is empty.

        Idempotent — safe to call multiple times. Returns a status dict.
        """
        status: dict[str, str] = {}

        existing = {ws.title for ws in self._sheet.worksheets()}

        wanted = [
            (config.SHEET_TAB_KB, KB_HEADERS, 1500, len(KB_HEADERS)),
            (config.SHEET_TAB_CONV, CONV_HEADERS, 5000, len(CONV_HEADERS)),
            (config.SHEET_TAB_PENDING, PENDING_HEADERS, 1000, len(PENDING_HEADERS)),
            (config.SHEET_TAB_FEEDBACK, FEEDBACK_HEADERS, 2000, len(FEEDBACK_HEADERS)),
        ]

        for name, headers, rows, cols in wanted:
            if name in existing:
                status[name] = "exists"
                ws = self._tab(name)
                # Ensure header row matches (don't clobber existing data below)
                current = ws.row_values(1)
                if current != headers:
                    ws.update("A1", [headers])
                    status[name] = "headers-updated"
            else:
                ws = self._sheet.add_worksheet(title=name, rows=rows, cols=cols)
                ws.update("A1", [headers])
                # Freeze header row for usability
                self._sheet.batch_update({
                    "requests": [{
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": ws.id,
                                "gridProperties": {"frozenRowCount": 1},
                            },
                            "fields": "gridProperties.frozenRowCount",
                        }
                    }]
                })
                status[name] = "created"

        # Delete the default Sheet1 if present and empty (clean look)
        for ws in self._sheet.worksheets():
            if ws.title == "Sheet1" and ws.row_count <= 1000:
                try:
                    if not any(ws.row_values(1)):
                        self._sheet.del_worksheet(ws)
                except Exception:
                    pass

        # Seed KB from CSV if KB tab is empty (only header row exists)
        if csv_seed_path and csv_seed_path.exists():
            kb_ws = self._tab(config.SHEET_TAB_KB)
            existing_rows = kb_ws.get_all_values()
            if len(existing_rows) <= 1:  # only header (or nothing)
                with csv_seed_path.open(encoding="utf-8") as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                if rows:
                    # Replace header with canonical, then append data rows
                    csv_header, *data_rows = rows
                    if csv_header != KB_HEADERS:
                        logger.warning(
                            "CSV header doesn't match canonical KB headers — "
                            "seeding under canonical headers anyway."
                        )
                    if data_rows:
                        kb_ws.append_rows(data_rows, value_input_option="USER_ENTERED")
                        status["seed"] = f"seeded {len(data_rows)} rows from CSV"
            else:
                status["seed"] = f"skipped (KB has {len(existing_rows) - 1} rows)"

        return status

    # ─── Knowledge base read (cached) ────────────────────────────────────

    def get_kb_rows(self, force_refresh: bool = False) -> list[dict[str, str]]:
        with self._lock:
            now = time.time()
            cache_ok = (
                not force_refresh
                and self._kb_cache is not None
                and (now - self._kb_cache_ts) < self.kb_refresh_seconds
            )
            if cache_ok:
                return self._kb_cache  # type: ignore[return-value]

            ws = self._tab(config.SHEET_TAB_KB)
            rows = ws.get_all_records()  # uses row 1 as headers
            # Normalize keys/values to strings to keep the CSV-like prompt format
            normalized: list[dict[str, str]] = []
            for r in rows:
                normalized.append({str(k): str(v) for k, v in r.items()})
            self._kb_cache = normalized
            self._kb_cache_ts = now
            logger.info("Reloaded KB from Sheets — %d rows", len(normalized))
            return normalized

    def kb_as_csv_text(self, force_refresh: bool = False) -> str:
        """Serialize KB as CSV text for embedding in the system prompt."""
        rows = self.get_kb_rows(force_refresh=force_refresh)
        import io
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=KB_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            # Make sure every header exists in row
            writer.writerow({k: r.get(k, "") for k in KB_HEADERS})
        return out.getvalue()

    def kb_row_count(self) -> int:
        return len(self.get_kb_rows())

    # ─── Append helpers ──────────────────────────────────────────────────

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def log_conversation(self, conv: Conversation) -> None:
        row = [
            self._now_iso(),
            conv.conv_id,
            str(conv.chat_id),
            str(conv.user_id),
            conv.username or "",
            conv.first_name or "",
            conv.inquiry,
            conv.response,
            str(conv.latency_ms),
            conv.model,
            "",  # rating (filled by feedback button)
            "",  # feedback_note
            "",  # corrected_response
            "",  # kb_updated
        ]
        with self._lock:
            try:
                self._tab(config.SHEET_TAB_CONV).append_row(
                    row, value_input_option="USER_ENTERED"
                )
            except Exception as exc:
                logger.exception("Failed to log conversation to Sheet: %s", exc)

    def log_pending_item(self, conv_id: str, item: str, context: str) -> None:
        row = [self._now_iso(), conv_id, item, context, "new", ""]
        with self._lock:
            try:
                self._tab(config.SHEET_TAB_PENDING).append_row(
                    row, value_input_option="USER_ENTERED"
                )
            except Exception as exc:
                logger.exception("Failed to log pending item: %s", exc)

    def log_feedback(
        self,
        conv_id: str,
        admin_user_id: int,
        admin_name: str,
        kind: str,
        value: str,
    ) -> None:
        """Append a feedback row AND update the conversations tab in-place."""
        row = [
            self._now_iso(),
            conv_id,
            str(admin_user_id),
            admin_name,
            kind,
            value,
        ]
        with self._lock:
            try:
                self._tab(config.SHEET_TAB_FEEDBACK).append_row(
                    row, value_input_option="USER_ENTERED"
                )
            except Exception as exc:
                logger.exception("Failed to log feedback: %s", exc)

            # Also patch the conversation row for at-a-glance review
            self._patch_conversation_feedback(conv_id, kind, value)

    def _patch_conversation_feedback(self, conv_id: str, kind: str, value: str) -> None:
        ws = self._tab(config.SHEET_TAB_CONV)
        try:
            # Column B = conv_id (index 2). Use gspread find for the cell.
            cell = ws.find(conv_id, in_column=2)
        except gspread.exceptions.CellNotFound:
            logger.warning("conv_id %s not found in conversations tab", conv_id)
            return
        except Exception as exc:
            logger.exception("Lookup failed for conv_id %s: %s", conv_id, exc)
            return

        if cell is None:
            return

        # Columns in CONV_HEADERS: rating=11, feedback_note=12, corrected_response=13
        col_by_kind = {
            "rating": 11,
            "note": 12,
            "correction": 13,
        }
        col = col_by_kind.get(kind)
        if col is None:
            return
        try:
            ws.update_cell(cell.row, col, value)
        except Exception as exc:
            logger.exception("Failed to patch conv row: %s", exc)

    # ─── KB write (admin /kb_add) ────────────────────────────────────────

    def add_kb_row(self, fields: dict[str, str]) -> int:
        """Append a row to knowledge_base. Returns the new row count."""
        row = [fields.get(h, "") for h in KB_HEADERS]
        with self._lock:
            try:
                self._tab(config.SHEET_TAB_KB).append_row(
                    row, value_input_option="USER_ENTERED"
                )
            except Exception as exc:
                logger.exception("Failed to add KB row: %s", exc)
                raise
            # Force-refresh cache so the new row is visible immediately
            self._kb_cache = None
        return self.kb_row_count()

    def update_kb_row(self, match_item: str, fields: dict[str, str]) -> bool:
        """Find a row in knowledge_base by item name and overwrite its fields.

        Returns True if found+updated, False if no row matched.
        """
        with self._lock:
            ws = self._tab(config.SHEET_TAB_KB)
            try:
                # Item is column A (1). Use case-insensitive substring match
                # because admins phrase items slightly differently over time.
                all_rows = ws.get_all_values()
            except Exception as exc:
                logger.exception("Failed to read KB for update: %s", exc)
                raise

            match_lower = match_item.strip().lower()
            target_row_idx: int | None = None
            for i, row in enumerate(all_rows[1:], start=2):  # 1-indexed, skip header
                if row and row[0].strip().lower() == match_lower:
                    target_row_idx = i
                    break

            if target_row_idx is None:
                # Fallback: substring match
                for i, row in enumerate(all_rows[1:], start=2):
                    if row and match_lower in row[0].strip().lower():
                        target_row_idx = i
                        break

            if target_row_idx is None:
                return False

            new_row = [fields.get(h, "") for h in KB_HEADERS]
            try:
                end_col = chr(ord("A") + len(KB_HEADERS) - 1)
                ws.update(
                    f"A{target_row_idx}:{end_col}{target_row_idx}",
                    [new_row],
                    value_input_option="USER_ENTERED",
                )
            except Exception as exc:
                logger.exception("Failed to update KB row: %s", exc)
                raise

            self._kb_cache = None
        return True
