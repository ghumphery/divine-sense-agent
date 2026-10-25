# -*- coding: utf-8 -*-
"""v0.9.118.0 — SHARED hard-timeout arbiter for long LLM turns.

Every caller that bounds a whole ``chat_turn`` under a wall-clock
guard (the v0.9.107.1 pattern: skill-compiler stage turns + the
telegram-bot turn) shares ONE judge here:

  * **Liveness (deterministic, free)** — the guarded thread is joined
    in 0.5s slices while a cheap evidence snapshot
    (``agent.last_tool_dispatches`` / ``last_usage``) is taken;
    a GROWING dispatch count at the deadline = work progressing →
    one more window WITHOUT any LLM call.
  * **Live Jev arbiter (v0.9.117.0, user proposal)** — an ambiguous
    (silent) run's evidence text goes to a real Jev classification;
    ``normal_long_work`` at prob ≥ floor → one more window.
  * **Fail-closed kill** — extensions exhausted / verdict
    ``abnormal`` / low prob / mock Jev mode / gate off / arbiter
    error → the v0.9.107.1 abandon path, byte-identical.

Extension budget per caller env (0 = off = exact v0.9.107.1):
skill stage = ``QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS`` (default 3);
telegram turn = ``QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS`` (default 3).

Shared Jev knobs (aliases — the 117.0 skill names still work):
``QDRANT_TIMEOUT_JEV`` (on default) / ``QDRANT_TIMEOUT_JEV_MIN_PROB``
(default 0.5).

Zero side effects at import (no ``src.config`` import — same
discipline as ``provider_chain.py`` / ``turn_gate.py``).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

_EXTENSIONS_DEFAULT = 3
_JEV_MIN_PROB_DEFAULT = 0.5


def resolve_extensions(env_name: str, default: int = _EXTENSIONS_DEFAULT
                       ) -> int:
    """Extra-window budget from the env (invalid/negative → default;
    0 = off)."""
    raw = (os.environ.get(env_name, "") or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return v if v >= 0 else default


def _gate_env() -> bool:
    """Jev gate ON unless one of the knobs says off (legacy skill
    alias checked first for 117.0 .env compat)."""
    for name in ("QDRANT_SKILL_TIMEOUT_JEV", "QDRANT_TIMEOUT_JEV"):
        raw = (os.environ.get(name, "") or "").strip().lower()
        if raw:
            return raw not in ("off", "0", "no", "false", "n")
    return True


def _min_prob() -> float:
    for name in ("QDRANT_SKILL_TIMEOUT_JEV_MIN_PROB",
                 "QDRANT_TIMEOUT_JEV_MIN_PROB"):
        raw = (os.environ.get(name, "") or "").strip()
        if raw:
            try:
                v = float(raw)
            except (TypeError, ValueError):
                break
            if v > 0:
                return min(0.99, max(0.05, v))
            break
    return _JEV_MIN_PROB_DEFAULT


def evidence_snapshot(agent: Any) -> dict:
    """Cheap per-poll liveness evidence off the guarded agent."""
    disp = getattr(agent, "last_tool_dispatches", None) or []
    usage = getattr(agent, "last_usage", None) or {}
    return {"dispatches": len(disp) if isinstance(disp, list) else 0,
            "tail": disp[-8:] if isinstance(disp, list) else [],
            "calls": usage.get("calls")}


def evidence_text(agent: Any, elapsed_s: float, snap: dict,
                  subject: str, meta: dict | None = None) -> str:
    """The Jev classification text: what the guarded work is + its
    measured tool activity at the deadline. ``subject`` = 「背景 stage 」
    (skill) / 「Telegram 回合 」 (bot)."""
    disp = getattr(agent, "last_tool_dispatches", None) or []
    usage = getattr(agent, "last_usage", None) or {}
    tail = ", ".join(str(d) for d in disp[-8:] if isinstance(
        disp, list))
    title = str((meta or {}).get("title") or "")
    q = str((meta or {}).get("question") or "")[:200]
    return (
        f"{subject}已連續執行 {elapsed_s / 60:.1f} 分鐘後撞上"
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


def jev_verdict(text: str) -> str | None:
    """'extend' only when a LIVE Jev confidently classifies the run as
    normal_long_work. Gate off / mock mode / error / low prob → None
    (= kill path, v0.9.107.1 behaviour). Never raises."""
    if not _gate_env():
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
            items=[{"id": "turn", "text": str(text or "")[:4000]}],
            instructions=(
                "判定一個撞上硬超時的背景工作是否仍在正常執行。"
                "工具派發持續且行為與任務相符 = normal_long_work；"
                "停滯、重複失敗、無進展 = abnormal。"),
            default_class="abnormal",
        )
        results = out.get("results") or []
        r = results[0] if results else {}
        if r.get("error"):
            logger.warning("[turn-arbiter] Jev verdict error: %r",
                           r.get("error"))
            return None
        choice = str(r.get("choice") or "")
        prob = float(r.get("prob") or 0.0)
        if choice == "normal_long_work" and prob >= _min_prob():
            logger.info("[turn-arbiter] Jev verdict: normal_long_work "
                        "p=%.2f — extending", prob)
            return "extend"
        logger.info("[turn-arbiter] Jev verdict: %s p=%.2f — kill",
                    choice, prob)
        return None
    except Exception as e:  # noqa: BLE001 — the arbiter must never
        # break the guard that hosts it
        logger.warning("[turn-arbiter] Jev verdict failed: %r", e)
        return None


def guarded_run_join(
    worker, box: dict, agent: Any,
    *, window_s: int, log_label: str, subject: str,
    meta: dict | None, extensions_env: str, extensions_default: int,
    verdict_fn: Callable[[str], str | None],
    grant: Callable[[], None] | None = None,
    stopped: Callable[[], bool] | None = None,
) -> tuple:
    """The guard join + arbitration loop (0.5s slice sampling — the
    exact skill-compiler v0.9.117.0 mechanics, now shared).

    Returns ``(result|None, timed_out, extensions_used)``; raises
    ``box["error"]`` when the worker finished by raising (the
    caller's re-raise path is preserved). ``verdict_fn`` = the
    caller's module-level wrapper (test-monkeypatchable);
    ``grant()`` = caller-side bookkeeping per extension;
    ``stopped()`` = owner abort → abandon path immediately."""
    window = max(1, int(window_s))
    deadline = time.monotonic() + window
    extensions_left = resolve_extensions(extensions_env,
                                         extensions_default)
    used = 0
    last_n: int | None = None
    while True:
        worker.join(0.5)
        if not worker.is_alive():
            if "error" in box:
                raise box["error"]
            return box.get("result"), False, used
        now = time.monotonic()
        if now < deadline:
            last_n = evidence_snapshot(agent)["dispatches"]
            continue
        # ── hard deadline hit while alive — arbitrate ──────────
        if stopped is not None and stopped():
            logger.info("%s deadline + owner stop requested — "
                        "abandoning", log_label)
            return None, True, used
        snap = evidence_snapshot(agent)
        grew = (last_n is not None
                and snap["dispatches"] > last_n)
        last_n = snap["dispatches"]
        verdict = "extend" if grew else verdict_fn(
            evidence_text(agent, now, snap, subject, meta))
        if verdict and extensions_left > 0:
            extensions_left -= 1
            used += 1
            if grant is not None:
                try:
                    grant()
                except Exception as e:  # noqa: BLE001
                    logger.warning("%s grant bookkeeping failed: %r",
                                   log_label, e)
            deadline = now + window
            logger.info(
                "%s hard timeout — window extended #%d by %s "
                "(+%ds; dispatches=%d tail=%s)",
                log_label, used, "liveness" if grew else "jev",
                window, snap.get("dispatches", 0),
                snap.get("tail")[-4:])
            continue
        logger.warning(
            "%s guard abandoning runaway turn (verdict=%s, "
            "extensions used=%d/%d)", log_label,
            verdict or "kill", used, used + extensions_left)
        return None, True, used