"""Chat context compression (v0.9.10.0+) — three opt-in techniques.

The default configuration (``CHAT_CONTEXT_COMPRESSION=off``) keeps
chat behaviour byte-identical to v0.9.9.4 — nothing in this module
runs unless the user opts in through ``.env``:

    CHAT_CONTEXT_COMPRESSION=off | auto | aggressive

Techniques:

1. **Summarization** — history longer than
   ``CHAT_COMPRESS_TRIGGER_MESSAGES`` folds the older tail into ONE
   compact ``system`` summary message; the most recent
   ``CHAT_COMPRESS_KEEP_MESSAGES`` messages stay verbatim. The
   summary is LLM-generated when possible
   (``CHAT_COMPRESS_SUMMARY_MODE=llm``; the Agent injects the
   callable, failures fall back to a deterministic extractive
   summarizer that needs no network) and cached per old-prefix so a
   growing session only re-summarizes when the folded portion
   changes.

2. **Offloading** — a successful tool result longer than
   ``CHAT_OFFLOAD_THRESHOLD_CHARS`` is written to a file (default:
   ``<filesystem read sandbox>/chat_offloads/`` so the LLM can read
   it back with the ``read_file`` tool) and the tool message is
   replaced by a pointer + 800-char preview. Tool ERRORS are never
   offloaded (they are short and must stay visible).

3. **Selective (semantic) compression** — any history message longer
   than ``CHAT_COMPRESS_MSG_MAX_CHARS`` keeps fenced code blocks and
   JSON-looking blocks verbatim and only squeezes the surrounding
   prose (head + tail sentences with an explicit omission marker).
   ``aggressive`` mode halves the trigger and the per-message budget.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

_MODES = ("off", "auto", "aggressive")

# v0.9.104.3 — the ABSOLUTE per-tool-result cap is enforced in
# agent.py::_run_tool_call (system-level, independent of the
# compressor); the resolver lives there (_tool_result_cap).

# Fenced code blocks are structured data — never squeezed.
_FENCE_RE = re.compile(r"```[\s\S]*?```")

# Sentence split: after CJK terminators (zero-width) or after Latin
# terminators followed by whitespace (avoids cutting 3.5 decimals).
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?])|(?<=[.!?])\s+")

_PREVIEW_CHARS = 800

# v0.9.107.0 — tools whose BIG successful results must NEVER be
# offloaded: their source artefact already lives in the read sandbox,
# so "save it to another file and read_file it back" is circular.
# The 2026-10-03/04 telegram stalls (read_file x62/x118 storms ->
# max_iterations=50) were exactly this pointer chain: each read-back
# of an offload file was itself a big read_file result -> offloaded
# AGAIN -> JSON-envelope onion with ever-deeper escaping. read_file
# results stay inline (bounded by the absolute 51,200-char cap in
# agent.py::_run_tool_call).
_OFFLOAD_EXEMPT_TOOLS = frozenset({"read_file", "read_multiple"})


@dataclass
class CompressStats:
    """Per-instance counters — handy for tests and REPL diagnosis."""

    summarized_messages: int = 0
    squeezed_messages: int = 0
    offloaded_calls: int = 0
    last_offload_path: str | None = None
    summary_cache_hits: int = 0
    summary_llm: int = 0
    summary_extractive: int = 0


def _extractive_summary(messages: list[dict], max_chars: int) -> str:
    """Deterministic no-LLM fallback: first sentence per message,
    head+tail lines kept when over budget."""
    lines: list[str] = []
    for m in messages:
        role = str(m.get("role", "?"))
        text = (m.get("content") or "").strip()
        if not text:
            continue
        first = _SENT_SPLIT_RE.split(text, maxsplit=1)[0].strip()
        if len(first) > 160:
            first = first[:159] + "…"
        lines.append(f"{role}: {first}" if first else f"{role}: (…)")
    if not lines:
        return "(no earlier conversation)"
    while lines and sum(len(l) + 1 for l in lines) > max_chars and len(lines) > 2:
        lines.pop(len(lines) // 2)
    return "\n".join(lines)


def _serialize_for_summary(messages: list[dict]) -> str:
    """Render the folded messages for the summarizer prompt with
    hard caps so one giant message cannot blow up the call."""
    per_msg = 1200
    chunks: list[str] = []
    for m in messages:
        c = (m.get("content") or "").strip()
        if len(c) > per_msg:
            c = (
                c[: per_msg // 2]
                + "\n…[middle truncated]…\n"
                + c[-(per_msg // 4):]
            )
        chunks.append(f"{m.get('role', '?')}: {c}")
    out = "\n\n".join(chunks)
    total_cap = 24000
    if len(out) > total_cap:
        out = (
            out[: total_cap // 2]
            + "\n…[middle truncated]…\n"
            + out[-(total_cap // 4):]
        )
    return out


_URL_KEEP_RE = re.compile(r"https?://[^\s\)\]\」』）]+")


def _append_omitted_urls(original: str, out: str) -> str:
    """v0.9.133.17 — multi-turn fetch continuity: URLs squeezed out of
    the middle are re-attached verbatim at the end so the NEXT turn
    still has the exact link it re-uses (the target-drift lesson:
    multi-article fetches drifted to the wrong post until reminded)."""
    kept_now = set(_URL_KEEP_RE.findall(out))
    missing: list[str] = []
    for u in _URL_KEEP_RE.findall(original):
        if u not in kept_now and u not in missing:
            missing.append(u)
    if not missing:
        return out
    return out + (
        "\nURLs present in the omitted part (verbatim):\n"
        + "\n".join(missing[:8])
    )


class ContextCompressor:
    """Stateful (summary cache + counters) but config-late-bound.

    ``compress_history`` is a pure transform over the already-filtered
    user/assistant history; ``offload_tool_result`` wraps tool call
    results. Both are no-ops when the feature is off.
    """

    def __init__(self) -> None:
        # oldest-first LRU of old-prefix-hash → summary text
        self._summary_cache: "OrderedDict[str, str]" = OrderedDict()
        self._offload_seq = 0
        self.stats = CompressStats()

    # ─── config (os.environ direct, monkeypatch-friendly) ──────
    @staticmethod
    def _cfg() -> SimpleNamespace:
        def _i(env: str, default: int) -> int:
            try:
                return int(str(os.environ.get(env, "")).strip() or default)
            except Exception:
                return default

        def _s(env: str, default: str) -> str:
            return str(os.environ.get(env, "") or "").strip().lower() or default

        mode = _s("CHAT_CONTEXT_COMPRESSION", "off")
        # v0.9.104.3 — 「on」是使用者直覺值（live .env 一直寫 on）：把它
        # 當 auto 的別名，不再整組靜默失效（live 事故：Gmail 整頁 HTML
        # 的 get_page_html 結果 1.75M tokens 直接進對話 → 全 provider
        # 40x）。true / yes / 1 同義。
        if mode in ("on", "yes", "true", "1"):
            mode = "auto"
        elif mode not in _MODES:
            mode = "off"
        smode = _s("CHAT_COMPRESS_SUMMARY_MODE", "llm")
        if smode not in ("llm", "extractive"):
            smode = "llm"
        trigger = _i("CHAT_COMPRESS_TRIGGER_MESSAGES", 24)
        keep = _i("CHAT_COMPRESS_KEEP_MESSAGES", 12)
        msg_max = _i("CHAT_COMPRESS_MSG_MAX_CHARS", 4000)
        if mode == "aggressive":
            # tighter budgets — squeeze sooner, squeeze harder
            trigger = max(4, trigger // 2)
            msg_max = max(400, msg_max // 2)
        return SimpleNamespace(
            mode=mode,
            trigger=max(6, trigger),
            keep=max(2, keep),
            summary_mode=smode,
            summary_max=max(
                200,
                _i("CHAT_COMPRESS_SUMMARY_MAX_CHARS", 900),
            ),
            msg_max=max(400, msg_max),
            offload_threshold=max(
                500, _i("CHAT_OFFLOAD_THRESHOLD_CHARS", 20000)
            ),
            offload_dir=str(
                os.environ.get("QDRANT_OFFLOAD_DIR", "") or ""
            ),
        )

    # ─── 1. history summarization ───────────────────────────────
    def compress_history(
        self,
        history: list[dict] | None,
        summarizer=None,
    ) -> list[dict]:
        """Return the message list to send to the LLM.

        ``history`` holds user/assistant dicts (already role-filtered
        by the caller). Disabled → an identical copy of the input.
        """
        cfg = self._cfg()
        msgs = list(history or [])
        if cfg.mode == "off" or not msgs:
            return msgs
        # Technique 3 — oversized single messages get a structure-
        # preserving prose squeeze in BOTH enabled modes.
        msgs = [self._squeeze_message(m, cfg) for m in msgs]
        if len(msgs) <= cfg.trigger:
            return msgs
        keep = min(cfg.keep, len(msgs) - 1)
        old, recent = msgs[:-keep], msgs[-keep:]
        summary = self._summary_for(old, summarizer, cfg)
        self.stats.summarized_messages = len(old)
        block = (
            f"[Auto-compacted earlier conversation — {len(old)} "
            "messages condensed into the summary below.]\n"
            f"{summary}\n"
            f"[The {len(recent)} most recent messages follow verbatim.]"
        )
        return [{"role": "system", "content": block}] + recent

    def _summary_for(self, old: list[dict], summarizer, cfg) -> str:
        key = hashlib.sha1(
            "\n".join(
                f"{m.get('role')}|{m.get('content', '')}" for m in old
            ).encode("utf-8")
        ).hexdigest()
        cached = self._summary_cache.get(key)
        if cached is not None:
            self.stats.summary_cache_hits += 1
            self._summary_cache.move_to_end(key)
            return cached
        text = ""
        if cfg.summary_mode == "llm" and callable(summarizer):
            try:
                text = str(
                    summarizer(_serialize_for_summary(old), cfg.summary_max)
                    or ""
                ).strip()
            except Exception:
                text = ""
        if text:
            self.stats.summary_llm += 1
        else:
            text = _extractive_summary(old, cfg.summary_max)
            self.stats.summary_extractive += 1
        if len(text) > cfg.summary_max:
            text = text[: cfg.summary_max - 1] + "…"
        self._summary_cache[key] = text
        while len(self._summary_cache) > 8:
            self._summary_cache.popitem(last=False)
        return text

# ─── 3. selective (semantic) compression ────────────────────
    def _squeeze_message(self, m: dict, cfg: SimpleNamespace) -> dict:
        if m.get("role") in ("system", "tool"):
            return m
        content = m.get("content") or ""
        if len(content) <= cfg.msg_max:
            return m
        squeezed = self._selective_compress(content, cfg.msg_max)
        if squeezed == content:
            return m
        out = dict(m)
        out["content"] = squeezed
        self.stats.squeezed_messages += 1
        return out

    def _selective_compress(self, text: str, budget: int) -> str:
        """Keep structured data verbatim, squeeze the prose around it."""
        parts = self._split_structured(text)
        prose = [(k, t) for k, t in parts if k == "prose"]
        if not prose:
            return text  # all structured → nothing we may touch
        structured = sum(len(t) for k, t in parts if k != "prose")
        prose_total = sum(len(t) for _, t in prose)
        if structured + prose_total <= budget:
            return text
        # If structured data alone exceeds the budget it still stays
        # verbatim — only prose pays the compression cost.
        prose_budget = max(200, (budget - structured) // len(prose))
        out: list[str] = []
        for kind, chunk in parts:
            if kind == "prose":
                out.append(self._squeeze_prose(chunk, prose_budget))
            else:
                out.append(chunk)
        return "".join(out)

    def _split_structured(self, text: str) -> list[tuple[str, str]]:
        """Split into (fence|json|prose|sep, chunk) preserving
        verbatim structured blocks."""
        parts: list[tuple[str, str]] = []
        pos = 0
        for m in _FENCE_RE.finditer(text):
            if m.start() > pos:
                parts.extend(
                    self._split_json_paragraphs(text[pos : m.start()])
                )
            parts.append(("fence", m.group(0)))
            pos = m.end()
        tail = text[pos:]
        if tail:
            parts.extend(self._split_json_paragraphs(tail))
        return parts

    def _split_json_paragraphs(self, seg: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        tokens = re.split(r"(\n\s*\n)", seg)
        for i, tok in enumerate(tokens):
            if i % 2 == 1:
                out.append(("sep", tok))
            elif tok.strip() and self._looks_like_json(tok):
                out.append(("json", tok))
            elif tok:
                out.append(("prose", tok))
        return out

    @staticmethod
    def _looks_like_json(s: str) -> bool:
        t = s.strip()
        if not t.startswith(("{", "[")):
            return False
        try:
            json.loads(t)
            return True
        except Exception:
            # Conservative fallback: a {/[ … }/] block is kept verbatim
            # rather than risk corrupting structured data.
            return t.endswith(("}", "]"))

    def _squeeze_prose(self, s: str, budget: int) -> str:
        if len(s) <= budget:
            return s
        s_norm = re.sub(r"[ \t]+\n", "\n", s)
        s_norm = re.sub(r"\n{3,}", "\n\n", s_norm)
        if len(s_norm) <= budget:
            return s_norm
        sentences = [
            x for x in _SENT_SPLIT_RE.split(s_norm) if x and x.strip()
        ]
        if len(sentences) <= 1:
            head = s_norm[: max(120, int(budget * 0.6))]
            tail = (
                s_norm[-max(80, budget // 4):]
                if len(s_norm) > budget + 300
                else ""
            )
            omitted = max(0, len(s_norm) - len(head) - len(tail))
            return _append_omitted_urls(
                s_norm,
                head
                + f"\n…[prose compressed: {omitted} chars omitted]…\n"
                + tail,
            )
        head_budget = max(120, int(budget * 0.6))
        tail_budget = max(60, int(budget * 0.25))
        head, head_chars, i = [], 0, 0
        while (
            i < len(sentences)
            and head_chars + len(sentences[i]) <= head_budget
        ):
            head.append(sentences[i])
            head_chars += len(sentences[i])
            i += 1
        tail, tail_chars, j = [], 0, len(sentences)
        while j > i and tail_chars + len(sentences[j - 1]) <= tail_budget:
            tail.insert(0, sentences[j - 1])
            tail_chars += len(sentences[j - 1])
            j -= 1
        if not head or not tail:
            head = sentences[:1]
            tail = sentences[-1:]
            i, j = 1, len(sentences) - 1
        omitted = sum(len(x) for x in sentences[i:j])
        return _append_omitted_urls(
            s_norm,
            "".join(head)
            + f"\n…[prose compressed: {omitted} chars omitted]…\n"
            + "".join(tail),
        )

    # ─── 2. tool-result offloading ──────────────────────────────
    def offload_tool_result(
        self, tool_name: str, text: str
    ) -> tuple[str, str | None]:
        """Return ``(inline_text, offloaded_path_or_None)``.

        Successful tool results over ``CHAT_OFFLOAD_THRESHOLD_CHARS``
        are written to a file (inside the read sandbox by default) and
        replaced by a pointer + preview. Write failures degrade to the
        inline text — a tool call must never break because of this.
        """
        cfg = self._cfg()
        if cfg.mode == "off" or not text or len(text) <= cfg.offload_threshold:
            return text, None
        if (tool_name or "").strip() in _OFFLOAD_EXEMPT_TOOLS:
            # v0.9.107.0 — exempt (see _OFFLOAD_EXEMPT_TOOLS): stays
            # inline, bounded by the agent-side absolute cap instead.
            return text, None
        path = self._write_offload(tool_name, text)
        if path is None:
            return text, None
        self.stats.offloaded_calls += 1
        self.stats.last_offload_path = str(path)
        preview = text[:_PREVIEW_CHARS]
        pointer = (
            f"[offloaded] Tool '{tool_name}' returned {len(text)} chars "
            f"(over the {cfg.offload_threshold}-char inline budget). "
            f"Full output saved to: {path}\n"
            f"Preview (first {len(preview)} chars):\n{preview}\n"
            "…[truncated]\n"
            "If you need the full content, call the filesystem "
            "``read_file`` tool with the absolute path above (it lives "
            "inside the read sandbox). Do NOT re-run the tool just to "
            "see what was truncated — read the file."
        )
        return pointer, str(path)

    def _offload_dir(self) -> Path:
        env_dir = self._cfg().offload_dir
        if env_dir:
            root = Path(env_dir)
        else:
            base = (
                os.environ.get("QDRANT_FILESYSTEM_READ_ROOT")
                or os.environ.get("WATCH_DIR")
                or "./documents"
            )
            root = Path(base) / "chat_offloads"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _write_offload(self, tool_name: str, text: str):
        try:
            d = self._offload_dir()
            self._offload_seq += 1
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", tool_name or "tool")[:40]
            safe = safe or "tool"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            p = d / f"{ts}_{self._offload_seq:04d}_{safe}.txt"
            p.write_text(text, encoding="utf-8")
            return p
        except Exception:
            return None

