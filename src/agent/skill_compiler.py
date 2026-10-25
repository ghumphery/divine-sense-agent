# -*- coding: utf-8 -*-
"""Tacti (tactical plan) / Workflow COMPILER + multi-turn executor (user-facing name TACTI since v0.9.120.3)
(v0.9.109.0 — in-process core; MCP surface = ``TactiLocalHost``, server tacti-local since v0.9.120.3).

User ask: 「針對技能、workflow、戰術、策略等，需多個回合分工運作，非
單一 chat 回合可完成的工作進行優化 (compile) 再執行」.

Design (mirrors scheduler.py / task_runner.py discipline):

* **Compile stage** — a declarative stage list (title + prompt + optional
  depends_on / budget / verify) is OPTIMIZED into an executable wave-plan:
    1. validation (empty prompt / unknown deps / duplicate titles-merge
       / cycle detection — Kahn topological sort),
    2. dependency → wave assignment (wave = 1 + max(dep waves)),
    3. wave packing by the parallel cap (``QDRANT_SKILL_WAVE_CAP``,
       default 4 = the QDRANT_TASK_MAX_PARALLEL discipline), so an
       independent 20-stage skill never spawns 20 parallel LLM turns,
    4. verification node injection (``verify_artifact`` directive +
       honesty rule — the v0.9.106 P0 hard gate, per stage),
    5. per-stage budget stamping (``MAX_TOOL_ITERATIONS`` resolution),
    6. checkpoint + completion-notify directives in the compiled prompt.
* **Execution stage** — a plan-driver daemon thread walks the waves;
  every stage is its own background ``chat_turn`` (fresh Agent sharing
  the caller's ``McpManager``, full tool surface) with:
    - a wall-clock guard (``QDRANT_SKILL_STAGE_TIMEOUT_S``, default 1800,
      0 = off) — the v0.9.107.1 never-silent-hang discipline,
    - its own session (``plan-<plan_id>-<stage_id>``; Sessions tab +
      ``get_chat_session`` readable),
    - task-registry visibility (``source="plan"`` in list_tasks /
      get_task_result — stage-level telegram notify comes free when
      the app wires the v0.9.108.0 notify callback).
* **Persistence** — plans live in ``plans/<id>.json`` (atomic writes).
  The registry-like in-memory cache is a convenience; the FILE is the
  durable record (task-registry restart lesson, manual v1.1 §8).
* **Resume** — ``run_plan`` on a partial/failed plan resumes: running
  stages from a dead process reset to pending; done stages skip;
  remaining waves drive in order. Re-running a DONE plan resets it
  (skills are re-runnable artefacts) — use ``run_plan_stage`` to retry
  ONE stage without touching the rest.
* **Fail policy** — ``on_failure=stop`` (default): first failed stage
  stops the plan (later waves skipped); ``continue``: failed stage's
  dependents get skipped, independent stages still run.

Zero side effects at import: the module sends NOTHING unless the app
layer (``gui/api.py``) wires a notify callback; agent factory injection
via :func:`set_agent_factory` (host passes a fresh-Agent factory).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_STAGES = 20
TERMINAL = ("done", "failed", "skipped")
_FAIL_POLICIES = ("stop", "continue")

_LOCK = threading.RLock()
_PLANS: dict[str, dict] = {}
_AGENT_FACTORY: Callable[..., Any] | None = None
_STOPS: set[str] = set()
# v0.9.121.2 — deleted plan ids. Kept FOREVER (uuid ids never reused):
# a zombie driver thread that survived the stop may still call
# save_plan after the file is gone — the guard here prevents
# resurrecting deleted plans (the file stays deleted).
_DELETED: set[str] = set()

_STAGE_TIMEOUT_DEFAULT_S = 1800  # 30 min per stage; 0 = off via env
_WAVE_CAP_DEFAULT = 4

# v0.9.117.0 — stage hard-timeout EXTENSION arbiter: at the deadline,
# a still-running stage can be granted another window (instead of the
# v0.9.107.1 abandon+fail) when the run looks like a legitimate long
# job. Verdict = deterministic tool-dispatch LIVENESS growth first;
# only an ambiguous (silent) stage goes to a live Jev classification.
_EXT_LOCK = threading.Lock()
_EXTENSIONS: dict[str, dict[str, int]] = {}  # plan_id -> {stage_id: n}
_EXTENSIONS_DEFAULT = 3
_TIMEOUT_JEV_MIN_PROB_DEFAULT = 0.5

_PLAN_NOTIFY_CB: Callable[[dict], Any] | None = None


class PlanError(ValueError):
    """Raised by compile_plan for invalid plans (never a silent pass)."""


def _rag_home() -> Path:
    env = (os.environ.get("QDRANT_RAG_HOME", "") or "").strip()
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".qdrant-rag-agent"


def plans_dir() -> Path:
    d = (os.environ.get("QDRANT_SKILL_PLANS_DIR", "") or "").strip()
    p = Path(d) if d else _rag_home() / "plans"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _stage_timeout_s() -> int:
    raw = (os.environ.get("QDRANT_SKILL_STAGE_TIMEOUT_S", "")
           or "").strip()
    if not raw:
        return _STAGE_TIMEOUT_DEFAULT_S
    try:
        v = int(raw)
    except ValueError:
        return _STAGE_TIMEOUT_DEFAULT_S
    if v < 0:
        return _STAGE_TIMEOUT_DEFAULT_S
    return v  # 0 = off honoured


def _wave_cap(override: int | None = None) -> int:
    if override is not None:
        return max(1, int(override))
    raw = (os.environ.get("QDRANT_SKILL_WAVE_CAP", "") or "").strip()
    if not raw:
        return _WAVE_CAP_DEFAULT
    try:
        v = int(raw)
    except ValueError:
        return _WAVE_CAP_DEFAULT
    if v < 0:
        return _WAVE_CAP_DEFAULT
    return v if v > 0 else 9999   # 0 = unlimited


# ── v0.9.117.0 — hard-timeout extension arbiter ────────────────────
def _timeout_extensions() -> int:
    """Max extra windows per stage (default 3; 0 = off = exact
    v0.9.107.1 behaviour)."""
    raw = (os.environ.get("QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS", "")
           or "").strip()
    if not raw:
        return _EXTENSIONS_DEFAULT
    try:
        v = int(raw)
    except ValueError:
        return _EXTENSIONS_DEFAULT
    return v if v >= 0 else _EXTENSIONS_DEFAULT


def _timeout_jev_enabled() -> bool:
    raw = (os.environ.get("QDRANT_SKILL_TIMEOUT_JEV", "") or "").strip().lower()
    return raw not in ("off", "0", "no", "false", "n")


def _timeout_jev_min_prob() -> float:
    raw = (os.environ.get("QDRANT_SKILL_TIMEOUT_JEV_MIN_PROB", "")
           or "").strip()
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return _TIMEOUT_JEV_MIN_PROB_DEFAULT
    return min(0.99, max(0.05, v)) if v > 0 else \
        _TIMEOUT_JEV_MIN_PROB_DEFAULT


def _extensions_for(plan_id: str, stage_id: str) -> int:
    with _EXT_LOCK:
        return int((_EXTENSIONS.get(plan_id) or {}).get(stage_id, 0))


def _extensions_used(plan_id: str, stages: list[str]) -> int:
    with _EXT_LOCK:
        m = _EXTENSIONS.get(plan_id) or {}
        return sum(int(m.get(sid) or 0) for sid in stages)


def _wave_deadline(wave_started: float, ext_base: int,
                   ext_used: int) -> float:
    """Pure math for the driver's per-wave wait deadline: the
    v0.9.107.1 base (max(60, t + 60)) stretches linearly with each
    granted hard-timeout extension (t × (1 + ext) + 60)."""
    base = max(1, int(ext_base or 1))
    return wave_started + max(60, base * (1 + max(0, int(ext_used))) + 60)


def _evidence_snapshot(agent: Any) -> dict:
    """Cheap per-poll liveness evidence off the stage agent."""
    disp = getattr(agent, "last_tool_dispatches", None) or []
    usage = getattr(agent, "last_usage", None) or {}
    return {"dispatches": len(disp) if isinstance(disp, list) else 0,
            "tail": disp[-8:] if isinstance(disp, list) else [],
            "calls": usage.get("calls")}


def _timeout_evidence_text(agent: Any, elapsed_s: float,
                           snap: dict, meta: dict | None) -> str:
    """The Jev classification text: what the stage is doing + its
    measured tool activity at the hard deadline."""
    disp = getattr(agent, "last_tool_dispatches", None) or []
    usage = getattr(agent, "last_usage", None) or {}
    tail = ", ".join(str(d) for d in disp[-8:] if isinstance(
        disp, list))
    title = str((meta or {}).get("title") or "")
    q = str((meta or {}).get("question") or "")[:200]
    return (
        f"背景 stage 已連續執行 {elapsed_s / 60:.1f} 分鐘後撞上"
        f"硬超時上限。工作：{title} — {q}\n"
        f"證據：本回合已派發 {snap.get('dispatches', 0)} 次工具呼叫"
        f"（最近：{tail or '無'}）；LLM 回合數 "
        f"{usage.get('calls', '?')}、prompt tokens "
        f"{usage.get('prompt_tokens', '?')}、completion tokens "
        f"{usage.get('completion_tokens', '?')}。\n"
        f"判定指南：工具活動持續且與任務相符的巨量工作（下載/批次轉換"
        f"/長研究）= normal_long_work；停滯無進展、重複同一錯誤、"
        f"無意義循環 = abnormal。"
    )


def _jev_timeout_verdict(text: str) -> str | None:
    """'extend' only when a LIVE Jev confidently classifies the run as
    normal_long_work. Gate off / mock mode / errors / low prob → None
    (= kill path, v0.9.107.1 behaviour)."""
    if not _timeout_jev_enabled():
        return None
    try:
        from src.jev_tools import MODE_LIVE, JevClassifierEngine

        eng = JevClassifierEngine()
        if eng.mode != MODE_LIVE:
            return None  # mock = hash pick, worthless arbiter
        out = eng.classify_batch(
            names=["abnormal", "normal_long_work"],
            descs={
                "abnormal": ("卡死/失控：無工具活動、重複同一錯誤、"
                             "循環無進展"),
                "normal_long_work": ("正常長工：工具活動持續、工作量"
                                     "大只是需要更多時間"),
            },
            items=[{"id": "stage", "text": str(text or "")[:4000]}],
            instructions=(
                "判定一個撞上硬超時的背景 stage 是否仍在正常工作。"
                "工具派發持續且行為與任務相符 = normal_long_work；"
                "停滯、重複失敗、無進展 = abnormal。"),
            default_class="abnormal",
        )
        results = out.get("results") or []
        r = results[0] if results else {}
        if r.get("error"):
            logger.warning("[tacti] timeout Jev verdict error: %r",
                           r.get("error"))
            return None
        choice = str(r.get("choice") or "")
        prob = float(r.get("prob") or 0.0)
        if choice == "normal_long_work" and prob >= \
                _timeout_jev_min_prob():
            logger.info("[tacti] timeout Jev verdict: "
                        "normal_long_work p=%.2f — extending", prob)
            return "extend"
        logger.info("[tacti] timeout Jev verdict: %s p=%.2f — kill",
                    choice, prob)
        return None
    except Exception as e:  # noqa: BLE001 — the arbiter must never
        # break the guard
        logger.warning("[tacti] timeout Jev verdict failed: %r", e)
        return None


def _grant_extension(plan_id: str, stage_id: str) -> int:
    with _EXT_LOCK:
        m = _EXTENSIONS.setdefault(plan_id, {})
        m[stage_id] = int(m.get(stage_id, 0)) + 1
        return m[stage_id]


def set_agent_factory(cb: Callable[..., Any] | None) -> None:
    """Inject the fresh-Agent factory used by stage turns (host wires
    it so background agents share the process tool fleet). v0.9.110.0:
    the factory is called optionally with ``llm=<pin>`` — a zero-arg
    legacy factory still works (a TypeError during the llm-arg probe
    falls back to the no-arg call)."""
    global _AGENT_FACTORY
    _AGENT_FACTORY = cb


def set_plan_notify_callback(
        cb: Callable[[dict], Any] | None) -> None:
    """Plan-level terminal notify (gui/api wires it; default silent)."""
    global _PLAN_NOTIFY_CB
    _PLAN_NOTIFY_CB = cb


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(title: str, idx: int) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", "-", str(title or "").strip()).strip("-")
    s = s[:28].lower() or f"stage"
    return f"{s}-{idx}"


def plan_file(plan_id: str) -> Path:
    return plans_dir() / f"{plan_id}.json"


def load_plan(plan_id: str) -> dict | None:
    pid = str(plan_id or "").strip()
    if not pid:
        return None
    with _LOCK:
        cached = _PLANS.get(pid)
    if cached is not None:
        return dict(cached)
    # v0.9.121.2 — a deleted plan reads as absent (its file is gone;
    # the in-file record cannot resurrect).
    with _LOCK:
        if pid in _DELETED:
            return None
    f = plan_file(pid)
    if not f.exists():
        return None
    try:
        import json

        rec = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — corrupt plan reads as None
        logger.warning("[tacti] load_plan(%s) failed: %r", pid, e)
        return None
    with _LOCK:
        _PLANS[pid] = rec
    return dict(rec)


def save_plan(rec: dict) -> None:
    import json

    rec = dict(rec)
    rec["updated_at"] = _now()
    with _LOCK:
        if rec["id"] in _DELETED:
            # v0.9.121.2 — a zombie driver's late save must not
            # resurrect a deleted plan.
            logger.debug(
                "[tacti] save_plan(%s) blocked — plan deleted",
                rec["id"],
            )
            return
        _PLANS[rec["id"]] = rec
    f = plan_file(rec["id"])
    tmp = f.with_suffix(f".json.tmp{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(f)


def list_plans(limit: int = 20) -> dict:
    try:
        lim = max(1, min(int(limit or 20), 200))
    except (TypeError, ValueError):
        lim = 20
    out: list[dict] = []
    for f in sorted(plans_dir().glob("*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        rec = load_plan(f.stem)
        if rec is None:
            continue
        done = sum(1 for s in rec["stages"]
                   if s["status"] == "done")
        out.append({
            "id": rec["id"], "name": rec["name"],
            "description": (rec.get("description") or "")[:120],
            "status": rec["status"],
            "stages": f"{done}/{len(rec['stages'])}",
            "waves": len(rec["waves"]),
            "updated_at": rec.get("updated_at"),
        })
        if len(out) >= lim:
            break
    return {"plans": out, "returned": len(out)}


# ─── compile ───────────────────────────────────────────────────────

def _budget_default() -> int:
    try:
        from src.agent.agent import _resolve_max_tool_iterations

        return max(1, int(_resolve_max_tool_iterations()))
    except Exception:
        return 20


def _draft_min_stages() -> int:
    """v0.9.127.0 — minimum stage count for a DONE plan to earn a
    SKILL.md auto-draft (``QDRANT_PLAN_SKILL_DRAFT_MIN_STAGES``)."""
    try:
        from src.skill_library import plan_skill_draft_min_stages

        return plan_skill_draft_min_stages()
    except Exception:
        return 3


def _validate_tools_allow(value: Any, label: str) -> list[str] | None:
    """v0.9.111.0 — structural validation for plan/stage ``tools_allow``.

    Returns the cleaned list (``None`` = unset). Fail-closed authoring:
    a string / non-list / EMPTY list / oversized list raises
    :class:`PlanError`. An explicit list is honoured at run time by
    ``chat_turn(active_tool_names=...)`` (the v0.9.14.0 per-TOOL
    gate): unknown tool names simply expose nothing — a typo'd name
    yields a tool-less stage (visible in the session, never a silent
    full-surface fallback). Scope is enforced by MECHANISM, not by
    prompt admonitions.
    """
    if value is None:
        return None
    if not isinstance(value, (list, tuple, set)):
        raise PlanError(
            f"{label}: tools_allow must be a list of MCP tool names "
            "(e.g. ['read_file', 'web_search']) — got "
            f"{type(value).__name__}.",
        )
    names = [str(x).strip() for x in value if str(x).strip()]
    if not names:
        raise PlanError(
            f"{label}: tools_allow is empty — a stage with zero tools "
            "can only answer prose. Drop the field entirely (the full "
            "gated surface runs) or list the tools the stage needs.",
        )
    if len(names) > 60:
        raise PlanError(
            f"{label}: at most 60 tool names per tools_allow "
            f"(got {len(names)}).",
        )
    return list(dict.fromkeys(names))


def _handoff_root() -> Path | None:
    """The filesystem WRITE sandbox root (or None when unresolvable).
    The stage-handoff convention lives under ``<write_root>/out/``."""
    raw = (os.environ.get("QDRANT_FILESYSTEM_WRITE_ROOT") or "").strip()
    try:
        return Path(raw or "./sandbox").resolve()
    except Exception:  # noqa: BLE001 — never raise from a hint builder
        return None


def handoff_rel(stage_id: str) -> str:
    """The standard stage-handoff artifact path (write-root relative)."""
    return f"out/{stage_id}.json"


def handoff_abs(stage_id: str) -> str:
    """Absolute-path hint embedded into compiled stage prompts."""
    root = _handoff_root()
    base = root.as_posix() if root is not None else "<write_sandbox>"
    return f"{base}/{handoff_rel(stage_id)}"


def _write_handoff(
    plan_id: str,
    stage_id: str,
    status: str,
    answer_text: str = "",
    session: str = "",
    artifact_path: str | None = None,
    recovery_hint: str | None = None,
) -> dict | None:
    """MECHANICAL stage-handoff writer (v0.9.111.0) — once a stage
    turns terminal the DRIVER (never the stage LLM) writes
    ``out/<stage_id>.json`` so downstream stages have a guaranteed
    rendezvous point: status + answer summary + formal-artifact
    pointer + session pointer (full transcript via get_chat_session).
    LLM compliance is not part of the contract. Never raises — a
    failed write degrades to prompt-only handoff.
    v0.9.124.0: failed stages carry a ``recovery_hint`` (Evo²
    crossover — search the SKILL library instead of re-deriving)."""
    try:
        root = _handoff_root()
        if root is None or not str(stage_id or "").strip():
            return None
        payload = {
            "plan_id": plan_id,
            "stage_id": stage_id,
            "status": status,
            "summary": str(answer_text or "")[:2000],
            "artifacts": (
                [{"path": str(artifact_path), "kind": "verify"}]
                if artifact_path else []
            ),
            "session": str(session or ""),
            "written_at": _now(),
        }
        if recovery_hint:
            payload["recovery_hint"] = str(recovery_hint)[:600]
        out = root / "out"
        out.mkdir(parents=True, exist_ok=True)
        f = out / f"{stage_id}.json"
        tmp = f.with_suffix(f".json.tmp{uuid.uuid4().hex[:6]}")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(f)
        return {"path": f.as_posix(), "bytes": f.stat().st_size}
    except Exception as e:  # noqa: BLE001 — bookkeeping must not break
        logger.debug("[tacti] handoff write(%s) failed: %r", stage_id, e)
        return None


def _sink_plan(rec: dict, final: str) -> None:
    """Plan-terminal long-term-memory sink (v0.9.111.0) — ONE memory
    per plan run, ``kind=task-result``, store-NOT-recall (the
    v0.9.67.2 stale-snapshot lesson: recall never auto-injects this
    kind; discovery is on-demand via ``search_memory(kind=
    'task-result')``). Fire-and-forget thread; never raises."""
    plan_id = str(rec.get("id") or "")

    def _work() -> None:
        try:
            from src.agent.memory_store import (
                store_task_result,
                task_sink_enabled,
            )
            if not task_sink_enabled():
                return
            rows = []
            for s in rec.get("stages") or []:
                rows.append(
                    f"[{s.get('title')}] {s.get('status')}"
                    + (f" — {str(s.get('error'))[:120]}"
                       if s.get("error") else "")
                )
            desc = str(rec.get("description") or "")
            store_task_result(
                title=f"[plan] {rec.get('name')} -> {final}",
                prompt=(f"plan {plan_id} "
                        f"({len(rec.get('stages') or [])} stages, "
                        f"{len(rec.get('waves') or [])} waves)\n{desc[:600]}"),
                answer="\n".join(rows)[:2000],
                source=f"plan:{plan_id}", session_id="",
            )
            # v0.9.127.0 — the successful-experience-fixation loop:
            # a DONE plan with enough stages is auto-drafted as a
            # SKILL.md bundle under $QDRANT_RAG_HOME/skill_drafts/
            # (never auto-imported — review, then skill_import).
            try:
                if (str(final) == "done"
                        and (len(rec.get("stages") or [])
                             >= _draft_min_stages())):
                    from src.skill_library import (
                        draft_from_plan,
                        plan_skill_draft_enabled,
                    )

                    if plan_skill_draft_enabled():
                        _d = draft_from_plan(rec)
                        if _d and _d.get("ok"):
                            logger.info(
                                "[tacti] skill draft written: %s "
                                "(import via skill_import after review)",
                                _d.get("path"),
                            )
            except Exception as e:  # noqa: BLE001
                logger.debug("[tacti] skill draft failed: %r", e)
        except Exception as e:  # noqa: BLE001 — sink must not break
            logger.debug("[tacti] plan memory sink failed: %r", e)

    threading.Thread(
        target=_work, daemon=True, name=f"plan-sink-{plan_id}",
    ).start()


def _compile_prompt(
    st: dict, *, plan_name: str, waves_total: int, stage_idx: int,
    stages_total: int,
    deps_handoff: list[dict] | None = None,
) -> str:
    """Compose the compiled stage prompt. v0.9.111.0: ``deps_handoff``
    lists the upstream stages' MECHANICAL handoff artifacts
    (``{id,title,path}``; path = ABSOLUTE ``<write_root>/out/<dep>.json``)
    — the stage reads them instead of redoing upstream work. The files
    are written by the DRIVER, never by the stage, so the handoff
    exists even when the upstream stage is sloppy."""
    parts = [
        f"[PLAN STAGE] plan={plan_name} stage={st['title']} "
        f"(wave {st['wave'] + 1}/{waves_total}, "
        f"stage {stage_idx + 1}/{stages_total})",
        "", str(st["prompt"]).strip(), "",
        "──────────── 執行規則（compiler 自動附加，強制）────────────",
        f"1. 本 stage 是一個獨立回合 — 預算 = {st['budget']} 次工具迭代；"
        "只做本 stage 範圍內的工作，其他 stage 的內容不要做。",
        "2. 每個產出檔案寫完立即 stat_file 驗證存在且非空；多產出逐一驗證。",
    ]
    v = st.get("verify")
    if v:
        parts.append(
            f"3. 最後呼叫 ``verify_artifact(path={v['path']!r}"
            + (f", checks=<stage 的 checks 定義>" if v.get("checks")
               else "") + ") 機械驗證；驗證失敗 → 你的回覆必須以"
            "「驗證失敗 + 原因」開頭，禁止任何成功結論。"
        )
    else:
        parts.append(
            "3. 回覆時如實列出「已產出檔案 + 大小」；產不出來就以"
            "「執行失敗 + 原因」開頭，禁止空口宣稱完成。"
        )
    parts.append(
        "4. 檢查點：多步驟時每個子步完成即把進度寫進 checkpoint 檔；"
        "若這是重跑本 stage，先找先前 checkpoint，已完成部分直接跳過。"
    )
    chat = None
    handoff_n = 0
    if deps_handoff:
        rows = "\n".join(
            f"   - {h['title']} (id={h['id']}) -> {h['path']}"
            for h in deps_handoff
        )
        parts.append(
            "5. 上游交接檔（驅動器自動寫出；read_file 讀取）：\n" + rows
            + "\n先讀取上游摘要與產出位置再動工；禁止重做上游已完成的"
            "工作。交接檔不存在 = 上游未完成 → 以「執行失敗 + 原因」回報。"
            "（你本 stage 的結果摘要也會由驅動器寫進 out/<stage_id>.json"
            " 供下游承接。）"
        )
        handoff_n = 1
    chat = st.get("_notify_chat_id")
    if chat:
        parts.append(
            f"{5 + handoff_n}. 結尾用 "
            f"send_telegram_message(chat_id={chat}, text=…)"
            f"回報「{st['title']} 完成/失敗 + 產出與大小」。"
        )
    return "\n".join(parts)


def compile_plan(
    *,
    name: str,
    stages: list[dict],
    description: str = "",
    on_failure: str = "stop",
    notify_chat_id: int | None = None,
    default_budget: int | None = None,
    wave_cap: int | None = None,
    stage_timeout_s: int | None = None,
    default_llm: str | None = None,
    tools_allow: list[str] | None = None,
) -> dict:
    """Validate + optimize a declarative stage list into a plan
    record (persisted). Returns the FULL plan record.

    v0.9.111.0: ``tools_allow`` (plan-level default) — plan/stage-level
    tool scope enforced MECHANICALLY at run time via
    ``chat_turn(active_tool_names=...)``; per-stage ``tools_allow``
    overrides the plan default; absence = the full gated surface
    (zero behaviour change).
    """
    name = str(name or "").strip()
    if not name:
        raise PlanError("'name' is required.")
    if on_failure not in _FAIL_POLICIES:
        raise PlanError(f"on_failure must be one of {_FAIL_POLICIES}.")
    plan_allow = _validate_tools_allow(tools_allow, "plan-level")
    try:
        budget_default = (int(default_budget)
                          if default_budget is not None
                          else _budget_default())
    except (TypeError, ValueError):
        raise PlanError("default_budget must be an integer.")
    budget_default = max(1, min(budget_default, 200))
    if not stages or not isinstance(stages, (list, tuple)):
        raise PlanError(
            "'stages' is required (a non-empty list of stage dicts — "
            f"1..{MAX_STAGES} stages).",
        )
    if len(stages) > MAX_STAGES:
        raise PlanError(f"at most {MAX_STAGES} stages per plan.")

    # v0.9.110.0 — plan-level default llm pin + per-stage llm pin,
    # both validated against the LIVE provider catalogs at COMPILE
    # time (fail-closed authoring); catalog-unreachable degrades to
    # ok+warning (the provider 404s at turn time if truly wrong).
    from src.agent.task_llm import validate_task_llm

    plan_llm_warning: str | None = None

    def _pin_or_raise(stage_label: str, spec: str | None) -> str | None:
        nonlocal plan_llm_warning
        spec = str(spec or "").strip()
        if not spec:
            return None
        verdict = validate_task_llm(spec)
        if not verdict.get("ok"):
            raise PlanError(
                f"{stage_label}: task llm pin rejected — "
                f"{verdict.get('error')}",
            )
        plan_llm_warning = verdict.get("warning") or plan_llm_warning
        return f"{verdict['provider']}:{verdict['model']}"

    default_llm_pin = _pin_or_raise("default_llm", default_llm)

    seen: dict[str, dict] = {}
    order: list[str] = []
    for i, raw in enumerate(stages, start=1):
        if not isinstance(raw, dict):
            raise PlanError(f"stage #{i} must be a dict.")
        title = str(raw.get("title") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        if not title:
            raise PlanError(f"stage #{i}: 'title' is required.")
        if not prompt:
            raise PlanError(f"stage #{i} {title!r}: 'prompt' is required.")
        dup = next((sid for sid in order
                    if seen[sid]["title"] == title
                    and seen[sid]["prompt"] == prompt), None)
        if dup is not None:
            extra_dep = [str(x).strip()
                         for x in (raw.get("depends_on") or [])
                         if str(x).strip()]
            if extra_dep:
                # merging a duplicate's deps into the first copy can
                # invert the graph and manufacture cycles — reject
                # instead (fail-closed authoring guard).
                raise PlanError(
                    f"duplicate stage {title!r} carries depends_on "
                    f"{extra_dep} — remove the duplicate or restructure "
                    "the stage list.",
                )
            seen[dup]["deduped"] = True
            continue
        if len(order) >= MAX_STAGES:
            raise PlanError(f"at most {MAX_STAGES} stages per plan.")
        try:
            budget = int(raw.get("budget") or 0)
        except (TypeError, ValueError):
            raise PlanError(
                f"stage {title!r}: budget must be an integer.",
            )
        budget = max(1, min(budget or budget_default, 200))
        deps = [str(x).strip() for x in (raw.get("depends_on") or [])
                if str(x).strip()]
        sid = str(raw.get("id") or "").strip() or _slug(title, i)
        sid = re.sub(r"[^0-9A-Za-z_-]", "", sid) or f"stage-{i}"
        verify = raw.get("verify") if isinstance(raw.get("verify"),
                                                 dict) else None
        if verify is not None:
            vpath = str(verify.get("path") or "").strip()
            if not vpath:
                raise PlanError(
                    f"stage {title!r}: verify.path is required."
                )
            verify = {
                "path": vpath,
                "checks": list(verify.get("checks") or []),
            }
        if sid in seen:
            raise PlanError(f"duplicate stage id {sid!r}.")
        seen[sid] = {
            "id": sid, "title": title, "prompt": prompt,
            "budget": budget, "depends_on": deps, "verify": verify,
            "llm": _pin_or_raise(
                f"stage {title!r}", raw.get("llm")),
            # v0.9.111.0 — per-stage tool scope overrides the plan-level
            # default; None = full gated surface (zero change).
            "tools_allow": (
                _validate_tools_allow(
                    raw.get("tools_allow"), f"stage {title!r}",
                )
                or plan_allow
            ),
            "handoff_path": handoff_rel(sid),
            "wave": 0, "status": "pending",
            "task_id": None, "sid": None, "started_at": None,
            "finished_at": None, "error": None,
            "answer_chars": None, "verify_ok": None,
            "verify_errors": None,
        }
        order.append(sid)

    # resolve deps (by id OR by title), validate, cycle-check (Kahn).
    def dep_id(d: str) -> str | None:
        if d in seen:
            return d
        for sid in order:
            if seen[sid]["title"] == d:
                return sid
        return None

    indeg: dict[str, int] = {}
    edges: dict[str, set] = {sid: set() for sid in order}
    for sid in order:
        deps = []
        for d in seen[sid]["depends_on"]:
            got = dep_id(d)
            if got is None:
                raise PlanError(
                    f"stage {seen[sid]['title']!r}: unknown dependency "
                    f"{d!r} — use a stage id or an exact stage title.",
                )
            if got == sid:
                raise PlanError(
                    f"stage {seen[sid]['title']!r} depends on itself.",
                )
            if got not in deps:
                deps.append(got)
        seen[sid]["depends_on"] = deps
        indeg[sid] = len(deps)
        for d in deps:
            edges[d].add(sid)

    queue = [sid for sid in order if indeg[sid] == 0]
    topo: list[list[str]] = []
    remaining = set(order)
    while queue:
        wave = queue[:]
        topo.append(wave)
        nxt: list[str] = []
        for sid in wave:
            remaining.discard(sid)
            for nxt_id in sorted(edges[sid]):
                indeg[nxt_id] -= 1
                if indeg[nxt_id] == 0:
                    nxt.append(nxt_id)
        queue = nxt
    if remaining:
        raise PlanError(
            "dependency cycle detected among stages: "
            + ", ".join(sorted(remaining)),
        )
    for wi, wave in enumerate(topo):
        for sid in wave:
            seen[sid]["wave"] = wi

    # pack topo waves by cap (independent stages beyond cap → later wave)
    cap = _wave_cap(wave_cap)
    packed: list[list[str]] = []
    for wave in topo:
        chunk: list[str] = []
        for sid in wave:
            chunk.append(sid)
            if len(chunk) >= cap:
                packed.append(chunk)
                chunk = []
        if chunk:
            packed.append(chunk)
    for wi, wave in enumerate(packed):
        for sid in wave:
            seen[sid]["wave"] = wi

    pid = ("sk-" + re.sub(r"[^0-9a-z]+", "-", name.lower()).strip("-")
           [:24] + "-" + uuid.uuid4().hex[:6])
    stages_out = [seen[sid] for sid in order]
    try:
        notify_chat_id = (int(notify_chat_id)
                          if notify_chat_id else None)
    except (TypeError, ValueError):
        notify_chat_id = None
    for i, st in enumerate(stages_out):
        st["_notify_chat_id"] = notify_chat_id
        # v0.9.111.0 — build the upstream handoff map from the RESOLVED
        # dependency edges (title + absolute handoff artifact path).
        deps_rows = [
            {"id": d, "title": seen[d]["title"],
             "path": handoff_abs(d)}
            for d in st["depends_on"]
        ]
        st["compiled_prompt"] = _compile_prompt(
            st, plan_name=name,
            waves_total=len(packed), stage_idx=i,
            stages_total=len(stages_out),
            deps_handoff=deps_rows or None,
        )
    rec = {
        "id": pid, "name": name,
        "description": str(description or "")[:500],
        "on_failure": on_failure,
        "notify_chat_id": (int(notify_chat_id)
                           if notify_chat_id else None),
        "default_llm": default_llm_pin,
        "llm_warning": plan_llm_warning,
        "tools_allow": plan_allow,
        "default_budget": budget_default,
        "stage_timeout_s": (int(stage_timeout_s) if stage_timeout_s
                            is not None else _stage_timeout_s()),
        "wave_cap": cap if cap < 9999 else 0,
        "status": "compiled",
        "created_at": _now(), "updated_at": _now(),
        "waves": packed, "stages": stages_out,
    }
    save_plan(rec)
    logger.info(
        "[tacti] compiled plan %s name=%r stages=%d waves=%d "
        "policy=%s", pid, name, len(stages_out), len(packed),
        on_failure,
    )
    return rec

# ─── execution / status / resume ──────────────────────────────────

def _reflect_postmortem(rec: dict, final: str, plan_id: str) -> None:
    """P3 (v0.9.124.0) — Evo² 雙角色事後檢討 (Reflector→Proposer)：
    一個 TACTI plan 以 failed/partial 收場且任一 stage 失敗時，
    背景追加一個檢討回合：Reflector 角色重看失敗 stage 的 session
    與 handoff 檔 → Proposer 把結論落實成 rules_add / rules_revise
    （證據=failure handoff / session 摘要）或 skill_search 補位建議；
    無證據不得修訂（fail-closed）。
    Opt-in：``QDRANT_REFLECT_POSTMORTEM=on``（預設 off — 會燒 LLM
    tokens）。永不 raise；agent factory 未接線 / 已停用 → 直接跳過。
    """
    gate = (os.environ.get("QDRANT_REFLECT_POSTMORTEM", "") or "").strip()
    if gate.lower() not in ("on", "1", "yes", "true"):
        return
    if _AGENT_FACTORY is None:
        logger.debug("[tacti] post-mortem skipped (no agent factory)")
        return
    failed = [
        s for s in rec.get("stages") or []
        if s.get("status") == "failed"
    ]
    if not failed:
        return
    try:
        from src.agent import task_runner
    except Exception:  # noqa: BLE001
        task_runner = None  # type: ignore[assignment]

    rows = []
    for s in failed:
        sid = s.get("sid") or ""
        handoff = (
            handoff_abs(s["id"]) if _handoff_root() else handoff_rel(s["id"])
        )
        rows.append(
            f"• stage {s['id']}「{s.get('title')}」 — "
            f"error: {str(s.get('error') or '')[:200]}；"
            f"session: {sid or 'n/a'}；handoff: {handoff}"
        )
    prompt = (
        "你是 SELF-EVOLVING 複盤雙角色（Evo²借鏡）：先 Ref後 Prop。\n"
        f"plan {plan_id}「{rec.get('name')}」以 {final} 收場，失敗 stage：\n"
        + "\n".join(rows) + "\n"
        "工作流程（一個回合內完成）：\n"
        "1. Reflector —（read/get_chat_session 讀失敗 stage 的 handoff "
        "檔與 session 全文）找出根因類型：實作 bug（腳本/流程錯）還是 "
        "理解錯誤（對環境的假設錯，Memento 雙層分診）。\n"
        "2. Proposer — 把結論落實：\n"
        "   (a) 對環境的假設錯 → 規則簿修訂：rules_revise(rule_id, "
        "new_text, evidence=引這次失敗的 handoff/session 事實) 或 "
        "rules_add(title, text, evidence)；【嚴禁】無證據修訂 — 規則簿 "
        "工具會 fail-closed 拒絕，不要重試偽造。\n"
        "   (b) 實作 bug → 修腳本/流程的具體建議（可 skill_search 找 "
        "已驗證配方補位，Evo² crossover）。\n"
        "3. 收尾：一句話說明你動了哪些規則（id）與為什麼；什麼都沒動"
        "（例如根因是一次性 typo）也要誠實說「不做修訂」，不要為了"
        "交差而硬改規則簿。"
    )

    tid = None
    if task_runner is not None:
        try:
            tid = task_runner.begin_task(
                "plan-postmortem",
                f"[plan] {rec.get('name')}事後檢討 ({plan_id})",
            )
        except Exception:  # noqa: BLE001
            tid = None

    def _work():
        try:
            stage_llm = rec.get("default_llm")

            def _mk():
                try:
                    return _AGENT_FACTORY(llm=stage_llm)
                except TypeError:
                    return _AGENT_FACTORY()

            agent = _mk()
            result = agent.chat_turn(question=prompt, use_tools=True)
            if isinstance(result, dict):
                answer = result.get("answer")
            elif isinstance(result, tuple) and result:
                answer = result[0]
            else:
                answer = None
            text = str(answer or "").strip()
            logger.info(
                "[tacti] post-mortem %s done (%d chars; rules_"
                "revisions noted in the answer + memory sink)",
                plan_id, len(text),
            )
            if tid and task_runner:
                task_runner.end_task(
                    tid, task_runner.DONE, answer_chars=len(text),
                )
        except Exception as e:  # noqa: BLE001 — post-mortem 永不炸 driver
            logger.warning(
                "[tacti] post-mortem %s crashed: %r", plan_id, e,
            )
            if tid and task_runner:
                try:
                    task_runner.end_task(
                        tid, task_runner.FAILED, error=repr(e)[:300],
                    )
                except Exception:  # noqa: BLE001
                    pass

    threading.Thread(
        target=_work, daemon=True,
        name=f"plan-postmortem-{plan_id}",
    ).start()


def get_plan_status(plan_id: str) -> dict | None:
    rec = load_plan(plan_id)
    if rec is None:
        return None
    stages = [
        {
            "id": s["id"], "title": s["title"],
            "wave": s["wave"] + 1, "status": s["status"],
            "task_id": s.get("task_id"), "sid": s.get("sid"),
            "budget": s["budget"],
            "llm": s.get("llm"),
            "tools_allow": s.get("tools_allow"),
            "handoff_path": s.get("handoff_path"),
            "verify_ok": s.get("verify_ok"),
            "answer_chars": s.get("answer_chars"),
            "error": s.get("error"),
        } for s in rec["stages"]
    ]
    done = sum(1 for s in rec["stages"] if s["status"] == "done")
    running = sum(1 for s in rec["stages"] if s["status"] == "running")
    return {
        "id": rec["id"], "name": rec["name"],
        "description": rec.get("description") or "",
        "status": rec["status"], "on_failure": rec["on_failure"],
        "waves": len(rec["waves"]),
        "progress": f"{done}/{len(rec['stages'])}",
        "running": running,
        "notify_chat_id": rec.get("notify_chat_id"),
        "stages": stages,
        "note": (
            "get_chat_session(sid) 讀 stage 全文；"
            "run_plan_stage(plan_id, stage_id) 重跑單一 stage；"
            "run_plan 會 resume（done 跳過、running 歸位 pending）。"
        ),
    }


def _stage(rec: dict, stage_id: str) -> dict | None:
    for s in rec["stages"]:
        if s["id"] == stage_id:
            return s
    return None


def _guard_join(worker: threading.Thread, timeout_s: int) -> bool:
    """True = alive (timed out). Mirrors the v0.9.107.1 pattern."""
    deadline = time.monotonic() + max(1, timeout_s)
    while worker.is_alive() and time.monotonic() < deadline:
        worker.join(0.5)
    return worker.is_alive()


def _run_stage_async(plan_id: str, stage_id: str) -> None:
    threading.Thread(
        target=_run_stage, args=(plan_id, stage_id),
        daemon=True, name=f"plan-{plan_id}-{stage_id}",
    ).start()


def _mark(rec: dict, stage_id: str, status: str, **fields) -> None:
    s = _stage(rec, stage_id)
    if s is None:
        return
    s["status"] = status
    s.update(fields)
    save_plan(rec)


def _mark_plan(plan_id: str, stage_id: str, status: str,
               **fields) -> None:
    """FRESH-LOAD the record per write — stage threads each mutate
    their own per-thread copy, so two concurrent writers can never
    eat each other's updates (the shared cached-dict race)."""
    fresh = load_plan(plan_id)
    if fresh is None:
        return
    _mark(fresh, stage_id, status, **fields)


def _notify_plan(rec: dict, terminal_status: str) -> None:
    cb = _PLAN_NOTIFY_CB
    if cb is None:
        return
    done = sum(1 for s in rec["stages"] if s["status"] == "done")
    failed = sum(1 for s in rec["stages"] if s["status"] == "failed")
    skipped = sum(1 for s in rec["stages"] if s["status"] == "skipped")
    icon = {"done": "✅", "failed": "❌", "partial": "⚠️",
            "stopped": "⏹"}.get(terminal_status, "ℹ️")
    ev = {
        "kind": "plan", "plan_id": rec["id"], "name": rec["name"],
        "status": terminal_status, "progress": (
            f"{done} done / {failed} failed / {skipped} skipped"
            f"（共 {len(rec['stages'])} stages）"
        ),
        "text": (
            f"{icon} 計畫〔{rec['name']}〕"
            f"{'完成' if terminal_status == 'done' else '結束'}"
            f" — {done} done / {failed} failed / {skipped} skipped"
            f"（共 {len(rec['stages'])} stages）｜"
            f"get_plan_status({rec['id']}) 查細節"
        ),
    }
    try:
        cb(ev)
    except Exception as e:  # noqa: BLE001
        logger.debug("[tacti] plan notify failed: %r", e)


def run_plan(plan_id: str) -> dict:
    """Start (or RESUME) a compiled plan's wave driver.

    Resume semantics: orphan ``running`` stages (dead process) reset to
    pending; done stages skip. A DONE plan re-run = full reset (skills
    are re-runnable); failed/partial plans resume from what's left.
    """
    rec = load_plan(plan_id)
    if rec is None:
        return {"error": f"plan {plan_id!r} not found — list_plans?"}
    with _LOCK:
        was_running = rec["status"] == "running"
        for s in rec["stages"]:
            if s["status"] == "running":
                s["status"] = "pending"
                s["task_id"] = None
                s["status_changed"] = "orphan reset"
        if rec["status"] == "done":
            # skill re-run: full reset (except keep verify configs)
            for s in rec["stages"]:
                s["status"] = "pending"
                if s.get("task_id"):
                    s["task_id"] = None
        rec["status"] = "running"
        _STOPS.discard(rec["id"])
        with _EXT_LOCK:
            _EXTENSIONS.pop(plan_id, None)  # fresh budget per run
        save_plan(rec)
    # (resume already handled above)
    logger.info(
        "[tacti] plan %s driver started (name=%r)", rec["id"],
        rec["name"],
    )
    threading.Thread(
        target=_driver, args=(rec["id"],),
        daemon=True, name=f"plan-driver-{rec['id']}",
    ).start()
    return {
        "ok": True, "plan_id": rec["id"], "name": rec["name"],
        "status": "running", "waves": len(rec["waves"]),
        "stages": len(rec["stages"]),
        "note": ("Driver runs in the background — "
                 "check with get_plan_status / list_tasks."
                 ),
    }


def _driver(plan_id: str) -> None:
    rec = load_plan(plan_id)
    if rec is None:
        return
    wave_cap = max(1, rec.get("wave_cap") if rec.get("wave_cap")
                   else 9999)
    for wi, wave in enumerate(rec["waves"]):
        if plan_id in _STOPS:
            break
        # resume: skipped stages whose deps are now done (the user
        # retried them via run_plan_stage) get revived to pending.
        for sid in wave:
            s = _stage(rec, sid)
            if s["status"] == "skipped":
                bad_dep = [
                    d for d in s["depends_on"]
                    if _stage(rec, d)["status"] != "done"
                ]
                if not bad_dep:
                    _mark_plan(plan_id, sid, "pending", error=None)
                    s["status"] = "pending"
        to_run = [
            sid for sid in wave
            if _stage(rec, sid)["status"] not in TERMINAL
        ]
        # dependency gate: a stage whose deps failed → skipped
        for sid in to_run:
            s = _stage(rec, sid)
            failed_dep = [
                d for d in s["depends_on"]
                if _stage(rec, d)["status"] != "done"
            ]
            if failed_dep:
                _mark_plan(plan_id, sid, "skipped",
                      error="dependency not done: "
                            + ", ".join(failed_dep))
        to_run = [sid for sid in to_run
                  if _stage(rec, sid)["status"] == "pending"]
        if not to_run:
            continue
        for sid in to_run:
            _run_stage_async(plan_id, sid)
        # wait until every stage of this wave is terminal
        # (v0.9.117.0 — the deadline stretches with every granted
        #  timeout extension; the judge lives inside _guarded_chat_turn)
        wave_started = time.monotonic()
        ext_base = (rec.get("stage_timeout_s")
                    if isinstance(rec.get("stage_timeout_s"), int)
                    else _stage_timeout_s()) or 0
        deadline = _wave_deadline(wave_started, ext_base, 0)
        while True:
            time.sleep(0.7)
            rec = load_plan(plan_id) or rec
            if plan_id in _STOPS:
                break
            ext = _extensions_used(plan_id, wave)
            if ext:
                deadline = max(deadline,
                               _wave_deadline(wave_started, ext_base,
                                              ext))
            if time.monotonic() >= deadline:
                break
            if all(_stage(rec, sid)["status"] in TERMINAL
                   for sid in wave):
                break
        rec = load_plan(plan_id) or rec
        if plan_id in _STOPS:
            break
        failed = [sid for sid in wave
                  if _stage(rec, sid)["status"] == "failed"]
        if failed and rec["on_failure"] == "stop":
            # stop policy — later waves skipped, plan failed
            for later in rec["waves"][wi + 1:]:
                for sid in later:
                    if _stage(rec, sid)["status"] not in TERMINAL:
                        _mark_plan(plan_id, sid, "skipped",
                              error="plan stopped at an earlier wave")
            rec = load_plan(plan_id) or rec
            rec["status"] = "failed"
            save_plan(rec)
            _notify_plan(rec, "failed")
            logger.warning(
                "[tacti] plan %s FAILED at wave %d/%d (%s) — stopped",
                plan_id, wi + 1, len(rec["waves"]), failed,
            )
            return
    rec = load_plan(plan_id) or rec
    if plan_id in _STOPS:
        for s in rec["stages"]:
            if s["status"] == "running":
                s["status"] = "failed"
                s["error"] = "stopped by user"
            elif s["status"] == "pending":
                s["status"] = "skipped"
                s["error"] = "plan stopped"
        rec["status"] = "stopped"
        save_plan(rec)
        _notify_plan(rec, "stopped")
        return
    statuses = [s["status"] for s in rec["stages"]]
    final = ("done" if all(x == "done" for x in statuses)
             else ("failed" if "failed" in statuses
                   or "skipped" in statuses else "partial"))
    rec["status"] = final
    save_plan(rec)
    _notify_plan(rec, final)
    _sink_plan(rec, final)
    _reflect_postmortem(rec, final, plan_id)
    logger.info("[tacti] plan %s -> %s", plan_id, final)


def _run_stage(plan_id: str, stage_id: str) -> None:
    rec = load_plan(plan_id)
    if rec is None:
        return
    s = _stage(rec, stage_id)
    if s is None:
        return
    stage_sid = f"plan-{plan_id}-{stage_id}"
    # v0.9.110.0 — stage llm pin > plan default_llm pin > default chain
    # (computed BEFORE begin_task so the registry row shows the pin).
    stage_llm = (str(s.get("llm") or "").strip()
                 or str(rec.get("default_llm") or "").strip() or None)
    # v0.9.111.0 — MECHANICAL tool scope: stage allow-list > plan
    # allow-list > None (full gated surface, zero change). Enforced by
    # chat_turn(active_tool_names=...) — the per-TOOL gate from
    # v0.9.14.0, not prompt admonitions.
    tools_allow = (s.get("tools_allow")
                   or rec.get("tools_allow") or None)
    from src.agent import task_runner

    tid = task_runner.begin_task(
        "plan", f"{rec['name']}▸{s['title']}", sid=stage_sid,
        llm=stage_llm,
    )
    _mark_plan(plan_id, stage_id, "running",
          task_id=tid, sid=stage_sid, error=None,
          started_at=_now(), finished_at=None,
          answer_chars=None, verify_ok=None, verify_errors=None)
    if _AGENT_FACTORY is None:
        _mark_plan(plan_id, stage_id, "failed",
              error="agent factory not wired (in-process host only)",
              finished_at=_now())
        task_runner.end_task(tid, task_runner.FAILED,
                             error="agent factory missing")
        return
    prompt = s.get("compiled_prompt") or s.get("prompt")
    try:
        if stage_llm:
            try:
                agent = _AGENT_FACTORY(llm=stage_llm)
            except TypeError:
                # legacy zero-arg factory — the pin cannot be applied
                logger.warning(
                    "[tacti] agent factory predates llm pins — stage "
                    "%s runs on the default chain", stage_id,
                )
                agent = _AGENT_FACTORY()
        else:
            agent = _AGENT_FACTORY()
    except Exception as e:
        _mark_plan(plan_id, stage_id, "failed",
              error=f"agent build failed: {e!r}", finished_at=_now())
        task_runner.end_task(tid, task_runner.FAILED, error=repr(e))
        return
    # v0.9.120.0 — cooperative force-stop for the stage turn: the
    # flag (set via stop_plan → request_stop) ends the zombie/in-
    # flight stage turn at its next tool-loop boundary; on return
    # below the driver's marks are NOT overwritten when set.
    try:
        _setter = getattr(agent, "set_stop_check", None)
        if callable(_setter):
            _setter(lambda: task_runner.stop_requested(tid))
        else:
            setattr(agent, "_stop_check",
                    lambda: task_runner.stop_requested(tid))
    except Exception:
        pass
    history = None
    try:
        from src.agent.session import Session, SessionManager

        sess = Session(sid=stage_sid,
                       title=f"[plan] {rec['name']}▸{s['title']}")
        sess.messages.append({
            "role": "user", "content": prompt, "ts": _now(),
        })
        # v0.9.111.0 — thread the MECHANICAL tool scope through the same
        # per-TOOL gate the GUI tool picker uses (v0.9.14.0).
        turn_kw: dict = dict(question=prompt, history=history,
                             use_tools=True)
        if tools_allow:
            turn_kw["active_tool_names"] = list(tools_allow)
        try:
            result, timed_out = _guarded_chat_turn(
                agent, rec.get("stage_timeout_s")
                if isinstance(rec.get("stage_timeout_s"), int) else None,
                meta={"plan_id": plan_id, "stage_id": stage_id,
                      "title": s.get("title"), "question": prompt},
                **turn_kw,
            )
        except Exception as chat_exc:
            sess.messages.append({
                "role": "assistant",
                "content": f"[error] {chat_exc!r}",
                "ts": _now(), "sources": [], "error": True,
            })
            sess.updated_at = _now()
            try:
                SessionManager().save(sess)
            except Exception:
                pass
            _mark_plan(plan_id, stage_id, "failed",
                  error=repr(chat_exc)[:500], finished_at=_now())
            task_runner.end_task(tid, task_runner.FAILED,
                                 error=repr(chat_exc)[:500])
            return
        if timed_out:
            ext_used = _extensions_for(plan_id, stage_id)
            err = (f"stage hard timeout after "
                   f"{rec.get('stage_timeout_s')}s")
            if ext_used:
                err += (f" (+{ext_used} extension(s) granted — "
                        "liveness/Jev did not vouch for another "
                        "window)")
            sess.messages.append({
                "role": "assistant", "content": f"[timeout] {err}",
                "ts": _now(), "sources": [], "error": True,
            })
            sess.updated_at = _now()
            try:
                SessionManager().save(sess)
            except Exception:
                pass
            _mark_plan(plan_id, stage_id, "failed", error=err,
                  finished_at=_now())
            task_runner.end_task(tid, task_runner.FAILED,
                                 error=err)
            return
        if isinstance(result, dict):
            answer = result.get("answer")
        elif isinstance(result, tuple) and len(result) >= 1:
            answer = result[0]
        else:
            answer = None
        answer_text = str(answer or "").strip()
        # v0.9.120.0 — a stop request ends the stage turn as STOPPED;
        # the stage marks (failed "stopped by user" from the driver)
        # stay untouched — never overwriting them with done.
        if task_runner.stop_requested(tid):
            sess.messages.append({
                "role": "assistant",
                "content": "🛑 已依要求停止本 stage — 已完成的工作保留。",
                "ts": _now(), "sources": [], "turn_stopped": True,
            })
            sess.updated_at = _now()
            try:
                SessionManager().save(sess)
            except Exception:
                pass
            task_runner.end_task(
                tid, task_runner.STOPPED, answer_chars=19)
            logger.info(
                "[tacti] stage %s ended by stop request", stage_id)
            return
        sess.messages.append({
            "role": "assistant", "content": answer_text
            or "(無回覆)", "ts": _now(), "sources": [],
        })
        sess.updated_at = _now()
        try:
            SessionManager().save(sess)
        except Exception as e:
            logger.warning("[tacti] session save(%s) failed: %r",
                           stage_sid, e)
        # verification node (mechanical, v0.9.106 evaluator)
        v = s.get("verify")
        if v:
            try:
                from src.agent.scheduler import verify_artifact

                report = verify_artifact(v["path"],
                                         checks=v.get("checks"))
                ok = bool(report.get("ok"))
                _mark_plan(plan_id, stage_id, "done" if ok else "failed",
                      answer_chars=len(answer_text),
                      verify_ok=ok,
                      verify_errors=report.get("errors") or [],
                      finished_at=_now(),
                      error=None if ok else
                      "verify failed: "
                      + "; ".join(str(x)[:120]
                                  for x in (report.get("errors")
                                            or ["n/a"])[:5]))
            except Exception as e:
                _mark_plan(plan_id, stage_id, "failed",
                      answer_chars=len(answer_text),
                      error=f"verify crashed: {e!r}",
                      finished_at=_now())
        else:
            _mark_plan(plan_id, stage_id, "done",
                  answer_chars=len(answer_text),
                  finished_at=_now())
        st = _stage(load_plan(plan_id) or rec, stage_id)
        status = st["status"]
        # v0.9.111.0 — MECHANICAL handoff write BEFORE the stage goes
        # public-terminal: dependents can only start after the driver
        # sees terminal status, so the file always precedes the reader.
        artifact = None
        if status in ("done", "failed") and s.get("verify"):
            artifact = (s["verify"] or {}).get("path")
        # v0.9.124.0 — Evo² crossover on failure: the handoff file of a
        # FAILED stage carries an explicit recovery hint so the retry /
        # downstream turns search the SKILL library instead of blindly
        # re-deriving (report ⑥).
        recovery_hint = None
        if status == "failed":
            _t = str(s.get("title") or "?")[:60]
            recovery_hint = (
                f"recovery (Evo² crossover): skill_search('{_t}') the "
                "SKILL.md library for a proven recipe covering this "
                "weakness; adapt a donor skill rather than re-deriving. "
                "Also consider rules_add / rules_revise — cite THIS "
                "failure as the evidence (the rulebook rides the "
                "system prompt)."
            )
        wrote = _write_handoff(
            plan_id, stage_id, status, answer_text,
            session=stage_sid, artifact_path=artifact,
            recovery_hint=recovery_hint,
        )
        if wrote:
            logger.info(
                "[tacti] handoff %s -> %s (%s bytes)",
                stage_id, wrote["path"], wrote["bytes"],
            )
        task_runner.end_task(
            tid, status,
            answer_chars=len(answer_text),
            error=st.get("error"),
        )
        logger.info("[tacti] stage %s/%s -> %s", plan_id, stage_id,
                    status)
    except Exception as e:  # noqa: BLE001 — defence, mirror task host
        _mark_plan(plan_id, stage_id, "failed", error=repr(e)[:500],
              finished_at=_now())
        try:
            task_runner.end_task(tid, task_runner.FAILED,
                                 error=repr(e)[:500])
        except Exception:
            pass
    finally:
        with _LOCK:
            # drop the plan cache so the driver re-reads fresh state
            _PLANS.pop(plan_id, None)
        # wait — cache drop then _driver reload from file each poll.


def _guarded_chat_turn(
    agent: Any, timeout_s: int | None,
    meta: dict | None = None, **kw,
) -> tuple:
    """v0.9.107.1 wall-clock guard + v0.9.117.0/118.0 hard-timeout
    arbiter (the shared ``turn_arbiter.guarded_run_join`` judge:
    tool-dispatch liveness first, live Jev verdict for silent runs;
    fail-closed kill otherwise). ``meta`` carries plan/stage/
    title/question for the evidence text ONLY — never chat_turn.
    Returns ``(result, timed_out)`` like before."""
    to = int(timeout_s) if timeout_s else _stage_timeout_s()
    if to <= 0:
        return agent.chat_turn(**kw), False
    from src.agent import turn_arbiter

    stage_id = str((meta or {}).get("stage_id") or "?")
    plan_id = str((meta or {}).get("plan_id") or "")
    box: dict = {}

    def _run() -> None:
        try:
            box["result"] = agent.chat_turn(**kw)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    w = threading.Thread(target=_run, daemon=True,
                         name="skill-stage-guard")
    w.start()
    result, timed_out, _used = turn_arbiter.guarded_run_join(
        w, box, agent, window_s=to,
        log_label=f"[tacti] stage {stage_id}",
        subject="背景 stage ", meta=meta,
        extensions_env="QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS",
        extensions_default=_EXTENSIONS_DEFAULT,
        verdict_fn=lambda text: _jev_timeout_verdict(text),
        grant=lambda: _grant_extension(plan_id, stage_id),
        stopped=lambda: plan_id in _STOPS,
    )
    return result, timed_out


def _evidence_snapshot(agent: Any) -> dict:
    """Thin delegate to the shared arbiter (module name kept for the
    v0.9.117.0 tests / callers)."""
    from src.agent import turn_arbiter

    return turn_arbiter.evidence_snapshot(agent)


def _timeout_evidence_text(agent: Any, elapsed_s: float,
                           snap: dict, meta: dict | None) -> str:
    """Thin delegate — same text, subject pinned to the stage."""
    from src.agent import turn_arbiter

    return turn_arbiter.evidence_text(
        agent, elapsed_s, snap, "背景 stage ", meta)


def _jev_timeout_verdict(text: str) -> str | None:
    """Thin wrapper — gate check (skill knob or the shared knob) then
    the shared verdict; module-global name kept so the v0.9.117.0
    tests keep monkeypatching this exact symbol."""
    if not _timeout_jev_enabled():
        return None
    from src.agent import turn_arbiter

    return turn_arbiter.jev_verdict(text)


def stop_plan(plan_id: str) -> dict:
    if load_plan(plan_id) is None:
        return {"error": f"plan {plan_id!r} not found."}
    _STOPS.add(plan_id)
    # v0.9.120.0 — the stop flag also reaches the STAGE THREADS: the
    # zombie/in-flight stage turn ends at its next tool-loop boundary
    # (the only stop channel that reaches abandoned runs).
    try:
        from src.agent import task_runner

        rec = load_plan(plan_id) or {}
        for s in rec.get("stages", []) or []:
            tid2 = s.get("task_id")
            if tid2 and s.get("status") in ("running", "failed",
                                            "done"):
                # zombies of any terminal row keep honouring flags
                task_runner.request_stop(str(tid2))
    except Exception as e:  # noqa: BLE001
        logger.warning("[tacti] stop_plan flags failed: %r", e)
    logger.info("[tacti] plan %s stop requested", plan_id)
    return {"ok": True, "plan_id": plan_id,
            "note": ("stop requested — running stages marked failed, "
                     "pending skipped; get_plan_status to confirm.")}


def delete_plan(plan_id: str, confirm: bool = False,
                force: bool = False) -> dict:
    """v0.9.121.2 — IRREVERSIBLY delete a compiled plan.

    * ``confirm=false`` (default) → fail-closed error.
    * A RUNNING plan is refused (stop_plan first — 120.0 stop flags
      end the in-flight/zombie stage turns) unless ``force=true``
      (scatter the stop flags, then delete in one call).
    * Removal covers the FILE + the in-memory cache; the task
      registry rows and stage sessions survive (history intact).
    * A still-alive zombie driver cannot resurrect the plan —
      ``save_plan`` of a deleted id is a no-op (``_DELETED`` guard).
    """
    pid = str(plan_id or "").strip()
    if not pid:
        return {"error": "plan_id is required."}
    if not confirm:
        return {"error": (
            "delete_plan is IRREVERSIBLE — pass confirm=true "
            "(plans/<id>.json is removed; re-run = a fresh "
            "compile_plan).")}
    rec = load_plan(pid)
    if rec is None:
        return {
            "error": f"plan {pid!r} not found — list_plans?",
            "note": ("already deleted (or unknown) — either way "
                     "nothing to do."),
        }
    if rec["status"] == "running" and not force:
        return {"error": (
            f"plan {pid!r} is RUNNING — stop_plan({pid}) first "
            "(it also ends the in-flight/zombie stage turns), then "
            "delete_plan; or pass force=true for stop+delete in one "
            "call.")}
    if rec["status"] == "running" and force:
        # scatter the stop flags — the driver loop and every stage
        # thread honour them at their next boundary (v0.9.120.0)
        _STOPS.add(pid)
        try:
            from src.agent import task_runner

            for s in rec.get("stages", []) or []:
                tid2 = s.get("task_id")
                if tid2:
                    task_runner.request_stop(str(tid2))
        except Exception as e:  # noqa: BLE001
            logger.warning("[tacti] delete stop flags failed: %r", e)
    with _LOCK:
        _DELETED.add(pid)
        _PLANS.pop(pid, None)
        _STOPS.discard(pid)
        with _EXT_LOCK:
            _EXTENSIONS.pop(pid, None)
    f = plan_file(pid)
    try:
        if f.exists():
            f.unlink()
        for tmp in plans_dir().glob(f"{pid}.json.tmp*"):
            try:
                tmp.unlink()
            except OSError:
                pass
    except OSError as e:
        return {"error": f"plan file delete failed: {e!r} "
                         "(the plan is un-cached; retry delete_plan)"}
    logger.info("[tacti] plan %s deleted (name=%r)", pid, rec["name"])
    return {
        "ok": True, "deleted": pid, "name": rec["name"],
        "note": ("plan file removed — task-registry rows and stage "
                 "sessions survive (history intact)"),
    }


def run_plan_stage(plan_id: str, stage_id: str) -> dict:
    """Retry ONE stage (failed/skipped/pending only) without the driver.

    Re-run a whole plan later with ``run_plan`` (done stages skip) to
    continue dependent waves.
    """
    rec = load_plan(plan_id)
    if rec is None:
        return {"error": f"plan {plan_id!r} not found."}
    if rec["status"] == "running":
        return {"error": ("plan is running — use stop_plan first or "
                          "check get_plan_status.")}
    s = _stage(rec, stage_id)
    if s is None:
        return {"error": (f"stage {stage_id!r} not in plan; use "
                          f"get_plan_status({plan_id}).")}
    if s["status"] == "done":
        return {"error": (f"stage {stage_id!r} already done — re-run via "
                          "run_plan (full reset) if intended.")}
    _mark_plan(plan_id, stage_id, "pending", error=None, task_id=None,
          sid=None, started_at=None, finished_at=None,
          answer_chars=None, verify_ok=None, verify_errors=None)
    _run_stage_async(plan_id, stage_id)
    return {
        "ok": True, "plan_id": plan_id, "stage_id": stage_id,
        "status": "running",
        "note": ("stage re-submitted — get_plan_status for progress; "
                 "to continue dependent waves afterwards, run_plan "
                 "(done stages skip)."),
    }


def _reset_for_tests() -> None:
    """TEST-ONLY: wipe the in-memory plan cache / stop flags (the
    plan FILES on disk are untouched)."""
    with _LOCK:
        _PLANS.clear()
        _STOPS.clear()
    with _EXT_LOCK:
        _EXTENSIONS.clear()
    with _LOCK:
        _DELETED.clear()
