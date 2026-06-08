"""Runtime config loaded from environment variables."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent


def _parse_id_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

# Marketing groups: bot responds to inquiries, NO feedback buttons here.
MARKETING_CHAT_IDS: list[int] = _parse_id_list(os.getenv("MARKETING_CHAT_IDS"))

# Admin groups: bot mirrors every marketing inquiry here with feedback buttons.
# Admins can also /quote here (no mirror — already there).
ADMIN_CHAT_IDS: list[int] = _parse_id_list(os.getenv("ADMIN_CHAT_IDS"))

# Legacy fallback: if ALLOWED_CHAT_IDS is set and the two new lists are empty,
# treat ALLOWED_CHAT_IDS as MARKETING (old single-group behavior).
_legacy_allowed = _parse_id_list(os.getenv("ALLOWED_CHAT_IDS"))
if _legacy_allowed and not MARKETING_CHAT_IDS and not ADMIN_CHAT_IDS:
    MARKETING_CHAT_IDS = _legacy_allowed

ADMIN_USER_IDS: list[int] = _parse_id_list(os.getenv("ADMIN_USER_IDS"))

BOT_USERNAME: str = os.getenv("BOT_USERNAME", "ip_marketing_bot").lstrip("@")

TELEGRAM_MAX_MESSAGE_LEN: int = 4000

# ─── Google Sheets backend ───────────────────────────────────────────────
GSHEET_ID: str = os.getenv("GSHEET_ID", "")

# Credentials file path. Absolute path is used as-is (for Render Secret Files
# mounted at /etc/secrets/...). Relative path is anchored to BASE_DIR.
_cred_raw = os.getenv("GOOGLE_CREDENTIALS_FILE", "secrets/service_account.json")
_cred_path = Path(_cred_raw)
GOOGLE_CREDENTIALS_FILE: Path = (
    _cred_path if _cred_path.is_absolute() else BASE_DIR / _cred_path
)
KB_REFRESH_SECONDS: int = int(os.getenv("KB_REFRESH_SECONDS", "600"))

# Sheet tab names — keep in sync with sheets_client.bootstrap_sheet()
SHEET_TAB_KB = "knowledge_base"
SHEET_TAB_CONV = "conversations"
SHEET_TAB_PENDING = "pending_items"
SHEET_TAB_FEEDBACK = "feedback_log"


def is_marketing_chat(chat_id: int) -> bool:
    return chat_id in MARKETING_CHAT_IDS


def is_admin_chat(chat_id: int) -> bool:
    return chat_id in ADMIN_CHAT_IDS


def is_allowed_chat(chat_id: int) -> bool:
    """Bot responds in marketing OR admin groups."""
    return is_marketing_chat(chat_id) or is_admin_chat(chat_id)


def primary_admin_chat() -> int | None:
    """First admin group — used as the mirror target."""
    return ADMIN_CHAT_IDS[0] if ADMIN_CHAT_IDS else None


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def sheets_enabled() -> bool:
    return bool(GSHEET_ID) and GOOGLE_CREDENTIALS_FILE.exists()
