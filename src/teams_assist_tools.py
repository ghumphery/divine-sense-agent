"""Teams desktop-assist engine (v0.9.69.0+) — MCP extension core.

Zero-Microsoft-permission Teams assistant: the assistant operates the
user's OWN logged-in Teams DESKTOP client (via the existing windows-mcp
tool fleet) instead of any Teams API — no app registration, no OAuth
consent, no Graph permissions. This module is the pure engine the
``TeamsAssistLocalHost`` (src/mcp_client/host.py) wraps:

* screen-watch — a cheap (no-LLM) screenshot-hash poll loop that fires
  the LLM ONLY when the screen actually changed (bounded fire rate +
  uniform-screen skip + auto-pause after consecutive grab failures);
* fire engine — ``make_teams_assist_fire`` builds the judge-then-act
  prompt (v0.9.41.1 recipe) for a FRESH Agent turn: read the Teams
  window (``teams_assist_check`` → ``describe_image``), draft a reply
  in the USER's persona, and (``QDRANT_TEAMS_ASSIST_AUTO=on`` only)
  send it through windows-mcp, then register it with
  ``teams_assist_reply``;
* reply gate — ``teams_assist_reply(confirm=true)`` is the write gate
  (hicloud pattern): the AUTO-fired path needs
  ``state.auto_send_allowed`` (the fire engine sets it per turn from
  the AUTO switch); a manual user-requested send passes
  ``manual=true``; ``confirm=false`` records a DRAFT only;
* anti-fabrication guard — a fired turn whose answer claims 「已回覆/
  已送出…」 without a ``teams_assist_reply`` dispatch gets ONE
  corrective re-prompt (v0.9.57.1 photo-guard lesson);
* audit — every fire/draft/send lands in the state ring (50).

**Mock-first**: ``QDRANT_TEAMS_ASSIST_MODE=mock`` (the default)
replaces the real screen grab with ``mock_screen.txt`` content
hashing — the whole watch/fire loop is testable without ever touching
the real UI. ``live`` mode grabs the real screen via PIL ImageGrab
(Pillow is already a project dependency). There is NO direct win32
interaction anywhere in this module — the actual typing into Teams is
performed by the LLM through the windows-mcp fleet, exactly like the
ONVIF judge-then-notify recipe.

Env (all read directly from ``os.environ`` — monkeypatch-friendly):

* ``QDRANT_TEAMS_ASSIST_MODE``             mock|live (default mock)
* ``QDRANT_TEAMS_ASSIST_POLL_INTERVAL``    seconds between watch
  polls (default 15, clamp 10-300)
* ``QDRANT_TEAMS_ASSIST_WATCH_MODE``       screen|uia (default
  screen). v0.9.71.0+: ``uia`` = hash the Teams CHAT TEXT via the
  UIA TextPattern read channel — the watch fires only when the chat
  text actually changed (near-zero false fires, works with the Teams
  window minimised). A failed UIA read falls back to the pixel-hash
  path automatically.
* ``QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL`` minimum seconds between
  two LLM fires (default 120, clamp 0-3600)
* ``QDRANT_TEAMS_ASSIST_AUTO``             on|off (default off =
  draft-only fires)
* ``TEAMS_ASSIST_ALLOW_CHATS``             comma list of chat titles
  the assistant may engage — fail-closed: empty ⇒ the watch refuses
  to start and the fire prompt is taught to stand down
* ``TEAMS_ASSIST_WINDOW_RECT``             ``x,y,w,h`` grab region
  (the Teams window); unset = full screen
* ``TEAMS_MY_NAME`` / ``TEAMS_MY_ROLE`` / ``TEAMS_REPLY_STYLE``
  persona interpolated into the fire prompt.
"""
from __future__ import annotations

import hashlib
import fnmatch
import io
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_AUDIT = 50
MAX_GRAB_FAILURES = 5
SESSION_ID = "teamsassist-watch"

# ─── env resolvers (os.environ direct — monkeypatch-friendly) ────


def _assist_mode() -> str:
    mode = (os.environ.get("QDRANT_TEAMS_ASSIST_MODE", "mock")
            or "mock").strip().lower()
    return mode if mode in ("mock", "live") else "mock"


def _poll_interval(default: float = 15.0) -> float:
    try:
        v = float(os.environ.get("QDRANT_TEAMS_ASSIST_POLL_INTERVAL", "")
                  or default)
    except (TypeError, ValueError):
        return default
    return max(10.0, min(300.0, v))


def _min_fire_interval(default: float = 120.0) -> float:
    try:
        v = float(os.environ.get("QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL", "")
                  or default)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(3600.0, v))


def _auto_enabled() -> bool:
    return (os.environ.get("QDRANT_TEAMS_ASSIST_AUTO", "off")
            or "off").strip().lower() in ("1", "on", "true", "yes")


def _allow_chats() -> list[str]:
    raw = (os.environ.get("TEAMS_ASSIST_ALLOW_CHATS", "")
           or "").strip()
    return [c.strip() for c in raw.split(",") if c.strip()]


def _allow_all() -> bool:
    """Wildcard allowlist (v0.9.73.0+): ``TEAMS_ASSIST_ALLOW_CHATS=*``
    means the assistant may engage ANY chat (still fail-closed when
    the list is empty)."""
    return "*" in _allow_chats()


def _chat_allowed(title: str | None) -> bool:
    """Filter-match a chat title against the allowlist (v0.9.75.0+).

    Each entry is either an EXACT title or a GLOB filter containing
    ``*`` (``加密分持*`` = starts with, ``*會議*`` = contains;
    case-sensitive). ``*`` alone engages every chat. None/empty →
    False (fail-closed)."""
    if not title:
        return False
    entries = _allow_chats()
    if not entries:
        return False
    if _allow_all():
        return True
    title = title.strip()
    for entry in entries:
        if entry == title:
            return True
        if "*" in entry and fnmatch.fnmatchcase(title, entry):
            return True
    return False


# Chat-list UI chrome labels filtered out of the UIA read text
# (v0.9.76.0 heuristic list extraction).
_UIA_LIST_NOISE = frozenset({
    "聊天", "頻道", "其他清單選項", "查看更多", "快速檢視", "提及",
    "探索", "草稿", "已儲存", "我的最愛", "新項目", "Copilot",
    "Teams 和頻道", "已共用", "備註", "重點回顧", "新增索引標籤",
    "語音通話", "其他通話選項", "翻譯", "設定及其他",
    "3", "返回", "向前",
})


def _uia_chat_titles() -> list[str]:
    """Heuristic chat-title extraction (v0.9.76.0+) from the UIA read
    text's LEFT chat-list panel. Best-effort: the panel text is cut at
    the message-pane boundary, UI chrome labels are filtered, and
    duplicates dropped in order. Empty list = read failed / no list."""
    if _assist_mode() != "live":
        return []
    try:
        out = _uia_ops("read")
    except Exception:
        return []
    if not out.get("ok"):
        return []
    # The message pane (active chat body) starts at this chrome label —
    # everything before it is the chat-list panel.
    text = str(out.get("text") or "").split("調整 左 面板大小")[0]
    titles: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if (not line or line == "￼" or line in _UIA_LIST_NOISE
                or "(Ctrl" in line or line.endswith("(您)")):
            continue
        if len(line) > 60 or re.match(r"^\d+[/月]", line):
            continue  # message bodies / timestamps
        if line not in titles:
            titles.append(line)
    return titles


def _engaged_chat_sessions() -> list[dict[str, Any]]:
    """Per-chat sessions that already exist (chats the assistant has
    engaged — i.e. being watched/were watched), newest first."""
    from src.agent.session import SessionManager

    rows = [
        {
            "sid": s.get("id") or "",
            "title": s.get("title") or s.get("id") or "",
            "messages": s.get("n_messages") or 0,
            "updated_at": s.get("updated_at"),
        }
        for s in SessionManager().list()
        if (s.get("id") or "").startswith("teamsassist-")
        and s.get("id") != SESSION_ID
    ]
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return rows


def _window_rect() -> tuple[int, int, int, int] | None:
    raw = (os.environ.get("TEAMS_ASSIST_WINDOW_RECT", "")
           or "").strip()
    if not raw:
        return None
    try:
        parts = [int(p) for p in raw.split(",")]
        if len(parts) != 4:
            return None
        x, y, w, h = parts
        if w <= 0 or h <= 0:
            return None
        return (x, y, w, h)
    except (TypeError, ValueError):
        return None


def _persona() -> dict[str, str]:
    return {
        "name": (os.environ.get("TEAMS_MY_NAME", "") or "").strip(),
        "role": (os.environ.get("TEAMS_MY_ROLE", "") or "").strip(),
        "style": (os.environ.get("TEAMS_REPLY_STYLE", "") or "").strip(),
    }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _assist_home() -> Path:
    base = Path(
        os.environ.get(
            "QDRANT_RAG_HOME", Path.home() / ".qdrant-rag-agent"
        )
    )
    p = base / "teams_assist"
    p.mkdir(parents=True, exist_ok=True)
    return p

# ─── screen grab (live) ───────────────────────────────────────────


def _default_grab() -> bytes:
    """Grab the (rect-restricted) screen as PNG bytes. Live mode only —
    mock mode injects its own grab source. Raises when Pillow is
    unavailable or the grab fails (the watcher counts failures)."""
    from PIL import ImageGrab

    rect = _window_rect()
    img = ImageGrab.grab(bbox=rect) if rect else ImageGrab.grab()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _screen_signature(png: bytes) -> dict[str, Any]:
    """Cheap screen signature: 64×64 grayscale hash + uniform flag.

    ``uniform=True`` means locked / blank screen — the watcher skips
    it instead of burning an LLM turn on a black frame."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("L")
    small = img.resize((64, 64))
    lo, hi = small.getextrema()
    return {
        "hash": hashlib.sha1(small.tobytes()).hexdigest(),
        "uniform": (hi - lo) < 8,
    }


def _mock_screen_text() -> str:
    p = _assist_home() / "mock_screen.txt"
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _mock_grab() -> bytes:
    """Mock grab source: hash of ``mock_screen.txt`` content. Tests
    simulate a "new message on screen" by rewriting that file."""
    return _mock_screen_text().encode("utf-8")


# ─── persistent state (atomic writes, ScheduleManager shape) ─────


def _state_path() -> Path:
    return _assist_home() / "state.json"


def _default_state() -> dict[str, Any]:
    return {
        "last_hash": None,
        "last_uia_hash": None,
        "last_uia_error": None,
        "last_check_at": None,
        "last_fire_at": None,
        "paused": False,
        "grab_failures": 0,
        "auto_send_allowed": False,
        "watch": {"running": False, "interval_s": None,
                  "started_at": None, "stopped_at": None},
        "audit": [],
    }


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            merged = _default_state()
            merged.update(data)
            merged.setdefault("watch", _default_state()["watch"])
            merged.setdefault("audit", [])
            return merged
    except (OSError, ValueError):
        pass
    return _default_state()


def _save_state(state: dict[str, Any]) -> None:
    """Atomic write via tmp + os.replace (ScheduleManager pattern)."""
    try:
        path = _state_path()
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("[teams-assist] state save failed: %r", e)


def _audit(state: dict[str, Any], kind: str, **fields: Any) -> None:
    state.setdefault("audit", [])
    row = {"ts": _now(), "kind": kind, **fields}
    state["audit"].append(row)
    state["audit"] = state["audit"][-MAX_AUDIT:]

# ─── anti-fabrication guard + fire prompt ─────────────────────────

_CLAIM_WORDS = (
    "已回覆", "已送出", "已傳送", "已發送", "已回", "已代回",
    "已貼上", "已打字", "replied", "sent the reply",
)


def _needs_reply_guard(answer: str, dispatches: list) -> bool:
    """Deterministic anti-fabrication guard (v0.9.57.1 lesson): a
    fired turn whose answer claims the reply was delivered but never
    dispatched ``teams_assist_reply`` gets one corrective re-prompt."""
    if not answer:
        return False
    claimed = any(w in answer for w in _CLAIM_WORDS)
    if not claimed:
        return False
    for d in dispatches or []:
        name = d if isinstance(d, str) else str(d)
        if isinstance(name, str) and name.startswith("teams_assist"):
            return False
    return True


_REPLY_RETRY_PROMPT = (
    "糾正回合：你上一則回答宣稱已回覆，但本回合沒有任何 "
    "teams_assist_reply 派發紀錄。請誠實處理：\n"
    "1) 若尚未真正送出 — 完成送出（windows-mcp 聚焦 Teams → "
    "輸入框 → 打字 → 送出）後呼叫 teams_assist_reply(text, chat, "
    "confirm=true) 註冊；\n"
    "2) 若已透過 windows-mcp 送出但漏了註冊 — 只呼叫 "
    "teams_assist_reply(text, chat, confirm=true) 補註冊，"
    "切勿重複打字送出（會雙重發送）；\n"
    "3) 若無法完成 — 明確回覆失敗原因，禁止宣稱已回覆。"
)


def _compose_fire_prompt(payload: dict | None) -> str:
    persona = _persona()
    who = persona.get("name") or "使用者本人"
    role = persona.get("role") or ""
    style = persona.get("style") or "簡潔、專業、繁體中文"
    chats = _allow_chats()
    if _allow_all():
        allow_line = "任何聊天（萬用字元 *）"
        allow_rule = (
            "2. 白名單為萬用字元 * — 可以處理任何聊天（仍只處理 Teams"
            " 內的真人對話；頻道通知/機器人貼文一律不回）。\n"
        )
    else:
        allow_line = (
            "、".join(chats) if chats else "（白名單為空 — 一律不得送出）"
        )
        allow_rule = (
            "2. 只處理符合白名單過濾條件的聊天（v0.9.75.0+）：每項為"
            "聊天標題的『完全比對』或『含 * 的過濾條件』（如 "
            "加密分持* = 標題以此開頭；*會議* = 標題含此字）。"
            f"當前條件：{allow_line}。聊天標題皆不符合 → 只回覆"
            "『(白名單外 — 未處理)』，不得送出任何訊息。\n"
        )
    auto_on = _auto_enabled()
    mode = _assist_mode()
    head = (
        f"[Teams 自動助理觸發（watch 偵測到畫面變化；mode={mode}）]\n"
        f"觸發資訊: {json.dumps(payload or {}, ensure_ascii=False)}\n\n"
        f"你以「{who}」的身份與口吻處理 Teams 訊息"
    )
    if role:
        head += f"（身份脈絡：{role}）"
    head += f"；文風：{style}。\n\n任務：\n"
    uia = _send_mode() == "uia"
    if uia:
        head += (
            "1. 呼叫 teams_assist_read_uia 直接讀出聊天文字（零干擾；"
            "輸出含左側清單與訊息樹，自行判讀出聊天標題與最新他人訊息）"
            "— 失敗才改用 teams_assist_check + describe_image。\n"
            "   嚴禁使用 windows-mcp 的 Snapshot/Screenshot/App/Click/"
            "Type 等螢幕操作工具讀取或操作 Teams（使用者正在工作 — "
            "截圖/還原視窗/點擊都是干擾）；本 fire 只准用 UIA 通道："
            "讀取一律 teams_assist_read_uia，送出僅在 AUTO=on 時經 "
            "UIA 通道。違反即失職。\n"
            "   注意：UIA 只能讀到『目前開啟』的聊天內文。若新訊息來自"
            "背景聊天（左側清單出現未讀標記，但作用中聊天是別的），"
            "無法在不搶焦點的情況下讀內文 — 如實回覆"
            "『(背景聊天未開啟 — 請將該聊天開在 Teams 目前視窗)』，"
            "不得杜撰背景聊天的內容。\n"
        )
    else:
        head += (
            "1. 呼叫 teams_assist_check 取得螢幕截圖路徑，再用 "
            "describe_image 讀出：目前 Teams 聊天標題、最後幾則訊息。\n"
        )
    head += (
        "3. 若沒有「他人傳來的新訊息」→ 只回覆『(無新訊息)』，"
        "不得送出。\n"
        "4. 有新訊息 → 以使用者本人的身份與口吻草擬回覆（不是以"
        "助理身份，是代使用者本人回覆對話）。\n"
    )
    # splice the allowlist rule (branch-dependent wording) — steps keep
    # their numbering (2 / 3 / 4).
    head += allow_rule
    if auto_on and uia:
        head += (
            "5. AUTO=on + UIA 模式 — 呼叫 teams_assist_send_uia(text, "
            "chat, confirm=true) 背景送出（無鍵鼠干擾、不搶焦點；若"
            "腳本回報 compose-not-empty-yield 表示你在打字 — 改記草稿"
            "）；回覆保持單一段落。\n"
        )
    elif auto_on:
        head += (
            "5. AUTO=on — 送出流程：先用 windows-mcp 檢查 Teams 輸入框"
            "沒有使用者本人未送出的草稿（有 → 讓路，只記草稿不送出）；"
            "聚焦 Teams 視窗 → 點擊輸入框 → 打字 → 送出；成功後呼叫 "
            "teams_assist_reply(text, chat, confirm=true) 註冊。\n"
        )
    else:
        head += (
            "5. AUTO=off — 不得打字送出。呼叫 teams_assist_reply("
            "text, chat, confirm=false) 記錄草稿，並在回答中附上草稿"
            "全文供使用者核可（回答長度不足 20 字而未附草稿視為失職）。\n"
        )
    head += (
        "6. 絕不虛構「已回覆」— 沒有真的送出就不得宣稱已送出；"
        "無法完成時誠實回報原因。\n"
    )
    return head

# ─── fire engine (mirror of make_webhook_fire) ────────────────────


def _chat_slug(title: str) -> str:
    """Sid-safe slug for a chat title (CJK kept, path/control chars
    stripped, ≤40 chars)."""
    cleaned = re.sub(r"[\\/:*?\"<>|\r\n\t]", " ", (title or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:40].strip()


def _active_chat_title() -> str | None:
    """Resolve the ACTIVE chat title (v0.9.74.0+) from the Teams
    window title via the UIA read channel (``聊天 | <title> | …``).
    Used to file each fire into a PER-CHAT session. Returns None when
    the UIA channel is unavailable / not a chat window / mock mode —
    the fire then falls back to the master watch session."""
    if _assist_mode() != "live":
        return None
    try:
        out = _uia_ops("read")
    except Exception:
        return None
    if not out.get("ok"):
        return None
    parts = [p.strip() for p in str(out.get("window") or "").split("|")]
    if len(parts) >= 2 and parts[0] in ("聊天", "Chat"):
        return parts[1] or None
    return None


def _fire_history_limit() -> int:
    """``QDRANT_TEAMS_ASSIST_FIRE_HISTORY`` — default **-1** (FULL
    per-chat history; sessions are per-chat since v0.9.74.0, so
    growth is bounded by ONE chat). 0 = independent fires (no
    history), N = only the last N history messages. Invalid → -1."""
    raw = (os.environ.get("QDRANT_TEAMS_ASSIST_FIRE_HISTORY", "")
           or "").strip()
    if not raw:
        return -1
    try:
        return int(raw)
    except ValueError:
        return -1


def _fire_session_for(chat: str) -> tuple[str, str]:
    """Per-chat session (sid, title) — but ONLY when the active chat
    actually matches the filters (v0.9.77.0 lesson: a background chat
    message changes the watch hash while ANOTHER chat is open; filing
    that fire into the active-but-unrelated chat's session pollutes
    it). The master watch session is the fallback otherwise."""
    slug = _chat_slug(chat)
    if slug and _chat_allowed(chat):
        return f"teamsassist-{slug}", f"[teams-assist] {slug}"
    return SESSION_ID, "[teams-assist] watch"


def _cap_history(messages: list, limit: int) -> list:
    """History fed to the LLM under the FIRE_HISTORY cap (the session
    file itself always keeps everything)."""
    if limit < 0:
        return messages
    return messages[-limit:] if limit else []


def make_teams_assist_fire(
    agent_factory: Callable[[], Any],
) -> Callable[[dict | None], dict[str, Any]]:
    """Build ``fire(payload) -> dict`` that runs ONE judge-then-act
    ``Agent.chat_turn`` over a fresh Agent (same isolation as the
    scheduler/webhook/telegram fires).

    The turn is persisted in a PER-CHAT session (``teamsassist-<聊天
    標題>`` — v0.9.74.0+; the active chat title is resolved from the
    Teams window title via the UIA read channel; the master
    ``teamsassist-watch`` session is the fallback), registered in the
    process-wide task registry, and guarded by the anti-fabrication
    corrective re-prompt. ``QDRANT_TEAMS_ASSIST_FIRE_HISTORY`` caps
    the history fed to the LLM (default -1 = full per-chat history;
    the session file always keeps everything).
    """
    from src.agent.session import Session, SessionManager

    def fire(payload: dict | None = None) -> dict[str, Any]:
        started = datetime.now()
        state = _load_state()
        mode = _assist_mode()
        auto_on = _auto_enabled()
        state["auto_send_allowed"] = auto_on
        _save_state(state)

        # ── Jev watch gate (v0.9.84.0+, plan §5.3) — deterministic
        # UIA read + one noul decision BEFORE burning an LLM turn; every
        # failure fails OPEN to the LLM turn below.
        gate_skip = _watch_gate_skip()
        if gate_skip is not None:
            state = _load_state()
            g = dict(state.get("gate") or {})
            g["skipped"] = int(g.get("skipped") or 0) + 1
            g["last_p"] = gate_skip.get("p")
            g["last_at"] = _now()
            state["gate"] = g
            _audit(state, "watch-gate-skip", p=gate_skip.get("p"),
                   elapsed=gate_skip.get("elapsed_s"))
            _save_state(state)
            dur = (datetime.now() - started).total_seconds()
            return {
                "ok": True, "fired": False, "gate": "skipped",
                "p": gate_skip.get("p"),
                "duration_s": round(dur, 2),
            }

        agent = agent_factory()
        sm = SessionManager()
        # v0.9.74.0+: per-CHAT session when the active chat title is
        # resolvable (UIA window title); master watch session falls
        # back otherwise. QDRANT_TEAMS_ASSIST_FIRE_HISTORY caps the
        # history FED to the LLM (the session file keeps everything).
        chat = _active_chat_title() or ""
        sid, title = _fire_session_for(chat)
        sess = sm.load(sid)
        if sess is None:
            sess = Session(sid=sid, title=title)
        framed = _compose_fire_prompt(payload)
        sess.messages.append({
            "role": "user", "content": framed, "ts": _now(),
        })
        limit = _fire_history_limit()
        history = _cap_history(sess.messages[:-1], limit)
        from src.agent.task_runner import begin_task, end_task

        _tr = begin_task(
            "teams-assist", f"watch fire (mode={mode})", sid=sess.id,
        )
        try:
            result = agent.chat_turn(
                question=framed,
                history=history,
                use_tools=True,
            )
        except Exception as chat_exc:
            try:
                end_task(_tr, "failed", error=repr(chat_exc)[:500])
            except Exception:
                pass
            logger.exception(
                "teams-assist: chat_turn failed: %r", chat_exc,
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
            return {"ok": False, "error": repr(chat_exc),
                    "session_id": sess.id}
        # Tuple/dict dual contract (v0.9.23.0 lesson).
        if isinstance(result, dict):
            answer = result.get("answer")
        elif isinstance(result, tuple) and len(result) >= 1:
            answer = result[0]
        else:
            answer = None
        answer_text = str(answer or "").strip()

        # ── anti-fabrication corrective re-prompt (one shot) ──────
        dispatches = list(
            getattr(agent, "last_tool_dispatches", []) or [],
        )
        if _needs_reply_guard(answer_text, dispatches):
            logger.warning(
                "teams-assist: reply-fabrication guard fired "
                "(dispatched=%r, answer=%.120r) — corrective "
                "re-prompt", dispatches, answer_text,
            )
            sess.messages.append({
                "role": "assistant", "content": answer_text,
                "ts": _now(), "sources": [],
                "fabrication_guarded": True,
            })
            sess.messages.append({
                "role": "user", "content": _REPLY_RETRY_PROMPT,
                "ts": _now(),
            })
            try:
                retry = agent.chat_turn(
                    question=_REPLY_RETRY_PROMPT,
                    history=_cap_history(sess.messages[:-1], limit),
                    use_tools=True,
                )
                if isinstance(retry, dict):
                    r_answer = retry.get("answer")
                elif isinstance(retry, tuple) and len(retry) >= 1:
                    r_answer = retry[0]
                else:
                    r_answer = None
                r_text = str(r_answer or "").strip()
                if r_text:
                    answer_text = r_text
                    sess.messages.append({
                        "role": "assistant", "content": r_text,
                        "ts": _now(), "sources": [],
                        "corrective": True,
                    })
                else:
                    sess.messages.append({
                        "role": "assistant", "content": answer_text,
                        "ts": _now(), "sources": [],
                        "corrective_failed": True,
                    })
            except Exception as retry_exc:
                logger.warning(
                    "teams-assist: corrective retry failed: %r",
                    retry_exc,
                )
                sess.messages.append({
                    "role": "assistant", "content": answer_text,
                    "ts": _now(), "sources": [],
                    "corrective_failed": True,
                })
        sess.updated_at = _now()
        if not _needs_reply_guard(answer_text, dispatches):
            # Plain success path — record the final answer (the guard
            # path appends the corrective answer itself below).
            sess.messages.append({
                "role": "assistant", "content": answer_text,
                "ts": _now(), "sources": [],
            })
        try:
            sm.save(sess)
        except Exception:
            pass
        try:
            end_task(_tr, "done", answer_chars=len(answer_text))
        except Exception:
            pass
        state = _load_state()
        state["last_fire_at"] = _now()
        _audit(state, "fire", mode=mode, auto=auto_on,
               answer=answer_text[:200])
        _save_state(state)
        dur = (datetime.now() - started).total_seconds()
        return {
            "ok": True,
            "answer": answer_text,
            "session_id": sess.id,
            "chat": chat or None,
            "mode": mode,
            "auto": auto_on,
            "duration_s": round(dur, 2),
        }

    return fire

# ─── UIA send/read channel (v0.9.70.0+) ───────────────────────────

UIA_REASON_HINTS = {
    "compose-not-empty-yield": (
        "輸入框已有內容（可能你正在打字）— 自動讓路，未觸碰未送出"
    ),
    "send-button-still-present": (
        "送出按鈕仍在 — 未確認送出，絕不重複觸發，請人工確認"
    ),
    "no-teams-process": "Teams 未執行",
    "no-teams-window": "找不到 Teams 主視窗",
    "no-compose-box": "找不到訊息輸入框（Teams UI 結構可能改版）",
    "value-pattern-unsupported": "輸入框不支援 UIA ValuePattern",
}


def _send_mode() -> str:
    mode = (os.environ.get("QDRANT_TEAMS_ASSIST_SEND_MODE", "windows-mcp")
            or "windows-mcp").strip().lower()
    return mode if mode in ("windows-mcp", "uia") else "windows-mcp"


def _watch_mode() -> str:
    """Watch (detection) mode — v0.9.71.0+.

    ``screen`` (default) = the legacy 64×64 pixel-hash poll (any screen
    change is a candidate fire). ``uia`` = read the Teams chat text via
    the UIA TextPattern channel and hash THAT — the watcher fires only
    when the chat text actually changed (near-zero false fires, works
    with the Teams window minimised/covered). Pixel hash stays as the
    automatic fallback whenever a UIA read fails."""
    mode = (os.environ.get("QDRANT_TEAMS_ASSIST_WATCH_MODE", "screen")
            or "screen").strip().lower()
    return mode if mode in ("screen", "uia") else "screen"


# ─── runtime (process-only) config overrides (v0.9.72.0+) ─────────

_CONFIG_KEYS = (
    "mode", "send_mode", "watch_mode", "auto", "allow_chats",
    "my_name", "my_role", "reply_style", "window_rect",
    "poll_interval", "min_fire_interval",
)

_ENV_OF = {
    "mode": "QDRANT_TEAMS_ASSIST_MODE",
    "send_mode": "QDRANT_TEAMS_ASSIST_SEND_MODE",
    "watch_mode": "QDRANT_TEAMS_ASSIST_WATCH_MODE",
    "auto": "QDRANT_TEAMS_ASSIST_AUTO",
    "allow_chats": "TEAMS_ASSIST_ALLOW_CHATS",
    "my_name": "TEAMS_MY_NAME",
    "my_role": "TEAMS_MY_ROLE",
    "reply_style": "TEAMS_REPLY_STYLE",
    "window_rect": "TEAMS_ASSIST_WINDOW_RECT",
    "poll_interval": "QDRANT_TEAMS_ASSIST_POLL_INTERVAL",
    "min_fire_interval": "QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL",
}

_ENUM_OF = {
    "mode": ("mock", "live"),
    "send_mode": ("windows-mcp", "uia"),
    "watch_mode": ("screen", "uia"),
}


def _set_config(args: dict | None) -> dict[str, Any]:
    """TEMPORARY runtime config — the LLM-facing config tool.

    Validates and writes ``os.environ`` ONLY: values take effect on
    the next resolver read (the watcher re-reads per poll tick), are
    visible to every in-process path, and DIE with the process —
    ``.env`` values return on the next boot. All-or-nothing: nothing
    is applied when any key fails validation."""
    args = args or {}
    unknown = [k for k in args if k not in _ENV_OF]
    if unknown:
        return {
            "error": ("unknown key(s): "
                      + ", ".join(sorted(unknown))),
            "valid_keys": list(_CONFIG_KEYS),
        }
    if not args:
        return {"error": "nothing to set",
                "valid_keys": list(_CONFIG_KEYS)}
    old_env = {v: os.environ.get(v) for v in _ENV_OF.values()}
    errors: dict[str, str] = {}
    normalised: dict[str, str | None] = {}
    for key, val in args.items():
        if key in _ENUM_OF:
            s = str(val or "").strip().lower()
            if s in _ENUM_OF[key]:
                normalised[key] = s
            else:
                errors[key] = "must be one of " + "|".join(_ENUM_OF[key])
        elif key == "auto":
            s = str(val or "").strip().lower()
            if s in ("on", "1", "true", "yes"):
                normalised[key] = "on"
            elif s in ("off", "0", "false", "no", "none", ""):
                normalised[key] = "off"
            else:
                errors[key] = "must be on|off"
        elif key == "allow_chats":
            if isinstance(val, (list, tuple)):
                s = ", ".join(
                    str(v).strip() for v in val if str(v).strip()
                )
            else:
                s = str(val or "").strip()
            if s.lower() in ("*", "all", "全部", "所有"):
                s = "*"  # wildcard = engage ANY chat (v0.9.73.0+)
            normalised[key] = s  # empty is legal (fail-closed refuses)
        elif key in ("my_name", "my_role", "reply_style"):
            normalised[key] = str(val or "").strip()
        elif key == "window_rect":
            if val is None or str(val).strip().lower() in (
                "", "off", "clear", "none",
            ):
                normalised[key] = None  # clear → drop the env var
            elif isinstance(val, (list, tuple)) and len(val) == 4:
                try:
                    normalised[key] = ",".join(str(int(v)) for v in val)
                except (TypeError, ValueError):
                    errors[key] = "must be [x, y, w, h] integers"
            else:
                parts = [p.strip() for p in str(val).split(",")]
                ok = (len(parts) == 4
                      and all(p.lstrip("-").isdigit() for p in parts))
                if not ok:
                    errors[key] = "must be 'x,y,w,h' or null to clear"
                else:
                    x, y, w, h = (int(p) for p in parts)
                    if w <= 0 or h <= 0:
                        errors[key] = "w/h must be > 0"
                    else:
                        normalised[key] = f"{x},{y},{w},{h}"
        else:  # poll_interval / min_fire_interval
            try:
                f = float(val)
                if f <= 0:
                    raise ValueError(val)
            except (TypeError, ValueError):
                errors[key] = "must be a positive number"
            else:
                normalised[key] = str(f)
    if errors:
        return {
            "error": ("invalid value(s): " + "; ".join(
                f"{k}: {v}" for k, v in errors.items())),
            "valid_keys": list(_CONFIG_KEYS),
        }
    applied = {}
    for key, s in normalised.items():
        env = _ENV_OF[key]
        if s is None:
            os.environ.pop(env, None)
        else:
            os.environ[env] = s
        applied[key] = {"old": old_env[env], "new": s}
    return {
        "ok": True,
        "applied": applied,
        "effective": {
            "mode": _assist_mode(),
            "send_mode": _send_mode(),
            "watch_mode": _watch_mode(),
            "auto": _auto_enabled(),
            "allow_chats": _allow_chats(),
            "persona": _persona(),
            "window_rect": _window_rect(),
            "poll_interval_s": _poll_interval(),
            "min_fire_interval_s": _min_fire_interval(),
        },
        "note": (
            "暫時設定：只改本執行中的 process（不寫 .env）— 重啟後回到 "
            ".env 的值。watch 執行緒下一次輪詢即採用新值；若 watch 尚未"
            "啟動/白名單剛清空，需再 teams_assist_start_watch。"
        ),
    }


def _uia_script_path() -> Path | None:
    p = (os.environ.get("QDRANT_TEAMS_ASSIST_UIA_SCRIPT", "")
         or "").strip()
    if p:
        cand = Path(p)
        return cand if cand.is_file() else None
    cand = Path(__file__).resolve().parents[1] / "scripts" / (
        "uia_teams_ops.ps1")
    return cand if cand.is_file() else None


def _uia_ops(action: str, text: str | None = None) -> dict:
    """Run ``scripts/uia_teams_ops.ps1 -Action read|send|search`` and parse
    the single-line JSON stdout. Text rides via a UTF-8 temp file (CJK-safe
    command lines). ``search``（v0.9.105.1）＝純 UIA 導航：目標名填入 Teams
    搜尋框→選第一結果→回附 doc_chars 供呼叫端驗證。Never raises — every
    failure returns ``{"ok": False, "reason": ...}`` (hints in
    ``UIA_REASON_HINTS``)."""
    script = _uia_script_path()
    if script is None:
        return {"ok": False, "reason": "uia-script-missing"}
    if action not in ("read", "send", "search"):
        return {"ok": False, "reason": "bad-action"}
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(script), "-Action", action]
    tmp_path: str | None = None
    if action in ("send", "search"):
        if not text or not text.strip():
            return {"ok": False, "reason": "empty-text"}
        # UIA v1: single-paragraph sends (ValuePattern newline
        # semantics on the contenteditable box are unverified).
        single = " ".join(text.split())
        fd, tmp_path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(single)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return {"ok": False, "reason": "temp-write-failed"}
        cmd += ["-TextFile", tmp_path]
    try:
        import subprocess

        kwargs: dict[str, Any] = {
            "capture_output": True, "timeout": 30,
            "encoding": "utf-8", "errors": "replace",
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(cmd, **kwargs)
        out = (proc.stdout or "").strip()
        if proc.returncode != 0 or not out:
            return {"ok": False, "reason": "uia-run-failed",
                    "returncode": proc.returncode,
                    "stderr": (proc.stderr or "")[:300]}
        try:
            data = json.loads(out)
        except ValueError:
            return {"ok": False, "reason": "bad-json", "raw": out[:300]}
        if not isinstance(data, dict):
            return {"ok": False, "reason": "bad-json-shape"}
        hint = UIA_REASON_HINTS.get(str(data.get("reason") or ""))
        if hint and data.get("ok") is False:
            data["hint"] = hint
        return data
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "uia-timeout"}
    except FileNotFoundError:
        return {"ok": False, "reason": "powershell-missing"}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ─── UIA watch (detection) channel (v0.9.71.0+) ───────────────────


# ─── Jev watch gate (v0.9.84.0) ────────────────────────────────────


def _watch_gate_enabled() -> bool:
    v = (os.environ.get("QDRANT_WATCH_GATE", "") or "").strip().lower()
    return v in ("on", "true", "1", "yes")


def _watch_gate_skip_prob() -> float:
    raw = (os.environ.get("QDRANT_WATCH_GATE_SKIP_PROB", "") or "").strip()
    if not raw:
        return 0.7
    try:
        v = float(raw)
    except ValueError:
        return 0.7
    if v < 0.0 or v > 1.0:
        return 0.7
    return v


def _watch_gate_skip() -> dict[str, Any] | None:
    """v0.9.84.0 — the Jev decision pre-filter for watch fires (plan
    §5.3): read the Teams chat text via the UIA channel (deterministic,
    zero-interference, NO LLM) and ask Jev noul
    「目前是否沒有需要使用者本人回覆的新訊息？」— p ≥
    ``QDRANT_WATCH_GATE_SKIP_PROB`` → the whole LLM turn is skipped
    (~12k tokens + 3-5 s saved per false fire).

    Returns a skip result when the fire should be dropped, and ``None``
    whenever the LLM turn must run: gate off, mock mode, short/failed
    UIA read, Jev failure — EVERY failure fails OPEN to the current
    behaviour (plan §7: a missed real message costs more than a wasted
    turn)."""
    if not _watch_gate_enabled() or _assist_mode() != "live":
        return None
    try:
        text = _default_uia_read()
    except Exception as e:
        logger.info(
            "[teams-assist] watch gate: uia read failed (%.120r) — "
            "LLM turn runs", e,
        )
        return None
    if len(text.strip()) < 20:
        return None  # too little context to judge — run the LLM
    from src.agent.rerank import jev_noul

    p, meta = jev_noul(
        "根據這段 Teams 聊天內文，目前是否沒有需要「使用者本人」回覆的"
        "新訊息？（內容與先前相同無新進展、或只有系統/機器人貼文 → "
        "回答 true）",
        text[:6000],
    )
    if p is None:
        logger.info(
            "[teams-assist] watch gate: jev failed (%.160s) — LLM turn",
            str(meta.get("error")),
        )
        return None
    logger.info(
        "[teams-assist] watch gate: skip_p=%.2f (%.2fs)", p,
        meta.get("elapsed_s", 0),
    )
    if p >= _watch_gate_skip_prob():
        return {"gate": "skipped", "p": p,
                "elapsed_s": meta.get("elapsed_s")}
    return None


def _default_uia_read() -> str:
    """Read the Teams chat text via the UIA read channel. Raises on
    failure so the watcher can fall back to the pixel-hash path."""
    out = _uia_ops("read")
    if not out.get("ok"):
        raise RuntimeError(f"uia read failed: {out.get('reason')}")
    text = str(out.get("text") or "")
    if not text.strip():
        raise RuntimeError("uia read empty")
    return text


def _uia_signature(text: str) -> dict[str, Any]:
    """Cheap chat-text signature: CRLF-normalised sha1 + char count."""
    norm = "\n".join(
        line.rstrip()
        for line in text.replace("\r\n", "\n").split("\n")
    )
    return {
        "hash": hashlib.sha1(norm.encode("utf-8")).hexdigest(),
        "chars": len(norm),
    }


# ─── screen watcher (daemon thread inside the host) ───────────────


def _cooldown_gate(state: dict[str, Any]) -> dict[str, Any] | None:
    """Shared fire-rate gate: returns the cooldown skip result when the
    last fire is still inside ``QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL``
    (both the pixel and the UIA watch paths call this)."""
    last_fire = state.get("last_fire_at")
    cooldown_s = _min_fire_interval()
    if last_fire and cooldown_s > 0:
        try:
            elapsed = (
                datetime.now()
                - datetime.fromisoformat(last_fire)
            ).total_seconds()
        except (TypeError, ValueError):
            elapsed = cooldown_s + 1
        if elapsed < cooldown_s:
            return {
                "ok": True, "changed": True, "fired": False,
                "reason": (f"cooldown ({elapsed:.0f}s < "
                           f"{cooldown_s:.0f}s)"),
            }
    return None


def _fire_or_wire(self: "TeamsAssistWatcher",
                  payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one fire through the wired engine (shared by both
    watch paths)."""
    if self._fire is None:
        return {"ok": True, "changed": True, "fired": False,
                "reason": "no fire engine wired"}
    try:
        result = self._fire(payload)
    except Exception as e:
        logger.exception("[teams-assist] fire failed: %r", e)
        return {"ok": False, "changed": True, "fired": False,
                "error": repr(e)}
    return {"ok": True, "changed": True, "fired": True,
            "result": result if isinstance(result, dict) else None}


class TeamsAssistWatcher:
    """Cheap screenshot-hash poll loop. ``grab`` is injectable (mock
    mode / tests); every tick: grab → signature → skip conditions →
    fire callback once with the change payload."""

    def __init__(self, grab: Callable[[], bytes] | None = None,
                 fire: Callable[[dict], Any] | None = None,
                 uia_read: Callable[[], str] | None = None):
        self._grab = grab or (
            _mock_grab if _assist_mode() == "mock" else _default_grab
        )
        self._fire = fire
        self._uia_read = uia_read or _default_uia_read
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # -- lifecycle (OnvifLocalHost watch-thread shape) -------------

    @property
    def is_running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return False
            if not _allow_chats():
                logger.warning(
                    "[teams-assist] watch refused: "
                    "TEAMS_ASSIST_ALLOW_CHATS is empty (fail-closed)",
                )
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True,
                name="teams-assist-watch",
            )
            self._thread.start()
            state = _load_state()
            state["watch"] = {
                "running": True,
                "interval_s": _poll_interval(),
                "watch_mode": _watch_mode(),
                "started_at": _now(), "stopped_at": None,
            }
            state["paused"] = False
            state["grab_failures"] = 0
            _save_state(state)
            return True

    def stop(self) -> bool:
        with self._lock:
            t, self._thread = self._thread, None
            self._stop.set()
        if t is not None:
            try:
                t.join(timeout=3.0)
            except RuntimeError:
                pass
            state = _load_state()
            state["watch"]["running"] = False
            state["watch"]["stopped_at"] = _now()
            _save_state(state)
            return True
        return False

    # -- one poll tick (also used directly by tests / the tool) ----

    def poll_once(self) -> dict[str, Any]:
        state = _load_state()
        if state.get("paused"):
            return {"ok": True, "changed": False, "fired": False,
                    "reason": "paused"}
        # v0.9.71.0+: UIA watch mode — hash the CHAT TEXT, not the
        # screen pixels. A UIA read failure falls through to the pixel
        # path below (automatic fallback, never pauses the watch).
        if _watch_mode() == "uia":
            uia = self._poll_uia(state)
            if uia is not None:
                return uia
            state = _load_state()
        try:
            png = self._grab()
        except Exception as e:
            state["grab_failures"] = int(
                state.get("grab_failures") or 0) + 1
            failures = state["grab_failures"]
            if failures >= MAX_GRAB_FAILURES:
                state["paused"] = True
                _audit(state, "auto-paused",
                       reason=f"grab failed ×{failures}",
                       error=repr(e)[:200])
                logger.warning(
                    "[teams-assist] %d consecutive grab failures — "
                    "watch auto-paused: %r", failures, e,
                )
            _save_state(state)
            return {"ok": False, "error": repr(e),
                    "grab_failures": failures}
        state["grab_failures"] = 0
        state["last_check_at"] = _now()
        try:
            sig = _screen_signature(png)
        except Exception as e:
            _save_state(state)
            return {"ok": False, "error": f"signature failed: {e!r}"}
        if sig["uniform"]:
            state["last_hash"] = None
            _save_state(state)
            return {"ok": True, "changed": False, "fired": False,
                    "reason": "uniform-screen (locked/blank?)"}
        if sig["hash"] == state.get("last_hash"):
            _save_state(state)
            return {"ok": True, "changed": False, "fired": False,
                    "reason": "no change"}
        # Screen changed → cooldown gate before burning an LLM turn.
        state["last_hash"] = sig["hash"]
        blocked = _cooldown_gate(state)
        _save_state(state)
        if blocked is not None:
            return blocked
        payload = {
            "source": "teams-assist-watch",
            "mode": _assist_mode(),
            "watch_mode": "screen",
            "ts": _now(),
            "screen_hash": sig["hash"][:12],
        }
        return _fire_or_wire(self, payload)

    def _poll_uia(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """One UIA watch tick (v0.9.71.0+). Returns the poll result, or
        ``None`` when the UIA read failed (the caller falls back to the
        pixel path — failures here never pause the watch)."""
        try:
            text = self._uia_read() or ""
        except Exception as e:
            state["last_uia_error"] = repr(e)[:200]
            _save_state(state)
            logger.debug("[teams-assist] uia watch read failed: %r", e)
            return None
        if not text.strip():
            state["last_uia_error"] = "uia-read-empty"
            _save_state(state)
            return None
        state["last_uia_error"] = None
        state["last_check_at"] = _now()
        sig = _uia_signature(text)
        if sig["hash"] == state.get("last_uia_hash"):
            _save_state(state)
            return {"ok": True, "changed": False, "fired": False,
                    "reason": "no change (uia)"}
        # Chat text changed → cooldown gate before burning an LLM turn.
        state["last_uia_hash"] = sig["hash"]
        blocked = _cooldown_gate(state)
        _save_state(state)
        if blocked is not None:
            return blocked
        payload = {
            "source": "teams-assist-watch",
            "mode": _assist_mode(),
            "watch_mode": "uia",
            "ts": _now(),
            "chat_text_hash": sig["hash"][:12],
            "text_chars": sig["chars"],
        }
        return _fire_or_wire(self, payload)

    def _loop(self) -> None:
        interval = _poll_interval()
        logger.info(
            "[teams-assist] watch started (interval=%.0fs, mode=%s, "
            "watch_mode=%s, auto=%s)", interval, _assist_mode(),
            _watch_mode(), _auto_enabled(),
        )
        while not self._stop.wait(interval):
            try:
                self.poll_once()
            except Exception as e:
                logger.exception(
                    "[teams-assist] poll tick failed: %r", e,
                )
        logger.info("[teams-assist] watch stopped")