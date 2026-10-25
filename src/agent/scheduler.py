"""Schedule manager — run prompts on cron / interval / one-shot triggers.

Each schedule lives in ``$QDRANT_RAG_HOME/schedules/<id>.json`` (atomic
write via tmp + os.replace, mirroring ``SessionManager``). On every
fire, the scheduler runs the prompt through ``Agent.chat_turn`` with
the full tool-calling surface available (``use_tools=True``,
``use_web_search`` per-schedule), then:

1. Appends the ``{user, assistant}`` turn to a dedicated session
   named ``sched-<id>-<fire-ts>``. Users can open that session in
   the Sessions tab to read the full conversation history.
2. Writes a compact ``{"ts","prompt","answer","session_id"}`` line
   to ``schedules/<id>.history.jsonl`` (ring-buffered to the last
   ``MAX_HISTORY`` entries).

Three schedule types are supported:

* ``"once_at"`` — fires at a single ISO-8601 timestamp, then
  auto-disables itself.
* ``"cron"`` — standard 5-field cron (``* * * * *`` = minute hour
  dom month dow). Day-of-week is 0-6 (Sun=0).
* ``"interval_seconds"`` — fires every N seconds.

A daemon thread ticks once a second checking each schedule. Exit
cleanly via ``Scheduler.stop()`` (called from ``Agent.shutdown``).
Crash-safe: a crash mid-tick is recovered by reading the on-disk
``next_fire_at`` next time.

This module is independent of MCP / web-search plumbing — the Agent
itself decides which tools to use.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─── Persistence paths ────────────────────────────────────────


def _schedules_dir() -> Path:
    """Default location: ``$QDRANT_RAG_HOME/schedules`` (or
    ``~/.qdrant-rag-agent/schedules`` on Windows/macOS/Linux)."""
    base = Path(
        os.environ.get(
            "QDRANT_RAG_HOME", Path.home() / ".qdrant-rag-agent"
        )
    )
    p = base / "schedules"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ─── v0.9.67.1 — schedule-fire context + telegram-send guard ─────

def _fire_history_limit() -> int:
    """``QDRANT_SCHEDULE_FIRE_HISTORY`` — how many PRIOR messages of
    the reused ``sched-<id>`` session are passed as ``history`` to
    each fire.

    Live log 2026-09-16 15:50: a cron schedule whose session had
    accumulated 14 fires (30 messages) produced a fabricated reply —
    the LLM skipped EVERY tool call (no web_search, no
    send_telegram_message) and instead cloned the historical reply
    shape, claiming "✅ 已成功發送至 Telegram (訊息長度 1,024 字元)"
    with ZERO dispatches. Repeating a fixed prompt over a growing
    history teaches the model to parrot the transcript instead of
    doing the work again.

    Default **0** = every fire is an INDEPENDENT run (the session
    file still accumulates the full transcript for the record; only
    the LLM context is fresh). ``2`` keeps one prior Q/A pair for
    lightweight continuity; ``-1`` restores the legacy full history.
    Bad values fall back to the default with a warning.
    """
    raw = (os.environ.get("QDRANT_SCHEDULE_FIRE_HISTORY", "") or "").strip()
    if not raw:
        return 0
    try:
        v = int(raw)
    except ValueError:
        logger.warning(
            "[scheduler] QDRANT_SCHEDULE_FIRE_HISTORY=%r is not a "
            "number — falling back to 0 (independent fires)", raw,
        )
        return 0
    return v


# A fire whose prompt asks for a Telegram send AND whose reply claims
# the send happened — but no ``send_telegram_*`` tool was dispatched.
# v0.9.103.2 — the live 2026-09-29 14:00 fire evaded the old regex:
# the flash model's answer said 「整理分析後發送 Telegram」 /
# 「現在發送 Telegram」 (FUTURE/announce shapes with NO 到/至/已
# prefix) → no match → no corrective turn → nothing was ever sent.
# The widened set catches the future/announce shapes + the bare
# 發送/傳送-then-telegram adjacency; the negative list spares honest
# no-send replies (不發送 / 無需發送 …).
_CLAIM_RE = re.compile(
    r"(已發送|已送出|發送至|發送到|已傳送|已成功|發送成功|成功發送|"
    r"現在發送|即將發送|準備發送|待會發送|稍後發送|馬上發送|"
    r"(?:之後|接著|然後|再)?後發送|發送後|"
    r"發送\s*(?:telegram|tg)|傳送\s*(?:telegram|tg)|"
    r"(?:send(?:ing)?|sent)\s*(?:to|via|a)?\s*telegram)", re.I)
_CLAIM_NEG_RE = re.compile(
    r"不發送|無需發送|不需要發送|不送出|不用發送|不會發送|未發送")


def _telegram_intent(prompt: str) -> bool:
    return "telegram" in (prompt or "").lower()


# ─── v0.9.106.0 — P0 驗證前置化 ──────────────────────────────────
#
# 三守則過去只寫在 prompt 裡，LLM 可能跳過（歷史上已發生 8 次假成功）。
# v0.9.106.0 把驗證升級成「流程中硬擋」：
#   1. fire prompt 注入 VERIFICATION DIRECTIVE（結構化：執行 → 強制
#      驗證 → 如實回報）。
#   2. 事後機械守衛 — 回覆「宣稱成功」但「無任何驗證結論」→ 判為
#      未驗證，追加 ONE 糾正回合強制補驗證（不依賴 LLM 自律）。
#   3. history.jsonl / session 記錄 ``verified`` 三態旗標（True/
#      False/None = 未啟用驗證），供 get_scheduled_task_status /
#      list_scheduled_task_runs / GUI 判讀。

# Positive claims that LOOK like success (CJK + English). 「完成」
# alone is too weak (「分析完成（本地任務）。」 is an honest summary
# line, not a receipt) — it only counts in the 完成-of-SOMETHING
# concrete shapes (已發送/已存檔/已寫入 already carry the object).
_CLAIM_SUCCESS_RE = re.compile(
    r"已成功|成功完成|已發送|已送出|已寄出|已存檔|已寫入|已建立|"
    r"已產出|已達成|並存檔|順利達成|全部成功|一切順利|"
    r"success(?:ful)?|succeeded|completed successfully|"
    r"sent successfully|saved successfully",
    re.I,
)
# Honest failure markers — a reply that says it FAILED is NOT an
# unverified success claim (no corrective turn for those).
_CLAIM_FAILURE_RE = re.compile(
    r"失敗|錯誤|無法|未能|拒絕|中止|驗證失敗|"
    r"fail(?:ed|ure)|error|unable to|cannot|could not|refused",
    re.I,
)
# The verification step itself. The injected directive names these
# words explicitly, so their presence in the reply is machine-checkable
# evidence that the verification step RAN.
_VERIFY_EVIDENCE_RE = re.compile(
    r"verify_artifact|驗證(?:結果|通過|失敗|完畢|證據)|已驗證|"
    r"驗證步驟|驗證清單|get_chat_session(?:\s*回讀)?|"
    r"回執比對|核對(?:結果|完成)|verification (?:passed|failed|done)",
    re.I,
)


def _verification_directive(schedule_id: str = "") -> str:
    """The VERIFICATION DIRECTIVE appended to every fire prompt when
    ``Schedule.verify_required`` is True (v0.9.106.0 P0).

    Mirrors the improvement plan's structure: step N = run the task,
    step N+1 = a MANDATORY verification node; verification failure →
    abort + report the failure (never a success conclusion).
    """
    sid_note = (
        f"（本排程 id={schedule_id}，可用 get_chat_session 讀回本回合"
        f" session 的工具訊息）" if schedule_id else ""
    )
    return (
        "\n\n────────────────────────────────────────────\n"
        "VERIFICATION DIRECTIVE（排程流程強制節點，非建議）:\n"
        " 1. 執行任務本身（步驟 N）。\n"
        " 2. 執行後【必須】執行驗證步驟（步驟 N+1，強制，不可略過）:\n"
        "    - 對每個產出檔案呼叫 ``verify_artifact``（機械式檢查:\n"
        "      存在/大小/JSON 欄位），或\n"
        "    - 以 ``get_chat_session`` 回讀本 session 比對工具回執，\n"
        "      或以工具自身的成功回執（message_id / 檔名 / exit_code）\n"
        "      為準。" + sid_note + "\n"
        " 3. 驗證失敗或任何步驟失敗 → 立即中止，最終回覆必須以\n"
        "    「驗證失敗/執行失敗 + 原因」開頭，【禁止】產出任何成功\n"
        "    結論或「已成功/已完成」字樣。\n"
        " 4. 驗證通過後才允許寫總結報告；最終回覆需明確包含「驗證\n"
        "    通過/驗證結果」字樣與驗證依據。\n"
        "────────────────────────────────────────────"
    )


def _verification_guard_prompt(schedule_id: str) -> str:
    """The corrective prompt fired when a reply claims success with
    zero verification evidence (v0.9.106.0 P0 事後硬擋)."""
    return (
        "[系統驗證守衛] 你的上一則回覆宣稱任務成功，但沒有任何驗證"
        "證據（未呼叫 verify_artifact、未以 get_chat_session 回讀、"
        "也未引用工具回執）。根據本排程的驗證三守則，未經驗證不得"
        "宣稱成功。請現在立即補驗證：呼叫 verify_artifact 檢查你"
        "宣稱產出的檔案，或以 get_chat_session 回讀本回合的工具"
        "回執；驗證不通過就如實回報「驗證失敗 + 原因」，禁止維持"
        "任何成功結論。（排程 id=" + schedule_id + "）"
    )


def _has_success_claim(answer: str) -> bool:
    """True when the reply claims SUCCESS and is not an honest
    failure report (the failure list wins — a reply that says
    「驗證失敗」 is not an unverified success)."""
    if not answer:
        return False
    if _CLAIM_FAILURE_RE.search(answer):
        return False
    return bool(_CLAIM_SUCCESS_RE.search(answer))


def _has_verification_evidence(answer: str) -> bool:
    """True when the reply carries machine-checkable verification
    evidence: a verify_artifact call echo, an explicit 驗證/回執
    conclusion, or a get_chat_session read-back."""
    if not answer:
        return False
    return bool(_VERIFY_EVIDENCE_RE.search(answer))


def _unverified_success(answer: str) -> bool:
    """P0 post-fire machine gate: success claim + NO verification
    evidence = unverified (needs the corrective turn)."""
    return _has_success_claim(answer) and not _has_verification_evidence(
        answer
    )


def _verifier_alt_pin_enabled() -> bool:
    """v0.9.127.0 -「驗證者不能是發現者」做到底: the VERIFICATION
    corrective turn runs on a DIFFERENT provider:model so a same-源
    hallucination cannot re-verify its own fabrication.
    ``QDRANT_VERIFIER_ALT_PIN=off`` opts out."""
    v = os.environ.get("QDRANT_VERIFIER_ALT_PIN", "on") or "on"
    return str(v).strip().lower() not in ("off", "0", "no", "false")


def _alt_verifier_pin(agent: Any) -> str | None:
    """First configured FALLBACK candidate the task_llm pin accepts
    (provider whitelist = openrouter / ollama); ``None`` = no usable
    alternate — the corrective turn then reuses the firing agent
    exactly as before (single-provider installs lose nothing)."""
    try:
        cands = agent._provider_candidates()
    except Exception:
        return None
    for name, model, _client in (cands[1:] or []):
        if str(name) in ("openrouter", "ollama"):
            m = str(model or "").strip()
            if m:
                return "%s:%s" % (name, m)
    return None


def _dispatched_verify_artifact(agent: Any) -> bool:
    """True when the turn actually dispatched ``verify_artifact`` —
    the machine-check tool call is the strongest evidence, even when
    the prose omits the word 驗證. Accepts the same dispatch shapes
    as ``_dispatched_telegram_send`` (list[str] / dict / object)."""
    dispatches = getattr(agent, "last_tool_dispatches", None) or []
    for d in dispatches:
        if isinstance(d, str):
            name = d
        elif isinstance(d, dict):
            name = d.get("name")
        else:
            name = getattr(d, "name", "")
        if str(name or "") == "verify_artifact":
            return True
    return False


# Tools whose OWN response is the machine receipt for the turn's
# success claim: the verifier itself, the chat-history read-back,
# and the send tools (message_id 回執). A turn that dispatched one
# of these has machine evidence — the verification guard must not
# fire a redundant corrective on top of the telegram guard.
_RECEIPT_TOOL_NAMES = (
    "verify_artifact", "get_chat_session",
    "send_telegram_message", "send_telegram_markdown",
    "send_telegram_photo",
)


def _dispatched_receipt_tool(agent: Any) -> bool:
    for d in getattr(agent, "last_tool_dispatches", None) or []:
        if isinstance(d, str):
            name = d
        elif isinstance(d, dict):
            name = d.get("name")
        else:
            name = getattr(d, "name", "")
        if str(name or "") in _RECEIPT_TOOL_NAMES:
            return True
    return False


def _verified_flag(
    answer: str | None, error: Any, verify_required: bool,
    agent: Any = None,
) -> bool | None:
    """Three-state ``verified`` marker written into history.jsonl.

    * ``None``  — the schedule does not require verification (legacy
      behaviour; the flag is semantically absent).
    * ``True``  — the reply contains verification evidence OR a
      receipt tool (verify_artifact / get_chat_session /
      send_telegram_*) was actually dispatched OR the reply is an
      honest failure/neutral report (nothing claimed, nothing
      fabricated).
    * ``False`` — an unverified success claim slipped through the
      corrective turn (kept HONEST on purpose: the row must not be
      upgraded just because a correction failed).
    """
    if not verify_required:
        return None
    if error:
        return True  # an honest error row is already a failure report
    if not answer:
        return False
    if _has_verification_evidence(answer):
        return True
    if agent is not None and (
        _dispatched_verify_artifact(agent)
        or _dispatched_receipt_tool(agent)
    ):
        return True
    if not _has_success_claim(answer):
        return True  # neutral reply — nothing claimed, nothing faked
    return False


def _fire_verify_enabled() -> bool:
    """Global opt-out for the P0 verification flow.

    ``QDRANT_SCHEDULE_VERIFY`` — default **on**. ``off`` restores the
    legacy free-form fire prompt (no directive, no post-fire guard,
    no verified flag) for operators who want the old behaviour.
    Per-schedule ``Schedule.verify_required=False`` overrides this
    for a single schedule.
    """
    raw = (os.environ.get("QDRANT_SCHEDULE_VERIFY", "") or "").strip()
    return raw.lower() not in ("off", "false", "0", "no")


# ─── v0.9.106.0 — P1 機械式檢查（獨立評估器，零 LLM） ─────────────
#
# 「驗證者不能是發現者」：產出驗證用純 Python 機械式檢查（存在 /
# 大小 / JSON 欄位齊全 / 數值範圍），零成本、零幻覺。``verify_artifact``
# MCP 工具與排程 fire 的驗證節點共用這個引擎。

# Hard caps — a pathological verify request must never blow up memory.
_VERIFY_MAX_BYTES = 8 * 1024 * 1024
_VERIFY_MAX_JSON = 512 * 1024

_JSON_CHECK_TYPES = (
    "json_fields", "json_number", "json_array", "json_string",
)


def _resolve_verify_path(path_str: str) -> Path:
    """Resolve ``path_str`` against the filesystem sandboxes (relative
    paths anchor at the WRITE sandbox — the same convention the
    filesystem write tools use). Raises ``ValueError`` for paths
    outside every sandbox."""
    from src.mcp_client.host import _is_within, _resolve_filesystem_root

    p = Path(str(path_str or "")).expanduser()
    if not p.is_absolute():
        p = _resolve_filesystem_root("QDRANT_FILESYSTEM_WRITE_ROOT") / p
    p = p.resolve()
    roots: list[Path] = []
    for env_var in (
        "QDRANT_FILESYSTEM_WRITE_ROOT", "QDRANT_FILESYSTEM_READ_ROOT",
    ):
        try:
            roots.append(_resolve_filesystem_root(env_var))
        except Exception:
            pass
    if not any(_is_within(p, r) for r in roots):
        raise ValueError(
            f"path is outside every filesystem sandbox: {path_str!r}"
        )
    return p


def _unwrap_parent(payload: Any, name: str) -> set:
    """Best-effort dict keys at the parent path of ``name`` — used
    only to distinguish 'field explicitly null' from 'missing' in
    the default ``json_fields`` branch. Never raises."""
    try:
        parts = re.findall(r"[^.\[\]]+", name)
        cur = payload
        for part in parts[:-1]:
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        if isinstance(cur, dict):
            return set(cur.keys())
        return set()
    except Exception:
        return set()


def _check_requirement(req: Any, payload: Any) -> tuple[bool, str]:
    """Evaluate one machine-check requirement against the loaded
    artifact payload. Returns ``(ok, detail)``. Unknown requirement
    names are REJECTED (fail-closed) so a typo can never silently
    pass verification."""
    if not isinstance(req, dict):
        return False, (
            f"requirement must be an object, got {type(req).__name__}"
        )
    name = req.get("field") or ""
    typ = str(req.get("type") or "").lower()
    # Fail-closed FIRST: an unknown check type never navigates, so a
    # typo can never silently pass verification (and the error message
    # must not be masked by a navigation failure on a None payload).
    _KNOWN_TYPES = (
        "exists", "nonempty", "json_fields", "json_number",
        "json_array", "json_string", "file_exists",
    )
    if typ and typ not in _KNOWN_TYPES:
        return False, f"unknown check type: {typ!r}"
    try:
        # ``a.b.c`` / ``a[0].b`` dotted navigation into nested payloads.
        cur: Any = payload
        if name:
            for part in re.findall(r"[^.\[\]]+", name):
                if isinstance(cur, list):
                    cur = cur[int(part)]
                elif isinstance(cur, dict):
                    if part not in cur:
                        return False, f"missing field: {name}"
                    cur = cur[part]
                else:
                    return False, (
                        f"cannot navigate {name!r} into "
                        f"{type(cur).__name__}"
                    )
    except (KeyError, IndexError, ValueError, TypeError) as e:
        return False, f"field {name!r} navigation failed: {e}"

    if typ == "exists":
        return True, (
            f"field {name!r} present" if name else "artifact loaded"
        )
    if typ == "nonempty":
        ok = (isinstance(cur, str) and cur.strip() != "") or bool(cur)
        return ok, (
            f"field {name!r} nonempty" if ok
            else f"field {name!r} is empty/missing"
        )
    if typ == "json_fields" or not typ:
        # Default check: the field (or payload root) must simply be
        # present. Extra ``required`` sub-list = nested keys.
        if name and cur is None and name not in _unwrap_parent(
            payload, name
        ):
            return False, f"missing field: {name}"
        missing = [
            f for f in (req.get("required") or [])
            if not isinstance(cur, dict) or f not in cur
        ]
        if missing:
            return False, (
                f"missing sub-fields under {name or 'root'}: {missing}"
            )
        return True, (
            f"field {name!r} + required sub-fields present" if name
            else "required sub-fields present"
        )
    if typ == "json_number":
        if isinstance(cur, bool) or not isinstance(cur, (int, float)):
            return False, f"field {name!r} is not a number"
        mn = req.get("min")
        mx = req.get("max")
        if mn is not None and cur < mn:
            return False, f"field {name!r}={cur} < min={mn}"
        if mx is not None and cur > mx:
            return False, f"field {name!r}={cur} > max={mx}"
        return True, f"field {name!r}={cur} ok"
    if typ == "json_array":
        if not isinstance(cur, list):
            return False, f"field {name!r} is not an array"
        mn = req.get("min_length")
        if mn is not None and len(cur) < mn:
            return False, (
                f"field {name!r} has {len(cur)} items < min_length={mn}"
            )
        return True, f"field {name!r} array len={len(cur)} ok"
    if typ == "json_string":
        if not isinstance(cur, str):
            return False, f"field {name!r} is not a string"
        pat = req.get("pattern")
        if pat and not re.search(pat, cur):
            return False, f"field {name!r} does not match pattern {pat!r}"
        return True, f"field {name!r} string ok"
    if typ == "file_exists":
        try:
            target = _resolve_verify_path(
                cur if isinstance(cur, str) else name
            )
        except ValueError as e:
            return False, str(e)
        if not target.is_file():
            return False, f"referenced file not found: {target}"
        return True, f"referenced file exists: {target}"
    # Fail-closed: unknown check types never pass.
    return False, f"unknown check type: {typ!r}"


def verify_artifact(
    path: str,
    checks: list[dict] | None = None,
) -> dict:
    """P1 獨立評估器 — mechanical (zero-LLM) artifact verification.

    Verifies a produced file INSIDE the filesystem sandboxes:

    * exists + size (``min_bytes``) — the "fake findings.json" trap;
    * JSON parse (when any check is JSON-typed);
    * per-check requirements: ``field`` (dotted path ``a.b[0].c``),
      ``type`` ∈ exists | nonempty | json_fields (default; optional
      ``required`` sub-fields) | json_number (``min``/``max``) |
      json_array (``min_length``) | json_string (``pattern``) |
      file_exists — anything unknown FAILS closed.

    Returns a JSON-friendly report dict — ``ok`` is the single
    machine verdict the schedule verification node relies on.
    Never raises; all failures surface as ``ok: False`` + ``errors``.
    """
    report: dict[str, Any] = {"ok": True, "path": path, "errors": []}
    checks = checks or []
    try:
        p = _resolve_verify_path(path)
    except ValueError as e:
        return {**report, "ok": False, "errors": [str(e)]}
    report["resolved_path"] = p.as_posix()
    if not p.exists():
        return {**report, "ok": False, "errors": ["artifact does not exist"]}
    if not p.is_file():
        return {**report, "ok": False, "errors": ["path is not a file"]}
    try:
        size = p.stat().st_size
    except OSError as e:
        return {**report, "ok": False, "errors": [f"stat failed: {e}"]}
    report["size"] = size
    min_bytes = 0
    for c in checks:
        if isinstance(c, dict) and c.get("type") == "min_bytes":
            try:
                min_bytes = max(min_bytes, int(c.get("value") or 0))
            except (TypeError, ValueError):
                pass
    if size < min_bytes:
        return {
            **report, "ok": False,
            "errors": [f"size {size} < min_bytes {min_bytes}"],
        }
    payload: Any = None
    # Any check with a ``field`` needs the payload — even plain
    # ``exists`` navigates into it (e.g. {"field": "summary",
    # "type": "exists"}).
    needs_json = any(
        isinstance(c, dict) and (
            str(c.get("type") or "json_fields") in _JSON_CHECK_TYPES
            or bool(c.get("field"))
        )
        for c in checks
    )
    if needs_json:
        raw = b""
        try:
            if size <= _VERIFY_MAX_BYTES:
                raw = p.read_bytes()[:_VERIFY_MAX_JSON]
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return {
                **report, "ok": False,
                "errors": [
                    f"JSON parse failed: {e} (is the artifact the "
                    "right format?)"
                ],
            }
        report["json"] = True
    passed: list[str] = []
    for i, c in enumerate(checks):
        if not isinstance(c, dict):
            report["ok"] = False
            report["errors"].append(
                f"check[{i}] must be an object, got {type(c).__name__}"
            )
            continue
        ctype = str(c.get("type") or "json_fields")
        if ctype == "min_bytes":
            continue  # already applied above
        try:
            ok, detail = _check_requirement(c, payload)
        except Exception as e:  # never raise from a checker
            ok, detail = False, f"check[{i}] crashed: {e!r}"
        if ok:
            passed.append(detail)
        else:
            report["ok"] = False
            report["errors"].append(detail)
    report["passed"] = passed
    report["check_count"] = len(checks)
    if not checks:
        report["passed"] = [
            "artifact exists" if not min_bytes
            else f"size {size} >= {min_bytes}"
        ]
    return report


def _claims_telegram_send(answer: str) -> bool:
    if not answer:
        return False
    low = answer.lower()
    if "telegram" not in low:
        return False
    if _CLAIM_NEG_RE.search(answer):
        return False
    return bool(_CLAIM_RE.search(answer))


def _dispatched_telegram_send(agent: Any) -> bool:
    """``agent.last_tool_dispatches`` (v0.9.57.1) — tool NAMES
    dispatched during THIS turn. The real Agent stores plain
    ``list[str]`` (``_run_tool_call`` appends the name); dicts /
    attribute objects are tolerated for test fakes.

    v0.9.67.2 hotfix: the v0.9.67.1 guard parsed every entry as a
    dict/attribute object, so plain-string entries NEVER matched —
    the guard fired a false corrective turn (and a duplicate Telegram
    send) even when the fire HAD dispatched send_telegram_message.
    """
    dispatches = getattr(agent, "last_tool_dispatches", None) or []
    for d in dispatches:
        if isinstance(d, str):
            name = d
        elif isinstance(d, dict):
            name = d.get("name")
        else:
            name = getattr(d, "name", "")
        if str(name or "").startswith("send_telegram"):
            return True
    return False


def _fire_memory_enabled() -> bool:
    """``QDRANT_SCHEDULE_FIRE_MEMORY`` — whether the per-turn
    long-term-memory recall block is injected into a fire's LLM
    context.

    Default **off**: each fire's earlier web_search results are
    ingested as memories (v0.9.59.0 kind=query), so recall re-injects
    OLD prices into a fire asking for TODAY's data (user report
    2026-09-16: 「異常分析使用長期記憶資料使用舊的股價」). ``on``
    restores recall for fires that WANT continuity.
    """
    raw = (os.environ.get("QDRANT_SCHEDULE_FIRE_MEMORY", "") or "").strip()
    return raw.lower() in ("on", "true", "1", "yes")


def _extract_fire_answer(result: Any) -> tuple[str | None, list, Any]:
    """Unpack a ``chat_turn`` result of either contract (dict /
    (answer, context_md, sources) tuple)."""
    if isinstance(result, dict):
        answer = result.get("answer")
        sources = result.get("sources", [])
        error = result.get("error")
    elif isinstance(result, tuple) and len(result) >= 1:
        answer = result[0]
        sources = result[2] if len(result) >= 3 else []
        error = None
    else:
        answer = None
        sources = []
        error = None
    if isinstance(sources, list):
        sources = [
            s if isinstance(s, str) else s.get("filename", "")
            for s in sources
        ]
    return answer, sources, error


# ─── Cron expression support ───────────────────────────────────
# Minimal 5-field cron: minute / hour / day-of-month / month / day-of-week
# Supports: integers, ``*``, ``*/n``, comma-separated lists, and ``a-b``
# ranges. Day-of-week: 0-6 with Sun=0 (POSIX). We don't parse month
# / day names — keep it simple, no extra deps.

def _parse_field(expr: str, lo: int, hi: int) -> set[int]:
    """Expand a single cron field into the set of values it matches.

    Supports ``*``, ``*/n``, ``a-b``, ``a-b/n``, ``a,b,c``, plain
    integers. ``hi`` is inclusive. Raises ``ValueError`` on bad input.
    """
    out: set[int] = set()
    for piece in expr.split(","):
        piece = piece.strip()
        if not piece:
            raise ValueError(f"empty cron field: {expr!r}")
        if piece.startswith("*/"):
            step = int(piece[2:])
            if step <= 0:
                raise ValueError(f"bad step in {piece!r}")
            out.update(range(lo, hi + 1, step))
            continue
        if "-" in piece:
            base, _, step_s = piece.partition("/")
            lo_s, hi_s = base.split("-", 1)
            a, b = int(lo_s), int(hi_s)
            if a > b:
                raise ValueError(f"reverse range in {piece!r}")
            step = int(step_s) if step_s else 1
            if step <= 0:
                raise ValueError(f"bad step in {piece!r}")
            out.update(range(a, b + 1, step))
            continue
        if piece == "*":
            out.update(range(lo, hi + 1))
            continue
        out.add(int(piece))
    out = {v for v in out if lo <= v <= hi}
    if not out:
        raise ValueError(f"cron field {expr!r} resolved to empty set")
    return out


def cron_matches(expr: str, dt: datetime) -> bool:
    """Return True if the given datetime matches the 5-field cron
    expression. Day-of-week 0-6 (Sun=0)."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            f"cron needs 5 fields, got {len(fields)}: {expr!r}"
        )
    minute, hour, dom, month, dow = fields
    return all([
        dt.minute in _parse_field(minute, 0, 59),
        dt.hour in _parse_field(hour, 0, 23),
        dt.day in _parse_field(dom, 1, 31),
        dt.month in _parse_field(month, 1, 12),
        dt.weekday() in _parse_field(dow, 0, 6),
    ])


def next_cron_fire(expr: str, after: datetime | None = None) -> datetime:
    """Return the next datetime >= ``after`` that matches ``expr``.

    Iterates minute-by-minute up to 4 years — cron expressions with
    ``Feb 30`` etc. never match and we'd loop forever, so we cap.
    """
    if after is None:
        after = datetime.now()
    dt = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    cap = dt + timedelta(days=365 * 4)
    while dt < cap:
        if cron_matches(expr, dt):
            return dt
        dt += timedelta(minutes=1)
    raise ValueError(
        f"cron expression {expr!r} never matches within 4 years"
    )


# ─── Schedule dataclass ────────────────────────────────────────


# Ring-buffer limit for ``history.jsonl``.
MAX_HISTORY = 50

# How many seconds between cron evaluations. Cheaper than per-second.
TICK_SECONDS = 1


@dataclass
class Schedule:
    """A scheduled prompt. Persisted to ``schedules/<id>.json``."""

    id: str = ""
    name: str = ""
    prompt: str = ""
    schedule_type: str = "interval_seconds"
    once_at: str | None = None
    cron: str | None = None
    interval_seconds: int | None = None
    use_web_search: bool = False
    use_tools: bool = True
    web_max_results: int = 5
    enabled: bool = True
    last_fire_at: str | None = None
    next_fire_at: str | None = None
    fire_count: int = 0
    last_session_id: str | None = None
    created_at: str = ""
    # v0.8.6+: persisted across fires so the LLM (via
    # ``get_scheduled_task_errors``) and the GUI can surface why the
    # last fire died. ``None`` when the last fire succeeded.
    last_error: str | None = None
    # v0.8.6+: ISO-8601 ts of ``last_error`` so the LLM can answer
    # "上次失敗是什麼時候" without scanning history.jsonl.
    last_error_at: str | None = None
    # v0.8.7+: optional cap on total fire count. Once
    # ``fire_count >= max_fires`` the daemon auto-disables the
    # schedule and stamps ``status="exhausted"``. ``None`` (default)
    # means unlimited. The LLM can read this directly to answer
    # "還能跑幾次" without scanning the history.
    max_fires: int | None = None
    # v0.8.7+: optional hard expiry — ISO-8601 timestamp after
    # which the schedule stops firing. ``compute_next_fire`` returns
    # ``None`` once ``now >= expires_at``, the schedule is
    # auto-disabled and ``status="expired"``. ``None`` means no
    # expiry.
    expires_at: str | None = None
    # v0.8.7+: lifecycle marker that the LLM / GUI can read at a
    # glance. One of "active" / "expired" / "exhausted" / "disabled"
    # / "failed". ``Scheduler.tick_once`` skips non-active rows.
    # Updated by ``compute_next_fire`` (auto-stop conditions) and by
    # ``make_agent_on_fire`` (after a fatal chat_turn exception the
    # whole schedule is marked ``"failed"``). ``None`` is treated as
    # "active" so old on-disk schedules keep running.
    status: str | None = None
    # v0.9.106.0 (P0 驗證前置化): when True (the default), every
    # fire appends a VERIFICATION directive to the prompt — the LLM
    # must run a verification step (tool回執比對 / ``verify_artifact``
    # / get_chat_session 回讀) BEFORE any success conclusion, and the
    # post-fire machine guard re-checks the reply: a success claim
    # with ZERO verification evidence triggers ONE corrective turn
    # that forces the verification call. ``False`` restores the
    # legacy free-form prompt (opt-out per schedule). Old on-disk
    # JSON files without this key load as True via from_dict.
    verify_required: bool = True
    # v0.9.111.0 — optional fire-level chat-model pin (``provider:model``,
    # openrouter | ollama; catalog-validated at ADD time via
    # task_llm.coerce_llm_pin). ""/None = the default provider chain
    # (zero behaviour change). At FIRE time a pin that no longer
    # validates degrades to the default chain with a warning — schedule
    # robustness beats pin strictness (a schedule must keep firing).
    llm: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Schedule":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def compute_next_fire(self, after: datetime | None = None) -> datetime | None:
        """Compute the next fire datetime given the schedule type.
        Returns None if the schedule is one-shot and already fired,
        or disabled.

        v0.8.7+: also enforces the optional ``max_fires`` and
        ``expires_at`` lifecycle caps. When either is tripped the
        schedule is auto-disabled (``enabled=False``) and
        ``status`` is stamped so the LLM can explain the stop
        without re-deriving it from the history.
        """
        if not self.enabled:
            return None
        if after is None:
            after = datetime.now()
        # v0.8.7+: terminal-lifecycle checks. These run BEFORE the
        # schedule-type branch so a one-shot that already exhausted
        # its max_fires (e.g. max_fires=1) reports exhausted
        # consistently with a recurring schedule that hit the same
        # cap.
        if (
            self.max_fires is not None
            and self.fire_count >= self.max_fires
        ):
            self.status = "exhausted"
            self.enabled = False
            self.next_fire_at = None
            return None
        # v0.8.7+: parse ``expires_at`` once at the top of the
        # lifecycle check so every branch below can compare against
        # the same datetime object. ``Z``-suffix is rewritten to
        # ``+00:00`` so Python's ``fromisoformat`` (Py3.10+) accepts
        # it; we then strip the tzinfo because ``after`` comes from
        # ``datetime.now()`` which is naive. Comparing a UTC-aware
        # expiry against a naive local ``after`` would otherwise
        # raise ``TypeError: can't compare offset-naive and
        # offset-aware datetimes``.
        exp: datetime | None = None
        if self.expires_at:
            try:
                exp = datetime.fromisoformat(
                    self.expires_at.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                exp = None
        if exp is not None and after >= exp:
            self.status = "expired"
            self.enabled = False
            self.next_fire_at = None
            return None
        if self.schedule_type == "once_at":
            if self.fire_count > 0:
                # One-shot that already fired — auto-disable so
                # ``tick_once`` doesn't keep re-evaluating it on
                # every tick. The previous behaviour lived in
                # ``tick_once``; moving it here lets phase-2 of
                # ``tick_once`` clean up via ``mgr.save()`` even
                # when ``next_fire_at`` was hand-nulled by a test.
                if self.status is None:
                    self.status = "exhausted" if (
                        self.max_fires is not None
                        and self.fire_count >= self.max_fires
                    ) else "disabled"
                self.enabled = False
                self.next_fire_at = None
                return None
            if not self.once_at:
                raise ValueError("once_at schedule missing once_at")
            # Even a one-shot can have an ``expires_at`` that has
            # not yet passed — if the requested fire time is past
            # the expiry we refuse.
            fire_dt = datetime.fromisoformat(self.once_at)
            if exp is not None and fire_dt > exp:
                self.status = "expired"
                self.enabled = False
                self.next_fire_at = None
                return None
            return fire_dt
        if self.schedule_type == "cron":
            if not self.cron:
                raise ValueError("cron schedule missing cron")
            nxt = next_cron_fire(self.cron, after=after)
            # ``next_cron_fire`` already enforces the 4-year ceiling
            # but not our user-supplied ``expires_at``. If the next
            # legitimate cron match would land past the expiry, we
            # cap it.
            if (
                exp is not None
                and nxt is not None
                and nxt >= exp
            ):
                self.status = "expired"
                self.enabled = False
                self.next_fire_at = None
                return None
            return nxt
        if self.schedule_type == "interval_seconds":
            if not self.interval_seconds or self.interval_seconds <= 0:
                raise ValueError(
                    "interval_seconds schedule missing positive interval"
                )
            # Anchor on the previous fire so long-running prompts
            # don't drift the schedule.
            if self.last_fire_at:
                base = datetime.fromisoformat(self.last_fire_at)
            else:
                base = after
            candidate = base + timedelta(seconds=self.interval_seconds)
            # If the candidate is in the past (e.g. scheduler was
            # off for a while), schedule for ``after + interval``.
            if candidate <= after:
                candidate = after + timedelta(
                    seconds=self.interval_seconds
                )
            # Cap the next candidate at the expiry so the LLM /
            # user don't see a schedule that claims to fire at
            # 2027-01-01 when they set ``expires_at=2026-12-31``.
            if exp is not None and candidate >= exp:
                self.status = "expired"
                self.enabled = False
                self.next_fire_at = None
                return None
            return candidate
        raise ValueError(f"unknown schedule_type: {self.schedule_type!r}")


# ─── Manager ───────────────────────────────────────────────────


class ScheduleManager:
    """CRUD over on-disk schedule JSON files."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else _schedules_dir()

    def _path(self, sid: str) -> Path:
        # Reject path-traversal: only allow [a-z0-9_-]{1,32}.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", sid):
            raise ValueError(f"invalid schedule id: {sid!r}")
        return self.root / f"{sid}.json"

    def _history_path(self, sid: str) -> Path:
        return self.root / f"{sid}.history.jsonl"

    def _atomic_write(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list(self) -> list[Schedule]:
        out: list[Schedule] = []
        for p in sorted(self.root.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append(Schedule.from_dict(d))
            except Exception as e:
                logger.warning(
                    "schedules: skipping corrupt %s: %r", p, e
                )
        return out

    def get(self, sid: str) -> Schedule | None:
        p = self._path(sid)
        if not p.exists():
            return None
        return Schedule.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, s: Schedule) -> Schedule:
        if not s.id:
            raise ValueError("schedule id is required")
        # Recompute next_fire_at on save so the scheduler picks it
        # up on the next tick.
        try:
            nxt = s.compute_next_fire()
            s.next_fire_at = (
                nxt.isoformat(timespec="seconds") if nxt else None
            )
        except ValueError as e:
            logger.warning(
                "schedules: %s next_fire_at compute failed: %r",
                s.id, e,
            )
        self._atomic_write(self._path(s.id), s.to_dict())
        return s

    def delete(self, sid: str) -> bool:
        p = self._path(sid)
        h = self._history_path(sid)
        removed = False
        for f in (p, h):
            if f.exists():
                f.unlink()
                removed = True
        return removed

    def set_enabled(
        self, sid: str, enabled: bool,
    ) -> Schedule | None:
        """Toggle the ``enabled`` flag on a schedule.

        v0.8.6+: called by the Scheduler MCP server / LocalHost
        when the user (or the LLM) says "暫停 X" / "啟用 Y".
        Recomputes ``next_fire_at`` on enable so the daemon picks
        the schedule up on its next tick. Returns the updated
        schedule, or ``None`` if ``sid`` doesn't exist.
        """
        s = self.get(sid)
        if s is None:
            return None
        s.enabled = bool(enabled)
        return self.save(s)

    def clear_error(self, sid: str) -> Schedule | None:
        """Wipe ``last_error`` + ``last_error_at`` on successful re-fire.

        v0.8.6+: called from ``make_agent_on_fire`` after a successful
        run so a stale error doesn't shadow fresh activity. Returns
        ``None`` if ``sid`` doesn't exist.
        """
        s = self.get(sid)
        if s is None:
            return None
        s.last_error = None
        s.last_error_at = None
        return self.save(s)

    def record_error(self, sid: str, exc: BaseException) -> Schedule | None:
        """Persist the last fire's exception repr.

        v0.8.6+: called from ``Scheduler.tick_once`` when ``on_fire``
        raises and from ``make_agent_on_fire`` when ``chat_turn``
        raises. The schedule's lifecycle fields (``fire_count``,
        ``next_fire_at``) are NOT touched here — we only stamp the
        failure so the LLM can later answer "為什麼 X 失敗了".
        """
        s = self.get(sid)
        if s is None:
            return None
        s.last_error = repr(exc)
        s.last_error_at = _now()
        return self.save(s)

    # ─── v0.8.7+ progress / lifecycle snapshot ───────────────────
    #
    # These two methods (plus ``set_max_fires`` / ``set_expires_at``
    # below) back the LLM's "為什麼 X 失敗了" / "還能跑幾次" /
    # "這個排程過期了嗎" answers and the new ``get_schedule_status``
    # / ``list_schedule_runs`` MCP tools. Keeping the surface here
    # means the GUI / CLI / MCP all share one JSON shape — no
    # separate serialiser per consumer.

    def get_progress(self, sid: str) -> dict[str, Any] | None:
        """v0.8.7+: lifecycle snapshot for the LLM / GUI to render.

        Returns a JSON-friendly dict with the keys the MCP tools
        (``get_schedule_status``) and the GUI badge all need:

          * id, name, prompt (truncated), schedule_type, enabled
          * lifecycle: status, fire_count, max_fires, fires_remaining
          * timing:    last_fire_at, next_fire_at, expires_at,
                       seconds_until_next_fire,
                       seconds_until_expiry
          * last error: last_error, last_error_at
          * recent runs: tail of history.jsonl (max 5 rows, prompt +
            answer preview + sources count + error flag)

        Returns ``None`` if ``sid`` doesn't exist. The history
        tail is rendered here (rather than split into a separate
        ``list_runs`` call) so the LLM gets a single roundtrip for
        the typical "為什麼 X 失敗了" question — exactly the use
        case user logs cite for the v0.8.6.2 Telegram + Schedule
        composition trap.
        """
        s = self.get(sid)
        if s is None:
            return None
        now = datetime.now()
        # Re-evaluate lifecycle caps so a stale ``status`` field
        # on disk gets refreshed before we serialise. This is a
        # read-only sync — we don't write the file because the
        # daemon does that on the next tick.
        try:
            s.compute_next_fire(after=now)
        except ValueError:
            pass

        def _secs(target_iso: str | None) -> int | None:
            if not target_iso:
                return None
            try:
                dt = datetime.fromisoformat(target_iso)
            except ValueError:
                return None
            return int((dt - now).total_seconds())

        if s.max_fires is None:
            fires_remaining: int | None = None
        else:
            fires_remaining = max(0, s.max_fires - s.fire_count)

        recent_runs = self._tail_history(sid, limit=5)
        # v0.9.106.0 (P0): verification snapshot — the LLM / GUI can
        # answer「上次跑完有驗證嗎」in one roundtrip.
        verified_flags = [
            r.get("verified") for r in recent_runs
            if "verified" in r
        ]
        return {
            "id": s.id,
            "name": s.name,
            "prompt_preview": s.prompt[:200],
            "schedule_type": s.schedule_type,
            "enabled": s.enabled,
            "status": s.status or ("active" if s.enabled else "disabled"),
            "fire_count": s.fire_count,
            "max_fires": s.max_fires,
            "fires_remaining": fires_remaining,
            "last_fire_at": s.last_fire_at,
            "next_fire_at": s.next_fire_at,
            "seconds_until_next_fire": _secs(s.next_fire_at),
            "expires_at": s.expires_at,
            "seconds_until_expiry": _secs(s.expires_at),
            "last_error": s.last_error,
            "last_error_at": s.last_error_at,
            "last_session_id": s.last_session_id,
            "verify_required": bool(s.verify_required),
            # _tail_history is oldest-first (file order) — the
            # newest run is the LAST element.
            "last_verified": (
                recent_runs[-1].get("verified")
                if recent_runs and "verified" in recent_runs[-1]
                else None
            ),
            "unverified_recent": sum(
                1 for v in verified_flags if v is False
            ),
            "recent_runs": recent_runs,
        }

    def list_runs(
        self, sid: str, limit: int = 10,
    ) -> list[dict[str, Any]] | None:
        """v0.8.7+: return the most recent N history rows.

        Each row is the raw JSON line from ``<sid>.history.jsonl``
        with ``answer`` truncated to 500 chars. Returns ``None``
        if ``sid`` doesn't exist; an empty list if the schedule
        hasn't fired yet.
        """
        s = self.get(sid)
        if s is None:
            return None
        return self._tail_history(sid, limit=limit)

    def _tail_history(
        self, sid: str, limit: int,
    ) -> list[dict[str, Any]]:
        """Read the last ``limit`` lines of ``<sid>.history.jsonl``.

        Per-row schema (the daemon already writes this; we just
        truncate ``answer`` for chat-friendliness):
            {"ts", "prompt", "answer" (str | None), "sources"
             (list[str]), "session_id", "error" (str | None)}
        """
        hp = self._history_path(sid)
        if not hp.exists():
            return []
        try:
            raw = hp.read_text(encoding="utf-8")
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for ln in raw.splitlines()[-limit:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ans = row.get("answer")
            if isinstance(ans, str) and len(ans) > 500:
                row["answer"] = ans[:500] + "…"
            out.append(row)
        return out

    def set_max_fires(
        self, sid: str, max_fires: int | None,
    ) -> Schedule | None:
        """v0.8.7+: update the ``max_fires`` cap on a live schedule.

        ``None`` removes the cap (back to unlimited). If the new
        cap is already below ``fire_count`` the schedule is
        auto-disabled with ``status="exhausted"`` on the next
        ``save()`` (which calls ``compute_next_fire``).
        """
        s = self.get(sid)
        if s is None:
            return None
        if max_fires is not None and max_fires <= 0:
            raise ValueError("max_fires must be positive or None")
        s.max_fires = max_fires
        return self.save(s)

    def set_expires_at(
        self, sid: str, expires_at: str | None,
    ) -> Schedule | None:
        """v0.8.7+: update the ``expires_at`` ISO-8601 timestamp.

        ``None`` removes the cap. A bad ISO-8601 string raises
        ``ValueError`` so the caller (LLM / GUI) gets a clean
        signal rather than a silent ignored value. Accepts the
        ``Z`` UTC suffix by rewriting it to ``+00:00``.
        """
        s = self.get(sid)
        if s is None:
            return None
        if expires_at is not None:
            try:
                datetime.fromisoformat(
                    expires_at.replace("Z", "+00:00")
                )
            except ValueError as e:
                raise ValueError(
                    f"invalid ISO-8601 expires_at {expires_at!r}: {e}"
                )
        s.expires_at = expires_at
        return self.save(s)

    def find_by_name(self, name: str) -> Schedule | None:
        """Resolve a human-readable name to its schedule.

        v0.8.6+: lets the LLM / GUI say "停掉 daily-summary" without
        looking up the UUID first. Returns the FIRST match — name
        uniqueness is the user's responsibility (the GUI surfaces a
        warning if multiple schedules share a name).
        """
        if not name:
            return None
        for s in self.list():
            if s.name == name:
                return s
        return None

    def append_history(self, sid: str, entry: dict) -> None:
        """Append a fire record; trim to MAX_HISTORY lines."""
        h = self._history_path(sid)
        h.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        existing: list[str] = []
        if h.exists():
            try:
                existing = [
                    ln for ln in h.read_text(encoding="utf-8").splitlines()
                    if ln.strip()
                ]
            except OSError:
                existing = []
        existing.append(line)
        if len(existing) > MAX_HISTORY:
            existing = existing[-MAX_HISTORY:]
        tmp = h.with_suffix(h.suffix + ".tmp")
        tmp.write_text("\n".join(existing) + "\n", encoding="utf-8")
        os.replace(tmp, h)

    def get_history(self, sid: str, limit: int = 50) -> list[dict]:
        h = self._history_path(sid)
        if not h.exists():
            return []
        lines = [
            ln for ln in h.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        out: list[dict] = []
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out


# ─── Scheduler (daemon thread) ────────────────────────────────


class Scheduler:
    """Daemon-thread scheduler. Runs ``on_fire(schedule)`` whenever
    a schedule's ``next_fire_at`` is reached.

    The default ``on_fire`` callback uses an ``Agent`` to run
    ``chat_turn``; tests can pass a plain callable instead.
    """

    def __init__(
        self,
        manager: ScheduleManager | None = None,
        on_fire: Callable[[Schedule], None] | None = None,
        tick_seconds: int = TICK_SECONDS,
    ):
        self.mgr = manager or ScheduleManager()
        self.on_fire = on_fire
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # Schedule IDs currently inside ``on_fire`` so a long
        # prompt doesn't get double-fired on the next tick.
        self._firing: set[str] = set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="scheduler-tick", daemon=True
        )
        self._thread.start()
        logger.info("scheduler: started (tick=%ds)", self.tick_seconds)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("scheduler: stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as e:
                logger.exception("scheduler: tick error: %r", e)
            # Sleep in small chunks so stop() is responsive.
            for _ in range(self.tick_seconds * 10):
                if self._stop.is_set():
                    return
                time.sleep(0.1)

    def tick_once(self) -> list[str]:
        """Inspect all schedules, fire any that are due. Returns
        the list of schedule IDs that fired this tick.

        Two-phase check (v0.8.7+):
          1. The on-disk ``next_fire_at`` is the cheap "is it due?"
             gate. We honour whatever the file says so hand-edits
             and tests that force a past ``next_fire_at`` still
             fire on the next tick.
          2. Before calling ``on_fire`` we re-evaluate
             ``compute_next_fire`` so a schedule that hit its
             ``max_fires`` cap or whose ``expires_at`` slipped past
             between saves is caught. If the re-eval returns None
             (capped / expired) we persist the new lifecycle status
             and skip the fire.
        """
        fired: list[str] = []
        if not self.on_fire:
            return fired
        now = datetime.now()
        for s in self.mgr.list():
            if not s.enabled:
                continue
            # v0.8.7+: skip schedules whose lifecycle status is not
            # ``active``. ``compute_next_fire`` already stamps
            # ``"expired"`` / ``"exhausted"`` and disables them, but
            # this is defence-in-depth — the on-disk file might have
            # been hand-edited or the JSON might pre-date the field
            # (``status=None`` is treated as active).
            if s.status and s.status not in ("active", None):
                continue
            if s.id in self._firing:
                continue
            # Phase 1: cheap ``next_fire_at`` gate.
            #
            # ``None`` means the schedule has no future fire
            # queued — that could be a previously-fired one-shot, a
            # previously-capped ``max_fires``, OR a stale JSON
            # that lost its ``next_fire_at`` somewhere. Re-evaluate
            # via ``compute_next_fire`` so a one-shot that needs
            # to be auto-disabled still gets the lifecycle stamp
            # on this tick (matches v0.8.6 behaviour tested in
            # ``test_scheduler_tick_once_auto_disables_one_shot``).
            if not s.next_fire_at:
                try:
                    refreshed = s.compute_next_fire(after=now)
                except ValueError:
                    continue
                if refreshed is None:
                    try:
                        self.mgr.save(s)
                    except Exception:
                        pass
                continue
            try:
                nxt = datetime.fromisoformat(s.next_fire_at)
            except ValueError:
                continue
            if nxt > now:
                continue
            # Phase 2: re-evaluate lifecycle caps so a schedule that
            # hit ``max_fires`` or expired between saves doesn't
            # fire one last time. ``compute_next_fire`` mutates
            # ``s`` in place when a cap trips — we persist the new
            # state and skip the fire. We also fire the schedule
            # even if ``refreshed`` is in the future: phase 1 said
            # it's due, phase 2 just gates on lifecycle caps; the
            # on_fire path will reschedule via ``mgr.save()``.
            try:
                refreshed = s.compute_next_fire(after=now)
            except ValueError:
                continue
            if refreshed is None:
                try:
                    self.mgr.save(s)
                except Exception:
                    pass
                continue
            with self._lock:
                self._firing.add(s.id)
            try:
                self.on_fire(s)
                fired.append(s.id)
            except Exception as e:
                logger.exception(
                    "scheduler: on_fire(%s) failed: %r", s.id, e
                )
                # v0.8.6+: persist the failure so the LLM / GUI can
                # answer "為什麼 X 失敗了". We re-read the schedule
                # (rather than reusing the in-memory `s`) so the
                # ``last_error`` write is atomic with the rest of the
                # on-disk state and doesn't race with concurrent
                # GUI edits.
                try:
                    self.mgr.record_error(s.id, e)
                except Exception as ee:
                    logger.warning(
                        "scheduler: record_error(%s) failed: %r",
                        s.id, ee,
                    )
            finally:
                self._firing.discard(s.id)
        return fired


# ─── Agent-backed on_fire helper ──────────────────────────────


def make_agent_on_fire(
    agent_factory: Callable[[], Any],
) -> Callable[[Schedule], None]:
    """Build an ``on_fire`` callback that delegates to ``Agent``.

    ``agent_factory`` is called per-fire so the daemon thread
    doesn't share state with the main thread's Agent. Each
    invocation writes a fire record into both a dedicated
    session and the schedule's ``history.jsonl``.

    The factory should return an object with ``chat_turn`` and
    ``session_manager`` methods — see ``Agent`` for the canonical
    implementation.
    """
    def _fire(sched: Schedule) -> None:
        # v0.9.111.0 — optional fire-level model pin (Schedule.llm).
        # A stale/invalid pin DEGRADES to the default chain (schedule
        # robustness — a pinned schedule must keep firing even when a
        # model leaves the catalog). Mirrors the skill _run_stage pin.
        pin: str | None = (
            str(getattr(sched, "llm", "") or "").strip() or None
        )
        if pin:
            try:
                from src.agent.task_llm import validate_task_llm

                verdict = validate_task_llm(pin)
                if verdict.get("ok"):
                    pin = f"{verdict['provider']}:{verdict['model']}"
                else:
                    logger.warning(
                        "scheduler: %s llm pin %r invalid (%s) — "
                        "fire runs on the default chain",
                        sched.id, sched.llm, verdict.get("error"),
                    )
                    pin = None
            except Exception as e:  # noqa: BLE001 — probe never blocks
                logger.warning(
                    "scheduler: llm pin probe failed (%r) — fire runs "
                    "on the default chain", e,
                )
                pin = None
        if pin:
            try:
                agent = agent_factory(llm=pin)
            except TypeError:
                # legacy zero-arg factory — the pin cannot be applied.
                logger.warning(
                    "scheduler: agent factory predates llm pins — "
                    "schedule %s runs on the default chain", sched.id,
                )
                agent = agent_factory()
        else:
            agent = agent_factory()
        from src.agent.session import Session, SessionManager
        smgr = ScheduleManager()
        sm = SessionManager()
        sess = None
        if sched.last_session_id:
            sess = sm.load(sched.last_session_id)
        if sess is None:
            sess = Session(
                sid=(
                    f"sched-{sched.id}-"
                    f"{datetime.now().strftime('%Y%m%d%H%M%S')}"
                ),
                title=f"[schedule] {sched.name or sched.id}",
            )
        # Append user prompt; chat_turn takes history minus the
        # current user msg (Agent.chat_turn will append it again).
        # v0.9.106.0 (P0 驗證前置化): when the schedule requires
        # verification, the fire prompt gains a VERIFICATION DIRECTIVE
        # — the mandated verification NODE lives in the prompt so the
        # LLM cannot claim the flow without it; the post-fire machine
        # guard below is the second, independent layer.
        _verify_required = (
            bool(sched.verify_required) and _fire_verify_enabled()
        )
        if _verify_required:
            sched_prompt = sched.prompt + _verification_directive(sched.id)
        else:
            sched_prompt = sched.prompt
        user_msg = {"role": "user", "content": sched_prompt, "ts": _now()}
        sess.messages.append(user_msg)
        # v0.8.6+: ``chat_turn`` may itself raise (Qdrant down,
        # OpenAI 5xx, network glitch). Wrap the call so the daemon
        # records ``last_error`` instead of silently swallowing it.
        # v0.9.45.0 Phase 2: register the fire in the process-wide
        # task registry (list_tasks / get_task_result visibility).
        from src.agent.task_runner import begin_task, end_task
        _tr = begin_task(
            "scheduler", sched.name or sched.prompt[:60], sid=sess.id,
            llm=pin,
        )
        try:
            # v0.9.67.1 — cap the carried-over history: repeating a
            # fixed prompt over the full accumulated session history
            # taught the model to parrot old replies (fabricated
            # sends). Fires are independent runs by default.
            fire_history = list(sess.messages[:-1])
            limit = _fire_history_limit()
            if limit >= 0 and len(fire_history) > limit:
                fire_history = fire_history[-limit:] if limit else []
            result = agent.chat_turn(
                question=sched_prompt,
                history=fire_history,
                use_web_search=sched.use_web_search,
                web_max_results=sched.web_max_results,
                use_tools=sched.use_tools,
                # v0.9.67.2 — fires ask for TODAY's data; injecting
                # long-term memories (which contain earlier fires'
                # ingested web_search results = OLD prices) made the
                # analysis quote stale numbers. Off by default;
                # QDRANT_SCHEDULE_FIRE_MEMORY=on restores recall.
                use_memory=_fire_memory_enabled(),
            )
        except Exception as chat_exc:
            try:
                end_task(
                    _tr, "failed", error=repr(chat_exc)[:500],
                )
            except Exception:
                pass
            # Persist the failure for ``get_scheduled_task_errors`` /
            # the GUI error badge, then re-raise so the daemon's
            # ``tick_once`` exception handler also runs (defence in
            # depth — both layers stamp the error and log).
            try:
                smgr.record_error(sched.id, chat_exc)
            except Exception as ee:
                logger.warning(
                    "scheduler: record_error(%s) failed: %r",
                    sched.id, ee,
                )
            # Still write a degraded session row so the user can
            # see what the prompt was supposed to do.
            sess.messages.append({
                "role": "assistant",
                "content": f"[error] {chat_exc!r}",
                "ts": _now(),
                "sources": [],
                "schedule_id": sched.id,
                "error": True,
            })
            sess.updated_at = _now()
            try:
                sm.save(sess)
            except Exception:
                pass
            degraded_row = {
                "ts": _now(),
                "prompt": sched.prompt,
                "answer": None,
                "sources": [],
                "session_id": sess.id,
                "error": repr(chat_exc),
            }
            # v0.9.106.0 (P0): an honest error row is already a
            # failure report — flagged True when verification
            # applies; key absent for legacy schedules.
            _err_vflag = _verified_flag(None, chat_exc, _verify_required)
            if _err_vflag is not None:
                degraded_row["verified"] = _err_vflag
            smgr.append_history(sched.id, degraded_row)
            # v0.8.7+: stamp the schedule lifecycle as ``"failed"``
            # so the LLM / GUI can tell at a glance that this row
            # is broken. We still re-raise so the daemon's own
            # exception handler also runs.
            try:
                fresh = smgr.get(sched.id)
                if fresh is not None:
                    fresh.status = "failed"
                    fresh.last_error = repr(chat_exc)[:500]
                    fresh.last_error_at = _now()
                    fresh.enabled = False
                    smgr.save(fresh)
            except Exception as ee:
                logger.warning(
                    "scheduler: failed-stamp(%s) failed: %r",
                    sched.id, ee,
                )
            raise
        # v0.9.23.0: `Agent.chat_turn` returns the TUPLE
        # (answer, context_md, sources_list) — the old dict-only
        # contract (`result.get("answer")`) matched only the test
        # fakes, so every REAL Agent fire recorded answer=None /
        # assistant "[error] None" / history "answer=0 chars" even
        # when the LLM had produced a full summary.
        answer, sources, error = _extract_fire_answer(result)
        # ── v0.9.67.1 — telegram-send integrity guard ──────────────
        # A fire whose prompt asks for a Telegram send, whose reply
        # CLAIMS the send happened, but with ZERO ``send_telegram_*``
        # dispatches in the turn = a fabricated send. Corrective
        # re-run: force a tool call with the SAME prompt directive
        # (no re-analysis), then adopt the corrective answer.
        if (
            _telegram_intent(sched.prompt)
            and _claims_telegram_send(answer)
            and not _dispatched_telegram_send(agent)
        ):
            logger.warning(
                "scheduler: %s claimed a Telegram send WITHOUT "
                "dispatching send_telegram_* — running corrective "
                "turn", sched.id,
            )
            fix_prompt = (
                f"{sched.prompt}\n\n"
                "[系統守衛] 你剛才的回覆宣稱已發送 Telegram 訊息，"
                "但並未實際呼叫 send_telegram_message / "
                "send_telegram_markdown 工具。請不要重新分析，"
                "立即呼叫 send_telegram_message 工具（chat_id 與內容"
                "照原指示），完成發送後僅簡短回覆工具結果。"
            )
            try:
                result2 = agent.chat_turn(
                    question=fix_prompt,
                    history=[],
                    use_web_search=False,
                    use_tools=True,
                    use_memory=_fire_memory_enabled(),
                )
                answer2, sources2, _err2 = _extract_fire_answer(result2)
                if answer2:
                    answer = answer2
                    sources = sources2 or sources
                    logger.info(
                        "scheduler: corrective telegram turn for %s "
                        "(answer=%d chars, telegram dispatched=%s)",
                        sched.id, len(answer2 or ""),
                        _dispatched_telegram_send(agent),
                    )
            except Exception as guard_exc:
                logger.warning(
                    "scheduler: telegram integrity corrective turn "
                    "failed for %s: %r", sched.id, guard_exc,
                )
        # ── v0.9.106.0 — P0 驗證前置化：事後機械守衛 ────────────────
        # 回覆「宣稱成功」但「零驗證證據」= 未驗證。追加 ONE 糾正
        # 回合強制補驗證；糾正失敗/空回覆 → 退回原回覆但記錄
        # verified=False（不升級成已驗證 — 誠實呈現勝過美化）。
        # A dispatch of a receipt tool (verify_artifact /
        # get_chat_session / send_telegram_*) IS machine evidence —
        # no corrective on top of an already-receipted turn.
        guard_agent = agent
        if (
            _verify_required
            and _unverified_success(answer)
            and not _dispatched_receipt_tool(agent)
        ):
            logger.warning(
                "scheduler: %s replied a SUCCESS claim with ZERO "
                "verification evidence — running verification "
                "corrective turn", sched.id,
            )
            # v0.9.127.0 — verifier isolation: run the corrective turn
            # on a DIFFERENT model when one is available (task_llm pins
            # accept openrouter/ollama; a zero-arg legacy factory or a
            # single-provider install degrades to the firing agent).
            try:
                _pin2 = (
                    _alt_verifier_pin(agent)
                    if _verifier_alt_pin_enabled() else None
                )
                if _pin2 and _pin2 != (pin or ""):
                    try:
                        guard_agent = agent_factory(llm=_pin2)
                        logger.info(
                            "scheduler: verifier isolation — %s "
                            "corrective turn pinned to %s",
                            sched.id, _pin2,
                        )
                    except TypeError:
                        logger.info(
                            "scheduler: agent factory predates llm pins "
                            "— %s corrective turn runs on the firing "
                            "agent", sched.id,
                        )
                    except Exception as af_exc:
                        logger.warning(
                            "scheduler: alt-pinned factory failed (%r) "
                            "— %s corrective turn reuses firing agent",
                            af_exc, sched.id,
                        )
                        guard_agent = agent
            except Exception:  # noqa: BLE001 — isolation never blocks
                pass
            try:
                result3 = guard_agent.chat_turn(
                    question=_verification_guard_prompt(sched.id),
                    history=[],
                    use_web_search=False,
                    use_tools=True,
                    use_memory=False,
                )
                answer3, sources3, _err3 = _extract_fire_answer(result3)
                if answer3:
                    answer = answer3
                    sources = sources3 or sources
                logger.info(
                    "scheduler: verification corrective turn for %s "
                    "(answer=%d chars, verify_artifact dispatched=%s, "
                    "unverified-still=%s)",
                    sched.id, len(answer3 or ""),
                    _dispatched_verify_artifact(guard_agent),
                    _unverified_success(answer),
                )
            except Exception as vguard_exc:
                logger.warning(
                    "scheduler: verification corrective turn failed "
                    "for %s: %r", sched.id, vguard_exc,
                )
        # v0.9.45.0 Phase 2: stamp the registry — fire finished.
        try:
            end_task(_tr, "done", answer_chars=len(answer or ""))
        except Exception:
            pass
        # v0.9.106.0 (P0): three-state verification marker —
        # True (verified / honest failure) / False (unverified
        # success claim) / None (verification not required → absent).
        _vflag = _verified_flag(
            answer, error, _verify_required, agent=guard_agent,
        )
        assistant_msg = {
            "role": "assistant",
            "content": answer or f"[error] {error}",
            "ts": _now(),
            "sources": sources,
            "schedule_id": sched.id,
        }
        if _vflag is not None:
            assistant_msg["verified"] = _vflag
        sess.messages.append(assistant_msg)
        sess.updated_at = _now()
        sm.save(sess)
        # Update the schedule's lifecycle fields + history.
        sched.last_fire_at = _now()
        sched.fire_count += 1
        sched.last_session_id = sess.id
        # v0.8.7+: keep ``status`` honest — every successful fire
        # means the row is currently doing what it's supposed to
        # do. ``compute_next_fire`` below may flip it to
        # ``"exhausted"`` or ``"expired"`` if a lifecycle cap was
        # just hit, which is the right precedence.
        sched.status = "active"
        if sched.schedule_type == "once_at":
            sched.enabled = False
            sched.next_fire_at = None
            # A one-shot that exhausted its max_fires is
            # ``"exhausted"`` rather than just disabled.
            if (
                sched.max_fires is not None
                and sched.fire_count >= sched.max_fires
            ):
                sched.status = "exhausted"
        else:
            try:
                nxt = sched.compute_next_fire()
                sched.next_fire_at = (
                    nxt.isoformat(timespec="seconds") if nxt else None
                )
            except ValueError:
                sched.next_fire_at = None
        # v0.8.6+: stamp the error on the schedule if chat_turn
        # reported a non-empty ``error`` key (e.g. web search down
        # but the LLM still answered), otherwise clear any stale
        # ``last_error`` left by an earlier failed fire.
        if error:
            try:
                sched.last_error = repr(error)[:500]
                sched.last_error_at = _now()
            except Exception:
                pass
        else:
            sched.last_error = None
            sched.last_error_at = None
        smgr.save(sched)
        # v0.9.106.0 (P0): the verification marker is merged into the
        # row BEFORE the single append so legacy runs (verification
        # not required) keep the exact pre-0.9.106 row shape (key
        # absent, not null).
        fire_row = {
            "ts": sched.last_fire_at,
            "prompt": sched.prompt,
            "answer": answer,
            "sources": sources,
            "session_id": sess.id,
            "error": error,
        }
        if _vflag is not None:
            fire_row["verified"] = _vflag
        smgr.append_history(sched.id, fire_row)
        logger.info(
            "scheduler: fired %s -> session %s (answer=%d chars)",
            sched.id, sess.id, len(answer or ""),
        )
    return _fire
