# -*- coding: utf-8 -*-
"""session_compactor — T+1 batch summaries of persisted chat sessions
into the long-term memory (v0.9.133.3, kind=``session-summary``).

Inspired by the UniiChat memory-tree study
(``uploads/uniichat_memory_report.md`` §6, user-approved plan ② for
this codebase): UniiChat maintains a CONTINUOUS per-message merge tree
inside one never-ending chat; THIS project is a fleet of DISCRETE
sessions, so the same goal — "old conversations stay recallable" — is
reached with a daily BACKGROUND batch:

    scheduler fires → the persisted session files modified within the
    window are LLM-compressed ONE call each into 1-3 lines and stored
    into ``memory_docs`` (kind=session-summary) with a session_id
    back-link (source=+session_id=) — recallable semantics, verified by
    an exact payload read-back before moving on.

RECALL CONTRACT (deliberate, v0.9.133.3): ``session-summary`` rows are
NOT excluded from the auto-recall (``Agent._memory_block``) — summary
lines are prose about past conversations, not stale numeric snapshots.
ONLY ``task-result`` stays store-NOT-recall (the v0.9.67.2 /
v0.9.111.0 stale-snapshot lesson).  Raw sessions remain reachable via
``search_chat_history`` / ``get_chat_session`` (chat-history-local);
the summary is the recall layer, the session file is the ground truth.

Idempotency: a session id already present in the memory (any point
with kind=session-summary and source==sid, exact payload match, no
embedding involved) is SKIPPED — re-running a window never duplicates.

Cost posture: one cheap LLM call per session (default pin
``ollama:qwen2.5:7b``, CPU inference, 32 GB RAM — the report §6.2;
gpt-oss:20b was suggested there but is NOT installed locally).
Every failure is per-session best-effort; the module NEVER raises on
the fire path (scheduler resilience mirrors the memory-maintenance
schedule, v0.9.127.0).

Env vars:
* ``QDRANT_SESSION_SUMMARY_SINK`` — off/0/no/false disables every
  entry point (default on)
* ``QDRANT_SESSION_SUMMARY_LLM`` — default model pin for the CLI /
  boot-seeded schedule (default ``ollama:qwen2.5:7b``)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

SESSION_SUMMARY_KIND = "session-summary"
_SCHEDULE_NAME = "每日對話摘要沉澱（T+1）"

# Machine-created sessions are maintenance records (schedule fires,
# detached background tasks, webhook runs) — summarising them adds
# noise, not recallable context. chat-history-local hides ``sched-``
# for the same reason (v0.9.21.0).
_SESSION_EXCLUDED_PREFIXES = ("sched-", "task-", "webhook-")

# Transcript budget per session (chars): user lines carry more signal
# (the actual questions/requests) than assistant prose.
_TOTAL_CAP = 12_000
_USER_CAP = 600
_ASST_CAP = 400

MAX_SESSIONS_DEFAULT = 50


def session_summary_sink_enabled() -> bool:
    v = (os.environ.get("QDRANT_SESSION_SUMMARY_SINK", "on") or "on"
         ).strip().lower()
    return v not in ("off", "0", "no", "false")


def default_llm_pin() -> str:
    """Model pin for one-shot compaction calls. The report §6.2 named
    ``ollama:gpt-oss:20b`` — which is NOT installed on this box (live
    ``/api/tags`` only has qwen3-embedding:4b + qwen2.5:7b), so the
    default is its local fallback ``ollama:qwen2.5:7b``. Empty string
    = the default provider chain (no pin)."""
    return (os.environ.get("QDRANT_SESSION_SUMMARY_LLM", "")
            or "ollama:qwen2.5:7b").strip()


def _local_ollama_url() -> str:
    """The LOCAL ollama server for compactor calls — deliberately NOT
    ``OLLAMA_BASE_URL`` (that key is the chat/vision chain's CLOUD
    endpoint, default ``https://ollama.com/v1``; pointing it at the
    local box would rip the cloud models out of the main chains). The
    compactor wants the cheap LOCAL inference pass, so it gets its
    own knob."""
    return (os.environ.get("QDRANT_SESSION_SUMMARY_OLLAMA_URL", "")
            or "http://127.0.0.1:11434/v1").strip().rstrip("/")


def _local_llamacpp_url() -> str:
    """The LOCAL llama.cpp chat server for compactor calls (v0.9.133.3:
    the qwen2.5 summary pass runs CPU-only on :8090 —
    ``C:\\llama.cpp\\start-llamacpp-chat-cpu.bat`` — deliberately OFF
    the AMD GPU that hosts the two embedding servers, per user).
    Same local-vs-cloud rationale as ``_local_ollama_url``."""
    return (os.environ.get("QDRANT_SESSION_SUMMARY_LLAMACPP_URL", "")
            or "http://127.0.0.1:8090/v1").strip().rstrip("/")


def _direct_local_call(base_url: str, api_key: str, model: str,
                       prompt: str) -> str:
    """One direct OpenAI-compatible round against a LOCAL server
    (llama.cpp :8090 / ollama :11434). Same single-shot shape as
    ``Agent._summarize_history``. Returns "" on failure."""
    if not (base_url and model):
        return ""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _COMPACTOR_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=400,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 — per-session best effort
        return ""


def _ollama_direct_call(model: str, prompt: str) -> str:
    """One direct round against the LOCAL ollama server (the pinned
    ollama model does not exist on the cloud endpoint — live 404
    proof). Returns "" on failure."""
    return _direct_local_call(
        _local_ollama_url(),
        os.environ.get("OLLAMA_API_KEY", "") or "ollama",
        model, prompt)


def _llamacpp_direct_call(model: str, prompt: str) -> str:
    """One direct round against the LOCAL llama.cpp chat server
    (OpenAI-compatible /v1/chat/completions; no auth required — the
    api key is a placeholder token). Returns "" on failure."""
    return _direct_local_call(
        _local_llamacpp_url(), "llama.cpp", model, prompt)
def _load_json(p: Path) -> Any:
    import json

    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _iter_recent_sessions(
    days: float = 1.0, *, now: float | None = None, manager: Any = None,
) -> list[dict[str, Any]]:
    """Persisted chat sessions whose file mtime falls inside the window
    ``[now - days*86400, now)``. Machine prefixes are excluded; corrupt
    files are skipped (report-side only)."""
    import time as _time

    from src.agent.session import SessionManager

    now = float(now if now is not None else _time.time())
    win_since = now - max(0.05, float(days)) * 86400.0
    mgr = manager or SessionManager()
    out: list[dict[str, Any]] = []
    for p in mgr.root.glob("*.json"):
        sid = p.stem
        if sid.startswith(_SESSION_EXCLUDED_PREFIXES):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if not (win_since <= mtime <= now):
            continue
        try:
            d = _load_json(p)
        except Exception:  # noqa: BLE001 — corrupt json = skip
            continue
        if not isinstance(d, dict):
            continue
        out.append({
            "sid": sid,
            "title": str(d.get("title") or ""),
            "messages": list(d.get("messages") or []),
            "mtime": mtime,
        })
    out.sort(key=lambda r: r["mtime"])  # oldest first
    return out


def _transcript(
    messages: list, *, total_cap: int = _TOTAL_CAP,
    user_cap: int = _USER_CAP, asst_cap: int = _ASST_CAP,
) -> str:
    """Compact chat transcript for the compactor prompt (user lines
    fuller than assistant prose; global hard cap)."""
    rows: list[str] = []
    used = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip() or "?"
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        cap = user_cap if role == "user" else asst_cap
        if content.startswith("[error]"):
            content = content[:200]
        row = f"{role}: {content[:cap]}"
        if used + len(row) > total_cap:
            rows.append("…[transcript truncated]")
            break
        rows.append(row)
        used += len(row) + 1
    return "\n".join(rows).strip()


_COMPACTOR_SYSTEM = (
    "你是對話摘要員。把以下「一次聊天對話」壓成 1-3 行（合計≤300字）："
    "保留討論的主題、做的決定、重要事實/數字/檔名/人名/關鍵術語與未解"
    "問題；省略寒暄、重複、過場。只用摘要本身回答——不要前言、不要"
    "註解、不要標題。"
)


def _default_llm_call(prompt: str, llm: str | None = None) -> str:
    """One cheap LLM call for ONE session summary.

    Routing (v0.9.133.3, live-404 lesson): ``llamacpp:<model>`` and
    ``ollama:<model>`` pins go DIRECT to their LOCAL servers
    (``QDRANT_SESSION_SUMMARY_LLAMACPP_URL`` default :8090 CPU-only /
    ``QDRANT_SESSION_SUMMARY_OLLAMA_URL`` default :11434) — NOT the
    ``Agent(task_llm=…)`` chain, because agent's ollama branch uses
    ``OLLAMA_BASE_URL`` (the chat chain's CLOUD endpoint, default
    ``https://ollama.com/v1``) where local model names 404. Any other
    pin rides the pinned ``Agent`` (``_create_validated`` single-shot,
    modeled on ``Agent._summarize_history``). Returns "" on ANY
    failure.

    v0.9.133.11: when the CALLER passes no ``llm`` (or a junk one),
    fall back to the ``QDRANT_SESSION_SUMMARY_LLM`` env pin (.env is
    the single source of truth) — the 15:30 live fire had the puppy
    LLM invent its own ``llm`` argument and every session silently
    degraded to the cloud default chain. Bare model names WITHOUT the
    ``provider:`` prefix are treated as invalid pins and ALSO fall
    back to the env value."""
    pin = (llm or "").strip()
    if pin and (":" not in pin or pin.split(":", 1)[0].lower() not in (
            "llamacpp", "ollama", "openrouter", "openai", "openai-compat",
            "siliconflow", "anthropic", "google")):
        pin = ""  # junk pin (bare model name etc.) — ignore it
    if not pin:
        pin = (os.environ.get("QDRANT_SESSION_SUMMARY_LLM", "") or "").strip()
    if pin.startswith("llamacpp:") and len(pin) > len("llamacpp:"):
        return _llamacpp_direct_call(pin.split(":", 1)[1], prompt)
    if pin.startswith("ollama:") and pin.count(":") >= 2:
        return _ollama_direct_call(pin.split(":", 1)[1], prompt)
    try:
        from src.agent.agent import Agent

        agent = Agent(task_llm=pin) if pin else Agent()
        _ = agent.client  # resolve the provider chain FIRST (sets
        # agent._model — the lazy property; _summarize_history does
        # the same thing before _create_validated)
        resp, ok = agent._create_validated(
            model=agent._model,
            messages=[
                {"role": "system", "content": _COMPACTOR_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=400,
        )
        if not ok:
            return ""
        return (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 — per-session best effort
        return ""


# ─── main entry ─────────────────────────────────────────────────────
def summarize_sessions(
    days: float = 1.0,
    *,
    llm: str | None = None,
    dry_run: bool = False,
    store: Any = None,
    llm_call: Callable | None = None,
    now: float | None = None,
    max_sessions: int = MAX_SESSIONS_DEFAULT,
    manager: Any = None,
) -> dict:
    """T+1 batch: compress recent sessions into ``memory_docs`` /
    kind=session-summary (one LLM call per session). Idempotent,
    per-session best-effort, never raises. ``llm_call`` is the test
    seam (prompt → summary text; "" = failure)."""
    if not session_summary_sink_enabled():
        return {
            "ok": False,
            "error": ("session-summary sink is disabled "
                      "(QDRANT_SESSION_SUMMARY_SINK=off)"),
        }
    import time as _time

    from src.agent.memory_store import _shared_store

    st = store or _shared_store()
    llm_call = llm_call  # test seam; None → real single-shot call below
    now = float(now if now is not None else _time.time())
    scanned = _iter_recent_sessions(
        days, now=now, manager=manager,
    )[: max(1, int(max_sessions))]

    already: set[str] = set()
    already_error: str | None = None
    try:
        for row in st.recent(kind=SESSION_SUMMARY_KIND, limit=500):
            src = str(row.get("source") or "")
            if src:
                already.add(src)
    except Exception as e:  # noqa: BLE001 — degrade to re-summarize
        already_error = repr(e)[:200]

    skipped_existing = [r["sid"] for r in scanned if r["sid"] in already]
    todo = [r for r in scanned if r["sid"] not in already]

    failed: list[dict] = []
    summaries: list[dict] = []
    for r in todo:
        prompt = (
            f"對話（id={r['sid']}，標題={r['title'] or '(untitled)'}）:\n"
            + _transcript(r["messages"])
        )
        try:
            summary = (
                llm_call(prompt) if llm_call
                else _default_llm_call(prompt, llm)
            )
        except Exception as e:  # noqa: BLE001 — per-session best effort
            failed.append({"sid": r["sid"], "error": repr(e)[:200]})
            continue
        if not (summary or "").strip():
            failed.append({"sid": r["sid"],
                           "error": "LLM returned an empty summary"})
            continue
        if dry_run:
            summaries.append({"sid": r["sid"],
                              "would_write": summary[:200]})
            continue
        try:
            st.remember(
                text=(f"session {r['sid']}「{r['title'] or '(untitled)'}」"
                      f"的對話摘要：\n{summary}")[:4000],
                kind=SESSION_SUMMARY_KIND,
                source=r["sid"],
                session_id=r["sid"],
            )
        except Exception as e:  # noqa: BLE001
            failed.append({"sid": r["sid"], "error": repr(e)[:200]})
            continue
        # §6.3 — verify by an exact payload read-back BEFORE moving on;
        # a silent-write loss stops the batch honestly.
        try:
            got = _readback(st, r["sid"])
        except Exception as e:  # noqa: BLE001 — read failure is not fatal
            summaries.append({"sid": r["sid"],
                              "verify": f"ERROR {repr(e)[:120]}"})
            continue
        if not got:
            return {
                "ok": False,
                "error": (f"write verification failed for session "
                          f"{r['sid']} — stopped early"),
                "days": days, "scanned": len(scanned),
                "summarized": len(summaries),
                "skipped_existing": len(skipped_existing),
                "failed": failed, "summaries": summaries,
                "stopped_early": True,
                "kind": SESSION_SUMMARY_KIND,
            }
        summaries.append({"sid": r["sid"], "summary": summary[:200]})

    out = {
        "ok": not failed,
        "days": days,
        "scanned": len(scanned),
        "summarized": len(summaries),
        "skipped_existing": len(skipped_existing),
        "failed": failed,
        "summaries": summaries,
        "kind": SESSION_SUMMARY_KIND,
    }
    if not dry_run and summaries and not failed:
        out["verified"] = True
    if already_error:
        out["idempotency_scan_error"] = already_error
    return out


def _readback(store: Any, sid: str) -> list[dict]:
    """Exact-payload verification that a session now has a summary row
    (``recent(kind)`` + source match — no embedding involved)."""
    for row in store.recent(kind=SESSION_SUMMARY_KIND, limit=500):
        if str(row.get("source") or "") == sid:
            return [row]
    return []


# ─── boot-seed schedule prompt (gui/api.py) ─────────────────────────
SCHEDULE_PROMPT = (
    "每日對話摘要沉澱（T+1 自動排程）：\n"
    "1. 呼叫 summarize_sessions（days=1）。\n"
    "2. 回報結果：摘要寫入幾個對話、略過幾個已寫過；有 failed 就逐條\n"
    "   誠實列出（session id + 原因）。\n"
    "3. 把每個新摘要的第一行附上。\n"
    "4. 不要呼叫 purge_memories / forget_memory —— 摘要是歷史紀錄；\n"
    "   記憶功能關閉時誠實回報，不要硬試。"
)


def main(argv: list[str] | None = None) -> int:
    """CLI thin layer — ``python -m src.agent.session_compactor``."""
    import argparse
    import json

    ap = argparse.ArgumentParser(
        prog="session_compactor",
        description=(f"T+1 batch summaries of chat sessions into the "
                     f"long-term memory (kind={SESSION_SUMMARY_KIND})"))
    ap.add_argument("--days", type=float, default=1.0,
                    help="look-back window in days (default 1)")
    ap.add_argument("--llm", default=None,
                    help="model pin provider:model (default: "
                         "QDRANT_SESSION_SUMMARY_LLM or ollama:qwen2.5:7b)")
    ap.add_argument("--max", type=int, default=MAX_SESSIONS_DEFAULT,
                    dest="max_sessions")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    llm = args.llm if args.llm else (default_llm_pin() or None)
    r = summarize_sessions(
        days=args.days, llm=llm, dry_run=args.dry_run,
        max_sessions=args.max_sessions,
    )
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())