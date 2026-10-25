"""MCP server: Telegram Bot integration (v0.8.1+).

Exposes six MCP tools so an LLM agent can push notifications to a
**specific, pre-approved** set of Telegram chats:

* ``send_telegram_message(chat_id, text)``     — plain text
* ``send_telegram_markdown(chat_id, text)``    — Telegram MarkdownV2
* ``send_telegram_photo(chat_id, path, caption?)`` — sandbox image
  file uploaded as a photo (v0.9.31.0+)
* ``send_telegram_voice(chat_id, text, caption?)`` — speech synthesis
  delivered as a voice note (v0.9.58.0+)
* ``send_telegram_file(chat_id, path, caption?)``  — sandbox file of
  any non-executable type uploaded as a document via sendDocument,
  ≤50 MB (v0.9.62.0+)
* ``list_allowed_chats()``                     — shows the allowlist

Security model
--------------
The ``TELEGRAM_ALLOWED_CHAT_IDS`` env var is a comma-separated list of
chat IDs the bot is **permitted** to write to. Any tool call whose
``chat_id`` is not in the list is rejected *before* any HTTP request
leaves this process — the LLM can ask all it wants, but the message
will never reach a non-allowlisted recipient.

The bot token is read from ``TELEGRAM_BOT_TOKEN`` (or the env passed
by the McpManager via ``config/mcp.json`` ``env`` block). It is
**never** logged, never embedded in tool output, never sent to anyone
but ``api.telegram.org``.

Transport
---------
Run as a stdio MCP server so it composes with the existing fleet:

    python -m src.mcp_server.telegram_server

Add to ``config/mcp.json``:

    {
      "name": "telegram",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.telegram_server"],
      "env": {
        "TELEGRAM_BOT_TOKEN": "${TELEGRAM_BOT_TOKEN}",
        "TELEGRAM_ALLOWED_CHAT_IDS": "${TELEGRAM_ALLOWED_CHAT_IDS}"
      }
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

# Telegram Bot API constraints — keep in one place.
TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096        # hard limit per sendMessage call
REQUEST_TIMEOUT_S = 15.0      # network timeout per TEXT HTTP call

# v0.9.31.0+ — sendPhoto constraints.
TELEGRAM_PHOTO_MAX_BYTES = 10 * 1024 * 1024   # Bot API photo upload cap
TELEGRAM_DOC_MAX_BYTES = 50 * 1024 * 1024     # Bot API document cap
MAX_CAPTION_LEN = 1024

# v0.9.57.0 — intermittent-delivery fixes. Diagnosis (user report:
# 「送畫面到 telegram 有時可以收到有時收不到」):
#   1. the FIXED 15 s timeout applied to the multipart photo upload —
#      a few-MB upload on a fluctuating uplink (home broadband / Wi-Fi)
#      regularly exceeds 15 s → URLError timed out → delivered nothing;
#   2. ZERO retries — Telegram 429 flood limits (a watch/schedule
#      pushing several snapshots in a row trips them), 5xx blips and
#      DNS/TLS hiccups all failed the send outright;
#   3. >10 MB photos were REFUSED even though sendDocument allows 50 MB.
# Fixes: size-scaled upload timeout + bounded retry with
# retry_after-aware backoff on transient errors + automatic
# sendDocument downgrade for oversized images.

TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504, 524, 529}
_UPLOAD_MIN_S = 30.0
_UPLOAD_BYTES_PER_S = 262144.0   # assume ≥256 KB/s uplink floor


def _upload_timeout(n_bytes: int) -> float:
    """Upload timeout scaled to the payload (v0.9.57.0): the fixed 15 s
    text timeout regularly killed multi-MB photo uploads on a slow or
    fluctuating uplink. floor = 30 s, plus 1 s per 256 KB of payload →
    a 10 MB photo gets ≈55 s."""
    return max(_UPLOAD_MIN_S, REQUEST_TIMEOUT_S + n_bytes / _UPLOAD_BYTES_PER_S)


def _is_transient_error(msg: str) -> bool:
    """True for errors worth retrying: Telegram 429 / 5xx, and
    network-level failures (URLError — DNS, refused, timed out)."""
    m = re.search(r"HTTP (\d{3})", msg)
    if m and int(m.group(1)) in TRANSIENT_HTTP_CODES:
        return True
    return "network error" in msg


def _retry_after_s(msg: str) -> float | None:
    """Parse Telegram's ``retry_after`` out of the error body when the
    429 carried one (``{"parameters": {"retry_after": N}}``)."""
    m = re.search(r'retry_after(?:&quot;|["\'])?\s*[:=]\s*(\d+)', msg)
    return float(m.group(1)) if m else None


def _bot_post_retry(
    req: urllib.request.Request,
    timeout: float,
    attempts: int = 3,
) -> dict[str, Any]:
    """``_bot_post`` with bounded retry on TRANSIENT errors (v0.9.57.0).

    Telegram 429 (flood) carries ``retry_after`` — we honour it; other
    transient failures back off exponentially (2 s, 4 s). Permanent
    errors (400/401/403/404, Telegram ok=False validation) raise on
    the first attempt without retry.
    """
    last: RuntimeError | None = None
    for attempt in range(attempts):
        try:
            return _bot_post(req, timeout)
        except RuntimeError as e:
            last = e
            msg = str(e)
            if not _is_transient_error(msg) or attempt >= attempts - 1:
                raise
            wait = _retry_after_s(msg) or float(2 ** (attempt + 1))
            logger.warning(
                "[telegram-mcp] transient error (%.200s) — retry "
                "%d/%d in %.0fs", msg, attempt + 1, attempts - 1, wait,
            )
            time.sleep(wait)
    raise last or RuntimeError("telegram send failed")

_PHOTO_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# v0.9.62.0+ — sendDocument upload extension gate (mirror of
# ``src.mcp_client.host._FS_DANGEROUS_WRITE_EXTS``; kept local so the
# stdio server stays dependency-light, same pattern as
# ``_image_magic_ok``). Any file type is uploadable EXCEPT these —
# the file tool must never become an LLM-driven executable-
# exfiltration channel.
_TELEGRAM_REFUSED_UPLOAD_EXTS: frozenset[str] = frozenset({
    ".exe", ".dll", ".so", ".dylib",
    ".bat", ".cmd", ".sh", ".ps1", ".vbs", ".js", ".wsf",
    ".scr", ".com", ".cpl",
})


# ──────────────────────────────────────────────────────────────
# Allowlist helpers
# ──────────────────────────────────────────────────────────────


def _load_allowed_ids() -> list[int]:
    """Read ``TELEGRAM_ALLOWED_CHAT_IDS`` from env, return parsed ints.

    Empty / unset → empty list (i.e. no chats allowed — every send
    call will be rejected with a clear error). This is the safe
    default: forgetting to configure the allowlist should fail closed,
    not open.
    """
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
            logger.warning(
                "[telegram-mcp] ignoring non-integer chat id %r", tok,
            )
    return out


def _is_allowed(chat_id: int, allowed: list[int]) -> bool:
    return chat_id in allowed


# ──────────────────────────────────────────────────────────────
# Bot API HTTP client (stdlib urllib, zero extra deps)
# ──────────────────────────────────────────────────────────────


def _bot_post(
    req: urllib.request.Request,
    timeout: float = REQUEST_TIMEOUT_S,
) -> dict[str, Any]:
    """Shared POST + response parsing for the urlencoded AND the
    multipart Bot API callers. Raises ``RuntimeError`` with a
    human-readable message on any failure (HTTP error, non-2xx,
    invalid JSON, Telegram-side ``ok=False``)."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"Telegram API HTTP {e.code}: {body_text or e.reason}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Telegram API network error: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Telegram API non-JSON response: {e}") from e

    if not payload.get("ok"):
        raise RuntimeError(
            f"Telegram API error: "
            f"{payload.get('error_code', '?')} "
            f"{payload.get('description', 'unknown')}"
        )
    return payload.get("result", {})


def _post_bot_method(
    token: str,
    method: str,
    params: dict[str, Any],
    timeout: float = REQUEST_TIMEOUT_S,
) -> dict[str, Any]:
    """POST to ``https://api.telegram.org/bot<token>/<method>``.

    Returns the parsed JSON ``result`` field on success. Raises
    ``RuntimeError`` with a human-readable message on any failure
    (HTTP error, non-2xx, invalid JSON, Telegram-side ``ok=False``).
    """
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return _bot_post_retry(req, timeout)


def _post_bot_multipart(
    token: str,
    method: str,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    content_type: str,
    data: bytes,
    timeout: float = REQUEST_TIMEOUT_S,
) -> dict[str, Any]:
    """Multipart/form-data POST (v0.9.31.0+) — used by ``sendPhoto``.

    ``fields`` carries the simple text params (chat_id, caption);
    ``data`` is the raw file bytes uploaded under ``file_field``.
    Same error semantics as ``_post_bot_method``.
    """
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n'
                f"\r\n{value}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    # v0.9.57.0: transient-error retry + size-scaled timeout — the
    # fixed 15 s timeout was the main cause of intermittent
    # 「照片有時送不到」 on fluctuating uplinks.
    return _bot_post_retry(req, timeout)


def _send_text(
    token: str,
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        params["parse_mode"] = parse_mode
    return _post_bot_method(token, "sendMessage", params)


# ──────────────────────────────────────────────────────────────
# sendPhoto (v0.9.31.0+) — sandbox-gated local image upload
# ──────────────────────────────────────────────────────────────


def _image_magic_ok(data: bytes, mime: str) -> bool:
    """Mirror of gui.api._image_magic_ok — kept local so the stdio
    server stays dependency-light (no gui / qdrant import chain)."""
    if mime == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if mime == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if mime == "image/gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if mime == "image/webp":
        return (
            len(data) >= 12 and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
        )
    if mime == "image/bmp":
        return data[:2] == b"BM"
    return True


def _root_contains(child: Path, parent: Path) -> bool:
    """Mirror of ``src.mcp_client.host._is_within`` — resolved-path
    containment so symlink / ``..`` traversal can't bypass the gate."""
    try:
        c = child.resolve()
        p = parent.resolve()
    except OSError:
        return False
    if hasattr(c, "is_relative_to"):
        try:
            return c.is_relative_to(p)
        except ValueError:
            return False
    try:
        return (c.as_posix() + "/").startswith(p.as_posix() + "/")
    except Exception:
        return False


def _telegram_allowed_roots() -> list[Path]:
    """Mirror of ``src.mcp_client.host._allowed_file_roots()`` —
    env-only resolution (import ∪ filesystem WRITE ∪ READ roots,
    WATCH_DIR / ./documents fallback) so the heavy host import
    chain is not pulled into the stdio server."""
    out: list[Path] = []
    seen: set[str] = set()
    for env in (
        "QDRANT_IMPORT_ALLOWED_ROOT",
        "QDRANT_FILESYSTEM_WRITE_ROOT",
        "QDRANT_FILESYSTEM_READ_ROOT",
    ):
        raw = (os.environ.get(env, "") or "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (Path(__file__).resolve().parents[2] / raw).resolve()
        else:
            p = p.resolve()
        key = p.as_posix()
        if key not in seen:
            seen.add(key)
            out.append(p)
    if not out:
        raw = (os.environ.get("WATCH_DIR", "") or "").strip()
        base = Path(raw) if raw else Path("./documents")
        if not base.is_absolute():
            base = (Path(__file__).resolve().parents[2] / base).resolve()
        out.append(base)
    return out


def _send_photo(
    token: str,
    chat_id: int,
    file_path: str,
    caption: str | None = None,
) -> dict[str, Any]:
    """Upload a LOCAL sandbox image to Telegram via ``sendPhoto``.

    Security mirrors the inline-image bridge: the file MUST live
    inside one of the allowed sandbox roots (import ∪ filesystem
    WRITE ∪ READ), the extension must be a sendable image type,
    magic bytes are verified and the 10 MB Bot API upload cap is
    enforced BEFORE any HTTP request leaves this process.
    """
    p_str = str(file_path or "").strip()
    if not p_str:
        raise RuntimeError("'path' is required")
    target = Path(p_str)
    if not target.is_absolute():
        # Relative paths (uploads/x.png) anchor at the WRITE root —
        # the roots-containment check below still applies, so an
        # escaping relative path is refused.
        raw = (os.environ.get("QDRANT_FILESYSTEM_WRITE_ROOT", "")
               or "").strip()
        if not raw:
            raise RuntimeError(
                f"relative path {p_str} without a configured "
                "QDRANT_FILESYSTEM_WRITE_ROOT",
            )
        target = Path(raw).expanduser() / target
    roots = _telegram_allowed_roots()
    if not any(_root_contains(target, r) for r in roots):
        raise RuntimeError(
            f"path {p_str} is outside the allowed sandbox roots — "
            "save or copy the image into the filesystem write "
            "sandbox first (files on the Desktop / OneDrive cannot "
            "be sent)",
        )
    mime = _PHOTO_MIME_BY_EXT.get(target.suffix.lower())
    if not mime:
        raise RuntimeError(
            "not a sendable image extension: "
            f"{target.suffix or '(none)'} (PNG/JPEG/GIF/WEBP/BMP only)",
        )
    if not target.is_file():
        raise RuntimeError(f"file not found: {target.as_posix()}")
    data = target.read_bytes()
    # v0.9.57.0: >10 MB photos are NOT refused anymore — they are
    # downgraded to sendDocument (Bot API cap 50 MB) so oversized
    # snapshots still reach the chat instead of failing outright.
    use_document = len(data) > TELEGRAM_PHOTO_MAX_BYTES
    if use_document and len(data) > TELEGRAM_DOC_MAX_BYTES:
        raise RuntimeError(
            f"image too large ({len(data) // (1024 * 1024)} MB > "
            f"{TELEGRAM_DOC_MAX_BYTES // (1024 * 1024)} MB "
            "Bot API document cap)",
        )
    if not _image_magic_ok(data, mime):
        raise RuntimeError("content is not a valid image")
    fields: dict[str, str] = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption[:MAX_CAPTION_LEN]
    result = _post_bot_multipart(
        token,
        "sendDocument" if use_document else "sendPhoto",
        fields,
        "document" if use_document else "photo",
        target.name,
        mime,
        data,
        timeout=_upload_timeout(len(data)),
    )
    if isinstance(result, dict):
        result["_sent_as"] = "document" if use_document else "photo"
    return result




# ──────────────────────────────────────────────────────────────
# sendDocument (v0.9.62.0+) — sandbox-gated local file upload
# ──────────────────────────────────────────────────────────────


def _send_document(
    token: str,
    chat_id: int,
    file_path: str,
    caption: str | None = None,
) -> dict[str, Any]:
    """Upload a LOCAL sandbox file to Telegram via ``sendDocument``
    (v0.9.62.0+).

    Security mirrors ``_send_photo``: the file MUST live inside one of
    the allowed sandbox roots (import ∪ filesystem WRITE ∪ READ),
    relative paths anchor at the WRITE root, and the 50 MB Bot API
    document cap is enforced BEFORE any HTTP request leaves this
    process. Any file type is sendable EXCEPT the dangerous-executable
    blocklist (``.exe`` / ``.bat`` / ``.ps1`` / ...) — the tool opens
    a file-transfer channel, never an executable exfiltration channel.
    """
    p_str = str(file_path or "").strip()
    if not p_str:
        raise RuntimeError("'path' is required")
    target = Path(p_str)
    if not target.is_absolute():
        # Relative paths (uploads/x.pdf) anchor at the WRITE root —
        # the roots-containment check below still applies, so an
        # escaping relative path is refused.
        raw = (os.environ.get("QDRANT_FILESYSTEM_WRITE_ROOT", "")
               or "").strip()
        if not raw:
            raise RuntimeError(
                f"relative path {p_str} without a configured "
                "QDRANT_FILESYSTEM_WRITE_ROOT",
            )
        target = Path(raw).expanduser() / target
    roots = _telegram_allowed_roots()
    if not any(_root_contains(target, r) for r in roots):
        raise RuntimeError(
            f"path {p_str} is outside the allowed sandbox roots — "
            "save or copy the file into the filesystem write "
            "sandbox first (files on the Desktop / OneDrive cannot "
            "be sent)",
        )
    ext = target.suffix.lower()
    if ext in _TELEGRAM_REFUSED_UPLOAD_EXTS:
        raise RuntimeError(
            f"refusing to upload a dangerous executable ({ext}) — "
            "documents only; executables and scripts are blocked",
        )
    if not target.is_file():
        raise RuntimeError(f"file not found: {target.as_posix()}")
    data = target.read_bytes()
    if len(data) > TELEGRAM_DOC_MAX_BYTES:
        raise RuntimeError(
            f"file too large ({len(data) // (1024 * 1024)} MB > "
            f"{TELEGRAM_DOC_MAX_BYTES // (1024 * 1024)} MB "
            "Bot API document cap)",
        )
    mime = (
        mimetypes.guess_type(target.name)[0]
        or "application/octet-stream"
    )
    fields: dict[str, str] = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption[:MAX_CAPTION_LEN]
    result = _post_bot_multipart(
        token,
        "sendDocument",
        fields,
        "document",
        target.name,
        mime,
        data,
        timeout=_upload_timeout(len(data)),
    )
    if isinstance(result, dict):
        result["_sent_as"] = "document"
    return result


# ──────────────────────────────────────────────────────────────
# Tool handlers
# ──────────────────────────────────────────────────────────────


class TelegramMCPServer:
    """Telegram-backed MCP server (6 tools, allowlist-enforced)."""

    def __init__(
        self,
        token: str | None = None,
        allowed_chat_ids: list[int] | None = None,
    ):
        # Token + allowlist are read from env at construction time.
        # Tests inject fixed values; production reads from .env via
        # the env-overlay that ``McpManager`` passes to stdio.
        self.token = (
            token
            if token is not None
            else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        )
        self.allowed = (
            list(allowed_chat_ids)
            if allowed_chat_ids is not None
            else _load_allowed_ids()
        )
        self.server = Server("divine-sense-agent-telegram")
        self._register_tools()

    # ── tool registration ───────────────────────────────────────
    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return [
                Tool(
                    name="send_telegram_message",
                    description=(
                        "Send a plain-text message to a pre-approved "
                        "Telegram chat. ``chat_id`` MUST be in the "
                        "allowlist (see ``list_allowed_chats``); calls "
                        "with other chat IDs are rejected before any "
                        "HTTP request is made."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "chat_id": {
                                "type": "integer",
                                "description": (
                                    "Telegram chat ID — must be in "
                                    "the allowlist."
                                ),
                            },
                            "text": {
                                "type": "string",
                                "description": (
                                    "Message body, up to 4096 chars."
                                ),
                            },
                        },
                        "required": ["chat_id", "text"],
                    },
                ),
                Tool(
                    name="send_telegram_markdown",
                    description=(
                        "Send a FORMATTED message to a Telegram chat — "
                        "write NORMAL markdown (``**bold**``, ``_italic_``, "
                        "``# heading``, ``- lists``, ``| tables |``, "
                        "``> quotes``, ``[links](url)``, `` `code` ``); the "
                        "bot converts it to Telegram MarkdownV2 "
                        "automatically and falls back to plain text if "
                        "Telegram still rejects it. Subject to the same "
                        "allowlist check."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "chat_id": {
                                "type": "integer",
                                "description": (
                                    "Telegram chat ID — must be in "
                                    "the allowlist."
                                ),
                            },
                            "text": {
                                "type": "string",
                                "description": (
                                    "Message body in normal markdown "
                                    "(converted to Telegram MarkdownV2 "
                                    "automatically), up to 4096 chars."
                                ),
                            },
                        },
                        "required": ["chat_id", "text"],
                    },
                ),
                Tool(
                    name="send_telegram_photo",
                    description=(
                        "Send a LOCAL image file (PNG/JPEG/GIF/WEBP/BMP) "
                        "to a pre-approved Telegram chat as a photo — "
                        "or, automatically, as a document when it "
                        "exceeds the 10 MB photo cap (up to 50 MB). "
                        "Transient delivery errors (429 flood / 5xx / "
                        "network) are retried automatically with "
                        "backoff. ``path`` MUST be inside the "
                        "filesystem sandbox roots — files outside "
                        "(Desktop / OneDrive) are refused; copy them "
                        "into the write sandbox first. ``caption`` is "
                        "optional (≤1024 chars). ``chat_id`` must be "
                        "in the allowlist (see ``list_allowed_chats``)."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "chat_id": {
                                "type": "integer",
                                "description": (
                                    "Telegram chat ID — must be in "
                                    "the allowlist."
                                ),
                            },
                            "path": {
                                "type": "string",
                                "description": (
                                    "Absolute path (or uploads/-relative "
                                    "path) of the image file inside the "
                                    "filesystem sandbox."
                                ),
                            },
                            "caption": {
                                "type": "string",
                                "description": (
                                    "Optional photo caption, up to "
                                    "1024 chars."
                                ),
                            },
                        },
                        "required": ["chat_id", "path"],
                    },
                ),
                Tool(
                    name="send_telegram_file",
                    description=(
                        "Send a LOCAL file of ANY type (pdf / csv / "
                        "zip / docx / mp4 / ...) to a pre-approved "
                        "Telegram chat as a DOCUMENT via sendDocument "
                        "(Bot API cap 50 MB). Executables / scripts "
                        "(.exe/.bat/.ps1/...) are refused. ``path`` "
                        "MUST be inside the filesystem sandbox roots "
                        "— files outside (Desktop / OneDrive) are "
                        "refused; copy them into the write sandbox "
                        "first. Transient delivery errors (429 flood "
                        "/ 5xx / network) are retried automatically "
                        "with backoff. Use ``send_telegram_photo`` "
                        "instead for images. ``caption`` is optional "
                        "(≤1024 chars). ``chat_id`` must be in the "
                        "allowlist (see ``list_allowed_chats``)."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "chat_id": {
                                "type": "integer",
                                "description": (
                                    "Telegram chat ID — must be in "
                                    "the allowlist."
                                ),
                            },
                            "path": {
                                "type": "string",
                                "description": (
                                    "Absolute path (or uploads/-relative "
                                    "path) of the file inside the "
                                    "filesystem sandbox."
                                ),
                            },
                            "caption": {
                                "type": "string",
                                "description": (
                                    "Optional document caption, up "
                                    "to 1024 chars."
                                ),
                            },
                        },
                        "required": ["chat_id", "path"],
                    },
                ),
                Tool(
                    name="send_telegram_voice",
                    description=(
                        "Send a VOICE note (speech synthesis) to a "
                        "pre-approved Telegram chat: the ``text`` is "
                        "synthesized via OpenRouter TTS and delivered "
                        "with sendVoice (.ogg/Opus; mp3 payloads are "
                        "transcoded via ffmpeg when available). Use it "
                        "when the user asks to reply BY VOICE (「用語音"
                        "回覆」「唸給我聽」) or to voice-ify short "
                        "summaries. Markdown is stripped before "
                        "synthesis; keep ``text`` under ~2000 chars. "
                        "``chat_id`` must be in the allowlist (see "
                        "``list_allowed_chats``)."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "chat_id": {
                                "type": "integer",
                                "description": (
                                    "Telegram chat ID — must be in "
                                    "the allowlist."
                                ),
                            },
                            "text": {
                                "type": "string",
                                "description": (
                                    "Text to speak (markdown is "
                                    "stripped; up to ~2000 chars)."
                                ),
                            },
                            "caption": {
                                "type": "string",
                                "description": (
                                    "Optional voice-message caption, "
                                    "up to 1024 chars."
                                ),
                            },
                        },
                        "required": ["chat_id", "text"],
                    },
                ),
                Tool(
                    name="list_allowed_chats",
                    description=(
                        "List the chat IDs the bot is permitted to "
                        "write to. Use this to discover which "
                        "``chat_id`` values are valid before calling "
                        "``send_telegram_message``."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            try:
                if name == "send_telegram_message":
                    return await self._handle_send(
                        arguments, parse_mode=None,
                    )
                if name == "send_telegram_markdown":
                    return await self._handle_send(
                        arguments, parse_mode="MarkdownV2",
                    )
                if name == "send_telegram_photo":
                    return await self._handle_photo(arguments)
                if name == "send_telegram_file":
                    return await self._handle_file(arguments)
                if name == "send_telegram_voice":
                    return await self._handle_voice(arguments)
                if name == "list_allowed_chats":
                    return await self._handle_list_allowed()
            except Exception as e:
                return [TextContent(
                    type="text", text=f"Error: {e}",
                )]
            return [TextContent(
                type="text", text=f"Unknown tool: {name}",
            )]



    async def _handle_send(
        self,
        args: dict,
        parse_mode: str | None,
    ) -> list[TextContent]:
        return await asyncio.to_thread(
            self._do_send, args, parse_mode,
        )

    def _do_send(
        self,
        args: dict,
        parse_mode: str | None,
    ) -> list[TextContent]:
        # ── input validation ────────────────────────────────────
        if not self.token:
            return [TextContent(
                type="text",
                text=(
                    "Error: TELEGRAM_BOT_TOKEN is not configured. "
                    "Set it in .env or pass it via the McpManager "
                    "env overlay."
                ),
            )]
        if not self.allowed:
            return [TextContent(
                type="text",
                text=(
                    "Error: TELEGRAM_ALLOWED_CHAT_IDS is empty — no "
                    "chats are allowed. Set the env var to a "
                    "comma-separated list of chat IDs."
                ),
            )]

        chat_id = args.get("chat_id")
        # bool is a subclass of int — exclude it explicitly so
        # ``True`` doesn't sneak through as chat_id=1.
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return [TextContent(
                type="text",
                text="Error: 'chat_id' must be an integer.",
            )]
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            return [TextContent(
                type="text",
                text="Error: 'text' must be a non-empty string.",
            )]
        # ── MarkdownV2 conversion (v0.9.67.0) ─────────────────────
        # The LLM writes NORMAL markdown — convert it to strict
        # Telegram MarkdownV2 here (bold/italic/code/tables/quotes/
        # links + full specials escaping) so the sendMessage never
        # 400s on unescaped entities. Plain mode keeps the text
        # byte-identical.
        original_text = text
        if parse_mode == "MarkdownV2":
            from src.telegram_md import markdown_to_telegram_v2

            text = markdown_to_telegram_v2(text)
        if len(text) > MAX_MESSAGE_LEN:
            return [TextContent(
                type="text",
                text=(
                    f"Error: 'text' is {len(text)} chars after "
                    f"MarkdownV2 conversion; Telegram allows at most "
                    f"{MAX_MESSAGE_LEN}. Try shortening the message "
                    f"or use send_telegram_message (plain)."
                ),
            )]

        # ── allowlist check (the whole point of this module) ─────
        if not _is_allowed(chat_id, self.allowed):
            logger.warning(
                "[telegram-mcp] rejected chat_id=%s (not in allowlist)",
                chat_id,
            )
            return [TextContent(
                type="text",
                text=(
                    f"Error: chat_id {chat_id} is not in the "
                    f"allowlist. Call list_allowed_chats to see "
                    f"which IDs are accepted."
                ),
            )]

        # ── send ─────────────────────────────────────────────────
        try:
            result = _send_text(
                self.token, chat_id, text, parse_mode=parse_mode,
            )
        except RuntimeError as e:
            # v0.9.67.0 — MarkdownV2 parse failures (Telegram rejects
            # an entity edge case) degrade to a PLAIN send of the
            # original text — the message is never lost to a parser
            # quirk.
            if parse_mode == "MarkdownV2":
                logger.warning(
                    "[telegram-mcp] MarkdownV2 send failed (%r) — "
                    "retrying as plain text", e,
                )
                try:
                    result = _send_text(
                        self.token, chat_id, original_text,
                        parse_mode=None,
                    )
                except RuntimeError as e2:
                    return [TextContent(
                        type="text", text=f"Error: {e2}",
                    )]
                msg = result.get("message", {}) if isinstance(
                    result, dict) else {}
                out = {
                    "ok": True,
                    "chat_id": chat_id,
                    "message_id": msg.get("message_id"),
                    "parse_mode": None,
                    "mdv2_fallback": True,
                    "length": len(original_text),
                }
                return [TextContent(
                    type="text",
                    text=json.dumps(out, ensure_ascii=False),
                )]
            return [TextContent(type="text", text=f"Error: {e}")]

        msg = result.get("message", {}) if isinstance(result, dict) else {}
        out = {
            "ok": True,
            "chat_id": chat_id,
            "message_id": msg.get("message_id"),
            "parse_mode": parse_mode,
            "length": len(text),
        }
        return [TextContent(
            type="text", text=json.dumps(out, ensure_ascii=False),
        )]

    async def _handle_list_allowed(self) -> list[TextContent]:
        return await asyncio.to_thread(self._do_list_allowed)

    async def _handle_photo(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_send_photo, args)

    def _do_send_photo(self, args: dict) -> list[TextContent]:
        # ── input validation (same shape as _do_send) ────────────
        if not self.token:
            return [TextContent(
                type="text",
                text=(
                    "Error: TELEGRAM_BOT_TOKEN is not configured. "
                    "Set it in .env or pass it via the McpManager "
                    "env overlay."
                ),
            )]
        if not self.allowed:
            return [TextContent(
                type="text",
                text=(
                    "Error: TELEGRAM_ALLOWED_CHAT_IDS is empty — no "
                    "chats are allowed. Set the env var to a "
                    "comma-separated list of chat IDs."
                ),
            )]
        chat_id = args.get("chat_id")
        # bool is a subclass of int — exclude it explicitly so
        # ``True`` doesn't sneak through as chat_id=1.
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return [TextContent(
                type="text",
                text="Error: 'chat_id' must be an integer.",
            )]
        if not _is_allowed(chat_id, self.allowed):
            logger.warning(
                "[telegram-mcp] rejected photo chat_id=%s "
                "(not in allowlist)", chat_id,
            )
            return [TextContent(
                type="text",
                text=(
                    f"Error: chat_id {chat_id} is not in the "
                    f"allowlist. Call list_allowed_chats to see "
                    f"which IDs are accepted."
                ),
            )]
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return [TextContent(
                type="text",
                text="Error: 'path' must be a non-empty string.",
            )]
        caption = args.get("caption")
        if caption is not None:
            if not isinstance(caption, str):
                return [TextContent(
                    type="text",
                    text="Error: 'caption' must be a string.",
                )]
            if len(caption) > MAX_CAPTION_LEN:
                return [TextContent(
                    type="text",
                    text=(
                        f"Error: 'caption' is {len(caption)} chars; "
                        f"Telegram allows at most {MAX_CAPTION_LEN} "
                        "for a photo caption."
                    ),
                )]

        # ── send (sandbox gate runs inside _send_photo) ──────────
        try:
            result = _send_photo(
                self.token, chat_id, path, caption=caption or None,
            )
        except RuntimeError as e:
            return [TextContent(type="text", text=f"Error: {e}")]

        # Telegram returns the Message object directly as ``result``;
        # some mocks / legacy shapes wrap it under ``message``.
        msg = {}
        if isinstance(result, dict):
            raw = result.get("message")
            msg = raw if isinstance(raw, dict) else result
        photos = msg.get("photo")
        thumb = (
            photos[-1]
            if isinstance(photos, list) and photos
            else {}
        )
        out = {
            "ok": True,
            "chat_id": chat_id,
            "message_id": msg.get("message_id") if isinstance(msg, dict) else None,
            "file": Path(str(path).strip()).name,
            "photo_width": thumb.get("width"),
            "photo_height": thumb.get("height"),
            "caption": (caption or "")[:MAX_CAPTION_LEN] or None,
            # v0.9.57.0: "photo" or "document" (>10 MB auto-downgrade)
            "sent_as": (
                result.get("_sent_as") if isinstance(result, dict) else None
            ),
        }
        return [TextContent(
            type="text", text=json.dumps(out, ensure_ascii=False),
        )]

    # ── v0.9.62.0: send_telegram_file (sendDocument upload) ─────

    async def _handle_file(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_send_file, args)

    def _do_send_file(self, args: dict) -> list[TextContent]:
        # ── input validation (same shape as _do_send_photo) ──────
        if not self.token:
            return [TextContent(
                type="text",
                text=(
                    "Error: TELEGRAM_BOT_TOKEN is not configured. "
                    "Set it in .env or pass it via the McpManager "
                    "env overlay."
                ),
            )]
        if not self.allowed:
            return [TextContent(
                type="text",
                text=(
                    "Error: TELEGRAM_ALLOWED_CHAT_IDS is empty — no "
                    "chats are allowed. Set the env var to a "
                    "comma-separated list of chat IDs."
                ),
            )]
        chat_id = args.get("chat_id")
        # bool is a subclass of int — exclude it explicitly so
        # ``True`` doesn't sneak through as chat_id=1.
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return [TextContent(
                type="text",
                text="Error: 'chat_id' must be an integer.",
            )]
        if not _is_allowed(chat_id, self.allowed):
            logger.warning(
                "[telegram-mcp] rejected file chat_id=%s "
                "(not in allowlist)", chat_id,
            )
            return [TextContent(
                type="text",
                text=(
                    f"Error: chat_id {chat_id} is not in the "
                    f"allowlist. Call list_allowed_chats to see "
                    f"which IDs are accepted."
                ),
            )]
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return [TextContent(
                type="text",
                text="Error: 'path' must be a non-empty string.",
            )]
        caption = args.get("caption")
        if caption is not None:
            if not isinstance(caption, str):
                return [TextContent(
                    type="text",
                    text="Error: 'caption' must be a string.",
                )]
            if len(caption) > MAX_CAPTION_LEN:
                return [TextContent(
                    type="text",
                    text=(
                        f"Error: 'caption' is {len(caption)} chars; "
                        f"Telegram allows at most {MAX_CAPTION_LEN} "
                        "for a document caption."
                    ),
                )]

        # ── send (sandbox gate runs inside _send_document) ───────
        try:
            result = _send_document(
                self.token, chat_id, path, caption=caption or None,
            )
        except RuntimeError as e:
            return [TextContent(type="text", text=f"Error: {e}")]

        # Telegram returns the Message object directly as ``result``;
        # some mocks / legacy shapes wrap it under ``message``. The
        # sendDocument response carries a ``document`` dict with
        # file_name / mime_type / file_size.
        msg = {}
        if isinstance(result, dict):
            raw = result.get("message")
            msg = raw if isinstance(raw, dict) else result
        doc = (
            msg.get("document")
            if isinstance(msg, dict)
            and isinstance(msg.get("document"), dict)
            else {}
        )
        out = {
            "ok": True,
            "chat_id": chat_id,
            "message_id": msg.get("message_id") if isinstance(msg, dict) else None,
            "file": Path(str(path).strip()).name,
            "file_name": doc.get("file_name"),
            "mime_type": doc.get("mime_type"),
            "size_bytes": doc.get("file_size"),
            "caption": (caption or "")[:MAX_CAPTION_LEN] or None,
            "sent_as": (
                result.get("_sent_as") if isinstance(result, dict) else None
            ),
        }
        return [TextContent(
            type="text", text=json.dumps(out, ensure_ascii=False),
        )]

    # ── v0.9.58.0: send_telegram_voice (TTS via OpenRouter) ─────

    @property
    def _speech(self):
        client = self.__dict__.get("_speech_client")
        if client is None:
            from src.agent.speech import SpeechClient
            client = SpeechClient.from_env()
            self.__dict__["_speech_client"] = client
        return client

    async def _handle_voice(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_send_voice, args)

    def _do_send_voice(self, args: dict) -> list[TextContent]:
        # ── input validation (same shape as _do_send) ─────────────
        if not self.token:
            return [TextContent(
                type="text",
                text=(
                    "Error: TELEGRAM_BOT_TOKEN is not configured. "
                    "Set it in .env or pass it via the McpManager "
                    "env overlay."
                ),
            )]
        if not self.allowed:
            return [TextContent(
                type="text",
                text=(
                    "Error: TELEGRAM_ALLOWED_CHAT_IDS is empty — no "
                    "chats are allowed. Set the env var to a "
                    "comma-separated list of chat IDs."
                ),
            )]
        chat_id = args.get("chat_id")
        # bool is a subclass of int — exclude it explicitly so
        # ``True`` doesn't sneak through as chat_id=1.
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return [TextContent(
                type="text",
                text="Error: 'chat_id' must be an integer.",
            )]
        if not _is_allowed(chat_id, self.allowed):
            logger.warning(
                "[telegram-mcp] rejected voice chat_id=%s "
                "(not in allowlist)", chat_id,
            )
            return [TextContent(
                type="text",
                text=(
                    f"Error: chat_id {chat_id} is not in the "
                    f"allowlist. Call list_allowed_chats to see "
                    f"which IDs are accepted."
                ),
            )]
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            return [TextContent(
                type="text",
                text="Error: 'text' must be a non-empty string.",
            )]
        caption = args.get("caption")
        if caption is not None:
            if not isinstance(caption, str):
                return [TextContent(
                    type="text",
                    text="Error: 'caption' must be a string.",
                )]
            if len(caption) > MAX_CAPTION_LEN:
                return [TextContent(
                    type="text",
                    text=(
                        f"Error: 'caption' is {len(caption)} chars; "
                        f"Telegram allows at most {MAX_CAPTION_LEN} "
                        "for a voice-message caption."
                    ),
                )]

        # ── synthesize (OpenRouter TTS → OGG/Opus) ────────────────
        try:
            speech = self._speech
            audio, fmt = speech.synthesize(text)
            if fmt != "ogg":
                audio = speech.to_voice_ogg(audio)
        except RuntimeError as e:
            return [TextContent(type="text", text=f"Error: {e}")]

        fields: dict[str, str] = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption[:MAX_CAPTION_LEN]
        result = _post_bot_multipart(
            self.token,
            "sendVoice",
            fields,
            "voice",
            "reply.ogg",
            "audio/ogg",
            audio,
            timeout=_upload_timeout(len(audio)),
        )
        msg = result.get("message") if isinstance(result, dict) else {}
        if not isinstance(msg, dict):
            msg = result if isinstance(result, dict) else {}
        out = {
            "ok": True,
            "chat_id": chat_id,
            "message_id": msg.get("message_id"),
            "spoken_chars": len(text),
            "audio_bytes": len(audio),
            "audio_format": (
                "ogg" if audio[:4] == b"OggS" else "mp3(transcoded-"
                "fallback)"
            ),
        }
        return [TextContent(
            type="text", text=json.dumps(out, ensure_ascii=False),
        )]

    def _do_list_allowed(self) -> list[TextContent]:
        body = {
            "count": len(self.allowed),
            "allowed_chat_ids": list(self.allowed),
            "bot_configured": bool(self.token),
        }
        return [TextContent(
            type="text", text=json.dumps(body, ensure_ascii=False),
        )]

    async def run_stdio(self) -> None:
        """Run the MCP server over stdio."""
        async with stdio_server() as (r, w):
            await self.server.run(r, w, self.server.create_initialization_options())


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout is reserved for JSON-RPC
    )
    server = TelegramMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
