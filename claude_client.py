"""Async wrapper around the Anthropic API for the IP Marketing bot."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic, APIError, APIStatusError, RateLimitError

import config
from knowledge_loader import get_full_system_prompt

logger = logging.getLogger(__name__)

_USER_CONTEXT_TEMPLATE = (
    "Inquiry dari marketing:\n"
    "{user_message}\n\n"
    "Output format WAJIB ikuti instruksi system: 4 baris pokok "
    "(Klasifikasi/Harga/Container/HS Code) + max 1 baris note. "
    "JANGAN tanya balik. JANGAN format 2-layer."
)


# Matches `[NEEDS_KB_ENTRY: ...]` lines emitted by the model when an item isn't in KB.
_NEEDS_KB_PATTERN = re.compile(
    r"^\s*\[\s*NEEDS_KB_ENTRY\s*:\s*(?P<item>[^\]]+?)\s*\]\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class ClaudeReply:
    text: str
    latency_ms: int
    model: str
    unknown_items: list[str]


class ClaudeClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key or config.ANTHROPIC_API_KEY)
        self.model = model or config.ANTHROPIC_MODEL
        self.max_tokens = max_tokens

    async def generate(self, user_message: str) -> ClaudeReply:
        system_prompt = get_full_system_prompt()
        user_content = _USER_CONTEXT_TEMPLATE.format(user_message=user_message)
        t0 = time.monotonic()

        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
            )
        except RateLimitError as exc:
            logger.warning("Anthropic rate limit: %s", exc)
            return ClaudeReply(
                text="⚠️ Sistem sedang ramai (rate limit). Coba lagi sebentar ya pak.",
                latency_ms=int((time.monotonic() - t0) * 1000),
                model=self.model,
                unknown_items=[],
            )
        except APIStatusError as exc:
            logger.exception("Anthropic API status error: %s", exc)
            return ClaudeReply(
                text=(
                    "⚠️ Maaf, lagi ada gangguan di sisi AI. Coba ulang sebentar lagi "
                    "atau langsung tanya ke Alvin ya pak."
                ),
                latency_ms=int((time.monotonic() - t0) * 1000),
                model=self.model,
                unknown_items=[],
            )
        except APIError as exc:
            logger.exception("Anthropic API error: %s", exc)
            return ClaudeReply(
                text=(
                    "⚠️ Maaf, ada error dari sisi AI. Coba ulang ya, kalau masih "
                    "gagal langsung tanya ke Alvin."
                ),
                latency_ms=int((time.monotonic() - t0) * 1000),
                model=self.model,
                unknown_items=[],
            )

        latency_ms = int((time.monotonic() - t0) * 1000)
        parts = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        ]
        raw = "\n".join(parts).strip() or "(empty response)"

        unknown_items = [m.group("item").strip() for m in _NEEDS_KB_PATTERN.finditer(raw)]
        cleaned = _NEEDS_KB_PATTERN.sub("", raw).strip()
        # Collapse any runs of blank lines left behind by the strip
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        return ClaudeReply(
            text=cleaned,
            latency_ms=latency_ms,
            model=self.model,
            unknown_items=unknown_items,
        )

    async def propose_kb_update(
        self,
        inquiry: str,
        original_response: str,
        admin_correction: str,
    ) -> "KbProposal":
        """Synthesize a KB update from an admin correction.

        Returns a structured proposal: which action (add/update/no_change),
        the proposed KB row, and reasoning. Caller is expected to ask the
        admin to approve before writing to the Sheet.
        """
        system = get_full_system_prompt() + (
            "\n\n## TASK: PROPOSE KB UPDATE\n"
            "Kamu sedang me-review koreksi dari admin terhadap response bot yang lalu. "
            "Tugas kamu: keluarkan satu JSON object (tanpa markdown fence, tanpa "
            "komentar di luar JSON) yang menggambarkan update KB yang diusulkan.\n\n"
            "Struktur JSON wajib:\n"
            "{\n"
            '  "action": "add_new" | "update_existing" | "no_change",\n'
            '  "reasoning": "1-2 kalimat alasan singkat (Bahasa Indonesia)",\n'
            '  "match_item": "<nama item lama di KB yg di-update; null kalau action=add_new>",\n'
            '  "kb_row": {\n'
            '     "item": "...",\n'
            '     "tier": "Umum 1 | Umum 2 | Lartas 1 | Lartas Berat | Semi Garment | '
            'Kosmetik Makanan | Garment | Hot Items | Tekstil | Super Sensitive | Rokok | REJECT",\n'
            '     "container": "Umum | Mix",\n'
            '     "price_per_cbm": "<integer rupiah, atau Special-drone, atau 0 untuk REJECT>",\n'
            '     "hs_code_hint": "<string, boleh kosong>",\n'
            '     "brand_surcharge_possible": "Y | N",\n'
            '     "ovw_rate": "5000",\n'
            '     "acceptance_status": "auto | conditional | reject",\n'
            '     "special_notes": "...",\n'
            '     "packing_requirement": "Standard | ...",\n'
            '     "indonesia_regulation": "Standard | ...",\n'
            '     "china_export_note": "Standard | ..."\n'
            "  }\n"
            "}\n\n"
            "Aturan:\n"
            "- Tier WAJIB cocok salah satu dari opsi di atas, persis.\n"
            "- container WAJIB \"Umum\" atau \"Mix\". Default: Umum 1/2 & Lartas 1 = Umum; "
            "Hot Items / Super Sensitive / Lartas Berat = Mix. Sesuaikan dengan koreksi admin.\n"
            "- price_per_cbm WAJIB sama dengan tier-nya (Umum 1=4500000, Umum 2=5500000, "
            "Lartas 1=5500000, Lartas Berat=6500000, Semi Garment=7500000, "
            "Kosmetik Makanan=8000000, Garment=10000000, Hot Items=10000000, "
            "Tekstil=12500000, Super Sensitive=20000000, Rokok=30000000).\n"
            "- Kalau koreksi tidak mengusulkan perubahan substantif (misalnya admin "
            'cuma confirm jawaban benar), pakai action="no_change" dan kb_row kosong.\n'
            "- Kalau item mirip dengan yang ada di CSV (synonym/varian), pakai "
            'action="update_existing" dan tulis nama lama di match_item.\n'
            "- HANYA output JSON. Jangan ada markdown fence, jangan ada teks lain."
        )

        user_msg = (
            "INQUIRY MARKETING:\n"
            f"{inquiry}\n\n"
            "RESPONSE BOT YANG SUDAH KEPATEN:\n"
            f"{original_response}\n\n"
            "KOREKSI ADMIN:\n"
            f"{admin_correction}\n\n"
            "Keluarkan JSON usulan KB update sekarang."
        )

        # Use the cheaper "fast" model (Haiku) for this structured-output task.
        # Schema is tight, validation is strict — Haiku is plenty.
        fast_model = config.ANTHROPIC_MODEL_FAST
        t0 = time.monotonic()
        try:
            response = await self._client.messages.create(
                model=fast_model,
                max_tokens=1500,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
            )
        except APIError as exc:
            logger.exception("Anthropic error on propose_kb_update: %s", exc)
            return KbProposal(
                action="error",
                reasoning=f"AI error: {exc}",
                latency_ms=int((time.monotonic() - t0) * 1000),
                raw="",
            )

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = "\n".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()

        # Strip optional ``` fences just in case the model adds them
        cleaned_raw = re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned_raw = re.sub(r"\s*```$", "", cleaned_raw).strip()

        try:
            data: dict[str, Any] = json.loads(cleaned_raw)
        except json.JSONDecodeError as exc:
            logger.warning("Bad JSON from propose_kb_update: %s -- raw=%r", exc, raw[:300])
            return KbProposal(
                action="error",
                reasoning=f"AI returned invalid JSON: {exc}",
                latency_ms=latency_ms,
                raw=raw,
            )

        return KbProposal(
            action=str(data.get("action", "error")),
            reasoning=str(data.get("reasoning", "")),
            match_item=(data.get("match_item") or None),
            kb_row={k: str(v) for k, v in (data.get("kb_row") or {}).items()},
            latency_ms=latency_ms,
            raw=raw,
            model=fast_model,
        )


@dataclass
class KbProposal:
    action: str  # "add_new" | "update_existing" | "no_change" | "error"
    reasoning: str
    latency_ms: int
    raw: str
    model: str = ""
    match_item: str | None = None
    kb_row: dict[str, str] = field(default_factory=dict)
