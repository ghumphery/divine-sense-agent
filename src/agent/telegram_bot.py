"""Telegram bot daemon — bidirectional chat (v0.9.27.0+).

The v0.8.1 Telegram MCP server is OUTBOUND-only (send_telegram_message
/ send_telegram_markdown / list_allowed_chats). This module adds the
INBOUND half: a daemon thread that long-polls Telegram ``getUpdates``,
feeds whitelisted users' messages into ``Agent.chat_turn`` (full tool
surface — the LLM can use MCP tools before answering), and delivers
the reply back via ``sendMessage``.

Conversation model
------------------
* One persistent session per chat (``tgbot-<chat_id>``) so the bot
  keeps multi-turn context; created on first message, reused forever.
* The user's message is framed as::

      [Telegram message from chat <id>]
      <text>

  plus a one-line note telling the LLM the reply is auto-delivered —
  so it does NOT call send_telegram_message itself (double-send trap).
* v0.9.63.0 — INBOUND FILES: a document / photo / video / audio
  message (previously SILENTLY ignored — no text, no log, no reply)
  is now downloaded via getFile (≤20 MB Bot API cap) into the
  filesystem WRITE sandbox (``uploads/telegram/<chat_id>/``) and
  framed for a full LLM turn. Executables (.exe/.bat/...) are
  refused; any failure replies with a clear ⚠️ message.
* Security mirrors the outbound MCP server: ``TELEGRAM_ALLOWED_CHAT_IDS``
  is fail-closed — chat ids outside the allowlist are silently
  ignored (debug log only, no reply, no LLM call).
* No inbound port needed: Telegram long-polling is pure OUTBOUND
  HTTPS to api.telegram.org (same as sending).

Lifecycle (mirrors scheduler / webhook):
* ``Agent.start_telegram_bot()`` spawns the daemon; idempotent.
* Boot auto-resume via ``gui/api.py::_resume_telegram_bot`` when
  ``QDRANT_TELEGRAM_BOT_POLLING`` != off AND the bot token + a
  non-empty allowlist are configured.
* ``Agent.shutdown()`` stops the thread.

Env vars:

* ``QDRANT_TELEGRAM_BOT_POLLING``   on|off (default on; off opts out)
* ``QDRANT_TELEGRAM_BOT_POLL_INTERVAL``  seconds between polls
  (default 2.0, min 1.0, max 30) — only applies to the SHORT sleep
  after an empty poll; the long-poll server wait is 10 s.
* Reuses ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_ALLOWED_CHAT_IDS``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Same Telegram Bot API constants as the outbound MCP server.
TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096
REQUEST_TIMEOUT_S = 15.0
LONG_POLL_TIMEOUT_S = 10.0   # Telegram-side getUpdates wait
HTTP_TIMEOUT_S = 20.0        # HTTP client timeout (long poll + margin)

# ── v0.9.102.0 — telegram session context discipline ────────────
# Live report (user: 「telegram 的 session 不能更新和清除 導致 context
# 內容過多 llm 錯亂」): the tgbot-<chat_id> session grows FOREVER and
# EVERY turn fed the ENTIRE history to chat_turn — context bloat made
# the LLM drift/confuse. Three controls:
#   1. TELEGRAM_HISTORY_CAP — messages FED to the LLM per turn
#      (default 12; ≤0/-1 = legacy full feed).
#   2. TELEGRAM_SESSION_MAX_MESSAGES — stored-session ring cap
#      (default 200; ≤0/-1 = unlimited; the file keeps the newest).
#   3. In-chat commands /new /clear /reset (or 清除對話/新對話) wipe
#      the chat's session immediately — user-controlled fresh start.


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("telegram-bot: bad env %s=%r — using %s",
                       name, raw, default)
        return default


def _telegram_history_cap() -> int:
    """Messages fed to chat_turn (default 12; -1 = legacy full)."""
    v = _env_int("TELEGRAM_HISTORY_CAP", 12)
    return v if v > 0 else -1


def _telegram_session_max_messages() -> int:
    """Stored-session ring cap (default 200; -1 = unlimited)."""
    v = _env_int("TELEGRAM_SESSION_MAX_MESSAGES", 200)
    return v if v > 0 else -1


# ── v0.9.107.1 — turn-level HARD timeout (last net against silent
# hangs). Two live incidents (2026-10-04 00:06 and 12:22): the turn
# stalled with ZERO log output for ~49-58 minutes and only a manual
# Ctrl+C unlocked it. The per-tool-call watchdog (v0.9.107.0) covers
# hung MCP dispatches and the LLM client has its own timeout, but any
# OTHER un-watched stall still froze the whole bot conversation. This
# guard bounds the WHOLE chat_turn: on timeout the registry row is
# failed, the session records it, and the user gets an honest reply.
_TURN_HARD_TIMEOUT_DEFAULT_S = 1800  # 30 min; 0 = off (legacy)


def _turn_hard_timeout_s() -> int:
    """``QDRANT_TURN_HARD_TIMEOUT_S`` — wall-clock cap for ONE telegram
    chat_turn. Invalid/unset -> the 1800s default; 0 = off (legacy);
    any other non-positive number is clamped to OFF (a negative
    watchdog must never exist)."""
    v = _env_int("QDRANT_TURN_HARD_TIMEOUT_S",
                 _TURN_HARD_TIMEOUT_DEFAULT_S)
    return max(0, v)


_TURN_TIMEOUT_REPLY = (
    "(處理超時) 這則訊息的處理超過 {secs} 秒仍未完成，已被強制中止 — "
    "請重新發送或改用更小的步驟。/ Processing exceeded {secs}s and was "
    "aborted — please resend or break the task into smaller steps.)"
)

# v0.9.120.0 — the cooperative force-stop reply (the arbiter's
# extensions ran out OR the user stopped it via a stop_task call).
_TURN_STOPPED_REPLY = (
    "🛑 已依你要求強制停止本回合 — 到目前為止的工具成果保留"
    "（沙盒檔案不受影響）。"
    "/ Turn force-stopped on request; completed tool work is kept."
)


def _turn_timeout_extensions() -> int:
    """v0.9.118.0 — ``QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS`` — the
    telegram turn's extra-window budget (default 3; 0 = off = the
    exact single-window v0.9.107.1 behaviour)."""
    from src.agent.turn_arbiter import resolve_extensions

    return resolve_extensions("QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS", 3)


def _turn_jev_verdict(text: str) -> str | None:
    """v0.9.118.0 — thin wrapper over the shared arbiter verdict
    (module-global name kept monkeypatchable in tests)."""
    from src.agent.turn_arbiter import jev_verdict

    return jev_verdict(text)


def _chat_turn_guarded(agent: Any, timeout_s: int, **kwargs: Any) -> tuple:
    """Run ``agent.chat_turn(**kwargs)`` under a wall-clock guard.

    v0.9.107.1: bounding the WHOLE turn; on timeout the runaway
    thread is abandoned (daemon) and ``(None, True)`` returned.
    v0.9.118.0: at each deadline a STILL-ALIVE turn is arbitrated via
    the shared ``turn_arbiter`` — growing tool dispatches (liveness)
    or a live-Jev ``normal_long_work`` verdict grant one more window
    (up to ``QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS``); otherwise the
    abandon path runs unchanged. Returns ``(result, timed_out)``;
    exceptions from chat_turn are NOT caught here (the caller's
    existing except-path handles them). ``timeout_s <= 0`` = off ->
    plain synchronous call.
    """
    if timeout_s <= 0:
        return agent.chat_turn(**kwargs), False
    box: dict = {}

    def _run() -> None:
        try:
            box["result"] = agent.chat_turn(**kwargs)
        except BaseException as e:  # noqa: BLE001 — re-raised below
            box["error"] = e

    worker = threading.Thread(target=_run, daemon=True,
                              name="tg-turn-guard")
    worker.start()
    from src.agent import turn_arbiter

    result, timed_out, _used = turn_arbiter.guarded_run_join(
        worker, box, agent, window_s=timeout_s,
        log_label="telegram-bot", subject="Telegram 回合 ",
        meta={"question": str(kwargs.get("question") or "")},
        extensions_env="QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS",
        extensions_default=3,
        verdict_fn=_turn_jev_verdict,
    )
    return result, timed_out


_SESSION_CLEAR_COMMANDS = {
    "/new", "/clear", "/reset", "/newchat",
    "清除對話", "清除對話記錄", "新對話", "重置對話", "清除記錄",
}
_SESSION_CLEAR_REPLY = (
    "🧹 已清除這個 Telegram 對話的記錄 — 開始新對話。"
)

# ── v0.9.57.1 photo-hallucination guard ─────────────────────────
# Live diagnosis 2026-09-15 00:03 (user: 「沒畫面」): the flash model
# intermittently answers camera-grab requests by FABRICATING a success
# reply (「✅ 已發送 訊息 #219 …」) without dispatching ANY tool — the
# claimed file did not exist and no photo was ever sent. The chat
# history is full of such fake successes, which self-reinforces. The
# guard detects the deterministic shape (photo-delivery claim + no
# photo/snapshot tool dispatched this turn) and re-prompts once with a
# corrective instruction.
_CAMERA_INTENT_RE = re.compile(
    r"攝影機|監視器|監視|camera|onvif|snapshot|截圖|截取|抓取|抓.{0,8}畫面"
    r"|畫面|照片|相片|photo|picture|門口|大門|車道|庭院|前院|後院|巷",
    re.IGNORECASE,
)
_PHOTO_CLAIM_RE = re.compile(
    r"訊息\s*#\d+|message_id[\"']?\s*[:=]?\s*\d+|已(?:發送|傳送|寄出|截取)"
    r"|已送出|已傳圖|已送圖|sent_as|已將.*(?:送|傳).{0,6}telegram",
    re.IGNORECASE,
)
# Tools that make a photo-delivery claim legitimate: the actual
# Telegram photo/document send (v0.9.63.0 adds send_telegram_file —
# an oversized snapshot delivered as a DOCUMENT is a real delivery).
# An onvif_* snapshot alone never delivered anything to the chat —
# the bot auto-delivers TEXT only, so a 「已發送到 Telegram」 claim
# without send_telegram_photo / send_telegram_file is still fake.
_PHOTO_GUARD_TOOL_RE = re.compile(r"send_telegram_(photo|file)")
_PHOTO_RETRY_PROMPT = (
    "[系統偵測] 你的上一則回覆宣稱已把畫面/照片發送到 Telegram，"
    "但本回合實際上沒有呼叫任何攝影機快照或 send_telegram_photo 工具"
    " — 那是憑記憶編造的假成功，使用者並沒有收到任何圖片。\n"
    "請現在立即真的執行：onvif_get_snapshot（或 onvif_rtsp_snapshot）"
    "取得新快照 → send_telegram_photo 送出；"
    "只能回報工具實際輸出的 message_id 與 file 欄位。"
    "若工具失敗，如實說明失敗原因，不要宣稱成功。"
)

# ── v0.9.63.0 inbound file receive (document / photo / video / audio) ──
# Live diagnosis (user: 「mcp telegram對於從 tg上傳的檔案沒有回應，
# 在log沒有看到收接的回應」): _handle_update only read msg["text"] —
# a document / photo / video / audio message has NO text, so the
# update fell through the "no text" return and was SILENTLY ignored
# (no log, no reply, no LLM turn).
TELEGRAM_FILE_MAX_BYTES = 20 * 1024 * 1024   # Bot API getFile cap
_TELEGRAM_DIR = "uploads/telegram"           # under the WRITE root

# Mirror of ``src.mcp_client.host._FS_DANGEROUS_WRITE_EXTS``; kept
# local so the daemon stays dependency-light (same pattern as the
# stdio telegram server's upload gate). A received executable is
# NEVER saved into the sandbox — the user is asked to zip it first.
_TELEGRAM_INBOUND_REFUSED_EXTS: frozenset[str] = frozenset({
    ".exe", ".dll", ".so", ".dylib",
    ".bat", ".cmd", ".sh", ".ps1", ".vbs", ".js", ".wsf",
    ".scr", ".com", ".cpl",
})

_INBOUND_KIND_LABELS = {
    "document": "文件",
    "photo": "照片",
    "video": "影片",
    "video_note": "圓形影片",
    "audio": "音訊",
    "animation": "GIF 動畫",
}

_FILE_KEYS = (
    ("document", "document"),
    ("video", "video"),
    ("video_note", "video_note"),
    ("audio", "audio"),
    ("animation", "animation"),
)
_DEFAULT_NAMES = {
    "document": ("document", ".bin"),
    "video": ("video", ".mp4"),
    "video_note": ("video_note", ".mp4"),
    "audio": ("audio", ".mp3"),
    "animation": ("animation", ".mp4"),
}
_DEFAULT_MIMES = {
    "document": "",
    "video": "video/mp4",
    "video_note": "video/mp4",
    "audio": "audio/mpeg",
    "animation": "video/mp4",
}


def _sanitize_filename(name: str) -> str:
    """Basename-only naming for a received file (mirror of the
    web-upload ``_sanitize_upload_name``): separators stripped, no
    traversal, control chars dropped. Empty → ""."""
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = name.replace("\x00", "").strip().strip(".")
    return name


def _extract_file_payload(msg: dict) -> dict | None:
    """Normalize the FIRST file payload of a Telegram ``message``.

    Returns ``{kind, file_id, file_name, mime_type, file_size,
    caption}`` or ``None`` when the message carries no file (plain
    text / location / contact / sticker / poll ...). ``photo`` picks
    the LARGEST size entry (Telegram sends a size ladder).
    """
    caption = str(msg.get("caption") or "").strip()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    for key, kind in _FILE_KEYS:
        node = msg.get(key) or None
        if isinstance(node, dict) and node.get("file_id"):
            name = _sanitize_filename(str(node.get("file_name") or ""))
            if not name:
                stem, ext = _DEFAULT_NAMES[kind]
                name = f"{stem}-{ts}{ext}"
            return {
                "kind": kind,
                "file_id": str(node.get("file_id") or ""),
                "file_name": name,
                "mime_type": str(
                    node.get("mime_type") or _DEFAULT_MIMES[kind] or ""
                ),
                "file_size": int(node.get("file_size") or 0),
                "caption": caption,
            }
    photo_sizes = msg.get("photo")
    if isinstance(photo_sizes, list) and photo_sizes:
        cands = [
            s for s in photo_sizes
            if isinstance(s, dict) and s.get("file_id")
        ]
        if cands:
            best = max(
                cands,
                key=lambda s: (
                    int(s.get("width") or 0) * int(s.get("height") or 0)
                ),
            )
            return {
                "kind": "photo",
                "file_id": str(best.get("file_id") or ""),
                "file_name": f"photo-{ts}.jpg",
                "mime_type": "image/jpeg",
                "file_size": int(best.get("file_size") or 0),
                "caption": caption,
            }
    return None


def _voice_reply_enabled() -> bool:
    """v0.9.59.0 — ``QDRANT_TELEGRAM_VOICE_REPLY`` (default on): when
    off, voice-in messages are STILL transcribed and answered, but the
    reply is TEXT-only (the synthesis/voice-out stage is skipped)."""
    v = (os.environ.get("QDRANT_TELEGRAM_VOICE_REPLY", "on") or "on"
         ).strip().lower()
    return v not in ("off", "0", "no", "false")


_TEXT_ONLY_RE = re.compile(
    r"不要語音|改用文字|用文字回覆|文字就好|no voice|text reply|"
    r"disable voice",
    re.IGNORECASE,
)


def _text_only_requested(text: str) -> bool:
    """Per-turn voice opt-out — the USER can say 「改用文字回覆」 in the
    voice transcript and this turn is answered text-only."""
    return bool(_TEXT_ONLY_RE.search(text or ""))


_VOICE_REPLY_LEAD_RE = re.compile(
    r"^(好的|當然|沒問題|了解|收到|ok|sure)\s*[，,、：:]?\s*",
    re.IGNORECASE,
)
_VOICE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _strip_md_for_voice(text: str) -> str:
    """Markdown → plain spoken prose for the voice summary.

    Code fences are dropped entirely (code is unreadable when spoken),
    headings/bullets/numbering markers are stripped, tables and
    blockquotes are skipped, links keep their label, bold/italics are
    unstyled, images are removed."""
    lines: list[str] = []
    in_fence = False
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not s or s.startswith(("|", ">")):
            continue
        s = _VOICE_IMAGE_RE.sub(" ", s)
        s = re.sub(r"^#{1,6}\s*", "", s)          # headings
        s = re.sub(r"^[-*•]\s+", "", s)           # bullets
        s = re.sub(r"^\d+[.)]\s+", "", s)         # numbered items
        s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)  # links → label
        s = s.replace("**", "").replace("__", "")
        s = s.strip()
        if s:
            lines.append(s)
    return " ".join(lines)


def _voice_cut(s: str, budget: int) -> str:
    """Hard cut at the best clause boundary ≥ half the budget."""
    cut = s[:budget]
    for sep in ("，", "、", "：", "；", ",", ";"):
        idx = cut.rfind(sep)
        if idx >= budget // 2:
            return cut[:idx].rstrip("，、：,；; ") + "…"
    return cut.rstrip("，、：,；; ") + "…"


def _condense_for_voice(text: str, max_chars: int | None = None) -> str:
    """Broadcast-news-style spoken summary of the FULL answer (v0.9.60.1).

    User feedback on v0.9.59.0: the "lead your answer with a spoken
    summary paragraph" prompt rule made models write a title-only lead,
    and the voice note read JUST that title. The spoken version is now
    DETERMINISTIC — the full text reply is markdown-stripped, split
    into sentences, and greedily packed into
    ``QDRANT_TELEGRAM_VOICE_MAX_CHARS`` (default 50, clamped 20-120):
    sentence boundaries preferred, then clause boundaries, then a hard
    cut + 「…」. The FULL answer still goes out as text."""
    if max_chars is None:
        try:
            max_chars = int(os.environ.get(
                "QDRANT_TELEGRAM_VOICE_MAX_CHARS", "50") or 50)
        except (TypeError, ValueError):
            max_chars = 50
    try:
        budget = max(20, min(int(max_chars), 120))
    except (TypeError, ValueError):
        budget = 50
    raw = (text or "").strip()
    if not raw:
        return ""
    plain = _strip_md_for_voice(raw) or raw
    # strip a polite filler lead-in — but ONLY when what remains is
    # still substantial (「好的。」 must not be stripped down to 「。」)
    stripped = _VOICE_REPLY_LEAD_RE.sub("", plain).strip()
    if len(stripped) >= 6:
        plain = stripped
    if len(plain) <= budget:
        return plain
    # sentence split on CJK/latin terminals AND line breaks
    parts = [
        p.strip()
        for p in re.split(r"(?<=[。！？!?；;])\s*|\n+", plain)
        if p.strip()
    ]
    out = ""
    for p in parts:
        cand = (out + p) if out else p
        if len(cand) <= budget:
            out = cand
            continue
        break
    if not out:
        # the first sentence alone exceeds the budget → clause cut
        out = _voice_cut(parts[0] if parts else plain, budget)
    return out


def _needs_photo_hallucination_retry(
    request_text: str,
    answer_text: str,
    dispatched_tools: list[str] | None,
) -> bool:
    """True when the turn LOOKS like a fabricated camera-photo delivery.

    All three must hold (high precision — a false positive costs one
    extra LLM turn that will just do the work properly):
      * the request is camera/photo-ish,
      * the reply CLAIMS a photo was delivered (訊息 #N / 已發送 / …),
      * NO send_telegram_photo / onvif_* tool was dispatched this turn.
    """
    if not _CAMERA_INTENT_RE.search(request_text or ""):
        return False
    if not _PHOTO_CLAIM_RE.search(answer_text or ""):
        return False
    for name in dispatched_tools or []:
        if _PHOTO_GUARD_TOOL_RE.search(name):
            return False
    return True

DEFAULT_POLL_INTERVAL = 2.0
MIN_POLL_INTERVAL = 1.0
MAX_POLL_INTERVAL = 30.0


def _poll_interval() -> float:
    raw = (os.environ.get("QDRANT_TELEGRAM_BOT_POLL_INTERVAL", "")
           or "").strip()
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_POLL_INTERVAL
    return min(max(v, MIN_POLL_INTERVAL), MAX_POLL_INTERVAL)


def _polling_enabled() -> bool:
    mode = (os.environ.get("QDRANT_TELEGRAM_BOT_POLLING", "on")
            or "on").strip().lower()
    return mode not in ("off", "0", "no", "false")


def _load_allowed_ids() -> list[int]:
    """Parse ``TELEGRAM_ALLOWED_CHAT_IDS`` (fail-closed allowlist)."""
    raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return []
    out: list[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            continue
    return out


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TelegramBotClient:
    """Thin HTTP client over the Telegram Bot API (stdlib urllib)."""

    def __init__(self, token: str = ""):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")

    # -- low-level HTTP ------------------------------------------------

    def _call(self, method: str, payload: dict | None = None,
              timeout: float = HTTP_TIMEOUT_S) -> Any:
        """POST ``{"token": <bot token>, ...}``-style request. Returns
        the parsed JSON ``result`` field; ``None`` on Telegram-ok-but-
        empty. Raises on network errors (callers decide retry)."""
        if not self.token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN 未設定 — bot 無法運作"
            )
        url = f"{TELEGRAM_API_BASE}/bot{self.token}/{method}"
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(
                f"Telegram API {method} failed: {body!r}",
            )
        return body.get("result")

    # -- Bot API wrappers ------------------------------------------------

    def get_updates(self, offset: int, wait: float = LONG_POLL_TIMEOUT_S,
                    ) -> list[dict]:
        """Long-poll for updates newer than ``offset``."""
        result = self._call(
            "getUpdates",
            {"offset": offset, "timeout": int(wait),
             "allowed_updates": ["message"]},
            timeout=wait + HTTP_TIMEOUT_S,
        )
        return list(result or [])

    def send_message(self, chat_id: int, text: str) -> Any:
        """Send one plain-text message (chunked at the 4096 limit)."""
        text = str(text or "").strip() or "(empty reply)"
        result = None
        for chunk_start in range(0, len(text), MAX_MESSAGE_LEN):
            chunk = text[chunk_start:chunk_start + MAX_MESSAGE_LEN]
            result = self._call("sendMessage", {
                "chat_id": chat_id, "text": chunk,
            })
        return result

    def send_markdown(self, chat_id: int, text: str) -> Any:
        """v0.9.67.0 — send one STRICT MarkdownV2 message (already
        converted by src/telegram_md; chunked at the 4096 limit). A
        per-chunk parse failure (Telegram "can't parse entities" 400)
        degrades THAT chunk to plain text so the message is never
        lost to a parser quirk."""
        text = str(text or "").strip() or "(empty reply)"
        result = None
        for chunk_start in range(0, len(text), MAX_MESSAGE_LEN):
            chunk = text[chunk_start:chunk_start + MAX_MESSAGE_LEN]
            try:
                result = self._call("sendMessage", {
                    "chat_id": chat_id, "text": chunk,
                    "parse_mode": "MarkdownV2",
                })
            except RuntimeError as e:
                if "400" in str(e):
                    logger.warning(
                        "telegram-bot: MarkdownV2 chunk rejected "
                        "(chat=%s): %r — resending as plain text",
                        chat_id, e,
                    )
                    result = self._call("sendMessage", {
                        "chat_id": chat_id, "text": chunk,
                    })
                else:
                    raise
        return result

    # -- voice (v0.9.58.0) ----------------------------------------------

    def get_file(self, file_id: str) -> str:
        """Bot API ``getFile`` — returns the server-relative
        ``file_path`` the download URL is built from."""
        result = self._call("getFile", {"file_id": file_id})
        path = str((result or {}).get("file_path") or "")
        if not path:
            raise RuntimeError(f"getFile returned no file_path: {result!r}")
        return path

    def download_file(self, file_path: str) -> bytes:
        """Download a Bot-API file
        (``https://api.telegram.org/file/bot<token>/<file_path>``)."""
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN 未設定")
        url = f"{TELEGRAM_API_BASE}/file/bot{self.token}/{file_path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return resp.read()

    def _call_multipart(
        self, method: str, fields: dict[str, str],
        file_field: str, filename: str, content_type: str,
        data: bytes, timeout: float = 90.0,
    ) -> Any:
        """Multipart POST wrapper (same boundary shape as the outbound
        telegram server) — used by ``send_voice``."""
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN 未設定")
        boundary = secrets.token_hex(16)
        parts: list[bytes] = []
        for key, value in (fields or {}).items():
            parts.append(
                (f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="{key}"\r\n'
                 f"\r\n{value}\r\n").encode("utf-8")
            )
        parts.append(
            (f"--{boundary}\r\n"
             f'Content-Disposition: form-data; name="{file_field}"; '
             f'filename="{filename}"\r\n'
             f"Content-Type: {content_type}\r\n\r\n").encode("utf-8")
        )
        parts.append(data)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        url = f"{TELEGRAM_API_BASE}/bot{self.token}/{method}"
        req = urllib.request.Request(
            url, data=b"".join(parts), method="POST",
            headers={
                "Content-Type":
                    f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API {method} failed: {body!r}")
        return body.get("result")

    def send_voice(
        self, chat_id: int, audio: bytes,
        filename: str = "reply.ogg",
    ) -> Any:
        """Send a voice note (OGG/Opus payload expected)."""
        return self._call_multipart(
            "sendVoice", {"chat_id": str(chat_id)},
            "voice", filename, "audio/ogg", audio,
        )


def make_telegram_on_message(
    agent_factory: Callable[[], Any],
) -> Callable[[int, str], str]:
    """Build the ``handle(chat_id, text) -> reply`` callback that runs
    the user's message through ``Agent.chat_turn`` (full tool surface)
    and returns the answer text for the daemon to deliver.

    Mirrors ``scheduler.make_agent_on_fire``: each message builds a
    fresh Agent (no shared OpenAI client state), the conversation is
    persisted in a dedicated per-chat session ``tgbot-<chat_id>``
    (multi-turn context), and chat_turn failures degrade to a short
    error reply instead of tearing the bot down.
    """
    from src.agent.session import Session, SessionManager

    def handle(chat_id: int, text: str) -> str:
        agent = agent_factory()
        sm = SessionManager()
        sid = f"tgbot-{chat_id}"
        # ── v0.9.102.0 — in-chat session clear (/new etc.) ─────────
        stripped = (text or "").strip()
        if stripped.lower() in _SESSION_CLEAR_COMMANDS or (
                stripped in _SESSION_CLEAR_COMMANDS):
            sess = Session(
                sid=sid, title=f"[telegram bot] chat {chat_id}",
            )
            try:
                sm.save(sess)
            except Exception as e:
                logger.warning(
                    "telegram-bot: session clear-save(%s) failed: %r",
                    sid, e,
                )
            logger.info(
                "telegram-bot: session cleared via in-chat command "
                "(chat=%s)", chat_id,
            )
            return _SESSION_CLEAR_REPLY
        sess = sm.load(sid)
        if sess is None:
            sess = Session(
                sid=sid, title=f"[telegram bot] chat {chat_id}",
            )
        # Frame the inbound message + anti-double-send note.
        framed = (
            f"[Telegram message from chat {chat_id}]\n{text}\n\n"
            "(你的回覆會由 Telegram bot 自動送回該對話 — "
            "不要自行呼叫 send_telegram_message；若需要送出額外的"
            "主動訊息才使用該工具。)"
        )
        sess.messages.append({"role": "user", "content": framed,
                              "ts": _now()})
        # v0.9.102.0 — context discipline: feed ONLY the last N
        # messages (session file keeps everything; ≤0/-1 = legacy full).
        cap = _telegram_history_cap()
        history = (
            sess.messages[:-1] if cap < 0
            else sess.messages[:-1][-cap:]
        )
        # v0.9.45.0 Phase 2: register the turn in the process-wide
        # task registry (list_tasks / get_task_result visibility).
        from src.agent.task_runner import (
            STOPPED, begin_task, end_task, stop_requested as _sr,
        )
        _tr = begin_task("telegram", text[:60], sid=sess.id)
        # v0.9.120.0 — cooperative force-stop: the registry stop flag
        # is checked at every tool-loop iteration boundary — it ends
        # in-flight turns AND zombie turns already abandoned by the
        # timeout guards (they share the same loop).
        _stopped = {"flag": False}

        def _stop_check() -> bool:
            try:
                if _sr(_tr):
                    _stopped["flag"] = True
                    return True
            except Exception:
                pass
            return False

        try:
            _setter = getattr(agent, "set_stop_check", None)
            if callable(_setter):
                _setter(_stop_check)
            else:
                setattr(agent, "_stop_check", _stop_check)
        except Exception:
            pass
        try:
            result, turn_timed_out = _chat_turn_guarded(
                agent, _turn_hard_timeout_s(),
                question=framed,
                history=history,
                use_tools=True,
            )
        except Exception as chat_exc:
            try:
                end_task(
                    _tr, "failed", error=repr(chat_exc)[:500],
                )
            except Exception:
                pass
            logger.exception(
                "telegram-bot: chat_turn(chat=%s) failed: %r",
                chat_id, chat_exc,
            )
            sess.messages.append({
                "role": "assistant",
                "content": f"[error] {chat_exc!r}",
                "ts": _now(), "sources": [], "error": True,
            })
            sess.updated_at = _now()
            try:
                sm.save(sess)
            except Exception:
                pass
            return f"[內部錯誤] 處理訊息時發生問題：{chat_exc!r}"
        if turn_timed_out:
            try:
                end_task(_tr, "failed",
                         error=f"turn hard timeout after "
                               f"{_turn_hard_timeout_s()}s")
            except Exception:
                pass
            sess.messages.append({
                "role": "assistant",
                "content": _TURN_TIMEOUT_REPLY.format(
                    secs=_turn_hard_timeout_s()),
                "ts": _now(), "sources": [], "error": True,
                "turn_timed_out": True,
            })
            sess.updated_at = _now()
            try:
                sm.save(sess)
            except Exception:
                pass
            return _TURN_TIMEOUT_REPLY.format(secs=_turn_hard_timeout_s())
        if _stopped["flag"] or _sr(_tr):
            try:
                end_task(_tr, STOPPED)
            except Exception:
                pass
            sess.messages.append({
                "role": "assistant",
                "content": _TURN_STOPPED_REPLY,
                "ts": _now(), "sources": [], "turn_stopped": True,
            })
            sess.updated_at = _now()
            try:
                sm.save(sess)
            except Exception:
                pass
            return _TURN_STOPPED_REPLY
        # Tuple/dict dual contract (v0.9.23.0 lesson).
        if isinstance(result, dict):
            answer = result.get("answer")
        elif isinstance(result, tuple) and len(result) >= 1:
            answer = result[0]
        else:
            answer = None
        answer_text = str(answer or "").strip() or (
            "(無回覆 — 模型未產生內容)"
        )
        # ── v0.9.57.1 photo-hallucination guard ───────────────────
        # Deterministic detection of the fabricated camera-photo reply
        # (claim of delivery with ZERO photo/snapshot tools dispatched
        # this turn). One corrective re-prompt; the retry's answer is
        # delivered instead. Original answer stays in the session for
        # audit; a retry failure degrades to the original reply.
        dispatches = list(
            getattr(agent, "last_tool_dispatches", []) or [],
        )
        if _needs_photo_hallucination_retry(text, answer_text, dispatches):
            logger.warning(
                "telegram-bot: photo-hallucination guard fired "
                "(chat=%s, dispatched=%r, answer=%.120r) — "
                "corrective re-prompt", chat_id, dispatches, answer_text,
            )
            sess.messages.append({
                "role": "assistant",
                "content": answer_text,
                "ts": _now(), "sources": [],
                "hallucination_guarded": True,
            })
            sess.messages.append({
                "role": "user", "content": _PHOTO_RETRY_PROMPT,
                "ts": _now(),
            })
            try:
                retry, retry_timed_out = _chat_turn_guarded(
                    agent, _turn_hard_timeout_s(),
                    question=_PHOTO_RETRY_PROMPT,
                    history=history,
                    use_tools=True,
                )
                if retry_timed_out:
                    retry = None
                if isinstance(retry, dict):
                    retry_answer = retry.get("answer")
                elif isinstance(retry, tuple) and len(retry) >= 1:
                    retry_answer = retry[0]
                else:
                    retry_answer = None
                retry_text = str(retry_answer or "").strip()
                if retry_text:
                    answer_text = retry_text
            except Exception as retry_exc:
                logger.warning(
                    "telegram-bot: corrective retry failed (chat=%s): "
                    "%r — delivering the original reply",
                    chat_id, retry_exc,
                )
        # v0.9.45.0 Phase 2: stamp the registry — turn finished.
        try:
            end_task(_tr, "done", answer_chars=len(answer_text))
        except Exception:
            pass
        sess.messages.append({
            "role": "assistant",
            "content": answer_text,
            "ts": _now(), "sources": [],
            "telegram_chat_id": chat_id,
        })
        sess.updated_at = _now()
        # v0.9.102.0 — stored-session ring trim: keep the NEWEST
        # TELEGRAM_SESSION_MAX_MESSAGES (default 200) so the session
        # file cannot grow unbounded; -1 = keep everything (legacy).
        keep = _telegram_session_max_messages()
        if keep >= 0 and len(sess.messages) > keep:
            sess.messages = sess.messages[-keep:]
        try:
            sm.save(sess)
        except Exception as e:
            logger.warning(
                "telegram-bot: session save(%s) failed: %r", sid, e,
            )
        return answer_text

    return handle


def _extract_text(update: dict) -> str:
    """Pull the text out of a Telegram ``message`` update (edits and
    non-text messages are ignored by the allowed_updates filter)."""
    msg = update.get("message") or {}
    return str(msg.get("text") or "").strip()


# ─── Order-approval registry (v0.9.28.0 — stock live-order gate) ──
#
# The stock MCP's live order path (v0.9.26.0 wiring) requires an
# explicit Telegram consent BEFORE SendNewOrderEx fires:
#   1. the engine calls ``request_order_approval(approval_chat_id,
#      summary)`` — the request message carries a unique code
#      (``STK-XXXX``) and the accepted reply formats;
#   2. the approval chat (default: the first
#      TELEGRAM_ALLOWED_CHAT_IDS entry) replies — the BOT DAEMON (the
#      single getUpdates consumer) resolves the pending approval
#      BEFORE the normal chat flow consumes the message;
#   3. the engine's blocking wait returns ``approved`` / ``denied`` /
#      ``timeout`` and only an approval lets SendNewOrderEx proceed.
#
# The bot daemon must be running (QDRANT_TELEGRAM_BOT_POLLING=on) —
# it is the only consumer of getUpdates; ``_last_poll_ts`` is the
# liveness signal checked before waiting.


@dataclass
class _PendingApproval:
    code: str
    chat_id: int
    summary: str = ""
    event: threading.Event = field(default_factory=threading.Event)
    outcome: str = ""  # approved | denied


_APPROVALS: dict[str, dict[str, Any]] = {}
_APPROVAL_LOCK = threading.Lock()
_last_poll_ts: float | None = None  # heartbeat updated by poll_once
_STALE_POLL_S = 30.0                # daemon considered dead after this

_APPROVAL_WORDS = ("同意", "確認", "核准", "ok", "yes", "y", "approve")
_REJECT_WORDS = ("拒", "不同意", "取消", "reject", "deny", "cancel",
                 "no", "n")


def request_order_approval(
    chat_id: int,
    summary: str,
    timeout_s: float = 180.0,
    client: "TelegramBotClient | None" = None,
) -> str:
    """Stock live-order Telegram consent gate (blocks until the
    approval chat replies).

    Sends the confirmation request (unique ``STK-XXXX`` code) to
    ``chat_id`` and waits for the reply. Resolution — via the bot
    daemon (the single getUpdates consumer):

    * contains the code + an approval word (同意/確認/approve/…) OR
      is the bare code → ``approved``
    * contains the code + a reject word (拒/取消/…) → ``denied``
      (rejection beats approval when both appear)
    * code-less explicit rejection (拒/拒絕/取消 alone) while a
      pending request exists for this chat → ``denied``
    * anything else → NOT consumed (normal chat flow keeps running;
      the wait simply continues until the timeout)

    Returns ``approved`` | ``denied`` | ``timeout``. Raises
    RuntimeError when the bot daemon is not running (nobody is
    consuming getUpdates, so a reply could never arrive).
    """
    global _last_poll_ts
    if _last_poll_ts is None or (
        time.time() - _last_poll_ts > _STALE_POLL_S
    ):
        raise RuntimeError(
            "Telegram bot 輪詢未運行 — 無法接收同意回覆。請確認 "
            "QDRANT_TELEGRAM_BOT_POLLING=on（GUI/web 開機會自動恢復；"
            "token 與 TELEGRAM_ALLOWED_CHAT_IDS 需已設定）。"
        )
    client = client or TelegramBotClient()
    code = "STK-" + secrets.token_hex(2).upper()
    request_text = (
        f"⚠️ 股票下單確認請求（#{code}）\n{summary}\n\n"
        f"回覆「同意 {code}」核准（或僅回覆 {code}）；"
        f"回覆「拒絕 {code}」取消。\n"
        f"{int(timeout_s)} 秒內有效，逾期自動取消。"
    )
    entry = _PendingApproval(
        code=code, chat_id=chat_id, summary=summary,
    )
    # Register BEFORE sending: a fast reply could arrive while the
    # request text is still being delivered — the bot daemon must
    # find the pending entry already.
    with _APPROVAL_LOCK:
        _APPROVALS[code] = entry
    try:
        client.send_message(chat_id, request_text)
        if entry.event.wait(max(5.0, float(timeout_s))):
            return entry.outcome or "denied"
        return "timeout"
    finally:
        with _APPROVAL_LOCK:
            _APPROVALS.pop(code, None)


def _try_resolve_approval(chat_id: int, text: str) -> bool:
    """Called by the bot daemon BEFORE the normal chat flow: consume
    the incoming message if it resolves a pending approval for this
    chat. Returns True when the message was consumed."""
    lower = text.strip().lower()
    with _APPROVAL_LOCK:
        pending = [a for a in _APPROVALS.values()
                   if a.chat_id == chat_id]
    if not pending:
        return False
    for a in pending:
        if a.code not in text:
            continue
        if any(r in lower for r in _REJECT_WORDS):
            a.outcome = "denied"
        else:
            # approval word OR bare exact code echo → approved
            if (
                any(w in lower for w in _APPROVAL_WORDS)
                or text.strip().replace(" ", "") == a.code
            ):
                a.outcome = "approved"
            else:
                # code present but ambiguous → deny (money safety)
                a.outcome = "denied"
        a.event.set()
        return True
    # code-less rejection for the single pending request
    if len(pending) == 1 and len(lower) <= 8 and any(
        r in lower for r in _REJECT_WORDS
    ):
        pending[0].outcome = "denied"
        pending[0].event.set()
        return True
    return False


class TelegramBotDaemon:
    """Daemon thread long-polling Telegram ``getUpdates``.

    Feed: every whitelisted chat's TEXT message goes through
    ``on_message(chat_id, text) -> reply`` and the reply is delivered
    back with ``sendMessage``; VOICE notes take the STT pipeline and
    FILES (document / photo / video / audio, v0.9.63.0+) are
    downloaded into the filesystem WRITE sandbox then framed for the
    same LLM turn. Non-whitelisted chats are silently ignored
    (fail-closed allowlist).

    The daemon owns the update ``offset`` — acknowledged on the NEXT
    poll, so a crash between handling and acking re-delivers at most
    one batch (at-least-once; the reply send is idempotent-ish for a
    chat assistant).
    """

    def __init__(
        self,
        client: "TelegramBotClient | None" = None,
        on_message: "Callable[[int, str], str] | None" = None,
        poll_interval: float | None = None,
        speech=None,
    ):
        self.client = client or TelegramBotClient()
        self.on_message = on_message
        self.poll_interval = float(poll_interval or _poll_interval())
        # v0.9.58.0 — STT/TTS (OpenRouter audio endpoints by default).
        # SpeechClient.from_env() never raises; unconfigured features
        # degrade to text via the configured() flags.
        if speech is None:
            from src.agent.speech import SpeechClient
            speech = SpeechClient.from_env()
        self.speech = speech
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle (mirror Scheduler) --------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="telegram-bot-poll", daemon=True,
        )
        self._thread.start()
        logger.info(
            "telegram-bot: started (poll_interval=%.1fs, "
            "long_poll=%.0fs, allowed=%d chat(s))",
            self.poll_interval, LONG_POLL_TIMEOUT_S,
            len(_load_allowed_ids()),
        )

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("telegram-bot: stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:
                logger.warning(
                    "telegram-bot: poll error (will retry): %r", e,
                )
                # Back off after errors so a broken token does not
                # hammer the API (10 s).
                self._sleep(self.poll_interval * 5)
                continue
            self._sleep(self.poll_interval)

    def _sleep(self, seconds: float) -> None:
        # Sleep in small chunks so stop() stays responsive.
        for _ in range(max(1, int(seconds / 0.1))):
            if self._stop.is_set():
                return
            time.sleep(0.1)

    # -- one poll cycle ------------------------------------------------

    def poll_once(self) -> int:
        """One getUpdates batch: return the number of messages
        processed. Errors propagate (the loop catches)."""
        global _last_poll_ts
        _last_poll_ts = time.time()  # approval liveness heartbeat
        updates = self.client.get_updates(
            self._offset, wait=LONG_POLL_TIMEOUT_S,
        )
        handled = 0
        for upd in updates:
            upd_id = int(upd.get("update_id") or 0)
            if upd_id:
                self._offset = max(self._offset, upd_id + 1)
            try:
                self._handle_update(upd)
                handled += 1
            except Exception as e:
                logger.exception(
                    "telegram-bot: handle update failed: %r", e,
                )
        return handled

    def _handle_update(self, upd: dict) -> None:
        msg = upd.get("message") or {}
        chat_id = int((msg.get("chat") or {}).get("id") or 0)
        if not chat_id:
            return
        allowed = _load_allowed_ids()
        # Fail-closed: an EMPTY allowlist means nobody may talk to the
        # bot (same model as the outbound server's empty-allowlist
        # rejection).
        if chat_id not in allowed:
            logger.debug(
                "telegram-bot: chat %s not in allowlist — ignored",
                chat_id,
            )
            return
        # v0.9.58.0 — voice notes take the STT → LLM → voice-out path.
        voice = msg.get("voice") or None
        if voice:
            self._handle_voice(chat_id, voice)
            return
        # v0.9.63.0 — inbound files (document / photo / video / audio):
        # getFile → download → filesystem WRITE sandbox → framed
        # chat_turn. Previously these updates fell through the
        # "no text" return below and were SILENTLY ignored (no log,
        # no reply — user report: 「tg上傳的檔案沒有回應」).
        payload = _extract_file_payload(msg)
        if payload:
            self._handle_file_message(chat_id, payload)
            return
        text = _extract_text(upd)
        if not text:
            return  # non-text / empty message
        logger.info(
            "telegram-bot: message from chat %s (%d chars)",
            chat_id, len(text),
        )
        # v0.9.28.0: stock order-approval resolution — the bot daemon
        # is the single getUpdates consumer, so consent replies MUST
        # be intercepted here before the normal chat flow would turn
        # them into a regular LLM conversation turn.
        try:
            if _try_resolve_approval(chat_id, text):
                logger.info(
                    "telegram-bot: approval reply consumed "
                    "(chat=%s, %d pending resolved)",
                    chat_id, 1,
                )
                return
        except Exception as e:
            logger.exception(
                "telegram-bot: approval resolve failed: %r", e,
            )
        if self.on_message is None:
            reply = "(bot 處理器未接線 — on_message 未設定)"
        else:
            reply = self.on_message(chat_id, text)
        self._send_text(chat_id, reply)

    # -- voice pipeline (v0.9.58.0) -----------------------------------

    def _handle_voice(self, chat_id: int, voice_info: dict) -> None:
        """Inbound voice note: getFile → download → STT → chat_turn →
        TTS → sendVoice (any failure degrades to a text reply)."""
        file_id = str(voice_info.get("file_id") or "")
        duration = float(voice_info.get("duration") or 0)
        logger.info(
            "telegram-bot: voice from chat %s (%.0fs)",
            chat_id, duration,
        )
        try:
            transcript = self._transcribe_voice(file_id)
        except Exception as e:
            logger.warning(
                "telegram-bot: voice transcribe failed (chat=%s): %r",
                chat_id, e,
            )
            self._send_text(
                chat_id,
                "⚠️ 無法轉寫這則語音訊息（STT 未設定或失敗）—\n"
                f"{e}",
            )
            return
        if not transcript:
            self._send_text(chat_id, "⚠️ 語音內容為空（無法轉寫）")
            return
        # Approval codes can arrive by voice too (同意 STK-XXXX).
        try:
            if _try_resolve_approval(chat_id, transcript):
                logger.info(
                    "telegram-bot: approval VOICE reply consumed "
                    "(chat=%s)", chat_id,
                )
                return
        except Exception as e:
            logger.exception(
                "telegram-bot: approval resolve failed: %r", e,
            )
        if self.on_message is None:
            reply = "(bot 處理器未接線 — on_message 未設定)"
        else:
            reply = self.on_message(
                chat_id, f"[語音訊息（已轉文字）]\n{transcript}",
            )
        # v0.9.59.0 — per-turn voice opt-out: the transcript itself
        # can ask for a text-only answer.
        self._deliver_reply(
            chat_id, reply,
            voice_in=True and not _text_only_requested(transcript),
        )

    def _transcribe_voice(self, file_id: str) -> str:
        """getFile → download → SpeechClient.transcribe."""
        file_path = self.client.get_file(file_id)
        audio = self.client.download_file(file_path)
        filename = (file_path.split("/") or [file_path])[-1] or "voice.ogg"
        return self.speech.transcribe(audio, filename=filename)

    # -- inbound file pipeline (v0.9.63.0) ------------------------------

    def _inbound_write_root(self) -> Path:
        """Resolve the filesystem WRITE root (env → WATCH_DIR →
        ./documents), mirroring the filesystem MCP root resolution
        WITHOUT importing it (the daemon stays dependency-light;
        relative paths anchor at the project root like host.py)."""
        raw = (os.environ.get("QDRANT_FILESYSTEM_WRITE_ROOT", "")
               or os.environ.get("WATCH_DIR") or "documents").strip()
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (Path(__file__).resolve().parents[2] / raw).resolve()
        else:
            p = p.resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _save_inbound_file(
        self, chat_id: int, payload: dict,
    ) -> tuple[str, str]:
        """getFile → download → save under the filesystem WRITE
        sandbox (``uploads/telegram/<chat_id>/``).

        Returns ``(saved_abs_posix_path, "")`` on success or
        ``("", error_message)`` on failure (executable extension,
        oversize, download error). All refusals happen BEFORE or
        AROUND the download — nothing dangerous is ever written.
        """
        if not payload.get("file_id"):
            return "", "訊息中沒有可下載的 file_id"
        size = int(payload.get("file_size") or 0)
        cap_mb = TELEGRAM_FILE_MAX_BYTES // (1024 * 1024)
        if size > TELEGRAM_FILE_MAX_BYTES:
            return "", (
                f"檔案 {size // (1024 * 1024)} MB 超過 Telegram bot "
                f"下載上限 {cap_mb} MB"
            )
        ext = Path(str(payload.get("file_name") or "")).suffix.lower()
        if ext in _TELEGRAM_INBOUND_REFUSED_EXTS:
            return "", (
                f"拒收可執行檔（{ext}）— 請改以 .zip 壓縮後再上傳"
            )
        try:
            file_path = self.client.get_file(payload["file_id"])
            data = self.client.download_file(file_path)
        except Exception as e:
            return "", f"Telegram 檔案下載失敗：{e!r}"
        if len(data) > TELEGRAM_FILE_MAX_BYTES:
            return "", (
                f"檔案 {len(data) // (1024 * 1024)} MB 超過 Telegram "
                f"bot 下載上限 {cap_mb} MB"
            )
        root = self._inbound_write_root()
        dest_dir = root / _TELEGRAM_DIR / str(chat_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / str(payload.get("file_name") or "file.bin")
        stem, suffix = target.stem, target.suffix
        n = 0
        while target.exists():
            n += 1
            target = dest_dir / f"{stem}-{n}{suffix}"
        target.write_bytes(data)
        logger.info(
            "telegram-bot: file saved %s (%d bytes)",
            target.as_posix(), len(data),
        )
        return target.as_posix(), ""

    def _handle_file_message(self, chat_id: int, payload: dict) -> None:
        """v0.9.63.0 — inbound file: getFile → download → filesystem
        WRITE sandbox → framed chat_turn. Any failure degrades to a
        clear ⚠️ text reply (the file is never silently dropped)."""
        kind = str(payload.get("kind") or "file")
        logger.info(
            "telegram-bot: file from chat %s (kind=%s, name=%r, "
            "mime=%r, size=%s bytes)",
            chat_id, kind, payload.get("file_name"),
            payload.get("mime_type") or "unknown",
            payload.get("file_size") or "?",
        )
        saved, error = self._save_inbound_file(chat_id, payload)
        if error:
            logger.warning(
                "telegram-bot: file receive failed (chat=%s, %r): %s",
                chat_id, payload.get("file_name"), error,
            )
            self._send_text(chat_id, f"⚠️ 檔案接收失敗：{error}")
            return
        caption = str(payload.get("caption") or "").strip()
        size = int(payload.get("file_size") or 0)
        hint = (
            "處理提示: 圖片檔 — 可用 describe_image 工具辨識內容。"
            if kind == "photo"
            else "處理提示: 可用 read_file（或相關 MCP 工具）讀取後處理。"
        )
        framed = (
            "[Telegram 檔案上傳]\n"
            f"類型: {_INBOUND_KIND_LABELS.get(kind, kind)}\n"
            f"檔名: {payload.get('file_name')}\n"
            f"MIME: {payload.get('mime_type') or '未知'}\n"
            f"大小: {size:,} bytes\n"
            f"已存到: {saved}\n"
            + (f"使用者附註: {caption}\n" if caption else "")
            + hint
        )
        if self.on_message is None:
            reply = "(bot 處理器未接線 — on_message 未設定)"
        else:
            reply = self.on_message(chat_id, framed)
        self._send_text(chat_id, reply)

    def _deliver_reply(
        self, chat_id: int, reply: str, voice_in: bool = False,
    ) -> None:
        """v0.9.59.0 — voice-in gets BOTH channels: the VOICE note
        carries a SHORT spoken version (prompt-led summary paragraph
        or a deterministic condensation) and the TEXT carries the
        FULL answer. Any voice failure degrades to text (the reply is
        never lost). ``QDRANT_TELEGRAM_VOICE_REPLY=off`` (or a
        per-turn 「改用文字回覆」 transcript) → text only."""
        voice_out = (
            voice_in
            and self.speech is not None
            and self.speech.tts_configured()
            and _voice_reply_enabled()
        )
        if voice_out:
            try:
                spoken = _condense_for_voice(reply)
                audio, fmt = self.speech.synthesize(spoken or reply)
                if fmt != "ogg":
                    audio = self.speech.to_voice_ogg(audio)
                self.client.send_voice(chat_id, audio)
            except Exception as e:
                logger.warning(
                    "telegram-bot: voice reply failed (chat=%s): %r — "
                    "falling back to text", chat_id, e,
                )
        # v0.9.67.0 — the agent reply is markdown; deliver it as
        # Telegram MarkdownV2 (converted via src/telegram_md) with a
        # plain-text fallback (per-chunk parse failure inside
        # send_markdown + a full fallback here for clients that don't
        # implement send_markdown). Error notices keep plain text.
        if self._send_reply_md(chat_id, reply):
            return
        self._send_text(chat_id, reply)

    def _send_reply_md(self, chat_id: int, reply: str) -> bool:
        """Try the MarkdownV2 path for one agent reply. Returns True
        when the reply went out as MarkdownV2; False when the caller
        should fall back to plain ``_send_text``."""
        from src.telegram_md import markdown_to_telegram_v2

        send_md = getattr(self.client, "send_markdown", None)
        if not callable(send_md):
            return False
        try:
            send_md(chat_id, markdown_to_telegram_v2(reply))
            return True
        except Exception as e:
            logger.warning(
                "telegram-bot: markdown reply failed (chat=%s): %r — "
                "falling back to plain text", chat_id, e,
            )
            return False

    def _send_text(self, chat_id: int, reply: str) -> None:
        try:
            self.client.send_message(chat_id, reply)
        except Exception as e:
            logger.exception(
                "telegram-bot: reply send failed (chat=%s): %r",
                chat_id, e,
            )