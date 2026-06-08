"""Entry point — polling mode for Phase 1 / local use."""
from __future__ import annotations

import logging

import config
from bot_handler import build_application
from knowledge_loader import set_sheets_client


def configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        level=config.LOG_LEVEL,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)


def _init_sheets():
    """Try to bring up the Sheets backend. Returns SheetsClient or None."""
    log = logging.getLogger("main")
    if not config.sheets_enabled():
        log.warning(
            "Sheets backend NOT configured — falling back to local CSV. "
            "Feedback buttons + KB writes will be disabled. "
            "Set GSHEET_ID + place service account JSON to enable."
        )
        return None

    try:
        from sheets_client import SheetsClient
        client = SheetsClient()
        rows = client.kb_row_count()
        log.info(
            "Sheets backend OK — sheet=%s kb_rows=%d refresh=%ds",
            client._sheet.title, rows, config.KB_REFRESH_SECONDS,
        )
        return client
    except Exception as exc:
        log.exception(
            "Failed to init Sheets backend: %s — falling back to local CSV.", exc,
        )
        return None


def main() -> None:
    configure_logging()
    log = logging.getLogger("main")

    if not config.MARKETING_CHAT_IDS and not config.ADMIN_CHAT_IDS:
        log.warning(
            "Both MARKETING_CHAT_IDS and ADMIN_CHAT_IDS are empty — "
            "bot will not reply to any chat."
        )

    if not config.ADMIN_CHAT_IDS:
        log.warning(
            "ADMIN_CHAT_IDS not set — mirror & AI-mediated KB loop disabled. "
            "Bot will operate in single-group mode."
        )

    sheets = _init_sheets()
    if sheets is not None:
        set_sheets_client(sheets)

    log.info(
        "Starting bot — model=%s env=%s marketing=%d admin=%d admins=%d sheets=%s",
        config.ANTHROPIC_MODEL,
        config.ENVIRONMENT,
        len(config.MARKETING_CHAT_IDS),
        len(config.ADMIN_CHAT_IDS),
        len(config.ADMIN_USER_IDS),
        "ON" if sheets else "OFF (CSV fallback)",
    )

    app = build_application(sheets_client=sheets)
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
