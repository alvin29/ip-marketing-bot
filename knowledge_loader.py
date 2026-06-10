"""Build the system prompt = master markdown + rules CSV.

KB source priority:
  1. Google Sheets (if configured + reachable) — cached with TTL
  2. Local CSV fallback (knowledge_base/02_RULES_DATABASE.csv)

The master prompt is always read from the local markdown file.
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache
from pathlib import Path

import config

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
KNOWLEDGE_DIR = BASE_DIR / "knowledge_base"

MASTER_PROMPT_FILE = KNOWLEDGE_DIR / "11_COPY_PASTE_PROMPT.md"
RULES_CSV_FILE = KNOWLEDGE_DIR / "02_RULES_DATABASE.csv"


# Shared singleton — set by main.py at boot, used by claude_client.py.
_sheets_singleton = None
_singleton_lock = threading.Lock()


def set_sheets_client(client) -> None:
    """Inject the SheetsClient. Call once at startup if Sheets is enabled."""
    global _sheets_singleton
    with _singleton_lock:
        _sheets_singleton = client


def get_sheets_client():
    return _sheets_singleton


@lru_cache(maxsize=1)
def _master_prompt() -> str:
    return MASTER_PROMPT_FILE.read_text(encoding="utf-8")


def _rules_csv_text() -> str:
    """Return rules CSV text — preferring Sheets if available."""
    client = get_sheets_client()
    if client is not None:
        try:
            return client.kb_as_csv_text()
        except Exception as exc:
            logger.warning(
                "Sheets read failed (%s) — falling back to local CSV.", exc
            )
    return RULES_CSV_FILE.read_text(encoding="utf-8")


def get_full_system_prompt() -> str:
    """Concatenate master prompt + CSV rules. Called per request (cheap)."""
    prompt = _master_prompt()
    rules_csv = _rules_csv_text()
    source = "Google Sheets" if get_sheets_client() else "local CSV"
    return (
        f"{prompt}\n\n"
        "## RULES DATABASE (CSV)\n"
        f"Sumber data: {source}. Kolom: item, tier, container, price_per_cbm, hs_code_hint, "
        "brand_surcharge_possible, ovw_rate, acceptance_status, special_notes, "
        "packing_requirement, indonesia_regulation, china_export_note.\n\n"
        "**PENTING — input bisa berupa TEXT atau FOTO:**\n"
        "Kalau input berupa foto produk, identifikasi barang dari visual dulu, "
        "lalu klasifikasikan dengan format 4-baris yang sama. Kalau foto blur "
        "atau ambigu (banyak barang berbeda), output '(perlu konfirmasi)' di "
        "kolom yang ambigu + flag NEEDS_KB_ENTRY.\n\n"
        "**PENTING — unknown item handling:**\n"
        "1. Cek dulu KB. Kalau gak ada exact match, **infer tier dari HS Code** "
        "(BTKI chapter mapping ada di section PATOKAN KLASIFIKASI di atas).\n"
        "2. Kalau HS Code memberi tier yang JELAS → klasifikasi normal, tetap "
        "tambahkan baris flag di akhir response (di bawah note):\n"
        "   `[NEEDS_KB_ENTRY: nama_item_pendek]`\n"
        "3. Kalau item ambiguous (HS Code gak jelas / banyak chapter possible) → "
        "output '(perlu konfirmasi tier)' untuk Klasifikasi/Harga/Container + "
        "tambah flag `[NEEDS_KB_ENTRY: ...]`. **JANGAN ngarang harga.**\n"
        "4. Flag `[NEEDS_KB_ENTRY: ...]` selalu di-emit di tail. Bot akan strip "
        "sebelum kirim ke marketing dan log ke pending_items untuk owner review.\n\n"
        "```csv\n"
        f"{rules_csv}\n"
        "```\n"
    )
