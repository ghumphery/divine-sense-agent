"""v0.9.112.0 — periodic llama.cpp embedding watchdog.

The boot-time auto-heals (v0.9.109.3 / v0.9.109.4) only catch a DEAD
process (connection refused): the qdrant ``/readyz`` probe and the
firefox service ``/health`` probe both fail fast when the process is
gone. The llama.cpp server has a THIRD failure mode those heals cannot
catch: the ZOMBIE state — process alive, ``GET /v1/models`` happily
200, and every real ``POST /v1/embeddings`` failing (the live 2026-09-14
log example was Vulkan ``ErrorDeviceLost``: HTTP 500 on every embed
POST while ``/v1/models`` stayed green). ``Embedder._llamacpp_available
()`` probes ``/v1/models`` only, so a zombie passes the availability
probe, the auto chain burns one doomed POST per embed, and the
cooldown then pushes every embed to OpenRouter at ~400 ms/chunk —
10x the healthy local latency — until someone manually restarts the
server (which may be HOURS later for a long-lived GUI process).

This watchdog closes that gap the mechanical way (no LLM involved):

* Every ``QDRANT_LLAMACPP_WATCHDOG_INTERVAL`` seconds (default 1800)
  it makes a REAL embeddings POST (``{"input": "embed watchdog
  probe"}``) against ``LLAMACPP_EMBED_URL`` and checks the response
  SHAPE (HTTP 200 + non-empty ``data[0].embedding``).
* When ``failure_streak`` CONSECUTIVE probes fail (default 2) it
  heals: the llama-server PIDs listening on the embed port are
  terminated (``netstat -ano`` parse → ``taskkill /F /T /PID``) and
  the start bat is spawned DETACHED (exact same Popen shape as the
  v0.9.109.3/109.4 healers), then it waits up to ``_READY_WAIT_S``
  (150 s — the Qwen3-8B model reload takes tens of seconds) for the
  probe to turn green and reports the outcome in the log.
* v0.9.115.1 — the DEAD fast path: a probe whose RAW TCP connect
  REFUSES is the UNAMBIGUOUS dead state — nothing at all is on the
  embed port (a llama-server LOADING its model binds the port first
  and answers HTTP 503, never refused). Dead = heal NOW, no streak
  wait — guarded by a llama-process existence check (an alive
  llama-server.exe on a closed port may still be LOADING; spawning a
  second instance would race the port = the v0.9.113.1 mixed-dim
  disaster) + the usual 600 s cooldown.
  ``QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL=off`` opts out.
* A restart COOLDOWN (default 600 s) guarantees at most one heal per
  window, so a persistently broken box (missing bat, broken Vulkan
  install) can never wind up in a kill/spawn loop.

Design invariants (learned from the v0.9.25.3 + v0.9.109.x arcs):

* ONLY the HTTP result is trusted — NEVER the latency. A healthy
  llama-server mid-ingest can be slow for seconds; killing it because
  a probe took 5 s would murder healthy batches. The probe timeout
  (15 s) + the 2-streak requirement are the false-positive guards.
* Daemon thread mirrors ``Scheduler``: tick errors are caught +
  logged, ``stop()`` is idempotent, the thread never tears down the
  app process.
* NEVER raises across the whole surface: probe/kill/spawn errors land
  in the state dict + logs; the running embed chain keeps working
  (its own cooldown + chain fallthrough already handle degraded
  service while the watchdog heals the server).
* All knobs are env-gated with the project's raw-string convention:
  ``QDRANT_LLAMACPP_WATCHDOG=off`` opts out entirely (the status op
  still answers); the rest have sane defaults.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

from src.config import _normalize_loopback_url

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 1800.0
DEFAULT_PROBE_TIMEOUT_S = 15.0
DEFAULT_FAILURE_STREAK = 2
DEFAULT_RESTART_COOLDOWN_S = 600.0
# v0.9.115.1: a TCP-REFUSED probe failure heals immediately (dead is
# unambiguous — nothing on the port); off restores the legacy
# wait-for-2-streak behaviour for every failure kind.
DEFAULT_REFUSED_HEAL = True
READY_WAIT_S = 150.0
PROBE_TEXT = "embed watchdog probe"
DEFAULT_EMBED_URL = "http://127.0.0.1:8080/v1/embeddings"

# Detached-spawn flags — identical to the v0.9.109.x healers.
_DETACHED = 0x00000008 | 0x00000200 | 0x08000000

DEFAULT_START_BAT = r"C:\llama.cpp-vulkan\start-llamacpp-8b.bat"
_BAT_CANDIDATES = (
    DEFAULT_START_BAT,
    r"C:\llama.cpp\start-llamacpp-8b.bat",
)


def _iso_now() -> str:
    """Local-wall-clock ISO stamp (sessions / histories convention)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _env_bool(env_name: str, default: bool) -> bool:
    raw = (os.environ.get(env_name, "") or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _env_float(env_name: str, default: float, floor: float = 1.0) -> float:
    raw = (os.environ.get(env_name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(floor, float(raw))
    except ValueError:
        return default


def _resolve_embed_url() -> str:
    """The embeddings URL — same env the Embedder uses, same loopback
    normalisation (v0.9.25.3: bare ``localhost`` = the ::1 trap)."""
    raw = (os.environ.get("LLAMACPP_EMBED_URL", "") or "").strip()
    if not raw:
        return DEFAULT_EMBED_URL
    try:
        return _normalize_loopback_url(raw).rstrip("/")
    except Exception:
        return raw.rstrip("/")

# ─── watchdog state + engine ─────────────────────────────────────


class EmbedWatchdog:
    """Periodic REAL-POST probe + zombie heal for the embed server."""

    _READY_WAIT_S = READY_WAIT_S

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        # Raw env knobs (project convention: read os.environ directly,
        # resolvers swallow bad values with module defaults).
        self.enabled = _env_bool("QDRANT_LLAMACPP_WATCHDOG", True)
        self.interval_s = _env_float(
            "QDRANT_LLAMACPP_WATCHDOG_INTERVAL", DEFAULT_INTERVAL_S)
        self.timeout_s = _env_float(
            "QDRANT_LLAMACPP_WATCHDOG_TIMEOUT", DEFAULT_PROBE_TIMEOUT_S)
        self.failure_streak = int(_env_float(
            "QDRANT_LLAMACPP_WATCHDOG_STREAK", DEFAULT_FAILURE_STREAK,
            floor=1.0))
        self.cooldown_s = _env_float(
            "QDRANT_LLAMACPP_WATCHDOG_COOLDOWN", DEFAULT_RESTART_COOLDOWN_S)
        # v0.9.115.1 — dead(refused) fast-heal toggle.
        self.refused_heal = _env_bool(
            "QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL", DEFAULT_REFUSED_HEAL)
        # Mutable state (all access guarded by ``self._lock``).
        self.last_probe_ts: str | None = None
        self.last_probe_ok: bool | None = None
        self.last_probe_ms: float | None = None
        self.last_error = ""
        self.consecutive_failures = 0
        self.restarts = 0
        self.last_restart_ts: str | None = None
        self.cooling_until = 0.0
        # v0.9.122.2 — subclass hooks: thread name + log prefix
        # (Gemma2Watchdog overrides both; default = the primary 4B watch).
        self._thread_name = "embed-watchdog"
        self._log_prefix = "[embed-watchdog]"

    # ── config helpers ──
    @property
    def embed_url(self) -> str:
        return _resolve_embed_url()

    @property
    def embed_port(self) -> int:
        try:
            return urlparse(self.embed_url).port or 8080
        except Exception:
            return 8080

    @staticmethod
    def _resolve_start_bat() -> str:
        """``QDRANT_LLAMACPP_START_BAT`` override else the first
        installed candidate (Vulkan build preferred)."""
        override = (
            os.environ.get("QDRANT_LLAMACPP_START_BAT", "") or ""
        ).strip()
        if override:
            return override
        for cand in _BAT_CANDIDATES:
            if os.path.isfile(cand):
                return cand
        return DEFAULT_START_BAT

    # ── dead-state classification (v0.9.115.1) ──
    @staticmethod
    def _port_state(host: str, port: int, timeout: float = 1.5) -> str:
        """Raw TCP probe — ``"refused"`` / ``"open"`` / ``"unknown"``.

        ``"refused"`` (WinError 10061 / ECONNREFUSED) = NOTHING is on
        the port = the unambiguous dead state. A llama-server LOADING
        its model BINDS the port first and answers HTTP 503-loading,
        never refused."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return "open"
        except ConnectionRefusedError:
            return "refused"
        except OSError:
            return "unknown"

    @staticmethod
    def _llama_process_alive() -> bool | None:
        """tasklist scan — is any llama-server.exe alive?

        An alive process on a CLOSED embed port = the model may still
        be LOADING → spawning a second llama-server would race the
        port (v0.9.113.1 mixed-dim disaster) → observe only.
        ``None`` = cannot tell (tool failure) → treat conservatively
        as alive (no immediate spawn; the streak path still heals)."""
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq llama-server.exe",
                 "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=20,
                creationflags=flags,
            )
            return "llama-server.exe" in (r.stdout or "").lower()
        except Exception:  # noqa: BLE001 — probe must not raise
            return None

    def _dead_refused(self) -> bool:
        """TRUE only for the UNAMBIGUOUS dead state: the embed URL's
        TCP connect REFUSES **and** no llama-server.exe process is
        alive (a living process / an unreadable process table = the
        server may be loading → keep the streak path, no spawn)."""
        host = urlparse(self.embed_url).hostname or "127.0.0.1"
        if self._port_state(host, self.embed_port) != "refused":
            return False
        alive = self._llama_process_alive()
        return not alive if alive is not None else False

    # ── probe ──
    def _probe_once(self, timeout: float | None = None
                    ) -> tuple[bool, float, str]:
        """ONE real embeddings POST. Returns (ok, latency_ms, note).
        ok == HTTP 200 AND a non-empty ``data[0].embedding`` row —
        the only shape a zombie server fails."""
        t0 = time.perf_counter()
        try:
            resp = requests.post(
                self.embed_url,
                json={"input": PROBE_TEXT},
                timeout=(timeout or self.timeout_s),
            )
        except Exception as e:  # noqa: BLE001 — probe must not raise
            ms = (time.perf_counter() - t0) * 1000.0
            return False, ms, repr(e)[:200]
        ms = (time.perf_counter() - t0) * 1000.0
        if resp.status_code != 200:
            return False, ms, f"http {resp.status_code}"
        try:
            data = resp.json().get("data") or []
            emb = (data[0].get("embedding") if data else None) or []
        except Exception as e:
            return False, ms, repr(e)[:200]
        if not emb:
            return False, ms, "empty embedding"
        return True, ms, "ok"
    # ── process surgery (kill + spawn) ──
    @staticmethod
    def _extract_listen_pids(text: str, port: int) -> list[int]:
        """Parse ``netstat -ano`` output for PIDs bound to ``port``.

        NO state filter on purpose: Windows netstat STATE tokens are
        LOCALIZED (zh-TW boxes print 聆聽中, not LISTENING) — matching
        "the local address ends with :<port> AND the last column is a
        PID" covers both LISTENING and long-lived connections and is
        locale-proof. Established rows carry the same PID as the
        listener anyway."""
        pids: set[int] = set()
        for line in (text or "").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            local = parts[1]
            if not local.endswith(":" + str(port)):
                continue
            try:
                pids.add(int(parts[-1]))
            except (ValueError, IndexError):
                continue
        return sorted(pids)

    @staticmethod
    def _netstat() -> str:
        """Windows ``netstat -ano`` snapshot (never raises)."""
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"], capture_output=True,
                text=True, timeout=20, creationflags=flags,
            )
            return r.stdout or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("[embed-watchdog] netstat failed: %r", e)
            return ""

    @staticmethod
    def _kill_pid(pid: int) -> str:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=20,
                creationflags=flags,
            )
            return (r.stdout or r.stderr or "").strip()[:200]
        except Exception as e:  # noqa: BLE001
            return repr(e)[:200]

    @staticmethod
    def _spawn_detached(bat: str) -> str:
        """Detached launcher spawn — the exact Popen shape of the
        v0.9.109.3 (qdrant) / v0.9.109.4 (firefox) healers."""
        try:
            subprocess.Popen(
                ["cmd", "/c", bat],
                cwd=(os.path.dirname(bat) or "."),
                creationflags=_DETACHED,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return "spawned"
        except Exception as e:  # noqa: BLE001
            logger.warning("[embed-watchdog] spawn failed: %r", e)
            return f"spawn-failed: {e!r}"[:200]

    def _restart(self, reason: str) -> dict[str, Any]:
        """Kill the zombie llama-server (if any) and relaunch it.

        Order: bat check FIRST (never kill what we cannot respawn) →
        netstat PIDs (empty = process already dead, spawn only) →
        taskkill → detached spawn → readiness wait. Never raises;
        returns a small report dict."""
        bat = self._resolve_start_bat()
        if not os.path.isfile(bat):
            logger.warning(
                f"{self._log_prefix} start bat missing (%s) — cannot heal; "
                "probe failures kept as diagnostics only", bat)
            return {"action": "skipped", "reason": "start-bat-missing",
                    "bat": bat}
        with self._lock:
            now = time.time()
            if self.cooling_until and now < self.cooling_until:
                return {"action": "skipped", "reason": "cooldown",
                        "remaining_s": round(self.cooling_until - now)}
            self.cooling_until = now + self.cooldown_s
        port = self.embed_port
        txt = self._netstat()
        pids = self._extract_listen_pids(txt, port)
        killed: list[str] = []
        for pid in pids:
            out = self._kill_pid(pid)
            killed.append(f"{pid}: {out}")
        if pids:
            logger.warning(
                f"{self._log_prefix} killed llama-server PID(s) %s "
                "(probe: %.200s)", pids, reason)
        else:
            logger.info(
                f"{self._log_prefix} no listener PID on port %d (already "
                "down) — spawning only", port)
        spawn = self._spawn_detached(bat)
        ready = False
        deadline = time.time() + self._READY_WAIT_S
        while time.time() < deadline:
            time.sleep(3)
            ok, ms, note = self._probe_once(timeout=max(5.0, min(
                self.timeout_s, 30.0)))
            if ok:
                ready = True
                logger.info(
                    f"{self._log_prefix} embed server READY after ~%.0fs "
                    "(%.0f ms) — heal complete",
                    self._READY_WAIT_S - (deadline - time.time()), ms)
                break
        if not ready:
            logger.warning(
                f"{self._log_prefix} still not ready after %.0fs — the "
                "embed chain degrades per its own cooldown meanwhile",
                self._READY_WAIT_S)
        with self._lock:
            self.restarts += 1
            self.last_restart_ts = _iso_now()
        return {"action": spawn, "killed": killed, "bat": bat,
                "ready": ready}
    # ── tick + loop lifecycle ──
    def tick(self) -> dict[str, Any]:
        """ONE watchdog cycle: probe → record → maybe heal. Never
        raises (the loop-level catch is the last resort, this level
        keeps failure detail)."""
        ok, ms, note = self._probe_once()
        with self._lock:
            self.last_probe_ts = _iso_now()
            self.last_probe_ok = ok
            self.last_probe_ms = round(ms, 1)
        if ok:
            with self._lock:
                self.consecutive_failures = 0
            return {"status": "ok", "latency_ms": round(ms, 1)}
        with self._lock:
            self.consecutive_failures += 1
            streak = self.consecutive_failures
        logger.warning(
            f"{self._log_prefix} probe FAILED (%.200s) — streak %d/%d "
            "(interval %.0fs, timeout %.0fs)",
            note, streak, self.failure_streak,
            self.interval_s, self.timeout_s,
        )
        action = "observed"
        # v0.9.115.1 — refused/dead classification: nothing is on the
        # embed port at all AND no llama-server process exists → heal
        # NOW instead of waiting a full interval for streak 2. All
        # other failures (http 500 / timeout / bad shape = the ZOMBIE
        # or a busy-loading model) keep the streak guard.
        dead_now = False
        if self.refused_heal:
            try:
                dead_now = self._dead_refused()
            except Exception as e:  # noqa: BLE001 — never break the tick
                logger.warning(
                    f"{self._log_prefix} dead-state check failed: %r", e)
            if dead_now:
                logger.info(
                    f"{self._log_prefix} connect refused and no "
                    "llama-server process — unambiguous DEAD, healing "
                    "immediately (skips the %d-streak wait)",
                    self.failure_streak)
        if dead_now or streak >= self.failure_streak:
            report = self._restart(note)
            action = str(report.get("action", "observed"))
            return {"status": "degraded", "note": note, "streak": streak,
                    "dead_now": dead_now, **report}
        return {"status": "degraded", "note": note, "streak": streak,
                "dead_now": dead_now}

    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 — last-resort catch
                with self._lock:
                    self.last_error = repr(e)[:200]
                logger.warning(
                    f"{self._log_prefix} tick failed: %r", e)
            # Sleep in <=1 s slices so stop() is prompt (Scheduler
            # daemon convention).
            slept = 0.0
            while (slept < self.interval_s
                   and not self._stop_evt.is_set()):
                slice_ = min(1.0, self.interval_s - slept)
                if self._stop_evt.wait(slice_):
                    break
                slept += slice_

    def start(self) -> bool:
        """Idempotent thread start. Returns False when disabled
        (QDRANT_LLAMACPP_WATCHDOG=off) or already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
        if not self.enabled:
            logger.info(
                f"{self._log_prefix} disabled (its enabled flag is False)")
            return False
        t = threading.Thread(
            target=self._loop, daemon=True, name=self._thread_name,
        )
        with self._lock:
            self._thread = t
        t.start()
        logger.info(
            "%s started (every %.0fs → %s, streak %d)",
            self._log_prefix,
            self.interval_s, self.embed_url, self.failure_streak,
        )
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def status(self) -> dict[str, Any]:
        """Thread-safe snapshot for ``get_runtime_status`` / the
        runtime MCP tool / the GUI. Never raises, never probes."""
        with self._lock:
            return {
                "enabled": self.enabled,
                "running": self.running,
                "interval_s": self.interval_s,
                "embed_url": self.embed_url,
                "probe_timeout_s": self.timeout_s,
                "failure_streak": self.failure_streak,
                "refused_heal": self.refused_heal,
                "restart_cooldown_s": self.cooldown_s,
                "start_bat": self._resolve_start_bat(),
                "ready_wait_s": self._READY_WAIT_S,
                "last_probe_ts": self.last_probe_ts,
                "last_probe_ok": self.last_probe_ok,
                "last_probe_ms": self.last_probe_ms,
                "consecutive_failures": self.consecutive_failures,
                "restarts": self.restarts,
                "last_restart_ts": self.last_restart_ts,
                "last_error": self.last_error,
            }


# ─── module singleton + gui/api boot hook ────────────────────────

_WATCHDOG: EmbedWatchdog | None = None
_WATCHDOG_LOCK = threading.Lock()


def get_watchdog() -> EmbedWatchdog:
    """Process-wide singleton (created lazily; the THREAD only starts
    when ``start_embed_watchdog`` is called — status is answerable in
    CLI / stdio processes that never started it)."""
    global _WATCHDOG
    with _WATCHDOG_LOCK:
        if _WATCHDOG is None:
            _WATCHDOG = EmbedWatchdog()
        return _WATCHDOG


def start_embed_watchdog() -> bool:
    """gui/api boot hook (v0.9.112.0). Idempotent; NEVER raises."""
    try:
        return get_watchdog().start()
    except Exception as e:  # noqa: BLE001
        logger.warning("embed-watchdog start failed: %r", e)
        return False


# ─── v0.9.122.2 — gemma2 (EmbeddingGemma-2 media server) watchdog ──
#
# The 4B watch above only babysits LLAMACPP_EMBED_URL (:8080). The
# gemma2 media server (:8081, EmbeddingGemma-2 + --mmproj, powering the
# media side-channel + search_media) previously had NO watchdog: once
# it died, media embeddings degraded silently until a manual bat run.
# This subclass reuses EVERYTHING (real-POST probe, 2-streak, refused
# fast-heal, netstat-PID kill, detached spawn, cooldown) with its own
# URL / start bat / thread name / log prefix. The heal chain stays
# correct with BOTH servers coexisting: tasklist sees llama-server.exe
# (the 4B) → no blind refused-kill; the kill step only terminates PIDs
# actually LISTENING on :8081.

GEMMA2_DEFAULT_EMBED_URL = "http://127.0.0.1:8081/v1/embeddings"
GEMMA2_DEFAULT_START_BAT = r"C:\llama.cpp-un\start-llamacpp-gemma2.bat"

_GEMMA2_WATCHDOG: EmbedWatchdog | None = None
_GEMMA2_LOCK = threading.Lock()


def _gemma2_gate_default() -> bool:
    """Empty ``QDRANT_GEMMA2_WATCHDOG`` = follow the media side-channel
    gate (no media embedding enabled → nothing consumes :8081 → no
    babysitting)."""
    try:
        from src.ingest.media_embed import media_embedding_enabled

        return media_embedding_enabled()
    except Exception:  # noqa: BLE001
        return False


def gemma2_watchdog_enabled() -> bool:
    """``QDRANT_GEMMA2_WATCHDOG`` explicit on/off; empty = follow the
    media-embedding gate."""
    return _env_bool("QDRANT_GEMMA2_WATCHDOG", _gemma2_gate_default())


class Gemma2Watchdog(EmbedWatchdog):
    """Watchdog instance for the :8081 gemma2 media server. Every
    cadence/streak/cooldown/refused-heal knob is SHARED with the 4B
    watch (one set of ``QDRANT_LLAMACPP_WATCHDOG_*`` envs governs
    both); only the URL, start bat, thread name, log prefix and the
    enable gate differ."""

    def __init__(self) -> None:
        super().__init__()
        self.enabled = gemma2_watchdog_enabled()
        self._thread_name = "gemma2-watchdog"
        self._log_prefix = "[gemma2-watchdog]"

    @property
    def embed_url(self) -> str:
        try:
            from src.ingest.media_embed import media_embed_url

            u = media_embed_url()
            if u:
                return u
        except Exception:  # noqa: BLE001 — never break construction
            pass
        return GEMMA2_DEFAULT_EMBED_URL

    @staticmethod
    def _resolve_start_bat() -> str:
        """``QDRANT_GEMMA2_START_BAT`` override else the bundled gemma2
        bat (single candidate — the install path is fixed)."""
        override = (os.environ.get("QDRANT_GEMMA2_START_BAT", "")
                    or "").strip()
        return override or GEMMA2_DEFAULT_START_BAT


def get_gemma2_watchdog() -> Gemma2Watchdog:
    """Process-wide gemma2 singleton (same lazy contract as
    ``get_watchdog``)."""
    global _GEMMA2_WATCHDOG
    with _GEMMA2_LOCK:
        if _GEMMA2_WATCHDOG is None:
            _GEMMA2_WATCHDOG = Gemma2Watchdog()
        return _GEMMA2_WATCHDOG


def start_gemma2_watchdog() -> bool:
    """gui/api boot hook (v0.9.122.2). Idempotent; NEVER raises; no-op
    when the gate is off."""
    try:
        if not gemma2_watchdog_enabled():
            logger.info(
                "gemma2-watchdog not started (gate off / media embedding "
                "off)")
            return False
        return get_gemma2_watchdog().start()
    except Exception as e:  # noqa: BLE001
        logger.warning("gemma2-watchdog start failed: %r", e)
        return False