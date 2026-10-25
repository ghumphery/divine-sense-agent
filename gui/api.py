"""API bridge exposed to the WebView frontend.

Mirrors the CLI sub-commands 1:1 (see ``cli.py``). Each method is callable
from JavaScript via ``window.pywebview.api.<method>(...)``. Errors are
caught and returned as ``{"error": "..."}`` dicts so the frontend can
display them in the status bar instead of crashing the window.

The Qdrant / Agent / Session objects are lazily initialised so import
errors (e.g. server down on startup) don't kill the window — they're
surfaced the first time the user clicks a button.

Every method emits structured log lines to ``gui/app.log`` (rotating
2 MB × 3 backups). Failures log full traceback via ``logger.error``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any

from src.config import config

# ─── Clipboard paste image (v0.9.29.0+) ─────────────────────────
UPLOAD_DIR_NAME = "uploads"
_PASTE_B64_MAX = 40_000_000  # ~30 MB decoded — clipboard sanity cap


def _raw_base64_ok(s: str) -> bool:
    return bool(s) and len(s) >= 8


def _sniff_image(data: bytes) -> str | None:
    """Magic-byte sniff → 'png' | 'jpg' | None (PNG/JPEG only)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    return None


def _sanitize_paste_name(filename: str, kind: str) -> str:
    """Basename-only, keep [A-Za-z0-9._-]; force the sniffed ext."""
    base = os.path.basename(str(filename or "")).strip()
    keep = [c if (c.isalnum() and c.isascii()) or c in "._-" else "_"
            for c in base]
    clean = "".join(keep).strip("._-") or "paste"
    if len(clean) > 64:
        clean = clean[:64]
    ext = os.path.splitext(clean)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        clean = clean + (".png" if kind == "png" else ".jpg")
    return clean


# ─── Chat inline images (v0.9.30.0+) ─────────────────────────────
_IMG_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
_READ_IMAGE_MAX_BYTES = 20 * 1024 * 1024  # 20 MB per displayed image

# ─── Chat voice bridge (v0.9.68.0+) ──────────────────────────────
# MediaRecorder blob → STT（voice_stt）與回覆文字 → TTS（tts_speak）。
# SpeechClient（src/agent/speech.py）共用 Telegram 語音的 OpenRouter
# 音訊端點 + ffmpeg 橋接；webm/mp4 容器由 speech.py 嗅探 + 轉碼。
_TTS_UI_CACHE: "dict[str, dict]" = {}      # sha1(text) → payload（LRU）
_TTS_UI_CACHE_MAX = 16


def _ui_voice_max_b64() -> int:
    """``QDRANT_UI_VOICE_MAX_MB`` — base64 size cap for one chat
    voice-recording upload (default 8 MB ≈ 6 MB raw audio; floor 1)."""
    try:
        mb = int((os.environ.get("QDRANT_UI_VOICE_MAX_MB", "") or "8")
                 .strip() or "8")
    except ValueError:
        mb = 8
    return max(1, mb) * 1024 * 1024


def _image_magic_ok(data: bytes, mime: str) -> bool:
    """Light magic-byte verification for the inline-image bridge.

    Extension → mime mapping is trusted for GIF/WebP/BMP (cheap),
    PNG/JPEG/WebP/BMP magic bytes are checked so a renamed text file
    never reaches the chat bubble as a broken image.
    """
    if mime == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if mime == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if mime == "image/gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if mime == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if mime == "image/bmp":
        return data[:2] == b"BM"
    return True


# GUI logging — must be set up before any other gui.* import so the
# file handler is wired before we start emitting log lines.
from gui.logger import setup_logging
setup_logging()
logger = logging.getLogger("gui.api")

# Force UTF-8 on Windows (Qdrant / Ollama responses contain CJK).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass


def _err(msg: str) -> dict[str, Any]:
    """Wrap an error so the JS side can ``if ('error' in result) ...``."""
    return {"error": str(msg)}


# ─── readiness probe plumbing (v0.9.112.1) ───────────────────────────
# The splash rows time out at 5 s (SPLASH_CHECK_TIMEOUT_MS in app.js),
# so the qdrant stats check must be BOUNDED: run it in a tiny shared
# thread-pool; on timeout fall back to a single urllib ``/readyz`` GET
# (no qdrant-client construction). See get_readiness for the full why.

_READINESS_STATS_TIMEOUT_S = 2.5
_STATS_EXECUTOR: "Any | None" = None


def _readiness_executor():
    """Shared executor (max 2 workers) so splash re-polls don't spawn
    threads unboundedly. Created lazily; a leaked build thread after a
    timeout is FINE — it warms the store singleton for later polls."""
    global _STATS_EXECUTOR
    from concurrent.futures import ThreadPoolExecutor
    import threading

    with threading.Lock():
        if _STATS_EXECUTOR is None:
            _STATS_EXECUTOR = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="readiness-stats")
        return _STATS_EXECUTOR


def _readyz_probe(timeout_s: float = 2.0) -> bool:
    """ONE urllib GET ``<QDRANT_URL>/readyz`` — the lightweight
    fallback probe (no QdrantStore, no qdrant-client). True iff 2xx.
    Any failure (down / wrong config / slow) is just ``False``."""
    try:
        from src.config import config as cfg

        base = (cfg.QDRANT_URL or "").rstrip("/")
        if not base:
            return False
        import urllib.request

        with urllib.request.urlopen(
                base + "/readyz", timeout=timeout_s) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


# v0.9.14.1: the GUI footer / splash previously showed a hardcoded
# '0.8.0' that silently drifted out of sync (pyproject.toml was bumped
# on every release, this literal never was). pyproject.toml is the
# documented single source of truth (ARCHITECTURE.md App. B), so read
# the version from it at import. Plain regex — not tomllib — so it
# also works on Python 3.10. Any failure falls back to the last known
# literal; this must never break GUI startup.
def _app_version() -> str:
    try:
        pp = Path(__file__).resolve().parents[1] / "pyproject.toml"
        m = re.search(
            r'^version\s*=\s*"([^"]+)"',
            pp.read_text(encoding="utf-8"),
            re.M,
        )
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.9.14.1"


_APP_VERSION = _app_version()

class Api:
    """JS-callable wrapper around existing CLI handlers.

    Lifecycle:
        * Constructor does NOT touch the network — every method that
          needs Qdrant / LLM uses its own lazily-cached handle.
        * ``get_status()`` is the single source of truth the frontend
          polls at startup to render the footer.
    """

    # ─── lazy handles (initialised on first use) ────────────────────
    _store = None
    _retriever = None
    _agent = None
    _session_mgr = None
    _embedder = None
    _diagrams_manager = None
    # v0.9.11.1+: one-shot flag — the background prewarm thread
    # (mcp_manager build ~15 s + provider resolution) must only be
    # kicked on the very first get_status poll of a GUI session.
    _prewarm_started = False
    # v0.9.80.1 — set True (in a finally) when the background prewarm
    # thread finishes building agent + mcp_manager; the readiness mcp
    # slot polls this instead of blocking the request.
    _prewarm_done = False
    # v0.9.16.3 — user log 2026-09-06: in web mode TWO pages booted at
    # once (desktop auto-open + an open phone tab) and two get_status
    # polls raced `_kick_prewarm`'s UNLOCKED flag check → two prewarm
    # threads → both ran `_get_agent()`'s UNLOCKED check-then-set →
    # two Agent instances → `McpManager.from_config` ran TWICE (every
    # server connected twice in app.log; the losing Agent's stdio
    # subprocesses linger). The DCL on `Agent.mcp_manager` is
    # per-instance and cannot protect across two Agents — guard the
    # lazy init + the flag with one class-level lock instead.
    _agent_init_lock = threading.Lock()

    # ─── status / diagnostics ──────────────────────────────────────
    def get_status(self) -> dict[str, Any]:
        """Return health snapshot for the footer.

        v0.8.2+: Qdrant-unreachable is logged once per state change,
        not on every poll. ``_qdrant_last_ok`` tracks the last
        known connectivity state so a long Qdrant outage produces
        one WARNING at the start of the outage, then DEBUG-only,
        then one INFO when it recovers.
        """
        logger.debug("get_status() called")
        out: dict[str, Any] = {
            "version": _APP_VERSION,
            "qdrant_url": config.QDRANT_URL,
            "collection": config.QDRANT_COLLECTION,
            "embedding_dim": config.EMBEDDING_DIM,
            "llm_provider": _detect_provider(),
            "llm_model": _detect_model(),
            "ollama_key_set": bool(config.OLLAMA_API_KEY),
            "openai_key_set": bool(config.OPENAI_API_KEY),
            "qdrant_ok": False,
            "qdrant_error": None,
        }
        try:
            store = self._get_store()
            stats = store.get_stats()
            out["qdrant_ok"] = True
            out["qdrant_stats"] = stats
            # State transition: unreachable -> reachable.
            if getattr(self, "_qdrant_last_ok", None) is False:
                logger.info(
                    "get_status: Qdrant recovered (%s points in '%s')",
                    stats.get("num_entities"),
                    stats.get("collection"),
                )
            else:
                logger.debug(
                    "get_status: Qdrant OK (%s points)",
                    stats.get("num_entities"),
                )
            self._qdrant_last_ok = True
        except Exception as e:
            out["qdrant_error"] = str(e)
            # State transition: reachable -> unreachable (or first
            # ever poll). Log once, then drop to DEBUG until the
            # outage ends — otherwise get_status at 30s cadence
            # floods the log when Qdrant is down.
            if getattr(self, "_qdrant_last_ok", True):
                logger.warning(
                    "get_status: Qdrant unreachable: %r "
                    "(further failures will log at DEBUG)",
                    e,
                )
            else:
                logger.debug(
                    "get_status: Qdrant still unreachable: %r", e,
                )
            self._qdrant_last_ok = False

        # v0.8.0+ MCP tool surface snapshot for the GUI footer /
        # Settings tab. Cheap — just connects lazily.
        try:
            agent = self._get_agent()
            # v0.9.11.1+: one-shot background prewarm — the first
            # get_status poll (GUI boot) starts building the
            # McpManager tool surface in a daemon thread so the
            # first chat turn doesn't pay the ~15 s build + the
            # provider resolution.
            self._kick_prewarm()
            mgr = agent.mcp_manager
            out["mcp_tool_count"] = len(mgr.list_tools())
            out["mcp_servers"] = sorted(
                set(t.server_name for t in mgr.list_tools())
            )
            out["mcp_failed"] = dict(mgr.failed) if mgr.failed else {}
        except Exception as e:
            out["mcp_tool_count"] = 0
            out["mcp_servers"] = []
            out["mcp_error"] = str(e)

        # v0.9.47.0 Phase 3: running background-task count for the
        # chat toolbar badge (in-memory registry read — never raises
        # into the poll loop).
        try:
            from src.agent import task_runner
            out["running_tasks"] = task_runner.list_tasks()["running"]
        except Exception:
            out["running_tasks"] = 0
        return out

    # ─── readiness checks (v0.8.7 splash screen) ─────────────────
    def get_readiness(self) -> dict[str, Any]:
        """Aggregate health summary used by the boot splash overlay.

        Returns three independent ``{ok, detail}`` slots so the GUI
        can render a per-row checklist (✅/❌/⏳) and skip anything
        still pending. Each check has its own try/except — a single
        failure does NOT cascade into the others. The endpoint also
        reports an overall ``ready`` flag (true iff all three pass)
        so a paranoid caller can short-circuit.

        v0.8.7: designed to be polled by ``app.js`` while the splash
        overlay is up. Cheap (reuses the same ``QdrantStore`` /
        ``Agent`` lazy singletons that the main ``get_status``
        endpoint touches). Returns plain JSON; no streaming, no
        Server-Sent Events — the client runs the show via timers.
        """
        out: dict[str, Any] = {
            "version": _APP_VERSION,
            "checks": {
                "backend": {"ok": True,  "detail": "self"},
                "qdrant":  {"ok": False, "detail": None},
                "llm":     {"ok": False, "detail": "no keys"},
                "mcp":     {"ok": False, "detail": None},
            },
            "ready": False,
        }
        # 1. Backend = this very endpoint responded. Trivially true
        #    when the call reaches us; we surface a single-line
        #    detail so the splash row reads "self" (i.e. self-test).
        try:
            out["checks"]["backend"]["detail"] = (
                f"py {sys.version_info.major}.{sys.version_info.minor}"
            )
        except Exception:
            pass

        # 2. Qdrant — the readiness endpoint must answer FAST (the
        #    splash rows time out at 5 s). v0.9.80.1 made the mcp row
        #    non-blocking, but the qdrant/llm rows still
        #    "piggy-back" on the FIRST response: on a cold boot the
        #    prewarm thread is saturating the CPU while building the
        #    MCP fleet (16 s measured on this box) AND the first
        #    ``_get_store()`` costs ~2.7 s (qdrant-client connect +
        #    collection checks) — together they regularly pushed the
        #    first get_readiness past its own 5 s deadline, so BOTH
        #    the qdrant AND llm rows printed 「逾時」 on every
        #    restart (v0.9.112.1 report). Fix: run the stats probe in
        #    a thread-pool with a HARD timeout; on timeout fall back
        #    to a single lightweight urllib ``/readyz`` probe (the
        #    server being up is the actual readiness question —
        #    counts are nice-to-have and arrive with every later
        #    poll once the store singleton is warm). A raise (import
        #    error, connection refused) surfaces verbatim like
        #    before. ``/readyz`` never builds the store.
        try:
            out["checks"]["qdrant"] = self._qdrant_stats_check()
        except Exception as e:  # belt-and-braces — helper never raises
            out["checks"]["qdrant"]["detail"] = str(e)[:200]

        # 3. LLM keys — either Ollama key OR OpenAI key set is the
        #    baseline; users have occasionally added other providers
        #    via a custom ``base_url`` (Ollama-compatible OpenAI
        #    clients) so we accept EITHER key signal. The exact
        #    provider that wins is determined lazily by ``Agent``
        #    itself; the splash only needs to know "something is
        #    configured", not which one.
        try:
            found: list[str] = []
            if (os.environ.get("OLLAMA_API_KEY")
                    or os.environ.get("OLLAMA_BASE_URL")):
                found.append("ollama")
            if (os.environ.get("OPENAI_API_KEY")
                    or os.environ.get("OPENAI_BASE_URL")):
                found.append("openai")
            # v0.9.112.1 — the 5-provider chain (v0.9.18/19.0) also
            # counts as "something configured"; a user whose primary
            # is openrouter/siliconflow/openai-compat used to see a
            # misleading ❌ LLM API 金鑰 row.
            if os.environ.get("OPENROUTER_API_KEY"):
                found.append("openrouter")
            if os.environ.get("SILICONFLOW_API_KEY"):
                found.append("siliconflow")
            if (os.environ.get("OPENAI_COMPAT_API_KEY")
                    and os.environ.get("OPENAI_COMPAT_BASE_URL")):
                found.append("openai-compat")
            if found:
                out["checks"]["llm"] = {
                    "ok": True,
                    "detail": " + ".join(found),
                }
            else:
                out["checks"]["llm"]["detail"] = (
                    "no OLLAMA_API_KEY / OPENAI_API_KEY env vars"
                )
        except Exception as e:
            out["checks"]["llm"]["detail"] = str(e)[:200]

        # 4. MCP initialization (v0.9.80.0/80.1) — NON-BLOCKING: the
        #    v0.9.80.0 synchronous build stalled the whole response for
        #    15-25 s, which pushed the FAST qdrant/llm rows past their
        #    5 s per-row timeout (user-visible 「逾時」). Instead: kick
        #    the background prewarm thread (idempotent — it builds
        #    agent + mcp_manager off the chat path) and report its
        #    progress; the splash POLLS this slot until ``done``.
        #    - pending  → {"ok": False, "done": False, "初始化中…"},
        #      EXCLUDED from `ready` (original 3-row contract intact);
        #    - done     → tools count + failed-server list (a failed
        #      server → ok=False, splash shows ❌ but still dismisses);
        #    - agent unbuildable (e.g. no LLM keys — the llm row
        #      already flags it) → skipped, also excluded from ready.
        mcp_ran = False
        try:
            self._kick_prewarm()
            if not getattr(Api, "_prewarm_done", False):
                out["checks"]["mcp"] = {
                    "ok": False, "detail": "初始化中…", "done": False,
                }
            else:
                agent = self._get_agent()
                mgr = agent.mcp_manager  # already built — instant
                tools = mgr.active_tools()
                failed = sorted(str(k) for k in (mgr.failed or {}))
                mcp_ran = True
                detail = f"{len(tools)} tools"
                if failed:
                    detail += f"；失敗: {', '.join(failed)}"
                out["checks"]["mcp"] = {
                    "ok": not failed, "detail": detail, "done": True,
                }
        except Exception as e:
            out["checks"]["mcp"] = {
                "ok": True,
                "detail": f"skipped: {str(e)[:80]}",
                "done": True,
            }

        relevant = [
            slot for key, slot in out["checks"].items()
            if key != "mcp"
            or (slot.get("done") and not str(
                slot.get("detail") or ""
            ).startswith("skipped"))
        ]
        out["ready"] = all(slot["ok"] for slot in relevant)
        out["mcp_ran"] = mcp_ran
        return out

    # ─── readiness: bounded qdrant probe (v0.9.112.1) ──────────────
    def _qdrant_stats_check(self) -> dict[str, Any]:
        """Bounded qdrant readiness probe. stats in a thread-pool with
        a hard timeout → ``/readyz`` fallback on timeout → structured
        error on raise. Never raises (its caller wraps in try anyway).
        Side note: the store build keeps running after a timeout and
        warms the ``Api._store`` singleton, so every later readiness
        poll answers instantly with full counts."""
        from concurrent.futures import TimeoutError as _FTimeout

        try:
            fut = _readiness_executor().submit(
                lambda: self._get_store().get_stats())
            stats = fut.result(timeout=_READINESS_STATS_TIMEOUT_S)
            n = stats.get("num_entities", "?")
            dim = stats.get("dim", stats.get("vector_dim", "?"))
            return {"ok": True, "detail": f"{n} pts / {dim}d"}
        except _FTimeout:
            if _readyz_probe(2.0):
                return {"ok": True,
                        "detail": "server up (readyz; stats pending)"}
            return {"ok": False,
                    "detail": (
                        f"qdrant probe timed out "
                        f"({_READINESS_STATS_TIMEOUT_S:.0f}s) AND "
                        f"/readyz is unreachable")}
        except Exception as e:  # noqa: BLE001 — keep the old shape
            return {"ok": False, "detail": str(e)[:200]}

    # ─── MCP surface (v0.8.0+) ───────────────────────────────────
    def list_mcp_tools(self) -> dict[str, Any]:
        """Return the tool inventory VISIBLE to the Agent (gated).

        v0.9.79.3: uses ``mgr.active_tools()`` — the SAME gated
        surface the LLM's tool loop sees (``MCP_ACTIVE_SERVERS`` +
        zero-match fallback) — instead of the ungated full inventory,
        so the chat UI's 🧰 picker can never list a tool the model
        cannot actually call (the v0.9.13.2 discovery-trap contract
        extended to the frontend).

        Read-only metadata for GUI inspection / debugging. Safe to
        call repeatedly; the underlying McpManager caches its own
        tool listings.
        """
        try:
            agent = self._get_agent()
            mgr = agent.mcp_manager
            tools = mgr.active_tools()
            return {
                "status": "ok",
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "server": t.server_name,
                        "schema": t.input_schema,
                    }
                    for t in tools
                ],
                "failed": dict(mgr.failed),
            }
        except Exception as e:
            return _err(f"list_mcp_tools failed: {e!r}")

    # ─── MCP fleet editor (v0.8.0+) ────────────────────────────
    # These methods back the Admin-tab MCP device editor. The
    # ``config/mcp.json`` file is the source of truth; we read it
    # on demand and write it atomically (tmp + os.replace) on save.
    # ``replace_config`` is invoked immediately so the next chat
    # turn sees the new fleet without restarting the agent.

    DEFAULT_MCP_CONFIG_PATH = "config/mcp.json"

    def _resolve_mcp_config_path(self) -> Path:
        """Find an existing MCP config file to read.

        Priority:
          1. ``Agent.DEFAULT_MCP_CONFIG`` if it exists
          2. ``config/mcp.json`` in the working directory
          3. Fall through to ``_resolve_write_path()``
        """
        from src.agent.agent import Agent
        default = Path(getattr(Agent, "DEFAULT_MCP_CONFIG"))
        if default.exists():
            return default
        cwd_default = Path(self.DEFAULT_MCP_CONFIG_PATH)
        if cwd_default.exists():
            return cwd_default.resolve()
        return self._resolve_write_path()

    def _resolve_write_path(self) -> Path:
        """Pick where to write when there's no existing file.

        Honors ``Agent.DEFAULT_MCP_CONFIG`` so tests can
        monkeypatch the constant to redirect writes to a
        tmp path. Falls back to ``config/mcp.json`` only if the
        Agent default is the literal default value.
        """
        from src.agent.agent import Agent
        default = Path(getattr(Agent, "DEFAULT_MCP_CONFIG"))
        # Always honour whatever the constant points to — even
        # if the file doesn't exist yet.
        return default

    def _read_mcp_config(self) -> dict[str, Any]:
        """Parse the on-disk config. Strip ``_comment`` /
        ``_placeholder`` keys so the editor doesn't render example
        placeholders as live entries.
        """
        path = self._resolve_mcp_config_path()
        if not path.exists():
            return {"servers": []}
        try:
            text = path.read_text(encoding="utf-8")
            cfg = json.loads(text)
        except Exception as e:
            return {
                "_path": str(path),
                "_error": f"parse failed: {e!r}",
                "servers": [],
            }
        if isinstance(cfg, dict):
            raw = cfg.get("servers", []) or []
            cfg["servers"] = [
                s for s in raw
                if not (
                    isinstance(s, dict)
                    and any(k.startswith("_") for k in s.keys())
                )
            ]
            cfg["_path"] = str(path)
        return cfg

    def _validate_server_entry(self, entry: dict) -> str | None:
        """Lightweight validation. Returns ``None`` if OK or an
        error string otherwise.
        """
        if not isinstance(entry, dict):
            return "entry must be a JSON object"
        name = entry.get("name")
        if not name or not isinstance(name, str):
            return 'missing required field "name" (string)'
        transport = (entry.get("transport") or "stdio").lower()
        if transport in ("stdio", "local"):
            cmd = entry.get("command")
            if not (isinstance(cmd, list) and cmd):
                return (
                    'transport "stdio" requires "command" '
                    "(non-empty array)"
                )
        elif transport in ("streamable_http", "http", "http_sse"):
            if not entry.get("url"):
                return (
                    f'transport "{transport}" requires "url" '
                    "(string)"
                )
        elif transport == "sse":
            if not entry.get("url"):
                return 'transport "sse" requires "url" (string)'
        else:
            return (
                f"unknown transport {transport!r}; "
                "use 'stdio' | 'streamable_http' | 'sse'"
            )
        return None

    def _atomic_write_json(self, path: Path, payload: dict) -> None:
        """Write JSON to ``path`` atomically (tmp + os.replace).

        Creates parent directories as needed. Preserves UTF-8
        and uses ``indent=2`` so the file stays human-editable.
        """
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

    # ─── Credentials tab (v0.9.39.0+) ──────────────────────────────

    def list_credentials(self, service: str = "") -> dict[str, Any]:
        """Masked credential-set list for the Credentials tab."""
        try:
            from src.credential_store import CredentialStore

            entries = CredentialStore().list(
                service=str(service or "") or None, masked=True,
            )
            return {
                "status": "ok",
                "count": len(entries),
                "credentials": entries,
            }
        except Exception as e:
            return _err(f"list_credentials failed: {e!r}")

    def save_credential(self, payload: dict) -> dict[str, Any]:
        """Create / replace one credential set (Credentials tab form)."""
        try:
            if not isinstance(payload, dict):
                return _err("payload must be a JSON object")
            from src.credential_store import CredentialStore

            out = CredentialStore().add(
                name=str(payload.get("name") or ""),
                username=str(payload.get("username") or ""),
                password=str(payload.get("password") or ""),
                password_env=str(payload.get("password_env") or ""),
                service=str(payload.get("service") or "generic"),
                notes=str(payload.get("notes") or ""),
                overwrite=bool(payload.get("overwrite") or False),
            )
            if out.get("error"):
                return _err(out["error"])
            return {"status": "ok", **out}
        except Exception as e:
            return _err(f"save_credential failed: {e!r}")

    def remove_credential(self, name: str) -> dict[str, Any]:
        """Delete one credential set."""
        try:
            from src.credential_store import CredentialStore

            name = str(name or "").strip()
            if not name:
                return _err("'name' is required")
            if not CredentialStore().remove(name):
                return _err(f"credential set '{name}' not found")
            return {"status": "ok", "removed": name}
        except Exception as e:
            return _err(f"remove_credential failed: {e!r}")

    def get_mcp_config(self) -> dict[str, Any]:
        """Read the current MCP config for the Admin-tab editor.

        Returns ``{path, exists, servers: [...], parse_error?,
        raw_text}``.
        ``servers`` entries are validated (unknown transports /
        missing fields are flagged) but never auto-corrected —
        the user owns the file. ``raw_text`` is the on-disk file
        contents verbatim (or ``""`` if it does not yet exist)
        so the GUI can offer a "raw JSON edit" mode.
        """
        try:
            cfg = self._read_mcp_config()
            path = cfg.pop("_path", None)
            err = cfg.pop("_error", None)
            servers = cfg.get("servers", []) or []
            annotated: list[dict] = []
            for s in servers:
                if not isinstance(s, dict):
                    continue
                msg = self._validate_server_entry(s)
                annotated.append({"entry": s, "error": msg})
            raw_text = ""
            if path and Path(path).exists():
                try:
                    raw_text = Path(path).read_text(encoding="utf-8")
                except Exception:
                    raw_text = ""
            return {
                "status": "ok",
                "path": path or "",
                "exists": bool(path and Path(path).exists()),
                "servers": annotated,
                "parse_error": err,
                "raw_text": raw_text,
            }
        except Exception as e:
            return _err(f"get_mcp_config failed: {e!r}")

    def _has_agent(self) -> bool:
        """Cheap predicate so save/restart can guard reads."""
        return getattr(Api, "_agent", None) is not None

    def save_mcp_config(self, config_data: dict) -> dict[str, Any]:
        """Save the MCP config and hot-reload the manager.

        ``config_data`` is the full JSON object (same shape as
        ``config/mcp.json``). All ``servers`` entries are
        validated up front; any validation failure aborts the
        save and returns an error.
        """
        try:
            if not isinstance(config_data, dict):
                return _err(
                    "config must be a JSON object with a "
                    "'servers' array"
                )
            servers = config_data.get("servers") or []
            if not isinstance(servers, list):
                return _err("'servers' must be a list")
            errors: list[str] = []
            for i, s in enumerate(servers):
                msg = self._validate_server_entry(s)
                if msg:
                    name = (
                        s.get("name", f"<entry {i}>")
                        if isinstance(s, dict) else f"<entry {i}>"
                    )
                    errors.append(f"{name}: {msg}")
            if errors:
                return _err(
                    "validation failed:\n  - "
                    + "\n  - ".join(errors)
                )
            path = Path(self._resolve_write_path())
            payload = {"servers": servers}
            self._atomic_write_json(path, payload)
            logger.info(
                "save_mcp_config: wrote %d servers to %s",
                len(servers), path,
            )
            # Hot-reload the manager so the next chat_turn sees
            # the new fleet.
            try:
                agent = self._get_agent()
                agent.mcp_manager.replace_config(servers)
            except Exception as e:
                # File saved; the manager reload is best-effort.
                logger.warning(
                    "save_mcp_config: reload failed: %r", e,
                )
            return {
                "status": "ok",
                "path": str(path),
                "saved": len(servers),
                "failed": (
                    dict(agent.mcp_manager.failed)
                    if self._has_agent() else {}
                ),
                "tools": (
                    len(agent.mcp_manager.list_tools())
                    if self._has_agent() else 0
                ),
            }
        except Exception as e:
            return _err(f"save_mcp_config failed: {e!r}")

    def restart_mcp_server(self, server_name: str) -> dict[str, Any]:
        """Close + reconnect a single remote MCP server.

        Useful when the user updates credentials (env vars etc.)
        and wants to apply them without restarting the whole
        agent. Local hosts are untouched.
        """
        try:
            agent = self._get_agent()
            mgr = agent.mcp_manager
            cfg = self._read_mcp_config()
            target = None
            for s in cfg.get("servers", []) or []:
                if (
                    isinstance(s, dict)
                    and s.get("name") == server_name
                ):
                    target = s
                    break
            if target is None:
                return _err(
                    f"server {server_name!r} not found in config"
                )
            # Drop any existing client for this name.
            client = mgr._clients.get(server_name)
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
                mgr._clients.pop(server_name, None)
                mgr._tool_index = {
                    n: sp for n, sp in mgr._tool_index.items()
                    if not (
                        sp.dispatch[0] == "remote"
                        and sp.dispatch[1].server_name
                        == server_name
                    )
                }
            mgr.failed.pop(server_name, None)
            from src.mcp_client.manager import (
                _build_client_from_config,
            )
            client = _build_client_from_config(target)
            if client is None:
                return _err(
                    f"server {server_name!r} failed to build; "
                    f"check config"
                )
            mgr.register_client(client)
            tools_after = [
                t for t in mgr.list_tools()
                if t.server_name == server_name
            ]
            return {
                "status": "ok",
                "server": server_name,
                "tools": len(tools_after),
                "failed": dict(mgr.failed),
            }
        except Exception as e:
            return _err(f"restart_mcp_server failed: {e!r}")

    def get_mcp_server_logs(self, server_name: str) -> dict[str, Any]:
        """Per-server tool inventory + status for the editor card."""
        try:
            agent = self._get_agent()
            mgr = agent.mcp_manager
            tools = [
                t for t in mgr.list_tools()
                if t.server_name == server_name
            ]
            return {
                "status": "ok",
                "server": server_name,
                "connected": server_name in mgr._clients,
                "tool_count": len(tools),
                "tools": [t.name for t in tools],
                "failed_reason": mgr.failed.get(server_name),
            }
        except Exception as e:
            return _err(f"get_mcp_server_logs failed: {e!r}")

    # ─── Clipboard paste image (v0.9.29.0+) ────────────────────────

    def save_pasted_image(self, data_base64: str, filename: str = "") -> dict:
        """Chat paste-image bridge (pywebview GUI mode).

        Decodes a base64 clipboard image and writes it into the
        filesystem WRITE sandbox ``uploads/`` (the same dir the web
        ``POST /upload`` endpoint uses), returning the absolute path
        so the frontend can insert it into the chat input. Magic-byte
        sniffing forces a correct extension (PNG/JPEG only) and the
        usual timestamp + collision-suffix naming is applied.
        """
        try:
            import base64 as _b64

            from src.mcp_client.host import _resolve_filesystem_root

            raw_b64 = str(data_base64 or "").strip()
            if not _raw_base64_ok(raw_b64):
                return {"ok": False, "error": "data_base64 is empty"}
            if len(raw_b64) > _PASTE_B64_MAX:
                return {
                    "ok": False,
                    "error": (
                        "clipboard image too large "
                        f"({len(raw_b64) // 1024} KB base64 > "
                        f"{_PASTE_B64_MAX // 1024} KB cap)"
                    ),
                }
            try:
                data = _b64.b64decode(raw_b64, validate=False)
            except Exception as e:
                return {"ok": False, "error": f"invalid base64: {e!r}"}
            kind = _sniff_image(data)
            if not kind:
                return {
                    "ok": False,
                    "error": "unsupported clipboard image "
                             "(PNG/JPEG only)",
                }
            write_root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT",
            )
            uploads = write_root / UPLOAD_DIR_NAME
            uploads.mkdir(parents=True, exist_ok=True)
            clean = _sanitize_paste_name(filename or "", kind)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target = uploads / f"{stamp}-{clean}"
            n = 0
            while target.exists() and n < 1000:
                n += 1
                target = uploads / f"{stamp}-{n}-{clean}"
            target.write_bytes(data)
            return {
                "ok": True,
                "name": clean,
                "path": target.resolve().as_posix(),
                "relative_path": f"{UPLOAD_DIR_NAME}/{target.name}",
                "size": len(data),
            }
        except Exception as e:
            logger.error(f"save_pasted_image failed: {e!r}")
            return {"ok": False, "error": repr(e)}

    # ─── Chat inline images (v0.9.30.0+) ────────────────────────────

    def read_sandbox_image(self, path: str = "") -> dict:
        """Chat inline-image bridge (pywebview GUI + web shim).

        Reads an image file that lives inside one of the allowed
        sandbox roots (import root ∪ filesystem READ ∪ WRITE — the
        SAME union ``describe_image`` accepts) and returns it as
        base64 + mime so the frontend can render it INLINE inside
        the chat bubble as a ``data:`` URL. Only image extensions
        are served; magic bytes are verified; a 20 MB cap bounds
        the response size. Non-image paths / paths outside the
        roots are refused — this must never become an arbitrary
        file read.
        """
        try:
            import base64 as _b64

            from src.mcp_client.host import _allowed_file_roots, _is_within

            p_str = str(path or "").strip()
            if not p_str:
                return {"ok": False, "error": "path is empty"}
            # Defensive: accept a file:/// URI the frontend may have
            # picked up from a markdown image render.
            low = p_str.lower()
            if low.startswith("file:///"):
                p_str = p_str[8:]
            elif low.startswith("file://"):
                p_str = p_str[7:]
            target = Path(p_str)
            if not target.is_absolute():
                # v0.9.30.1+ — relative paths (e.g. uploads/paste.png)
                # resolve against the filesystem WRITE sandbox; the
                # roots-containment check below still applies, so an
                # escaping relative path is refused.
                from src.mcp_client.host import _resolve_filesystem_root
                try:
                    target = (
                        _resolve_filesystem_root(
                            "QDRANT_FILESYSTEM_WRITE_ROOT") / target
                    )
                except Exception as e:
                    return {
                        "ok": False,
                        "error": (
                            f"relative path without a write root: {e!r}"
                        ),
                    }
            roots = _allowed_file_roots()
            if not any(_is_within(target, r) for r in roots):
                return {
                    "ok": False,
                    "error": "outside the allowed sandbox roots",
                }
            mime = _IMG_MIME_BY_EXT.get(target.suffix.lower())
            if not mime:
                return {
                    "ok": False,
                    "error": "not a displayable image extension: "
                             f"{target.suffix or '(none)'}",
                }
            try:
                resolved = target.resolve()
            except OSError as e:
                return {"ok": False, "error": f"resolve failed: {e!r}"}
            if not resolved.is_file():
                return {"ok": False, "error": "file not found"}
            size = resolved.stat().st_size
            if size > _READ_IMAGE_MAX_BYTES:
                return {
                    "ok": False,
                    "error": (
                        f"image too large ({size // (1024 * 1024)} MB > "
                        f"{_READ_IMAGE_MAX_BYTES // (1024 * 1024)} MB cap)"
                    ),
                }
            data = resolved.read_bytes()
            if not _image_magic_ok(data, mime):
                return {"ok": False, "error": "content is not a valid image"}
            return {
                "ok": True,
                "mime": mime,
                "data_base64": _b64.b64encode(data).decode("ascii"),
                "path": resolved.as_posix(),
                "size": size,
            }
        except Exception as e:
            logger.error(f"read_sandbox_image failed: {e!r}")
            return {"ok": False, "error": repr(e)}

    # ─── Chat voice bridge (v0.9.68.0+) ─────────────────────────────
    # Web + pywebview GUI 雙向語音:mic 錄音 → STT（voice_stt）與
    # 回覆文字 → TTS（tts_speak）。與 Telegram 語音共用
    # src/agent/speech.py 的 SpeechClient（OpenRouter 音訊端點 +
    # ffmpeg 橋接）；瀏覽器 MediaRecorder 的 webm/mp4 容器由
    # speech.py 的嗅探 + 轉碼覆蓋。

    def voice_stt(
        self, audio_b64: str = "", filename: str = "voice.webm",
    ) -> dict[str, Any]:
        """Chat voice-IN bridge: browser MediaRecorder blob → text.

        ``audio_b64`` is base64 (a ``data:audio/webm;base64,…`` prefix
        is tolerated) of the recorded blob; it is decoded, size-capped
        (``QDRANT_UI_VOICE_MAX_MB``, default 8) and handed to
        ``SpeechClient.transcribe`` — NO temp file, NO sandbox write
        (the audio never lands on disk). Unconfigured STT → a clear
        error instead of a crash."""
        try:
            import base64 as _b64

            from src.agent.speech import SpeechClient

            raw = str(audio_b64 or "").strip()
            if raw.lower().startswith("data:"):
                raw = raw.split("base64,", 1)[-1]
            if not raw:
                return {"ok": False, "error": "audio_b64 is empty"}
            max_b64 = _ui_voice_max_b64()
            if len(raw) > max_b64:
                return {
                    "ok": False,
                    "error": (
                        f"audio too large ({len(raw) // 1024} KB "
                        f"base64 > {max_b64 // 1024} KB cap)"
                    ),
                }
            try:
                data = _b64.b64decode(raw, validate=False)
            except Exception as e:
                return {"ok": False, "error": f"invalid base64: {e!r}"}
            if not data:
                return {"ok": False, "error": "decoded audio is empty"}
            sc = SpeechClient()
            if not sc.stt_configured():
                return {
                    "ok": False,
                    "error": (
                        "STT 未設定 — 需要 TELEGRAM_STT_API_KEY"
                        "（或 OPENROUTER_API_KEY）"
                    ),
                }
            fname = str(filename or "").strip() or "voice.webm"
            # Extension allowlist (the filename is advisory —
            # transcribe sniffs the container and overrides).
            allowed_exts = (
                ".webm", ".ogg", ".oga", ".mp3", ".wav", ".m4a",
                ".mp4", ".aac", ".opus", ".flac",
            )
            if not fname.lower().endswith(allowed_exts):
                fname = "voice.webm"
            text = sc.transcribe(data, fname)
            logger.info("voice_stt: ok chars=%d fname=%s",
                        len(text or ""), fname)
            return {"ok": True, "text": text, "bytes": len(data)}
        except Exception as e:
            logger.error("voice_stt FAILED: %r", e)
            # v0.9.127.12 — humanise the error surface: the provider's
            # transient「provider down」is meaningless to a user (the
            # retry + fallback layers already ran before we land here).
            msg = str(e)
            if ("provider down" in msg
                    or "connection" in msg.lower()
                    or "timeout" in msg.lower()
                    or "暫時不可用" in msg):
                friendly = ("語音辨識服務暫時不可用，請稍後再說一次"
                            "（若持續失敗請改用打字）")
            else:
                friendly = msg or repr(e)
            return {"ok": False, "error": friendly}

    def tts_speak(self, text: str = "") -> dict[str, Any]:
        """Chat voice-OUT bridge: reply text → TTS → playable audio.

        ``SpeechClient.synthesize`` returns (mp3|ogg|pcm); the result
        is adapted for the browser/WebView2 ``<audio>`` element via
        ``SpeechClient.to_browser_audio`` (mp3/ogg native, pcm → wav)
        and returned as a base64 ``data:`` URL. A small per-text LRU
        cache (16 entries) avoids re-paying TTS for repeated
        🔊 clicks on the same bubble."""
        try:
            import base64 as _b64

            from src.agent.speech import SpeechClient

            clean = str(text or "").strip()
            if not clean:
                return {"ok": False, "error": "text is empty"}
            key = hashlib.sha1(clean.encode("utf-8", "replace")).hexdigest()
            cached = _TTS_UI_CACHE.get(key)
            if cached is not None:
                cached = dict(cached)
                cached["cached"] = True
                return cached
            sc = SpeechClient()
            if not sc.tts_configured():
                return {
                    "ok": False,
                    "error": (
                        "TTS 未設定 — 需要 TELEGRAM_TTS_API_KEY"
                        "（或 OPENROUTER_API_KEY）"
                    ),
                }
            audio, container = sc.synthesize(clean)
            playable = sc.to_browser_audio(audio, container)
            if not playable:
                return {
                    "ok": False,
                    "error": (
                        "TTS 回傳 PCM 但無法轉碼（ffmpeg 缺失）— "
                        "以文字回覆"
                    ),
                }
            audio, mime = playable
            out = {
                "ok": True,
                "mime": mime,
                "data_base64": _b64.b64encode(audio).decode("ascii"),
                "bytes": len(audio),
                "chars": len(clean),
            }
            _TTS_UI_CACHE[key] = dict(out)
            if len(_TTS_UI_CACHE) > _TTS_UI_CACHE_MAX:
                _TTS_UI_CACHE.pop(next(iter(_TTS_UI_CACHE)))
            logger.info(
                "tts_speak: ok bytes=%d mime=%s chars=%d",
                out["bytes"], mime, out["chars"],
            )
            return out
        except Exception as e:
            logger.error("tts_speak FAILED: %r", e)
            return {"ok": False, "error": repr(e)}

    # ─── Chat file download (v0.9.65.0+) ────────────────────────────

    def _resolve_sandbox_file(
        self, p_str: str,
    ) -> tuple[Path | None, int, str]:
        """Resolve one sandbox file path for the download bridges
        (``GET /download`` in web mode + ``save_sandbox_file_as`` in
        the pywebview GUI). Mirrors ``read_sandbox_image``: file:///
        stripping, relative paths anchored at the filesystem WRITE
        root, import ∪ READ ∪ WRITE roots containment, must exist and
        be a regular file — NEVER an arbitrary read.

        Returns ``(abs_path, 0, "")`` on success or ``(None, status,
        message)`` with status ∈ {400, 403, 404}.
        """
        from src.mcp_client.host import _allowed_file_roots, _is_within

        p_str = str(p_str or "").strip()
        if not p_str:
            return None, 400, "path is empty"
        low = p_str.lower()
        if low.startswith("file:///"):
            p_str = p_str[8:]
        elif low.startswith("file://"):
            p_str = p_str[7:]
        target = Path(p_str)
        if not target.is_absolute():
            from src.mcp_client.host import _resolve_filesystem_root
            try:
                target = (
                    _resolve_filesystem_root("QDRANT_FILESYSTEM_WRITE_ROOT")
                    / target
                )
            except Exception as e:
                return None, 400, (
                    f"relative path without a write root: {e!r}"
                )
        roots = _allowed_file_roots()
        if not any(_is_within(target, r) for r in roots):
            return None, 403, "outside the allowed sandbox roots"
        try:
            resolved = target.resolve()
        except OSError as e:
            return None, 400, f"resolve failed: {e!r}"
        if not resolved.is_file():
            if resolved.is_dir():
                return None, 400, "path is a directory"
            return None, 404, "file not found"
        return resolved, 0, ""

    def _download_name_allowed(self, name: str) -> tuple[bool, str]:
        """Dangerous-executable refusal — the same blocklist the
        filesystem MCP write tools / ``POST /upload`` / telegram file
        tools use. The sandbox can never CONTAIN one of these (writes
        are refused upstream); enforcing it here too keeps the download
        surface consistent with the rest of the file pipeline."""
        from src.mcp_client.host import _FS_DANGEROUS_WRITE_EXTS

        ext = os.path.splitext(name)[1].lower()
        if ext in _FS_DANGEROUS_WRITE_EXTS:
            return False, f"dangerous extension {ext!r} refused"
        return True, ""

    def save_sandbox_file_as(self, path: str = "",
                             dest_dir: str = "") -> dict:
        """Chat file download — pywebview GUI mode bridge.

        The frontend first opens ``pywebview.create_file_dialog
        (FileDialog.FOLDER)`` (a real native folder picker in GUI
        mode) and passes the chosen directory as ``dest_dir``; this
        method then copies the sandbox file there under its own name
        (collision-safe ``-N`` suffix). Same sandbox resolution /
        dangerous-ext refusal as the web ``GET /download`` endpoint.
        """
        try:
            resolved, status, msg = self._resolve_sandbox_file(path)
            if resolved is None:
                return {"ok": False, "error": msg, "status": status}
            allowed, why = self._download_name_allowed(resolved.name)
            if not allowed:
                return {"ok": False, "error": why}
            d_str = str(dest_dir or "").strip()
            if not d_str:
                return {"ok": False,
                        "error": "dest_dir is required (pick a folder)"}
            dest_root = Path(d_str)
            if not dest_root.is_dir():
                return {"ok": False,
                        "error": f"dest_dir not a directory: {d_str}"}
            target = dest_root / resolved.name
            n = 0
            while target.exists() and n < 1000:
                n += 1
                stem, ext = os.path.splitext(resolved.name)
                target = dest_root / f"{stem}-{n}{ext}"
            import shutil as _shutil

            _shutil.copyfile(resolved, target)
            size = target.stat().st_size
            logger.info(
                "download: %s -> %s (%d bytes)",
                resolved.as_posix(), target.resolve().as_posix(), size,
            )
            return {
                "ok": True,
                "dest": target.resolve().as_posix(),
                "name": resolved.name,
                "size": size,
                "source": resolved.as_posix(),
            }
        except Exception as e:
            logger.error(f"save_sandbox_file_as failed: {e!r}")
            return {"ok": False, "error": repr(e)}

    # ─── Schedules (v0.8.0+) ────────────────────────────────────────
    # Back the Schedules tab. The schedule definitions live in
    # ``~/.qdrant-rag-agent/schedules/<id>.json`` (atomic writes);
    # fire history lives in ``<id>.history.jsonl``. The scheduler
    # daemon is started lazily inside ``Agent.scheduler`` so the
    # GUI doesn't have to manage its own thread.

    def _normalize_schedule_payload(self, p: dict) -> dict:
        """Drop unknown keys, coerce types, and recompute
        ``next_fire_at`` so the scheduler picks the change up
        on the next tick."""
        from src.agent.scheduler import Schedule
        if not isinstance(p, dict):
            raise ValueError("payload must be a JSON object")
        allowed = set(Schedule.__dataclass_fields__)
        clean = {k: v for k, v in p.items() if k in allowed}
        if "interval_seconds" in clean:
            v = clean["interval_seconds"]
            if v is not None:
                clean["interval_seconds"] = int(v)
        if "web_max_results" in clean:
            clean["web_max_results"] = int(clean["web_max_results"])
        return clean

    def _validate_schedule(self, p: dict) -> str | None:
        """Lightweight validation; returns None or error string."""
        stype = p.get("schedule_type") or "interval_seconds"
        if stype not in ("once_at", "cron", "interval_seconds"):
            return (
                f"unknown schedule_type {stype!r}; use "
                "'once_at' | 'cron' | 'interval_seconds'"
            )
        if not p.get("prompt"):
            return "prompt is required"
        if stype == "once_at" and not p.get("once_at"):
            return "once_at schedule needs 'once_at' (ISO datetime)"
        if stype == "cron" and not p.get("cron"):
            return "cron schedule needs 'cron' expression"
        if stype == "interval_seconds":
            v = p.get("interval_seconds")
            if not isinstance(v, int) or v <= 0:
                return (
                    "interval_seconds schedule needs positive "
                    "integer 'interval_seconds'"
                )
        return None

    def list_schedules(self) -> dict[str, Any]:
        """Return all schedules as a flat dict list (for the
        Schedules tab) plus daemon status."""
        try:
            from src.agent.scheduler import ScheduleManager
            mgr = ScheduleManager()
            items = mgr.list()
            daemon = "stopped"
            try:
                agent = self._get_agent()
                if agent.scheduler.is_running():
                    daemon = "running"
            except Exception:
                pass
            return {
                "status": "ok",
                "schedules": [s.to_dict() for s in items],
                "daemon": daemon,
            }
        except Exception as e:
            return _err(f"list_schedules failed: {e!r}")

    def add_schedule(self, schedule: dict) -> dict[str, Any]:
        """Create a new schedule. The agent's daemon picks it up
        on the next tick (or starts if it isn't running)."""
        try:
            from src.agent.scheduler import Schedule, ScheduleManager
            clean = self._normalize_schedule_payload(schedule)
            err = self._validate_schedule(clean)
            if err:
                return _err(f"validation failed: {err}")
            s = Schedule(**clean)
            mgr = ScheduleManager()
            mgr.save(s)
            return {"status": "ok", "schedule": s.to_dict()}
        except ValueError as e:
            return _err(f"add_schedule failed: {e!r}")
        except Exception as e:
            return _err(f"add_schedule failed: {e!r}")

    def update_schedule(
        self, schedule_id: str, patch: dict
    ) -> dict[str, Any]:
        """Patch an existing schedule. ``id`` and ``created_at``
        are immutable."""
        try:
            from src.agent.scheduler import Schedule, ScheduleManager
            mgr = ScheduleManager()
            existing = mgr.get(schedule_id)
            if existing is None:
                return _err(f"schedule '{schedule_id}' not found")
            base = existing.to_dict()
            for k, v in patch.items():
                if k in ("id", "created_at"):
                    continue
                base[k] = v
            base["id"] = schedule_id
            clean = self._normalize_schedule_payload(base)
            err = self._validate_schedule(clean)
            if err:
                return _err(f"validation failed: {err}")
            updated = Schedule(**clean)
            updated.fire_count = existing.fire_count
            updated.last_fire_at = existing.last_fire_at
            updated.last_session_id = existing.last_session_id
            mgr.save(updated)
            return {"status": "ok", "schedule": updated.to_dict()}
        except ValueError as e:
            return _err(f"update_schedule failed: {e!r}")
        except Exception as e:
            return _err(f"update_schedule failed: {e!r}")

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        """Delete a schedule and its fire history."""
        try:
            from src.agent.scheduler import ScheduleManager
            mgr = ScheduleManager()
            if not mgr.delete(schedule_id):
                return _err(f"schedule '{schedule_id}' not found")
            return {"status": "ok", "deleted": schedule_id}
        except Exception as e:
            return _err(f"delete_schedule failed: {e!r}")

    def run_schedule_now(self, schedule_id: str) -> dict[str, Any]:
        """Fire a schedule synchronously (bypasses the daemon)."""
        try:
            from src.agent.scheduler import (
                ScheduleManager, make_agent_on_fire,
            )
            from src.agent.agent import Agent
            mgr = ScheduleManager()
            s = mgr.get(schedule_id)
            if s is None:
                return _err(f"schedule '{schedule_id}' not found")
            # Share the main thread's Agent so the fire uses the
            # same tool surface as Chat. v0.9.111.0: pin-aware factory
            # (validate/degrade lives inside make_agent_on_fire._fire).
            agent = self._get_agent()
            on_fire = make_agent_on_fire(
                agent_factory=lambda llm=None: Agent(
                    mcp_manager=agent._mcp_manager, task_llm=llm,
                ),
            )
            on_fire(s)
            return {
                "status": "ok",
                "schedule_id": schedule_id,
                "session_id": s.last_session_id,
                "fire_count": s.fire_count,
            }
        except Exception as e:
            return _err(f"run_schedule_now failed: {e!r}")

    def get_schedule_history(
        self, schedule_id: str, limit: int = 50
    ) -> dict[str, Any]:
        """Return the last N fire records for a schedule."""
        try:
            from src.agent.scheduler import ScheduleManager
            mgr = ScheduleManager()
            if mgr.get(schedule_id) is None:
                return _err(f"schedule '{schedule_id}' not found")
            items = mgr.get_history(schedule_id, limit=limit)
            return {
                "status": "ok",
                "schedule_id": schedule_id,
                "history": items,
            }
        except Exception as e:
            return _err(f"get_schedule_history failed: {e!r}")

    def start_scheduler(self) -> dict[str, Any]:
        """Spawn the scheduler daemon thread on the agent."""
        try:
            agent = self._get_agent()
            agent.start_scheduler()
            return {"status": "ok", "daemon": "running"}
        except Exception as e:
            return _err(f"start_scheduler failed: {e!r}")

    def stop_scheduler(self) -> dict[str, Any]:
        """Stop the scheduler daemon thread (waits up to 2 s)."""
        try:
            agent = self._get_agent()
            agent.stop_scheduler()
            return {"status": "ok", "daemon": "stopped"}
        except Exception as e:
            return _err(f"stop_scheduler failed: {e!r}")

    # ─── Chat ──────────────────────────────────────────────────────
    def chat_turn(
        self,
        question: str,
        history: list[dict] | None = None,
        web_search: bool = False,
        web_max_results: int = 5,
        top_k: int = 5,
        response_language: str | None = None,
        active_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        """Multi-turn chat. ``history`` is a list of {role, content} dicts.

        ``response_language`` (v0.8.5+) is the I18N code the user has
        selected in the GUI (e.g. ``'zh-TW'``, ``'zh-CN'``, ``'en'``,
        ``'ja'``). It's threaded into the agent's system prompt so
        the LLM answers in the user's UI locale. None = no directive
        (preserves the v0.8.4 behaviour — LLM picks a language).

        ``active_tools`` (v0.9.14.0+) — the per-TOOL allow-list
        from the chat tool picker (None = no override; the
        ``MCP_ACTIVE_SERVERS`` env gate applies).
        """
        q_preview = (question or "")[:80]
        if active_tools:
            # v0.9.83.6 — the per-request 🧰 allow-list is a silent
            # global restriction (v0.9.13.2 discovery trap, picker
            # edition): a stale localStorage disabled-set rode on every
            # request and HID get_current_time/web_search/search_
            # documents (17:50 live E2E — the model answered a WRONG
            # date from memory with the tools present server-side).
            # Log every override + WARN when a core tool is excluded.
            logger.info(
                "chat_turn: active_tools override = %d/%d tools",
                len(active_tools), len(active_tools) + 0,
            )
            core = {"search_documents", "get_current_time", "web_search"}
            missing = core - set(active_tools)
            if missing:
                logger.warning(
                    "chat_turn: the 🧰 picker allow-list HIDES core "
                    "tools %s — the model CANNOT call them; re-enable "
                    "via the 🧰 picker (All-on) in the chat toolbar",
                    sorted(missing),
                )
        logger.info(
            "chat_turn: q=%r history=%d web=%s top_k=%d lang=%s",
            q_preview, len(history or []), web_search, top_k,
            response_language,
        )
        try:
            agent = self._get_agent()
            self._wire_openwith_diagrams_opener(agent)
            answer, context, sources = agent.chat_turn(
                question=question,
                history=history or [],
                use_web_search=web_search,
                web_max_results=web_max_results,
                top_k=top_k,
                response_language=response_language,
                active_tool_names=active_tools,
            )
            # v0.9.12.0+: per-pair token usage (aggregated across the
            # whole tool loop). Only forwarded when it is a real dict
            # — MagicMock agents in tests / legacy providers must not
            # leak a non-serialisable object into the JS bridge.
            usage = getattr(agent, "last_usage", None)
            if not isinstance(usage, dict):
                usage = None
            logger.info(
                "chat_turn: ok answer_len=%d sources=%d tokens=%s",
                len(answer or ""), len(sources or []),
                usage.get("total_tokens") if usage else None,
            )
            # v0.9.102.6+ - per-pair MCP tool names dispatched THIS turn
            # (Agent.last_tool_dispatches); plain strings only.
            tools_used = getattr(agent, "last_tool_dispatches", None)
            if not isinstance(tools_used, list):
                tools_used = []
            tools_used = [str(x) for x in tools_used if x]
            return {"answer": answer, "context": context,
                    "sources": sources, "usage": usage, "tools_used": tools_used}
        except Exception as e:
            logger.error("chat_turn FAILED: %r\n%s", e, traceback.format_exc())
            return _err(f"chat failed: {e!r}")

    # ─── Turn event stream (v0.9.123.0) ─────────────────────────
    # Two-phase chat: start_turn registers the turn and runs it on a
    # daemon thread with the execution-event stream attached; the
    # frontend (web browser on the LAN / the pywebview window) polls
    # poll_turn and renders the live log. Zero new endpoints/ports —
    # the events ride the existing POST /api/<method> bridge.

    def start_turn(
        self,
        question: str,
        history: list[dict] | None = None,
        web_search: bool = False,
        web_max_results: int = 5,
        top_k: int = 5,
        response_language: str | None = None,
        active_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a chat turn, run it on a daemon thread (same
        parameters as ``chat_turn``), and return ``{"turn_id": …}``
        immediately. The final payload (answer/context/sources/usage/
        tools_used + error) is delivered via ``poll_turn`` once the
        terminal status lands; the execution log streams live in
        between. Never blocks."""
        import uuid as _uuid

        from src.agent import turn_events as _te

        turn_id = _uuid.uuid4().hex[:12]
        _te.start(turn_id)
        # v0.9.132.1 — stash the question so a mid-flight detach can
        # title the background task and rebuild its session.
        qs = getattr(self, "_turn_questions", None)
        if qs is None:
            qs = self._turn_questions = {}
        qs[turn_id] = str(question or "")
        logger.info(
            "start_turn: %s q=%r history=%d web=%s lang=%s",
            turn_id, (question or "")[:80], len(history or []),
            web_search, response_language,
        )

        def _work() -> None:
            agent = None
            try:
                agent = self._get_agent()
                self._wire_openwith_diagrams_opener(agent)
                agent.begin_turn_stream(
                    turn_id,
                    lambda kind, text: _te.record(turn_id, kind, text),
                )
                answer, context, sources = agent.chat_turn(
                    question=question,
                    history=history or [],
                    use_web_search=web_search,
                    web_max_results=web_max_results,
                    top_k=top_k,
                    response_language=response_language,
                    active_tool_names=active_tools,
                )
                usage = getattr(agent, "last_usage", None)
                if not isinstance(usage, dict):
                    usage = None
                tools_used = getattr(agent, "last_tool_dispatches", None)
                if not isinstance(tools_used, list):
                    tools_used = []
                tools_used = [str(x) for x in tools_used if x]
                stopped = bool(getattr(agent, "last_tool_stopped", False))
                _te.finish(
                    turn_id,
                    "stopped" if stopped else "done",
                    {
                        "answer": answer,
                        "context": context,
                        "sources": sources,
                        "usage": usage,
                        "tools_used": tools_used,
                    },
                )
                # v0.9.132.1 — if this turn was promoted into the
                # background-task registry mid-flight, stamp the task.
                self._finalize_detached_turn(
                    turn_id,
                    "stopped" if stopped else "done",
                    str(answer or ""),
                    None,
                )
            except Exception as e:  # noqa: BLE001 — land it as error
                logger.error(
                    "start_turn %s FAILED: %r", turn_id,
                    traceback.format_exc()[-800:],
                )
                _te.finish(turn_id, "error", {"error": repr(e)[:400]})
                self._finalize_detached_turn(
                    turn_id, "error", "", repr(e)[:500])
            finally:
                if agent is not None:
                    try:
                        agent.end_turn_stream(turn_id)
                    except Exception:  # noqa: BLE001
                        pass

        try:
            threading.Thread(
                target=_work, daemon=True, name=f"turn-{turn_id}",
            ).start()
        except Exception as e:  # noqa: BLE001 — even the spawn failing
            # lands as an honest terminal state
            _te.finish(turn_id, "error", {"error": repr(e)[:400]})
        return {"turn_id": turn_id}

    def poll_turn(self, turn_id: str, cursor: int = 0) -> dict[str, Any]:
        """Incremental snapshot of a streamed turn: the events after
        ``cursor`` plus the current status/result. Never raises.
        ``found: False`` ⇒ unknown id (e.g. the server restarted)."""
        try:
            from src.agent import turn_events as _te

            return _te.snapshot(str(turn_id or ""), int(cursor or 0))
        except Exception as e:  # noqa: BLE001
            return {
                "found": False, "status": "unknown", "events": [],
                "result": None, "error": repr(e)[:200],
                "next_cursor": int(cursor or 0),
            }

    def stop_turn(self, turn_id: str) -> dict[str, Any]:
        """Signal the turn-scoped stop Event (v0.9.123.0): the running
        two-phase turn ends at its next tool-loop boundary exactly like
        the v0.9.120.0 force-stop (TURN_STOPPED_TEXT, work kept)."""
        try:
            agent = self._get_agent()
            ok = bool(agent.signal_turn_stop(str(turn_id or "")))
            return {"ok": ok} if ok else {
                "ok": False,
                "error": "turn_id not found / not streamed",
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": repr(e)[:200]}

    # ─── Long-turn auto-background (v0.9.132.1) ─────────────────────
    # A foreground two-phase turn that runs past the frontend patience
    # threshold (gui/assets/app.js ``_TURN_BG_AFTER_MS`` = 60 s) is
    # promoted into the background-task registry: the ⏳ task panel
    # tracks it, the terminal notify fires, and the reply still lands
    # in the chat (the frontend poller keeps polling). The registry row
    # is stamped by ``_finalize_detached_turn`` when the turn reaches
    # its terminal state — the running work is NEVER killed or redone.

    def _detach_state(self) -> tuple[dict, Any]:
        """Lazy ``turn_id → {task_id, sid, question}`` map + its lock."""
        m = getattr(self, "_detached_turns", None)
        if m is None:
            import threading as _threading

            self._detached_lock = _threading.Lock()
            m = self._detached_turns = {}
        return m, self._detached_lock

    def detach_turn_to_background(self, turn_id: str) -> dict[str, Any]:
        """Promote a RUNNING foreground turn into the background-task
        registry (v0.9.132.1). Returns ``{ok, task_id, sid}``; idempotent
        for an already-detached turn. The running turn is never killed —
        all work done so far is kept."""
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            return _err("turn_id is required")
        reg, lock = self._detach_state()
        with lock:
            existing = reg.get(turn_id)
        if existing:
            return {"ok": True, "task_id": existing["task_id"],
                    "sid": existing["sid"], "already": True}
        try:
            from src.agent import turn_events as _te

            snap = _te.snapshot(turn_id, 0)
        except Exception as e:  # noqa: BLE001
            return _err(f"detach failed: {e!r}")
        if not snap.get("found"):
            return _err("turn_id not found (the server may have restarted)")
        if snap.get("status") != "running":
            return _err(
                f"turn is already {snap.get('status')} — nothing to detach")
        try:
            from src.agent import task_runner

            qs = getattr(self, "_turn_questions", {}) or {}
            question = str(qs.get(turn_id) or "")
            task_id = task_runner.begin_task(
                "chat", (question[:40] or "chat turn"))
            sid = f"task-{task_id}"
            task_runner.attach_sid(task_id, sid)
            # v0.9.133.10 — bind the turn id: the ⏳ panel polls THIS
            # turn's execution log (poll_turn) while the task runs.
            try:
                task_runner.attach_turn(task_id, turn_id)
            except Exception:  # noqa: BLE001
                pass
            # v0.9.132.2 — the ⏳ panel's 🛑 must reach this turn too,
            # but WITHOUT touching the SHARED agent ``_stop_check``
            # slot: the v0.9.132.1 chain leaked (the registry stop flag
            # is deliberately NEVER cleared — zombie-reachable design —
            # so one stopped detached task force-stopped EVERY later
            # chat turn on the main agent). A tiny self-terminating
            # watcher bridges the flag into THIS turn's turn-scoped
            # stop event instead; the shared slot is never mutated.
            try:
                agent = self._get_agent()

                def _watch() -> None:
                    try:
                        import time as _time

                        from src.agent import turn_events as _te2

                        while True:
                            if task_runner.stop_requested(task_id):
                                try:
                                    agent.signal_turn_stop(turn_id)
                                except Exception:  # noqa: BLE001
                                    pass
                                return
                            try:
                                if _te2.snapshot(
                                    turn_id, 0,
                                ).get("status") != "running":
                                    return
                            except Exception:  # noqa: BLE001
                                return
                            _time.sleep(0.5)
                    except Exception:  # noqa: BLE001 — watcher is
                        return        # best-effort bookkeeping

                threading.Thread(
                    target=_watch, daemon=True,
                    name=f"turn-bg-watch-{turn_id}",
                ).start()
            except Exception:  # noqa: BLE001 — watcher is best-effort
                pass
            with lock:
                reg[turn_id] = {
                    "task_id": task_id, "sid": sid, "question": question,
                }
            logger.info(
                "detach_turn_to_background: %s -> task %s (sid=%s)",
                turn_id, task_id, sid,
            )
            return {"ok": True, "task_id": task_id, "sid": sid}
        except Exception as e:  # noqa: BLE001
            return _err(f"detach failed: {e!r}")

    def _finalize_detached_turn(
        self, turn_id: str, status: str, answer_text: str,
        error: str | None,
    ) -> None:
        """Stamp the detached task's registry row + write its dedicated
        session (mirrors ``TaskLocalHost._run_once``'s tail). Never
        raises; a no-op when the turn was never detached."""
        reg, lock = self._detach_state()
        with lock:
            info = reg.pop(turn_id, None)
        if not info:
            return
        task_id = info.get("task_id")
        sid = info.get("sid")
        # v0.9.133.10 — the execution log rides into the saved assistant
        # message (msg.turnLog) so the task session keeps a 查看執行 log
        # expander exactly like a foreground turn. Snapshot BEFORE the
        # ring's LRU can evict the turn; a failure leaves no field.
        turn_log = None
        try:
            from src.agent import turn_events as _te

            snap = _te.snapshot(turn_id, 0)
            evs = list(snap.get("events") or [])
            if evs:
                turn_log = evs[-120:]
        except Exception:  # noqa: BLE001
            turn_log = None
        try:
            from src.agent import task_runner
            from src.agent.session import Session, SessionManager

            sess = Session(
                sid=sid, title=f"[task] {info.get('question', '')[:40]}")
            sess.messages.append({
                "role": "user", "content": info.get("question", ""),
                "ts": task_runner.now_iso(),
            })
            text = str(answer_text or "").strip()
            if status == "error":
                sess.messages.append({
                    "role": "assistant",
                    "content": f"[error] {error or 'unknown error'}",
                    "ts": task_runner.now_iso(), "sources": [],
                    "error": True, "task_id": task_id,
                })
            else:
                sess.messages.append({
                    "role": "assistant",
                    "content": text or "(無回覆 — 模型未產生內容)",
                    "ts": task_runner.now_iso(), "sources": [],
                    "task_id": task_id,
                })
                if turn_log:
                    sess.messages[-1]["turnLog"] = turn_log
                else:
                    # empty stream (mocked agents / zero tool-loop events)
                    # still carries the field; the frontend hides an
                    # empty expander, so no visual harm.
                    sess.messages[-1]["turnLog"] = []
            sess.updated_at = task_runner.now_iso()
            try:
                SessionManager().save(sess)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "detached task session save(%s) failed: %r", sid, e)
            registry_status = {
                "done": task_runner.DONE,
                "stopped": task_runner.STOPPED,
            }.get(status, task_runner.FAILED)
            task_runner.end_task(
                task_id, registry_status,
                answer_chars=(len(text) if status != "error" else None),
                error=error,
            )
        except Exception as e:  # noqa: BLE001 — bookkeeping must not break
            logger.warning("finalize_detached(%s) failed: %r", turn_id, e)

    def ask(
        self,
        question: str,
        top_k: int = 5,
        web_search: bool = False,
        response_language: str | None = None,
    ) -> dict[str, Any]:
        """Single-turn RAG ask (no history)."""
        logger.info("ask: q=%r top_k=%d web=%s lang=%s",
                    (question or "")[:80], top_k, web_search,
                    response_language)
        try:
            agent = self._get_agent()
            self._wire_openwith_diagrams_opener(agent)
            answer = agent.ask(
                question=question, top_k=top_k, use_web_search=web_search,
                response_language=response_language,
            )
            logger.info("ask: ok answer_len=%d", len(answer or ""))
            return {"answer": answer}
        except Exception as e:
            logger.error("ask FAILED: %r\n%s", e, traceback.format_exc())
            return _err(f"ask failed: {e!r}")

    # ─── Background tasks (v0.9.47.0 Phase 3 — task panel) ─────────
    def list_background_tasks(
        self, status: str | None = None, limit: int = 20,
    ) -> dict[str, Any]:
        """Running + recent background LLM turns (task panel data).

        Delegates to the process-wide ``src.agent.task_runner``
        registry — scheduler fires / webhook fires / telegram turns /
        manual ``run_task_once`` submissions ALL report here, so the
        panel sees everything without polling the Sessions tab.
        """
        try:
            from src.agent import task_runner
            return task_runner.list_tasks(status=status, limit=limit)
        except Exception as e:
            return _err(f"list_background_tasks failed: {e!r}")

    def get_background_task(
        self, task_id: str, preview_chars: int = 800,
    ) -> dict[str, Any]:
        """One task record + the session's last assistant message."""
        try:
            from src.agent import task_runner

            rec = task_runner.get_task(task_id)
            if rec is None:
                return _err(f"unknown task_id {task_id!r}")
            try:
                preview = min(max(int(preview_chars), 100), 4000)
            except (TypeError, ValueError):
                preview = 800
            if rec.get("sid"):
                from src.agent.session import SessionManager
                sess = SessionManager().load(rec["sid"])
                if sess is not None:
                    for msg in reversed(sess.messages):
                        if msg.get("role") == "assistant":
                            text = str(msg.get("content") or "")
                            rec["answer_preview"] = text[:preview]
                            rec["preview_truncated"] = len(text) > preview
                            break
            return rec
        except Exception as e:
            return _err(f"get_background_task failed: {e!r}")

    def submit_background_task(
        self,
        title: str,
        prompt: str,
        use_web_search: bool = False,
        chat_sid: str | None = None,
    ) -> dict[str, Any]:
        """Submit ONE background LLM turn; returns task_id immediately.

        Reuses ``TaskLocalHost._run_once`` (Phase 2) — a fresh Agent
        sharing the MAIN Agent's McpManager runs the turn with the
        full tool surface and persists it into session
        ``task-<task_id>``. The chat stays responsive; the result
        lands in the task panel + the Sessions tab.

        ``chat_sid`` (v0.9.132.3) — the ORIGIN chat session the task
        was submitted from: recorded as the registry ``origin_sid`` so
        the client can write the finished result back into that
        conversation.
        """
        prompt = str(prompt or "").strip()
        if not prompt:
            return _err("prompt is empty — nothing to run")
        try:
            agent = self._get_agent()
            from src.mcp_client.host import TaskLocalHost
            host = TaskLocalHost(mcp_manager=agent.mcp_manager)
            return host._run_once({
                "title": str(title or ""),
                "prompt": prompt,
                "use_web_search": bool(use_web_search),
                "origin_sid": str(chat_sid or "").strip() or None,
            })
        except Exception as e:
            return _err(f"submit_background_task failed: {e!r}")

    # ─── Ingest ────────────────────────────────────────────────────
    def ingest_file(self, file_path: str) -> dict[str, Any]:
        """Ingest a single file. Returns chunks-added count."""
        logger.info("ingest_file: path=%s", file_path)
        try:
            pipeline = _get_pipeline()
            n = pipeline.ingest_file(Path(file_path))
            logger.info("ingest_file: ok chunks=%d path=%s", n, file_path)
            return {"status": "ok", "chunks": n, "path": file_path}
        except Exception as e:
            logger.error("ingest_file FAILED: path=%s err=%r\n%s",
                         file_path, e, traceback.format_exc())
            return _err(f"ingest failed: {e!r}")

    def ingest_directory(self, dir_path: str) -> dict[str, Any]:
        """Recursively ingest a directory. Returns per-file counts."""
        logger.info("ingest_directory: path=%s", dir_path)
        try:
            pipeline = _get_pipeline()
            results = pipeline.ingest_directory(dir_path)
            total = sum(v for v in results.values() if v > 0)
            logger.info("ingest_directory: ok files=%d total_chunks=%d",
                        len(results), total)
            return {
                "status": "ok",
                "per_file": results,
                "total_chunks": total,
            }
        except Exception as e:
            logger.error("ingest_directory FAILED: path=%s err=%r\n%s",
                         dir_path, e, traceback.format_exc())
            return _err(f"directory ingest failed: {e!r}")

    # ─── Search ────────────────────────────────────────────────────
    def search(
        self,
        query: str,
        top_k: int = 5,
        filename_filter: str | None = None,
        collection: str | None = None,
    ) -> dict[str, Any]:
        """Vector search. v0.9.60.0: ``collection`` selects ANY Qdrant
        collection (default = the configured one) — the GUI Search tab's
        collection picker feeds it. Results include the point ``id`` so
        the per-result delete (資料管理) can act on it."""
        collection = (collection or "").strip() or config.QDRANT_COLLECTION
        logger.info("search: q=%r top_k=%d filter=%s collection=%s",
                    (query or "")[:80], top_k, filename_filter, collection)
        try:
            retriever = self._get_retriever_for(collection)
            results = retriever.search(
                query=query, top_k=top_k, filename_filter=filename_filter,
            )
            logger.info("search: ok count=%d collection=%s",
                        len(results), collection)
            return {
                "results": results,
                "count": len(results),
                "collection": collection,
            }
        except Exception as e:
            logger.error("search FAILED: q=%r err=%r\n%s",
                         (query or "")[:80], e, traceback.format_exc())
            return _err(f"search failed: {e!r}")

    def _get_retriever_for(self, collection: str | None):
        """Retriever for the requested collection (v0.9.60.0) — the
        configured one reuses the cached instance; any other builds a
        dedicated QdrantStore + Retriever for that collection."""
        if not collection or collection == config.QDRANT_COLLECTION:
            return self._get_retriever()
        from src.ingest.embedder import Embedder
        from src.vector_store.qdrant_client import QdrantStore
        from src.vector_store.retriever import Retriever

        store = QdrantStore(collection=collection)
        retriever = Retriever()
        retriever.store = store
        retriever.embedder = Embedder()
        return retriever

    def _collection_store(self, collection: str):
        """QdrantStore for an arbitrary collection, REUSED across page
        turns (v0.9.60.2) — at a million points the browser turns
        dozens of pages per session; rebuilding the HTTP client per
        call would pay connection setup on EVERY page."""
        cache = getattr(self, "_store_cache", None)
        if cache is None:
            cache = self._store_cache = {}
        store = cache.get(collection)
        if store is None:
            from src.vector_store.qdrant_client import QdrantStore

            store = QdrantStore(collection=collection)
            cache[collection] = store
        return store

    def collection_browse(
        self,
        collection: str,
        page_size: int = 50,
        offset: str | None = None,
    ) -> dict[str, Any]:
        """Paged point browser for the Stats tab (v0.9.60.0) — batch
        display over HUGE collections instead of dumping everything;
        each row carries id + truncated payload for per-row delete.
        Scalability (v0.9.60.2): ONE cursor-anchored scroll per page
        (the Qdrant offset is a point-id seek, never a scan from the
        start), so page N costs the same as page 1 even on a
        million-point collection."""
        collection = (collection or "").strip()
        logger.info("collection_browse: c=%s offset=%r",
                    collection, (offset or "")[:24])
        if not collection:
            return _err("collection is required")
        try:
            page_size = int(page_size)
        except (TypeError, ValueError):
            return _err("invalid page_size")
        if page_size < 1:
            return _err("page_size must be >= 1")
        try:
            store = self._collection_store(collection)
            rows, next_offset = store.browse_points(
                limit=page_size, offset=offset,
            )
            return {
                "collection": collection,
                "items": rows,
                "next_offset": next_offset,
                "page_size": len(rows),
            }
        except Exception as e:
            logger.error("collection_browse FAILED: %r\n%s",
                         e, traceback.format_exc())
            return _err(f"collection_browse failed: {e!r}")

    def delete_points(
        self, collection: str, ids: list[str],
    ) -> dict[str, Any]:
        """Per-row data management (v0.9.60.0) — delete points by id
        from ANY collection (destructive — frontend must confirm)."""
        collection = (collection or "").strip()
        ids = [str(i) for i in (ids or []) if str(i or "").strip()]
        logger.warning(
            "delete_points: called (destructive) c=%s n=%s",
            collection, len(ids),
        )
        if not collection:
            return _err("collection is required")
        if not ids:
            return _err("no point ids provided")
        try:
            deleted = self._collection_store(collection).delete_points(ids)
            return {"status": "ok", "deleted": deleted,
                    "collection": collection}
        except Exception as e:
            logger.error("delete_points FAILED: %r\n%s",
                         e, traceback.format_exc())
            return _err(f"delete_points failed: {e!r}")

    # ─── Stats / documents ─────────────────────────────────────────
    def get_stats(self, collection: str | None = None) -> dict[str, Any]:
        """Collection stats. v0.9.60.0: ``collection`` selects ANY
        collection (default = configured) — the Stats tab's picker.
        The indexed-documents list (filename group-by) is only
        meaningful on the RAG collection; other collections get their
        data via the paged point browser below."""
        collection = ((collection or "").strip()
                      or config.QDRANT_COLLECTION)
        logger.info("get_stats: called collection=%s", collection)
        try:
            if collection == config.QDRANT_COLLECTION:
                store = self._get_store()
            else:
                from src.vector_store.qdrant_client import QdrantStore

                store = QdrantStore(collection=collection)
            stats = store.get_stats()
            # list_documents() used to be a 168-scroll loop on the
            # 167k-pt test collection and would hang the GUI for
            # minutes. It now uses Qdrant's group-by API (~3 s) and
            # is per-instance cached for 60 s by QdrantStore. Still,
            # wrap in its own try so a slow count doesn't blank out
            # the chunks / dim cards too. v0.9.60.0: only the RAG
            # collection has a filename payload — the other
            # collections use the paged point browser instead.
            if collection == config.QDRANT_COLLECTION:
                try:
                    docs = store.list_documents()
                    stats["documents"] = docs
                    stats["doc_count"] = len(docs)
                except Exception as e:
                    logger.warning("get_stats: list_documents failed: %r", e)
                    stats["documents"] = []
                    stats["doc_count"] = None
                    stats["docs_error"] = str(e)
            logger.info("get_stats: ok chunks=%s docs=%s",
                        stats.get("num_entities"), stats.get("doc_count"))
            return stats
        except Exception as e:
            logger.error("get_stats FAILED: %r\n%s", e, traceback.format_exc())
            return _err(f"stats failed: {e!r}")

    def list_documents(self) -> dict[str, Any]:
        logger.info("list_documents: called")
        try:
            store = self._get_store()
            docs = store.list_documents()
            logger.info("list_documents: ok count=%d", len(docs))
            return {"documents": docs}
        except Exception as e:
            logger.error("list_documents FAILED: %r\n%s", e, traceback.format_exc())
            return _err(f"list_documents failed: {e!r}")

    # ─── Embedding cache (v0.6.0) ────────────────────────────────
    # The Embedder now has a persistent SQLite cache (in addition to
    # the in-process LRU). These methods let the GUI admin tab show
    # hit-ratio and clear caches.
    def backend_info(self) -> dict[str, Any]:
        """Return active embedding backend + cache stats (disk + LRU).

        The frontend renders this in the Admin tab's "Embedding" card.
        """
        logger.info("backend_info: called")
        try:
            embedder = self._get_embedder()
            info = embedder.backend_info()
            disk = info.get("disk_cache") or {}
            disk_n = disk.get("n_entries", "?") if isinstance(disk, dict) else "?"
            logger.info("backend_info: ok backend=%s disk_entries=%s",
                        info.get("backend"), disk_n)
            return info
        except Exception as e:
            logger.error("backend_info FAILED: %r\n%s", e, traceback.format_exc())
            return _err(f"backend_info failed: {e!r}")

    def cache_clear_all(self) -> dict[str, Any]:
        """Wipe both LRU + disk embedding cache. Returns summary."""
        logger.warning("cache_clear_all: called (destructive)")
        try:
            embedder = self._get_embedder()
            r = embedder.cache_clear_all()
            logger.info("cache_clear_all: ok lru=%d disk=%d",
                        r.get("lru_cleared"), r.get("disk_cleared"))
            return r
        except Exception as e:
            logger.error("cache_clear_all FAILED: %r\n%s", e, traceback.format_exc())
            return _err(f"cache_clear_all failed: {e!r}")

    # ─── hash-pollution audit + repair (v0.9.112.0) ────────────────
    def audit_embedding_backends(self) -> dict[str, Any]:
        """Scan the active collection for hash-fallback vectors. The
        heuristic re-derives every text's deterministic hash vector
        and compares it with the stored vector EXACTLY — points
        ingested before the v0.9.112.0 ``embedding_backend`` payload
        tag existed are detectable too. Read-only; errors are
        envelopes."""
        logger.info("audit_embedding_backends: called")
        try:
            from src.ingest.reembed import audit_or_reembed

            report = audit_or_reembed(self._get_store())
            if report.get("ok"):
                logger.info(
                    "audit_embedding_backends: ok scanned=%s "
                    "hash_points=%s",
                    report.get("scanned"), report.get("hash_points"),
                )
            return report
        except Exception as e:
            logger.error("audit_embedding_backends FAILED: %r", e)
            return _err(f"audit_embedding_backends failed: {e!r}")

    def reembed_hash_points(self) -> dict[str, Any]:
        """Re-embed every hash-vector point with the CURRENT backend
        (caches bypassed; hash-tagged points untouched so the repair
        cannot lie). Errors are envelopes."""
        logger.warning("reembed_hash_points: called (repair op)")
        try:
            from src.ingest.reembed import audit_or_reembed

            report = audit_or_reembed(self._get_store(), reembed=True)
            if report.get("ok"):
                logger.info(
                    "reembed_hash_points: ok flagged=%s reembedded=%s "
                    "still_hash=%s",
                    report.get("hash_points"),
                    report.get("reembedded"),
                    len(report.get("still_hash") or []),
                )
            return report
        except Exception as e:
            logger.error("reembed_hash_points FAILED: %r", e)
            return _err(f"reembed_hash_points failed: {e!r}")

    # ─── STAIR heading-path backfill (v0.9.113.0) ───────────────────
    def backfill_headings(self, collection: str | None = None,
                          limit: int = 0, force: bool = False) -> dict[str, Any]:
        """Fill ``heading_path`` payload on existing points from their own
        text (metadata-only set_payload — vectors/ids untouched; the
        ``heading_path`` KEYWORD index is created best-effort). Errors
        are envelopes."""
        logger.warning("backfill_headings: called (metadata-only op)")
        try:
            from src.ingest.heading_backfill import backfill_collection

            store = (self._get_store() if not collection
                     else self._get_store_for_collection(collection))
            report = backfill_collection(store, limit=limit, force=force)
            if not report.get("errors"):
                logger.info("backfill_headings: ok updated=%s scanned=%s",
                            report.get("updated"), report.get("scanned"))
            return report
        except Exception as e:
            logger.error("backfill_headings FAILED: %r", e)
            return _err(f"backfill_headings failed: {e!r}")

    def _get_store_for_collection(self, collection: str):
        """QdrantStore for an explicit collection name (heading backfill)."""
        from src.vector_store.qdrant_client import QdrantStore

        return QdrantStore(collection=collection)

    # ─── Sessions ──────────────────────────────────────────────────
    def list_sessions(self) -> dict[str, Any]:
        logger.info("list_sessions: called")
        try:
            sessions = self._get_session_mgr().list()
            logger.info("list_sessions: ok count=%d", len(sessions))
            return {"sessions": sessions}
        except Exception as e:
            logger.error("list_sessions FAILED: %r\n%s", e, traceback.format_exc())
            return _err(f"list_sessions failed: {e!r}")

    def load_session(self, sid: str) -> dict[str, Any]:
        logger.info("load_session: sid=%s", sid)
        try:
            s = self._get_session_mgr().load(sid)
            if s is None:
                logger.warning("load_session: sid=%s not found", sid)
                return _err(f"session {sid} not found")
            logger.info("load_session: ok messages=%d", len(s.messages))
            return {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "messages": s.messages,
            }
        except Exception as e:
            logger.error("load_session FAILED: sid=%s err=%r\n%s",
                         sid, e, traceback.format_exc())
            return _err(f"load_session failed: {e!r}")

    def save_session(self, sid: str, title: str, messages: list[dict]) -> dict[str, Any]:
        logger.info("save_session: sid=%s title=%r messages=%d",
                    sid, title, len(messages or []))
        try:
            mgr = self._get_session_mgr()
            s = mgr.load(sid) or _new_session(sid, title)
            s.messages = messages
            s.title = title or s.title
            mgr.save(s)
            logger.info("save_session: ok id=%s", s.id)
            return {"status": "ok", "id": s.id}
        except Exception as e:
            logger.error("save_session FAILED: sid=%s err=%r\n%s",
                         sid, e, traceback.format_exc())
            return _err(f"save_session failed: {e!r}")

    # ─── v0.9.103.5 — session title rename (Sessions-tab ✏️) ────────
    @staticmethod
    def _session_title_from_payload(payload: Any) -> tuple[str, str]:
        """The single-dict bridge payload convention (v0.9.96.3):
        ``{"id": ..., "title": ...}`` — ``sid`` / ``session_id`` /
        ``new_title`` key aliases tolerated; a ``(sid, title)`` 2-item
        sequence works too (positional bridge shape). Unknown shapes →
        ("", "") — never raises."""
        sid = title = ""
        if isinstance(payload, dict):
            for k in ("id", "sid", "session_id"):
                v = payload.get(k)
                if v is not None and str(v).strip():
                    sid = str(v).strip()
                    break
            for k in ("title", "new_title"):
                v = payload.get(k)
                if v is not None and str(v).strip():
                    title = str(v).strip()
                    break
        elif isinstance(payload, (list, tuple)) and len(payload) >= 2:
            sid = str(payload[0] or "").strip()
            title = str(payload[1] or "").strip()
        elif isinstance(payload, str):
            sid = payload.strip()
        return sid, title

    def update_session_title(self, payload: Any = None) -> dict[str, Any]:
        """v0.9.103.5 — rename ONE persisted session's title (the
        Sessions-tab ✏️ per-row edit). A METADATA-ONLY edit: the
        messages are untouched and the edit rides
        ``SessionManager.rename`` (updated_at preserved, memory
        re-ingest skipped). Clean errors: no session id / empty title
        / session not found; never raises."""
        sid, title = self._session_title_from_payload(payload)
        logger.info("update_session_title: sid=%s title_len=%d",
                    sid, len(title))
        if not sid:
            return _err("update_session_title: no session id")
        if not title:
            return _err("update_session_title: title is empty")
        try:
            s = self._get_session_mgr().rename(sid, title)
            if s is None:
                logger.warning(
                    "update_session_title: sid=%s not found", sid)
                return _err(f"session {sid} not found")
            logger.info("update_session_title: ok id=%s title=%r",
                        sid, s.title)
            return {"status": "ok", "id": s.id, "title": s.title}
        except Exception as e:
            logger.error("update_session_title FAILED: sid=%s err=%r\n%s",
                         sid, e, traceback.format_exc())
            return _err(f"update_session_title failed: {e!r}")

    def create_session(self, title: str = "") -> dict[str, Any]:
        logger.info("create_session: title=%r", title)
        try:
            s = self._get_session_mgr().create(title=title)
            logger.info("create_session: ok id=%s", s.id)
            return {
                "id": s.id, "title": s.title,
                "created_at": s.created_at, "messages": [],
            }
        except Exception as e:
            logger.error("create_session FAILED: %r\n%s", e, traceback.format_exc())
            return _err(f"create_session failed: {e!r}")

    def delete_session(self, sid: str) -> dict[str, Any]:
        logger.info("delete_session: sid=%s", sid)
        try:
            ok = self._get_session_mgr().delete(sid)
            logger.info("delete_session: %s id=%s", "ok" if ok else "not_found", sid)
            return {"status": "ok" if ok else "not_found", "id": sid}
        except Exception as e:
            logger.error("delete_session FAILED: sid=%s err=%r\n%s",
                         sid, e, traceback.format_exc())
            return _err(f"delete_session failed: {e!r}")

    # ─── v0.9.103.1 — Sessions-tab multi-select batch ops ───────────
    @staticmethod
    def _session_ids_from_payload(payload: Any) -> list[str]:
        """The single-dict bridge payload convention (v0.9.96.3
        lesson): ``{"ids": [...]}``; a bare list / comma string is
        tolerated too. Order kept, duplicates collapsed, non-str
        items stringified, empty/unknown shapes → []."""
        if isinstance(payload, dict):
            payload = payload.get("ids", [])
        if isinstance(payload, str):
            payload = [x.strip() for x in payload.split(",") if x.strip()]
        if not isinstance(payload, (list, tuple)):
            payload = []
        out: list[str] = []
        for x in payload:
            sid = str(x or "").strip()
            if sid and sid not in out and len(out) < 1000:
                out.append(sid)
        return out

    def delete_sessions(self, payload: Any = None) -> dict[str, Any]:
        """v0.9.103.1 — batch delete selected sessions (Sessions-tab
        multi-select). Per-id containment: ONE bad id never aborts the
        batch; never raises."""
        ids = self._session_ids_from_payload(payload)
        logger.info("delete_sessions: n=%d", len(ids))
        try:
            mgr = self._get_session_mgr()
            results = []
            deleted = not_found = 0
            for sid in ids:
                try:
                    ok = mgr.delete(sid)
                    results.append({
                        "id": sid,
                        "status": "ok" if ok else "not_found",
                    })
                    if ok:
                        deleted += 1
                    else:
                        not_found += 1
                except Exception as e:
                    results.append({"id": sid, "status": "error",
                                    "error": repr(e)})
            logger.info("delete_sessions: ok deleted=%d not_found=%d",
                        deleted, not_found)
            return {"ok": True, "deleted": deleted, "not_found": not_found,
                    "results": results}
        except Exception as e:
            logger.error("delete_sessions FAILED: %r\n%s", e,
                         traceback.format_exc())
            return _err(f"delete_sessions failed: {e!r}")

    def export_sessions(self, payload: Any = None) -> dict[str, Any]:
        """v0.9.103.1 — batch export the selected sessions into ONE
        combined JSON file written ATOMICALLY under the filesystem
        WRITE sandbox's ``uploads/`` dir; the frontend hands ``path``
        to the pre-existing ``downloadSandboxFile()`` flow (web:
        ``GET /download``; pywebview GUI: native save dialog)."""
        ids = self._session_ids_from_payload(payload)
        if not ids:
            return _err("no sessions selected")
        try:
            sessions = []
            skipped = []
            for sid in ids:
                try:
                    s = self._get_session_mgr().load(sid)
                except Exception:
                    s = None
                if s is None:
                    skipped.append(sid)
                    continue
                sessions.append({
                    "id": s.id, "title": s.title,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                    "messages": s.messages,
                })
            if not sessions:
                return _err("no sessions found to export")
            from src.mcp_client.host import _resolve_filesystem_root
            from datetime import datetime as _dt
            root = Path(_resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT"))
            updir = root / "uploads"
            updir.mkdir(parents=True, exist_ok=True)
            stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
            name = f"sessions-export-{stamp}.json"
            dest = updir / name
            blob = json.dumps({
                "exported_at":
                    _dt.now().isoformat(timespec="seconds"),
                "count": len(sessions),
                "skipped": skipped,
                "sessions": sessions,
            }, ensure_ascii=False, indent=1)
            fd, tmp = tempfile.mkstemp(
                dir=str(updir), suffix=".tmp")
            try:
                with os.fdopen(
                    fd, "w", encoding="utf-8", newline="",
                ) as f:
                    f.write(blob)
                os.replace(tmp, str(dest))
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            logger.info(
                "export_sessions: ok count=%d skipped=%d path=%s",
                len(sessions), len(skipped), dest.as_posix())
            return {"ok": True, "path": dest.as_posix(), "name": name,
                    "count": len(sessions), "skipped": skipped,
                    "size": dest.stat().st_size}
        except Exception as e:
            logger.error("export_sessions FAILED: %r\n%s", e,
                         traceback.format_exc())
            return _err(f"export_sessions failed: {e!r}")

    # ─── Collection ops (destructive — caller should confirm) ──────
    def list_collections(self) -> dict[str, Any]:
        """All Qdrant collections with per-collection stats (v0.9.59.1).

        The GUI Admin tab's Collections table lists EVERY collection
        (file_docs / log_docs / stock_docs / memory_docs / ...) with
        points / dim / kind label + a per-row destructive delete
        button (like the existing file_docs card). ``kind`` is
        inferred from the well-known collection names so the user can
        tell what each collection feeds."""
        logger.info("list_collections: called")
        try:
            cols = self._get_store().list_all_collections()
            known = {
                config.QDRANT_COLLECTION: "rag",
                "log_docs": "log",
                "stock_docs": "stock",
                "memory_docs": "memory",
            }
            for c in cols:
                c["configured"] = (
                    c.get("name") == config.QDRANT_COLLECTION
                )
                c["kind"] = known.get(c.get("name"), "other")
            logger.info("list_collections: ok n=%s", len(cols))
            return {"collections": cols}
        except Exception as e:
            logger.error("list_collections FAILED: %r\n%s",
                         e, traceback.format_exc())
            return _err(f"list_collections failed: {e!r}")

    def delete_collection_by_name(self, name: str) -> dict[str, Any]:
        """Delete ANY collection by exact name (destructive — the
        frontend must confirm). Mirrors the file_docs
        ``delete_collection`` button for every row of the Collections
        table."""
        name = str(name or "").strip()
        logger.warning(
            "delete_collection_by_name: called (destructive) name=%s",
            name,
        )
        if not name:
            return _err("collection name is required")
        try:
            ok = self._get_store().delete_collection_by_name(name)
            if ok:
                logger.info("delete_collection_by_name: ok name=%s", name)
                return {"status": "ok", "name": name}
            return {"status": "not_found", "name": name}
        except Exception as e:
            logger.error("delete_collection_by_name FAILED: %r\n%s",
                         e, traceback.format_exc())
            return _err(f"delete_collection_by_name failed: {e!r}")

    def delete_collection(self) -> dict[str, Any]:
        logger.warning("delete_collection: called (destructive)")
        try:
            self._get_store().delete_collection()
            logger.info("delete_collection: ok")
            return {"status": "ok"}
        except Exception as e:
            logger.error("delete_collection FAILED: %r\n%s", e, traceback.format_exc())
            return _err(f"delete_collection failed: {e!r}")

    def recreate_collection(self) -> dict[str, Any]:
        logger.warning("recreate_collection: called (destructive)")
        try:
            self._get_store().recreate_collection()
            stats = self._get_store().get_stats()
            logger.info("recreate_collection: ok stats=%s", stats)
            return {"status": "ok", "stats": stats}
        except Exception as e:
            logger.error("recreate_collection FAILED: %r\n%s",
                         e, traceback.format_exc())
            return _err(f"recreate_collection failed: {e!r}")

    # ─── Per-document removal (v0.7.0+) ────────────────────────────────
    # Mirrors the CLI ``remove-files`` subcommand. Filename is exact,
    # case-sensitive, basename-only. Returning a per-file count lets
    # the frontend render a precise toast and refresh stats
    # immediately after.
    def remove_documents(self, filenames: list[str]) -> dict[str, Any]:
        """Delete one or more indexed documents by basename.

        Always returns ``{"removed": [...], "missing": [...]}`` on
        success; error envelopes come back as ``{"error": "..."}``
        instead. Both lists reflect what was attempted, so the GUI
        can tell "I deleted 3 of 5 requested" vs "typo on a name".
        Each success invalidates the per-store ``list_documents``
        cache, so the next ``refreshStats`` is fresh.
        """
        if not filenames:
            return {"removed": [], "missing": [],
                    "error": "no filenames provided"}
        # Coerce and dedupe while preserving order — important
        # for UI feedback ("you asked to remove these 5 names").
        seen = set()
        unique: list[str] = []
        for f in filenames:
            if isinstance(f, str) and f and f not in seen:
                seen.add(f)
                unique.append(f)
        if not unique:
            return {"removed": [], "missing": [],
                    "error": "no filenames provided"}
        logger.warning(
            "remove_documents: called (destructive) files=%s", unique,
        )
        try:
            store = self._get_store()
            # Pre-flight diff so we report what's actually in the
            # collection vs. typos. list_documents() is cached for
            # 60 s on the server side; one round-trip is enough.
            existing = set(store.list_documents())
            missing = [n for n in unique if n not in existing]
            present = [n for n in unique if n in existing]

            # Actual deletions — one round-trip per file (Qdrant
            # doesn't have a multi-key delete filter). delete_by_*
            # already invalidates the per-store cache; we do NOT
            # invalidate it again here.
            removed: dict[str, int] = {}
            for name in present:
                n = store.delete_by_filename(name)
                removed[name] = n
            logger.info(
                "remove_documents: ok removed=%s missing=%s",
                removed, missing,
            )
            return {"removed": removed, "missing": missing}
        except Exception as e:
            logger.error(
                "remove_documents FAILED: %r\n%s",
                e, traceback.format_exc(),
            )
            return _err(f"remove_documents failed: {e!r}")

    # ─── drawio viewer (v0.9.5+) ────────────────────────────────────
    # Each method is a thin wrapper around DiagramsWindowManager;
    # see ``gui/diagrams_window.py`` for the actual logic. All
    # four return ``{"error": "..."}`` on failure so the JS side
    # can render a friendly message instead of crashing.

    def open_diagrams_window(self) -> dict[str, Any]:
        """Open an empty drawio viewer window."""
        logger.info("open_diagrams_window: called")
        try:
            return self._get_diagrams_manager().open_blank()
        except Exception as e:
            logger.error(
                "open_diagrams_window FAILED: %r\n%s",
                e, traceback.format_exc(),
            )
            return _err(f"open_diagrams_window failed: {e!r}")

    def open_diagrams_with_xml(
        self,
        title: str,
        xml: str,
    ) -> dict[str, Any]:
        """Open a drawio viewer loaded with the given XML.

        ``title`` is shown in the OS window title bar.
        ``xml`` must be non-empty; size-cap is enforced inside
        the manager (4 MB by default).
        """
        logger.info(
            "open_diagrams_with_xml: called title=%r xml_len=%d",
            title, len(xml or ""),
        )
        try:
            return self._get_diagrams_manager().open_with_xml(
                title or "Drawio Viewer", xml or "",
            )
        except Exception as e:
            logger.error(
                "open_diagrams_with_xml FAILED: %r\n%s",
                e, traceback.format_exc(),
            )
            return _err(f"open_diagrams_with_xml failed: {e!r}")

    def open_diagrams_with_path(
        self,
        title: str,
        path: str,
    ) -> dict[str, Any]:
        """Open a drawio viewer loaded from ``path`` inside
        the filesystem write sandbox."""
        logger.info(
            "open_diagrams_with_path: called title=%r path=%r",
            title, path,
        )
        try:
            return self._get_diagrams_manager().open_with_path(
                title or "Drawio Viewer", path or "",
            )
        except Exception as e:
            logger.error(
                "open_diagrams_with_path FAILED: %r\n%s",
                e, traceback.format_exc(),
            )
            return _err(f"open_diagrams_with_path failed: {e!r}")

    def diagrams_window_status(self) -> dict[str, Any]:
        """Return how many viewer windows are currently open.

        v0.9.5.8: the chat-toolbar entry point (the "📊 Diagrams
        Viewer" button that opened a BLANK viewer) was removed
        from the main GUI. The only remaining entry points are
        the per-message "View in Diagrams" buttons (which route
        through ``open_diagrams_with_xml``) and the public
        ``open_diagrams_window`` / ``open_diagrams_with_*`` API
        methods themselves. Kept as public API for external
        introspection.
        """
        try:
            return {
                "ok": True,
                "open_count": self._get_diagrams_manager().open_count,
            }
        except Exception as e:
            logger.error(
                "diagrams_window_status FAILED: %r\n%s",
                e, traceback.format_exc(),
            )
            return _err(f"diagrams_window_status failed: {e!r}")

    # ─── handle accessors (lazy, cached) ───────────────────────────
    def _get_store(self):
        if Api._store is None:
            logger.debug("lazy-init QdrantStore")
            from src.vector_store.qdrant_client import QdrantStore
            Api._store = QdrantStore()
        return Api._store

    def _get_retriever(self):
        if Api._retriever is None:
            logger.debug("lazy-init Retriever")
            from src.vector_store.retriever import Retriever
            Api._retriever = Retriever()
        return Api._retriever

    def _get_agent(self):
        # v0.9.16.3: locked — two concurrent first-polls (multiple
        # browser pages / prewarm thread + request thread) used to
        # race the check-then-set and build two Agents (each with its
        # own McpManager → every MCP server connected twice).
        with Api._agent_init_lock:
            if Api._agent is None:
                logger.debug("lazy-init Agent")
                from src.agent.agent import Agent
                Api._agent = Agent()
            return Api._agent

    # ─── agent prewarm (v0.9.11.1+) ────────────────────────────────
    def _kick_prewarm(self) -> None:
        """Start the background prewarm thread exactly once per
        process. Called from the first get_status poll (the GUI
        front-end polls it right after boot).

        v0.9.16.3: the flag check-and-set is locked — two pages
        booting simultaneously (web mode) used to spawn two prewarm
        threads, which raced ``_get_agent`` into building two Agents
        (every MCP server connected twice)."""
        with Api._agent_init_lock:
            if Api._prewarm_started:
                return
            Api._prewarm_started = True
            Api._prewarm_done = False
        try:
            threading.Thread(
                target=self._prewarm_agent,
                daemon=True,
                name="agent-prewarm",
            ).start()
            logger.info("prewarm: background agent build started")
        except Exception as e:
            logger.debug("prewarm thread start failed: %r", e)

    def _prewarm_agent(self) -> None:
        """Build the Agent's expensive lazies OFF the chat path.

        Measured on this box (v0.9.11.0 logs): ``agent.mcp_manager``
        costs ~15 s on first access (11 LocalHost registrations +
        every remote MCP connect in config/mcp.json) and the module
        import + provider resolution add seconds more. Prewarming at
        GUI boot moves that cost off the user's first message. The
        ``Agent.mcp_manager`` property is double-checked-locked, so a
        chat turn racing this thread builds it exactly once. All
        failures are swallowed — the lazy properties simply build
        later on demand.
        """
        # v0.9.109.3 — ensure the Qdrant server is reachable BEFORE
        # building the agent: a dead standalone qdrant.exe (machine
        # reboot without the start bat) used to surface as 10061
        # errors mid-conversation; now the boot heals it.
        try:
            self._ensure_qdrant_server()
        except Exception as e:  # noqa: BLE001 — boot must not break
            logger.debug("qdrant auto-heal probe failed: %r", e)
        # v0.9.109.4 — same treatment for the firefox_craw middle
        # service (dist/service_server.exe): dead service = every
        # browser_* tool errors mid-conversation despite a healthy
        # stdio MCP connect.
        try:
            self._ensure_firefox_service()
        except Exception as e:  # noqa: BLE001 — boot must not break
            logger.debug("firefox service auto-heal probe failed: %r", e)
        try:
            agent = self._get_agent()
            _ = agent.mcp_manager  # full local-host + remote build
            _ = agent.client      # provider + model resolution
            logger.info(
                "prewarm: agent ready (provider=%s model=%s)",
                getattr(agent, "_provider", None),
                getattr(agent, "_model", None),
            )
            # v0.9.22.0: auto-resume the scheduler daemon (root cause
            # of the missed 2026-09-07T14:00:00 fire — the daemon is a
            # THREAD that dies with the process; v0.8.6.3 only
            # auto-starts it when the LLM ADDS a schedule, so every
            # GUI/web restart silently left enabled schedules unfired).
            self._resume_scheduler_daemon(agent)
            # v0.9.127.0 — opt-in weekly memory maintenance schedule
            self._ensure_memory_maintenance_schedule(agent)
            # v0.9.133.3 — opt-in T+1 session-summary compaction schedule
            self._ensure_session_summary_schedule(agent)
            # v0.9.25.0: auto-resume the webhook HTTP listener (same
            # thread-dies-with-process reasoning — triggers persist
            # in webhooks/*.json but their URLs die with the process).
            self._resume_webhook_server(agent)
            # v0.9.27.0: auto-resume the Telegram bot polling daemon
            # (bidirectional chat — same thread-dies-with-process
            # reasoning; gated by env + token + allowlist).
            self._resume_telegram_bot(agent)
            # v0.9.41.0: auto-resume ONVIF event watchers (motion →
            # snapshot / webhook; configs survive in
            # onvif_watches.json, threads die with the process).
            self._resume_onvif_watches(agent)
            # v0.9.69.0: auto-resume the Teams desktop-assist watch
            # thread (screen-watch → fresh-Agent judge-then-act; the
            # state persists in teams_assist/state.json, the thread
            # dies with the process — same resume reasoning).
            self._resume_teams_assist(agent)
            # v0.9.42.0: opt-in config hot-reload watcher (.env /
            # config/mcp.json mtime polling → reload + service restart).
            self._start_config_watcher(agent)
            # v0.9.108.0: fail-closed task notify hook + progress
            # monitor (QDRANT_TASK_NOTIFY=on + token + allowlist).
            self._wire_task_notify()
            # v0.9.112.0: periodic llama.cpp embedding watchdog (the
            # third auto-heal — boot heal cannot catch the ZOMBIE
            # state: process alive, every embeddings POST failing).
            self._start_embed_watchdog()
            # v0.9.122.2 — the SAME watchdog machinery for the gemma2
            # media server (:8081; gated by QDRANT_GEMMA2_WATCHDOG /
            # the media-embedding gate — no-op when off).
            self._start_gemma2_watchdog()
        except Exception as e:
            logger.debug(
                "prewarm failed (lazy paths will retry on demand): %r",
                e,
            )
        finally:
            # v0.9.80.1: readiness polls this flag — the mcp slot
            # reports "初始化中…" until the fleet build finishes.
            Api._prewarm_done = True

    # ─── qdrant server auto-heal (v0.9.109.3) ──────────────────────
    _QDRANT_HEAL_WAIT_S = 30

    def _ensure_qdrant_server(self) -> None:
        """Probe ``<QDRANT_URL>/readyz``; when refused AND the URL is a
        LOOPBACK address, spawn the Qdrant start bat detached and wait
        until it turns green (or ~30 s). Loopback-only by design — a
        REMOTE Qdrant down is an ops problem, not ours to start.
        Env: ``QDRANT_SPAWN_QDRANT=off`` opts out;``QDRANT_QDRANT_START_BAT``
        overrides the launcher path (default ``C:/qdrant/start-qdrant.bat``)."""
        import urllib.request

        from src.config import config as cfg

        url = (cfg.QDRANT_URL or "").rstrip("/")
        if not url:
            return
        if not any(host in url for host in (
                "127.0.0.1", "localhost", "[::1]", "::1")):
            logger.debug(
                "qdrant auto-heal skipped: %s is not loopback", url)
            return
        if (os.environ.get("QDRANT_SPAWN_QDRANT", "") or "").strip().lower() \
                in ("off", "0", "false", "no"):
            logger.info(
                "qdrant auto-heal skipped (QDRANT_SPAWN_QDRANT=off)")
            return
        readyz = url + "/readyz"
        try:
            with urllib.request.urlopen(readyz, timeout=3) as r:
                if 200 <= r.status < 300:
                    return  # already healthy
        except Exception:
            pass  # down — attempt the spawn below
        bat = os.environ.get("QDRANT_QDRANT_START_BAT", "") \
            or r"C:\qdrant\start-qdrant.bat"
        if not os.path.isfile(bat):
            logger.warning(
                "qdrant down AND start bat missing (%s) — cannot "
                "auto-heal; start Qdrant manually.", bat)
            return
        DETACHED = 0x00000008 | 0x00000200 | 0x08000000
        try:
            import subprocess as _sp

            _sp.Popen(
                ["cmd", "/c", bat],
                cwd=(os.path.dirname(bat) or "."),
                creationflags=DETACHED,
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                stdin=_sp.DEVNULL,
            )
        except Exception as e:
            logger.warning("qdrant spawn failed: %r", e)
            return
        deadline = time.time() + self._QDRANT_HEAL_WAIT_S
        while time.time() < deadline:
            time.sleep(2)
            try:
                with urllib.request.urlopen(readyz, timeout=3) as r:
                    if 200 <= r.status < 300:
                        logger.info(
                            "qdrant auto-heal: server ready after "
                            "~%ds (%s)",
                            int(self._QDRANT_HEAL_WAIT_S
                                - (deadline - time.time())),
                            url,
                        )
                        return
            except Exception:
                continue
        logger.warning(
            "qdrant auto-heal: still unreachable after %ss "
            "(chat tools will degrade until it is up)",
            self._QDRANT_HEAL_WAIT_S,
        )

    # ─── firefox_craw middle service auto-heal (v0.9.109.4) ────────
    _FFSVC_HEAL_WAIT_S = 30

    def _ensure_firefox_service(self) -> None:
        """Probe the firefox_craw three-stage middle service
        (``mcp_getweb/dist/service_server.exe``, HTTP API on
        ``127.0.0.1:<MCP_SERVICE_PORT>`` default 8766) via ``GET /health``;
        when refused, spawn the detached launcher bat and wait until it
        turns green (or ~30 s). Mirrors ``_ensure_qdrant_server``.
        Gates: heal only runs when `firefox_craw` is in the active MCP
        surface (``MCP_ACTIVE_SERVERS`` unset = full surface = included);
        ``QDRANT_SPAWN_FIREFOX_SERVICE=off`` opts out;
        ``QDRANT_FIREFOX_SERVICE_START_BAT`` overrides the launcher path
        (default ``C:/Users/humphery/coding/mcp_getweb/run_service.bat``)."""
        import urllib.request

        port = (os.environ.get("MCP_SERVICE_PORT", "") or "8766").strip()
        active = (os.environ.get("MCP_ACTIVE_SERVERS", "") or "")
        if active.strip() and "firefox_craw" not in active.lower():
            logger.debug(
                "firefox service auto-heal skipped: firefox_craw not in "
                "MCP_ACTIVE_SERVERS")
            return
        if (os.environ.get("QDRANT_SPAWN_FIREFOX_SERVICE", "") or "").strip() \
                .lower() in ("off", "0", "false", "no"):
            logger.info(
                "firefox service auto-heal skipped "
                "(QDRANT_SPAWN_FIREFOX_SERVICE=off)")
            return
        url = f"http://127.0.0.1:{port}/health"
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if 200 <= r.status < 300:
                    return  # already healthy
        except Exception:
            pass  # down — attempt the spawn below
        bat = os.environ.get("QDRANT_FIREFOX_SERVICE_START_BAT", "") or \
            r"C:\Users\humphery\coding\mcp_getweb\run_service.bat"
        if not os.path.isfile(bat):
            logger.warning(
                "firefox middle service down AND launcher missing (%s) — "
                "cannot auto-heal; browser tools will degrade until it "
                "is started manually.", bat)
            return
        DETACHED = 0x00000008 | 0x00000200 | 0x08000000
        try:
            import subprocess as _sp

            _sp.Popen(
                ["cmd", "/c", bat],
                cwd=(os.path.dirname(bat) or "."),
                creationflags=DETACHED,
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                stdin=_sp.DEVNULL,
            )
        except Exception as e:
            logger.warning("firefox service spawn failed: %r", e)
            return
        deadline = time.time() + self._FFSVC_HEAL_WAIT_S
        while time.time() < deadline:
            time.sleep(2)
            try:
                with urllib.request.urlopen(url, timeout=3) as r:
                    if 200 <= r.status < 300:
                        logger.info(
                            "firefox service auto-heal: server ready "
                            "after ~%ds (port %s)",
                            int(self._FFSVC_HEAL_WAIT_S
                                - (deadline - time.time())),
                            port,
                        )
                        return
            except Exception:
                continue
        logger.warning(
            "firefox service auto-heal: still unreachable after %ss "
            "on port %s (browser tools will degrade until it is up)",
            self._FFSVC_HEAL_WAIT_S,
            port,
        )

    # ─── llama.cpp embedding watchdog (v0.9.112.0) ─────────────────
    def _start_embed_watchdog(self) -> None:
        """Start the periodic llama.cpp embeddings watchdog (idempotent
        daemon thread). Mirrors the v0.9.109.x boot-heal family but
        PERIODIC — only a real embeddings POST can detect the zombie
        state (process alive, /v1/models 200, every POST failing).
        All failures swallowed; ``QDRANT_LLAMACPP_WATCHDOG=off`` makes
        this a no-op (the watchdog's own ``enabled`` flag reads the
        same env)."""
        try:
            from src.embed_watchdog import start_embed_watchdog

            started = start_embed_watchdog()
            logger.info(
                "embed watchdog %s",
                "started" if started else
                "not started (off or already running)",
            )
        except Exception as e:  # noqa: BLE001 — boot must not break
            logger.debug("embed watchdog start failed: %r", e)

    def _start_gemma2_watchdog(self) -> None:
        """v0.9.122.2 — start the gemma2 media-server watchdog (:8081;
        same probe/heal engine, own URL/bat/gate). Idempotent, never
        raises, no-op when the gate is off."""
        try:
            from src.embed_watchdog import start_gemma2_watchdog

            if start_gemma2_watchdog():
                logger.info("gemma2 watchdog started (8081 media server)")
        except Exception as e:  # noqa: BLE001 — boot must not break
            logger.debug("gemma2 watchdog start failed: %r", e)

    def embedding_watchdog_status(self) -> dict[str, Any]:
        """Watchdog snapshot for the Admin tab (never raises). The
        ``gemma2`` sub-key appears only when its gate is on."""
        try:
            from src.embed_watchdog import get_watchdog

            out = get_watchdog().status()
        except Exception as e:  # noqa: BLE001
            return {"enabled": False, "error": repr(e)[:200]}
        try:
            from src.embed_watchdog import (
                gemma2_watchdog_enabled, get_gemma2_watchdog,
            )

            if gemma2_watchdog_enabled():
                out["gemma2"] = get_gemma2_watchdog().status()
        except Exception as e:  # noqa: BLE001
            out["gemma2"] = {"enabled": False, "error": repr(e)[:200]}
        return out

    def _wire_task_notify(self) -> None:
        """v0.9.108.0 — background-task completion/progress notify.

        Fail-closed: requires ``QDRANT_TASK_NOTIFY=on`` +
        ``TELEGRAM_BOT_TOKEN`` + an allowlisted chat; default off =
        silent legacy (the registry ships callback=None, sends nothing).
        Terminal events and (opt-in) periodic progress are delivered by
        ``TelegramBotClient.send_message`` to
        ``QDRANT_TASK_NOTIFY_CHAT_ID`` (default: first allowed chat).
        """
        try:
            enabled = (
                (os.environ.get("QDRANT_TASK_NOTIFY", "") or "").strip()
                .lower() in ("on", "1", "true", "yes")
            )
            if not enabled:
                return
            from src.agent import task_runner
            from src.agent.telegram_bot import (
                TelegramBotClient, _load_allowed_ids,
            )

            token = (os.environ.get("TELEGRAM_BOT_TOKEN", "")
                     or "").strip()
            ids = _load_allowed_ids()
            if not token or not ids:
                logger.info(
                    "task-notify disabled: TELEGRAM_BOT_TOKEN / "
                    "allowlist missing",
                )
                return
            raw_chat = (os.environ.get("QDRANT_TASK_NOTIFY_CHAT_ID", "")
                        or "").strip()
            chat = int(raw_chat) if raw_chat.isdigit() else ids[0]
            client = TelegramBotClient(token)

            def _cb(event: dict) -> None:
                if not isinstance(event, dict):
                    return
                if event.get("kind") in ("progress", "plan"):
                    # progress = monitor tick summary; plan = compiled
                    # plan's final verdict (both carry a ready "text").
                    text = str(event.get("text") or "").strip()
                    if text:
                        client.send_message(chat, text)
                    return
                ok = event.get("status") == "done"
                body = ("✅ 背景任務完成" if ok
                        else "❌ 背景任務失敗")
                body += f"：〔{event.get('title')}〕"
                if ok and event.get("answer_chars") is not None:
                    body += f" — 回覆 {event['answer_chars']} 字"
                if event.get("error"):
                    body += f" — {str(event.get('error'))[:200]}"
                body += (
                    f"\n(sid: {event.get('sid')}｜"
                    f"get_task_result({event.get('task_id')}) 讀全文)"
                )
                client.send_message(chat, body)

            task_runner.set_notify_callback(_cb)
            # v0.9.109.1 — the skill-compiler's plan-level verdict rides
            # the SAME client/chat (fails silently when unused).
            try:
                from src.agent import skill_compiler as _skc

                _skc.set_plan_notify_callback(_cb)
            except Exception:
                pass
            raw_iv = (os.environ.get("QDRANT_TASK_MONITOR_INTERVAL_S",
                                     "") or "").strip()
            try:
                interval = int(raw_iv) if raw_iv else 300
            except ValueError:
                interval = 300
            task_runner.start_monitor(interval)
            logger.info(
                "task-notify wired (chat=%s, monitor=%ds, "
                "notify=%s)", chat, interval if interval > 0 else 0,
                enabled,
            )
        except Exception as e:  # noqa: BLE001 — boot must not break
            logger.debug("task-notify wiring failed (non-fatal): %r", e)

    def _resume_webhook_server(self, agent) -> None:
        """v0.9.25.0 — auto-resume the webhook HTTP listener at boot.

        Triggers persist (``$QDRANT_RAG_HOME/webhooks/*.json``) but
        the listener is a thread + bound socket inside the app
        process — every GUI/web restart kills it. The first
        get_status poll (prewarm) re-binds whenever at least one
        trigger is ``enabled`` (and lifecycle status active). The
        per-trigger access keys stay valid across restarts.

        Opt out with ``QDRANT_WEBHOOK_AUTORESUME=off``. All failures
        are swallowed (non-fatal — a bind failure is only a warning
        and the LLM tools still work as before).
        """
        mode = (os.environ.get("QDRANT_WEBHOOK_AUTORESUME", "on")
                or "on").strip().lower()
        if mode in ("off", "0", "no", "false"):
            logger.debug(
                "webhook auto-resume disabled "
                "(QDRANT_WEBHOOK_AUTORESUME=%s)", mode,
            )
            return
        try:
            from src.agent.webhook import WebhookManager

            enabled = [
                w for w in WebhookManager().list()
                if w.enabled and (not w.status or w.status == "active")
            ]
            if not enabled:
                logger.debug(
                    "webhook auto-resume skipped: no enabled triggers",
                )
                return
            agent.start_webhook_server()  # idempotent + bind-failure-safe
            logger.info(
                "webhook server auto-resumed (%d enabled trigger(s): %s)",
                len(enabled),
                ", ".join(w.name or w.id for w in enabled[:5]),
            )
        except Exception as e:
            logger.warning(
                "webhook auto-resume failed (non-fatal; the LLM "
                "webhook tools still work): %r", e,
            )

    # ── hot reload + service restart (v0.9.42.0+) ─────────────────

    def reload_runtime_config(self, restart_services: bool = True) -> dict:
        """Reload .env/config on the fly + restart daemon services +
        reconnect the remote MCP fleet (config/mcp.json).

        Bridge for the Admin-tab 🔄 button and the config watcher:
        ``Agent.reload_runtime`` does the heavy lifting (config.reload
        → LLM chain reset → conditional daemon restarts); the MCP
        remote reconnection happens here so an edited mcp.json is
        picked up too. Returns a summary dict for the UI toast.
        """
        try:
            agent = self._get_agent()
            out = agent.reload_runtime(restart_services=restart_services)
            if not out.get("ok"):
                return {"status": "error", "error": out.get("error")}
            try:
                out["mcp"] = agent.mcp_manager.reconnect_remote_servers()
            except Exception as e:
                out["mcp"] = {"ok": False, "error": repr(e)}
            return {"status": "ok", **out}
        except Exception as e:
            return {"status": "error", "error": repr(e)}

    def _start_config_watcher(self, agent) -> None:
        """v0.9.42.0 — opt-in .env / mcp.json mtime watcher at boot.

        ``QDRANT_CONFIG_HOT_RELOAD=on`` (default off) starts a daemon
        thread that calls ``reload_runtime_config`` whenever a watched
        file changes. The GUI button / CLI ``reload`` work regardless.
        """
        try:
            from src.config_watch import watcher_enabled

            if not watcher_enabled():
                return
            if getattr(self, "_config_watcher", None) \
                    and self._config_watcher.alive:
                return

            def _on_change(changed: list) -> None:
                logger.info(
                    "[config-watcher] change detected: %s — reloading",
                    changed,
                )
                out = self.reload_runtime_config()
                logger.info(
                    "[config-watcher] reload done: changed=%s "
                    "services=%s mcp=%s",
                    out.get("changed"), out.get("services"),
                    (out.get("mcp") or {}).get("rebuilt"),
                )

            from src.config_watch import ConfigWatcher

            self._config_watcher = ConfigWatcher(_on_change)
            self._config_watcher.start()
            logger.info(
                "config watcher started (%s, interval=%ss)",
                ", ".join(str(p) for p in self._config_watcher._paths),
                self._config_watcher._interval,
            )
        except Exception as e:
            logger.debug("config watcher start failed (non-fatal): %r", e)

    def _resume_onvif_watches(self, agent) -> None:
        """v0.9.41.0 — auto-resume ONVIF event watcher threads at boot.

        Watch configs persist in ``onvif_watches.json`` but the
        polling threads die with the process (same reasoning as the
        scheduler/webhook/telegram resumes above). ``QDRANT_ONVIF_
        WATCH_AUTORESUME=off`` opts out. All failures swallowed
        (non-fatal — the LLM watch tools still work on demand).
        """
        mode = (os.environ.get("QDRANT_ONVIF_WATCH_AUTORESUME", "on")
                or "on").strip().lower()
        if mode in ("off", "0", "no", "false"):
            logger.debug(
                "onvif-watch auto-resume disabled "
                "(QDRANT_ONVIF_WATCH_AUTORESUME=%s)", mode,
            )
            return
        try:
            host = getattr(agent.mcp_manager, "_onvif_local_host", None)
            if host is None:
                return
            started = host.resume_watches()
            if started:
                logger.info(
                    "onvif watch auto-resumed (%d watcher(s))",
                    started,
                )
        except Exception as e:
            logger.debug(
                "onvif-watch auto-resume failed (non-fatal): %r", e,
            )

    def _resume_teams_assist(self, agent) -> None:
        """v0.9.69.0 — auto-resume the Teams desktop-assist watch
        thread at boot.

        Conditions (ALL must hold):
          * ``QDRANT_TEAMS_ASSIST_AUTORESUME`` != off (default on)
          * the persisted ``teams_assist/state.json`` watch record
            says ``running`` (i.e. the watch was running when the
            process died)
          * ``TEAMS_ASSIST_ALLOW_CHATS`` non-empty (fail-closed —
            the watcher refuses to start without it anyway)

        All failures swallowed (non-fatal). The watch is pure local
        screen polling (no network); mock mode never grabs the real
        screen. ``agent.resume_teams_assist`` is idempotent.
        """
        try:
            mode = (os.environ.get("QDRANT_TEAMS_ASSIST_AUTORESUME", "on")
                    or "on").strip().lower()
            if mode in ("off", "0", "no", "false"):
                logger.debug(
                    "teams-assist auto-resume disabled "
                    "(QDRANT_TEAMS_ASSIST_AUTORESUME=%s)", mode,
                )
                return
            if not os.environ.get("TEAMS_ASSIST_ALLOW_CHATS", "").strip():
                logger.debug(
                    "teams-assist auto-resume skipped "
                    "(TEAMS_ASSIST_ALLOW_CHATS empty — fail-closed)",
                )
                return
            started = agent.resume_teams_assist()  # idempotent
            if started:
                logger.info(
                    "teams-assist watch auto-resumed (%d watcher(s))",
                    started,
                )
        except Exception as e:
            logger.debug(
                "teams-assist auto-resume failed (non-fatal): %r", e,
            )

    def _resume_telegram_bot(self, agent) -> None:
        """v0.9.27.0 — auto-resume the Telegram bot polling daemon
        (bidirectional chat) at boot.

        Conditions (ALL must hold):
          * ``QDRANT_TELEGRAM_BOT_POLLING`` != off (default on)
          * ``TELEGRAM_BOT_TOKEN`` configured
          * ``TELEGRAM_ALLOWED_CHAT_IDS`` non-empty (fail-closed — an
            empty allowlist would make the daemon ignore everything)

        All failures swallowed (non-fatal). Polling is pure OUTBOUND
        HTTPS to api.telegram.org — no inbound port, no https needed.
        """
        try:
            mode = (os.environ.get("QDRANT_TELEGRAM_BOT_POLLING", "on")
                    or "on").strip().lower()
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            allowlist = os.environ.get(
                "TELEGRAM_ALLOWED_CHAT_IDS", "",
            ).strip()
            ready = (
                mode not in ("off", "0", "no", "false")
                and token and allowlist
            )
            if not ready:
                logger.debug(
                    "telegram-bot auto-resume skipped "
                    "(QDRANT_TELEGRAM_BOT_POLLING=off or missing "
                    "token/allowlist)",
                )
                return
            agent.start_telegram_bot()  # idempotent
            logger.info(
                "telegram-bot auto-resumed (bidirectional chat on)",
            )
        except Exception as e:
            logger.warning(
                "telegram-bot auto-resume failed (non-fatal): %r", e,
            )

    def _ensure_memory_maintenance_schedule(self, agent) -> None:
        """v0.9.127.0 — opt-in weekly memory-maintenance schedule
        (``QDRANT_MEMORY_MAINTENANCE=on``). Auto-creates ONE schedule
        (「記憶維護（每週）」, cron ``0 9 * * 1``) whose prompt fires
        ``suggest_memory_cleanup`` and REPORTS the dry-run candidates —
        it NEVER purges by itself (purge_memories / forget_memory stay
        confirm-gated; a real cleanup waits for the user's explicit
        approve in a following turn). Same-name schedule exists =
        no-op. All failures swallowed (boot must never break)."""
        try:
            flag = (os.environ.get("QDRANT_MEMORY_MAINTENANCE", "")
                    or "").strip().lower()
            if flag not in ("on", "1", "yes", "true"):
                return
            from src.agent.scheduler import Schedule, ScheduleManager

            mgr = ScheduleManager()
            if mgr.find_by_name("記憶維護（每週）"):
                return
            sched = Schedule(
                name="記憶維護（每週）",
                prompt=(
                    "長期記憶每週維護（自動排程 — 只報告不清除）：\n"
                    "1. 呼叫 suggest_memory_cleanup"
                    "（older_than_s=2592000）。\n"
                    "2. 把候選整理成表格回報（id / kind / 內容摘要 / age）。"
                    "\n3. 不要自行呼叫 purge_memories 或 forget_memory —"
                    " 等使用者核可後才清理；清單為空就簡短回報「無候選」。"
                ),
                schedule_type="cron",
                cron="0 9 * * 1",
                enabled=True,
            )
            mgr.save(sched)
            logger.info(
                "[memory] weekly maintenance schedule created: %s",
                getattr(sched, "id", ""),
            )
            try:
                agent.start_scheduler()
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "[memory] maintenance schedule ensure failed: %r", e)

    def _ensure_session_summary_schedule(self, agent) -> None:
        """v0.9.133.3 — opt-in daily T+1 session-summary compaction
        (``QDRANT_SESSION_SUMMARY_CRON``, e.g. ``0 3 * * *``; empty =
        off). Auto-creates ONE schedule (「每日對話摘要沉澱（T+1）」)
        whose LLM pin comes from ``QDRANT_SESSION_SUMMARY_LLM``
        (default ``ollama:qwen2.5:7b`` — the report §6.2's cheap local
        CPU pass; gpt-oss:20b was named there but is not installed).
        The fire prompt drives ``summarize_sessions`` (memory-local)
        — reports ONLY; it never purges. Same-name schedule exists =
        no-op; every failure swallowed (boot must never break)."""
        try:
            cron = (os.environ.get("QDRANT_SESSION_SUMMARY_CRON", "")
                    or "").strip()
            if not cron:
                return
            from src.agent.scheduler import Schedule, ScheduleManager
            from src.agent.session_compactor import SCHEDULE_PROMPT

            mgr = ScheduleManager()
            if mgr.find_by_name("每日對話摘要沉澱（T+1）"):
                return
            llm = (os.environ.get("QDRANT_SESSION_SUMMARY_LLM", "")
                   or "ollama:qwen2.5:7b").strip() or None
            sched = Schedule(
                name="每日對話摘要沉澱（T+1）",
                prompt=SCHEDULE_PROMPT,
                schedule_type="cron",
                cron=cron,
                enabled=True,
                llm=llm,
            )
            mgr.save(sched)
            logger.info(
                "[memory] session-summary schedule created: %s (cron=%s"
                " llm=%s)", getattr(sched, "id", ""), cron, llm,
            )
            try:
                agent.start_scheduler()
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "[memory] session-summary schedule ensure failed: %r", e)

    def _resume_scheduler_daemon(self, agent) -> None:
        """v0.9.22.0 — auto-resume the scheduler daemon at boot.

        Schedules persist (``$QDRANT_RAG_HOME/schedules/*.json``) but
        the daemon is a thread inside the app process — every GUI/web
        restart kills it, and the v0.8.6.3 auto-start only fires when
        the LLM ADDS a schedule. With this, the first get_status poll
        (prewarm) resumes the daemon whenever at least one schedule is
        ``enabled`` (and lifecycle status active) — the missed
        ``next_fire_at`` catches up on the first tick (scheduler.tick_
        once fires anything whose ``next_fire_at`` is in the past).

        Opt out with ``QDRANT_SCHEDULER_AUTORESUME=off``. All failures
        are swallowed (non-fatal) — the GUI Schedules tab button and
        the LLM tools still work as before.
        """
        mode = (os.environ.get("QDRANT_SCHEDULER_AUTORESUME", "on")
                or "on").strip().lower()
        if mode in ("off", "0", "no", "false"):
            logger.debug(
                "scheduler auto-resume disabled "
                "(QDRANT_SCHEDULER_AUTORESUME=%s)", mode,
            )
            return
        try:
            from src.agent.scheduler import ScheduleManager

            enabled = [
                s for s in ScheduleManager().list()
                if s.enabled and (not s.status or s.status == "active")
            ]
            if not enabled:
                logger.debug(
                    "scheduler auto-resume skipped: no enabled schedules",
                )
                return
            agent.start_scheduler()  # idempotent (thread-safe start)
            logger.info(
                "scheduler daemon auto-resumed (%d enabled schedule(s): %s)",
                len(enabled),
                ", ".join(s.name or s.id for s in enabled[:5]),
            )
        except Exception as e:
            logger.warning(
                "scheduler auto-resume failed (non-fatal; the GUI "
                "Schedules tab button still works): %r", e,
            )

    # ─── open-with wiring (v0.9.6.0+) ──────────────────────────────
    def _wire_openwith_diagrams_opener(self, agent) -> None:
        """Give the OpenWithLocalHost MCP host a way to open
        ``.drawio`` artifacts inside THIS GUI's Drawio Viewer window.

        Mirrors the scheduler auto-start callback pattern: the
        callback is idempotent to set (``McpManager.set_diagrams_opener``
        checks before pushing), so calling this on every chat turn
        is cheap. Exceptions are swallowed — a mis-wired opener must
        never break a chat turn; the MCP host falls back to the OS
        file association on its own.
        """
        try:
            mgr = agent.mcp_manager
            if getattr(mgr, "_openwith_local_host", None) is None:
                return
            if getattr(mgr, "diagrams_opener_callback", None) is None:
                mgr.set_diagrams_opener(self._open_drawio_via_viewer)
        except Exception as e:
            logger.debug(
                "openwith diagrams-opener wire failed: %r", e,
            )

    def _open_drawio_via_viewer(self, path: str) -> dict[str, Any]:
        """Callback handed to OpenWithLocalHost: open a ``.drawio``
        file in the in-GUI Drawio Viewer window. Must return a dict
        (``{ok: ...}`` or ``{error: ...}``) — the host inspects
        ``error`` to decide whether to fall back to the OS
        association."""
        try:
            return self._get_diagrams_manager().open_with_path(
                "Diagram from chat", path,
            )
        except Exception as e:
            logger.warning(
                "openwith drawio viewer open failed for %r: %r",
                path, e,
            )
            return {"error": f"drawio viewer failed: {e!r}"}

    def _get_session_mgr(self):
        if Api._session_mgr is None:
            logger.debug("lazy-init SessionManager")
            from src.agent.session import SessionManager
            Api._session_mgr = SessionManager()
        return Api._session_mgr

    def _get_embedder(self):
        if Api._embedder is None:
            logger.debug("lazy-init Embedder")
            from src.ingest.embedder import Embedder
            Api._embedder = Embedder()
        return Api._embedder

    def _get_diagrams_manager(self):
        """v0.9.5+ drawio viewer window manager.

        Lazy because the constructor resolves the filesystem
        write root (which on first call might mkdir() the
        sandbox). We don't want that side-effect at GUI import
        time — only when the user actually opens a viewer.
        """
        if Api._diagrams_manager is None:
            logger.debug("lazy-init DiagramsWindowManager")
            from gui.diagrams_window import DiagramsWindowManager
            Api._diagrams_manager = DiagramsWindowManager()
        return Api._diagrams_manager

    # ── 筆記本 UI（v0.9.95.0+；本地筆記本 store） ─────────────────

    def _notes_engine(self):
        """Lazy shared KeepEngine for the Notes UI. The UI ALWAYS edits
        the LOCAL persistent note book ($QDRANT_RAG_HOME/notes/
        notes.json) — the same store the LLM's keep-local local mode
        reads/writes; independent of KEEP_MODE."""
        if getattr(self, "_notes_engine_ref", None) is None:
            from src.keep_tools import KeepEngine

            self._notes_engine_ref = KeepEngine()
        return self._notes_engine_ref

    @staticmethod
    def _notes_payload(payload: Any) -> dict:
        """v0.9.96.3 — the pywebview bridge AND the web shim are
        POSITIONAL-ONLY (`fn(*args)`): the frontend sends ONE dict
        payload (the save_credential/add_schedule convention). The
        original v0.9.95.0 signatures used named params, so the whole
        object landed on the first param -> TypeError. Accept a dict;
        tolerate a bare id string for get/delete."""
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _payload_int(v: Any, default: int) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def notes_page(self, payload: dict | None = None) -> dict:
        """Paged notes browse for the web/mobile Notes tab — payload =
        ``{page, page_size, query, category, date_from, date_to}``.
        NEVER dumps the whole list (assume the store can grow huge):
        filters (title/content keyword, category, updateTime date
        range) + offset/limit pagination with the total count. The
        frontend fetches page by page (async)."""
        try:
            p = self._notes_payload(payload)
            eng = self._notes_engine()
            page = max(1, self._payload_int(p.get("page"), 1))
            page_size = self._payload_int(p.get("page_size"), 50)
            out = eng.local_search(
                query=str(p.get("query") or ""),
                category=str(p.get("category") or ""),
                date_from=str(p.get("date_from") or ""),
                date_to=str(p.get("date_to") or ""),
                offset=(page - 1) * page_size,
                limit=page_size,
            )
            out["page"] = page
            out["page_size"] = max(1, min(200, page_size))
            out["categories"] = eng.local_categories()
            return out
        except Exception as e:
            return {"error": repr(e)}

    def notes_get(self, payload: dict | str | None = None) -> dict:
        """payload = ``{note_id}`` (or a bare id string)."""
        try:
            if isinstance(payload, dict):
                nid = str(payload.get("note_id") or "").strip()
            else:
                nid = str(payload or "").strip()
            out = self._notes_engine().local_get(nid)
            return out if out else {"error": f"找不到記事 {nid}"}
        except Exception as e:
            return {"error": repr(e)}

    def notes_save(self, payload: dict | None = None) -> dict:
        """Create (note_id empty) / update a note from the Notes UI.
        payload = ``{note_id, title, body, category}``. UI actions are
        user-driven (button click) — the MCP per-call confirm gate is
        an LLM guard, not a UI one, so confirm=True implicitly."""
        try:
            p = self._notes_payload(payload)
            eng = self._notes_engine()
            title = str(p.get("title") or "").strip()[:200]
            body = str(p.get("body") or "")[:500000]
            category = str(p.get("category") or "").strip()[:80]
            if not title:
                return {"error": "title 必填"}
            nid = str(p.get("note_id") or "").strip()
            if nid:
                out = eng.local_update(nid, title=title, body=body,
                                       category=category)
                if out is None:
                    return {"error": f"找不到記事 {nid}"}
                return out
            return eng.local_create(title, body, category=category)
        except Exception as e:
            return {"error": repr(e)}

    def notes_delete(self, payload: dict | str | None = None) -> dict:
        """payload = ``{note_id}`` (or a bare id string)."""
        try:
            if isinstance(payload, dict):
                nid = str(payload.get("note_id") or "").strip()
            else:
                nid = str(payload or "").strip()
            eng = self._notes_engine()
            if eng.local_delete(nid):
                return {"ok": True, "deleted": nid}
            return {"error": f"找不到記事 {nid}"}
        except Exception as e:
            return {"error": repr(e)}

    def notes_categories(self) -> dict:
        try:
            return {"categories": self._notes_engine().local_categories()}
        except Exception as e:
            return {"error": repr(e)}

    _HW_OCR_PROMPT = (
        "這是一張手寫記事的圖片。請逐字轉寫（transcribe）所有手寫文字："
        "繁體中文照原樣輸出、英文與數字也原樣保留；保持原文的行與段落"
        "分隔；只輸出轉寫的文字本身，不要加任何說明、標題或註解。"
    )
    # v0.9.125.6 — PDF-underlay mode: the transcription must be the
    # HANDWRITING only; the printed PDF text baked into the image is
    # reference, not content.
    _HW_OCR_PDF_SUFFIX = (
        "（注意：圖片底層來自一份 PDF 文件的印刷內容，僅為批註底稿——"
        "請只轉寫「手寫」的墨跡文字，忽略印刷體排版文字。）"
    )
    _HW_PDF_MAX_PAGES = 10        # = the per-page archive cap (125.1)
    _HW_PDF_FILE_CAP = 50 * 1024 * 1024   # source PDF size cap
    _HW_PDF_PAGE_B64_CAP = 3 * 1024 * 1024  # per-page PNG base64 payload

    def notes_handwriting_save(self, payload: dict | None = None) -> dict:
        """v0.9.125.0 — the ✍️ handwritten-note flow: the web/mobile
        canvas POSTs a base64 PNG here; the host (1) ARCHIVES the
        image into ``$QDRANT_RAG_HOME/handwriting/``, (2) runs OCR
        through the project vision chain (ImageDescriber + a
        transcription prompt — sha1-cached), (3) archives a
        Notes-store note (category default 手寫), and (4) BUILDS THE
        INDEX by ingesting the .txt transcript through the SAME
        single-file ingest the 文件 tab uses (chunk → embed → Qdrant,
        so semantic search finds the handwritten note by CONTENT).
        Honest failure semantics: OCR-empty still archives the
        note+PNG and indexes the title."""
        import base64

        try:
            p = payload if isinstance(payload, dict) else {}
            data_url = str(p.get("data_url") or "")
            m = re.match(
                r"^data:image/(png|jpeg);base64,(.+)$",
                data_url, re.DOTALL,
            )
            if not m:
                return {"error": (
                    "data_url must be a base64 image/png or "
                    "image/jpeg data URL")}
            try:
                raw = base64.b64decode(m.group(2))
            except Exception as e:
                return {"error": f"bad base64 payload: {e!r}"}
            is_png = raw[:8] == b"\x89PNG\r\n\x1a\n"
            is_jpg = raw[:3] == b"\xff\xd8\xff"
            if not (is_png or is_jpg):
                return {"error": "payload is not a PNG/JPEG image"}
            if len(raw) > 8 * 1024 * 1024:
                return {"error": "image too large (8 MB cap)"}
            # ① archive the PNG on the host
            home = (os.environ.get("QDRANT_RAG_HOME", "") or "").strip() \
                or os.path.expanduser("~/.qdrant-rag-agent")
            hdir = Path(home) / "handwriting"
            hdir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            png = hdir / f"hw-{ts}.{'png' if is_png else 'jpg'}"
            png.write_bytes(raw)
            logger.info("handwriting: archived %s (%d bytes)",
                        png.name, len(raw))

            # v0.9.125.1 — per-page PNGs from the multipage pad
            # (pages[i] = data URL; stitched main = data_url above)
            page_files = []
            pages = p.get("pages")
            if isinstance(pages, list) and pages:
                for i, du in enumerate(pages[:10], start=1):
                    mm = re.match(
                        r"^data:image/(png|jpeg);base64,(.+)$",
                        str(du or ""), re.DOTALL,
                    )
                    if not mm:
                        continue
                    try:
                        praw = base64.b64decode(mm.group(2))
                    except Exception:
                        continue
                    p_ok = (praw[:8] == b"\x89PNG\r\n\x1a\n"
                            or praw[:3] == b"\xff\xd8\xff")
                    if not p_ok:
                        continue
                    pf = hdir / (
                        f"hw-{ts}-p{i:02d}."
                        + ("png" if praw[:8] == b"\x89PNG\r\n\x1a\n"
                           else "jpg"))
                    try:
                        pf.write_bytes(praw)
                        page_files.append(pf.as_posix())
                    except Exception as pe:
                        logger.warning(
                            "handwriting: page %d archive failed: %r",
                            i, pe)

            # ② OCR through the project vision chain
            ocr_text = ""
            ocr_backend = ""
            try:
                from src.ingest.image_describer import ImageDescriber

                d = ImageDescriber()
                out = d.describe(
                    png,
                    prompt=self._HW_OCR_PROMPT
                    + (self._HW_OCR_PDF_SUFFIX
                       if p.get("has_pdf_bg") else ""),
                    max_tokens=1800,
                ).strip()
                ocr_backend = str(d.last_backend or "")
                if out and not out.startswith("[Image:"):
                    ocr_text = out
                else:
                    logger.warning(
                        "handwriting: OCR produced a marker/error: %r",
                        out[:120],
                    )
            except Exception as ocr_e:
                logger.error("handwriting OCR FAILED: %r", ocr_e)
                ocr_text = ""

            # ③ archive the transcription as a Notes-store note
            title = str(p.get("title") or "").strip()[:200]
            if not title:
                first = (ocr_text or "").strip().splitlines()
                title = (first[0].strip()[:60]
                         if first and first[0] else "")
            if not title:
                title = f"手寫筆記 {ts}"
            category = str(p.get("category") or "手寫").strip()[:80]
            # v0.9.125.6 — the PDF underlay the pages were built on
            source_pdf = str(p.get("source_pdf") or "").strip()[:500]
            body = ocr_text or "（OCR 未取得文字）"
            if source_pdf:
                body += f"\n\n📄 底稿：{source_pdf}"
            body += f"\n\n🖼 手寫原文：{png.as_posix()}"
            note = self._notes_engine().local_create(
                title, body, category=category,
            )
            nid = (note or {}).get("id") \
                if isinstance(note, dict) else ""

            # ④ build the index — the .txt transcript goes through the
            # SAME single-file ingest the 文件 tab uses (chunk → embed
            # → the RAG collection), so search hits it by content.
            txt = hdir / f"hw-{ts}.txt"
            try:
                txt.write_text(
                    title + "\n\n" + (ocr_text or ""),
                    encoding="utf-8",
                )
            except Exception as txt_e:
                logger.error("handwriting: transcript write failed: %r",
                             txt_e)
            ingested = None
            if txt.exists():
                ing = self.ingest_file(str(txt))
                if isinstance(ing, dict) and not ing.get("error"):
                    ingested = ing
                    logger.info("handwriting: indexed %s (chunks=%s)",
                                txt.name, ing.get("chunks"))
                else:
                    logger.warning(
                        "handwriting: index build failed: %s",
                        str((ing or {}).get("error"))[:160],
                    )
            return {
                "ok": True,
                "id": nid,
                "title": title,
                "category": category,
                "ocr": ocr_text,
                "ocr_backend": ocr_backend,
                "png": png.as_posix(),
                "pages": page_files,
                "transcript": txt.as_posix() if txt.exists() else "",
                "indexed_chunks": (ingested or {}).get("chunks"),
            }
        except Exception as e:
            logger.error("notes_handwriting_save FAILED: %r\n%s",
                         e, traceback.format_exc()[-800:])
            return {"error": repr(e)}

    def read_handwriting_image(
        self, payload: dict | str | None = None,
    ) -> dict:
        """v0.9.125.1 — 可看圖: return a data-URL for an ARCHIVED
        handwriting PNG so the web editor preview can show it inline
        (the note body links it via 🖼 手寫原文：<path>).
        Hard-scoped to ``$QDRANT_RAG_HOME/handwriting/``: resolve()
        containment, suffix whitelist, 8 MB cap — any traversal attempt
        is refused with an explicit error."""
        import base64

        try:
            p = (payload.get("path")
                 if isinstance(payload, dict) else payload)
            p = str(p or "").strip()
            if not p:
                return {"error": "path is required"}
            home = (os.environ.get("QDRANT_RAG_HOME", "") or "").strip() \
                or os.path.expanduser("~/.qdrant-rag-agent")
            hdir = (Path(home) / "handwriting").resolve()
            cand = (hdir / p) if not Path(p).is_absolute() else Path(p)
            f = cand.resolve()
            if f != hdir and hdir not in f.parents:
                return {"error": (
                    "path outside the handwriting archive is refused")}
            if not f.is_file():
                return {"error": f"file not found: {f.name}"}
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                return {"error": "not a handwriting image"}
            raw = f.read_bytes()
            if len(raw) > 8 * 1024 * 1024:
                return {"error": "image too large (8 MB cap)"}
            mime = "png" if f.suffix.lower() == ".png" else "jpeg"
            return {
                "ok": True,
                "path": f.as_posix(),
                "data_url": ("data:image/" + mime + ";base64,"
                             + base64.b64encode(raw).decode("ascii")),
            }
        except Exception as e:
            return {"error": repr(e)}

    def notes_handwriting_pdf_pages(
        self, payload: dict | str | None = None,
    ) -> dict:
        """v0.9.125.6 — PDF underlay import for the handwriting pad:
        render each PDF page to a PNG the web/mobile canvas can wear as
        its per-page background (the 📄 匯入 PDF button).

        Input ``{path}`` = a PDF reachable through the file sandboxes
        (the chat 📎 upload flow lands it in ``<write root>/uploads/``;
        relative paths resolve there). Guards: ``.pdf`` suffix +
        ``%PDF-`` magic + 50 MB cap + read∪write roots containment —
        never an arbitrary read. Output: up to ``_HW_PDF_MAX_PAGES``
        pages rendered at 150 DPI and ARCHIVED under
        ``$QDRANT_RAG_HOME/handwriting/pdf-<ts>/``, returned as
        in-memory data URLs (the bridge shape ``read_handwriting_image``
        already uses — GUI and web modes load them without an extra
        endpoint). ``pages_total``/``truncated`` report honesty when
        the PDF exceeds the cap. Rendering needs PyMuPDF (fitz) — a
        missing install surfaces an explicit install hint."""
        import base64

        try:
            p = (payload if isinstance(payload, dict) else {})
            raw_path = str(p.get("path") or "").strip()
            if not raw_path:
                return {"error": "path is required"}
            f = Path(raw_path)
            if not f.is_absolute():
                from src.mcp_client.host import _resolve_filesystem_root
                try:
                    f = (_resolve_filesystem_root(
                        "QDRANT_FILESYSTEM_WRITE_ROOT") / f)
                except Exception as re_e:
                    return {"error": (
                        f"relative path without a write root: {re_e!r}")}
            try:
                f = f.resolve()
            except OSError as res_e:
                return {"error": f"resolve failed: {res_e!r}"}
            if f.suffix.lower() != ".pdf":
                return {"error": "not a .pdf file"}
            if not f.is_file():
                return {"error": f"file not found: {f.name}"}
            if f.stat().st_size > self._HW_PDF_FILE_CAP:
                return {"error": (
                    f"PDF too large ({f.stat().st_size // (1024 * 1024)} MB "
                    f"> {self._HW_PDF_FILE_CAP // (1024 * 1024)} MB cap)")}
            with f.open("rb") as fh:
                head = fh.read(5)
            if head != b"%PDF-":
                return {"error": "file is not a PDF (bad magic)"}
            # sandbox containment — mirror _resolve_sandbox_file
            from src.mcp_client.host import (
                _allowed_file_roots,
                _is_within,
            )
            if not any(_is_within(f, r) for r in _allowed_file_roots()):
                return {"error": (
                    "path outside the allowed sandbox roots is refused")}
            try:
                import fitz  # PyMuPDF
            except Exception as imp_e:
                return {"error": (
                    "rendering PDF pages needs PyMuPDF (fitz) — "
                    f"install it with: pip install pymupdf ({imp_e!r})")}
            return self._render_pdf_pages(f)
        except Exception as e:
            logger.error(
                "notes_handwriting_pdf_pages FAILED: %r\n%s",
                e, traceback.format_exc()[-800:],
            )
            return {"error": repr(e)}

    def _render_pdf_pages(self, f: Path) -> dict:
        """v0.9.125.6 — the render loop (split out so the guard chain
        stays readable): fitz → per-page 150-DPI PNG → archive +
        in-memory base64 payload (per-page cap) → the pages list."""
        import base64

        import fitz  # PyMuPDF

        doc = fitz.open(f)
        try:
            total = doc.page_count
            n = min(total, self._HW_PDF_MAX_PAGES)
            home = (os.environ.get("QDRANT_RAG_HOME", "") or "").strip() \
                or os.path.expanduser("~/.qdrant-rag-agent")
            out_dir = Path(home) / "handwriting" / \
                f"pdf-{time.strftime('%Y%m%d-%H%M%S')}"
            out_dir.mkdir(parents=True, exist_ok=True)
            pages = []
            for i in range(n):
                pix = doc[i].get_pixmap(dpi=150)
                png_bytes = pix.tobytes("png")
                pf = out_dir / f"p{i + 1:02d}.png"
                try:
                    pf.write_bytes(png_bytes)
                except Exception as wr_e:
                    logger.warning(
                        "handwriting pdf: page %d archive failed: %r",
                        i + 1, wr_e)
                b64 = base64.b64encode(png_bytes).decode("ascii")
                if len(b64) > self._HW_PDF_PAGE_B64_CAP:
                    # honest downgrade: a giant page must not blow the
                    # bridge payload — skip the page with a note
                    logger.warning(
                        "handwriting pdf: page %d base64 %.0f KB over "
                        "the per-page cap — skipped",
                        i + 1, len(b64) / 1024,
                    )
                    continue
                pages.append({
                    "index": i + 1,
                    "path": pf.as_posix() if pf.is_file() else "",
                    "data_url": "data:image/png;base64," + b64,
                    "w": pix.width,
                    "h": pix.height,
                })
            logger.info(
                "handwriting pdf: rendered %d/%d page(s) from %s "
                "(truncated=%s)", len(pages), total, f.name,
                total > n,
            )
            return {
                "ok": True,
                "pdf": f.as_posix(),
                "name": f.name,
                "pages": pages,
                "pages_total": total,
                "truncated": total > n,
            }
        finally:
            doc.close()

    # ── 快速片語（v0.9.101.0；GLOBAL store — 所有裝置共用同一份） ──
    # v0.9.122.0 — routed through src/snippet_store.py (the same core
    # the snippets-local MCP host uses; multi-line entries now legal,
    # MAX 100 × 2000 chars).

    _SNIPPETS_MAX = 100     # = snippet_store.MAX_SNIPPETS
    _SNIPPET_MAX_CHARS = 2000   # = snippet_store.MAX_CHARS

    @staticmethod
    def _snippets_path():
        """Server-side global snippet store: $QDRANT_RAG_HOME/
        snippets.json (same data-dir convention as notes/schedules) —
        every device (web LAN / phone / GUI) reads the SAME file, so a
        phrase built once is visible everywhere without rebuilding."""
        import os

        home = os.environ.get("QDRANT_RAG_HOME") or str(
            Path.home() / ".qdrant-rag-agent")
        return Path(home) / "snippets.json"

    @classmethod
    def _sanitize_snippets(cls, raw: Any) -> list[str]:
        """v0.9.122.0 — delegate to the core normalizer (multi-line
        kept; strip/2000-char cap/dedupe/100 cap — the same rules
        every MCP write path honours)."""
        from src.snippet_store import load_snippets, save_snippets

        try:
            return save_snippets(raw if isinstance(raw, list) else [])
        except Exception:
            return load_snippets()

    def snippets_get(self) -> dict:
        """GLOBAL quick-phrases for the chat ⚡ panel (read-only)."""
        try:
            from src.snippet_store import load_snippets

            return {"ok": True, "snippets": load_snippets()}
        except Exception as e:
            return {"error": repr(e), "snippets": []}

    def snippets_save(self, payload: dict | None = None) -> dict:
        """Replace the GLOBAL snippet list — payload = ``{snippets:
        [str, ...]}`` (entries may be MULTI-LINE strings,
        v0.9.122.0). Atomic via the core; sanitized."""
        try:
            p = self._notes_payload(payload)
            from src.snippet_store import save_snippets

            snippets = save_snippets(p.get("snippets"))
            return {"ok": True, "snippets": snippets}
        except Exception as e:
            return {"error": repr(e)}


# ─── module-level helpers (not exposed to JS) ────────────────────

_PIPELINE_SINGLETON = None


def _get_pipeline():
    """Lazily cached IngestPipeline."""
    global _PIPELINE_SINGLETON
    if _PIPELINE_SINGLETON is None:
        logger.debug("lazy-init IngestPipeline")
        from src.ingest.pipeline import IngestPipeline
        _PIPELINE_SINGLETON = IngestPipeline()
    return _PIPELINE_SINGLETON


def _new_session(sid: str, title: str):
    from src.agent.session import Session
    return Session(sid=sid, title=title)


def _detect_provider() -> str:
    if config.OLLAMA_API_KEY:
        return "ollama"
    if config.OPENAI_API_KEY:
        return "openai"
    return "(none — set OLLAMA_API_KEY or OPENAI_API_KEY)"


def _detect_model() -> str:
    if config.OLLAMA_API_KEY:
        return config.OLLAMA_CHAT_MODEL
    if config.OPENAI_API_KEY:
        return config.LLM_MODEL
    return ""
