"""Session manager for interactive chat with persistent history.

Sessions are stored as JSON in ``~/.qdrant-rag-agent/sessions/<id>.json``.
Each session keeps:
  - id (uuid4 hex[12])
  - title (auto from first user message, first 40 chars; user-renamable
    via ``SessionManager.rename`` since v0.9.103.5)
  - created_at / updated_at (ISO 8601)
  - messages: list of {role, content, ts, sources: [filename...]}

Sessions are loaded on demand and written atomically (write to .tmp then
rename) so a crash mid-write never corrupts the history.

Ported from milvus-mcp-agent/src/agent/session.py — only the home
directory default changed (milvus-mcp → qdrant-rag-agent).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def _sessions_dir() -> Path:
    """Default location: ``$QDRANT_RAG_HOME/sessions`` (or
    ``~/.qdrant-rag-agent/sessions`` on Windows/macOS/Linux)."""
    base = Path(
        os.environ.get("QDRANT_RAG_HOME", Path.home() / ".qdrant-rag-agent")
    )
    p = base / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug_title(text: str, max_len: int = 40) -> str:
    """Pick a short, filesystem-safe title from the first user message."""
    text = text.strip().splitlines()[0] if text.strip() else "(empty)"
    text = re.sub(r"[\\/:*?\"<>|]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text or "untitled"


class Session:
    """In-memory representation of a chat session."""

    def __init__(self, sid: str | None = None, title: str = ""):
        self.id = sid or uuid.uuid4().hex[:12]
        self.title = title
        self.created_at = _now()
        self.updated_at = self.created_at
        self.messages: list[dict[str, Any]] = []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        s = cls(sid=d.get("id"), title=d.get("title", ""))
        s.created_at = d.get("created_at") or _now()
        s.updated_at = d.get("updated_at") or s.created_at
        s.messages = list(d.get("messages") or [])
        return s


class SessionManager:
    """CRUD over on-disk session JSON files."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else _sessions_dir()

    def _path(self, sid: str) -> Path:
        return self.root / f"{sid}.json"

    def _path(self, sid: str) -> Path:
        return self.root / f"{sid}.json"

    def list(self) -> list[dict]:
        """Return metadata for all sessions, newest first.

        v0.9.133.7 — each row carries an INSTANT summary (no LLM, the
        file is already loaded here): ``summary_first_user`` = the
        first non-empty user message (≈ the conversation topic),
        ``summary_last_assistant`` = the last non-error assistant
        reply, ``summary_chars`` = total message character volume —
        the Sessions-tab user glances at these to decide rename/
        delete; the LLM's ``list_chat_sessions`` sees the same fields.
        Row construction is exception-isolated per session; a summary
        never breaks the listing.
        """
        out: list[dict] = []
        for p in self.root.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                msgs = d.get("messages") or []

                def _clip(t: Any, n: int = 120) -> str:
                    try:
                        x = str(t or "").strip().replace(
                            "\r", " ").replace("\n", " ")
                    except Exception:
                        x = ""
                    return (x[:n] + "…") if len(x) > n else x

                fu = ""
                for m in msgs:
                    if (m.get("role") == "user"
                            and (m.get("content") or "").strip()
                            and m.get("content").startswith("[") is False):
                        fu = _clip(m.get("content"))
                        break
                la = ""
                for m in reversed(msgs):
                    c = (m.get("content") or "").strip()
                    if (m.get("role") == "assistant" and c
                            and not c.startswith("[error]")):
                        la = _clip(c)
                        break
                out.append({
                    "id": d.get("id", p.stem),
                    "title": d.get("title", "(untitled)"),
                    "created_at": d.get("created_at"),
                    "updated_at": d.get("updated_at"),
                    "n_messages": len(msgs),
                    "summary_first_user": fu,
                    "summary_last_assistant": la,
                    "summary_chars": sum(
                        len(str(m.get("content") or ""))
                        for m in msgs),
                })
            except Exception:
                # Corrupt file — skip but don't crash listing.
                continue
        out.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
        return out

    def create(self, title: str = "") -> Session:
        s = Session(title=title)
        self.save(s)
        return s

    def load(self, sid: str) -> Session | None:
        p = self._path(sid)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return Session.from_dict(d)
        except Exception:
            return None

    def save(self, session: Session, *, ingest: bool = True,
             touch: bool = True) -> None:
        """Atomic write; two keyword-only flags (v0.9.103.5) whose
        defaults keep the old behaviour byte-identical for every
        existing caller: ``ingest=False`` skips the long-term-memory
        re-ingest hook and ``touch=False`` preserves ``updated_at``
        (a metadata-only edit like ``rename`` must not re-order the
        newest-first session list)."""
        if touch:
            session.updated_at = _now()
        target = self._path(session.id)
        # Atomic write: .tmp + os.replace
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False,
            dir=str(self.root), prefix=f".{session.id}.", suffix=".tmp",
        ) as f:
            tmp_path = Path(f.name)
            json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
        # v0.9.133.16 — WinError 5 retry: a concurrent reader (the
        # Sessions/poll list re-scanning every json every second, the
        # browser's own autoSave racing a background task's save…)
        # holds the target open for a tiny window; Windows refuses
        # os.replace onto an OPEN target with PermissionError(5).
        # The write is the losing side (read window <100 ms) — short
        # backoff retries win 100% of observed races; the final
        # failure still raises (the caller logs it, honest).
        for attempt in range(5):
            try:
                os.replace(tmp_path, target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.08 * (attempt + 1))
        # v0.9.59.0 — long-term-memory ingest (fire-and-forget): the
        # memory module ingests the LAST Q/A pair into the Qdrant
        # memory collection (deterministic id → re-saves are
        # upserts). ANY failure here must never break persistence.
        if ingest and session.messages:
            try:
                from src.agent.memory_store import (
                    ingest_session_pair,
                    memory_enabled,
                )
                if memory_enabled():
                    ingest_session_pair(session)
            except Exception:
                pass

    def delete(self, sid: str) -> bool:
        p = self._path(sid)
        if p.exists():
            p.unlink()
            return True
        return False

    def rename(self, sid: str, title: str) -> Session | None:
        """v0.9.103.5 — rename a persisted session's title (the
        Sessions-tab ✏️ per-row edit / ``cli.py sessions rename``).

        A METADATA-ONLY edit: messages, ``created_at`` AND
        ``updated_at`` are untouched (renaming must not re-order the
        newest-first session list) and the long-term-memory ingest is
        skipped — re-upserting the unchanged LAST Q/A point would only
        re-embed identical text.

        Returns the renamed Session, or ``None`` when the session is
        missing or the new title is empty.
        """
        title = (title or "").strip()
        if not title:
            return None
        s = self.load(sid)
        if s is None:
            return None
        s.title = title
        self.save(s, ingest=False, touch=False)
        return s

    def append_message(self, sid: str, message: dict) -> Session | None:
        s = self.load(sid)
        if s is None:
            return None
        s.messages.append(message)
        # Auto-title from first user message
        if not s.title and message.get("role") == "user":
            s.title = _slug_title(message.get("content", ""))
        self.save(s)
        return s