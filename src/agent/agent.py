"""LLM Agent — RAG question answering against Qdrant (v0.8.0+)."""
from __future__ import annotations
import json, logging, os, re, threading, time, uuid
from typing import Any

from openai import APIStatusError, OpenAI

from src.config import config
from src.agent.system_rules import load_system_rules
from src.agent.rulebook import (
    load_rulebook, rulebook_enabled, rulebook_hint_enabled,
)
from src.mcp_client import McpManager
from src.mcp_client.base import McpCallResult
from src.agent.context_compress import ContextCompressor
from src.agent.turn_gate import turn_slot
from src.tools.web_search import build_web_search
from src.vector_store.retriever import Retriever



def _get_config():
    """Late-bound lookup of the Config singleton so that
    ``importlib.reload(src.config)`` (used by tests + REPL
    tinkering) is reflected in subsequent ``Agent().client``
    calls without reloading this module too. See
    ``_PROVIDER_ORDER`` for the only caller."""
    from src import config as _cfg_mod
    return _cfg_mod.config


logger = logging.getLogger(__name__)

# v0.9.16.1 — user log 2026-09-05: a chat turn died with
# ``TypeError: 'NoneType' object is not subscriptable`` at
# ``resp.choices[0]`` — some OpenRouter upstreams answer HTTP 200
# with ``choices=None`` / ``[]`` (transient gateway failure, content
# filter, upstream 5xx tunnelled through). One bad response used to
# tear down the whole turn; now it is retried once and degrades to a
# graceful user-facing message.
EMPTY_RESPONSE_TEXT = (
    "(模型這次沒有回傳內容 — 上游供應商暫時回應異常 (empty choices),"
    "請再送出一次或稍後重試。/ The provider returned an empty "
    "response (no choices) — please retry.)"
)
_EMPTY_RESPONSE_RETRY_DELAY = 0.8  # seconds before the one retry

# v0.9.120.0 — the cooperative force-stop final answer (no extra LLM
# round: the tool loop returns this verbatim when the stop flag fires
# at an iteration boundary; completed tool work stays untouched).
TURN_STOPPED_TEXT = (
    "🛑 (已強制中止) 使用者要求停止，本回合在此收尾 — "
    "到目前為止的工具工作成果已保留（沙盒檔案/計畫不受影響）。"
    "如需續做，請說明接續方向。"
    "/ Force-stopped on user request — completed tool work is kept."
)


def _valid_completion(resp: Any) -> bool:
    """True iff the OpenAI-compatible response carries at least one
    choice with a message object. ``choices=None`` / ``[]`` / a bare
    error payload all count as invalid (they used to raise TypeError
    on ``choices[0]``)."""
    try:
        choices = getattr(resp, "choices", None)
        if not choices:
            return False
        return getattr(choices[0], "message", None) is not None
    except (IndexError, TypeError, AttributeError):
        return False


# ─── v0.9.16.2 — strip base64 blobs from tool-result messages ────
#
# User log 2026-09-06: after a successful describe_image the NEXT
# create() died with OpenRouter 404 "No endpoints found for
# z-ai/glm-5.3-flash" — routing funnel ``Filter by Context Length:
# 24 → 1 → Filter by Fallback → 0``. Cause: the tool result carried
# ``raw_image_base64`` (a multi-MB base64 of the phone photo), which
# ballooned the follow-up request past every endpoint's context
# window. Our tool loop is text-only anyway — the base64 bytes are
# never consumed — so the tool message is slimmed in place: known
# base64 keys and any >2048-char base64 run become
# ``<base64 omitted: N chars>``. Kill-switch:
# ``AGENT_STRIP_TOOL_BASE64=off``.
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/=]{2048,}")


def _base64_stripping_enabled() -> bool:
    raw = (os.environ.get("AGENT_STRIP_TOOL_BASE64", "") or "").strip().lower()
    return raw not in ("off", "0", "false", "no")


def _strip_base64_from_tool_text(text: str) -> str:
    """Slim base64 blobs out of one tool-result message (v0.9.16.2).

    1. When the text parses as JSON, every string field whose key
       mentions ``base64`` and is >= 1024 chars is replaced with a
       placeholder (dict/list-walking, description untouched).
    2. Otherwise (or when no known key matched) any single
       alphanumeric ``+/=`` run of >= 2048 chars with >= 16 DISTINCT
       characters is replaced — file contents / prose never form such
       runs, and a low-entropy run ("rrrrr…") is not base64 either.
    """
    if not _base64_stripping_enabled() or len(text) < 4096:
        return text
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, (dict, list)):
        changed = False

        def _walk(obj: Any) -> None:
            nonlocal changed
            if isinstance(obj, dict):
                for k, v in list(obj.items()):
                    if (
                        isinstance(v, str)
                        and "base64" in k.lower()
                        and len(v) >= 1024
                    ):
                        obj[k] = f"<base64 omitted: {len(v)} chars>"
                        changed = True
                    else:
                        _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)

        _walk(data)
        if changed:
            try:
                return json.dumps(data, ensure_ascii=False)
            except Exception:
                pass  # fall through to the regex pass

    def _sub_run(m: "re.Match[str]") -> str:
        run = m.group(0)
        if len(set(run)) < 16:  # low-entropy run — not base64
            return run
        return f"<base64 omitted: {len(run)} chars>"

    return _BASE64_RUN_RE.sub(_sub_run, text)


def _memory_tools() -> list[str]:
    """Whitelisted MCP tools whose SUCCESSFUL results are stored into
    the long-term-memory collection (v0.9.59.0). QDRANT_MEMORY_TOOLS
    is a comma list; default = web_search,web_fetch (stock queries
    already auto-persist via stock_docs; describe_image is excluded
    by default)."""
    raw = (os.environ.get("QDRANT_MEMORY_TOOLS", "web_search,web_fetch")
           or "").strip()
    return [t.strip() for t in raw.split(",") if t.strip()]


def _memory_top_k() -> int:
    raw = (os.environ.get("QDRANT_MEMORY_TOP_K", "5") or "5").strip()
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return 5


def _norm_for_match(s: str) -> str:
    """Whitespace/punctuation-free form for deterministic
    substring matching between a memory and the answer."""
    return re.sub(r"[\s，。；：、,.;:!！?？\"'「」『』()（）]+", "", s or "")


def _memory_phrases(content: str, min_len: int = 6) -> list[str]:
    """Normalized, length-filtered phrases from a memory — the probes
    the deterministic citation matcher looks for in the answer."""
    probes: list[str] = []
    for phrase in re.split(r"[，。；：、,.;:!？?！\n\r]+", content or ""):
        norm = _norm_for_match(phrase)
        if len(norm) >= min_len:
            probes.append(norm)
    return probes


def _resolve_max_tool_iterations() -> int:
    """v0.9.14.1 — per-call resolution of the tool-loop iteration
    budget. Reads ``os.environ`` directly (monkeypatch-friendly, the
    same pattern as OPENROUTER_REASONING_EFFORT / MCP_ACTIVE_SERVERS);
    unset, invalid, or non-positive values fall back to
    ``Agent.DEFAULT_MAX_TOOL_ITERATIONS`` with a warning — a typo in
    the env var must never take down a chat turn.
    """
    default = Agent.DEFAULT_MAX_TOOL_ITERATIONS
    raw = (os.environ.get("MAX_TOOL_ITERATIONS", "") or "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        logger.warning(
            "[agent] MAX_TOOL_ITERATIONS=%r invalid (not an int) — "
            "using default %d", raw, default,
        )
        return default
    if val < 1:
        logger.warning(
            "[agent] MAX_TOOL_ITERATIONS=%d non-positive — using "
            "default %d", val, default,
        )
        return default
    return val


_TOOL_RESULT_CAP_DEFAULT = 51200


def _tool_result_cap() -> int:
    """v0.9.104.3 — the ABSOLUTE per-tool-result char cap enforced in
    ``_run_tool_call``. System-level guard INDEPENDENT of the
    compressor (defense in depth — the live 2026-10-01 Gmail incident:
    a full-page Gmail HTML tool result (get_page_html) rode inline
    into the prompt at 1,751,656 tokens → every provider 40x → the
    whole turn died). Reads ``QDRANT_TOOL_RESULT_MAX_CHARS`` directly
    (os.environ, monkeypatch-friendly); 0 = UNLIMITED (opt-out, same
    convention as MCP_SURFACE_MAX_TOOLS); unset/invalid/negative →
    the 51,200-char default (~13k tokens)."""
    raw = (os.environ.get("QDRANT_TOOL_RESULT_MAX_CHARS", "")
           or "").strip()
    if not raw:
        return _TOOL_RESULT_CAP_DEFAULT
    try:
        v = int(raw)
    except ValueError:
        return _TOOL_RESULT_CAP_DEFAULT
    if v < 0:
        return _TOOL_RESULT_CAP_DEFAULT
    return v


# v0.9.107.0 — per-tool-call watchdog (QDRANT_TOOL_CALL_TIMEOUT_S).
_TOOL_CALL_TIMEOUT_DEFAULT_S = 0  # 0 = OFF (legacy, opt-in knob)


def _tool_call_timeout_s() -> int:
    """Watchdog seconds for ONE MCP dispatch in ``_run_tool_call``.

    The 2026-10-04 00:06 telegram turn sat ~58 minutes on a single
    never-returning tool call with ZERO log output (a dispatch is
    logged only AFTER it returns) — the user had to Ctrl+C the whole
    app. With the watchdog on, a hung call returns a normal tool
    error the loop can react to; the runaway thread is abandoned
    (daemon). 0 = off (byte-identical legacy); invalid/negative ->
    the off default."""
    raw = (os.environ.get("QDRANT_TOOL_CALL_TIMEOUT_S", "") or "").strip()
    if not raw:
        return _TOOL_CALL_TIMEOUT_DEFAULT_S
    try:
        v = int(raw)
    except ValueError:
        return _TOOL_CALL_TIMEOUT_DEFAULT_S
    if v < 0:
        return _TOOL_CALL_TIMEOUT_DEFAULT_S
    return v


def _llamacpp_chat_base() -> str:
    """v0.9.133.11 — the LOCAL llama.cpp chat server base for task
    pins (``llamacpp:<model>``). Same env key as the session compactor
    (``QDRANT_SESSION_SUMMARY_LLAMACPP_URL``, default
    ``http://127.0.0.1:8090/v1``) — one env line drives both; no
    import back into session_compactor to keep the module graph
    acyclic (compactor imports Agent)."""

    return (os.environ.get("QDRANT_SESSION_SUMMARY_LLAMACPP_URL", "")
            or "http://127.0.0.1:8090/v1").strip().rstrip("/")


_LLM_TIMEOUT_DEFAULT_S = 300.0


def _llm_timeout_s() -> float:
    """v0.9.63.1 — per-request ceiling for the chat/vision LLM HTTP
    calls. Reads ``QDRANT_LLM_TIMEOUT_S`` directly from ``os.environ``
    (monkeypatch-friendly, same pattern as MAX_TOOL_ITERATIONS);
    unset/invalid → the 300 s default with a warning; values below
    30 s are clamped up, above 1200 s clamped down.

    WHY: the openai SDK's default timeout is 600 s PER ATTEMPT with
    built-in retries — a hung / slow provider call (live diagnosis
    2026-09-16 10:08: a telegram file turn whose prompt carried a
    huge vault read-back stayed silent for 8+ minutes) froze the
    whole turn with no reply. The timeout surfaces as
    ``APITimeoutError`` → the existing retry / cross-provider
    failover path degrades gracefully instead of hanging forever.
    """
    raw = (os.environ.get("QDRANT_LLM_TIMEOUT_S", "") or "").strip()
    if not raw:
        return _LLM_TIMEOUT_DEFAULT_S
    try:
        val = float(raw)
    except ValueError:
        logger.warning(
            "[agent] QDRANT_LLM_TIMEOUT_S=%r invalid (not a number) "
            "— using default %.0f s", raw, _LLM_TIMEOUT_DEFAULT_S,
        )
        return _LLM_TIMEOUT_DEFAULT_S
    if val < 30:
        logger.warning(
            "[agent] QDRANT_LLM_TIMEOUT_S=%s below the 30 s floor "
            "— clamped", val,
        )
        return 30.0
    return min(val, 1200.0)


def _write_sandbox_hint() -> str:
    """v0.9.30.3+ — literal filesystem write-sandbox path for the
    Show-images prompt rule. The LLM cannot see env vars, so the
    system prompt names the ACTUAL directory it must save generated
    images into (the chat UI can only display files inside the
    sandbox roots). Falls back to a generic hint when the root
    cannot be resolved — never raises.
    """
    try:
        from src.mcp_client.host import _resolve_filesystem_root
        return str(_resolve_filesystem_root("QDRANT_FILESYSTEM_WRITE_ROOT"))
    except Exception:
        return "(the configured filesystem write sandbox)"


# ─── v0.9.102.7 — deterministic camera-snapshot embedding ───────────
# Live evidence (2026-09-29 12:18 「get 五股大門畫面」): the flash model
# answered 成功並 479-char 散文, never echoed the snapshot path the tool
# returned → renderChat()'s renderSandboxImages() had nothing to match
# → no inline preview (the v0.9.57.1 / v0.9.76.3 lesson AGAIN: prompt-
# taught echo rules are not guaranteed). Fix = the same v0.9.76.3
# deterministic post-process pattern: _run_tool_call stashes the
# snapshot path from the tool result, the tool-loop return sites run
# _ensure_snapshot_embed() which APPENDS the path line the chat front-
# end (branch b) already knows how to preview.
_SNAPSHOT_TOOLS = frozenset({
    "onvif_get_snapshot", "onvif_rtsp_snapshot",
})
_SNAPSHOT_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _snapshot_path_from_result(text: str) -> str:
    """Extract the saved-snapshot path from an ONVIF snapshot tool
    result. Prefers the ``path``/``snapshot``/``file``/``saved_path``
    JSON key; falls back to a drive-path regex over the raw text.
    Returns "" when nothing image-like is found — never raises."""
    if not text:
        return ""
    p = ""
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            for k in ("path", "snapshot", "file", "saved_path"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    p = v.strip()
                    break
    except Exception:
        pass
    if not p:
        m = re.search(r"[A-Za-z]:[/\\]\S+", text)
        while m:
            cand = m.group(0).strip("\"'`<>，。；！？)」』]")
            if cand.lower().endswith(_SNAPSHOT_EXT):
                p = cand
                break
            rest = text[m.end():]
            m = re.search(r"[A-Za-z]:[/\\]\S+", rest)
    if not p.lower().endswith(_SNAPSHOT_EXT):
        return ""
    return p


# ─── Jev time route (v0.9.84.1, plan §5.1 slice) ───────────────────

_WEEKDAY_ZH = (
    ("Monday", "星期一"), ("Tuesday", "星期二"), ("Wednesday", "星期三"),
    ("Thursday", "星期四"), ("Friday", "星期五"), ("Saturday", "星期六"),
    ("Sunday", "星期日"),
)
_WEEKDAY_ZH_MAP = dict(_WEEKDAY_ZH)

# v0.9.84.2 — greeting templates per UI locale (deterministic reply).
_GREETING_TEMPLATES = {
    "zh-TW": "你好！有什麼我可以幫忙的嗎？",
    "zh-CN": "你好！有什么我可以帮忙的吗？",
    "en": "Hello! How can I help?",
    "ja": "こんにちは！何かお手伝いできますか？",
}


def _time_route_mode() -> str:
    """``QDRANT_TIME_ROUTE`` — off (default) | shadow | on. Shadow logs
    the Jev verdict without acting (a week of data before enabling);
    ``on`` routes date/time questions to the deterministic direct
    answer."""
    v = (os.environ.get("QDRANT_TIME_ROUTE", "") or "").strip().lower()
    return v if v in ("off", "shadow", "on") else "off"


def _time_route_prob() -> float:
    raw = (os.environ.get("QDRANT_TIME_ROUTE_PROB", "") or "").strip()
    if not raw:
        return 0.85
    try:
        v = float(raw)
    except ValueError:
        return 0.85
    if v < 0.0 or v > 1.0:
        return 0.85
    return v


# ─── v0.9.85.0 — Jev abandon gate (探索放棄閘) ───────────────────
# A long exploration turn (the 2026-09-22 12:49 tweet-follow) can burn
# the whole 20-iteration budget on dead mirror sites. When the tool
# loop collects CONSECUTIVE empty/error results, ONE jev_noul decision
# decides whether the model should stop trying variants and answer
# from what it already has. off (default) | shadow (log only) | on.
def _jev_abandon_mode() -> str:
    raw = (os.environ.get("QDRANT_JEV_ABANDON", "") or "").strip().lower()
    return raw if raw in ("off", "shadow", "on") else "off"


def _jev_abandon_prob() -> float:
    raw = (os.environ.get("QDRANT_JEV_ABANDON_PROB", "") or "").strip()
    if not raw:
        return 0.75
    try:
        v = float(raw)
    except ValueError:
        return 0.75
    if v < 0.0 or v > 1.0:
        return 0.75
    return v


def _jev_abandon_streak() -> int:
    raw = (os.environ.get("QDRANT_JEV_ABANDON_STREAK", "") or "").strip()
    if not raw:
        return 3
    try:
        v = int(raw)
    except ValueError:
        return 3
    return max(2, min(10, v))


# ─── v0.9.86.0 — answer-support check (Agentic-RAG slice ②) ───────
# After an answer that USED RAG context (memory hits / search_documents
# dispatch), ONE jev_noul call asks「答案是否含有未被檢索內容支持的
# 斷言？」; on + p ≥ PROB runs ONE no-tools corrective create that
# rewrites the answer strictly from the retrieved context (a failed
# corrective keeps the original — fail-open). off | shadow | on.
def _support_check_mode() -> str:
    raw = (os.environ.get("QDRANT_SUPPORT_CHECK", "") or "").strip().lower()
    return raw if raw in ("off", "shadow", "on") else "off"


def _support_check_prob() -> float:
    raw = (os.environ.get("QDRANT_SUPPORT_CHECK_PROB", "") or "").strip()
    if not raw:
        return 0.7
    try:
        v = float(raw)
    except ValueError:
        return 0.7
    if v < 0.0 or v > 1.0:
        return 0.7
    return v


_ABANDON_FAIL_MARKERS = (
    "（參數定義：",          # LocalTool error envelope (v0.9.81 hint)
    '"content": ""',         # web_fetch empty page
    '"content":""',
)


def _tool_result_failed(text: str) -> bool:
    """Heuristic: did this tool result yield NOTHING useful?"""
    s = (text or "").strip()
    if not s:
        return True
    if len(s) < 200 and any(m in s for m in _ABANDON_FAIL_MARKERS):
        return True
    return s.startswith(("Error", "error", "執行失敗", "LLM 呼叫失敗"))


def _jev_abandon_check(streak: int, tool_names: list[str],
                       iteration: int) -> tuple[float | None, dict]:
    """ONE Jev noul call at the streak threshold. Returns (p, meta) —
    p None on any failure (fail-open)."""
    from src.agent.rerank import jev_noul

    p, meta = jev_noul(
        "工具呼叫已連續多次取得空結果或錯誤（例如鏡像站失效）。是否應"
        "停止嘗試更多變體、改用已取得的資訊作答？（值得繼續嘗試 → "
        "false；應停止 → true）",
        json.dumps({
            "streak": streak, "iteration": iteration,
            "recent_tools": tool_names[-6:],
        }, ensure_ascii=False)[:1500],
    )
    return p, meta


def _format_time_answer(mgr: Any) -> str | None:
    """Call the real ``get_current_time`` tool and format a zh-TW
    deterministic answer (shared by the v1 noul route and the v2
    choice router). ``None`` on any tool failure."""
    try:
        r = mgr.call("get_current_time", {})
        if getattr(r, "is_error", True) or not getattr(r, "text", ""):
            return None
        d = json.loads(r.to_message_text() or "{}")
        local = d.get("local") or {}
        date = str(local.get("date") or "")
        tstr = str(local.get("time") or "")
        wzh = _WEEKDAY_ZH_MAP.get(str(local.get("weekday_en") or ""), "")
        off = str(local.get("utc_offset") or "")
        if not date:
            return None
        parts = [f"今天是 {date}"]
        if wzh:
            parts.append(f"（{wzh}）")
        if tstr:
            parts.append(f"；本地時間 {tstr}")
        if off:
            parts.append(f"（UTC{off}）")
        return "".join(parts) + "。"
    except Exception as e:
        logger.warning("[TimeRoute] direct answer failed (%.120r)", e)
        return None


def _jev_time_route(mgr: Any, question: str
                    ) -> tuple[str | None, float | None]:
    """plan §5.1 slice — the time/date message routing.

    Returns ``(direct_answer, jev_p)``: ``direct_answer`` is the
    deterministic ``get_current_time`` result formatted for the user
    when the route FIRES (mode=``on`` + p ≥ prob); otherwise
    ``(None, p|None)`` so the normal LLM path runs. Shadow mode only
    logs the verdict; every Jev/tool failure falls back to the LLM
    (fail-closed)."""
    tmode = _time_route_mode()
    if tmode == "off":
        return None, None
    from src.agent.rerank import jev_noul

    p, meta = jev_noul(
        "使用者這則訊息是否只是在問今天的日期或時間？（例：「今天是幾月幾"
        "日」「現在幾點」「今天星期幾」→ true；涉及其他任務、天氣、"
        "資料查詢或長句 → false）",
        str(question or "")[:2000],
    )
    if p is None:
        return None, None
    logger.info(
        "[TimeRoute] jev_p=%.2f (%.2fs) q=%.60r", p,
        meta.get("elapsed_s", 0), question,
    )
    if tmode != "on" or p < _time_route_prob():
        return None, p
    return _format_time_answer(mgr), p


# ─── Jev message router v2 (v0.9.84.2, plan §5.1) ──────────────────

_ROUTE_OPTIONS = ("time", "greeting", "status", "weather", "chat")


def _route_classes() -> list[str]:
    """``QDRANT_ROUTE_CLASSES`` — the enabled handler classes (default
    = the approved first batch). Unknown tokens are skipped."""
    raw = (os.environ.get("QDRANT_ROUTE_CLASSES", "") or "").strip()
    if not raw:
        return ["time", "greeting", "status", "weather"]
    out = []
    for tok in raw.replace(";", ",").split(","):
        tok = tok.strip().lower()
        if tok and tok in _ROUTE_OPTIONS:
            out.append(tok)
    return out or ["time", "greeting", "status", "weather"]


def _route_mode() -> str:
    """``QDRANT_ROUTE`` — off (default) | shadow | on — the v2 message
    router master switch (supersedes ``QDRANT_TIME_ROUTE`` when set)."""
    v = (os.environ.get("QDRANT_ROUTE", "") or "").strip().lower()
    return v if v in ("off", "shadow", "on") else "off"


def _route_prob() -> float:
    raw = (os.environ.get("QDRANT_ROUTE_PROB", "") or "").strip()
    if not raw:
        return 0.85
    try:
        v = float(raw)
    except ValueError:
        return 0.85
    if v < 0.0 or v > 1.0:
        return 0.85
    return v


# ─── Jev dynamic tool-surface (v0.9.98.0, plan §5.2) ────────────────

# ALWAYS-ON core: cheap, near-universal servers (RAG + clock + meta
# + the web pair). Everything else joins ONLY when its class wins.
# v0.9.104.4: += exec-local (user: 「exec 設定成基礎 MCP」) —
# run_python/run_bash/run_shell/run_grep/run_awk (+ script-library 3)
# are now on EVERY turn's surface regardless of the Jev class, like
# scheduler-local in v0.9.102.2.
_TOOLROUTE_CORE_SERVERS = (
    "qdrant-local", "web-local", "datetime-local", "meta-local",
    "filesystem-local", "scheduler-local", "exec-local",
    # v0.9.124.0: += rulebook-local (user: 「self-evolving-agent 譜系
    # 報告」建議①) — the rulebook tools must be ON THE SURFACE on the
    # exact turn a tool fails (the prediction-error hint can't route a
    # Jev class), like scheduler-local in v0.9.102.2.
    "rulebook-local",
    # v0.9.133.16: += task-local — live 2026-10-22 17:07: a docx task
    # turn whose LLM wanted to submit a background job FABRICATED the
    # reply 「已送出背景任務 8a2c1f4b (session: task-8a2c1f4b)」 with the
    # tool nowhere on the routed surface (task-8a2c1f4b.json NEVER got
    # created). run_task_once is the agent's「don't block the user」
    # channel — it belongs on EVERY routed surface (2 cheap tools);
    # the fake-call risk is exactly what being absent caused.
    "task-local",
)

# class -> extra servers mounted for the turn
_TOOLROUTE_CLASS_MAP = {
    "rag": ("memory-local",),
    "calendar": ("google-calendar",),
    "stock": ("stock-local",),
    "cloud": ("hicloud-local",),
    "camera": ("onvif-local", "netscan-local"),
    "network": ("netscan-local", "runtime-local"),
    "chat-history": ("chat-history-local",),
    "keep": ("keep-local",),
    "telegram": ("telegram",),
    "desktop": ("windows-mcp",),
    "diagram": ("drawio",),
    # v0.9.104.2 — firefox_craw joins web-external (browser-rendered
    # pages — x.com / JS-heavy sites that plain web_fetch cannot render).
    "web-external": ("my_web", "firefox_craw"),
    "team": ("teams-assist",),
    "webhook": ("webhook-local",),
    "cred": ("cred-local",),
    "task": ("task-local",),
    "archive": ("archive-local",),
    # v0.9.102.0 FIX — exec/archive/openwith do NOT live inside
    # filesystem-local: ExecLocalHost is its OWN server (exec-local,
    # run_shell/run_bash/run_python/run_grep/run_awk). The v0.9.92
    # latency pass dropped exec-local from MCP_ACTIVE_SERVERS, which
    # silently removed EVERY exec tool from the LLM surface (and the
    # old () mapping here gave the exec class nothing even when
    # re-enabled) — a simulation/backtest turn could not run python
    # and the turn "ended" with a plan only (the live log's 「執行歷史
    # K 線和模擬」 turns). Map it and let the route mount it.
    "exec": ("exec-local", "archive-local"),
    "log": ("log-analyzer-local",),
    "jev": ("jev-classifier-local",),
    # v0.9.104.0 — 產圖 class: the OpenRouter Image API tools mount
    # via the Jev route AND via the question hint below (a 產圖
    # prompt gets the server REGARDLESS of the winning class — same
    # union pattern as the v0.9.103.2 telegram hint).
    "image": ("imagegen-local",),
}


def _toolroute_mode() -> str:
    """``QDRANT_TOOLROUTE`` — off (default) | shadow | on.

    off    = byte-identical legacy surface (env gate + cap only).
    shadow = ONE Jev choice per turn is spent, the winning class is
             LOGGED, the surface stays legacy.
    on     = the turn's tool surface = core servers + the winning
             class's servers (still filtered by MCP_ACTIVE_SERVERS
             and hard-capped at MCP_SURFACE_MAX_TOOLS).
    """
    v = (os.environ.get("QDRANT_TOOLROUTE", "") or "").strip().lower()
    return v if v in ("off", "shadow", "on") else "off"


def _toolroute_prob() -> float:
    raw = (os.environ.get("QDRANT_TOOLROUTE_PROB", "") or "").strip()
    if not raw:
        return 0.6
    try:
        v = float(raw)
    except ValueError:
        return 0.6
    if v < 0.0 or v > 1.0:
        return 0.6
    return v


def _toolroute_classes() -> list[str]:
    """``QDRANT_TOOLROUTE_CLASSES`` — enabled classes (default = all
    mapped). Unknown tokens are skipped."""
    raw = (os.environ.get("QDRANT_TOOLROUTE_CLASSES", "") or "").strip()
    if not raw:
        return list(_TOOLROUTE_CLASS_MAP)
    out = []
    for tok in raw.replace(";", ",").split(","):
        tok = tok.strip().lower()
        if tok and tok in _TOOLROUTE_CLASS_MAP:
            out.append(tok)
    return out or list(_TOOLROUTE_CLASS_MAP)


def _toolroute_hard_cap() -> int:
    """``QDRANT_TOOLROUTE_CAP`` — per-turn hard cap (default 128, the
    provider tools-array limit; 0 = fall back to
    MCP_SURFACE_MAX_TOOLS only)."""
    raw = (os.environ.get("QDRANT_TOOLROUTE_CAP", "") or "").strip()
    if not raw:
        return 128
    try:
        v = int(raw)
    except ValueError:
        return 128
    return max(0, v)


def _jev_toolroute(
    question: str, prev_reply: str | None = None,
) -> tuple[str | None, float | None, dict[str, Any]]:
    """ONE Jev ``choice`` call: which tool CLASS does this question
    need? Returns ``(class, p, meta)`` — ``(None, p, meta)`` when the
    decision is unconfident/failed (caller fail-opens to the legacy
    surface). v0.9.104.1 — a SHORT follow-up (「ok」「執行」「繼續」
    「run」「核准」) carries NO class keywords of its own; its MEANING
    lives in the previous LLM reply (the plan it answered with). When
    ``prev_reply`` is given AND the question is short (≤64 chars),
    the classification text = question + a labelled previous-reply
    block (capped 500 chars) so Jev judges the CONTINUATION, not the
    bare word."""
    options = _toolroute_classes()
    instructions = (
        "判斷使用者訊息主要需要哪一類工具能力（單選）。"
        "rag=文件/知識庫檢索或一般問答；calendar=Google 行事曆；"
        "stock=股市行情/下單；cloud=hicloud 雲主機；"
        "camera=ONVIF 攝影機；network=網路掃描/系統服務；"
        "chat-history=查過往對話；keep=筆記本；telegram=發 Telegram 訊息；"
        "desktop=操作 Windows 桌面；diagram=繪製圖表；"
        "web-external=外部網頁工具；team=Teams 助理；"
        "webhook=webhook 觸發器；cred=帳號密碼集；task=背景任務；"
        "archive=壓縮解壓縮；exec=執行程式/指令；log=日誌分析；"
        "jev=批次文字分類；image=產圖/文生圖。"
        "general 或不確定 → rag（核心檢索工具已涵蓋）。"
    )
    ctx = str(question or "")
    if prev_reply and len(ctx.strip()) <= 64:
        ctx = (ctx.strip()
               + "\n--- 上一輪 LLM 回覆（本則訊息要執行它宣告的動作；"
               "僅供語境判斷---\n" + str(prev_reply)[:500])
    from src.agent.rerank import jev_choice  # lazy (module pattern)

    try:
        choice, p, meta = jev_choice(
            instructions, ctx[:1200], options,
            timeout_s=6.0,
        )
    except Exception as e:  # noqa: BLE001 — fail-open, never raise
        return None, None, {"backend": "jev", "error": repr(e)}
    if not choice or choice not in options:
        return None, p, meta
    if (p or 0.0) < _toolroute_prob():
        return None, p, meta
    return choice, p, meta


def _toolroute_telegram_hint(question: str) -> bool:
    """v0.9.103.2 — does this question/prompt ask for a Telegram
    send? Live 2026-09-29 14:00 evidence: the stock-analysis schedule
    prompt (「…將分析結果以 Telegram 訊息發送到 chat_id …」) was
    classed plain ``stock`` by the Jev route → the turn surface
    carried NO ``telegram`` server → the LLM could only PROSE
    「整理分析後發送 Telegram」 with ZERO real send_telegram_*
    dispatches (the v0.9.13.2 discovery trap). When the prompt itself
    asks for a Telegram send, the ``telegram`` server rides on the
    surface REGARDLESS of the winning class."""
    s = str(question or "").lower()
    return bool(re.search(
        r"send_telegram|telegram|chat_id\s*[:=]?\s*\d|"
        r"發送.{0,16}(?:訊息|通知|分析結果)|"
        r"傳送.{0,12}(?:訊息|通知|畫面)",
        s))


def _toolroute_browse_hint(question: str) -> bool:
    """v0.9.104.2 — explicit browser-side page-reading intent
    (firefox_craw): named tool/scheme mentions, x.com / twitter links
    (JS-only rendering that web_fetch cannot parse), browser / crawl
    verbs. Union-only; plain search / fetch stays web-local (the LLM
    falls back to firefox when web_fetch returns nothing)."""
    s = str(question or "").lower()
    return bool(re.search(
        r"firefox|firefox_craw|get_page_html|get_x_home|get_x_post|"
        r"download_x_post|mcp_getweb|瀏覽器|"
        r"x\.com|twitter\.com|"
        r"\bcrawl\b|\bbrowse\b",
        s))


def _toolroute_imagegen_hint(question: str) -> bool:
    """v0.9.104.0 — does this question/prompt ask for image
    GENERATION（產圖）? Same union pattern as the telegram hint
    (v0.9.103.2): a 產圖 request unions the ``imagegen-local`` server
    onto the turn surface REGARDLESS of the winning class — a plain
    「畫一張…」 is easily classed rag/keep and would otherwise LOSE the
    tools (the v0.9.13.2 discovery trap edition). Diagram requests
    （圖表/流程圖/drawio）must NOT hit this — they belong to the
    drawio viewer."""
    s = str(question or "").lower()
    if re.search(r"圖表|diagram|flowchart|drawio|流程圖", s):
        return False
    return bool(re.search(
        r"image_generate|imagegen|"
        r"產圖|生成圖片|產生圖片|生成一張|產一張|作圖|生圖|"
        r"畫一(?:張|幅|個)|"
        r"畫張|幫我畫|幫我做一|做一張|貼圖|插圖|"
        r"logo|海報|"
        r"(?:generate|create|make|draw)\s+(?:an?\s+|the\s+)?"
        r"(?:image|picture|photo|logo|illustration)\b",
        s))


def _toolroute_photocraft_hint(question: str) -> bool:
    """v0.9.119.3 — explicit PhotoCraft intent (headless doc/command
    editing): the app name, its file formats (.psd/.pcraft/.pcdroplet),
    editing verbs（修圖/去背/圖層/裁切/縮放圖/photoshop/photo edit）.
    The question-side union the v0.9.119.1 TRIGGERS row could NOT
    cover (TRIGGERS mine the PREVIOUS REPLY only — v0.9.104.1
    continuation semantics — so a history=0 first turn naming
    photocraft got NO photocraft tools, the 12:09 110k-token probe
    case). Plain 產圖/畫圖 stays imagegen-local via class=himage."""
    s = str(question or "").lower()
    return bool(re.search(
        r"photocraft|\.psd\b|\.pcraft\b|\.pcdroplet\b|"
        r"修圖|去背|圖層|裁切|縮放圖|"
        r"photoshop|\bphoto\s*edit(?:ing)?\b|\bbg\s*removal\b|"
        r"remove\s+(?:the\s+)?background\b",
        s))


# v0.9.104.1 — server-union triggers mined from the PREVIOUS LLM reply:
# a continuation (「ok / 執行 / 繼續 / run / 核准」) carries no keywords —
# the plan (which NAMES its tools / CJK nouns) lives in the last reply.
_TOOLROUTE_SERVER_TRIGGERS: tuple[tuple[str, str], ...] = (
    (r"send_telegram|telegram|chat_id|發送.{0,14}訊息|傳送.{0,12}訊息", "telegram"),
    (r"image_generate|imagegen|產圖", "imagegen-local"),
    (r"photocraft|\.psd|\.pcraft|\.pcdroplet|修圖|去背|圖層|裁切|縮放圖",
     "photocraft"),
    (r"pdfcraft|\.pdf|pdf ?文件|pdf ?檔|\bocr\b|文字辨識|redact|遮蔽|"
     r"浮水印|電子簽|簽署|表單填|合併 ?pdf|拆分 ?pdf|頁碼|pdfa",
     "pdfcraft"),
    (r"gridcraft|\bxlsx\b|\.xlsx|\.xlsm|\.xlsb|\.ods|\bexcel\b|"
     r"試算表|活頁簿|工作簿|儲存格|vlookup|樞紐分析",
     "gridcraft"),
    (r"soundcraft|\.scraft|\bscraft\b|混音|多軌|音軌|\bmixdown\b|"
     r"\bbounce\b|\blufs\b|匯出 ?(?:mp3|wav|flac|ogg)|音訊後製",
     "soundcraft"),
    (r"wordcraft|\.docx|\.doc\b|\.odt|\.rtf|word ?文件|word ?檔|文書|"
     r"\bdocx\b|追蹤修訂|頁首頁尾|\bdoc\b",
     "wordcraft"),
    (r"deckcraft|\.deckcraft|\.pptx|\bpptx\b|\bppt\b|powerpoint|"
     r"簡報|投影片|幻燈|簡報主題|slide ?deck",
     "deckcraft"),
    (r"filmcraft|\.fcproj|剪輯|剪片|時間軸|時間線|混剪|逐格|"
     r"\bnle\b|匯出 ?(?:mp4|mov|prores|h264|hevc)|\brenderVideo\b",
     "filmcraft"),
    (r"stock_|下單|報價|k線|k線圖", "stock-local"),
    (r"onvif_|攝影機|監視器|監視畫面", "onvif-local"),
    (r"hicloud_|雲伺服器|雲主機", "hicloud-local"),
    (r"keep_|筆記|記事本|備忘錄", "keep-local"),
    (r"cred_", "cred-local"),
    (r"archive_|\.zip|\.7z|壓縮|解壓", "archive-local"),
    (r"zipfs_|修改 ?(?:zip|tar|壓縮檔|压缩档)|在 ?(?:zip|tar|壓縮檔|压缩档)"
     r" ?(?:裡|里面|内|內)|edit (?:the )?(?:zip|tar|archive)"
     r"? member|in the (?:zip|tar)|壓縮檔內|archive ?(?:member|內部)"
     r"|删掉 ?(?:zip|tar)|刪掉 ?(?:zip|tar)",
     "zipfs-local"),
    (r"run_python|run_bash|run_shell|run_grep|run_awk|執行程式", "exec-local"),
    (r"calendar|行事曆|行程", "google-calendar"),
    (r"jev_classify|批次分類", "jev-classifier-local"),
    (r"net_scan|net_my_interfaces|掃描", "netscan-local"),
    (r"list_chat_sessions|search_chat_history|get_chat_session",
     "chat-history-local"),
    (r"remember|recall_memory|長期記憶|summarize_sessions|"
     r"session[_ ]?摘要|摘要沉澱|沉澱", "memory-local"),
    (r"run_task_once|背景執行|背景跑", "task-local"),
    (r"compile_plan|run_plan|get_plan_status|list_plans|run_plan_stage|"
     r"stop_plan|workflow|工作流|戰術|策略|多步驟|分階段|tacti",
     "tacti-local"),
    (r"skill_import|skill_list|skill_get|skill_search|skill_remove|"
     r"skill_use|技能庫|匯入技能|匯入skill|匯入 skill|"
     r"刪除技能|移除技能|引用技能|引用skill|引用 skill|"
     r"skill file|skill library|"
     r"哪些 ?skills?|有那些 ?skills?|有哪些 ?skills?|"
     r"list skills?\b|skills?\s*列表|列出 ?skills?|"
     r"(?:做成|存成|儲存為|保存成|儲成|寫成) ?skills?|技能|skill",
     "skill-library-local"),
    (r"snippet|片語|快速片語|快速回覆|snippet_add|snippet_edit",
     "snippets-local"),
    (r"webhook", "webhook-local"),
    (r"teams_assist", "teams-assist"),
    (r"create_diagram|edit_diagram", "drawio"),
    (r"reload_config|restart_services|restart_mcp_servers",
     "runtime-local"),
    (r"firefox|get_page_html|get_x_home|get_x_post|download_x_post|"
     r"mcp_getweb|瀏覽器", "firefox_craw"),
    (r"\bmy_web\b", "my_web"),
)


def _prev_assistant_reply(
    history: list[dict] | None, max_chars: int = 700,
) -> str:
    """v0.9.104.1 — the LAST assistant message in the history = the
    previous turn's answer/plan (exactly what an「ok」continues).
    Empty / error-marked / non-dict entries skipped; capped for the
    Jev context. Never raises."""
    if not history:
        return ""
    try:
        for m in reversed(history):
            if not isinstance(m, dict):
                continue
            if m.get("role") != "assistant":
                continue
            c = str(m.get("content") or "").strip()
            if not c or m.get("error"):
                continue
            return c[:max_chars]
    except Exception:
        return ""
    return ""


def _toolroute_prev_reply_servers(prev_reply: str | None) -> list[str]:
    """v0.9.104.1 — mine the PREVIOUS LLM reply for announced tool
    work; returns the servers whose tool names / CJK nouns it names
    (union candidates). Union-only: this NEVER removes anything.
    Garbage in → []."""
    s = str(prev_reply or "").lower()
    if not s.strip():
        return []
    out: list[str] = []
    for pattern, server in _TOOLROUTE_SERVER_TRIGGERS:
        try:
            if re.search(pattern, s) and server not in out:
                out.append(server)
        except re.error:
            continue
    return out


def _toolroute_servers_for(
    cls: str | None, question: str | None = None,
    prev_reply: str | None = None,
) -> tuple[list[str], list[str]]:
    """``(core+class server list, class names mounted)`` for the
    winning class; ``cls`` None → core only.
    v0.9.103.2 — a Telegram-send prompt unions the ``telegram``
    server into the surface no matter which class won (see
    ``_toolroute_telegram_hint``).
    v0.9.104.1 — ``prev_reply`` (the previous turn's LLM reply) is
    MINED for announced tool work and its servers union as well —
    the 「ok / 執行 / 繼續 / run / 核准」 continuation of a multi-turn
    plan must not lose the fleet (bare continuations would class to
    rag and the named tools would vanish — the v0.9.13.2 discovery
    trap, multi-turn edition)."""
    classes = [cls] if cls else []
    servers = set(_TOOLROUTE_CORE_SERVERS)
    for c in classes:
        servers.update(_TOOLROUTE_CLASS_MAP.get(c, ()))
    if question and _toolroute_telegram_hint(question):
        servers.add("telegram")
    if question and _toolroute_imagegen_hint(question):
        servers.add("imagegen-local")
    if question and _toolroute_browse_hint(question):
        servers.add("firefox_craw")
    if question and _toolroute_photocraft_hint(question):
        servers.add("photocraft")
    for srv in _toolroute_prev_reply_servers(prev_reply):
        servers.add(srv)
    # v0.9.120.2 — QUESTION-side mining: the trigger rows were
    # prev-REPLY mining only (v0.9.104.1); a history-0 or off-topic
    # prev turn that NAMED the intent (「目前有那些skill」→ the LLM
    # improvised script_list; 「list skill library」→ a Qdrant
    # search_documents; 「做成skill存檔」→ a hand-written write_file
    # "skill") mounted NOTHING. The same curated rows now union
    # from the question too — union-only; surface-mount ≠ forced
    # dispatch.
    for srv in _toolroute_prev_reply_servers(question):
        servers.add(srv)
    # v0.9.127.0 — LLM-proposable ToolRoute pattern overrides
    # (agent_toolroute.md, evidence-gated): mode=on unions the matched
    # servers; mode=shadow (default) only LOGS the would-be hit so the
    # proposal trail can be observed before it goes live. Union-only.
    try:
        from src.agent import toolroute_overrides as _tro

        for _src_txt in (question, prev_reply):
            if not _src_txt:
                continue
            for _m in _tro.matches(_src_txt):
                if _tro.mode() == "on":
                    servers.add(_m["server"])
                else:
                    logger.info(
                        "[toolroute-override] shadow hit: %r → %s "
                        "(mode=shadow, NOT applied)",
                        _m["pattern"], _m["server"],
                    )
    except Exception:  # noqa: BLE001 — routing must never break
        pass
    return sorted(servers), classes


def _jev_class_route(mgr: Any, question: str, lang: str | None = None
                     ) -> tuple[str | None, Any, float | None]:
    """v2 message router (plan §5.1) — ONE Jev ``choice`` call splits
    the message into a handler class; confident classes are served
    WITHOUT the LLM tool loop:

    * ``time``     → the real get_current_time result, templated
    * ``greeting`` → a locale template (no tool at all)
    * ``status``   → teams_assist_status, compactly formatted
    * ``weather``  → PRE-FETCH: web_search runs deterministically and
                     the caller feeds the results to a ONE-SHOT LLM
                     summary (skips the tool loop's extra round-trips)

    Returns ``(action, payload, p)``:
    * (None, None, p)          → run the normal LLM path
    * ("direct", answer, p)    → answer deterministically
    * ("prefetch", results, p) → one-shot LLM summary over ``results``
    Every Jev/tool failure returns (None, None, None) — fail-closed.
    """
    rmode = _route_mode()
    if rmode == "off":
        return None, None, None
    enabled = _route_classes()
    options = [c for c in _ROUTE_OPTIONS
               if c == "chat" or c in enabled]
    if len(options) < 2:  # nothing to route to — normal path
        return None, None, None
    from src.agent.rerank import jev_choice

    choice, prob, meta = jev_choice(
        "使用者這則訊息屬於哪一類？",
        str(question or "")[:2000],
        options,
    )
    if not choice:
        return None, None, None
    logger.info(
        "[Route] class=%s p=%s (%.2fs) q=%.60r", choice,
        ("%.2f" % prob) if prob is not None else "-",
        meta.get("elapsed_s", 0), question,
    )
    # shadow: log the verdict only — handlers never act yet.
    if rmode == "shadow":
        return None, choice, prob
    if choice == "chat" or prob is None or prob < _route_prob():
        return None, choice, prob
    if choice not in enabled:
        return None, choice, prob  # handler disabled → normal LLM path
    try:
        if choice == "time":
            return "direct", _format_time_answer(mgr) or None, prob
        if choice == "greeting":
            return "direct", _GREETING_TEMPLATES.get(
                (lang or "zh-TW").strip(),
                _GREETING_TEMPLATES["zh-TW"]), prob
        if choice == "status":
            r = mgr.call("teams_assist_status", {})
            if getattr(r, "is_error", True) or not getattr(r, "text", ""):
                return None, choice, prob
            d = json.loads(r.to_message_text() or "{}")
            watch = d.get("watch") or {}
            running = "執行中" if watch.get("running") else "停止"
            auto = "on" if d.get("auto") else "off"
            paused = "、已暫停" if d.get("paused") else ""
            gate = d.get("gate") or {}
            skipped = int(gate.get("skipped") or 0)
            return ("direct", (
                f"Teams 助理狀態：watch {running}{paused}"
                f"（mode={d.get('mode')}，自動送出={auto}）；"
                f"watch 過濾閘已省下 {skipped} 次誤報回合。"), prob)
        if choice == "weather":
            q = f"{str(question or '').strip()} 預報 中央氣象署"
            r = mgr.call("web_search", {"query": q, "max_results": 3})
            if getattr(r, "is_error", True) or not getattr(r, "text", ""):
                return None, choice, prob
            return "prefetch", r.to_message_text()[:3000], prob
    except Exception as e:
        logger.warning("[Route] handler %r failed (%.120r)", choice, e)
        return None, choice, prob
    return None, choice, prob


_CALENDAR_WRITE_TOOLS = (
    "create-event", "create-events", "update-event", "delete-event",
    "respond-to-event",
)
_CALENDAR_NOUN_RE = re.compile(
    r"(行事曆|行程|活動|會議|日程|event|calendar|schedule)", re.IGNORECASE,
)
_CALENDAR_VERB_RE = re.compile(
    r"(移|搬|改|調整|刪|刪除|新增|建立|加|排|取消|變更|reschedul|"
    r"change|move|delete|creat|add|cancel)", re.IGNORECASE,
)


def _calendar_guard_mode() -> str:
    """v0.9.91.0 — QDRANT_CALENDAR_GUARD: off (default) | on."""
    return (os.environ.get("QDRANT_CALENDAR_GUARD", "") or
            "").strip().lower()


def _result_grounding_mode() -> str:
    """v0.9.94.0 — QDRANT_RESULT_GROUNDING: off (default) | shadow | on."""
    return (os.environ.get("QDRANT_RESULT_GROUNDING", "") or
            "").strip().lower()


def _result_grounding_prob() -> float:
    """Grounded 機率門檻 — Jev p 低於此 = 疑似記憶推論 → 糾正。"""
    try:
        return max(0.1, min(0.95, float(os.environ.get(
            "QDRANT_RESULT_GROUNDING_PROB", "0.6"))))
    except (TypeError, ValueError):
        return 0.6


_DISTINCTIVE_TOKEN_RE = re.compile(
    r"\d[\d.,/:-]{2,}"            # 數字類：價格/日期/數量/ID
    r"|\b[A-Za-z][A-Za-z0-9_-]{5,}\b",  # 較長的英數識別字
)


def _distinctive_tokens(text: str) -> list[str]:
    """從工具結果抽取可辨識的特徵詞（價格/日期/ID/長英文詞）—
    grounding 快篩用（答案含任一特徵詞 = 明確有用工具結果）。"""
    toks = _DISTINCTIVE_TOKEN_RE.findall(text or "")
    out: list[str] = []
    for t in toks:
        t = t.strip(".,;:")
        if len(t) >= 3 and t.lower() not in (
                "error", "result", "results", "status", "total",
                "count", "items", "true", "false", "success", "ok"):
            out.append(t)
        if len(out) >= 40:
            break
    return out


class Agent:
    """LLM Agent with a tool-calling loop."""

    DEFAULT_CHAT_MODEL = "gpt-oss:120b-cloud"
    DEFAULT_WEB_MAX_RESULTS = 5
    # v0.9.14.1: GUI desktop automation (windows-mcp) needs MANY tool
    # steps per turn (launch → snapshot → click → drag → colour → ...).
    # The historical 6 aborted every drawing task mid-way; 20 gives a
    # full multi-step mouse session headroom. Override at runtime with
    # the MAX_TOOL_ITERATIONS env var (_resolve_max_tool_iterations).
    DEFAULT_MAX_TOOL_ITERATIONS = 20
    DEFAULT_MCP_CONFIG = "config/mcp.json"

    def __init__(self, mcp_manager: McpManager | None = None,
                 task_llm: str | None = None,
                 stop_check: "Callable[[], bool] | None" = None):
        self._stop_check = stop_check  # v0.9.120.0 — cooperative
        # force-stop (checked at every tool-loop iteration boundary;
        # set later via ``set_stop_check`` too)
        self.retriever = Retriever()
        self.web = build_web_search()
        self._mcp_manager = mcp_manager
        self._client = None
        self._provider = None
        self._model = None
        # v0.9.110.0 — task-level model pin (``provider:model``, both
        # provider and model validated against the LIVE provider
        # catalog at task submission; here it is honoured verbatim
        # by _pick_provider and BYPASSES the default chain + failover
        # walk). Empty/None = the default chain, zero behaviour change.
        self._task_llm = str(task_llm or "").strip()
        # v0.9.11.1+: the McpManager build takes ~15 s (local-host
        # registration + remote MCP connects), so the GUI prewarms it
        # in a background thread at boot — this lock makes the lazy
        # property safe against a chat turn racing the prewarm.
        self._mcp_manager_lock = threading.Lock()
        # v0.9.12.0+: per-turn token usage (prompt / completion /
        # total / reasoning / create-calls), aggregated across the
        # whole tool loop and read by the GUI right after chat_turn.
        self.last_usage: dict[str, int] | None = None
        # v0.9.57.1 — tool names dispatched during the CURRENT turn
        # (reset at every ask/chat_turn entry next to last_usage).
        # Consumed by the telegram photo-hallucination guard.
        self.last_tool_dispatches: list[str] = []
        # v0.9.10.0+ — opt-in chat context compression (summarize /
        # offload / selective squeeze). No-op while the feature is
        # off in .env (the default).
        self.compressor = ContextCompressor()
        # v0.9.9.2+: extra kwargs (``extra_body={"provider": {...}}``)
        # resolved once in _pick_provider and spread into every
        # chat.completions.create call. ``None`` until resolved;
        # always a dict afterwards ({} = inject nothing).
        self._extra_body: dict[str, Any] | None = None
        # v0.9.17.0 — which provider answered the LAST create call
        # (primary or a failover candidate). Diagnostic; also logged.
        self.last_provider: str | None = None

    # v0.8.9+: 4 supported chat LLM providers. All are
    # OpenAI-compatible (the ``openai`` SDK + a custom base_url),
    # so dispatch is a single if/elif chain picking the right
    # ``(api_key, base_url, model)`` triple.
    #
    # Resolution order:
    #   1. Explicit ``config.LLM_PROVIDER`` override - the user
    #      pinned a provider in .env even though multiple keys
    #      are set.
    #   2. Auto-detect by first non-empty *_API_KEY:
    #      ollama -> openrouter -> siliconflow -> openai.
    #   3. If none of the above is set: raise with a clear
    #      message listing all four env vars so the user can
    #      pick one.
    # v0.8.9+: IMPORTANT - the lambdas below import
    # ``src.config`` and look up ``config`` via the module
    # attribute (rather than the import-time closure binding).
    # This way, callers that ``importlib.reload(src.config)``
    # (unit tests, REPL tinkering) see the new env-var values
    # without us having to also reload the agent module.
    _PROVIDER_ORDER = (
        # (name, api_key_getter, base_url_getter, model_getter)
        ("ollama",
         lambda: _get_config().OLLAMA_API_KEY,
         lambda: _get_config().OLLAMA_BASE_URL,
         # LLM_MODEL (if explicitly set to a non-default value)
         # wins over OLLAMA_CHAT_MODEL - lets the user point
         # the Ollama Cloud client at a different model
         # without having to touch OLLAMA_CHAT_MODEL.
         lambda: (
             _get_config().LLM_MODEL
             if _get_config().LLM_MODEL != "gpt-4o-mini"
             else (_get_config().OLLAMA_CHAT_MODEL
                   or Agent.DEFAULT_CHAT_MODEL)
         )),
        ("openrouter",
         lambda: _get_config().OPENROUTER_API_KEY,
         lambda: _get_config().OPENROUTER_BASE_URL,
         lambda: (_get_config().LLM_MODEL
                  if _get_config().LLM_MODEL != "gpt-4o-mini"
                  else _get_config().OPENROUTER_CHAT_MODEL)),
        ("siliconflow",
         lambda: _get_config().SILICONFLOW_API_KEY,
         lambda: _get_config().SILICONFLOW_BASE_URL,
         lambda: (_get_config().LLM_MODEL
                  if _get_config().LLM_MODEL != "gpt-4o-mini"
                  else _get_config().SILICONFLOW_CHAT_MODEL)),
        ("openai",
         lambda: _get_config().OPENAI_API_KEY,
         lambda: _get_config().OPENAI_BASE_URL,
         # v0.9.19.0: OPENAI_CHAT_MODEL is the dedicated model for
         # this provider — point the openai slot at a generic
         # OpenAI-compatible endpoint (DeepSeek / LM Studio / vLLM /
         # ...) WITHOUT touching LLM_MODEL (whose global override
         # would rewrite the other 3 providers' models and break
         # cross-provider failover). Priority mirrors the other 3
         # providers: a non-default LLM_MODEL still wins; unset
         # OPENAI_CHAT_MODEL = LLM_MODEL (v0.8.9-v0.9.18 behaviour).
         lambda: (
             _get_config().LLM_MODEL
             if _get_config().LLM_MODEL != "gpt-4o-mini"
             else (_get_config().OPENAI_CHAT_MODEL or _get_config().LLM_MODEL)
         )),
        # v0.9.19.0 — FIFTH provider: a fully independent generic
        # OpenAI-compatible endpoint that COEXISTS with `openai`
        # (two compat endpoints can ride the same chain). Appended
        # last: auto-detect and the default failover order are
        # unchanged for existing users. OPENAI_COMPAT_BASE_URL is
        # REQUIRED when the key is set (guarded in _pick_provider /
        # _provider_candidates — a generic endpoint has no sensible
        # default, defaulting would silently hit api.openai.com).
        ("openai-compat",
         lambda: _get_config().OPENAI_COMPAT_API_KEY,
         lambda: _get_config().OPENAI_COMPAT_BASE_URL,
         lambda: (
             _get_config().LLM_MODEL
             if _get_config().LLM_MODEL != "gpt-4o-mini"
             else (_get_config().OPENAI_COMPAT_CHAT_MODEL
                   or _get_config().LLM_MODEL)
         )),
    )

    # v0.9.18.0 — chat 主鏈(primary 選擇 + 跨供應商 failover)與
    # vision OCR 鏈(ImageDescriber)是兩個獨立的應用需求;本鏈的
    # 優先序可用 ``LLM_PROVIDER_CHAIN`` 在 .env 重排(空白 = 此處
    # 的預設順序,行為與 v0.8.9-v0.9.17 完全一致)。
    @classmethod
    def _resolved_provider_order(cls) -> tuple:
        """``_PROVIDER_ORDER`` reordered by ``LLM_PROVIDER_CHAIN``.

        Reads ``os.environ`` directly (monkeypatch-friendly — the
        same pattern as MAX_TOOL_ITERATIONS / MCP_ACTIVE_SERVERS).
        Resolution (via ``src.provider_chain.parse_provider_chain``):

        * empty / unset -> the class default order (no behaviour
          change);
        * unknown tokens are skipped with a warning;
        * duplicates collapse to the first occurrence;
        * a partial list takes priority in the given order, then the
          unlisted providers are appended in their default relative
          order (the chain defines PRIORITY, never exclusion).

        ``LLM_PROVIDER`` (forced single provider) still wins over
        the chain for PRIMARY selection — the chain only reorders
        auto-detection and the failover walk.
        """
        from src.provider_chain import parse_provider_chain

        names = tuple(entry[0] for entry in cls._PROVIDER_ORDER)
        ordered = parse_provider_chain(
            (os.environ.get("LLM_PROVIDER_CHAIN", "") or "").strip(),
            valid_names=names,
            default_names=names,
            warn_label="LLM_PROVIDER_CHAIN",
        )
        by_name = {entry[0]: entry for entry in cls._PROVIDER_ORDER}
        return tuple(by_name[n] for n in ordered)

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._provider, self._model, self._client = self._pick_provider()
        return self._client

    def _pick_provider(self):
        """Resolve (provider_name, model_name, openai_client).

        Honours ``config.LLM_PROVIDER`` if set; otherwise walks
        ``_PROVIDER_ORDER`` and returns the first triple whose
        API key is non-empty. Raises a helpful error if no
        provider is configured at all.
        """
        forced = (_get_config().LLM_PROVIDER or "").strip().lower()
        # v0.9.110.0 — task-level model pin: honour it VERBATIM and
        # WITHOUT the chain / failover walk (the pin exists precisely
        # to choose the model; a fallback to another model would be a
        # silent breach of that choice — fail-closed instead).
        task_pin = str(getattr(self, "_task_llm", "") or "").strip()
        if task_pin:
            from src.agent.task_llm import parse_task_llm, TaskLlmError

            try:
                pin_name, pin_model = parse_task_llm(task_pin)
            except TaskLlmError as e:
                raise ValueError(f"task_llm={task_pin!r}: {e}") from e
            # v0.9.133.11 — the LOCAL llama.cpp chat server is a pin
            # target that must NEVER join _PROVIDER_ORDER (it is a weak
            # CPU summary model; auto-detect must not pick it as the
            # chat primary), so it is honoured here, BEFORE the
            # provider-table walk that would otherwise reject it with
            # "not in the provider table". No bearer key needed.
            if pin_name == "llamacpp":
                from src.agent.task_llm import _llamacpp_chat_base as _b

                base = _b()
                client = OpenAI(
                    api_key="llamacpp-local",
                    base_url=base,
                    timeout=_llm_timeout_s(),
                )
                self._extra_body = {}
                logger.info(
                    "[agent] task model pin: %s (provider=llamacpp, "
                    "local base=%s, clean extra_body)",
                    task_pin, base,
                )
                return "llamacpp", pin_model, client
            for name, key_fn, url_fn, _model_fn in (
                    self._resolved_provider_order()):
                if name != pin_name:
                    continue
                api_key = key_fn()
                if not api_key:
                    raise ValueError(
                        f"task_llm={task_pin!r} pins provider "
                        f"{name!r} but "
                        f"{name.upper().replace('-', '_')}_API_KEY "
                        "is empty — the pin cannot be honoured."
                    )
                client = OpenAI(
                    api_key=api_key, base_url=url_fn(),
                    timeout=_llm_timeout_s(),
                )
                # v0.9.110.0 live-E2E lesson: the GLOBAL openrouter
                # routing (provider order / fallbacks=false, tuned for
                # the DEFAULT model) filter-removed a pinned model's
                # own endpoints -> 404. A pin replaces routing:
                # send clean, let openrouter route the pinned model.
                self._extra_body = {}
                logger.info(
                    "[agent] task model pin: %s (provider=%s, "
                    "clean extra_body)",
                    task_pin, name,
                )
                return name, pin_model, client
            raise ValueError(
                f"task_llm={task_pin!r}: provider {pin_name!r} is not "
                f"in the provider table."
            )
        # v0.9.18.0: chain-orderable (LLM_PROVIDER_CHAIN) — the
        # forced-override semantics below are unchanged.
        for name, key_fn, url_fn, model_fn in self._resolved_provider_order():
            if forced and forced != name:
                continue
            api_key = key_fn()
            if not api_key:
                if forced:
                    raise ValueError(
                        f"LLM_PROVIDER={forced!r} set in .env but "
                        f"{name.upper().replace('-', '_')}_API_KEY is "
                        f"empty. Set it to a non-empty key."
                    )
                continue
            base_url = url_fn()
            # v0.9.19.0: the generic openai-compat endpoint has no
            # sensible base_url default — defaulting would silently
            # send the compat key to api.openai.com. Raise instead.
            if name == "openai-compat" and not (base_url or "").strip():
                raise ValueError(
                    "OPENAI_COMPAT_BASE_URL is required when "
                    "OPENAI_COMPAT_API_KEY is set — a generic "
                    "OpenAI-compatible endpoint has no default "
                    "(e.g. OPENAI_COMPAT_BASE_URL="
                    "https://api.deepseek.com/v1)."
                )
            model = model_fn()
            # v0.9.9.2+: OpenRouter-only routing hooks — the
            # ``provider`` object + the ``:nitro`` / ``:floor``
            # model-variant suffix. Other providers get untouched
            # model strings and an empty extra_body.
            if name == "openrouter":
                model, self._extra_body = self._apply_openrouter_routing(model)
            else:
                self._extra_body = {}
            # v0.9.63.1: a per-request timeout (QDRANT_LLM_TIMEOUT_S,
            # default 300 s) — the SDK default is 600 s per attempt
            # with built-in retries, which let a hung provider call
            # freeze a telegram/scheduler turn for tens of minutes.
            client = OpenAI(
                api_key=api_key, base_url=base_url,
                timeout=_llm_timeout_s(),
            )
            return name, model, client
        if forced:
            # LLM_PROVIDER was set but the loop never matched any
            # provider name. Either a typo (e.g. ``bogus-provider``)
            # or all known providers were skipped because the
            # override doesn't match their lowercase names.
            raise ValueError(
                f"LLM_PROVIDER={forced!r} set in .env but no provider "
                f"matched that name. Valid options: "
                f"ollama | openrouter | siliconflow | openai | "
                f"openai-compat. "
                f"Also make sure the corresponding "
                f"{forced.upper().replace('-', '_')}_API_KEY env var "
                f"is non-empty."
            )
        raise ValueError(
            "No LLM provider configured. Set one of the following "
            "in your .env:\n"
            "  OLLAMA_API_KEY         - https://ollama.com (cloud or local)\n"
            "  OPENROUTER_API_KEY     - https://openrouter.ai (400+ models)\n"
            "  SILICONFLOW_API_KEY    - https://siliconflow.cn (Qwen/DeepSeek/GLM)\n"
            "  OPENAI_API_KEY         - https://openai.com (or any "
            "OpenAI-compatible endpoint via OPENAI_BASE_URL)\n"
            "  OPENAI_COMPAT_API_KEY  - any second OpenAI-compatible "
            "endpoint (OPENAI_COMPAT_BASE_URL required too)\n"
            "Or set LLM_PROVIDER=ollama|openrouter|siliconflow|openai|"
            "openai-compat "
            "to force a specific provider when multiple keys are set."
        )

    # ─── OpenRouter provider routing (v0.9.9.2+) ──────────────
    _VALID_DATA_COLLECTION = ("allow", "deny")
    _VALID_SORT = ("price", "throughput")
    _VALID_QUANTIZATIONS = (
        "int4", "int8", "fp4", "fp6", "fp8", "fp16", "bf16",
        "fp32", "unknown",
    )

    # v0.9.11.1+: OpenRouter normalises `reasoning` across providers.
    # Reasoning-mandatory endpoints (Together's z-ai/glm-5.3-flash,
    # gpt-oss) reject {"enabled": false} with HTTP 400 but accept an
    # effort level — "low" skips the thinking pass entirely (a
    # trivial hello drops from ~4.4 s / 281 reasoning tokens to
    # ~1.4 s / 0 tokens on this box).
    _VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high")

    @classmethod
    def _apply_openrouter_routing(cls, model: str) -> tuple[str, dict[str, Any]]:
        """Parse the ``OPENROUTER_*`` env vars into the OpenRouter
        ``provider`` routing object.

        Returns ``(model, extra_body)``:

        * ``model`` — the chat model slug, with the optional
          ``OPENROUTER_MODEL_SUFFIX`` (e.g. ``:nitro`` / ``:floor``)
          appended unless it already ends with it. Users may also
          write the variant directly into
          ``OPENROUTER_CHAT_MODEL`` (``...:nitro``) — both work.
        * ``extra_body`` — ``{"provider": {...}}`` with only the
          fields the user actually configured, or ``{}`` when
          nothing is configured (default OpenRouter auto-routing,
          byte-identical request to the v0.8.9 behaviour).

        Invalid values (unknown sort / data_collection /
        quantization tokens) are SKIPPED with a warning rather
        than raising — a typo in one routing knob must never take
        down the whole chat provider.
        """
        from src import config as _cfg
        c = _cfg.config

        # ── 1. model-variant fast lane (:nitro / :floor / :free) ──
        suffix = (c.OPENROUTER_MODEL_SUFFIX or "").strip().lower()
        if suffix and not suffix.startswith(":"):
            suffix = f":{suffix}"
        if suffix and not model.lower().endswith(suffix):
            model = f"{model}{suffix}"

        # ── 2. provider routing object ──
        provider: dict[str, Any] = {}

        def _csv(raw: str) -> list[str]:
            return [t.strip() for t in raw.split(",") if t.strip()]

        order_raw = (c.OPENROUTER_PROVIDER_ORDER or "").strip()
        if order_raw:
            provider["order"] = _csv(order_raw)

        afb_raw = (c.OPENROUTER_ALLOW_FALLBACKS or "").strip().lower()
        if afb_raw:
            if afb_raw in ("true", "1", "yes"):
                provider["allow_fallbacks"] = True
            elif afb_raw in ("false", "0", "no"):
                provider["allow_fallbacks"] = False
            else:
                logger.warning(
                    "[agent] OPENROUTER_ALLOW_FALLBACKS=%r invalid "
                    "(use true/false) — skipped", afb_raw,
                )

        rp_raw = (c.OPENROUTER_REQUIRE_PARAMETERS or "").strip().lower()
        if rp_raw:
            if rp_raw in ("true", "1", "yes"):
                provider["require_parameters"] = True
            elif rp_raw in ("false", "0", "no"):
                provider["require_parameters"] = False
            else:
                logger.warning(
                    "[agent] OPENROUTER_REQUIRE_PARAMETERS=%r invalid "
                    "(use true/false) — skipped", rp_raw,
                )

        dc_raw = (c.OPENROUTER_DATA_COLLECTION or "").strip().lower()
        if dc_raw:
            if dc_raw in cls._VALID_DATA_COLLECTION:
                provider["data_collection"] = dc_raw
            else:
                logger.warning(
                    "[agent] OPENROUTER_DATA_COLLECTION=%r invalid "
                    "(use allow/deny) — skipped", dc_raw,
                )

        ignore_raw = (c.OPENROUTER_IGNORE or "").strip()
        if ignore_raw:
            provider["ignore"] = _csv(ignore_raw)

        qz_raw = (c.OPENROUTER_QUANTIZATIONS or "").strip()
        if qz_raw:
            valid = [q for q in _csv(qz_raw) if q.lower() in cls._VALID_QUANTIZATIONS]
            invalid = [q for q in _csv(qz_raw) if q.lower() not in cls._VALID_QUANTIZATIONS]
            if invalid:
                logger.warning(
                    "[agent] OPENROUTER_QUANTIZATIONS invalid tokens "
                    "skipped: %s (valid: %s)",
                    invalid, ", ".join(cls._VALID_QUANTIZATIONS),
                )
            if valid:
                provider["quantizations"] = [q.lower() for q in valid]

        sort_raw = (c.OPENROUTER_SORT or "").strip().lower()
        if sort_raw:
            if sort_raw in cls._VALID_SORT:
                provider["sort"] = sort_raw
            else:
                logger.warning(
                    "[agent] OPENROUTER_SORT=%r invalid "
                    "(use price/throughput) — skipped", sort_raw,
                )

        extra: dict[str, Any] = {"provider": provider} if provider else {}

        # ── 3. reasoning effort (v0.9.11.1+) ──
        # Injected as a TOP-LEVEL extra_body field (NOT inside
        # `provider`): OpenRouter's `reasoning` control sits at the
        # request root. Read via os.environ (same precedent as
        # context_compress) so tests can monkeypatch it without a
        # config reload.
        effort = (os.environ.get("OPENROUTER_REASONING_EFFORT", "") or "").strip().lower()
        if effort:
            if effort in cls._VALID_REASONING_EFFORTS:
                extra["reasoning"] = {"effort": effort}
            else:
                logger.warning(
                    "[agent] OPENROUTER_REASONING_EFFORT=%r invalid "
                    "(use minimal/low/medium/high) — skipped", effort,
                )
        return model, extra

    def _routing_create_kwargs(self) -> dict[str, Any]:
        """The extra kwargs to spread into every
        ``chat.completions.create`` call. ``{}`` for non-openrouter
        providers or unset routing (adds NO parameters — existing
        callers / tests observing kwargs stay untouched).

        ``getattr`` (not the bare attribute) because legacy tests
        build agents via ``object.__new__`` and stub ``_client``
        directly — ``_pick_provider`` never ran, so ``_extra_body``
        may not exist. Missing means "nothing configured" = ``{}``.
        """
        if getattr(self, "_client", None) is None:
            _ = self.client  # ensure _pick_provider has run
        extra = dict(getattr(self, "_extra_body", None) or {})
        # Wrap as the SDK's extra_body kwarg (merged into the JSON
        # request body) — a bare provider=... kwarg would NOT reach
        # the wire. Empty config -> {} -> NO extra kwarg at all.
        return {"extra_body": extra} if extra else {}

    # ─── cross-provider failover (v0.9.17.0) ──────────────────────
    def _provider_candidates(self) -> list[tuple[str, str, Any]]:
        """``(name, model, client)`` failover candidates in priority
        order (user request: 把 describe_image 式的備援機制推廣到
        chat LLM 本體).

        Entry 0 = the resolved primary. Then every OTHER provider in
        the resolved provider chain (``_PROVIDER_ORDER`` reordered by
        ``LLM_PROVIDER_CHAIN``, v0.9.18.0) whose API key is set,
        walked in priority order (default ollama → openrouter →
        siliconflow → openai, starting after the primary). Fresh
        ``OpenAI`` clients are built per candidate; each provider's
        own ``model_fn`` semantics (incl. the ``LLM_MODEL`` global
        override) are preserved.

        ``_apply_openrouter_routing`` is class-level and returns the
        (model, extra_body) pair without touching ``self._extra_body``
        — the primary's routing config is never clobbered here.
        """
        task_pin = str(getattr(self, "_task_llm", "") or "").strip()
        if task_pin:
            return [(self._provider, self._model, self._client)]
        _ = self.client  # ensure the primary is resolved
        primary_name = self._provider
        out: list[tuple[str, str, Any]] = [
            (primary_name, self._model, self._client),
        ]
        for name, key_fn, url_fn, model_fn in self._resolved_provider_order():
            if name == primary_name:
                continue
            key = key_fn()
            if not key:
                continue
            base_url = url_fn()
            if name == "openai-compat" and not (base_url or "").strip():
                # v0.9.19.0: a compat candidate without a base_url
                # would silently hit api.openai.com — skip it (the
                # failover walk must not crash the primary path;
                # _pick_provider already raises for the primary).
                logger.warning(
                    "[agent] failover candidate openai-compat skipped: "
                    "OPENAI_COMPAT_BASE_URL is empty"
                )
                continue
            model = model_fn()
            if name == "openrouter":
                model, _eb = self._apply_openrouter_routing(model)
            # v0.9.63.1: same per-request timeout as the primary —
            # a hung failover leg must not stall the walk either.
            out.append((name, model, OpenAI(
                api_key=key, base_url=base_url,
                timeout=_llm_timeout_s(),
            )))
        return out

    def _failover_candidates(self) -> list[tuple[str, str, Any]]:
        """Failover candidates EXCLUDING the already-tried primary."""
        try:
            return self._provider_candidates()[1:]
        except Exception as e:
            logger.warning(
                "[agent] failover candidate resolution failed: %r", e,
            )
            return []


    # ─── tool surface ─────────────────────────────────────────
    @property
    def mcp_manager(self) -> McpManager:
        if self._mcp_manager is None:
            # v0.9.11.1+: double-checked locking — the GUI prewarm
            # thread and the first chat turn can race here; a second
            # full build would register duplicate tools and
            # re-connect every remote server.
            with self._mcp_manager_lock:
                if self._mcp_manager is None:
                    self._mcp_manager = McpManager.from_config(
                        self.DEFAULT_MCP_CONFIG,
                        include_local_hosts=True,
                    )
                    if self._mcp_manager.failed:
                        logger.warning(
                            "[agent] some MCP servers failed to "
                            "connect: %s",
                            self._mcp_manager.failed,
                        )
                    # v0.8.6.3: wire the auto-start callback so an
                    # LLM-built schedule via ``add_scheduled_task``
                    # kicks the daemon thread off without requiring
                    # the user to click "Start daemon" in the GUI.
                    # ``set_auto_start_callback`` is the canonical
                    # setter — it both stores the callback on the
                    # manager AND pushes it down to the
                    # SchedulerLocalHost if one was registered (which
                    # it was, by ``McpManager.from_config``).
                    self._mcp_manager.set_auto_start_callback(
                        self._auto_start_scheduler_daemon
                    )
                    # v0.9.25.0: wire the webhook trigger callbacks so
                    # an LLM-built trigger via ``add_webhook_trigger``
                    # spins the HTTP listener up immediately AND the
                    # host's run_webhook_trigger_now gets the fire
                    # engine. Both setters are canonical + race-free.
                    self._mcp_manager.set_webhook_auto_start_callback(
                        self._auto_start_webhook_server
                    )
                    self._mcp_manager.set_webhook_fire_callback(
                        self._webhook_fire_now
                    )
                    # v0.9.43.0: wire the runtime-ops engine so the
                    # RuntimeLocalHost tools (reload_config /
                    # restart_services / get_runtime_status) delegate
                    # to this Agent.
                    self._mcp_manager.set_runtime_ops_callback(
                        self._runtime_ops
                    )
                    # v0.9.69.0: wire the Teams desktop-assist fire
                    # engine so the watch thread inside
                    # TeamsAssistLocalHost fires fresh-Agent turns on
                    # a screen change.
                    self._mcp_manager.set_teams_assist_fire_callback(
                        self._teams_assist_fire
                    )
        return self._mcp_manager

    def _auto_start_scheduler_daemon(self) -> None:
        """Idempotent daemon kicker used by ``SchedulerLocalHost._add``.

        Wraps ``self.start_scheduler()`` so the LocalTool can call
        a no-argument callable without knowing about the Agent.
        Exceptions are swallowed — a daemon-start failure must not
        propagate back into the LLM's tool response (the schedule
        is already saved; the user can manually start the daemon
        from the GUI / CLI if the auto-start ever fails).
        """
        try:
            self.start_scheduler()
        except Exception as e:
            logger.warning(
                "[agent] auto_start_scheduler_daemon failed: %r", e,
            )

    # ─── webhook triggers (v0.9.25.0+) ────────────────────────────

    @property
    def webhook_server(self) -> "WebhookServer":
        """Lazy HTTP listener for webhook triggers. Fires the
        configured prompt via ``chat_turn`` when an external caller
        hits ``/trigger/<id>`` with the right access key."""
        if getattr(self, "_webhook_server", None) is None:
            from src.agent.webhook import (
                WebhookManager, WebhookServer, make_webhook_fire,
            )
            self._webhook_server = WebhookServer(
                manager=WebhookManager(),
                fire=make_webhook_fire(
                    # Each fire builds a fresh Agent so the listener
                    # thread never shares OpenAI client state with
                    # the main thread's chat REPL.
                    agent_factory=lambda: Agent(mcp_manager=self._mcp_manager),
                ),
            )
        return self._webhook_server

    def start_webhook_server(self) -> None:
        """Spawn the HTTP listener thread. Idempotent (bind failure
        logs a warning instead of raising)."""
        self.webhook_server.start()

    def stop_webhook_server(self) -> None:
        srv = getattr(self, "_webhook_server", None)
        if srv is not None:
            srv.stop(timeout=2.0)

    def _auto_start_webhook_server(self) -> None:
        """Idempotent listener kicker used by ``WebhookLocalHost._add``
        (mirror of ``_auto_start_scheduler_daemon``)."""
        try:
            self.start_webhook_server()
        except Exception as e:
            logger.warning(
                "[agent] auto_start_webhook_server failed: %r", e,
            )

    def _webhook_fire_now(self, trigger, payload=None) -> dict:
        """Fire engine for ``run_webhook_trigger_now`` — builds a
        fresh Agent per fire (same isolation as the scheduler)."""
        from src.agent.webhook import make_webhook_fire

        fire = make_webhook_fire(
            agent_factory=lambda llm=None: Agent(
                mcp_manager=self._mcp_manager, task_llm=llm,
            ),
        )
        return fire(trigger, payload)

    # ─── teams desktop-assist (v0.9.69.0+) ────────────────────────

    def _teams_assist_fire(self, payload: dict | None = None) -> dict:
        """Fire engine for the TeamsAssistLocalHost watch thread —
        builds a fresh Agent per fire (same isolation as the
        scheduler / webhook / telegram fires)."""
        from src.teams_assist_tools import make_teams_assist_fire

        fire = make_teams_assist_fire(
            agent_factory=lambda: Agent(mcp_manager=self._mcp_manager),
        )
        return fire(payload)

    def resume_teams_assist(self) -> int:
        """Boot auto-resume hook (gui/api.py) — restart the Teams
        watch thread from persisted state. Idempotent; 0 = nothing
        resumed (never running, or allowlist empty = fail-closed)."""
        try:
            host = getattr(
                self.mcp_manager, "_teams_assist_local_host", None,
            )
            return host.resume_watch() if host is not None else 0
        except Exception as e:
            logger.warning(
                "[agent] resume_teams_assist failed: %r", e,
            )
            return 0

    # ─── hot reload + service restart (v0.9.42.0+) ────────────────

    def _runtime_ops(self, op: str, args: dict | None = None) -> dict:
        """v0.9.43.0 — ops engine wired into RuntimeLocalHost.

        reload → reload_runtime(+optional MCP reconnect);
        restart_services → _restart_daemon_services;
        status → provider/model snapshot for the LLM.
        """
        args = args or {}
        if op == "reload":
            out = self.reload_runtime(
                restart_services=bool(args.get("restart_services", True)),
            )
            if out.get("ok") and args.get("reconnect_mcp", True):
                try:
                    out["mcp"] = (
                        self.mcp_manager.reconnect_remote_servers()
                    )
                except Exception as e:
                    out["mcp"] = {"ok": False, "error": repr(e)}
            return out
        if op == "restart_services":
            return {
                "ok": True,
                "services": self._restart_daemon_services(),
            }
        if op == "status":
            out = {
                "ok": True,
                "provider": self._provider,
                "model": self._model,
                "client_ready": self._client is not None,
                "last_provider": getattr(self, "last_provider", None),
            }
            # v0.9.108.0 — expose the RESOLVED runtime limits so the LLM
            # can plan parallel batches / long jobs without touching
            # .env. Every field is best-effort (one bad resolver never
            # breaks the status report).
            limits: dict[str, Any] = {}
            try:
                limits["max_tool_iterations"] = (
                    _resolve_max_tool_iterations()
                )
            except Exception:
                pass
            try:
                limits["tool_call_watchdog_s"] = _tool_call_timeout_s()
            except Exception:
                pass
            try:
                limits["tool_result_cap_chars"] = _tool_result_cap()
            except Exception:
                pass
            try:
                from src.agent import turn_gate as _tgt

                limits["max_concurrent_turns"] = (
                    _tgt.resolve_max_concurrent_turns() or 0
                ) or None  # None = unlimited
                limits["turn_wait_seconds"] = (
                    _tgt.resolve_turn_wait_seconds()
                )
            except Exception:
                pass
            try:
                from src.mcp_client.host import _task_max_parallel

                limits["task_max_parallel"] = _task_max_parallel()
            except Exception:
                pass
            try:
                from src.agent import task_runner as _tr_mod

                limits["tasks_running"] = _tr_mod.list_tasks(
                    status="running", limit=1,
                ).get("running")
            except Exception:
                pass
            for key in ("MCP_ACTIVE_SERVERS", "CHAT_CONTEXT_COMPRESSION",
                        "CHAT_OFFLOAD_THRESHOLD_CHARS",
                        "QDRANT_TASK_NOTIFY",
                        "QDRANT_TASK_MONITOR_INTERVAL_S"):
                v = (os.environ.get(key, "") or "").strip()
                if v:
                    limits[key.lower()] = v
            out["limits"] = limits
            # v0.9.112.0 — llama.cpp embedding watchdog snapshot (the
            # LLM can answer "嵌入狀態如何"/decide to re-ingest after
            # a heal; never raises).
            try:
                from src.embed_watchdog import get_watchdog as _get_wd

                out["embedding_watchdog"] = _get_wd().status()
            except Exception:
                pass
            # v0.9.122.2 — gemma2 media-server watchdog snapshot
            # (surfaced only when its gate is on — honesty).
            try:
                from src.embed_watchdog import (
                    gemma2_watchdog_enabled as _gwd_on,
                    get_gemma2_watchdog as _get_gwd,
                )

                if _gwd_on():
                    out["gemma2_watchdog"] = _get_gwd().status()
            except Exception:
                pass
            out["limits_note"] = (
                "None = unlimited/unset. max_concurrent_turns queues "
                "then fails after turn_wait_seconds "
                "(TurnGateBusy); every background task pays its own "
                "turn budget — parallel cost ~ N x the schema+prompt."
            )
            return out
        return {"error": f"unknown runtime op {op!r}"}

    def reload_runtime(self, restart_services: bool = True) -> dict:
        """Re-read ``.env``/config, reset the LLM chain, restart the
        daemon services. One entry point shared by the GUI reload
        button, the CLI ``reload`` command and the config watcher.

        * ``config.reload()`` re-assigns every env-backed Config attr
          (dotenv override=True) and returns the CHANGED names.
        * The lazily-resolved provider chain (``_client`` / ``_provider``
          / ``_model`` / ``_extra_body`` / ``last_provider``) is reset —
          the NEXT turn re-picks the provider from the new config.
          In-flight turns keep their already-built client.
        * ``MCP_ACTIVE_SERVERS`` / ``MAX_TOOL_ITERATIONS`` /
          ``OPENROUTER_REASONING_EFFORT`` are read per call — no reset
          needed.
        * Daemon services restart with their enable-conditions
          (scheduler: enabled schedules; webhook: enabled triggers;
          telegram: token + allowlist; onvif: persisted watches).
        """
        from src import config as _cfg_mod

        cfg = _cfg_mod.config
        try:
            changed = cfg.reload()
        except Exception as e:
            return {"ok": False, "error": repr(e)}
        self._client = None
        self._provider = None
        self._model = None
        self._extra_body = None
        self.last_provider = None
        services: dict[str, str] = (
            self._restart_daemon_services() if restart_services else {}
        )
        return {
            "ok": True, "changed": changed, "services": services,
        }

    def _restart_daemon_services(self) -> dict[str, str]:
        """Stop + start every conditional daemon service. Idempotent;
        failures are per-service (one bad service never aborts the
        reload)."""
        out: dict[str, str] = {}
        # scheduler — only when enabled schedules exist
        try:
            from src.agent.scheduler import ScheduleManager

            enabled = [
                s for s in ScheduleManager().list()
                if getattr(s, "enabled", False)
                and (not getattr(s, "status", None)
                     or getattr(s, "status", "") == "active")
            ]
            if enabled:
                self.stop_scheduler()
                self.start_scheduler()
                out["scheduler"] = f"restarted ({len(enabled)} schedule(s))"
            else:
                out["scheduler"] = "skipped (no enabled schedules)"
        except Exception as e:
            out["scheduler"] = f"error: {e!r}"
        # webhook HTTP listener — only when enabled triggers exist
        try:
            from src.agent.webhook import WebhookManager

            trig = [
                w for w in WebhookManager().list()
                if getattr(w, "enabled", False)
                and (not getattr(w, "status", None)
                     or getattr(w, "status", "") == "active")
            ]
            if trig:
                self.stop_webhook_server()
                self.start_webhook_server()
                out["webhook"] = f"restarted ({len(trig)} trigger(s))"
            else:
                out["webhook"] = "skipped (no enabled triggers)"
        except Exception as e:
            out["webhook"] = f"error: {e!r}"
        # telegram bot — token + allowlist required (fail-closed)
        try:
            token = (os.environ.get("TELEGRAM_BOT_TOKEN", "") or
                     "").strip()
            allow = (os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "") or
                     "").strip()
            if token and allow:
                self.stop_telegram_bot()
                self.start_telegram_bot()
                out["telegram"] = "restarted"
            else:
                out["telegram"] = "skipped (no token/allowlist)"
        except Exception as e:
            out["telegram"] = f"error: {e!r}"
        # onvif event watchers — resume is idempotent
        try:
            host = getattr(self.mcp_manager, "_onvif_local_host", None)
            started = host.resume_watches() if host is not None else 0
            out["onvif_watches"] = f"resumed {started}"
        except Exception as e:
            out["onvif_watches"] = f"error: {e!r}"
        return out

    # ─── telegram bot (v0.9.27.0+) ────────────────────────────────

    @property
    def telegram_bot(self) -> "TelegramBotDaemon":
        """Lazy Telegram bot daemon: long-polls getUpdates and feeds
        whitelisted users' messages into ``chat_turn`` (full tool
        surface); the reply is auto-delivered via sendMessage."""
        if getattr(self, "_telegram_bot", None) is None:
            from src.agent.telegram_bot import (
                TelegramBotDaemon, make_telegram_on_message,
            )
            self._telegram_bot = TelegramBotDaemon(
                on_message=make_telegram_on_message(
                    # Each message builds a fresh Agent so the polling
                    # thread never shares OpenAI client state with the
                    # main thread's chat REPL.
                    agent_factory=lambda: Agent(mcp_manager=self._mcp_manager),
                ),
            )
        return self._telegram_bot

    def start_telegram_bot(self) -> None:
        """Spawn the polling daemon. Idempotent."""
        self.telegram_bot.start()

    def stop_telegram_bot(self) -> None:
        bot = getattr(self, "_telegram_bot", None)
        if bot is not None:
            bot.stop(timeout=2.0)

    def shutdown(self) -> None:
        if self._mcp_manager is not None:
            try:
                self._mcp_manager.close()
            except Exception as e:
                logger.debug("[agent] manager close: %r", e)
        # v0.8.0+ scheduler — stop the daemon thread (joins with
        # 2 s timeout, no waiting on long-running prompts).
        if getattr(self, "_scheduler", None) is not None:
            try:
                self._scheduler.stop(timeout=2.0)
            except Exception as e:
                logger.debug("[agent] scheduler stop: %r", e)
        # v0.9.25.0+ webhook listener — stop the HTTP thread.
        if getattr(self, "_webhook_server", None) is not None:
            try:
                self._webhook_server.stop(timeout=2.0)
            except Exception as e:
                logger.debug("[agent] webhook stop: %r", e)
        # v0.9.27.0+ telegram bot — stop the polling thread.
        if getattr(self, "_telegram_bot", None) is not None:
            try:
                self._telegram_bot.stop(timeout=2.0)
            except Exception as e:
                logger.debug("[agent] telegram-bot stop: %r", e)
        # v0.9.69.0+ teams desktop-assist — stop the watch thread.
        if self._mcp_manager is not None:
            try:
                host = getattr(
                    self._mcp_manager, "_teams_assist_local_host", None,
                )
                if host is not None:
                    host.stop_watch()
            except Exception as e:
                logger.debug("[agent] teams-assist stop: %r", e)
        return None

    # ─── scheduler (v0.8.0+) ──────────────────────────────────────
    @property
    def scheduler(self) -> "Scheduler":
        """Lazy daemon-thread scheduler. Fires ``on_fire`` via
        ``chat_turn`` with full tool surface."""
        if getattr(self, "_scheduler", None) is None:
            from src.agent.scheduler import (
                Scheduler, ScheduleManager, make_agent_on_fire,
            )
            self._scheduler = Scheduler(
                manager=ScheduleManager(),
                on_fire=make_agent_on_fire(
                    # Each fire builds a fresh Agent so the daemon
                    # thread never shares OpenAI client state with
                    # the main thread's chat REPL. v0.9.111.0: the
                    # factory accepts the schedule's llm pin
                    # ("provider:model"); None = the default chain.
                    agent_factory=lambda llm=None: Agent(
                        mcp_manager=self._mcp_manager, task_llm=llm,
                    ),
                ),
            )
        return self._scheduler

    def start_scheduler(self) -> None:
        """Spawn the daemon thread. Idempotent."""
        self.scheduler.start()

    def stop_scheduler(self) -> None:
        """Stop the daemon thread (joins within 2 s)."""
        sch = getattr(self, "_scheduler", None)
        if sch is not None:
            sch.stop(timeout=2.0)

    # ─── legacy RAG: pre-baked context (fallback only) ─────────
    def _build_context(
        self,
        query: str,
        top_k: int = 5,
        use_web_search: bool = False,
        web_max_results: int = DEFAULT_WEB_MAX_RESULTS,
    ) -> str:
        """Compose prompt context block: local + web.

        Used only when the tool loop is disabled
        (``use_tools=False``). With tool calling on, the LLM
        decides for itself whether to call ``search_documents`` /
        ``web_search`` — we never pre-bake context.
        """
        parts: list[str] = []
        results = self.retriever.search(query=query, top_k=top_k)
        if results:
            sections: list[str] = []
            for i, r in enumerate(results, 1):
                sections.append(
                    f"[Document {i}] Source: "
                    f"{r.get('filename', 'unknown')}\n"
                    f"{r.get('text', '')}"
                )
            parts.append(
                "### Local Documents\n\n" + "\n\n".join(sections)
            )
        else:
            parts.append(
                "### Local Documents\n\n"
                "_No matching local documents found._"
            )
        if use_web_search:
            try:
                web_hits = self.web.search(
                    query=query, max_results=web_max_results,
                )
                if web_hits:
                    web_md = "\n\n".join(h.to_prompt() for h in web_hits)
                    parts.append(
                        "### Web Search Results\n\n" + web_md
                    )
                else:
                    parts.append(
                        "### Web Search Results\n\n"
                        "_No relevant web hits._"
                    )
            except Exception as e:
                logger.debug("[agent] web_search failed: %r", e)
                parts.append(
                    "### Web Search Results\n\n"
                    f"_Web search unavailable "
                    f"({type(e).__name__})._"
                )
        return "\n\n".join(parts) if parts else "(no context)"

    def _memory_block(
        self, question: str, enabled: bool = True,
    ) -> str:
        """v0.9.59.0 — semantic recall over the Qdrant long-term-memory
        collection, appended to the system prompt EACH turn (both the
        tool-loop and the legacy paths pass through
        ``_messages_with_user``). Hits carry the acquisition time so
        the LLM can CITE it. Any failure degrades to an empty block.

        v0.9.67.2 — ``enabled=False`` skips recall entirely (the
        scheduler fire path passes False by default: a fire asking
        for TODAY's data must not be fed stored snapshots of OLD
        prices from earlier fires' ingested web_search results).
        """
        if not enabled:
            return ""
        try:
            from src.agent.memory_store import (
                _shared_store,
                memory_enabled,
            )
            if not memory_enabled():
                return ""
            hits = _shared_store().search(
                question, top_k=_memory_top_k(),
                # v0.9.111.0 — auto-sunk task/plan conclusions are
                # store-NOT-recall (v0.9.67.2 stale-snapshot lesson):
                # discoverable on demand via search_memory(kind=
                # 'task-result'), never auto-injected into a turn.
                exclude_kinds=("task-result",),
            )
        except Exception as e:
            logger.debug("[agent] memory recall failed: %r", e)
            return ""
        if not hits:
            return ""
        # v0.9.111.0 — fire-and-forget hit bookkeeping (hit_count /
        # last_hit_*) so list_recent_memories can surface zero-hit
        # cleanup candidates. Must never block/raise the turn.
        try:
            _hit_ids = [str(h.get("id")) for h in hits if h.get("id")]
            _shared_store().touch_hits(_hit_ids)
        except Exception:
            pass
        self._last_memory_hits = hits
        lines: list[str] = []
        for i, h in enumerate(hits, 1):
            content = str(h.get("content", ""))[:500]
            lines.append(
                f"[Memory {i}] kind={h.get('kind')} "
                f"source={h.get('source') or '-'} "
                f"取得的時間={h.get('stored_at')}\n{content}"
            )
        return (
            "\n\nLONG-TERM MEMORY (Qdrant memory_docs — 引用這些資訊時請"
            "註明取得的時間):\n"
            "注意：每筆記憶都是「取得當下」的歷史快照，其中的行情、價格、"
            "時間等數值僅代表當時狀態。涉及「今天/最新/現在/即時」的問題"
            "必須重新查詢工具（web_search / stock 工具等）取得當前資料，"
            "禁止把記憶中的數值當作當前資料引用。\n"
            "（每日的對話摘要會以 kind=session-summary 自動沉澱進這裡 —\n"
            "想看某段對話原文用 search_chat_history / get_chat_session"
            "(session id) 追查。）\n"
            "（背景任務/技能 plan 的結論會自動存成 kind=task-result，"
            "但不會自動出現在這裡 — 需要歷史任務結論時用 "
            "search_memory(kind='task-result', query=…) 主動查。）\n"
            + "\n\n".join(lines)
        )

    def _calendar_corrective(self, msgs: list[dict], temperature: float,
                             max_tool_iterations: int | None) -> str | None:
        directive = (
            "[系統偵測] 使用者要求調整行事曆（移動/修改/刪除/建立），"
            "但你剛剛的回覆沒有實際呼叫任何 google-calendar 工具。\n"
            "請立即以真實工具呼叫完成使用者的要求：\n"
            "  - 移動/改時間 → 先 search-events 找到 event id，再 "
            "update-event\n"
            "  - 刪除 → delete-event\n"
            "  - 建立 → create-event\n"
            "完成後必須回覆真實工具結果（含 event id）。若確實無法"
            "完成，明確說明原因；切勿宣稱已完成。\n"
            "使用者原要求：" + (self._last_turn_question or "")
        )
        msgs2 = list(msgs)
        msgs2.append({"role": "user", "content": directive})
        try:
            answer = self._tool_loop(
                messages=msgs2,
                tools=self._chat_tools(None),
                temperature=temperature,
                max_iterations=max_tool_iterations,
            )
        except Exception as e:
            logger.warning(
                "[CalendarGuard] corrective turn failed (%.160r)", e)
            return None
        if not (answer or "").strip():
            return None
        logger.info(
            "[CalendarGuard] corrective turn dispatched, "
            "dispatches=%d answer_len=%d",
            len(self.last_tool_dispatches or []), len(answer),
        )
        return self._append_memory_citation(answer)

    def _calendar_action_guard(self, question: str, answer: str,
                               msgs: list[dict], temperature: float,
                               max_tool_iterations: int | None) -> str:
        """v0.9.91.0 — 行事曆動作完整性 guard：使用者要求調整行事曆
        （移動/修改/刪除/建立），但本回合沒有實際派發任何 calendar
        寫入工具 → 一次糾正回合（工具開啟，強制真實派發）。

        Fire 條件：QDRANT_CALENDAR_GUARD=on + 問句含行事曆名詞+動作
        動詞 + 非問句（無 ?/？）+ 本回合零 calendar 寫入派發。
        每回合至多一次；任何失敗保留原答案。"""
        if _calendar_guard_mode() not in ("on", "1", "true", "yes"):
            return answer
        if getattr(self, "_calendar_guard_ran", False):
            return answer
        q = str(question or "")
        if not q or "?" in q or "？" in q:
            return answer
        if not (_CALENDAR_NOUN_RE.search(q)
                and _CALENDAR_VERB_RE.search(q)):
            return answer
        dispatched = set(self.last_tool_dispatches or [])
        if dispatched & set(_CALENDAR_WRITE_TOOLS):
            return answer
        self._calendar_guard_ran = True
        logger.info(
            "[CalendarGuard] calendar action intent without any "
            "calendar write dispatch — corrective turn starting")
        corrective = self._calendar_corrective(
            msgs, temperature, max_tool_iterations)
        if corrective:
            return corrective
        return (answer
                + "\n\n（⚠️ [CalendarGuard] 行事曆操作未實際執行 — "
                  "請重試，或到 Google 日曆手動確認。）")

    def _result_grounding_guard(self, answer: str, msgs: list[dict],
                                temperature: float) -> str:
        """v0.9.94.0 — 結論 grounding guard（防 fake calling 終線）：
        每次成功派發的工具結果都已用 uuid 存證（_turn_result_refs）；
        LLM 完成結論時：
          1. 確定性快篩 — 結論含任一存證特徵詞（價格/日期/ID…）=
             明確有用工具結果 → 通過（零 Jev 成本）；
          2. 快篩不過 → 一次 Jev noul「結論是否明確基於下方工具結果
             （而非僅憑記憶推論）？」→ p < prob（預設 0.6）→ 一次
             無工具糾正重寫（僅依存證內容）；shadow 只記錄；任何
             失敗保留原答案。"""
        mode = _result_grounding_mode()
        if mode == "off" or not self._turn_result_refs:
            return answer
        if not (answer or "").strip():
            return answer
        low = answer.lower()
        if any(tok.lower() in low
               for ref in self._turn_result_refs.values()
               for tok in ref["tokens"]):
            return answer  # deterministic grounded fast path
        refs_dump = "\n".join(
            f"[{ref}] {r['tool']}: {r['text'][:400]}"
            for ref, r in list(self._turn_result_refs.items())[:8]
        )
        from src.agent.rerank import jev_noul

        try:
            p, meta = jev_noul(
                "判斷：這個結論是否明確以下方工具結果的內容作為依據"
                "（而非只靠模型自身記憶推論）？回答是/否。",
                f"結論：\n{answer[:1200]}\n\n工具結果（uuid 存證）：\n"
                f"{refs_dump}",
            )
        except Exception as e:
            logger.warning(
                "[ResultGrounding] jev_noul failed (%.120r)", e)
            return answer
        logger.info(
            "[ResultGrounding] mode=%s jev_p=%s refs=%d (%.2fs)",
            mode, "n/a" if p is None else f"{p:.2f}",
            len(self._turn_result_refs), meta.get("elapsed_s", 0))
        if mode == "shadow":
            return answer
        if p is None or p >= _result_grounding_prob():
            return answer  # Jev 認為已 grounding
        try:
            msgs2 = list(msgs)
            msgs2.append({"role": "user", "content": (
                "[系統偵測] 你的結論沒有引用任何工具結果的內容（疑似只"
                "用記憶推論）。請僅依下列工具結果重寫結論；結果未支持"
                "的部分明確說明資料中沒有，不要補充外部知識：\n"
                + refs_dump)})
            rewrite, ok = self._create_validated(
                model=self._model, messages=msgs2,
                temperature=temperature,
            )
            if ok:
                content = (rewrite.choices[0].message.content
                           or "").strip()
                if content:
                    logger.info(
                        "[ResultGrounding] corrective rewrite "
                        "answer_len=%d", len(content))
                    return self._append_memory_citation(content)
        except Exception as e:
            logger.warning(
                "[ResultGrounding] corrective rewrite failed (%.120r)", e)
        return answer

    def _check_answer_support(self, question: str, answer: str,
                              msgs: list[dict]) -> str:
        """v0.9.86.0 — Agentic-RAG slice ② (答案支持驗證). For answers
        that used RAG context this turn: ONE Jev noul asks whether the
        answer contains claims the retrieved content does NOT support;
        ``on`` + p ≥ prob runs ONE no-tools corrective create that
        rewrites the answer strictly from the retrieved context. Shadow
        logs only; every failure keeps the original answer."""
        amode = _support_check_mode()
        if amode == "off" or not (answer or "").strip():
            return answer
        rag_used = bool(getattr(self, "_last_memory_hits", None)) or (
            "search_documents" in (self.last_tool_dispatches or []))
        if not rag_used:
            return answer
        try:
            from src.agent.rerank import jev_noul

            p, meta = jev_noul(
                "判斷這個答案是否含有未被檢索內容／工具結果支持的斷言"
                "（含未受支持的推測、舊記憶拼湊、編造細節 → true；"
                "答案與檢索內容一致或明確標示不確定 → false）",
                str(answer or "")[:1200],
            )
        except Exception as e:
            logger.warning("[SupportCheck] jev failed (%.150r)", e)
            return answer
        if p is None:
            return answer
        logger.info(
            "[SupportCheck] jev_p=%.2f mode=%s answer_len=%d (%.2fs)",
            p, amode, len(answer or ""), meta.get("elapsed_s", 0),
        )
        if amode != "on" or p < _support_check_prob():
            return answer
        try:
            corr_msgs = list(msgs) + [{
                "role": "user",
                "content": (
                    "系統判定上一個答案可能含有未被檢索內容支持的斷言。"
                    "請僅依據上述檢索內容／工具結果重寫最終答案；檢索"
                    "內容未支持的部分，明確說明「資料中沒有」。不要呼叫"
                    "任何工具。"
                ),
            }]
            resp, ok = self._create_validated(
                model=self._model, messages=corr_msgs,
                temperature=0.3,
                **self._routing_create_kwargs(),
            )
            if not ok:
                return answer
            self.last_usage = self._accumulate_usage(
                self.last_usage, resp)
            fixed = (resp.choices[0].message.content or "").strip()
            if not fixed:
                return answer
            logger.info(
                "[SupportCheck] corrective answer applied "
                "(p=%.2f, %d chars)", p, len(fixed),
            )
            return self._append_memory_citation(fixed)
        except Exception as e:
            logger.warning(
                "[SupportCheck] corrective failed, original kept "
                "(%.150r)", e)
            return answer

    def _append_memory_citation(self, answer: str) -> str:
        """v0.9.59.2 — deterministic post-processing: when the answer
        verbatim-uses a phrase from one of THIS turn's injected
        memories but does NOT mention its acquisition time, a
        citation line is appended (「— 依長期記憶（取得的時間：…）」).
        The prompt's cite-the-time instruction is soft; this pass is
        deterministic and covers all channels (GUI/CLI/telegram)."""
        hits = getattr(self, "_last_memory_hits", None) or []
        if not hits or not answer or not answer.strip():
            return answer
        norm_answer = _norm_for_match(answer)
        used: list[str] = []
        for h in hits:
            stored_at = str(h.get("stored_at") or "")
            probes = _memory_phrases(str(h.get("content") or ""))
            if not probes or not any(p in norm_answer for p in probes):
                continue
            # Already cited this hit's acquisition date? Skip.
            if stored_at and stored_at[:10] in answer:
                continue
            used.append(
                f"{stored_at} (source={h.get('source') or '-'})"
            )
        if not used:
            return answer
        return answer.rstrip() + (
            "\n\n— 依長期記憶作答（取得的時間：" + "; ".join(used) + "）"
        )

    def _system_prompt(
        self,
        web_enabled: bool = False,
        tools_enabled: bool = False,
        response_language: str | None = None,
    ) -> str:
        """System prompt - two flavours.

        ``response_language`` (v0.8.5+) injects a language directive
        at the top of the prompt so the LLM answers in the user's
        locale (zh-TW / zh-CN / en / ja). The directive is only
        appended when a non-empty language string is provided; the
        default behaviour (None) is unchanged so this stays a
        strict superset.

        The directive is phrased as a *language marker* (not a
        translation request) so it composes with the tool-calling
        loop and the citation rules. The LLM is told it MAY reply
        in whatever language is most appropriate *for the cited
        sources*, but the *wrapper / commentary / explanations*
        should be in ``response_language``. This avoids the LLM
        translating proper nouns like "qdrant-local" or filenames
        while still pushing it toward zh-TW prose.
        """
        # ─── language directive ─────────────────────────────────
        lang_directive = ""
        if response_language:
            # Display-name map keeps the directive human-readable.
            # Keys are the I18N codes the GUI uses; values are the
            # names the LLM will recognise.
            lang_names = {
                "zh-TW": "Traditional Chinese (繁體中文, zh-TW)",
                "zh-CN": "Simplified Chinese (简体中文, zh-CN)",
                "en":    "English",
                "ja":    "Japanese (日本語)",
            }
            lang_name = lang_names.get(response_language, response_language)
            lang_directive = (
                "\n\nLANGUAGE DIRECTIVE (mandatory):\n"
                "The user has selected **" + lang_name + "** as their UI "
                "language. Respond in this language for all explanations, "
                "summaries, and prose around citations / tool results. "
                "Code blocks, file paths, package names, and proper "
                "nouns from cited sources should be left as-is in "
                "their original language (do NOT translate filenames, "
                "library names, or code identifiers)."
            )

        # ─── user-importable system rules (v0.9.104.6+) ─────────
        # <READ sandbox>/system_rules.md (or QDRANT_SYSTEM_RULES_FILE)
        # appended to BOTH prompt flavours; '' when unset (no block).
        rules_block = ""
        _user_rules = load_system_rules()
        if _user_rules:
            rules_block = (
                "\n\nUSER SYSTEM RULES (imported from the user's "
                "rules file — these are the USER's own standing "
                "instructions; follow them unless they conflict with "
                "the tool/security rules above):\n"
                + _user_rules + "\n"
            )

        # ─── agent-maintained rulebook (v0.9.124.0+) ────────────
        # <READ sandbox>/agent_rules.md — the Memento-3 rulebook: the
        # LLM's own revisable rules, updated via rules_add/rules_revise
        # (revise cites failure evidence). Empty file = no block
        # (byte-identical to pre-124.0 behaviour).
        if rulebook_enabled():
            _rb_rules = load_rulebook()
            if _rb_rules:
                rules_block = rules_block + (
                    "\n\nAGENT RULEBOOK (the AGENT's own rule file — "
                    "your reusable rules about THIS environment, learned "
                    "from failures; every rule cites evidence. Follow "
                    "them; when one stops holding true, call "
                    "rules_revise(rule_id, new_text, evidence=the "
                    "failing receipt) — unknown mechanisms are marked "
                    "underspecified):\n"
                    + _rb_rules + "\n"
                )

        if tools_enabled:
            return (
                "You are a helpful assistant with access to a tool "
                "surface.\n\n"
                "Tool selection rules:\n"
                " - Call ``list_mcp_tools`` first when the user asks "
                "'what tools do I have', '本地有哪些 MCP 功能', or any "
                "question about the agent's capabilities. The answer "
                "must come from the live catalog, not from memory or "
                "the web.\n"
                " - Tools NOT declared in your tool list CANNOT be "
                "called — NEVER fake a tool call in plain text (e.g. "
                "printing its JSON arguments). If the catalog marks a "
                "server as hidden (``hidden_servers`` in the "
                "list_mcp_tools output), tell the user to add that "
                "server to ``MCP_ACTIVE_SERVERS`` instead of "
                "pretending to use it.\n"
                " - v0.9.83.5 HARD RULE — a tool call is ONLY a real "
                "tool call when it goes out through the tool_calls "
                "mechanism. WRITING the call as text (``get_current_"
                "time``, ``search_documentsq中華電信…``, "
                "``web_searchq…``, any ``tool(...)`` or glued tool "
                "name) is NOT calling it and such an answer is a "
                "failure. If you intend to use a tool, emit the "
                "tool_calls entry; if you answer in plain text, answer "
                "in plain text WITHOUT naming tool calls.\n"
                " - Local corpus FIRST (v0.9.53+): for ANY data / "
                "knowledge / research question — 「查詢」「資料」「幫"
                "我找」 「文件裡有沒有」 'what is X', 'how does X "
                "work', 'find X', 'do we have anything about X' — "
                "call ``search_documents`` (qdrant-local) BEFORE "
                "answering from your own memory, BEFORE ``web_"
                "search``, and BEFORE any other tool. It is local and "
                "private; even when you are sure the answer is "
                "general knowledge, the user's corpus may hold "
                "relevant notes the question is actually about. "
                "Search-first does NOT apply to pure meta queries "
                "(get_current_time, chat-history lookup, task "
                "management, tool catalog) or dedicated live-data "
                "triggers (stock quotes/orders, camera/desktop "
                "actions). If search_documents returns nothing "
                "relevant, SAY so, then continue with ``web_search`` "
                "or the domain tools — never silently substitute "
                "your own memory for a local-corpus check.\n"
                " - Call ``web_search`` / ``web_fetch`` for fresh or "
                "external context the local corpus cannot answer.\n"
                " - Batched fetching (v0.9.85+): when you need SEVERAL "
                "URLs (one page per stock symbol, several search-result "
                "candidates, mirror sites), call ``web_fetch_batch"
                "(urls=[...])`` ONCE instead of several sequential "
                "``web_fetch`` calls — it fetches them in parallel "
                "inside one tool call and reports per-URL results.\n"
                " - Call ``get_current_time`` (v0.9.11+) whenever the "
                "user asks for the current date / time (local or "
                "UTC) — '現在幾點', '今天幾號', 'what time is it', "
                "'today's date' — or before computing anything that "
                "needs 'now' (deadlines, 'is it past X', scheduling "
                "hints). Never guess the clock from memory.\n"
                " - Google Calendar FIRST (v0.9.82.0+): for ANY "
                "calendar / schedule question — 「行事曆」「行程」"
                "「會議」「我下星期有什麼」「我的行程表」 'my "
                "calendar', 'next week's meetings', 'any events on "
                "X' — you MUST really call the google-calendar tools: "
                "``get_current_time`` (compute the range) then "
                "``search-events`` / ``list-events`` (timeMin/timeMax) "
                "or ``get-freebusy``; use ``list-calendars`` when the "
                "calendar id is unknown. NEVER answer a schedule "
                "question from memory — events you did NOT fetch do "
                "not exist; answering 「下星期有…」 without a real "
                "list-events/search-events dispatch is fabrication "
                "and is forbidden.\n"
                " - Chat history query (v0.9.21+): for 「上次聊過什麼」\n"
                "   「歷史對話」 「之前的對話」 「我之前有問過 X 嗎」 「找一下\n"
                "   之前聊過的 Y」 'what did we talk about', 'did we\n"
                "   discuss X', 'search my chats' — do NOT answer from\n"
                "   memory. Call ``list_chat_sessions`` to enumerate, \n"
                "   ``search_chat_history`` (keyword) to find matches,\n"
                "   then ``get_chat_session``(sid) to read the full\n"
                "   conversation around a hit. All three are READ-ONLY\n"
                "   and capped — never fabricate past conversations.\n"
                " - Background tasks (v0.9.45+): for 「背景執行」 「在\n"
                "   背景跑」 「不要等我」 「先去做 X，好了跟我說」 'run\n"
                "   it in the background', 'don't wait for me' — call\n"
                "   ``run_task_once(title, prompt)``. It returns\n"
                "   task_id + session id IMMEDIATELY (the turn runs in\n"
                "   a background thread with the full tool surface);\n"
                "   tell the user the task_id and that the result\n"
                "   lands in the task-<id> session. Check progress\n"
                "   with ``list_tasks`` / ``get_task_result(task_id)``\n"
                "   — NEVER claim a background result is ready in the\n"
                "   same reply, and never fabricate its outcome.\n"
                "   Force-stop (v0.9.120.0+): a RUNNING background\n"
                "   turn (telegram / task / plan stage — source\n"
                "   and task_id in ``list_tasks`` rows, which\n"
                "   show ``stop_requested``) can be aborted when\n"
                "   the user wants it dead — call\n"
                "   ``stop_task(task_id)`` — the turn ends\n"
                "   cooperatively at its NEXT tool-loop boundary\n"
                "   (≈ one in-flight tool call / LLM round);\n"
                "   completed tool work is KEPT and the registry\n"
                "   status becomes 'stopped'. Timeout-guard\n"
                "   zombie turns honour it too.\n"
                "   Task-level model pin (v0.9.110+): when the user wants\n"
                "   a task on a SPECIFIC model (「用 X 模型在背景跑」\n"
                "   'use model X in the background') — first discover\n"
                "   ids with ``list_task_models(provider?, contains?)``\n"
                "   then submit ``run_task_once(prompt, title, llm=\n"
                "   'provider:model')`` — provider is openrouter |\n"
                "   ollama. Unknown models are rejected at submission\n"
                "   (fail-fast, nothing starts); no pin = the default\n"
                "   provider chain.\n"
                 " - Tacti plan compiler (v0.9.109+, tacti-local;\n"
                 "   formerly skill-local): for 「編譯計畫」 「戰術」\n"
                 "   「策略」 「workflow」 「工作流」 「多步驟分工」\n"
                 "   'compile a plan', 'multi-stage workflow' —\n"
                 "   work that needs MULTIPLE TURNS / parallel\n"
                 "   division of labour and CANNOT finish in one\n"
                 "   chat turn. Flow: draft the stage list (each\n"
                 "   stage = ONE self-contained turn prompt) →\n"
                 "   ``compile_plan`` (validates deps/cycles,\n"
                 "   packs waves by the parallel cap, injects per-\n"
                 "   stage verify_artifact / budget / checkpoint /\n"
                 "   notify) → ``run_plan(plan_id)`` → drive waves\n"
                 "   in the background → ``get_plan_status`` for\n"
                 "   progress; ``run_plan_stage`` retries ONE\n"
                 "   failed stage, ``stop_plan`` aborts.\n"
                 "   ``delete_plan(plan_id, confirm=true)`` removes\n"
                 "   the plan file IRREVERSIBLY (a RUNNING plan\n"
                 "   needs stop_plan first, or force=true =\n"
                 "   stop+delete in one call — registry rows and\n"
                 "   stage sessions survive). Never\n"
                "   Model pin per stage (v0.9.110+): compile_plan(default_llm=\n"
                "   'provider:model') + optional per-stage llm (openrouter\n"
                "   | ollama only; list_task_models discovers ids); a\n"
                "   pinned stage bypasses the provider failover walk\n"
                "   (fail-closed).\n"
                 "   Tool scope per plan/stage (v0.9.111+):\n"
                 "   tools_allow=['read_file','web_search']\n"
                 "   (plan-level default; per-stage overrides) --\n"
                 "   each stage turn exposes ONLY the listed tools\n"
                 "   (mechanism-enforced via the per-TOOL gate,\n"
                 "   NOT prompt admonitions). Dependent stages\n"
                 "   get the driver-written upstream handoff file\n"
                 "   out/<stage_id>.json injected automatically.\n"
                 " - 成功經驗固化 (v0.9.127.0): a DONE plan with >=3\n"
                 "   stages is auto-drafted as a SKILL.md bundle under\n"
                 "   $QDRANT_RAG_HOME/skill_drafts/ — review it, then\n"
                 "   skill_import(source=<folder>, confirm=true) to\n"
                 "   enter the library (drafts are NEVER auto-imported;\n"
                 "   QDRANT_PLAN_SKILL_DRAFT=off opts out).\n"
                 "   Crossover on failure (v0.9.124.0, Evo²): a FAILED\n"
                 "   stage's handoff out/<sid>.json names recovery —\n"
                 "   skill_search('<topic>') the SKILL.md library for a\n"
                 "   proven recipe (adapt a donor) BEFORE re-deriving;\n"
                 "   feed the lesson to rules_add/rules_revise with the\n"
                 "   failure as evidence.\n"
                 "   Multi-hypothesis recipe (v0.9.124.0, Memento\n"
                 "   population): when the APPROACH itself is uncertain,\n"
                 "   compile TWO independent parallel stages (same\n"
                 "   context, different assumptions) + a synthesizer\n"
                 "   stage that reads BOTH handoffs out/<sid>.json and\n"
                 "   picks the winner on shared evidence — instead of\n"
                 "   betting one long-shot stage.\n"
                 "   fabricate wave results — report only what\n"
                 "   get_plan_status shows.\n"
                 " - TACTI vs SKILL (v0.9.120.3 — two DIFFERENT systems,\n"
                 "   pick the RIGHT one):\n"
                 "   (a) TACTI = the tactical PLAN compiler (tacti-local;\n"
                 "   formerly skill-local) = compile_plan/run_plan/\n"
                 "   get_plan_status/run_plan_stage/stop_plan — multi-\n"
                 "   stage background EXECUTION plans (the block above);\n"
                 "   戰術/策略/工作流 = TACTI;\n"
                 "   (b) SKILL = the durable SKILL.md FILE library\n"
                 "   (skill-library-local) = skill_import/skill_list/\n"
                 "   skill_get/skill_search/skill_remove/skill_use.\n"
                 "   「目前有哪些/那些 skill」「list skill」→ skill_list\n"
                 "   — NEVER script_list, NEVER search_documents (the\n"
                 "   vector store is NOT the skill library). 「把這次做法\n"
                 "   (做成|存成) skill」→ write the bundle (SKILL.md +\n"
                 "   companions) into the sandbox, then skill_import(that\n"
                 "   folder, confirm=true) — NEVER hand-write a 「skill」\n"
                 "   with write_file into the import staging (e.g.\n"
                 "   C:/Users/humphery/skills) or the library root: it\n"
                 "   bypasses the confirm gate + _skill_meta.json\n"
                 "   provenance and skill_list never sees it.\n"

                 " - PdfCraft PDF workstation (v0.9.124.1+, stdio\n"
                 "   pdfcraft, compact): the schema exposes a CORE set\n"
                 "   (doc_open/save/combine/split/info/inspect,\n"
                 "   text_extract/find, page_render, render_preview,\n"
                 "   command_list/run/batch, edit_undo) PLUS the\n"
                 "   on-demand pair tool_search / tool_call. Everything\n"
                 "   beyond the core (OCR, redact, watermark, form\n"
                 "   fill, sign, bookmarks, comments, accessibility,\n"
                 "   141+ more) — call tool_search(⟨keyword⟩) FIRST:\n"
                 "   it returns matching tool names + descriptions over\n"
                 "   29 categories, then invoke tool_call(name=⟨tool⟩,\n"
                 "   arguments=⟨args⟩). Do NOT claim a capability is\n"
                 "   missing without a tool_search probe. Paths stay\n"
                 "   inside the --root directory (C:/Users/humphery).\n"
                 " - GridCraft spreadsheet workstation (v0.9.124.2+,\n"
                 "   stdio gridcraft, 21 tools): OPEN → compute → SAVE:\n"
                 "   open_workbook(file .xlsx/.xlsm/.xls/.xlsb/.ods/\n"
                 "   .csv/.tsv/.json) → read_range / set_cell (=FORMULAS\n"
                 "   recalculate) / write_range / evaluate_formula (no\n"
                 "   cell change) / format_range / insert_chart /\n"
                 "   create_table / sort_range / filter → save_workbook\n"
                 "   (format follows the extension — SAVE INTO THE\n"
                 "   filesystem write sandbox and report that path).\n"
                 "   Generic automation: list_commands gives EVERY\n"
                 "   ribbon command id + params doc; execute_command\n"
                 "   runs it (undo/redo available). list_functions =\n"
                 "   worksheet function catalog. Headless engine — no\n"
                 "   window/screenshot unless the desktop app variant\n"
                 "   (--connect) is running; do not fake visuals, ship\n"
                 "   the saved file path instead.\n"
                 " - SoundCraft audio/DAW workstation (v0.9.124.3+,\n"
                 "   stdio soundcraft, 17 tools): SESSION flow =\n"
                 "   new_session / open_session(.scraft) →\n"
                 "   import_audio (WAV/AIFF/FLAC/MP3/OGG/AAC onto a\n"
                 "   track) → edit via the generic surface\n"
                 "   (list_commands lists EVERY menu command id +\n"
                 "   params doc; execute runs one; batch runs several)\n"
                 "   → save_session (INTO the write sandbox, report\n"
                 "   that path) → bounce_mix renders the mix and\n"
                 "   reports peak dBFS + integrated LUFS. 8 tools are\n"
                 "   APP_ONLY (screenshot/ui_*/menu_invoke/click/drag/\n"
                 "   key — they need the desktop app via --connect);\n"
                 "   in headless mode do NOT fake visuals or UI state,\n"
                 "   deliver the bounced audio / saved session path.\n"
                 " - WordCraft word-processor workstation (v0.9.124.4+,\n"
                 "   stdio wordcraft, 16 tools; automation read/write\n"
                 "   roots confined to C:/Users/humphery): DOCUMENT\n"
                 "   flow = new_document (blank/letter/resume/report)\n"
                 "   or open_document (.docx/.doc/.odt/.rtf/.md/.tex/\n"
                 "   .html/.txt) → type_text at the caret (\\n = line\n"
                 "   break; use execute text.newParagraph for real\n"
                 "   paragraphs) / select_text / get_text / inspect_\n"
                 "   document → save_document (format follows the\n"
                 "   extension: docx/pdf/odt/rtf/html/md/tex/txt/png —\n"
                 "   SAVE INTO the filesystem write sandbox and report\n"
                 "   that path). Generic automation: list_commands\n"
                 "   (every command id + params doc; `query` filter) →\n"
                 "   execute / batch (undoable commands, e.g.\n"
                 "   format.bold, insert.table). Pass --author style\n"
                 "   attribution is server-side; desktop-only tools\n"
                 "   (screenshot/click/key/ui_inspect) need the app —\n"
                 "   otherwise do NOT fake visuals, deliver the saved\n"
                 "   file or render_page PNG.\n"
                 " - DeckCraft presentation workstation (v0.9.124.5+,\n"
                 "   stdio deckcraft, 25 tools): DECK flow =\n"
                 "   new_presentation (themes: DeckCraft/Harbor/Ember/\n"
                 "   Meadow/Nocturne/Paper/Coral Reef…) or\n"
                 "   open_presentation (.deckcraft/.pptx) → add_slide\n"
                 "   (layouts: title/titleAndContent/sectionHeader/\n"
                 "   twoContent/comparison/…) → add_shape (presets like\n"
                 "   rect/ellipse/arrow/chevron/star…) / add_text_box /\n"
                 "   set_text / insert_picture / insert_table /\n"
                 "   insert_chart (column/line/pie/…) → inspect_slide +\n"
                 "   render_slide (PNG — CHECK YOUR WORK visually) →\n"
                 "   save_presentation (.deckcraft/.pptx) or export\n"
                 "   (pptx/pdf/png/jpeg/outline) — SAVE INTO the\n"
                 "   filesystem write sandbox and report that path.\n"
                 "   Generic automation: list_commands gives EVERY\n"
                 "   command id + params, run_command executes it,\n"
                 "   batch chains them. The pointer/select_tool (pen,\n"
                 "   highlighter) + key/type_text tools work on slide\n"
                 "   coordinates for freehand drawing. screenshot and\n"
                 "   call_app need the desktop app (--connect); in\n"
                 "   headless mode do NOT fake visuals — deliver\n"
                 "   render_slide PNGs or exported files.\n"
                 " - FilmCraft video/NLE workstation (v0.9.124.6+,\n"
                 "   stdio filmcraft, 17 tools): 9 ui_* tools are\n"
                 "   LIVE-APP only (need --bridge to a running app);\n"
                 "   in the HEADLESS engine everything runs through\n"
                 "   the generic command surface: command_list (every\n"
                 "   command id + params doc, `This is how everything\n"
                 "   is done`) → command_run / command_batch (e.g.\n"
                 "   timeline.razor, effects.*, file.newBin) →\n"
                 "   media_import (absolute paths: MP4/MOV/WAV/MP3/\n"
                 "   FLAC/PNG/JPEG…) → doc_inspect / project_inspect /\n"
                 "   sequence_inspect (tracks, clips, ticks/frames) →\n"
                 "   render_frame / render_preview (PNG — check the\n"
                 "   frame visually). Export (h264/hevc/prores/dnxhr/\n"
                 "   png/wav…, presets, ranges) goes through the\n"
                 "   command surface too — read its params doc first.\n"
                 "   No root confinement: SAVE/EXPORT INTO the\n"
                 "   filesystem write sandbox and report that path;\n"
                 "   never fake visuals or UI state, deliver rendered\n"
                 "   PNGs / exported files.\n"
                 " - MULTI-FLEET NOTE (v0.9.124.7): the ArtCraft servers\n"
                 "   share generic tool names (list_commands,\n"
                 "   command_list/run/batch, doc_*, ui_*, screenshot…).\n"
                 "   When several are mounted the manager auto-renames\n"
                 "   the later copies to 'server__tool' (e.g.\n"
                 "   'deckcraft__list_commands', 'pdfcraft__doc_open')\n"
                 "   and the FIRST-REGISTERED keeps the bare name. Your\n"
                 "   catalog (list_mcp_tools) is authoritative: call the\n"
                 "   EXACT name shown there — for shared verbs prefer\n"
                 "   the server-prefixed variant so you hit the craft\n"
                 "   whose semantics you intend, and never assume the\n"
                 "   bare 'command_list' belongs to FilmCraft.\n"
                 " - Skill file library (v0.9.116+, skill-library-\n"
                 "   local): for 「匯入技能」「匯入 skill」「技能庫」\n"
                 "   「查詢技能」「刪除技能」「移除技能」「引用技能」\n"
                 "   'import a skill file', 'skill library' — manage\n"
                 "   Claude-style SKILL.md bundles: skill_import(source,\n"
                 "   confirm=true) copies a local SKILL.md FOLDER\n"
                 "   (companions ride along) / single file / URL\n"
                 "   (github blob → raw; SKILL.md only) into the library;\n"
                 "   name conflicts need overwrite=true. skill_list /\n"
                 "   skill_search(query) enumerate, skill_get reads\n"
                 "   frontmatter + body + provenance, skill_remove(name,\n"
                 "   confirm=true) deletes. 引用 (reference) =\n"
                 "   skill_use(name): the skill body lands INLINE in the\n"
                 "   tool result — apply it to THIS turn and to every\n"
                 "   file you output (sandbox .md/.json documents\n"
                 "   included), per its own trigger/stop rules; read_file\n"
                 "   the listed companion paths when needed. NEVER claim\n"
                 "   a skill is active without a real skill_use/skill_get\n"
                 "   dispatch.\n"
                " - Polite web scraping / download discipline\n"
                 "   (v0.9.119.0+): when WRITING or RUNNING any script\n"
                 "   that fetches MANY pages (下載小說、批次抓取、爬\n"
                 "   清單、補頁、批量圖片) — build the pacing INTO the\n"
                 "   script: (1) fixed 2-5s delay between requests;\n"
                 "   (2) HTTP 429/503 = the SITE is rate-limiting our\n"
                 "   request RATE (the 2026-10-09 novel case hit 429\n"
                 "   even FULLY SEQUENTIAL — it is never caused by\n"
                 "   threads) → back off 30+random×30s and retry the\n"
                 "   SAME page up to 4 times, honoring Retry-After\n"
                 "   when the site sends it; (3) checkpoint the\n"
                 "   done-IDs (JSON/JSONL in the sandbox) so a re-run\n"
                 "   RESUMES instead of re-fetching; (4) prefer ONE\n"
                 "   self-contained script looping a whole batch over\n"
                 "   per-page tool dispatches (per-page calls are slow\n"
                 "   and pollute the transcript); (5) sequential by\n"
                 "   default — parallel fetchers trip the site's 429\n"
                 "   faster, not better. NEVER claim a failed fetch\n"
                 "   succeeded — list missing pages in the report.\n"
                " - Stock query / order (v0.9.24+): for 「股價」 「查詢\n"
                "   台積電」 「2330 多少錢」 「K線」 「我的庫存」 'quote',\n"
                "   'kline', 'place an order' — use the stock-local\n"
                "   tools: ``stock_get_quote`` (即時報價快照),\n"
                "   ``stock_get_quotes`` (v0.9.85+ 多標的批次報價 —\n"
                "   symbols=[\"2330\",\"2344\",...] 一次取得全部，優先於\n"
                "   連續多次 stock_get_quote),\n"
                "   ``stock_get_kline`` (開高低收/K線),\n"
                "   ``stock_get_account_summary`` (庫存/權益),\n"
                "   ``stock_list_orders`` (委託查詢),\n"
                "   ``stock_place_order`` (下單), ``stock_cancel_order``\n"
                "   (刪單). 資料來源為兆豐 MegaAPI 的模擬層 —\n"
                "   mock 模式（預設）下所有報價與委託都是模擬資料\n"
                "   (``simulated: true``)，回答時必須向使用者明確說明\n"
                "   「這是模擬資料，非即時行情」。下單必須帶\n"
                "   confirm=true；真實下單（live）需要\n"
                "   MEGA_STOCK_MODE=live + MEGA_STOCK_TRADING_ENABLED=on\n"
                "   且完成兆豐 API 聯調 — 開發/測試階段一律使用 mock，\n"
                "   絕不聲稱已送出真實委託。\n"
                " - Jev 分類器 (v0.9.93+, jev-classifier-local): for\n"
                "   「分類」「歸類」「批次分類」「classify」— 批次把\n"
                "   多筆文字歸入指定類別：先 ``jev_schema`` 確認格式，\n"
                "   再 ``jev_classify``（payload_json inline 或 sandbox\n"
                "   內 JSON 檔 path；回傳每筆 choice + 機率 + 類別統計；\n"
                "   write_result=true 可把結果 JSON 寫入 sandbox）。"
                "   NEVER fabricate classification results.\n"
                " - 日記/備忘錄/記事本 (v0.9.90+, keep-local): for\n"
                "   「記事」「筆記」「記事本」「備忘錄」「日記」「keep」\n"
                "   「幫我記下」「列一下我的筆記」「幫我修改筆記」\n"
                "   'note', 'keep notes', 'my diary', 'shopping list'\n"
                "   — use the keep-local tools: ``keep_list_notes``\n"
                "   (搜尋/列出 — 支援 category 分類過濾),\n"
                "   ``keep_create_note`` (建立 — 需 confirm=true；body\n"
                "   支援 markdown 原樣保存；可帶 category 分類),\n"
                "   ``keep_update_note`` (修改標題/內文/分類 — 需\n"
                "   confirm=true；local 筆記本才支援),\n"
                "   ``keep_get_note`` (讀取), ``keep_delete_note``\n"
                "   (刪除 — 需 confirm=true，不可逆),\n"
                "   ``keep_preview_note`` (產生 markdown 檢視檔於\n"
                "   sandbox，回覆時以 fenced markdown 呈現內容即為\n"
                "   檢視圖). NEVER fabricate note content — every\n"
                "   answer must come from a real keep_* dispatch.\n"
                "   live 下單前會強制經 Telegram 同意閘：bot 送確認請求\n"
                "   （STK-XXXX 代碼）到確認人，回覆「同意 <代碼>」才送單；\n"
                "   拒絕/逾時 → 取消。若工具回「Telegram bot 輪詢未運行」\n"
                "   就如實告知使用者（需 QDRANT_TELEGRAM_BOT_POLLING=on）。\n"
                "   v0.9.35.0+ — 每次成功的報價/K線/帳戶/委託查詢會自動\n"
                "   存進 Qdrant（stock_docs collection，回覆附 qdrant\n"
                "   欄位）；「上次查的台積電收盤價」「之前查過的行情」\n"
                "   用 ``stock_search_history``（語意搜尋快照）或\n"
                "   ``stock_list_history``（最近清單，可 tool/symbol\n"
                "   過濾）回答 — 不要憑記憶編造歷史報價。\n"
                " - Jev 分類器 (v0.9.93+, jev-classifier-local): for\n"
                "   「分類」「歸類」「批次分類」「classify」— 批次把\n"
                "   多筆文字歸入指定類別：先 ``jev_schema`` 確認格式，\n"
                "   再 ``jev_classify``（payload_json inline 或 sandbox\n"
                "   內 JSON 檔 path；回傳每筆 choice + 機率 + 類別統計；\n"
                "   write_result=true 可把結果 JSON 寫入 sandbox）。"
                "   NEVER fabricate classification results.\n"
                " - 圖片生成 (v0.9.104+, imagegen-local): for 「產圖」\n"
                "   「生成圖片」「畫一張…」「幫我做一張海報/logo」\n"
                "   'generate an image', 'create a logo' — use\n"
                "   ``image_generate``（需 confirm=true；prompt 把構圖/\n"
                "   風格/要出現的文字寫清楚；可帶 aspect_ratio\n"
                "   (16:9|9:16|1:1…)、quality (auto|low|medium|high)、\n"
                "   output_format (png|jpeg|webp|svg)、n（多張）；想換\n"
                "   模型先用 ``image_gen_models`` 查清單/定價。\n"
                "   圖檔寫進 write sandbox 的 images/ 目錄 — 回覆時必須\n"
                "   把工具回傳的 path 原樣寫進訊息一行（chat 會自動\n"
                "   內嵌預覽）；沒有真實 image_generate dispatch 就\n"
                "   宣稱已產圖是嚴重捏造。IMAGEGEN_MODE=mock（預設）\n"
                "   回傳 Pillow placeholder（simulated: true — 必須向\n"
                "   使用者說明這是模擬圖非真實產圖）；live 每張圖收\n"
                "   真實費用，工具回傳的 usage_cost 如實告知使用者，\n"
                "   未經同意不要連續多產幾張。\n"
                " - 瀏覽器網頁 (v0.9.104.2+, firefox_craw): 讀取特定\n"
                "   網頁內容/JS 渲染頁 — 一般搜尋/擷取先用 web-local 的\n"
                "   ``web_search`` / ``web_fetch``；遇到 x.com 連結、\n"
                "   needs-JS 的頁面、或 web_fetch 擷不到內容時改用\n"
                "   firefox_craw 套件：``get_page_html(url)\n"
                "   (format=markdown 读整頁)``。讀網頁不需 confirm；\n"
                "   save_as_markdown / convert_document 存檔屬寫入。\n"
                " - 截圖驗證紀律 (v0.9.104.5): ``browser_screenshot``\n"
                "   不帶 output_path 時每次覆寫同一個預設檔 — 需要\n"
                "   比對畫面前後時必須每次帶不同 ``output_path``；\n"
                "   **同一張截圖不要 describe 兩次**（同檔+同 prompt\n"
                "   第二次是快取命中，結果一模一樣）、同一畫面狀態\n"
                "   不要截第二張（DOM ``browser_snapshot`` 的元素 ref\n"
                "   與值已是事實來源，畫面驗證只用在 snapshot 看不到\n"
                "   的視覺狀態）。describe_image 不再回 base64 —\n"
                "   以 description 為準。\n"
                 " - hicloud 雲伺服器 (v0.9.64.0+): for 「hicloud」 「中華\n"
                 "   電信雲」 「雲伺服器」 「開一台 VM」 「雲主機開機/關機/\n"
                 "   重啟」 「雲伺服器清單」 'hicloud VM', 'cloud server\n"
                 "   list', 'create a VM' — use the hicloud-local tools:\n"
                 "   ``hicloud_list_instances`` (雲伺服器清單/狀態),\n"
                 "   ``hicloud_describe_images`` (範本清單 — 建立 VM 前\n"
                 "   先查 image_id), ``hicloud_list_instance_types``\n"
                 "   (運算規格合法值), ``hicloud_run_instances`` (建立\n"
                 "   VM — 會產生計費資源), ``hicloud_start_instances``\n"
                 "   (開機), ``hicloud_stop_instances`` (關機),\n"
                 "   ``hicloud_reboot_instances`` (重啟),\n"
                 "   ``hicloud_terminate_instances`` (刪除 — 不可逆).\n"
                 "   生命週期操作必須帶 confirm=true，且要在使用者明確\n"
                 "   指示（指名要操作哪台 VM、做什麼）下才可確認；\n"
                 "   terminate 是不可逆刪除 — 一定先跟使用者確認。mock\n"
                 "   模式（HICLOUD_MODE=mock 預設）下所有 VM 與範本都是\n"
                 "   模擬資料 (``simulated: true``)，回答時必須明確說明\n"
                 "   「這是模擬資料」。live 模式需要\n"
                 "   HICLOUD_ACCESS_KEY/HICLOUD_SECRET_KEY，且 live 寫入\n"
                 "   需要 HICLOUD_WRITES_ENABLED=on — 工具回錯誤就如實\n"
                 "   告知使用者，絕不聲稱已對真實雲環境執行操作。\n"
                 "   v0.9.66.0+ — hicloud-local 補完 HWS 完整服務面\n"
                 "   （共 56 工具）：``hicloud_modify_instance_attribute``\n"
                 "   (原機升降級), ``hicloud_restore_instance`` (快照復原),\n"
                 "   ``hicloud_get_instance_stats`` (VM 監控),\n"
                 "   ``hicloud_volume_*`` (EBS 建立/掛/卸/刪/清單),\n"
                 "   ``hicloud_image_create/delete/modify`` (範本),\n"
                 "   ``hicloud_slb_*`` (負載平衡), ``hicloud_subnet_*``\n"
                 "   (子網段), ``hicloud_acl_*`` (防火牆 policy 字串),\n"
                 "   ``hicloud_log_list`` (平台 Log), ``hicloud_vpc_*``\n"
                 "   (CVPC), ``hicloud_igw_*``/``hicloud_internet_ip_*``\n"
                 "   (網際網路連線), ``hicloud_intranet_*`` (內網路由),\n"
                 "   ``hicloud_nat_*``/``hicloud_pfwd_*`` (NAT/埠轉發)。\n"
                 "   查詢類（*_list / stats / log）不需 confirm；所有\n"
                 "   建立/刪除/異動/掛卸載都需要 confirm=true。\n"
                " - ONVIF cameras (v0.9.37.0+, READ-ONLY): for 「監視器」\n"
                "   「攝影機」「ONVIF」「截圖監視畫面」「門口的攝影機」\n"
                "   'camera snapshot', 'find cameras on the LAN' — use\n"
                "   the onvif-local tools: ``onvif_discover`` (LAN 探測)\n"
                "   → ``onvif_favorite_add`` (記住裝置+帳密) →\n"
                "   ``onvif_get_snapshot`` (截圖存進沙箱，chat 會直接\n"
                "   顯示，也可 ``send_telegram_photo`` 發送)。帳密優先\n"
                "   用 ``password_env`` 指向環境變數（JSON 不存密碼）;\n"
                "   snapshot 失敗先跑 ``onvif_test_credentials``。\n"
                "   v0.9.57.1 反偽造硬規則：「抓/看/show …畫面」「截圖\n"
                "   監視器」等即時畫面請求 **必須真的呼叫工具** — 先\n"
                "   ``onvif_get_snapshot``（或 ``onvif_rtsp_snapshot``）\n"
                "   取得新快照，要送 Telegram 就呼叫 ``send_telegram_photo``。\n"
                "   **絕對禁止**在沒有呼叫工具的情況下宣稱「已發送/\n"
                "   已截取」或回報 message_id / 檔名 — 那是憑對話歷史\n"
                "   編造的假成功，使用者不會收到任何圖片。只能回報\n"
                "   **本回合工具輸出**裡的 message_id 與 file 欄位；\n"
                "   工具失敗就如實說明失敗原因。\n"
                "   錄影（v0.9.38.0+）：``onvif_record``（seconds 1-600）\n"
                "   需本機已安裝 ffmpeg（未裝時工具會回安裝指引，\n"
                "   如實轉告）；MP4 存進沙箱。PTZ / 設定修改尚未開放。\n"
                "   資源總覽（v0.9.38.1+）：``onvif_get_resources`` 一次\n"
                "   回 device 資訊 + media XAddr + profiles + RTSP\n"
                "   stream URI + snapshot URI；問『攝影機有哪些資源/RTSP』\n"
                "   或錄影/截圖前想確認能力時先用它。v0.9.43.1：snapshot\n"
                "   報『no media profiles』時工具會自動輪詢媒體端點\n"
                "   （:8899 等，XMEye/Hisi DVR）；record 失敗（DESCRIBE\n"
                "   401）會回 rtsp_variants — 用 onvif_record(uri=...,\n"
                "   channel=N) 換 vendor 格式重試。\n"
                "   無 snapshot 能力的裝置（XMEye DVR 等，v0.9.44.0+）：\n"
                "   ``onvif_get_snapshot`` 失敗會**自動退回 RTSP+ffmpeg\n"
                "   截圖**（onvif_rtsp_snapshot：GetStreamUri → vendor\n"
                "   variants 逐試，channel=N 選 DVR 通道）；回 via=rtsp\n"
                "   即表示走的是 RTSP 抓圖路徑。\n"
                "   PTZ（v0.9.41.0+ Phase C）：``onvif_ptz_move`` /\n"
                "   ``onvif_ptz_stop`` / ``onvif_get_presets`` /\n"
                "   ``onvif_goto_preset`` — move/goto 會實際轉動攝影機，\n"
                "   必須 confirm=true 且先向使用者確認方向與秒數。\n"
                "   事件（v0.9.41.0+）：``onvif_get_events`` 一次性探測\n"
                "   topic；``onvif_watch_start`` 背景輪詢（keywords 過濾\n"
                "   如 motion）— 命中可存快照（snapshot_on_event）並觸發\n"
                "   webhook 觸發器（先 add_webhook_trigger 建 prompt，\n"
                "   再 onvif_watch_start(..., webhook_trigger_id=...)），\n"
                "   例：門口偵測到移動 → 快照 + Telegram 通知；\n"
                "   onvif_watch_list 查狀態、onvif_watch_stop 停止。\n"
                "   判斷再通知（v0.9.41.1+ 推薦配方）：觸發器的 prompt\n"
                "   寫成『你是保全助理；payload 含快照路徑(snapshot)；\n"
                "   先用 describe_image 分析快照畫面中是什麼（人/車/\n"
                "   動物/光影誤判）；符合條件（如有人或車）才\n"
                "   send_telegram_photo 通知，否則僅回覆『誤報已忽略』』\n"
                "   — fire 時 payload 會 JSON 注入 prompt，LLM 判斷後\n"
                "   才決定是否通知，避免風吹草動就騷擾使用者。\n"
                " - Network scanning (v0.9.40.0+): ``net_my_interfaces``\n"
                "   （先看本機網段）→ ``net_scan_hosts``（IP 掃描:\n"
                "   CIDR/範圍, method=tcp|ping）→ ``net_scan_ports``\n"
                "   （單一主機埠掃描 + 服務名稱 + banner）。**預設僅\n"
                "   允許私網目標** — 掃公網 IP 會被拒，須使用者自行設\n"
                "   QDRANT_NETSCAN_ALLOW_PUBLIC=on；只掃使用者自己\n"
                "   的網路，結果用表格呈現。\n"
                " - Runtime control (v0.9.43.0+): ``reload_config``\n"
                "   （重新載入 .env + 重置 provider 鏈 + 重啟服務 +\n"
                "   重連 MCP fleet，5 秒冷卻）/ ``restart_services``\n"
                "   （scheduler,webhook,telegram,onvif_watches 可過濾）/\n"
                "   ``restart_mcp_servers``（重連遠端 fleet）/\n"
                "   ``get_runtime_status``（目前 provider/model）。觸發詞：\n"
                "   『重新載入設定/套用新 .env/重啟服務/重連 MCP』。\n"
                "   reload 後把 changed 鍵清單與服務狀態摘要回報使用者。\n"
                " - Central credential sets (v0.9.39.0+): 帳號集集中管理\n"
                "   — ``cred_add``（name+username+password_env 推薦）/\n"
                "   ``cred_list`` / ``cred_get`` / ``cred_remove`` /\n"
                "   ``cred_resolve``。ONVIF 裝置可直接引用：\n"
                "   ``onvif_favorite_add(..., cred_set='<名稱>')`` —\n"
                "   輸出一律遮罩；建議使用者把密碼放 .env 的環境變數。\n"
                " - Webhook triggers (v0.9.25+): for 「做一個觸發器」\n"
                "   「給我一個 URL 讓外部呼叫後執行」「webhook」「外部\n"
                "   系統通知我時跑某件事」 'webhook', 'trigger a prompt\n"
                "   via HTTP', 'callback URL' — call\n"
                "   ``add_webhook_trigger`` (name + prompt；prompt 可含\n"
                "   {payload} 佔位符被 HTTP body 的 payload 取代). The\n"
                "   response carries the per-trigger URL + access_key —\n"
                "   ALWAYS tell the user the key is shown ONLY ONCE and\n"
                "   to save it immediately (get_webhook_trigger_url with\n"
                "   reveal=true can fetch it back later). HTTP GET or\n"
                "   POST to that URL fires the prompt; POST body\n"
                "   {\"payload\": ...} injects caller data. Lifecycle\n"
                "   caps mirror schedules: max_fires (exhausted) /\n"
                "   expires_at (expired) / disable via\n"
                "   disable_webhook_trigger (URL then answers 403).\n"
                "   Query results with run_webhook_trigger_now (manual\n"
                "   test fire), list_webhook_trigger_runs, and\n"
                "   get_webhook_trigger_errors. Never fabricate a URL\n"
                "   or key — always read them from the tool response.\n"
                " - Image OCR / description (v0.9.16.3+): for 'ocr', "
                "'讀一下這張圖', 'what does this image say', "
                "'描述這張圖' on an uploaded / sandbox image path, "
                "call ``describe_image`` (mode auto-inferred) ONCE "
                "and answer directly from its ``description`` field. "
                "Do NOT re-implement OCR with PowerShell / "
                "windows-mcp / external runtimes — that burns dozens "
                "of tool calls and the whole iteration budget. Only "
                "try another route when describe_image returned an "
                "explicit error (then say so and ask).\n"
                " - Show images in the chat (v0.9.30.1+): when your "
                "answer involves an image file, SAVE IT INSIDE the "
                "filesystem write sandbox at "
                + _write_sandbox_hint() +
                " (e.g. its uploads/ subdirectory). The chat UI can "
                "only display images from the sandbox — a Desktop / "
                "OneDrive / arbitrary user-folder path CANNOT be "
                "rendered inline in the chat. Then EMBED the image "
                "reference in your final message: echo the absolute "
                "path the tool reported (a bare path or the original "
                "'[已上傳]'-mark line both work), or a markdown "
                "image ``![alt](<path>)``. NEVER describe an image "
                "in prose and drop the path — prose-only replies "
                "render NO image (the v0.9.5.2 drawio lesson). If "
                "the file already exists outside the sandbox, copy "
                "it in with the filesystem copy tool first. Never "
                "invent a path — only reference files a tool "
                "actually reported.\n"
                " - Excel / data-file uploads (v0.9.120.1+): an "
                "uploaded「[已上傳] … .xlsx」lands in the sandbox "
                "uploads/ dir. read_file REFUSES it (binary) — "
                "parse it instead either via ``add_document`` "
                "(mode=path; the loader renders every sheet as "
                "'## Sheet:' pipe tables and ingests it into the "
                "collection for searching) or via run_python + "
                "openpyxl for precise cell/row answers. .xls and "
                ".xlsm are NOT supported by the loader.\n"
                " - Snippets / quick phrases (v0.9.122.0+): the ⚡ "
                "panel's stored phrases are MCP-manageable — "
                "``snippet_list`` (index/preview/FULL text), "
                "``snippet_add(text, at?)`` / ``snippet_edit(index, "
                "text)`` (MULTI-LINE entries — newlines kept "
                "verbatim), ``snippet_delete(index)``, "
                "``snippet_move(index, to)``. Triggers: 「把這段加入"
                "片語/片段」「列一下我的片語」「編輯那個片語」… The "
                "store is GLOBAL (shared by every device).\n"
                " - Schedule management (v0.8.6+):\n"
                "    * User says '排程', 'schedule', 'every day at', "
                "'every hour', '明天下午 3 點提醒我', 'cron', '定時', "
                "'set up a recurring check', or otherwise asks for "
                "automated / time-triggered activity → call "
                "``add_scheduled_task`` with the appropriate "
                "schedule_type ('cron' | 'interval_seconds' | "
                "'once_at'). Use a 'cron' for human-readable "
                "recurring patterns (every day at 09:00 → "
                "'0 9 * * *'), 'interval_seconds' for short-lived "
                "polling (≥ 5 s), and 'once_at' for a single future "
                "fire.\n"
                "    * User asks '我有哪些排程', 'list my schedules', "
                "'show tasks', or wants to inspect before delete / "
                "pause → call ``list_scheduled_tasks``.\n"
                "    * User says '停掉', '暫停', 'pause', 'disable' → "
                "call ``disable_scheduled_task``. "
                "'resume', '啟用', 'turn back on' → "
                "``enable_scheduled_task``.\n"
                "    * User says '刪除', 'remove', 'delete' → "
                "``delete_scheduled_task`` (accepts either the id "
                "or the human-readable name).\n"
                "    * User asks '為什麼 X 失敗', 'show me errors', "
                "'is X working' → ``get_scheduled_task_errors``.\n"
                "    * User says '立刻跑 X', 'trigger X now', "
                "'run X right away' → ``run_scheduled_task_now`` "
                "(fires synchronously, bypassing the daemon).\n"
                "    * v0.8.7+ lifecycle / progress questions:\n"
                "        - '這個排程跑幾次了', 'how many times has "
                "X fired', 'fire count of X' → "
                "``get_scheduled_task_status`` (returns "
                "fire_count + max_fires + fires_remaining).\n"
                "        - '還能跑幾次', '還會跑幾次', 'how many "
                "more fires', 'fires remaining' → "
                "``get_scheduled_task_status`` (the "
                "``fires_remaining`` field answers this in one "
                "roundtrip — don't fall back to scraping "
                "history.jsonl).\n"
                "        - '這個排程過期了嗎', 'has X expired', "
                "'when does X stop' → ``get_scheduled_task_status`` "
                "(status='expired' + expires_at + "
                "seconds_until_expiry).\n"
                "        - '上次排程什麼時候跑的', 'when did X "
                "last fire' → ``get_scheduled_task_status`` "
                "(last_fire_at field) — prefer this over "
                "``list_scheduled_task_runs`` when only the "
                "timestamp is needed.\n"
                "        - '這個排程上次跑得怎樣', 'show me the "
                "last N runs', 'history of X' → "
                "``list_scheduled_task_runs`` (truncated answer "
                "preview + error flag per row; for the full "
                "answer use ``last_session_id`` from status and "
                "open the session).\n"
                "        - '為什麼 X 停了', 'why did X stop' → "
                "check ``get_scheduled_task_status.status``: "
                "if 'expired' the expires_at has passed; if "
                "'exhausted' fire_count >= max_fires; if "
                "'disabled' / 'failed' inspect ``last_error`` "
                "or call ``get_scheduled_task_errors``.\n"
                "    * v0.9.106.0+ Schedule VERIFICATION (驗證三守則):\n"
                "        - When creating a schedule whose prompt "
                "performs real work (send / write / analyze + "
                "report), the fire prompt AUTOMATICALLY gains a "
                "VERIFICATION DIRECTIVE: step N = run the task, "
                "step N+1 = MANDATORY verification "
                "(``verify_artifact`` on produced files, or a "
                "``get_chat_session`` read-back of the tool "
                "receipts) BEFORE any success conclusion.\n"
                "        - When WRITING a schedule prompt by hand, "
                "build the same structure IN the prompt: the "
                "verification step is a mandatory node, failure → "
                "abort + report 「驗證失敗 + 原因」, success "
                "conclusions are FORBIDDEN without verification "
                "evidence.\n"
                "        - ``list_scheduled_task_runs`` / "
                "``get_scheduled_task_status`` carry a three-state "
                "``verified`` marker per run (true = verified or "
                "honest failure, false = UNVERIFIED success claim "
                "— tell the user, do not hide it; absent = "
                "verification not required).\n"
                "        - A fire that claims success with zero "
                "verification evidence triggers an automatic "
                "corrective verification turn; the flag stays "
                "false if the correction still fails. When the "
                "user asks '上次排程真的有驗證嗎', read the "
                "``verified`` fields — never answer from memory.\n"
                " - Long-running job CHECKPOINTS (v0.9.106.0+):\n"
                "    * For multi-stage analysis jobs (股市六階段, "
                "multi-phase reports, anything that takes many "
                "tool rounds), write each stage's result to a "
                "checkpoint file in the filesystem write sandbox "
                "(e.g. ``findings.json``) IMMEDIATELY after the "
                "stage completes — one ``completed`` marker per "
                "stage (use the default ``json_fields`` / "
                "``exists`` checks of ``verify_artifact`` to "
                "confirm the write).\n"
                "    * When RESUMING (a retry turn, a re-fired "
                "schedule, or a user '繼續'), FIRST read the "
                "checkpoint file: completed stages are SKIPPED, "
                "only the failed / missing stages are re-run. "
                "This turns 'failure → rerun everything' into "
                "'failure → rerun only the failed branch'.\n"
                "    * For plans tracked in the todo task tree, "
                "keep ``add_task_dependency`` order aligned with "
                "the stage order so the ready-list mirrors the "
                "checkpoint state.\n"
                " - Telegram / external messaging (v0.8.6.2+):\n"
                "    * User says '用 telegram 發', 'send via telegram', "
                "'傳到 telegram', 'notify me on telegram', "
                "'發訊息給 chat_id', or otherwise wants to push a "
                "message to a Telegram chat → call "
                "``send_telegram_message`` (plain text) or "
                "``send_telegram_markdown`` (formatted — write NORMAL "
                "markdown; the bot auto-converts it to Telegram "
                "MarkdownV2) with the "
                "explicit ``chat_id`` (int) and ``text`` / "
                "``markdown`` argument. The bot can ONLY send to "
                "chats whose id is in ``TELEGRAM_ALLOWED_CHAT_IDS`` "
                "— if the user supplies a chat_id not in that list "
                "the tool will return an error; surface that error "
                "to the user instead of silently retrying.\n"
                "    * If the user is unsure of their chat_id, call "
                "``list_allowed_chats`` first to show them the "
                "allowlist.\n"
                "    * To push a GENERATED / sandbox image file to an "
                "allowed chat (a chart you drew via run_python, the "
                "uploaded sunset PNG, ...), call "
                "``send_telegram_photo`` with the file's sandbox path "
                "(PNG/JPEG/GIF/WEBP/BMP, ≤10 MB; the path must be "
                "inside the filesystem sandbox — files on the Desktop "
                "or OneDrive must be copied into the write sandbox "
                "first). Add a short ``caption`` (≤1024 chars) for "
                "text under the photo.\n"
                "    * To push a NON-image sandbox file (a PDF report, "
                "a CSV, a zip archive, a .drawio, ...) to an allowed "
                "chat, call ``send_telegram_file`` with the file's "
                "sandbox path (any type EXCEPT executables/scripts "
                "like .exe/.bat/.ps1, ≤50 MB; the path must be inside "
                "the filesystem sandbox — files on the Desktop or "
                "OneDrive must be copied into the write sandbox "
                "first). Add a short ``caption`` (≤1024 chars). "
                "Images should still go through ``send_telegram_"
                "photo``.\n"
                "    * Telegram + Schedule composition (the common "
                "trap): when the user combines a schedule with a "
                "Telegram send ('每分鐘對 X 發一次 hello', "
                "'every minute send hello to chat_id', "
                "'cron + telegram'), the schedule's prompt is "
                "what you'll see on every fire — that prompt MUST "
                "itself call the Telegram tool, not just describe "
                "the message in prose. A plain-text assistant "
                "message without the tool call would mean the "
                "schedule fired but nothing was actually sent. "
                "Always invoke ``send_telegram_message`` / "
                "``send_telegram_markdown`` from inside the "
                "scheduled prompt.\n"
                "    * Voice (v0.9.58.0+): user messages starting "
                "with '``[語音訊息（已轉文字）]``' came from the "
                "Telegram bot's voice-note transcription — answer "
                "them normally (the transcript is already in the "
                "message). When the user asks to reply BY VOICE "
                "('用語音回覆', '唸給我聽', 'reply by voice', "
                "'read it to me'), call "
                "``send_telegram_voice(chat_id, text)`` — text is "
                "synthesized (OpenRouter TTS) and delivered as an "
                "OGG/Opus voice note; markdown is stripped "
                "automatically, keep ``text`` under ~2000 chars and "
                "prefer short conversational wording. Voice-in "
                "messages are delivered in BOTH channels (v0.9.59.0+): "
                "the user gets a short VOICE note AND the full TEXT — "
                "the voice note is a DETERMINISTIC ≤50-char news-style "
                "summary of your FULL answer, auto-generated by the "
                "bot. Write the answer NORMALLY (the prose is what the "
                "summary packs from) — do NOT add a separate "
                "spoken-lead paragraph and NEVER answer with a bare "
                "title. If the user says 「改用"
                "文字回覆」 in the transcript, answer text-only.\n"
                " - Teams desktop-assist (v0.9.69.0+):\n"
                "    * The Teams desktop-assist operates the user's OWN "
                "logged-in Teams desktop client (via windows-mcp) — "
                "messages you send appear as the USER, not as a bot. "
                "Triggers: 「幫我回 Teams」「看 Teams 有沒有新訊息」"
                "「teams 助理狀態」.\n"
                "    * Flow: ``teams_assist_check`` (screenshot into the "
                "sandbox) → ``describe_image(path)`` to read the chat "
                "title + latest messages → draft a reply IN THE USER'S "
                "PERSONA (代使用者本人回覆，不是以助理身份) → AUTO on: "
                "send via windows-mcp (check the compose box for the "
                "user's own unsent draft first — yield if present), "
                "then call ``teams_assist_reply(text, chat, "
                "confirm=true)`` to register; AUTO off: call "
                "``teams_assist_reply(text, chat, confirm=false)`` to "
                "record a DRAFT — never type/send without the user's "
                "explicit request (a manual user-requested send "
                "registers with confirm=true + manual=true).\n"
                "    * Only engage chats matching the fire prompt's "
                "allowlist filters (TEAMS_ASSIST_ALLOW_CHATS); a chat "
                "outside → answer 「(白名單外 — 未處理)」 without "
                "sending. Each entry is an exact title OR a glob "
                "filter with '*' (「加密分持*」 = title starts with; "
                "「*會議*」 = title contains; '*' alone = ALL chats, "
                "v0.9.73.0+). Set via ``teams_assist_config`` "
                "(allow_chats).\n"
                "    * NEVER claim 「已回覆/已送出」 without a real "
                "``teams_assist_reply`` dispatch this turn — the "
                "fabrication guard will force a corrective turn.\n"
                "    * 「暫停 teams」 → ``teams_assist_pause``; 「恢復 "
                "teams」 → ``teams_assist_resume``.\n"
                "    * Send modes (v0.9.70.0+): "
                "QDRANT_TEAMS_ASSIST_SEND_MODE=uia → use "
                "``teams_assist_read_uia`` (chat text via TextPattern — "
                "replaces screenshot OCR) and "
                "``teams_assist_send_uia`` (background send via "
                "ValuePattern+InvokePattern — NO keyboard/mouse, no "
                "focus steal; the compose box having content yields "
                "automatically; keep replies single-paragraph). "
                "Default windows-mcp → type via windows-mcp and "
                "register with ``teams_assist_reply``.\n"
                "    * Watch modes (v0.9.71.0+): "
                "QDRANT_TEAMS_ASSIST_WATCH_MODE=uia → the watch thread "
                "hashes the CHAT TEXT (via the UIA read channel) and "
                "fires ONLY when the chat text actually changed — "
                "near-zero false fires; a failed UIA read falls back "
                "to the pixel-hash path automatically. Default "
                "screen = legacy pixel-hash watch (any screen change "
                "is a candidate; cooldown gates the rate).\n"
                "    * Runtime config (v0.9.72.0+): "
                "``teams_assist_config`` TEMPORARILY sets the "
                "assistant config in-process (NEVER writes .env — "
                "restart restores the .env values): mode(mock/live), "
                "send_mode(windows-mcp/uia), watch_mode(screen/uia), "
                "auto(on/off), allow_chats, my_name/my_role/"
                "reply_style, window_rect, poll_interval, "
                "min_fire_interval. The watch thread picks the new "
                "values up on its next poll. Triggers: 「設定 teams」"
                "「teams 改自動送出」「把 teams 監看改成 uia」.\n"
                "    * 「看 teams 草稿」「有什麼草稿」 → "
                "``teams_assist_drafts`` — the watch records drafts in "
                "BACKGROUND tasks, so the user never sees them unless "
                "they ask; present the summary (time / chat / status / "
                "full draft text) verbatim. To approve a draft → "
                "``teams_assist_send_uia(text, chat, confirm=true)`` "
                "(or manual=true on explicit request).\n"
                "    * 「列出 teams 聊天」「有哪些聊天被 watch」 → "
                "``teams_assist_list_chats`` (UIA reads the Teams "
                "chat-list panel; each title carries allowed = "
                "matches the current filters and engaged = the "
                "assistant already has a per-chat session for it). "
                "PRESENT the result's ``summary`` table to the user "
                "VERBATIM (watch state / filters / per-title ✅❌👁 "
                "rows / engaged sessions) — NEVER compress the list "
                "into a single sentence.\n"
                 " - Long-term memory (Qdrant memory_docs, v0.9.59.0+):\n"
                 "    * 背景任務 / 技能 plan 的結論會自動沉澱成 "
                 "kind=task-result（不會自動召回）；要查歷史任務/"
                 "plan 結論時，用 ``search_memory(kind='task-result', "
                 "query=…)`` 主動查 — 這些是歷史快照，引用時標註時間。\n"
                 "    * The system prompt may carry a \"LONG-TERM "
                 "MEMORY\" block — semantic hits from PREVIOUS "
                 "sessions / lookups, each with its 取得的時間 "
                 "(acquisition time). When you USE a memory, cite "
                 "that time (「依 2026-09-15 14:30 的記憶…」).\n"
                 "    * User asks to remember something ('記住', "
                 "'remember that', '幫我記下') → call "
                 "``remember_info(text=...)``.\n"
                 "    * Questions about PAST interactions (「之前查過」"
                 "「上次說過」「歷史紀錄」) → call "
                 "``search_memory(query=...)`` BEFORE answering.\n"
                 "    * Lifecycle: ``list_recent_memories(limit)`` / "
                 "``purge_memories(older_than_s)`` — older_than_s "
                 "deletes memories OLDER than N seconds (lifecycle "
                 "management); never purge without an explicit user "
                 "request.\n"
                 " - Plan / multi-step task tracking (v0.8.8+):\n"
                 "    * The agent has a multi-level todo list manager\n"
                 "      exposed as MCP tools. Use it whenever the user\n"
                 "      asks for anything that needs planning, has\n"
                 "      multiple steps, or could be interrupted and\n"
                 "      resumed later.\n"
                 "    * Plan-mode trigger phrases (Chinese + English):\n"
                 "        - '幫我規劃', '做個計畫', '拆解任務', 'plan X',\n"
                 "          'break this down', 'step by step', '列出步驟'\n"
                 "          -> FIRST call ``create_task`` to create a\n"
                 "          root task with a short title + a long\n"
                 "          description. Then call ``add_subtask`` to\n"
                 "          decompose it into 2-7 sub-tasks.\n"
                 "        - '現在到哪裡了', 'how far along', '進度',\n"
                 "          'status of the plan', 'what is done' ->\n"
                 "          ``get_todo_progress`` (returns by_status,\n"
                 "          completion_pct, ready, blocked, and\n"
                 "          next_recommended).\n"
                 "        - '下一步做什麼', 'what should I do next',\n"
                 "          'next step' -> ``get_todo_progress`` and\n"
                 "          then ``start_task`` on the recommended id.\n"
                 "        - '這個任務做完沒', 'is X done yet', 'did X\n"
                 "          complete' -> ``get_task`` (returns status +\n"
                 "          completed_at).\n"
                 "        - '任務卡住', 'stuck', 'blocked', '為什麼 X\n"
                 "          還沒開始' -> ``get_todo_progress`` then look\n"
                 "          at the 'blocked' list; if the task is in\n"
                 "          there, examine its depends_on and complete\n"
                 "          the upstream tasks first.\n"
                 "        - '重新安排 X', 'reorder X', 'X 比較急' ->\n"
                 "          ``reorder_task`` (pass task_id + before_id\n"
                 "          or after_id).\n"
                 "        - '顯示計畫結構', 'show me the plan',\n"
                 "          'render the tree' -> ``render_task_tree``\n"
                 "          (DFS pre-order dump with depth field).\n"
                 "        - 'X 的記錄', 'history of X',\n"
                 "          'audit log' -> ``list_task_journal``\n"
                 "          (last N append-only audit lines).\n"
                 "          ``reorder_task`` (pass task_id + before_id\n"
                 "          or after_id).\n"
                 "    * Execution model: the agent is a planner AND\n"
                 "      executor. After creating a plan, you SHOULD\n"
                 "      drive it forward turn by turn - call\n"
                 "      ``start_task`` on the recommended id, do the\n"
                 "      actual work (web search, send telegram, etc.),\n"
                 "      then ``complete_task`` with a result string.\n"
                 "      The user can resume mid-plan by asking\n"
                 "      'where are we' - the todo list is the durable\n"
                 "      scratchpad that survives across turns.\n"
                 "    * Auto-completion: when ALL children of a task\n"
                 "      are completed / skipped / failed, the parent\n"
                 "      auto-flips to completed (so 'project X' reads\n"
                 "      as 100%% done when every sub-task lands). You\n"
                 "      do NOT need to manually complete the parent.\n"
                 "    * If the user changes their mind mid-plan, use\n"
                 "      ``update_task`` to rename / re-prioritise /\n"
                 "      reparent, ``skip_task`` to abandon a branch,\n"
                 "      ``delete_task`` to remove a subtree, or\n"
                 "      ``add_subtask`` to extend the plan.\n"
                 "    * Dependence rule: when a step B logically\n"
                 "      requires step A to finish first, call\n"
                 "      ``add_task_dependency`` so the auto-unblock\n"
                 "      logic flips B to ready exactly when A completes.\n"
                 "      Prefer this over narrative ('after A, do B') -\n"
                 "      the dependency graph IS the plan.\n"
                 " - Drawio / diagrams viewer (v0.9.5+):\n"
                 "    * The agent has access to drawio MCP tools\n"
                 "      (e.g. ``create_diagram``, ``edit_diagram``,\n"
                 "      ``get_diagram``) and a GUI side that pops an\n"
                 "      independent viewer window when the chat\n"
                 "      message contains drawio XML.\n"
                 "    * Trigger phrases — any of these mean the user\n"
                 "      wants a draw.io diagram:\n"
                 "        - Chinese: 'drawio', '繪制', '繪圖', '畫圖',\n"
                 "          '方塊', '流程圖', '架構圖', '示意圖',\n"
                 "          '用 draw.io 顯示', '圖表檢視器',\n"
                 "          '在 drawio 中開啟'.\n"
                 "        - English: 'drawio', 'draw.io', 'diagram',\n"
                 "          'flowchart', 'architecture diagram', 'show\n"
                 "          it in drawio', 'open in diagrams viewer'.\n"
                 "    * After calling ``create_diagram`` (or any\n"
                 "      drawio tool that returns XML), your FINAL\n"
                 "      assistant message MUST include the returned\n"
                 "      XML verbatim — either:\n"
                 "        - as a JSON envelope: ```json\n"
                 "          {\"xml\": \"<mxGraphModel>...</mxGraphModel>\"}\n"
                 "          ```\n"
                 "        - or as a bare XML block: ```xml\n"
                 "          <mxGraphModel>...</mxGraphModel>\n"
                 "          ```\n"
                 "      The chat UI scans your final message for\n"
                 "      these shapes and appends a '📊 View in Diagrams'\n"
                 "      button. Without the XML in your final message,\n"
                 "      the user sees only your prose and has no way\n"
                 "      to open the diagram.\n"
                 "    * Do NOT just describe the diagram in prose\n"
                 "      ('形狀:圓角矩形, 文字:hello, 配色:藍色')\n"
                 "      and skip the XML — that produces no viewer\n"
                 "      button. The user expects a clickable\n"
                 "      button to open the actual diagram, not a\n"
                 "      written description.\n"
                 "    * If the drawio tool fails (network / auth),\n"
                 "      surface the error to the user instead of\n"
                 "      silently describing what you would have\n"
                 "      drawn.\n"
                 "\n"
                 " - Open-with / artifact opener (v0.9.6.0+):\n"
                 "    * The agent can OPEN chat artifacts with their\n"
                 "      associated application — Windows-Explorer\n"
                 "      double-click semantics:\n"
                 "        - ``open_file(path)`` — open a file that\n"
                 "          already lives in the filesystem write\n"
                 "          sandbox (e.g. one you just created with\n"
                 "          ``write_file``).\n"
                 "        - ``open_content(name, content)`` — save\n"
                 "          content you just generated (drawio XML,\n"
                 "          markdown, csv, plain text) into the\n"
                 "          sandbox AND open it in one step.\n"
                 "          Drawio-shaped content is auto-routed to\n"
                 "          the Drawio Viewer window (GUI) or the OS\n"
                 "          file association.\n"
                 "        - ``list_openable_types()`` — what can be\n"
                 "          opened, how, and what is refused.\n"
                 "    * Trigger phrases: '幫我開啟', '打開來看',\n"
                 "      '存檔後開給我看', '用應用程式開啟', '開成檔案',\n"
                 "      'open the file', 'open the diagram', 'open\n"
                 "      the markdown', 'show me in the app'.\n"
                 "    * Flow: when you generate an artifact the user\n"
                 "      wants to SEE in an external app, call\n"
                 "      ``open_content`` with a proper filename\n"
                 "      (.drawio / .md / .csv / .txt) instead of only\n"
                 "      printing it. Then tell the user WHAT was\n"
                 "      opened and WHERE (the returned ``path`` +\n"
                 "      ``opened_with``).\n"
                 "    * Security: paths are sandbox-scoped to the\n"
                 "      write root; executables (.exe/.bat/...) are\n"
                 "      refused. Never try to bypass — the refusal\n"
                 "      is final.\n"
                 "\n"
                 " - Log anomaly analysis (v0.9.7+):\n"
                 "    * Tools: ``ingest_log`` / ``analyze_anomalies``\n"
                 "      / ``search_logs`` / ``get_log_stats`` /\n"
                 "      ``list_log_sources`` / ``remove_logs`` —\n"
                 "      semantic analysis of access logs\n"
                 "      (Apache/Nginx) and system logs (syslog) over\n"
                 "      Qdrant.\n"
                 "    * Trigger phrases — any of these means the user\n"
                 "      wants log analysis:\n"
                 "        - Chinese: '分析 access log', '系統日誌',\n"
                 "          '異常登入', '被掃描', '暴力破解', 'log 異常',\n"
                 "          '檢查日誌', '誰在攻擊'.\n"
                 "        - English: 'analyze the access log',\n"
                 "          'system log', 'log anomaly', 'brute force',\n"
                 "          'who is attacking', 'check the logs'.\n"
                 "    * Flow: call ``list_log_sources`` first. If the\n"
                 "      target file is missing, call ``ingest_log``\n"
                 "      (paths sandboxed to QDRANT_LOG_ALLOWED_ROOT).\n"
                 "      Compressed archives (.zip / .7z / .tar /\n"
                 "      .tar.gz / .tgz / .tar.bz2 / .gz) are accepted\n"
                 "      directly — members are extracted and indexed\n"
                 "      as separate sources '<archive>!<member>'.\n"
                 "      Then call ``analyze_anomalies`` and present\n"
                 "      the findings as a table with severity,\n"
                 "      evidence (IP / path / sample lines), and\n"
                 "      recommended actions.\n"
                 "    * Use ``search_logs`` for semantic questions\n"
                 "      like '找出所有 SQL injection 嘗試' or 'who tried\n"
                 "      to log in as root' — pass the source /\n"
                 "      log_type / level / min_status filters when the\n"
                 "      user narrows the scope.\n"
                 "    * ``remove_logs`` is DESTRUCTIVE — confirm with\n"
                 "      the user before calling it.\n"
                 "    * The tools return structured JSON. YOUR JOB is\n"
                 "      to interpret severity and summarise in the\n"
                 "      user's language with a table — do NOT dump\n"
                 "      raw JSON at the user.\n"
                 "\n"
                 " - File archive / compression (v0.9.8+):\n"
                 "    * Tools: ``archive_create`` / ``archive_extract``\n"
                 "      / ``archive_list`` / ``list_archive_formats``\n"
                 "      — compress and decompress files inside the\n"
                 "      filesystem sandboxes.\n"
                 "    * Supported formats: zip, 7z, tar, tar.gz,\n"
                 "      tar.bz2, gz (gz = ONE file only; use tar.gz\n"
                 "      to bundle several).\n"
                 "    * Trigger phrases — any of these means the user\n"
                 "      wants archive work:\n"
                 "        - Chinese: '壓縮', '打包', '解壓縮', '解壓',\n"
                 "          'zip 起來', '7z', 'tar', 'gzip',\n"
                 "          '打包成一個檔案'.\n"
                 "        - English: 'compress', 'archive these',\n"
                 "          'zip it up', 'unzip', 'extract the 7z',\n"
                 "          'tar.gz it', 'pack these files'.\n"
                 "    * Flow: call ``archive_list`` first when the user\n"
                 "      wants to see what is inside an archive; call\n"
                 "      ``archive_create`` with the input files (or a\n"
                 "      directory — it is walked recursively) and an\n"
                 "      ``out_path`` whose extension sets the format;\n"
                 "      call ``archive_extract`` to unpack. Report\n"
                 "      the returned out_path / target_dir and the\n"
                 "      skipped rows (dangerous executables are\n"
                 "      deliberately skipped — mention that).\n"
                 "    * Safety: everything is sandbox-scoped to the\n"
                 "      filesystem read/write roots; zip-bomb bounds\n"
                 "      (max bytes / member count) are enforced and\n"
                 "      over-budget members are reported, not raised.\n"
                 "\n"
                 " - Zip/Tar as a FILESYSTEM (v0.9.133.13+, zipfs-local):\n"
                 "    * Tools: ``zipfs_list`` / ``zipfs_read`` /\n"
                 "      ``zipfs_write`` / ``zipfs_delete`` /\n"
                 "      ``zipfs_rename`` / ``zipfs_extract_one`` /\n"
                 "      ``zipfs_info`` / ``zipfs_status``.\n"
                 "    * Trigger — the user uploaded a .zip/.tar and wants\n"
                 "      to WORK ON THE CONTENTS IN PLACE: '修改 zip 裡的\n"
                 "      檔案', '在壓縮檔內加一個檔案', 'edit the tar',\n"
                 "      'rename a file in the zip', '刪掉 archive 裡的\n"
                 "      目錄', 'zip 裡的檔案改一下'. (Plain compress or\n"
                 "      extract still belongs to the archive_* tools.)\n"
                 "    * Flow: ``zipfs_info`` (+ ``zipfs_list``) first to\n"
                 "      see what is inside → operate with ``zipfs_read``\n"
                 "      / ``zipfs_write`` (text or base64) /\n"
                 "      ``zipfs_delete`` / ``zipfs_rename`` → tell the\n"
                 "      user the SAME single archive file now contains\n"
                 "      the changes (they can download it as-is).\n"
                 "    * Safety: writes rebuild the archive ATOMICALLY\n"
                 "      (crash-safe copy-on-write); deletes/renames\n"
                 "      REQUIRE confirm=true; mutations need the archive\n"
                 "      INSIDE the write sandbox (uploads/ is inside —\n"
                 "      the read root is read-only); .exe/.bat/... member\n"
                 "      names are refused; budgets cap member count and\n"
                 "      total uncompressed size. 7z is READ-ONLY (list /\n"
                 "      read / extract_one; the mutating tools explain).\n"
                 " - Local core tools / sandboxed compute (v0.9.9+):\n"
                 "    * Tools: ``run_shell`` / ``run_bash`` /\n"
                 "      ``run_grep`` / ``run_awk`` / ``run_python`` —\n"
                 "      run real commands in the filesystem write\n"
                 "      sandbox so bulk work happens LOCALLY instead\n"
                 "      of inside your context window.\n"
                 "    * Trigger phrases — any of these means you\n"
                 "      should reach for these tools:\n"
                 "        - Chinese: '轉換格式', '轉成 json', '處理\n"
                 "          這個檔案', '搜尋關鍵字', '統計一下',\n"
                 "          '批次取代', '用 python 處理'.\n"
                 "        - English: 'convert this file', 'extract\n"
                 "          columns', 'grep for X', 'run a script',\n"
                 "          'transform csv to json', 'process the log'.\n"
                 "    * Tool choice:\n"
                 "        - ``run_grep`` — find lines/patterns in\n"
                 "          sandbox files (regex, -i, -v, -n, -c, -l,\n"
                 "          context). ALWAYS prefer this over reading\n"
                 "          a file to search it yourself.\n"
                 "        - ``run_awk`` — column/field surgery over\n"
                 "          one file (pass field_separator for CSV).\n"
                 "        - ``run_bash`` — full Unix pipelines\n"
                 "          (grep|awk|sed|sort|uniq) when several\n"
                 "          steps chain together.\n"
                 "        - ``run_python`` — anything else: json/csv\n"
                 "          transforms, regex extraction, computed\n"
                 "          reports. Write outputs into the sandbox\n"
                 "          and tell the user the output path.\n"
                 "          Charts are auto-CJK-safe (v0.9.34.0+):\n"
                 "          run_python prepends a matplotlib font\n"
                 "          bootstrap (first installed CJK font, e.g.\n"
                 "          Microsoft JhengHei, wins) so Chinese\n"
                 "          labels never render as boxes — do NOT\n"
                 "          set rcParams['font.sans-serif'] yourself.\n"
                 "          v0.9.36.1+: EVERY runner is CJK-safe via\n"
                 "          MATPLOTLIBRC (write_file + PowerShell too) —\n"
                 "          if a chart script still needs an explicit\n"
                 "          font, add plt.rcParams['font.sans-serif']\n"
                 "          =['Microsoft JhengHei'] at the top.\n"
                 "          KEEP run_python CODE SHORT: never paste\n"
                 "          large datasets inline — save the data as a\n"
                 "          sandbox file (write_file / the tool result\n"
                 "          already gives CSV) and read it in the code\n"
                 "          (open / pandas). Giant inline payloads fail\n"
                 "          on the Windows -c limit (v0.9.36.0 runs\n"
                 "          oversized code via a temp file, but inline\n"
                 "          blobs also blow the LLM tool-call itself).\n"
                 "        - ``run_shell`` — OS-native batch work\n"
                 "          (Windows cmd / POSIX sh).\n"
                 '    * Script library (v0.9.102.5+): a SUCCESSFUL '  
                 '      run_python / run_bash / run_shell block (200+ '  
                 '      chars) is AUTO-SAVED into the script library - '  
                 '      the tool result stamps auto_saved: {name, note}. '  
                 '      A RECURRING job should call script_run(name, '  
                 '      args?) INSTEAD of re-generating the same code '  
                 '      (big token + latency win, byte-stable replay). '  
                 '      script_list shows the library (name / lang / '  
                 '      chars / description / runs / backend); script_save '  
                 '      stores under a fixed kebab-case name; the files '  
                 '      live in the write-sandbox scripts/ dir (read_file '  
                 '      / patch_file can review or edit them). Isolation '  
                 '      (optional, per script): conda_env= runs via conda '  
                 '      run -n; docker_image= runs in a container mounting '  
                 '      the write sandbox only (network none unless '  
                 '      network=bridge). '
                 '    * Script FAILURE triage (v0.9.124.0+, Memento '
                 '      two-layer): a broken script is one of TWO '
                 '      things — (a) the IMPLEMENTATION is wrong (a '
                 '      syntax/API bug) → fix the script file itself '
                 '      (read_file + patch/write it); (b) the '
                 '      UNDERSTANDING of the environment was wrong '
                 '      (the approach cannot work) → do NOT just patch '
                 '      code: revise the rulebook '
                 '      (rules_revise/rules_add with this failure as '
                 '      evidence), THEN rewrite the approach. Diagnose '
                 '      which layer failed before each retry; a third '
                 '      identical failure means layer (b) was missed.'
                 "    * Working rules: files live in the sandbox —\n"
                 "      use RELATIVE paths; write outputs into the\n"
                 "      sandbox; report the output path. stdout is\n"
                 "      capped (64 KB) and every call has a timeout\n"
                 "      (default 30 s, cap 120 s) — chunk big jobs.\n"
                 "      Machine-level destructive commands (rm -rf /,\n"
                 "      format, shutdown...) are refused; sandbox file\n"
                 "      operations are fine. Use these tools to LOWER\n"
                 "      your own workload — do not cat whole files\n"
                 "      into context when a grep/awk/python one-liner\n"
                 "      can produce the answer.\n"
                 "\n"
                 " - Context offloading (v0.9.10.0+): when CHAT_CONTEXT_"
                 "COMPRESSION is enabled, a very large successful tool "
                 "result is saved to a file and the tool message shows "
                 "'[offloaded] ... saved to: <path>' plus a short "
                 "preview. If you need the full content, call the "
                 "filesystem ``read_file`` tool with that absolute "
                 "path (it lives inside the read sandbox). Do NOT "
                 "re-run the tool just to see what was truncated — "
                 "read the file.\n"
                 "\n"
                 " - Windows desktop automation (windows-mcp, v0.9.14.1+):\n"
                 "    * Tools: ``App`` (launch / switch windows),\n"
                 "      ``Snapshot`` (screenshot), ``Click`` / ``Move``\n"
                 "      (mouse, Move supports drag), ``Wait``,\n"
                 "      ``PowerShell`` (run shell commands).\n"
                 "    * Trigger phrases — '開啟 X', '打開 X', '用小畫家\n"
                 "      畫', '用滑鼠操作', 'open Paint', 'launch notepad',\n"
                 "      'take a screenshot' and similar desktop-automation\n"
                 "      requests.\n"
                 "    * App launch ladder (try in order, stop at the\n"
                 "      first success):\n"
                 "        1. ``App`` mode=launch name=<start-menu name>\n"
                 "           (e.g. mspaint, notepad, calc).\n"
                 "        2. If it reports 'not found in start menu' →\n"
                 "           ``App`` mode=launch_executable with the FULL\n"
                 "           absolute path INCLUDING the .exe extension —\n"
                 "           e.g. C:\\\\Windows\\\\System32\\\\mspaint.exe.\n"
                 "           NEVER omit the extension (the tool checks the\n"
                 "           exact path) and NEVER pass a relative path\n"
                 "           (relative paths resolve against the MCP\n"
                 "           server's own cwd, not yours).\n"
                 "        3. If that still fails → one ``PowerShell`` call\n"
                 "           with 'Start-Process mspaint' — this bypasses\n"
                 "           start-menu indexing entirely.\n"
                 "    * Drawing in Paint — PREFER scripted rendering over\n"
                 "      mouse automation: ONE ``PowerShell`` call using\n"
                 "      System.Drawing can render the whole picture to a\n"
                 "      PNG file and then open it in Paint (Start-Process\n"
                 "      mspaint <png-path>). Use ``Click`` / ``Move`` drag\n"
                 "      drawing ONLY when the user explicitly wants to\n"
                 "      watch it being drawn step by step ('用滑鼠畫',\n"
                 "      '一步一步畫給我看').\n"
                 "    * Iteration budget: every turn has a limited number\n"
                 "      of tool iterations (default 20, ``MAX_TOOL_ITERATIONS``\n"
                 "      env). Batch several tool_calls into ONE response\n"
                 "      when they are independent. When the budget is\n"
                 "      exhausted the loop forces a final answer — then\n"
                 "      report EXACTLY which steps completed and which\n"
                 "      step is next, and tell the user to say '繼續' so\n"
                 "      the next turn can resume. NEVER claim a step\n"
                 "      succeeded without the corresponding tool result,\n"
                 "      and NEVER present a manual how-to as if the\n"
                 "      automation itself had failed.\n"
                 "\n"
                 "Failure handling:\n"
                " - If a tool returns an error (e.g. backend "
                "unreachable), tell the user the specific error in "
                "one sentence and STOP retrying that tool. "
                "Do NOT loop on the same failing tool more than "
                "once — pivot to an alternative (e.g. web_search) "
                "or surface the error.\n"
                " - Once you have enough tool results, output a "
                "plain assistant message - do NOT keep calling "
                "tools unnecessarily. Cite filenames (not "
                "'[Document N]')."
            ) + rules_block + lang_directive
        rules = ""
        if web_enabled:
            rules = (
                "\n - When you reference information from a web "
                "hit, include the title and URL shown in that hit."
                "\n - Local-document citations take priority for "
                "the same fact; web results are for fresh / "
                "external context."
            )
        return (
            "You are a helpful assistant that answers questions "
            "based on the user's local documents, optionally "
            "augmented with Web Search results. "
            "Use the provided context to answer accurately. "
            "If the context doesn't contain enough information, "
            "say so clearly.\n\n"
            "CITATION RULES (mandatory):\n"
            " - When you reference information from a context "
            "chunk, you MUST include the full source filename "
            "shown after 'Source:' in that chunk.\n"
            " - Do NOT cite by the '[Document N]' label alone; "
            "always pair the document number with the filename.\n"
            " - If multiple chunks come from the same file, "
            "group the citations." + rules + rules_block
            + lang_directive
        )

    # ─── tool-loop helpers ────────────────────────────────────
    def _pin_narrow_prompt(
        self, response_language: str | None = None,
    ) -> str:
        """v0.9.133.11 — NARROW system prompt for turns pinned to the
        LOCAL llama.cpp server (``task_llm="llamacpp:<model>"``).

        WHY: the GENERIC tool-flavour prompt measured 62k chars
        ≈ 19,330 tokens (a 988-line accumulated trigger/policy
        manual) — bigger than the CPU server's whole 12,288 ctx
        BEFORE any user content, so every llamacpp-pinned turn 400s
        (exceed_context_size) regardless of how few tools are
        attached. A pinned batch job is ONE pre-planned task over
        the ATTACHED tools only — the prompt states exactly that
        and nothing else (memory recall block still prepends)."""
        p = (
            "You are this agent's scheduled-task runner running ONE "
            "pre-planned batch job. Complete the steps in the user "
            "message by calling the attached tools IN ORDER "
            "(typically summarize_sessions to compact recent chat "
            "sessions — call it with days=<1>; OMIT optional args "
            "unless the user message says otherwise). Rules:\n"
            " - Honestly report counts and failures; NEVER claim a "
            "step succeeded without its tool result.\n"
            " - Do not call tools the steps do not need.\n"
            " - Keep the final reply a short completion report "
            "(written / skipped / failed).\n"
        )
        if response_language:
            p += f"\nRespond in {response_language}.\n"
        return p

    def _messages_with_user(
        self,
        question: str,
        history: list[dict] | None,
        web_enabled: bool,
        tools_enabled: bool,
        response_language: str | None = None,
        use_memory: bool | None = None,
    ) -> list[dict]:
        msgs: list[dict] = [
            {
                "role": "system",
                "content":
                    # v0.9.133.11 — turns pinned to the LOCAL llama.cpp
                    # server run the narrow prompt (the generic manual
                    # alone is ~19.3k tokens > that server's 12,288 ctx).
                    (self._pin_narrow_prompt(response_language)
                     if (str(getattr(self, "_task_llm", "") or "")
                         .split(":", 1)[0].strip().lower()
                         == "llamacpp")
                     else self._system_prompt(
                        web_enabled=web_enabled,
                        tools_enabled=tools_enabled,
                        response_language=response_language,
                    ))
                    + (self._memory_block(
                        question,
                        enabled=True if use_memory is None
                        else bool(use_memory),
                    ) or ""),
            }
        ]
        # v0.9.10.0+ — opt-in context compression over the filtered
        # user/assistant history. Off → compress_history returns an
        # identical copy (byte-identical legacy behaviour); any
        # compressor failure degrades to the uncompressed history.
        clean = [
            m
            for m in (history or [])
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        try:
            compressed = self.compressor.compress_history(
                clean, summarizer=self._summarize_history
            )
        except Exception as e:
            logger.warning("[agent] context compression failed: %r", e)
            compressed = clean
        for m in compressed:
            msgs.append({"role": m["role"], "content": m["content"]})
        msgs.append({"role": "user", "content": question})
        return msgs

    def _summarize_history(self, transcript: str, max_chars: int):
        """LLM summarization of the older conversation tail.

        Returns the summary text, or None on ANY failure so
        ContextCompressor falls back to its deterministic extractive
        summarizer (no network needed). v0.9.17.0: routed through
        ``_create_validated`` so provider failover applies here too.
        """
        try:
            _ = self.client  # ensure provider/model are resolved
            resp, ok = self._create_validated(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize the earlier conversation between "
                            "the user and the assistant so a future turn "
                            "keeps full continuity. Preserve: decisions, "
                            "facts, tool names, numbers, and open "
                            "questions. VERBATIM-preserve every URL "
                            "(article links / x.com post links / file "
                            "paths) and the TITLE of any post or article "
                            "being fetched — never paraphrase or drop "
                            "them; the next turn re-uses the exact link "
                            "to fetch the right target. Drop "
                            "pleasantries and repetition. Respond with "
                            "the summary only — no preamble, no "
                            "commentary."
                        ),
                    },
                    {"role": "user", "content": transcript},
                ],
                temperature=0.2,
                max_tokens=600,
            )
            if not ok:
                return None
            out = (resp.choices[0].message.content or "").strip()
            return out or None
        except Exception as e:
            logger.info(
                "[agent] llm history summarization failed, falling "
                "back to extractive: %r",
                e,
            )
            return None

    def _dispatch_with_watchdog(self, name: str, args: dict) -> Any:
        """v0.9.107.0 — one MCP dispatch under a wall-clock watchdog
        (``QDRANT_TOOL_CALL_TIMEOUT_S``; 0 = off = legacy passthrough).
        A hung call returns an error ``McpCallResult`` the tool loop
        can react to; the runaway thread is abandoned (daemon)."""
        timeout_s = _tool_call_timeout_s()
        if timeout_s <= 0:
            return self.mcp_manager.call(name, args)
        box: dict[str, Any] = {}

        def _run() -> None:
            try:
                box["result"] = self.mcp_manager.call(name, args)
            except BaseException as e:  # noqa: BLE001 — re-raised below
                box["error"] = e

        worker = threading.Thread(
            target=_run, name=f"tool-watchdog-{name}", daemon=True
        )
        worker.start()
        worker.join(timeout_s)
        if worker.is_alive():
            logger.warning(
                "[agent] tool %s watchdog FIRED after %ss — runaway "
                "call abandoned, returning tool error", name, timeout_s,
            )
            return McpCallResult(
                text=(
                    f"[tool watchdog] '{name}' did not return within "
                    f"{timeout_s}s — abandoned. Retry with narrower "
                    f"arguments / a shorter exec timeout / another tool."
                ),
                is_error=True,
            )
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _run_tool_call(self, call: Any) -> dict[str, Any]:
        """Dispatch a single tool call through the manager."""
        name = getattr(call.function, "name", None) or ""
        args_raw = (
            getattr(call.function, "arguments", None) or "{}"
        )
        call_id = getattr(call, "id", None) or ""
        try:
            args = (
                json.loads(args_raw)
                if isinstance(args_raw, str)
                else (args_raw or {})
            )
        except Exception:
            args = {}
        result = self._dispatch_with_watchdog(name, args)
        # v0.9.57.1 — audit trail for the telegram photo-hallucination
        # guard: the guard must know whether THIS turn really dispatched
        # a snapshot / send_telegram_photo tool.
        try:
            self.last_tool_dispatches.append(name)
        except AttributeError:
            self.last_tool_dispatches = [name]
        # v0.9.94.0 — result grounding registry: every successful tool
        # result is stamped with a uuid and saved so the post-turn
        # grounding guard can verify the LLM's conclusion actually used
        # tool content (not memory-only inference).
        if not result.is_error and (result.text or "").strip():
            try:
                ref = uuid.uuid4().hex[:8]
                self._turn_result_refs[ref] = {
                    "tool": name,
                    "args": {k: str(v)[:80]
                             for k, v in (args or {}).items()},
                    "text": result.text[:1500],
                    "tokens": _distinctive_tokens(result.text),
                }
                logger.info("[ResultRef] %s <- %s", ref, name)
            except AttributeError:
                self._turn_result_refs = {}
        # v0.9.59.0 — long-term memory: whitelisted "external lookup"
        # tool results (web_search / web_fetch …) are stored into the
        # Qdrant memory collection (kind=query, with stored_at) so
        # future turns can recall + cite them. Fire-and-forget.
        if name in _memory_tools() and not result.is_error:
            try:
                from src.agent.memory_store import (
                    _shared_store,
                    memory_enabled,
                )
                if memory_enabled():
                    _shared_store().remember(
                        result.to_message_text(),
                        kind="query", source=name,
                    )
            except Exception as mem_exc:
                logger.debug(
                    "[agent] tool-result memory ingest failed: %r",
                    mem_exc,
                )
        logger.info(
            "[agent] tool %s(%s) -> %s%s",
            name,
            list(args.keys()),
            "OK" if not result.is_error else "ERR",
            f": {result.text[:80]!r}" if result.text else "",
        )
        text = result.to_message_text()
        # v0.9.16.2 — slim base64 blobs (describe_image /
        # resize_image ``raw_image_base64`` is multi-MB and is never
        # consumed by the text-only tool loop; it blew the context
        # window of every OpenRouter endpoint → 404 "No endpoints").
        text = _strip_base64_from_tool_text(text)
        # v0.9.76.3 — deterministic list_chats presentation: stash the
        # formatted summary so the tool-loop post-process can append it
        # when the LLM compresses the list into a one-liner.
        if name == "teams_assist_list_chats" and not result.is_error:
            try:
                d = json.loads(text)
                s = d.get("summary") if isinstance(d, dict) else None
                if isinstance(s, str) and s.strip():
                    self._last_list_chats_summary = s.strip()
            except Exception:
                pass
        # v0.9.102.7 — deterministic snapshot embed (the list_chats
        # .76.3 pattern): stash the path so the tool-loop return sites
        # can append it when the LLM's answer dropped it. The result
        # text is parsed BEFORE the offload (snapshots are small and
        # stay inline anyway).
        if name in _SNAPSHOT_TOOLS and not result.is_error:
            try:
                p = _snapshot_path_from_result(text)
                if p:
                    paths = getattr(self, "_snapshot_paths", None)
                    if paths is None:
                        self._snapshot_paths = [p]
                    elif p not in paths:
                        paths.append(p)
            except Exception:
                pass
        # v0.9.10.0+ — opt-in offloading of oversized SUCCESSFUL tool
        # results (errors stay inline — they are short and visible).
        if not result.is_error:
            try:
                text, _offloaded = self.compressor.offload_tool_result(
                    name, text
                )
            except Exception as e:
                logger.warning(
                    "[agent] tool-result offload failed (inline kept): %r",
                    e,
                )
                _offloaded = None
        # v0.9.104.3 — ABSOLUTE cap (defense in depth, independent of
        # the compressor): one tool result must NEVER be able to blow
        # the context, even when the compressor is off or the offload
        # write failed. The tool message is not offloaded because it
        # is a full page dump — the pointer tells the model what to do.
        try:
            _cap = _tool_result_cap()
        except Exception:
            _cap = _TOOL_RESULT_CAP_DEFAULT
        if _cap and len(text) > _cap:
            logger.warning(
                "[agent] tool %s result capped: %d chars > %d cap",
                name, len(text), _cap,
            )
            text = (
                text[:_cap]
                + f"\n…[tool result truncated: {len(text) - _cap} chars "
                f"over the {_cap}-char safety cap]…\n"
                "(If you need more of this result, re-call the tool "
                "with narrowing arguments or use the filesystem "
                "`read_file` tool on a saved copy — do NOT expect the "
                "full content here.)"
            )
        # v0.9.124.0 — rulebook prediction-error hint: a FAILED tool
        # call IS a prediction error (Memento observe→reflect→revise).
        # Once per turn, offer the rulebook revise path as the
        # systematic fix (fire-and-forget; never alters the error
        # semantics of the tool message itself).
        if result.is_error:
            if (not getattr(self, "_rulebook_hint_shown", False)
                    and rulebook_hint_enabled()):
                try:
                    from src.agent.rulebook import (
                        hint_on_failure as _hof,
                    )

                    _hint = _hof()
                    if _hint:
                        text = text + "\n" + _hint
                        self._rulebook_hint_shown = True
                except Exception:  # noqa: BLE001
                    pass
            # v0.9.127.0 — per-rule failure telemetry (Evo2): one failed
            # tool call = one prediction error OBSERVED while the current
            # rule revisions were in effect. Fire-and-forget bookkeeping
            # (never alters the tool message semantics).
            try:
                from src.agent import rulebook as _rb

                if _rb.rulebook_telemetry_enabled():
                    threading.Thread(
                        target=_rb.note_tool_error, daemon=True,
                        name="rulebook-telemetry",
                    ).start()
            except Exception:  # noqa: BLE001
                pass
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": text,
        }

    # ─── tool-surface gating (v0.9.13.0+) ─────────────────────
    def _toolroute_tool_names(
        self, server_names: list[str],
    ) -> list[str] | None:
        """v0.9.98.0 — convert the routed SERVER allow-list into a
        TOOL allow-list over the env-gated catalog (so the surface is
        still filtered by MCP_ACTIVE_SERVERS + the 90 cap), then
        enforce ``QDRANT_TOOLROUTE_CAP`` (default 128). ANY failure OR
        a routed list matching ZERO tools → None (fail-open to the
        legacy surface — a zero-tool surface would silently disable
        tool calling for the turn)."""
        try:
            mgr = self.mcp_manager
            # raw_tools = the un-capped catalog (v0.9.133.15); fake
            # managers in tests may only implement active_tools.
            tools = (
                mgr.raw_tools() if hasattr(mgr, "raw_tools")
                else mgr.active_tools()
            )
            wanted = set(server_names or [])
            orders = {nm: i for i, nm in enumerate(server_names or [])}
            # v0.9.133.15 — group names BY ROUTED-SERVER ORDER (stable
            # within a server) so the hard cap can never eat exactly
            # the tools the turn asked for — they come FIRST.
            ordered_tools = sorted(
                (t for t in tools if t.server_name in wanted),
                key=lambda t: orders.get(t.server_name, len(orders)),
            )
            names = [t.name for t in ordered_tools]
            if not names:
                logger.warning(
                    "[ToolRoute] routed servers %s matched 0 tools — "
                    "falling back to the legacy surface", sorted(wanted),
                )
                return None
            cap = _toolroute_hard_cap()
            if cap > 0 and len(names) > cap:
                names = names[:cap]
            return names
        except Exception as e:  # noqa: BLE001
            logger.warning("[ToolRoute] tool-name resolution failed: %r", e)
            return None

    def _chat_tools(self, tool_names=None) -> list[dict[str, Any]]:
        """The OpenAI tool surface for THIS request, honouring
        the ``MCP_ACTIVE_SERVERS`` gate.

        Every ``chat.completions.create`` carries the FULL tool
        schema list — measured at ~32k prompt tokens for the
        100-tool surface (v0.9.11.1), on every single turn.
        v0.9.13.2+: the gate logic itself lives in
        ``McpManager.active_tools()`` — the single source of
        truth shared with MetaLocalHost's catalog so the LLM
        never discovers tools it cannot call (a fake text call
        was observed live when the catalog disagreed with the
        declared surface).
        v0.9.14.0+: ``tool_names`` — an explicit per-TOOL
        allow-list from the GUI chat tool picker; overrides the
        env gate for this request (None = env gate applies).
        v0.9.133.11: a task pin on the LOCAL llama.cpp server
        (``task_llm="llamacpp:<model>"``) further INTERSECTS the
        surface with ``QDRANT_LLAMACPP_PIN_SERVERS`` (default
        memory-local,datetime-local,meta-local) — the 500-tool
        schema measured 27,174 tokens > the 12,288-ctx CPU server
        → BadRequestError 400 on the 15:12 fire; a pinned local
        model needs a small surface."""
        specs = self.mcp_manager.active_specs(tool_names)
        pin = str(getattr(self, "_task_llm", "") or "").strip()
        if pin.split(":", 1)[0].strip().lower() == "llamacpp":
            env = (os.environ.get("QDRANT_LLAMACPP_PIN_SERVERS", "")
                   or "memory-local,datetime-local,meta-local")
            allowed = {s.strip() for s in env.split(",") if s.strip()}
            before = len(specs)
            specs = [s for s in specs
                     if s.tool.server_name in allowed]
            logger.info(
                "[agent] llamacpp pin surface: %d -> %d tools "
                "(QDRANT_LLAMACPP_PIN_SERVERS)", before, len(specs))
        return [
            s.to_openai()
            for s in specs
        ]

    # ─── per-turn token usage (v0.9.12.0+) ────────────────────
    _LIST_TABLE_KEYS = ("✅ 符合", "❌ 不符合", "👁", "聊天清單（")
    # v0.9.79.1/82.0 — tools the agent may run deterministically when
    # the model merely ECHOES the call as text. READ-ONLY / zero-risk
    # across the fleet. WRITE tools that type into Teams, send
    # messages, place orders or touch the filesystem are deliberately
    # EXCLUDED — a hallucinated write must never auto-run.
    _ECHO_SAFE_TOOLS = frozenset({
        # teams-assist (v0.9.79.1)
        "teams_assist_config", "teams_assist_start_watch",
        "teams_assist_list_chats", "teams_assist_drafts",
        "teams_assist_status",
        # time / meta (v0.9.82.0 — glm-flash echoed get_current_time)
        "get_current_time", "list_mcp_tools",
        # chat history / memory (read-only)
        "list_chat_sessions", "search_chat_history", "get_chat_session",
        "list_recent_memories",
        # qdrant / documents (read-only)
        "list_documents", "get_document_stats",
        # scheduler / webhook / tasks (read-only)
        "list_scheduled_tasks", "get_scheduled_task_status",
        "list_scheduled_task_runs", "list_webhook_triggers",
        "get_webhook_trigger", "list_webhook_trigger_runs",
        "list_tasks", "get_task_result",
        # misc read-only
        "list_allowed_chats", "list_openable_types",
        "list_archive_formats", "get_runtime_status",
        "list_log_sources", "get_log_stats", "local_interfaces",
        "list_credentials",
        # google-calendar READ-ONLY (v0.9.82.0 — schedule questions
        # must be answered from real fetches, never from memory)
        "list-calendars", "list-events", "search-events", "get-event",
        "get-freebusy", "list-colors",
        # web (v0.9.83.2 — live E2E: the model ECHOED
        # `web_search({"query": "中華電信董事長…"})` as text with ZERO
        # dispatch and answered from a WEEK-OLD memory (2026-09-15) —
        # current-facts questions must really run; both are read-only
        # external lookups (Ollama/OpenRouter search, best-effort GET)
        "web_search", "web_fetch",
        # v0.9.83.3 — read-only corpus/memory search (live E2E 17:02:
        # `search_documentsq中華電信 董事長` echoed GLUED, no parens)
        "search_documents", "search_memory",
    })
    # v0.9.83.3 — safe tools whose echo may lack args entirely: the
    # agent derives the query from the tool-name tail or the turn
    # question instead of skipping.
    _ECHO_QUERY_TOOLS = frozenset({
        "web_search", "web_fetch", "search_documents", "search_memory",
        "search-events",
    })
    _ECHO_RESULT_LIMIT = 400

    @staticmethod
    def _parse_echo_args(raw: str) -> dict[str, Any]:
        """Tolerant parser for echoed tool calls — tries JSON first
        (glm-flash echoes ``get_current_time({"format": ...})``), then
        the key=value shape (``allow_chats=["*"], watch_mode="uia"``)."""
        import ast
        import json

        raw = (raw or "").strip()
        if not raw:
            return {}
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        args: dict[str, Any] = {}
        for m in re.finditer(
            r"(\w+)\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|'[^']*'|[\w.\-]+)",
            raw,
        ):
            key, val = m.group(1), m.group(2)
            try:
                args[key] = ast.literal_eval(val)
            except (ValueError, SyntaxError):
                args[key] = val.strip("\"'")
        return args

    def _ensure_list_chats_summary(self, content: str) -> str:
        """v0.9.76.3/79.0/79.1 — deterministic guard for fake-calls
        (v0.9.59.2 lesson: prompt-taught rules are not enough for some
        models). Three layers:
        1. the turn dispatched list_chats and the model compressed the
           answer → append the stashed summary;
        2. the model ECHOED teams_assist_list_chats as text → run the
           tool ourselves and append the summary;
        3. the model ECHOED other SAFE teams tools (config /
           start_watch / drafts / status) → run them with the args
           parsed from the echo and append an audit note. Write tools
           that type into Teams (reply / send_uia) are NEVER auto-run.
        """
        content = content or ""
        summary = getattr(self, "_last_list_chats_summary", None)
        if not summary and "teams_assist_list_chats" in content:
            try:
                r = self.mcp_manager.call(
                    "teams_assist_list_chats", {},
                )
                if not getattr(r, "is_error", True):
                    d = json.loads(r.to_message_text())
                    s = d.get("summary") if isinstance(d, dict) else None
                    if isinstance(s, str) and s.strip():
                        summary = s.strip()
                        logger.info(
                            "[agent] list_chats was ECHOED not "
                            "dispatched — ran the tool deterministically"
                        )
            except Exception as e:
                logger.debug(
                    "[agent] deterministic list_chats call failed: %r", e,
                )
        notes: list[str] = []
        try:
            dispatched = set(
                str(d) for d in (self.last_tool_dispatches or [])
            )
        except Exception:
            dispatched = set()
        # v0.9.82.0 — generic echo pattern: ANY tool name in the safe
        # set called as text (args parsed tolerantly) and not actually
        # dispatched this turn → run deterministically. [\w\-]+ covers
        # hyphenated remote tool names (search-events, list-events…).
        for m in re.finditer(r"([\w\-]+)\s*\(([^)]*)\)", content):
            tool = m.group(1)
            if tool not in self._ECHO_SAFE_TOOLS or tool in dispatched:
                continue
            try:
                args = self._parse_echo_args(m.group(2))
                r = self.mcp_manager.call(tool, args)
                ok = not getattr(r, "is_error", True)
                notes.append(
                    f"（系統代為執行 {tool}{args if args else ''} → "
                    f"{'OK' if ok else '失敗'}）"
                )
                if ok and r.text:
                    # the user asked a QUESTION — surface the result
                    notes.append(str(r.text)[:self._ECHO_RESULT_LIMIT])
                logger.info(
                    "[agent] %s was ECHOED not dispatched — ran "
                    "deterministically (ok=%s)", tool, ok,
                )
            except Exception as e:
                notes.append(
                    f"（系統代為執行 {tool} 失敗：{type(e).__name__}）"
                )
        # v0.9.83.3 — bare/glued echo pass. Live shapes (2026-09-21):
        #   `search_documentsq中華電信 董事長`  (tool+arg-key GLUED, no parens)
        #   `get_current_time`                (bare tool name, ↓7 tok)
        # The parens regex above cannot see these. A tool whose name is
        # present glued to 1-8 latin arg-key chars, or standing ALONE on
        # a line, is run deterministically; query tools derive the query
        # from the name tail (minus the latin arg-key prefix) with the
        # turn question as fallback. Prose mentions (space/word after
        # the name) never trigger.
        handled = {m.group(1) for m in re.finditer(
            r"([\w\-]+)\s*\(([^)]*)\)", content)}
        question = (getattr(self, "_last_turn_question", "") or "").strip()
        for tool in self._ECHO_SAFE_TOOLS:
            if tool in dispatched or tool in handled:
                continue
            if tool == "teams_assist_list_chats":
                continue  # the dedicated block above already ran it
            m = re.search(re.escape(tool) + r"(.{0,160})", content)
            if not m:
                continue
            tail = m.group(1)
            glued = re.match(r"[A-Za-z_]{1,8}", tail) is not None
            bare = re.search(
                r"(?m)^\s*" + re.escape(tool) + r"\s*$", content,
            ) is not None
            if not glued and not bare:
                continue  # prose mention — never auto-run
            args2: dict[str, Any] = {}
            if tool in self._ECHO_QUERY_TOOLS:
                q = tail.strip()
                if tool == "web_fetch":
                    # the key is "url" — strip just it; a greedy latin
                    # strip would eat the "https" of the URL value
                    if re.match(r"^url", q, re.I):
                        q = q[3:].lstrip("=: ")
                    else:
                        q = ""
                elif glued:
                    # "max_results5query台北…" → "台北…" — the whole
                    # glued latin/digit key-plumbing run is plumbing
                    q = re.sub(r"^[A-Za-z_0-9]+", "", q)
                else:
                    q = ""
                q = q.strip().strip("。，、；！？!?：:")
                q = q[:200] if tool == "web_fetch" else q[:80]
                if len(q) < 2 and tool != "web_fetch":
                    q = question
                if not q:
                    notes.append(f"（系統代為執行 {tool} 失敗：無參數）")
                    continue
                args2 = {"url": q} if tool == "web_fetch" else {"query": q}
            try:
                r = self.mcp_manager.call(tool, args2)
                ok = not getattr(r, "is_error", True)
                notes.append(
                    f"（系統代為執行 {tool}{args2 if args2 else ''} → "
                    f"{'OK' if ok else '失敗'}）"
                )
                if ok and r.text:
                    notes.append(str(r.text)[:self._ECHO_RESULT_LIMIT])
                logger.info(
                    "[agent] %s was ECHOED (bare/glued) not dispatched"
                    " — ran deterministically (ok=%s)", tool, ok,
                )
            except Exception as e:
                notes.append(
                    f"（系統代為執行 {tool} 失敗：{type(e).__name__}）"
                )
        if not summary:
            return content + ("\n\n" + "\n".join(notes) if notes else "")
        if any(k in content for k in self._LIST_TABLE_KEYS):
            return content + ("\n\n" + "\n".join(notes) if notes else "")
        logger.info(
            "[agent] list_chats answer compressed by the model — "
            "appending the deterministic summary table"
        )
        tail = summary
        if notes:
            tail += "\n" + "\n".join(notes)
        return f"{content}\n\n{tail}"

    def _ensure_snapshot_embed(self, content: str) -> str:
        """v0.9.102.7 — deterministic camera-snapshot embed.

        The flash models intermittently describe the snapshot in prose
        and DROP the path the onvif_get_snapshot tool returned (live
        2026-09-29 「get 五股大門畫面」) — the chat UI's
        renderSandboxImages() can only preview a path present in the
        bubble text, so the user saw NO image. Mirrors the v0.9.76.3
        list-chats pattern: _run_tool_call stashes each snapshot's
        saved path this turn; this post-process appends a previewable
        path line for every stashed path the answer does not already
        carry (the chat front-end converts it to an inline preview).
        Any failure keeps the original answer — never raises."""
        content = content or ""
        paths = list(getattr(self, "_snapshot_paths", None) or [])
        if not paths:
            return content
        missing = [p for p in paths if p and p not in content]
        if not missing:
            return content
        lines = [f"📷 快照畫面：{p}" for p in missing]
        logger.info(
            "[agent] snapshot path missing from the answer — "
            "embedding %d path line(s)",
            len(missing),
        )
        return content + "\n\n" + "\n".join(lines)

    def _extract_usage(self, resp) -> dict[str, int] | None:
        """Pull token counts off a ``chat.completions`` response.
        Returns ``None`` when the provider omitted ``usage``."""
        usage = getattr(resp, "usage", None)
        if usage is None:
            return None
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None)
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(prompt_details, "cached_tokens", None)
        return {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(
                getattr(usage, "completion_tokens", 0) or 0
            ),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "reasoning_tokens": int(reasoning or 0),
            "cached_tokens": int(cached or 0),
            "calls": 1,
        }

    def _accumulate_usage(
        self,
        turn: dict[str, int] | None,
        resp,
    ) -> dict[str, int] | None:
        """Sum one response's usage into the per-turn accumulator —
        a tool-loop turn fires SEVERAL ``create`` calls and the pair
        should show the total cost, not just the last call."""
        u = self._extract_usage(resp)
        if u is None:
            return turn
        if turn is None:
            return u
        for k in (
            "prompt_tokens", "completion_tokens",
            "total_tokens", "reasoning_tokens", "cached_tokens",
            "calls",
        ):
            turn[k] = turn.get(k, 0) + u[k]
        return turn

    def _create_validated(self, **kwargs: Any) -> tuple[Any, bool]:
        """``chat.completions.create`` with per-provider retry +
        CROSS-PROVIDER FAILOVER (v0.9.17.0 — the describe_image-style
        backup mechanism applied to the chat LLM itself).

        Attempt sequence:

        1. the PRIMARY provider — up to 2 attempts (covers HTTP 200
           with ``choices=None`` / ``[]`` and transient API errors);
        2. every OTHER configured provider, walked in
           ``_PROVIDER_ORDER`` priority — 1 attempt each. The
           OpenRouter-only ``extra_body`` routing object is stripped
           for non-openrouter fallbacks (those endpoints reject
           unknown top-level request fields).

        Returns ``(resp, True)`` on the first validated success.
        When every attempt failed WITHOUT raising (choices=None),
        returns ``(None, False)``. When the last failure was an
        exception, re-raises it (callers degrade gracefully).
        ``self.last_provider`` records who answered.
        """
        last_exc: Exception | None = None
        saw_invalid_resp = False

        # ── 1. primary, up to 2 attempts ──
        for attempt in (1, 2):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                if _valid_completion(resp):
                    self.last_provider = getattr(self, "_provider", None)
                    return resp, True
                saw_invalid_resp = True
                logger.warning(
                    "[agent] create attempt %d returned no choices "
                    "(model=%s)", attempt, self._model,
                )
            except APIStatusError as e:
                last_exc = e
                logger.warning(
                    "[agent] create attempt %d failed: %s: %s",
                    attempt, type(e).__name__, str(e)[:200],
                )
                # v0.9.96.1 — deterministic HTTP statuses (auth /
                # credit / permission) never recover on retry with
                # the SAME payload: skip attempt 2 and fail over
                # immediately (the 2026-09-23 log showed every 402
                # turn paying 2 wasted attempts before failover).
                if getattr(e, "status_code", None) in (401, 402, 403):
                    logger.warning(
                        "[agent] status %s is non-retryable "
                        "(auth/credit/permission) — failing over now",
                        getattr(e, "status_code", None),
                    )
                    break
            except Exception as e:  # noqa: BLE001 — retry ANY api error
                last_exc = e
                logger.warning(
                    "[agent] create attempt %d failed: %s: %s",
                    attempt, type(e).__name__, str(e)[:200],
                )
            if attempt == 1:
                time.sleep(_EMPTY_RESPONSE_RETRY_DELAY)

        # ── 2. cross-provider failover ──
        for name, model, client in self._failover_candidates():
            if client is None:
                continue
            kw = dict(kwargs)
            kw["model"] = model
            if name != "openrouter":
                # OpenRouter-only routing extras (provider routing /
                # reasoning effort) would be rejected or ignored by
                # other OpenAI-compatible endpoints.
                kw.pop("extra_body", None)
            logger.warning(
                "[agent] failover: primary failed — trying %s "
                "(model=%s)", name, model,
            )
            try:
                resp = client.chat.completions.create(**kw)
                if _valid_completion(resp):
                    self.last_provider = name
                    logger.info(
                        "[agent] failover: %s answered (model=%s)",
                        name, model,
                    )
                    return resp, True
                saw_invalid_resp = True
            except Exception as e:  # noqa: BLE001
                last_exc = e
                logger.warning(
                    "[agent] failover %s failed: %s: %s",
                    name, type(e).__name__, str(e)[:200],
                )

        if last_exc is not None:
            raise last_exc
        if saw_invalid_resp:
            return None, False
        return None, False

    def set_stop_check(
        self, cb: "Callable[[], bool] | None",
    ) -> None:
        """v0.9.120.0 — install/clear the cooperative force-stop
        callback (checked at every tool-loop iteration boundary;
        even zombie turns abandoned by the timeout guards share the
        same loop and honour it)."""
        self._stop_check = cb

    # ─── v0.9.123.0 — per-thread turn event stream + scoped stop ──
    # The GUI/web two-phase chat (Api.start_turn → Api.poll_turn) taps
    # the calling thread via a threading.local, so concurrent callers
    # (Telegram poll / scheduler fires / webhook fires / task runner)
    # each stream — or stream nothing — completely independently.
    _TURN_EV = threading.local()
    _TURN_STOPS: dict = {}
    _TURN_STOPS_LOCK = threading.Lock()
    _TURN_STREAM_MAX = 8

    def begin_turn_stream(self, turn_id, cb):
        """Attach (turn_id, event-callback, scoped stop Event) to the
        CALLING thread for the duration of one chat turn. Returns the
        stop Event that ``Api.stop_turn`` sets from another thread via
        ``signal_turn_stop``. Never raises."""
        ev = threading.Event()
        try:
            with self._TURN_STOPS_LOCK:
                if len(self._TURN_STOPS) >= self._TURN_STREAM_MAX:
                    self._TURN_STOPS.pop(next(iter(self._TURN_STOPS)),
                                         None)
                self._TURN_STOPS[str(turn_id)] = ev
            self._TURN_EV.tid = str(turn_id)
            self._TURN_EV.stop_event = ev
            self._TURN_EV.cb = cb
        except Exception:  # noqa: BLE001 — streaming never wedges a turn
            pass
        return ev

    def end_turn_stream(self, turn_id) -> None:
        """Detach the stream for this thread (best-effort)."""
        try:
            with self._TURN_STOPS_LOCK:
                self._TURN_STOPS.pop(str(turn_id), None)
            self._TURN_EV.tid = None
            self._TURN_EV.stop_event = None
            self._TURN_EV.cb = None
        except Exception:  # noqa: BLE001
            pass

    def signal_turn_stop(self, turn_id) -> bool:
        """Set the turn-scoped stop Event registered by
        ``begin_turn_stream`` (callable from ANY thread). Returns False
        when the id is unknown / the turn is not streamed."""
        try:
            with self._TURN_STOPS_LOCK:
                ev = self._TURN_STOPS.get(str(turn_id))
            if ev is None:
                return False
            ev.set()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _emit_turn_event(self, kind: str, text: str) -> None:
        """Best-effort progress emission — fires ONLY when the calling
        thread has a turn stream attached; zero cost otherwise."""
        try:
            cb = getattr(self._TURN_EV, "cb", None)
            if cb is not None:
                cb(kind, text)
        except Exception:  # noqa: BLE001
            pass

    def _tool_loop(
        self,
        messages: list[dict],
        tools: list[dict[str, Any]],
        temperature: float,
        max_iterations: int,
    ) -> str:
        """Run the OpenAI tool-calling loop.

        1. Call ``chat.completions.create(messages, tools=...)``.
        2. If response has ``tool_calls``, execute each one,
           append the assistant message + the tool messages,
           and repeat.
        3. If ``finish_reason`` is ``stop`` (or no tool calls),
           return the assistant content.

        Bail-out: ``max_iterations`` reached -> one final plain
        call to force an answer.

        v0.9.12.0+: token usage from EVERY ``create`` call in the
        loop is accumulated into ``self.last_usage`` so the GUI can
        show the pair's total cost.
        """
        turn_usage: dict[str, int] | None = None
        import time as _t_mod  # local — ms timing for the event stream
        saw_tool_round = False
        # v0.9.120.0 — cooperative force-stop bookkeeping.
        self.last_tool_stopped = False
        _stop_cb = getattr(self, "_stop_check", None)
        # v0.9.85.0 — Jev abandon gate state: consecutive empty/error
        # tool results + a once-per-turn gate decision.
        fail_streak = 0
        abandon_asked = False
        for iteration in range(max_iterations):
            # v0.9.120.0 — COOPERATIVE FORCE-STOP: checked at every
            # iteration boundary — BEFORE the next LLM call — so it
            # ends runaway turns AND zombie threads abandoned by the
            # earlier hard-timeout guards (same loop, same boundary).
            _stopped = False
            if _stop_cb:
                try:
                    if _stop_cb():
                        _stopped = True
                except Exception as e:  # noqa: BLE001 — a broken
                    # stop callback must never wedge the loop
                    logger.debug("[agent] stop_check failed: %r", e)
            # v0.9.123.0 — the GUI/web turn-scoped stop Event (set via
            # Api.stop_turn → signal_turn_stop). Coexists with the
            # task-registry stop callback above; only affects the turn
            # running on THIS thread.
            _ts_ev = getattr(self._TURN_EV, "stop_event", None)
            if _ts_ev is not None and not _stopped and _ts_ev.is_set():
                _stopped = True
            if _stopped:
                self.last_tool_stopped = True
                self._emit_turn_event(
                    "stop",
                    "🛑 偵測到中止請求 — 本回合在此結束"
                    "（已完成的工作保留）",
                )
                logger.warning(
                    "[agent] stop requested — turn ends here "
                    "(iteration %d/%d; completed tool work is "
                    "kept)", iteration, max_iterations,
                )
                self.last_usage = turn_usage
                return TURN_STOPPED_TEXT
            # v0.9.123.0 — live turn-event stream (GUI/web two-phase
            # chat): every LLM round is emitted when a stream is
            # attached to this thread; zero cost otherwise.
            self._emit_turn_event(
                "llm_round", f"LLM 第 {iteration + 1} 輪生成中…")
            # Force the lazy OpenAI client + model to be picked
            # up-front so ``self._model`` is populated before we
            # start firing requests.
            _ = self.client
            kwargs: dict[str, Any] = dict(
                model=self._model,
                messages=messages,
                temperature=temperature,
            )
            if tools:
                kwargs["tools"] = tools
            # v0.9.9.2+: OpenRouter provider routing (no-op {} for
            # other providers / unset routing).
            kwargs.update(self._routing_create_kwargs())
            # v0.9.16.1/2: retry once; empty choices → graceful text,
            # hard API errors (after retry) → graceful text too.
            try:
                resp, ok = self._create_validated(**kwargs)
            except Exception as e:
                logger.warning(
                    "[agent] create failed after retry (model=%s): %s: %s",
                    self._model, type(e).__name__, str(e)[:200],
                )
                self.last_usage = turn_usage
                return (
                    f"(LLM 呼叫失敗,已自動重試一次仍失敗:{type(e).__name__}: "
                    f"{str(e)[:300]} — 請稍後再試。/ LLM call failed after "
                    f"one retry — please try again.)"
                )
            turn_usage = self._accumulate_usage(turn_usage, resp)
            if not ok:
                logger.warning(
                    "[agent] provider returned no choices (model=%s) — "
                    "returning graceful empty-response text",
                    self._model,
                )
                self.last_usage = turn_usage
                return EMPTY_RESPONSE_TEXT
            msg = resp.choices[0].message
            finish = resp.choices[0].finish_reason
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls or finish == "stop":
                content = (msg.content or "").strip()
                # v0.9.23.0: a reasoning model can emit content=""
                # with finish_reason="stop" right after a tool round —
                # surfacing that as answer=0 chars (the 14:55 schedule
                # fire did exactly that). When at least one tool round
                # already ran, do ONE no-tools wrap-up call that asks
                # the model to output the final answer from the tool
                # results already in `messages`.
                if content or not saw_tool_round:
                    self.last_usage = turn_usage
                    return self._ensure_snapshot_embed(
                        self._ensure_list_chats_summary(content)
                    )
                wrap_msgs = list(messages) + [{
                    "role": "user",
                    "content": (
                        "請根據以上工具呼叫的結果，直接輸出最終答案"
                        "（不要再呼叫任何工具）。/ Based on the tool "
                        "results above, output your FINAL answer now "
                        "— do NOT call any more tools."
                    ),
                }]
                self._emit_turn_event(
                    "note",
                    "工具結果後無最終答案 — 進行 wrap-up 總結…")
                try:
                    wrap_resp, wrap_ok = self._create_validated(
                        model=self._model, messages=wrap_msgs,
                        temperature=temperature,
                        **self._routing_create_kwargs(),
                    )
                    turn_usage = self._accumulate_usage(
                        turn_usage, wrap_resp,
                    )
                    if wrap_ok:
                        c2 = (
                            wrap_resp.choices[0].message.content or ""
                        ).strip()
                        if c2:
                            logger.info(
                                "[agent] empty final answer after tool "
                                "rounds — wrap-up summary produced "
                                "(%d chars)", len(c2),
                            )
                            self.last_usage = turn_usage
                            return self._ensure_snapshot_embed(
                                self._ensure_list_chats_summary(c2)
                            )
                except Exception as e:
                    logger.warning(
                        "[agent] wrap-up call failed: %s: %s",
                        type(e).__name__, str(e)[:200],
                    )
                self.last_usage = turn_usage
                return self._ensure_snapshot_embed(
                    self._ensure_list_chats_summary(content)
                )

            try:
                tool_calls_payload = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ]
            except Exception:
                tool_calls_payload = None
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": tool_calls_payload,
                }
            )
            for tc in tool_calls:
                _tc_name = str(getattr(getattr(tc, "function", None),
                                       "name", "") or "?")
                _tc_args_full = str(getattr(getattr(tc, "function", None),
                                            "arguments", "") or "")
                self._emit_turn_event(
                    "tool_call",
                    f"🔧 {_tc_name}({_tc_args_full[:120]})"
                    + ("…" if len(_tc_args_full) > 120 else ""),
                )
                _t0 = _t_mod.monotonic()
                tool_msg = self._run_tool_call(tc)
                messages.append(tool_msg)
                _ms = int((_t_mod.monotonic() - _t0) * 1000)
                _res_txt = str(tool_msg.get("content") or "")
                _res_kind = ("err"
                             if _tool_result_failed(_res_txt) else "ok")
                self._emit_turn_event(
                    "tool_result",
                    f"{_res_kind} {_tc_name} · {_ms}ms · "
                    f"{len(_res_txt)} chars",
                )
                # v0.9.85.0 — Jev abandon gate: track consecutive
                # empty/error tool results; at the streak threshold ONE
                # Jev decision may inject a stop-exploring note.
                if _tool_result_failed(str(tool_msg.get("content") or "")):
                    fail_streak += 1
                else:
                    fail_streak = 0
                amode = _jev_abandon_mode()
                if (fail_streak >= _jev_abandon_streak()
                        and not abandon_asked and amode != "off"):
                    abandon_asked = True
                    names = [str(getattr(t.function, "name", "") or "")
                             for t in tool_calls]
                    p, meta = _jev_abandon_check(
                        fail_streak, names, iteration,
                    )
                    if p is None:
                        logger.warning(
                            "[JevAbandon] streak=%d jev FAILED — "
                            "fail-open (note not injected)",
                            fail_streak,
                        )
                    else:
                        logger.info(
                            "[JevAbandon] streak=%d p=%.2f mode=%s",
                            fail_streak, p, amode,
                        )
                        if amode == "on" and p >= _jev_abandon_prob():
                            messages.append({
                                "role": "system",
                                "content": (
                                    f"[工具迴圈提醒] 工具呼叫已連續 "
                                    f"{fail_streak} 次取得空結果或錯誤"
                                    f"（多為鏡像站失效）。請立即改用已"
                                    f"取得的資訊輸出最終答案，不要再嘗試"
                                    f"更多鏡像站/變體；確有遺漏時最多再"
                                    f"試一次，並在答案中如實說明未能"
                                    f"取得的部分。"
                                ),
                            })
                            logger.info(
                                "[JevAbandon] stop-exploring note "
                                "injected (streak=%d, p=%.2f)",
                                fail_streak, p,
                            )
            # v0.9.23.0: at least one tool round completed — an empty
            # FINAL answer after this point triggers the wrap-up call.
            saw_tool_round = True

        self._emit_turn_event(
            "note",
            f"工具迭代預算用罄（{max_iterations} 輪）— 要求直接輸出"
            "最終答案…")
        logger.warning(
            "[agent] tool loop hit max_iterations=%d; "
            "returning last state",
            max_iterations,
        )
        try:
            resp = self.client.chat.completions.create(
                model=self._model,
                messages=messages + [
                    {
                        "role": "user",
                        "content": (
                            "You have used up the tool-iteration budget "
                            "for this turn. Provide your final answer NOW "
                            "without calling any more tools. If the task "
                            "is unfinished, state exactly which steps "
                            "completed, which step comes next, and invite "
                            "the user to say '繼續' to resume next turn. "
                            "Do NOT claim unfinished work is done."
                        ),
                    }
                ],
                temperature=temperature,
                **self._routing_create_kwargs(),
            )
            turn_usage = self._accumulate_usage(turn_usage, resp)
            self.last_usage = turn_usage
            return self._ensure_snapshot_embed(
                self._ensure_list_chats_summary(
                    (resp.choices[0].message.content or "").strip()
                )
            )
        except Exception as e:
            self.last_usage = turn_usage
            return (
                f"(tool loop terminated after {max_iterations} "
                f"steps; final LLM call failed: {e!r})"
            )

    # ─── public API ───────────────────────────────────────────
    def ask(
        self,
        question: str,
        top_k: int = 5,
        temperature: float = 0.3,
        use_web_search: bool = False,
        web_max_results: int = DEFAULT_WEB_MAX_RESULTS,
        use_tools: bool = True,
        max_tool_iterations: int | None = None,
        response_language: str | None = None,
        active_tool_names: list[str] | None = None,
    ) -> str:
        """Single-turn RAG ask (used by ``cli.py ask``).

        ``response_language`` (v0.8.5+) is forwarded to the system
        prompt so the LLM answers in the user's UI locale. None
        (the default) preserves the v0.8.4 behaviour with no
        language directive.

        ``active_tool_names`` (v0.9.14.0+) — explicit per-TOOL
        allow-list from the GUI chat tool picker; overrides the
        ``MCP_ACTIVE_SERVERS`` env gate for this request (None =
        env gate applies).

        v0.9.45.0 (parallel multi-task Phase 1): the turn runs inside
        the process-wide ``turn_slot()`` gate
        (``QDRANT_MAX_CONCURRENT_TURNS``; unset = unlimited = legacy
        behaviour). Nested same-thread turns reuse the slot, so a
        mid-turn ``run_scheduled_task_now`` cannot deadlock.
        """
        with turn_slot():
            return self._ask_impl(
                question=question,
                top_k=top_k,
                temperature=temperature,
                use_web_search=use_web_search,
                web_max_results=web_max_results,
                use_tools=use_tools,
                max_tool_iterations=max_tool_iterations,
                response_language=response_language,
                active_tool_names=active_tool_names,
            )

    def _ask_impl(
        self,
        question: str,
        top_k: int = 5,
        temperature: float = 0.3,
        use_web_search: bool = False,
        web_max_results: int = DEFAULT_WEB_MAX_RESULTS,
        use_tools: bool = True,
        max_tool_iterations: int | None = None,
        response_language: str | None = None,
        active_tool_names: list[str] | None = None,
    ) -> str:
        """``ask`` body — always runs inside a held turn slot."""
        # v0.9.14.1: None → resolve the env-configurable default
        # (MAX_TOOL_ITERATIONS); an explicit int still wins.
        if max_tool_iterations is None:
            max_tool_iterations = _resolve_max_tool_iterations()
        # v0.9.12.0+: fresh per-turn usage — a provider that omits
        # usage must not leave the PREVIOUS turn numbers behind.
        self.last_usage = None
        # v0.9.57.1: fresh per-turn tool-dispatch audit (telegram
        # photo-hallucination guard reads it after the turn).
        self.last_tool_dispatches = []
        # v0.9.91.0: calendar action-integrity guard — once per turn.
        self._calendar_guard_ran = False
        # v0.9.94.0: result grounding registry (uuid 存證) — reset per
        # turn at BOTH entries.
        self._turn_result_refs = {}
        # v0.9.124.0: rulebook prediction-error hint — at most ONCE per
        # turn (reset at BOTH entries, same discipline as above).
        self._rulebook_hint_shown = False
        # v0.9.76.3: fresh per-turn list_chats summary stash (the
        # deterministic post-process only covers results of THIS turn).
        self._last_list_chats_summary = None
        # v0.9.102.7: fresh per-turn snapshot-path stash — every
        # onvif_get_snapshot / onvif_rtsp_snapshot result this turn
        # feeds the _ensure_snapshot_embed deterministic post-process.
        self._snapshot_paths = []
        # v0.9.83.3: stash the question — the bare/glued echo pass
        # derives a real query from it when the model echoes a search
        # tool without usable args.
        self._last_turn_question = str(question or "")
        # v0.9.59.2: fresh per-turn memory-hit audit (the citation
        # post-process only covers memories injected THIS turn).
        self._last_memory_hits = []
        _ = self.client  # ensure model/provider are set
        mgr = self.mcp_manager
        # v0.9.84.1 — Jev time route (plan §5.1 slice; off/shadow/on):
        # a confident date/time question is answered deterministically
        # from the real get_current_time tool (skip the LLM loop);
        # shadow mode only logs; failures run the normal path.
        # v0.9.84.2 — router v2 runs FIRST (one choice call covering
        # time/greeting/status/weather); when it is off, the .84.1
        # time noul route stays available (compat, no double calls).
        rmode = _route_mode()
        action = None
        payload = None
        if rmode != "off":
            try:
                action, payload, _p = _jev_class_route(
                    mgr, question, response_language)
            except Exception:
                action, payload = None, None
            if action == "direct" and payload:
                return (payload, "", [])
            if action == "prefetch" and payload:
                try:
                    msgs = self._messages_with_user(
                        question, history=history,
                        web_enabled=False,
                        tools_enabled=False,
                        response_language=response_language,
                        use_memory=use_memory,
                    )
                    msgs.append({
                        "role": "system",
                        "content": ("以下為本回合預先查詢的網路搜尋結果；"
                                    "請直接整理成回答，不要呼叫任何工具：\n"
                                    + str(payload)),
                    })
                    resp, ok = self._create_validated(
                        model=self._model, messages=msgs,
                        temperature=temperature,
                    )
                    if ok:
                        content = (resp.choices[0].message.content
                                   or "").strip()
                        if content:
                            return (content, "", [])
                except Exception as e:
                    logger.warning(
                        "[Route] prefetch summary failed (%.120r)", e)
        else:
            try:
                direct, _p = _jev_time_route(mgr, question)
            except Exception:
                direct = None
            if direct:
                return (direct, "", [])
        if use_tools and mgr.has_tools():
            # v0.9.98.0 — same routed surface as chat_turn (ask path).
            _toolroute_override = None
            if active_tool_names is None:
                tmode = _toolroute_mode()
                if tmode != "off":
                    _tcls, _tp, _tmeta = _jev_toolroute(question)
                    _srvs, _cls2 = _toolroute_servers_for(_tcls, question)
                    logger.info(
                        "[ToolRoute] mode=%s class=%s p=%s servers=%s "
                        "elapsed=%.2fs",
                        tmode, _tcls, _tp, _srvs,
                        (_tmeta or {}).get("elapsed_s", 0) or 0,
                    )
                    if tmode == "on" and (_tcls or _srvs):
                        # v0.9.133.14 — the union ALSO rides when Jev is
                        # LOW-CONFIDENT (class=None): _srvs already
                        # contains the problem-side trigger hits
                        # (「…docx」→ wordcraft etc.) and the core set.
                        # Without this, a low-confidence question fell
                        # back to the FULL fleet surface, which the
                        # 500-tool cap trims BY REGISTRATION ORDER —
                        # live 2026-10-22 16:39: wordcraft/deckcraft/
                        # filmcraft/blender trimmed away on a docx
                        # form-filling task → 0 dispatches.
                        _toolroute_override = _srvs
            _route_tools = (
                self._toolroute_tool_names(_toolroute_override)
                if _toolroute_override is not None else None
            )
            msgs = self._messages_with_user(
                question, history=None,
                web_enabled=use_web_search,
                tools_enabled=True,
                response_language=response_language,
            )
            answer = self._tool_loop(
                messages=msgs,
                tools=self._chat_tools(
                    active_tool_names if active_tool_names is not None
                    else _route_tools),
                temperature=temperature,
                max_iterations=max_tool_iterations,
            )
            return self._append_memory_citation(answer)

        # Legacy path - one-shot with pre-baked context.
        context = self._build_context(
            question, top_k=top_k,
            use_web_search=use_web_search,
            web_max_results=web_max_results,
        )
        # v0.9.16.1/2: retry once; empty choices / hard API errors →
        # graceful text (was a raw crash / error toast).
        try:
            resp, ok = self._create_validated(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": self._system_prompt(
                            web_enabled=use_web_search,
                            tools_enabled=False,
                            response_language=response_language,
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Context:\n\n{context}\n\n"
                            f"Question: {question}\n\n"
                            f"Please answer based on the "
                            f"context above."
                        ),
                    },
                ],
                temperature=temperature,
                **self._routing_create_kwargs(),
            )
        except Exception as e:
            logger.warning(
                "[agent] ask(): create failed after retry (model=%s): "
                "%s: %s", self._model, type(e).__name__, str(e)[:200],
            )
            return (
                f"(LLM 呼叫失敗,已自動重試一次仍失敗:{type(e).__name__}: "
                f"{str(e)[:300]} — 請稍後再試。/ LLM call failed after "
                f"one retry — please try again.)"
            )
        self.last_usage = self._extract_usage(resp)
        if not ok:
            logger.warning(
                "[agent] ask(): provider returned no choices "
                "(model=%s) — returning graceful empty-response text",
                self._model,
            )
            return EMPTY_RESPONSE_TEXT
        return self._append_memory_citation(
            resp.choices[0].message.content or ""
        )

    def chat_turn(
        self,
        question: str,
        history: list[dict] | None = None,
        top_k: int = 5,
        temperature: float = 0.3,
        use_web_search: bool = False,
        web_max_results: int = DEFAULT_WEB_MAX_RESULTS,
        use_tools: bool = True,
        max_tool_iterations: int | None = None,
        response_language: str | None = None,
        active_tool_names: list[str] | None = None,
        use_memory: bool | None = None,
    ) -> tuple[str, str, list[str]]:
        """Multi-turn chat. Returns
        ``(answer, context, source_filenames)``.

        ``context`` is empty in tool-calling path; source
        filenames are scraped from the assistant's plain answer.

        ``response_language`` (v0.8.5+) is forwarded to the system
        prompt so the LLM answers in the user's UI locale. None
        (the default) preserves the v0.8.4 behaviour with no
        language directive.

        ``active_tool_names`` (v0.9.14.0+) — explicit per-TOOL
        allow-list from the GUI chat tool picker; overrides the
        ``MCP_ACTIVE_SERVERS`` env gate for this request (None =
        env gate applies).

        ``use_memory`` (v0.9.67.2+) — False suppresses the per-turn
        long-term-memory recall block for THIS turn (the scheduler
        fire path passes False by default: fires are independent
        runs asking for TODAY's data and must not be fed stored
        snapshots of old prices). None = default recall behaviour.

        v0.9.45.0 (parallel multi-task Phase 1): the turn runs inside
        the process-wide ``turn_slot()`` gate
        (``QDRANT_MAX_CONCURRENT_TURNS``; unlimited when unset — every
        fire source (GUI / scheduler / webhook / telegram / CLI)
        shares the cap; nested same-thread turns reuse the slot).
        """
        with turn_slot():
            return self._chat_turn_impl(
                question=question,
                history=history,
                top_k=top_k,
                temperature=temperature,
                use_web_search=use_web_search,
                web_max_results=web_max_results,
                use_tools=use_tools,
                max_tool_iterations=max_tool_iterations,
                response_language=response_language,
                active_tool_names=active_tool_names,
                use_memory=use_memory,
            )

    def _chat_turn_impl(
        self,
        question: str,
        history: list[dict] | None = None,
        top_k: int = 5,
        temperature: float = 0.3,
        use_web_search: bool = False,
        web_max_results: int = DEFAULT_WEB_MAX_RESULTS,
        use_tools: bool = True,
        max_tool_iterations: int | None = None,
        response_language: str | None = None,
        active_tool_names: list[str] | None = None,
        use_memory: bool | None = None,
    ) -> tuple[str, str, list[str]]:
        """``chat_turn`` body — always runs inside a held turn slot."""
        # v0.9.14.1: None → resolve the env-configurable default
        # (MAX_TOOL_ITERATIONS); an explicit int still wins.
        if max_tool_iterations is None:
            max_tool_iterations = _resolve_max_tool_iterations()
        # v0.9.12.0+: fresh per-turn usage (same reset discipline
        # as ask) — the tool-loop path sets it in _tool_loop.
        self.last_usage = None
        # v0.9.57.1: fresh per-turn tool-dispatch audit (telegram
        # photo-hallucination guard reads it after the turn).
        self.last_tool_dispatches = []
        # v0.9.91.0: calendar action-integrity guard — once per turn.
        self._calendar_guard_ran = False
        # v0.9.94.0: result grounding registry (uuid 存證) — reset per
        # turn at BOTH entries.
        self._turn_result_refs = {}
        # v0.9.124.0: rulebook prediction-error hint — at most ONCE per
        # turn (reset at BOTH entries, same discipline as ask).
        self._rulebook_hint_shown = False
        # v0.9.76.3: fresh per-turn list_chats summary stash.
        self._last_list_chats_summary = None
        # v0.9.102.7: fresh per-turn snapshot-path stash (same
        # discipline as the ask entry above).
        self._snapshot_paths = []
        # v0.9.83.3: stash the question — the bare/glued echo pass
        # derives a real query from it when the model echoes a search
        # tool without usable args.
        self._last_turn_question = str(question or "")
        # v0.9.59.2: fresh per-turn memory-hit audit — the citation
        # post-process only covers memories injected THIS turn.
        self._last_memory_hits = []
        _ = self.client  # ensure model/provider are set
        mgr = self.mcp_manager
        # v0.9.98.0 — Jev dynamic tool-surface: ONE pre-LLM Jev
        # ``choice`` picks the tool CLASS this question needs; the
        # turn's surface = core servers + the class's servers (still
        # env-gated + hard-capped). off default; shadow logs only;
        # GUI picker override and failures keep the legacy surface.
        _toolroute_override: list[str] | None = None
        if use_tools and mgr.has_tools() and active_tool_names is None:
            tmode = _toolroute_mode()
            if tmode != "off":
                # v0.9.104.1 — short continuations (「ok」「執行」「繼續」
                # 「run」「核准」) continue the PREVIOUS turn's plan:
                # thread the previous assistant reply into BOTH the Jev
                # class decision and the server-union mining.
                _prev_reply = _prev_assistant_reply(history)
                _tcls, _tp, _tmeta = _jev_toolroute(
                    question, prev_reply=_prev_reply)
                _srvs, _classes = _toolroute_servers_for(
                    _tcls, question, prev_reply=_prev_reply)
                logger.info(
                    "[ToolRoute] mode=%s class=%s p=%s servers=%s "
                    "elapsed=%.2fs",
                    tmode, _tcls, _tp, _srvs,
                    (_tmeta or {}).get("elapsed_s", 0) or 0,
                )
                if tmode == "on" and (_tcls or _srvs):
                    # v0.9.133.14 — same low-confidence union fallback
                    # as the ask path (see that comment).
                    _toolroute_override = _srvs
        if use_tools and mgr.has_tools():
            _tt0 = time.perf_counter()
            msgs = self._messages_with_user(
                question, history=history,
                web_enabled=use_web_search,
                tools_enabled=True,
                response_language=response_language,
                use_memory=use_memory,
            )
            _tt1 = time.perf_counter()
            # v0.9.98.0 — routed surface wins over the env gate unless
            # the user pinned tools via the GUI picker.
            _route_tools = (
                self._toolroute_tool_names(_toolroute_override)
                if _toolroute_override is not None else None
            )
            answer = self._tool_loop(
                messages=msgs,
                tools=self._chat_tools(
                    active_tool_names if active_tool_names is not None
                    else _route_tools),
                temperature=temperature,
                max_iterations=max_tool_iterations,
            )
            _tt2 = time.perf_counter()
            sources = _extract_sources(answer)
            answer = self._append_memory_citation(answer)
            _tt3 = time.perf_counter()
            # v0.9.86.0 — answer-support check (RAG answers only).
            answer = self._check_answer_support(question, answer, msgs)
            _tt4 = time.perf_counter()
            # v0.9.91.0 — calendar action-integrity guard.
            answer = self._calendar_action_guard(
                question, answer, msgs, temperature, max_tool_iterations)
            _tt5 = time.perf_counter()
            # v0.9.94.0 — result grounding guard (uuid 存證 + Jev 驗證).
            answer = self._result_grounding_guard(answer, msgs, temperature)
            _tt6 = time.perf_counter()
            # v0.9.92.0 — per-turn stage breakdown (web chat latency
            # confirmation: prompt build/memory recall vs the LLM tool
            # loop vs the deterministic post-guards).
            logger.info(
                "[TurnTiming] total=%.2fs recall+prompt=%.2fs "
                "llm_tool_loop=%.2fs citation=%.2fs support=%.2fs "
                "calendar_guard=%.2fs grounding=%.2fs dispatches=%d",
                _tt6 - _tt0, _tt1 - _tt0, _tt2 - _tt1, _tt3 - _tt2,
                _tt4 - _tt3, _tt5 - _tt4, _tt6 - _tt5,
                len(self.last_tool_dispatches or []),
            )
            return answer, "", sources

        # Legacy path.
        context = self._build_context(
            question, top_k=top_k,
            use_web_search=use_web_search,
            web_max_results=web_max_results,
        )
        msgs = self._messages_with_user(
            question, history=history,
            web_enabled=use_web_search,
            tools_enabled=False,
            response_language=response_language,
            use_memory=use_memory,
        )
        msgs[-1] = {
            "role": "user",
            "content": (
                f"Context:\n\n{context}\n\n"
                f"Question: {question}\n\n"
                f"Please answer based on the context above."
            ),
        }
        # v0.9.16.1/2: retry once; empty choices / hard API errors →
        # graceful text (was a raw crash / error toast).
        try:
            resp, ok = self._create_validated(
                model=self._model, messages=msgs,
                temperature=temperature,
                **self._routing_create_kwargs(),
            )
        except Exception as e:
            logger.warning(
                "[agent] chat_turn(): create failed after retry "
                "(model=%s): %s: %s",
                self._model, type(e).__name__, str(e)[:200],
            )
            return (
                f"(LLM 呼叫失敗,已自動重試一次仍失敗:{type(e).__name__}: "
                f"{str(e)[:300]} — 請稍後再試。/ LLM call failed after "
                f"one retry — please try again.)"
            ), context, []
        self.last_usage = self._extract_usage(resp)
        if not ok:
            logger.warning(
                "[agent] chat_turn(): provider returned no choices "
                "(model=%s) — returning graceful empty-response text",
                self._model,
            )
            return EMPTY_RESPONSE_TEXT, context, []
        answer = (resp.choices[0].message.content or "").strip()
        sources = re.findall(
            r"\[Document \d+\] Source: (.+)", context,
        )
        answer = self._append_memory_citation(answer)
        return answer, context, sources


def _extract_sources(answer: str) -> list[str]:
    """Pull likely source filenames out of the assistant reply."""
    out: list[str] = []
    seen: set[str] = set()
    for m in re.findall(
        r"`([^`\s]+\.[A-Za-z0-9]{1,5})`", answer or "",
    ):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out



 
