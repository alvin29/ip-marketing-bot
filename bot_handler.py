"""Telegram handlers: marketing group → bot, admin group → feedback + KB loop."""
from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    constants,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from claude_client import ClaudeClient, ClaudeReply, KbProposal
from sheets_client import KB_HEADERS, Conversation, SheetsClient

logger = logging.getLogger(__name__)

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
CONVERSATION_LOG = LOG_DIR / "conversations.jsonl"


# ─── Runtime state ───────────────────────────────────────────────────────

@dataclass
class ConvState:
    conv_id: str
    marketing_chat_id: int
    marketing_chat_title: str
    marketing_user_id: int
    marketing_username: str | None
    marketing_first_name: str | None
    inquiry: str
    response: str
    marketing_response_msg_id: int | None = None  # last bot reply msg in marketing group
    admin_mirror_msg_id: int | None = None        # message id in admin group
    pending_correction: bool = False              # set when 👎/📝 clicked


@dataclass
class ProposalState:
    proposal_id: str
    conv_id: str
    admin_user_id: int
    admin_name: str
    correction_text: str
    proposal: KbProposal
    admin_proposal_msg_id: int | None = None


class BotState:
    def __init__(self) -> None:
        self.paused: bool = False
        self.conversations: dict[str, ConvState] = {}
        self.proposals: dict[str, ProposalState] = {}

    def remember_conv(self, st: ConvState) -> None:
        self.conversations[st.conv_id] = st
        self._evict(self.conversations, 200)

    def remember_proposal(self, st: ProposalState) -> None:
        self.proposals[st.proposal_id] = st
        self._evict(self.proposals, 100)

    @staticmethod
    def _evict(d: dict, limit: int) -> None:
        while len(d) > limit:
            d.pop(next(iter(d)), None)

    def find_conv_by_admin_msg(self, admin_msg_id: int) -> ConvState | None:
        for conv in reversed(list(self.conversations.values())):
            if conv.admin_mirror_msg_id == admin_msg_id:
                return conv
        return None


# ─── Local fallback log ─────────────────────────────────────────────────

def _log_local(entry: dict) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    try:
        with CONVERSATION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Failed to write JSONL log: %s", exc)


# ─── Telegram helpers ────────────────────────────────────────────────────

def _split_for_telegram(text: str, limit: int = config.TELEGRAM_MAX_MESSAGE_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1 or cut == 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _is_mention(update: Update) -> tuple[bool, str]:
    msg = update.effective_message
    if not msg or not msg.text:
        return False, ""
    text = msg.text
    bot_handle = f"@{config.BOT_USERNAME}"
    if msg.entities:
        for ent in msg.entities:
            if ent.type == "mention":
                mentioned_handle = text[ent.offset : ent.offset + ent.length]
                if mentioned_handle.lower() == bot_handle.lower():
                    return True, (text[: ent.offset] + text[ent.offset + ent.length :]).strip()
    if bot_handle.lower() in text.lower():
        cleaned = text.replace(bot_handle, "").replace(bot_handle.lower(), "").strip()
        return True, cleaned
    return False, ""


def _whitelist_guard(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    if not config.is_allowed_chat(chat.id):
        logger.info(
            "Ignored chat id=%s title=%r",
            chat.id,
            getattr(chat, "title", None),
        )
        return False
    return True


def _feedback_keyboard(conv_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 Bagus", callback_data=f"fb:up:{conv_id}"),
        InlineKeyboardButton("👎 Salah", callback_data=f"fb:down:{conv_id}"),
        InlineKeyboardButton("📝 Koreksi", callback_data=f"fb:note:{conv_id}"),
    ]])


def _proposal_keyboard(proposal_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Setuju, masukin KB", callback_data=f"pr:ok:{proposal_id}"),
        InlineKeyboardButton("❌ Tolak", callback_data=f"pr:no:{proposal_id}"),
    ]])


async def _send_long(
    bot, chat_id: int, text: str, reply_markup=None
):
    """Send text, possibly split. Returns the LAST message sent (where buttons attach)."""
    chunks = _split_for_telegram(text)
    last = None
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        kwargs: dict[str, Any] = {}
        if is_last and reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        try:
            last = await bot.send_message(
                chat_id, chunk, parse_mode=constants.ParseMode.MARKDOWN, **kwargs
            )
        except Exception:
            last = await bot.send_message(chat_id, chunk, **kwargs)
    return last


# ─── Core inquiry handler ────────────────────────────────────────────────

async def _handle_inquiry(
    update: Update, context: ContextTypes.DEFAULT_TYPE, inquiry: str
) -> None:
    state: BotState = context.application.bot_data["state"]
    client: ClaudeClient = context.application.bot_data["claude"]
    sheets: SheetsClient | None = context.application.bot_data.get("sheets")
    chat = update.effective_chat
    user = update.effective_user

    if state.paused:
        if chat:
            await chat.send_message("Bot lagi di-pause sama admin. Coba lagi nanti.")
        return

    inquiry = (inquiry or "").strip()
    if not inquiry:
        if chat:
            await chat.send_message(
                "Kasih detail inquiry-nya pak — barang apa, berapa CBM, ada brand atau engga."
            )
        return

    if chat:
        try:
            await chat.send_action(constants.ChatAction.TYPING)
        except Exception:
            pass

    logger.info(
        "Inquiry chat=%s(%s) user=%s len=%d",
        chat.id if chat else "?",
        "ADMIN" if (chat and config.is_admin_chat(chat.id)) else "MARKETING",
        user.id if user else "?",
        len(inquiry),
    )

    reply: ClaudeReply = await client.generate(inquiry)
    conv_id = secrets.token_urlsafe(8)

    # Send to the originating chat WITHOUT feedback buttons.
    # Track the message id so we can reply-to it on /correct approval.
    marketing_response = await _send_long(context.bot, chat.id, reply.text)
    marketing_response_msg_id = (
        marketing_response.message_id if marketing_response else None
    )

    # Mirror to admin group if (a) inquiry is from a marketing group AND
    # (b) admin chat is configured.
    admin_mirror_msg_id: int | None = None
    admin_chat_id = config.primary_admin_chat()
    is_from_marketing = chat is not None and config.is_marketing_chat(chat.id)

    if admin_chat_id is not None and is_from_marketing:
        username = f"@{user.username}" if user and user.username else (
            user.full_name if user else "anon"
        )
        header = (
            f"📥 *INQUIRY DARI MARKETING*\n"
            f"From: {username}\n"
            f"Group: {chat.title or chat.id}\n\n"
            f"*Pertanyaan:*\n{inquiry}\n\n"
            f"*Jawaban bot:*\n"
        )
        try:
            mirror = await _send_long(
                context.bot,
                admin_chat_id,
                header + reply.text,
                reply_markup=_feedback_keyboard(conv_id),
            )
            if mirror:
                admin_mirror_msg_id = mirror.message_id
        except Exception as exc:
            logger.exception("Mirror to admin group failed: %s", exc)

    # Persist state
    state.remember_conv(ConvState(
        conv_id=conv_id,
        marketing_chat_id=chat.id if chat else 0,
        marketing_chat_title=(chat.title if chat else "") or "",
        marketing_user_id=user.id if user else 0,
        marketing_username=user.username if user else None,
        marketing_first_name=user.first_name if user else None,
        inquiry=inquiry,
        response=reply.text,
        marketing_response_msg_id=marketing_response_msg_id,
        admin_mirror_msg_id=admin_mirror_msg_id,
    ))

    if sheets is not None:
        sheets.log_conversation(Conversation(
            conv_id=conv_id,
            chat_id=chat.id if chat else 0,
            user_id=user.id if user else 0,
            username=user.username if user else None,
            first_name=user.first_name if user else None,
            inquiry=inquiry,
            response=reply.text,
            latency_ms=reply.latency_ms,
            model=reply.model,
        ))
        for item in reply.unknown_items:
            sheets.log_pending_item(conv_id, item, inquiry[:500])

    _log_local({
        "conv_id": conv_id,
        "chat_id": chat.id if chat else None,
        "user_id": user.id if user else None,
        "username": user.username if user else None,
        "inquiry": inquiry,
        "response": reply.text,
        "latency_ms": reply.latency_ms,
        "unknown_items": reply.unknown_items,
    })


# ─── /correct in admin group → trigger AI proposal ───────────────────────

async def cmd_correct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin replies to a mirrored inquiry with `/correct <teks koreksi>`.

    Bot fires a 2nd Claude call to propose a KB update, then posts that proposal
    with [✅ Setuju] / [❌ Tolak] buttons in the admin group.
    """
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message
    if not chat or not config.is_admin_chat(chat.id):
        return
    if not user or not config.is_admin(user.id):
        return
    if not msg or not msg.reply_to_message:
        await chat.send_message(
            "Reply ke pesan mirror dulu, lalu ketik:\n`/correct <teks koreksi>`",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    state: BotState = context.application.bot_data["state"]
    client: ClaudeClient = context.application.bot_data["claude"]
    sheets: SheetsClient | None = context.application.bot_data.get("sheets")

    correction = " ".join(context.args) if context.args else ""
    if not correction.strip():
        await chat.send_message("Kasih teks koreksinya: `/correct <teks koreksi>`",
                                parse_mode=constants.ParseMode.MARKDOWN)
        return

    conv = state.find_conv_by_admin_msg(msg.reply_to_message.message_id)
    if conv is None:
        await chat.send_message(
            "Gak ketemu conversation yang di-reply. Mungkin udah lama / bot baru restart."
        )
        return

    # Record the correction first (so we don't lose it even if AI fails)
    admin_name = user.full_name or user.username or str(user.id)
    if sheets is not None:
        sheets.log_feedback(conv.conv_id, user.id, admin_name, "correction", correction)

    await chat.send_action(constants.ChatAction.TYPING)
    await chat.send_message("🔄 Lagi proses koreksi pak, sebentar...")

    proposal = await client.propose_kb_update(
        inquiry=conv.inquiry,
        original_response=conv.response,
        admin_correction=correction,
    )

    await _post_proposal_to_admin(
        context, state, conv, user, admin_name, correction, proposal
    )


async def _post_proposal_to_admin(
    context: ContextTypes.DEFAULT_TYPE,
    state: BotState,
    conv: ConvState,
    user,
    admin_name: str,
    correction_text: str,
    proposal: KbProposal,
) -> None:
    admin_chat_id = config.primary_admin_chat()
    if admin_chat_id is None:
        logger.warning("Proposal generated but no admin chat configured.")
        return

    if proposal.action == "error":
        await context.bot.send_message(
            admin_chat_id,
            f"⚠️ Gagal proses koreksi: {proposal.reasoning}\n\n"
            f"Koreksi tetep ke-log di Sheet ya pak.",
        )
        return

    if proposal.action == "no_change":
        await context.bot.send_message(
            admin_chat_id,
            f"ℹ️ AI baca koreksi, tapi gak ngusulin perubahan KB.\n\n"
            f"Alasan: {proposal.reasoning}\n\n"
            f"Koreksi tetep ke-log.",
        )
        return

    proposal_id = secrets.token_urlsafe(8)
    state.remember_proposal(ProposalState(
        proposal_id=proposal_id,
        conv_id=conv.conv_id,
        admin_user_id=user.id,
        admin_name=admin_name,
        correction_text=correction_text,
        proposal=proposal,
    ))

    pretty = _format_proposal(proposal)
    text = (
        f"🔄 *USULAN UPDATE KB*\n\n"
        f"Aksi: `{proposal.action}`\n"
        f"Alasan: {proposal.reasoning}\n"
    )
    if proposal.action == "update_existing" and proposal.match_item:
        text += f"Item yang di-update: *{proposal.match_item}*\n"
    text += f"\n*Row yang diusulkan:*\n```\n{pretty}\n```\n"

    sent = await context.bot.send_message(
        admin_chat_id,
        text,
        parse_mode=constants.ParseMode.MARKDOWN,
        reply_markup=_proposal_keyboard(proposal_id),
    )
    state.proposals[proposal_id].admin_proposal_msg_id = (
        sent.message_id if sent else None
    )


def _format_proposal(proposal: KbProposal) -> str:
    lines = []
    for h in KB_HEADERS:
        val = proposal.kb_row.get(h, "")
        lines.append(f"{h:<28} : {val}")
    return "\n".join(lines)


# ─── Feedback button handler ─────────────────────────────────────────────

async def on_feedback_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    user = query.from_user

    if not config.is_admin(user.id):
        await query.answer("Cuma admin yang bisa kasih feedback.", show_alert=False)
        return

    chat = update.effective_chat
    if chat and not config.is_admin_chat(chat.id):
        # Feedback buttons should only appear in admin group, but defensive check.
        await query.answer("Feedback cuma di grup admin.", show_alert=False)
        return

    sheets: SheetsClient | None = context.application.bot_data.get("sheets")
    state: BotState = context.application.bot_data["state"]

    try:
        _, kind, conv_id = query.data.split(":", 2)
    except ValueError:
        await query.answer("Bad callback data.", show_alert=False)
        return

    admin_name = (user.full_name or user.username or str(user.id))

    if kind == "up":
        if sheets is not None:
            sheets.log_feedback(conv_id, user.id, admin_name, "rating", "👍")
        await query.answer(f"Tersimpan ({admin_name}): 👍")
        return

    if kind in ("down", "note"):
        # Log the rating, then prompt for correction.
        if sheets is not None:
            sheets.log_feedback(conv_id, user.id, admin_name, "rating",
                                 "👎" if kind == "down" else "📝")
        conv = state.conversations.get(conv_id)
        if conv is not None:
            conv.pending_correction = True

        await query.answer("Reply ke pesan mirror lalu /correct <koreksi>",
                            show_alert=True)
        # Also send a public nudge so other admins know correction is awaited.
        if chat:
            await chat.send_message(
                f"⚠️ {admin_name} flag jawaban ini perlu koreksi.\n"
                f"Reply ke pesan mirror di atas, lalu ketik:\n"
                f"`/correct <teks koreksi yang benar>`",
                parse_mode=constants.ParseMode.MARKDOWN,
            )
        return

    await query.answer("Unknown button.", show_alert=False)


# ─── Proposal button handler ─────────────────────────────────────────────

async def on_proposal_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    user = query.from_user

    if not config.is_admin(user.id):
        await query.answer("Cuma admin yang bisa approve.", show_alert=False)
        return

    chat = update.effective_chat
    sheets: SheetsClient | None = context.application.bot_data.get("sheets")
    state: BotState = context.application.bot_data["state"]

    try:
        _, action, proposal_id = query.data.split(":", 2)
    except ValueError:
        await query.answer("Bad callback data.", show_alert=False)
        return

    prop_st = state.proposals.get(proposal_id)
    if prop_st is None:
        await query.answer("Proposal expired / bot restart. Coba /correct lagi.",
                            show_alert=True)
        return

    admin_name = (user.full_name or user.username or str(user.id))

    if action == "no":
        if sheets is not None:
            sheets.log_feedback(
                prop_st.conv_id, user.id, admin_name, "proposal_reject",
                json.dumps(prop_st.proposal.kb_row, ensure_ascii=False),
            )
        await query.answer(f"Ditolak oleh {admin_name}. Tidak ada perubahan KB.",
                            show_alert=False)
        if chat:
            await chat.send_message(f"❌ Usulan KB ditolak oleh {admin_name}.")
        return

    if action == "ok":
        if sheets is None:
            await query.answer("Sheets belum aktif — gak bisa tulis KB.",
                                show_alert=True)
            return

        proposal = prop_st.proposal
        try:
            if proposal.action == "add_new":
                new_count = sheets.add_kb_row(proposal.kb_row)
                result_msg = (
                    f"✅ Item *{proposal.kb_row.get('item', '?')}* "
                    f"ditambah ke KB (tier {proposal.kb_row.get('tier', '?')}).\n"
                    f"Total KB sekarang {new_count} rows.\n"
                    f"Disetujui oleh: {admin_name}"
                )
            elif proposal.action == "update_existing":
                ok = sheets.update_kb_row(
                    proposal.match_item or proposal.kb_row.get("item", ""),
                    proposal.kb_row,
                )
                if ok:
                    result_msg = (
                        f"✅ Row *{proposal.match_item}* di-update di KB.\n"
                        f"Disetujui oleh: {admin_name}"
                    )
                else:
                    # Fallback: add as new if no match found
                    new_count = sheets.add_kb_row(proposal.kb_row)
                    result_msg = (
                        f"⚠️ Tidak ketemu row lama '{proposal.match_item}' untuk di-update.\n"
                        f"Saya tambah sebagai row baru. KB sekarang {new_count} rows.\n"
                        f"Disetujui oleh: {admin_name}"
                    )
            else:
                result_msg = f"Aksi `{proposal.action}` gak handled."
        except Exception as exc:
            logger.exception("KB write failed: %s", exc)
            await query.answer(f"Gagal tulis KB: {exc}", show_alert=True)
            return

        # Log the approval
        sheets.log_feedback(
            prop_st.conv_id, user.id, admin_name, "proposal_approved",
            json.dumps(proposal.kb_row, ensure_ascii=False),
        )

        await query.answer(f"KB updated ({admin_name})", show_alert=False)
        if chat:
            await chat.send_message(
                result_msg, parse_mode=constants.ParseMode.MARKDOWN
            )

        # ─── Re-send corrected response to marketing group ──────────
        conv = state.conversations.get(prop_st.conv_id)
        if conv is not None and conv.marketing_chat_id:
            try:
                await _resend_corrected_to_marketing(
                    context, conv, admin_name, sheets
                )
            except Exception as exc:
                logger.exception("Failed to resend corrected reply: %s", exc)
                if chat:
                    await chat.send_message(
                        f"⚠️ KB udah ke-update, tapi gagal kirim ulang ke grup "
                        f"marketing: {exc}"
                    )
        return

    await query.answer("Unknown action.", show_alert=False)


async def _resend_corrected_to_marketing(
    context: ContextTypes.DEFAULT_TYPE,
    conv: ConvState,
    admin_name: str,
    sheets: SheetsClient,
) -> None:
    """After admin approves a KB update, re-run the original inquiry through
    Claude (now with the updated KB) and post the corrected answer in the
    original marketing chat as a reply to the bot's first (wrong) response.
    """
    client: ClaudeClient = context.application.bot_data["claude"]

    # Force-refresh KB cache so Claude sees the new row this call.
    try:
        sheets.get_kb_rows(force_refresh=True)
    except Exception as exc:
        logger.warning("KB refresh before re-run failed: %s", exc)

    new_reply: ClaudeReply = await client.generate(conv.inquiry)

    header = (
        f"🔄 *KOREKSI JAWABAN*\n"
        f"_Update dari tim admin ({admin_name}). Jawaban yang benar:_\n\n"
    )
    body = header + new_reply.text

    # Send to the marketing group, replying to the original (wrong) bot reply
    # if we still have its message_id.
    chunks = _split_for_telegram(body)
    last_msg = None
    for i, chunk in enumerate(chunks):
        kwargs: dict[str, Any] = {}
        if i == 0 and conv.marketing_response_msg_id:
            kwargs["reply_to_message_id"] = conv.marketing_response_msg_id
            kwargs["allow_sending_without_reply"] = True
        try:
            last_msg = await context.bot.send_message(
                conv.marketing_chat_id,
                chunk,
                parse_mode=constants.ParseMode.MARKDOWN,
                **kwargs,
            )
        except Exception:
            last_msg = await context.bot.send_message(
                conv.marketing_chat_id, chunk, **kwargs
            )

    # Update conv state: the NEW response becomes the canonical one,
    # so subsequent /correct flows refer to the corrected message.
    conv.response = new_reply.text
    if last_msg is not None:
        conv.marketing_response_msg_id = last_msg.message_id

    # Log the corrected response to Sheet so audit trail is complete.
    sheets.log_conversation(Conversation(
        conv_id=f"{conv.conv_id}-corrected",
        chat_id=conv.marketing_chat_id,
        user_id=conv.marketing_user_id,
        username=conv.marketing_username,
        first_name=conv.marketing_first_name,
        inquiry=f"[KOREKSI by {admin_name}] {conv.inquiry}",
        response=new_reply.text,
        latency_ms=new_reply.latency_ms,
        model=new_reply.model,
    ))


# ─── Standard commands ───────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelist_guard(update):
        return
    chat = update.effective_chat
    role = "ADMIN" if config.is_admin_chat(chat.id) else "MARKETING"
    await chat.send_message(
        f"Halo 🙏 Ini IP Marketing AI Agent.\n"
        f"Grup ini: *{role}*\n\n"
        f"Cara pakai: ketik `/quote <inquiry>`\n"
        f"Contoh: `/quote mainan plastik 5 CBM no brand`\n\n"
        f"Ketik /help untuk panduan.",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelist_guard(update):
        return
    chat = update.effective_chat
    is_admin_grp = config.is_admin_chat(chat.id)
    base = (
        "*Inquiry*\n"
        "• `/quote <inquiry>`\n"
        "  Contoh: `/quote thermos stainless 2 CBM dari Yiwu, no brand`\n\n"
        "Bot HANYA respond ke `/quote` (untuk hemat biaya AI). "
        "Pesan biasa di grup gak akan dijawab.\n\n"
    )
    if is_admin_grp:
        base += (
            "*Admin commands (di grup ini)*\n"
            "• `/correct <teks>` — reply ke pesan mirror, lalu koreksi\n"
            "• `/status` `/pause` `/resume` `/reload_kb` `/kb_add`\n\n"
            "Setiap inquiry dari grup marketing otomatis ke-mirror di sini "
            "dengan tombol 👍/👎/📝. Kalau klik 👎 atau 📝, bot bakal "
            "ngingetin kasih `/correct`."
        )
    else:
        base += (
            "Bot bakal jawab di grup ini. Feedback / koreksi handled di "
            "grup admin (Alvin + tim)."
        )
    await chat.send_message(base, parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelist_guard(update):
        return
    args_text = " ".join(context.args) if context.args else ""
    await _handle_inquiry(update, context, args_text)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelist_guard(update):
        return
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return
    state: BotState = context.application.bot_data["state"]
    client: ClaudeClient = context.application.bot_data["claude"]
    sheets: SheetsClient | None = context.application.bot_data.get("sheets")
    kb_count = sheets.kb_row_count() if sheets is not None else "(local CSV)"
    await update.effective_chat.send_message(
        f"Status: {'PAUSED' if state.paused else 'ONLINE'}\n"
        f"Model: {client.model}\n"
        f"KB rows: {kb_count}\n"
        f"KB source: {'Google Sheets' if sheets else 'local CSV'}\n"
        f"Marketing chats: {len(config.MARKETING_CHAT_IDS)}\n"
        f"Admin chats: {len(config.ADMIN_CHAT_IDS)}\n"
        f"Admins: {len(config.ADMIN_USER_IDS)}"
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelist_guard(update):
        return
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return
    context.application.bot_data["state"].paused = True
    await update.effective_chat.send_message("Bot di-pause.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelist_guard(update):
        return
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return
    context.application.bot_data["state"].paused = False
    await update.effective_chat.send_message("Bot resumed.")


async def cmd_reload_kb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelist_guard(update):
        return
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return
    sheets: SheetsClient | None = context.application.bot_data.get("sheets")
    if sheets is None:
        await update.effective_chat.send_message(
            "Sheets backend tidak aktif (pakai local CSV)."
        )
        return
    rows = sheets.get_kb_rows(force_refresh=True)
    await update.effective_chat.send_message(
        f"KB reloaded — {len(rows)} rows aktif."
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: rekap semua inquiry hari ini."""
    if not _whitelist_guard(update):
        return
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return

    from datetime import date
    import json as _json

    today = date.today().isoformat()
    if not CONVERSATION_LOG.exists():
        await update.effective_chat.send_message("Belum ada log hari ini.")
        return

    entries: list[dict] = []
    try:
        with CONVERSATION_LOG.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if (obj.get("ts") or "").startswith(today):
                    entries.append(obj)
    except OSError as exc:
        await update.effective_chat.send_message(f"Gagal baca log: {exc}")
        return

    if not entries:
        await update.effective_chat.send_message(
            f"Belum ada inquiry hari ini ({today})."
        )
        return

    # Group by user
    from collections import Counter
    by_user: dict[int, list[dict]] = {}
    for e in entries:
        uid = e.get("user_id") or 0
        by_user.setdefault(uid, []).append(e)

    flagged_unknown = sum(len(e.get("unknown_items") or []) for e in entries)

    lines = [
        f"*Rekap inquiry hari ini ({today})*",
        f"Total: {len(entries)} inquiry dari {len(by_user)} orang",
    ]
    if flagged_unknown:
        lines.append(
            f"⚠️ {flagged_unknown} item di-flag sebagai unknown (cek tab pending_items)"
        )
    lines.append("")

    for uid, items in by_user.items():
        uname = items[0].get("username") or f"user_{uid}"
        lines.append(f"*@{uname}* ({len(items)} inquiry):")
        for e in items[-10:]:  # max 10 per user, kalau lebih dari 10 ambil terakhir
            ts = (e.get("ts") or "")[11:16]  # HH:MM
            inq = (e.get("inquiry") or "")[:80]
            lines.append(f"  • `{ts}` {inq}")
        if len(items) > 10:
            lines.append(f"  ... dan {len(items) - 10} lainnya")
        lines.append("")

    text = "\n".join(lines)
    await _send_long(context.bot, update.effective_chat.id, text)


async def cmd_kb_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual KB add — kept for cases where Bapak skips the AI proposal flow."""
    if not _whitelist_guard(update):
        return
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return
    sheets: SheetsClient | None = context.application.bot_data.get("sheets")
    if sheets is None:
        await update.effective_chat.send_message("Sheets backend belum aktif.")
        return

    args_text = " ".join(context.args) if context.args else ""
    if not args_text:
        await update.effective_chat.send_message(
            "Format: `/kb_add item | tier | price | hs | brand_y_n | ovw | acceptance | "
            "notes | packing | indo_reg | china_note`",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    parts = [p.strip() for p in args_text.split("|")]
    if len(parts) < 3:
        await update.effective_chat.send_message(
            "Minimal `item | tier | price`. Ketik `/kb_add` untuk format penuh."
        )
        return

    fields = {}
    for i, header in enumerate(KB_HEADERS):
        val = parts[i] if i < len(parts) else ""
        fields[header] = "" if val == "-" else val

    try:
        new_count = sheets.add_kb_row(fields)
    except Exception as exc:
        await update.effective_chat.send_message(f"❌ Gagal: {exc}")
        return

    await update.effective_chat.send_message(
        f"✅ Item *{fields['item']}* ditambah. Total KB: {new_count} rows.",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


# ─── Mention handler ─────────────────────────────────────────────────────

async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelist_guard(update):
        return
    mentioned, stripped = _is_mention(update)
    if not mentioned:
        return
    await _handle_inquiry(update, context, stripped)


# ─── Builder ─────────────────────────────────────────────────────────────

def build_application(sheets_client: SheetsClient | None = None) -> Application:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.bot_data["state"] = BotState()
    app.bot_data["claude"] = ClaudeClient()
    app.bot_data["sheets"] = sheets_client

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("reload_kb", cmd_reload_kb))
    app.add_handler(CommandHandler("kb_add", cmd_kb_add))
    app.add_handler(CommandHandler("correct", cmd_correct))
    app.add_handler(CommandHandler("today", cmd_today))

    app.add_handler(CallbackQueryHandler(on_feedback_button, pattern=r"^fb:"))
    app.add_handler(CallbackQueryHandler(on_proposal_button, pattern=r"^pr:"))

    # Mention handler is DISABLED — bot only responds to slash commands.
    # This is intentional: save Claude API costs by requiring explicit /quote.

    return app
