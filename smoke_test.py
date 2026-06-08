"""One-shot smoke test: verifies env, knowledge load, Telegram auth, Claude auth."""
from __future__ import annotations

import asyncio
import sys

import config
from knowledge_loader import get_full_system_prompt
from claude_client import ClaudeClient


async def main() -> int:
    print(f"[1/4] Config loaded.")
    print(f"      Model: {config.ANTHROPIC_MODEL}")
    print(f"      Allowed chats: {config.ALLOWED_CHAT_IDS}")
    print(f"      Admins: {config.ADMIN_USER_IDS}")
    print(f"      Bot username: @{config.BOT_USERNAME}")

    prompt = get_full_system_prompt()
    print(f"[2/4] Knowledge base loaded — {len(prompt):,} chars.")

    print("[3/4] Pinging Telegram getMe...")
    from telegram import Bot

    async with Bot(token=config.TELEGRAM_BOT_TOKEN) as bot:
        me = await bot.get_me()
        print(f"      OK — bot @{me.username} (id={me.id})")
        if me.username.lower() != config.BOT_USERNAME.lower():
            print(
                f"      ⚠️  BOT_USERNAME in .env (@{config.BOT_USERNAME}) "
                f"does not match Telegram (@{me.username}). Fix .env."
            )

    print("[4/4] Pinging Anthropic with a tiny test inquiry...")
    client = ClaudeClient(max_tokens=512)
    reply = await client.generate("test ping — kasih response 1 kalimat aja")
    print(f"      OK — {len(reply)} chars returned. First 200:")
    print(f"      {reply[:200]!r}")

    print("\nAll checks passed. Run `python main.py` to start the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
