"""Agent RULEBOOK — the rulebook-style revisable rule memory (v0.9.124.0+).

Borrowed designs (user: 「self-evolving-agent 譜系報告」):
* **Memento 3 (arXiv 2610.11794) — Rulebook 工件**：agent 以自然語言維護一份
  可修訂規則檔；工具失敗視為 prediction error（observe→reflect→revise）；
  未理解的機制以 ``underspecified`` 標記；每次工具失敗提示可修訂（每回合一次）。
* **Evo² (arXiv 2610.13025) — 證據門**：``rules_revise`` 必須引用證據
  （失敗工具回執 / 事件來源）；缺證據 = fail-closed 拒絕。
* **建議 ④（git 版本化）** — zero-dep snapshot ring：每次寫入先快照、
  ``rules_history`` 可 diff、``rules_rollback`` 可回滾（不需要真 git repo）。

檔案 = ``system_rules.md`` 的兄弟檔 ``agent_rules.md``（READ sandbox 預設）；
``QDRANT_RULEBOOK_FILE`` 可指向任意路徑（支援 ``~``）。每條規則是
一個 ``### [id] title`` 區塊；每次寫入 atomic（tmp + os.replace）且寫入前
先快照到 ``rulebook_history/`` ring（``QDRANT_RULEBOOK_HISTORY_MAX``，
預設 20）。

區塊 canonical 形（本 module 產出的樣子；人類手編同樣合法）::

    ### [r3] web_fetch 打 x.com 會失敗 — 改用 firefox_craw
    規則內文（多行）……
    - evidence: 2026-10-14 web_fetch('https://x.com/…') → 404（工具回執）
    - updated:  2026-10-14T20:00:00+08:00

id 自增 = 現有最大 ``[rN]`` 的 N + 1。規則體可標 ``underspecified``
（Memento 語意：機制未明）。

Env:
* ``QDRANT_RULEBOOK_ENABLED``     預設 on（off = prompt 不附、工具拒絕）
* ``QDRANT_RULEBOOK_FILE``        預設 <READ sandbox>/agent_rules.md
* ``QDRANT_RULEBOOK_MAX_CHARS``   預設 4000（prompt 注入截斷保護）
* ``QDRANT_RULEBOOK_SUGGEST``     預設 on（工具失敗 prediction-error 提示）
* ``QDRANT_RULEBOOK_HISTORY_MAX`` 預設 20（snapshot ring 深度）
* ``QDRANT_RULEBOOK_MEMORY``      預設 on（規則事件 sink memory，store-
  NOT-recall kind=rule-lesson）

Never-raise：讀取失敗回空字串（prompt byte-identical）；寫入失敗回
error dict（repo thin-host 慣例），只有「驗證失敗」（缺 title/缺
evidence）時回 error。
"""
from __future__ import annotations

import difflib
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

__all__ = [
    "DEFAULT_BASENAME", "rulebook_enabled", "rulebook_hint_enabled",
    "rulebook_memory_enabled", "rulebook_path", "load_rulebook",
    "parse_rules", "rules_add", "rules_revise", "rules_rollback",
    "rules_history", "hint_on_failure", "remember_rule_event",
    "rulebook_telemetry_enabled", "note_tool_error",
    "reset_rulebook_cache",
]

DEFAULT_BASENAME = "agent_rules.md"
_HISTORY_DIRNAME = "rulebook_history"
_EVIDENCE_MIN = 4  # chars — a bare "!" is not evidence (fail-closed gate)
_DIFF_CAP = 2500

_HEADER = (
    "# Agent Rulebook\n\n"
    "agent 維護的規則簿（rules_add / rules_revise — revise 必須引用證據；\n"
    "rules_rollback 可回滾；快照 ring 在 rulebook_history/）。\n"
    "跨工具習慣、環境事實、失敗教訓都寫在這裡；機制還沒看懂的條目\n"
    "標 ``underspecified``。人類可以直接編輯本檔（LLM 不鎖檔）。\n"
)

_LOCK = threading.Lock()
_CACHE: dict = {"path": None, "mtime": None, "text": None}
# ─── env knobs ────────────────────────────────────────────────────────────
def _flag(name: str, default: str = "on") -> bool:
    return ((os.environ.get(name, default) or default)
            .strip().lower() not in ("off", "0", "no", "false"))


def rulebook_enabled() -> bool:
    return _flag("QDRANT_RULEBOOK_ENABLED")


def rulebook_hint_enabled() -> bool:
    """The tool-failure prediction-error hint (one per turn, agent-side)."""
    return _flag("QDRANT_RULEBOOK_SUGGEST")


def rulebook_memory_enabled() -> bool:
    """kind=rule-lesson memory sink for rule add/revise/rollback events."""
    return _flag("QDRANT_RULEBOOK_MEMORY")


def _max_chars() -> int:
    try:
        v = int(os.environ.get("QDRANT_RULEBOOK_MAX_CHARS", "") or 0)
    except ValueError:
        v = 0
    return v if v > 0 else 4000


def _history_max() -> int:
    try:
        v = int(os.environ.get("QDRANT_RULEBOOK_HISTORY_MAX", "") or 0)
    except ValueError:
        v = 0
    return v if v > 0 else 20


def rulebook_path() -> Path | None:
    """Explicit env path wins; otherwise the sibling of system_rules.md
    inside the filesystem READ sandbox."""
    env = (os.environ.get("QDRANT_RULEBOOK_FILE", "") or "").strip()
    if env:
        try:
            return Path(os.path.expanduser(env))
        except Exception:  # noqa: BLE001 — never raise
            return None
    try:
        from src.mcp_client.host import _resolve_filesystem_root
        return (
            _resolve_filesystem_root("QDRANT_FILESYSTEM_READ_ROOT")
            / DEFAULT_BASENAME
        )
    except Exception:  # noqa: BLE001
        return None


# ─── read (mtime-cached, capped, never raises) ──────────────────────────
def load_rulebook() -> str:
    try:
        path = rulebook_path()
        if path is None or not path.is_file():
            return ""
        with _LOCK:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                return ""
            if (_CACHE["path"] == str(path)
                    and _CACHE["mtime"] == mtime
                    and isinstance(_CACHE["text"], str)):
                return _CACHE["text"]
            try:
                text = path.read_text(
                    encoding="utf-8", errors="replace",
                ).strip()
            except OSError:
                return ""
            cap = _max_chars()
            if len(text) > cap:
                text = (
                    text[:cap]
                    + f"\n…[rulebook truncated at {cap} chars — "
                    "prune with rules_rollback or edit the file]…"
                )
            _CACHE.update(path=str(path), mtime=mtime, text=text)
            return text
    except Exception:  # noqa: BLE001
        return ""


def reset_rulebook_cache() -> None:
    with _LOCK:
        _CACHE.update(path=None, mtime=None, text=None)
# ─── parse ────────────────────────────────────────────────────────────────
_HEAD_RE = re.compile(r"^###\s*\[([^\]\s]+)\]\s*(.*?)\s*$")
_META_RE = re.compile(r"^-\s*(evidence|updated|errors|last_error)\s*:\s*(.*)$")


def parse_rules(text: str) -> list[dict]:
    """Parse the file text into rule dicts.

    Each rule: {id, title, body, evidence, updated, start, end} where
    start/end are the 0-based line indexes of the block (end exclusive,
    covering the whole block up to — not including — the next ``### [``).
    """
    rules: list[dict] = []
    cur: dict | None = None
    lines = (text or "").splitlines()
    for i, raw in enumerate(lines):
        m = _HEAD_RE.match(raw)
        if m:
            if cur is not None:
                cur["end"] = i
                rules.append(cur)
            cur = {
                "id": m.group(1),
                "title": m.group(2),
                "body": [],
                "evidence": "",
                "updated": "",
                "errors": 0,
                "last_error": "",
                "start": i,
                "end": i,
            }
            continue
        if cur is None:
            continue
        mm = _META_RE.match(raw.strip())
        if mm:
            key, val = mm.group(1), mm.group(2).strip()
            if key == "evidence" and not cur["evidence"]:
                cur["evidence"] = val
            elif key == "updated" and not cur["updated"]:
                cur["updated"] = val
            elif key == "errors":
                try:
                    cur["errors"] = max(0, int(val))
                except ValueError:
                    cur["errors"] = 0
            elif key == "last_error" and not cur["last_error"]:
                cur["last_error"] = val
            else:
                cur["body"].append(raw)
        else:
            cur["body"].append(raw)
    if cur is not None:
        cur["end"] = len(lines)
        rules.append(cur)
    for r in rules:
        r["body"] = "\n".join(r["body"]).strip("\n")
    return rules


def _next_id(rules: list[dict]) -> str:
    mx = 0
    for r in rules:
        m = re.match(r"^[Rr](\d+)$", r["id"])
        if m:
            mx = max(mx, int(m.group(1)))
    return f"r{mx + 1}"


# ─── atomic write + snapshot ring ────────────────────────────────────────
def _now_iso() -> str:
    try:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return datetime.now().isoformat(timespec="seconds")


def _snapshot_file(path: Path):
    """Copy the current file into the history ring; return the snapshot
    path (None = best effort)."""
    try:
        if not path.is_file():
            return None
        import shutil

        hdir = path.parent / _HISTORY_DIRNAME
        hdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snap = hdir / (
            f"{path.stem}_{ts}_{uuid.uuid4().hex[:4]}{path.suffix}"
        )
        shutil.copy2(str(path), str(snap))
        snaps = sorted(hdir.glob("*" + path.suffix))
        cap = _history_max()
        while len(snaps) > cap:
            victim = snaps.pop(0)
            try:
                victim.unlink()
            except OSError:
                pass
        return snap
    except Exception:  # noqa: BLE001
        return None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{uuid.uuid4().hex[:6]}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _diff(prev: str, cur: str) -> str:
    try:
        d = "\n".join(difflib.unified_diff(
            (prev or "").splitlines(),
            (cur or "").splitlines(),
            fromfile="previous", tofile="current", lineterm="",
        ))
        return d[:_DIFF_CAP]
    except Exception:  # noqa: BLE001
        return ""


# ─── events sink (kind=rule-lesson, store-NOT-recall) ───────────────────
def remember_rule_event(text: str, source: str = "rulebook") -> None:
    """Fire-and-forget thread; never raises; gated by
    QDRANT_RULEBOOK_MEMORY + QDRANT_MEMORY_ENABLED."""
    if not (rulebook_memory_enabled() and text):
        return

    def _work():
        try:
            from src.agent.memory_store import _shared_store, memory_enabled
            if not memory_enabled():
                return
            _shared_store().remember(
                str(text)[:2000], kind="rule-lesson", source=source,
            )
        except Exception:  # noqa: BLE001 — bookkeeping must not break
            pass

    try:
        threading.Thread(
            target=_work, daemon=True, name="rulebook-lesson",
        ).start()
    except Exception:  # noqa: BLE001
        pass
# ─── operations (the thin host below calls these) ───────────────────────
def rules_add(title: str, text: str, evidence: str | None = None) -> dict:
    title = str(title or "").strip()
    text = str(text or "").strip()
    ev = str(evidence or "").strip()
    if not rulebook_enabled():
        return {"error": "rulebook disabled (QDRANT_RULEBOOK_ENABLED=off)"}
    if not title:
        return {"error": "title is required."}
    if not text:
        return {"error": "text is required (the rule content)."}
    path = rulebook_path()
    if path is None:
        return {"error": "rulebook path unresolvable (env not set?)"}
    try:
        with _LOCK:
            cur = (
                path.read_text(encoding="utf-8", errors="replace")
                if path.is_file() else ""
            )
            rules = parse_rules(cur)
            rid = _next_id(rules)
            _snapshot_file(path)
            block = f"\n\n### [{rid}] {title}\n{text}\n"
            if ev:
                block += f"- evidence: {ev}\n"
            block += f"- updated:  {_now_iso()}\n"
            new = (
                (cur.rstrip("\n") + "\n" + block)
                if cur.strip() else (_HEADER + block.lstrip("\n"))
            )
            _write(path, new)
        reset_rulebook_cache()
        remember_rule_event(
            f"rule added [{rid}] {title}：「{text[:160]}」",
            source=f"rulebook:{rid}",
        )
        return {
            "ok": True,
            "add": rid,
            "title": title,
            "evidence": ev or "(none)",
            "path": path.as_posix(),
            "note": (
                "rulebook re-rides the system prompt on the NEXT turn — "
                "no restart. Revisions need evidence; rollback via "
                "rules_rollback."),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"rules_add failed: {e!r}"}


def rules_revise(rule_id: str, text: str, evidence: str) -> dict:
    """Replace one rule's body + evidence + updated stamp.

    EVIDENCE IS REQUIRED (fail-closed — the evidence gate applied to
    rule revisions themselves)."""
    rid = str(rule_id or "").strip().lower()
    text = str(text or "").strip()
    ev = str(evidence or "").strip()
    if not rulebook_enabled():
        return {"error": "rulebook disabled (QDRANT_RULEBOOK_ENABLED=off)"}
    if not rid:
        return {"error": "rule_id is required (e.g. r3)."}
    if not text:
        return {"error": "text is required (the revised rule content)."}
    if len(ev) < _EVIDENCE_MIN:
        return {
            "error": (
                "evidence is REQUIRED for rules_revise (fail-closed) — "
                "cite the failing tool call / event that broke the old "
                "rule, e.g. '2026-10-14 web_fetch(x.com) → 404'."
            ),
            "got": ev,
        }
    path = rulebook_path()
    if path is None:
        return {"error": "rulebook path unresolvable (env not set?)"}
    try:
        with _LOCK:
            cur = (
                path.read_text(encoding="utf-8", errors="replace")
                if path.is_file() else ""
            )
            lines = cur.splitlines()
            rules = parse_rules(cur)
            target = next(
                (r for r in rules if r["id"].lower() == rid), None,
            )
            if target is None:
                return {
                    "error": f"rule {rid} not found.",
                    "known_ids": [r["id"] for r in rules][:40],
                }
            _snapshot_file(path)
            stamp = _now_iso()
            new_block = [f"### [{target['id']}] {target['title']}"]
            new_block.extend(text.splitlines())
            if ev:
                new_block.append(f"- evidence: {ev}")
            new_block.append(f"- updated:  {stamp}")
            out = (
                lines[:target["start"]]
                + new_block
                + lines[target["end"]:]
            )
            _write(path, "\n".join(out).rstrip("\n") + "\n")
        reset_rulebook_cache()
        remember_rule_event(
            f"rule revised [{rid}] evidence={ev[:160]}", source=rid,
        )
        return {
            "ok": True,
            "revise": target["id"],
            "title": target["title"],
            "evidence": ev,
            "path": path.as_posix(),
            "note": ("the old version is kept in rulebook_history/ — "
                     "rules_rollback reverts if the revision misses."),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"rules_revise failed: {e!r}"}


def rules_rollback(steps: int = 1) -> dict:
    """Restore the file from the Nth most recent snapshot (default 1 =
    the state BEFORE the latest write). The current file is snapshotted
    first — nothing is ever lost."""
    path = rulebook_path()
    if path is None:
        return {"error": "rulebook path unresolvable (env not set?)"}
    try:
        with _LOCK:
            hdir = path.parent / _HISTORY_DIRNAME
            snaps = sorted(
                hdir.glob("*" + path.suffix),
                key=lambda f: f.stat().st_mtime, reverse=True,
            ) if hdir.is_dir() else []
            if not snaps:
                return {
                    "error": (
                        "no snapshots yet — nothing to roll back (the "
                        "ring is written on every rules_add/revise)."
                    ),
                }
            try:
                n = max(1, min(int(steps or 1), len(snaps)))
            except (TypeError, ValueError):
                n = 1
            src = snaps[n - 1]
            cur = (
                path.read_text(encoding="utf-8", errors="replace")
                if path.is_file() else ""
            )
            _snapshot_file(path)  # backup current before restoring
            restored = src.read_text(encoding="utf-8", errors="replace")
            _write(path, restored)
        reset_rulebook_cache()
        if len(cur) > 60:
            remember_rule_event(
                f"rulebook rollback ×{n} from {src.name}",
                source="rulebook",
            )
        return {
            "ok": True,
            "steps": n,
            "restored_from": src.as_posix(),
            "diff": _diff(cur, restored),
            "note": "the pre-restore state was snapshotted first.",
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"rules_rollback failed: {e!r}"}
def rules_history(limit: int = 10) -> dict:
    path = rulebook_path()
    if path is None:
        return {"error": "rulebook path unresolvable (env not set?)"}
    try:
        out: list[dict] = []
        if path.is_file():
            out.append({
                "file": "current",
                "chars": len(
                    path.read_text(encoding="utf-8", errors="replace")
                ),
                "mtime": datetime.fromtimestamp(
                    path.stat().st_mtime,
                ).isoformat(timespec="seconds"),
            })
        hdir = path.parent / _HISTORY_DIRNAME
        snaps = (
            sorted(hdir.glob("*" + path.suffix),
                   key=lambda f: f.stat().st_mtime, reverse=True)
            if hdir.is_dir() else []
        )
        try:
            lim = max(1, min(int(limit or 10), 50))
        except (TypeError, ValueError):
            lim = 10
        for f in snaps[:lim]:
            out.append({
                "file": f.as_posix(),
                "chars": f.stat().st_size,
                "mtime": datetime.fromtimestamp(
                    f.stat().st_mtime,
                ).isoformat(timespec="seconds"),
            })
        latest_diff = ""
        if snaps and path.is_file():
            cur = path.read_text(encoding="utf-8", errors="replace")
            old = snaps[0].read_text(encoding="utf-8", errors="replace")
            latest_diff = _diff(old, cur)
        return {
            "ok": True,
            "path": path.as_posix(),
            "snapshots": len(snaps),
            "entries": out,
            "latest_diff": latest_diff,
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"rules_history failed: {e!r}"}


# ─── prediction-error hint (the Memento observe hook) ───────────────────
_HINT_TEXT = (
    "[rules] this failed tool call is a PREDICTION ERROR (Memento "
    "observe→reflect→revise). If it looks SYSTEMATIC (a wrong rule "
    "about the environment, not a one-off typo), call rules_add (new "
    "rule) or rules_revise (fix an existing rule) — REVISE must cite "
    "THIS failure as its evidence (fail-closed). The rulebook rides "
    "the system prompt on every later turn. One hint per turn — do "
    "not spam."
)



def rulebook_telemetry_enabled() -> bool:
    """v0.9.127.0 - per-rule failure telemetry (Evo2 prediction-error
    bookkeeping, default on): every FAILED tool call bumps ``errors``
    on every rule revision currently in force, so rollback / promotion
    decisions carry quantitative evidence.
    ``QDRANT_RULEBOOK_TELEMETRY=off`` opts out."""
    return _flag("QDRANT_RULEBOOK_TELEMETRY")


def note_tool_error() -> dict | None:
    """Bump ``errors`` / ``last_error`` on every in-force rule.

    Telemetry writes BYPASS the snapshot ring on purpose - counting is
    bookkeeping, not a rule revision, and must not churn history.
    Never raises (``None`` = disabled / unresolvable / no rules)."""
    if not (rulebook_enabled() and rulebook_telemetry_enabled()):
        return None
    path = rulebook_path()
    if path is None:
        return None
    try:
        with _LOCK:
            cur = (path.read_text(encoding="utf-8", errors="replace")
                   if path.is_file() else "")
            rules = parse_rules(cur)
            if not rules:
                return None
            lines = cur.splitlines()
            head = "\n".join(lines[:rules[0]["start"]]).rstrip("\n")
            stamp = _now_iso()
            blocks = []
            for r in rules:
                blk = ["### [%s] %s" % (r["id"], r["title"])]
                if r["body"]:
                    blk.append(str(r["body"]).strip("\n"))
                if r["evidence"]:
                    blk.append("- evidence: %s" % r["evidence"])
                if r["updated"]:
                    blk.append("- updated:  %s" % r["updated"])
                blk.append("- errors: %d" % (int(r.get("errors") or 0) + 1))
                blk.append("- last_error: %s" % stamp)
                blocks.append("\n".join(blk))
            _write(path, head + "\n\n\n"
                   + "\n\n".join(blocks) + "\n")
        reset_rulebook_cache()
        return {
            "ok": True,
            "rules_bumped": len(rules),
            "note": ("errors bumped on every in-force rule; no ring "
                     "snapshot (bookkeeping, not a revision)"),
        }
    except Exception as e:  # noqa: BLE001 - bookkeeping never breaks
        logger.debug("[rulebook] note_tool_error failed: %r", e)
    return None

def hint_on_failure() -> str | None:
    if not (rulebook_enabled() and rulebook_hint_enabled()):
        return None
    return _HINT_TEXT