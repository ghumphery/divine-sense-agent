"""Webhook trigger manager — fire prompts from external HTTP calls.

Each trigger lives in ``$QDRANT_RAG_HOME/webhooks/<id>.json`` (atomic
write via tmp + os.replace, mirroring ``ScheduleManager``). Every
trigger carries its OWN random ``access_key``; hitting

    POST /trigger/<trigger_id>?key=<access_key>
    (or header ``X-Access-Key``)

spins the pre-configured prompt through ``Agent.chat_turn`` with the
full tool-calling surface available (``use_tools=True``) — the
event-driven counterpart to the cron/interval/once ``Scheduler``.
On every fire the module:

1. Appends the ``{user, assistant}`` turn to a dedicated session
   named ``webhook-<id>-<fire-ts>`` (visible in the Sessions tab).
2. Writes a compact ``{ts, prompt, answer, session_id, ...}`` line
   to ``webhooks/<id>.history.jsonl`` (ring-buffered, MAX_HISTORY).
3. Updates the trigger's lifecycle fields (``fire_count``,
   ``last_fire_at``, ``last_error`` …) atomically.

Lifecycle gates (same shape as Schedule): ``enabled`` flag,
``max_fires`` cap (auto ``status="exhausted"``), ``expires_at``
(auto ``status="expired"``) and a manual ``status="disabled"``.

The HTTP listener (``WebhookServer``) is a daemon thread over the
stdlib ``ThreadingHTTPServer`` — no new dependency. It binds
``QDRANT_WEBHOOK_HOST:QDRANT_WEBHOOK_PORT`` (default
``127.0.0.1:8335``) and NEVER accepts a request without a valid
per-trigger access key (constant-time compare). Non-loopback binds
log a loud WARNING — each URL is a remote prompt-execution endpoint.

This module is independent of MCP / web-search plumbing — the Agent
itself decides which tools to use.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─── Persistence paths ────────────────────────────────────────


def _webhooks_dir() -> Path:
    """Default location: ``$QDRANT_RAG_HOME/webhooks``."""
    base = Path(
        os.environ.get(
            "QDRANT_RAG_HOME", Path.home() / ".qdrant-rag-agent"
        )
    )
    p = base / "webhooks"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


# ─── Webhook trigger dataclass ────────────────────────────────


MAX_HISTORY = 50


@dataclass
class WebhookTrigger:
    """A webhook-triggered prompt. Persisted to
    ``webhooks/<id>.json`` (shape mirrors ``Schedule``)."""

    id: str = ""
    name: str = ""
    prompt: str = ""
    # Per-trigger secret — constant-time-compared against the
    # ``?key=`` query param or ``X-Access-Key`` header. Generated
    # ONCE at creation; shown in FULL only in the add response.
    access_key: str = ""
    enabled: bool = True
    # v0.8.7-parity lifecycle caps (same semantics as Schedule).
    max_fires: int | None = None
    expires_at: str | None = None
    fire_count: int = 0
    last_fire_at: str | None = None
    last_session_id: str | None = None
    created_at: str = ""
    last_error: str | None = None
    last_error_at: str | None = None
    # Lifecycle marker: active / expired / exhausted / disabled /
    # failed. ``None`` is treated as "active" (old on-disk rows).
    status: str | None = None
    # v0.9.111.0 — optional fire-level chat-model pin (``provider:model``;
    # validated at ADD time via task_llm.coerce_llm_pin). ""/None = the
    # default chain. Fire time: an invalid pin degrades to the default
    # chain with a warning (a trigger must keep firing).
    llm: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = _now()
        if not self.access_key:
            # 32+ chars of URL-safe entropy — the per-trigger key is
            # the ONLY thing standing between the internet and a
            # prompt execution, so never shorten it.
            self.access_key = secrets.token_urlsafe(32)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WebhookTrigger":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def key_matches(self, provided: str | None) -> bool:
        """Constant-time access-key check (never short-circuit on
        length so timing cannot leak the key length)."""
        if not provided or not self.access_key:
            return False
        return hmac.compare_digest(
            self.access_key.encode("utf-8"),
            str(provided).encode("utf-8"),
        )

    def masked_key(self) -> str:
        return self.access_key[:6] + "…" if self.access_key else ""

    def check_lifecycle(self, now: Any = None) -> str | None:
        """Return ``None`` when the trigger MAY fire, or a refusal
        reason string. Mirrors ``Schedule.compute_next_fire``'s
        lifecycle gates (status / enabled / max_fires / expires_at)
        without mutating on-disk state — the fire path stamps the
        terminal status itself. The ``status`` stamp takes
        precedence over the plain ``enabled`` flag so a terminal
        state (exhausted / expired / failed) stays visible."""
        if now is None:
            from datetime import datetime

            now = datetime.now()
        if self.status and self.status not in ("active", None):
            return self.status
        if not self.enabled:
            return "disabled"
        if (
            self.max_fires is not None
            and self.fire_count >= self.max_fires
        ):
            return "exhausted"
        if self.expires_at:
            try:
                exp = _parse_iso(self.expires_at)
            except ValueError:
                exp = None
            if exp is not None and now >= exp:
                return "expired"
        return None


def _parse_iso(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
        tzinfo=None,
    )


# ─── Manager ───────────────────────────────────────────────────


class WebhookManager:
    """CRUD over on-disk webhook trigger JSON files."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else _webhooks_dir()

    def _path(self, wid: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", wid):
            raise ValueError(f"invalid webhook trigger id: {wid!r}")
        return self.root / f"{wid}.json"

    def _history_path(self, wid: str) -> Path:
        return self.root / f"{wid}.history.jsonl"

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

    def list(self) -> list[WebhookTrigger]:
        out: list[WebhookTrigger] = []
        for p in sorted(self.root.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append(WebhookTrigger.from_dict(d))
            except Exception as e:
                logger.warning(
                    "webhooks: skipping corrupt %s: %r", p, e,
                )
        return out

    def get(self, wid: str) -> WebhookTrigger | None:
        p = self._path(wid)
        if not p.exists():
            return None
        return WebhookTrigger.from_dict(
            json.loads(p.read_text(encoding="utf-8")),
        )

    def save(self, w: WebhookTrigger) -> WebhookTrigger:
        if not w.id:
            raise ValueError("webhook trigger id is required")
        if not w.prompt or not str(w.prompt).strip():
            raise ValueError("webhook trigger prompt is required")
        self._atomic_write(self._path(w.id), w.to_dict())
        return w

    def delete(self, wid: str) -> bool:
        p = self._path(wid)
        hp = self._history_path(wid)
        existed = p.exists()
        if existed:
            p.unlink()
        try:
            hp.unlink(missing_ok=True)
        except OSError:
            pass
        return existed

    def set_enabled(self, wid: str, enabled: bool) -> WebhookTrigger | None:
        w = self.get(wid)
        if w is None:
            return None
        w.enabled = bool(enabled)
        if enabled and w.status == "disabled":
            w.status = "active"
        return self.save(w)

    def record_error(self, wid: str, exc: Exception) -> WebhookTrigger | None:
        w = self.get(wid)
        if w is None:
            return None
        w.last_error = repr(exc)
        w.last_error_at = _now()
        return self.save(w)

    def find_by_name(self, name: str) -> WebhookTrigger | None:
        if not name:
            return None
        for w in self.list():
            if w.name == name:
                return w
        return None

    def append_history(self, wid: str, entry: dict) -> None:
        """Append a fire record; trim to MAX_HISTORY lines."""
        h = self._history_path(wid)
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

    def get_history(self, wid: str, limit: int = 50) -> list[dict]:
        h = self._history_path(wid)
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

    def get_progress(self, wid: str) -> dict[str, Any] | None:
        """Lifecycle snapshot (mirrors ``ScheduleManager.get_progress``)."""
        w = self.get(wid)
        if w is None:
            return None
        from datetime import datetime

        now = datetime.now()
        refusal = w.check_lifecycle(now)
        if w.max_fires is None:
            fires_remaining: int | None = None
        else:
            fires_remaining = max(0, w.max_fires - w.fire_count)
        return {
            "id": w.id,
            "name": w.name,
            "prompt_preview": w.prompt[:200],
            "enabled": w.enabled,
            "status": w.status or ("active" if w.enabled else "disabled"),
            "lifecycle_refusal": refusal,
            "fire_count": w.fire_count,
            "max_fires": w.max_fires,
            "fires_remaining": fires_remaining,
            "last_fire_at": w.last_fire_at,
            "expires_at": w.expires_at,
            "last_error": w.last_error,
            "last_error_at": w.last_error_at,
            "last_session_id": w.last_session_id,
            "recent_runs": self.get_history(wid, limit=5),
        }


# ─── Prompt composition + fire engine ─────────────────────────


def _compose_prompt(trigger: WebhookTrigger, payload: Any) -> str:
    """Merge the request payload into the configured prompt.

    * No payload (or empty) → the prompt verbatim.
    * Prompt contains a ``{payload}`` placeholder → replaced with
      the payload text (JSON-serialised for non-string payloads).
    * Otherwise the payload is appended as a ``[Webhook payload]``
      block so the LLM always sees the caller's data.
    """
    prompt = trigger.prompt or ""
    if payload is None or payload == "":
        return prompt
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False,
    )
    if "{payload}" in prompt:
        return prompt.replace("{payload}", text)
    return (
        prompt
        + "\n\n[Webhook payload]\n"
        + (text if len(text) <= 8000 else text[:8000] + "…(truncated)")
    )


def make_webhook_fire(
    agent_factory: Callable[[], Any],
) -> Callable[[WebhookTrigger, Any], dict[str, Any]]:
    """Build a ``fire(trigger, payload) -> dict`` callback that
    delegates to ``Agent.chat_turn`` (mirrors
    ``scheduler.make_agent_on_fire`` but returns the answer so the
    HTTP layer can respond synchronously).

    The returned dict carries ``ok`` / ``answer`` / ``sources`` /
    ``session_id`` / ``duration_s`` / ``error`` — serialised straight
    into the HTTP response. The trigger's lifecycle fields are
    stamped atomically on the JSON file and a fire record goes into
    ``<id>.history.jsonl``.
    """
    from datetime import datetime

    def _fire(w: WebhookTrigger, payload: Any = None) -> dict:
        started = datetime.now()
        # v0.9.111.0 — optional fire-level model pin (WebhookTrigger.llm);
        # stale pin degrades to the default chain (schedule parity).
        pin: str | None = (
            str(getattr(w, "llm", "") or "").strip() or None
        )
        if pin:
            try:
                from src.agent.task_llm import validate_task_llm

                verdict = validate_task_llm(pin)
                if verdict.get("ok"):
                    pin = f"{verdict['provider']}:{verdict['model']}"
                else:
                    logger.warning(
                        "webhook: %s llm pin %r invalid (%s) — fire runs "
                        "on the default chain",
                        w.id, w.llm, verdict.get("error"),
                    )
                    pin = None
            except Exception:  # noqa: BLE001 — probe never blocks
                pin = None
        if pin:
            try:
                agent = agent_factory(llm=pin)
            except TypeError:
                logger.warning(
                    "webhook: agent factory predates llm pins — trigger "
                    "%s runs on the default chain", w.id,
                )
                agent = agent_factory()
        else:
            agent = agent_factory()
        mgr = WebhookManager()
        from src.agent.session import Session, SessionManager

        sm = SessionManager()
        sess = None
        if w.last_session_id:
            sess = sm.load(w.last_session_id)
        if sess is None:
            sess = Session(
                sid=(
                    f"webhook-{w.id}-"
                    f"{datetime.now().strftime('%Y%m%d%H%M%S')}"
                ),
                title=f"[webhook] {w.name or w.id}",
            )
        final_prompt = _compose_prompt(w, payload)
        sess.messages.append({
            "role": "user", "content": final_prompt, "ts": _now(),
        })
        # v0.9.45.0 Phase 2: register the fire in the process-wide
        # task registry (list_tasks / get_task_result visibility).
        from src.agent.task_runner import begin_task, end_task
        _tr = begin_task(
            "webhook", w.name or w.id, sid=sess.id, llm=pin,
        )
        try:
            result = agent.chat_turn(
                question=final_prompt,
                history=sess.messages[:-1],
                use_tools=True,
            )
        except Exception as chat_exc:
            try:
                end_task(
                    _tr, "failed", error=repr(chat_exc)[:500],
                )
            except Exception:
                pass
            try:
                mgr.record_error(w.id, chat_exc)
            except Exception:
                pass
            sess.messages.append({
                "role": "assistant",
                "content": f"[error] {chat_exc!r}",
                "ts": _now(), "sources": [],
                "webhook_id": w.id, "error": True,
            })
            sess.updated_at = _now()
            try:
                sm.save(sess)
            except Exception:
                pass
            mgr.append_history(w.id, {
                "ts": _now(), "prompt": final_prompt,
                "answer": None, "sources": [],
                "session_id": sess.id, "error": repr(chat_exc),
                "payload": payload if isinstance(payload, str) else None,
            })
            # A fatal chat_turn failure stamps ``status="failed"`` —
            # a broken webhook must not keep accepting fire requests
            # silently (parity with the scheduler's failed-stamp).
            try:
                fresh = mgr.get(w.id)
                if fresh is not None:
                    fresh.status = "failed"
                    fresh.last_error = repr(chat_exc)[:500]
                    fresh.last_error_at = _now()
                    mgr.save(fresh)
            except Exception:
                pass
            return {
                "ok": False, "error": repr(chat_exc),
                "session_id": sess.id, "duration_s": round(
                    (datetime.now() - started).total_seconds(), 2,
                ),
            }
        return _finish_fire(
            w, result, final_prompt, sess, payload, mgr, sm, started,
            task_id=_tr,
        )

    return _fire


def _finish_fire(
    w: WebhookTrigger,
    result: Any,
    final_prompt: str,
    sess: Any,
    payload: Any,
    mgr: "WebhookManager",
    sm: Any,
    started: Any,
    task_id: str | None = None,
) -> dict:
    from datetime import datetime

    # Tuple/dict dual contract (v0.9.23.0 lesson — chat_turn returns
    # the TUPLE (answer, context_md, sources)).
    if isinstance(result, dict):
        answer = result.get("answer")
        sources = result.get("sources", [])
        error = result.get("error")
    elif isinstance(result, tuple) and len(result) >= 1:
        answer = result[0]
        sources = result[2] if len(result) >= 3 else []
        error = None
    else:
        answer, sources, error = None, [], None
    if isinstance(sources, list):
        sources = [
            s if isinstance(s, str) else s.get("filename", "")
            for s in sources
        ]
    # v0.9.45.0 Phase 2: stamp the registry — fire finished. A
    # result-level ``error`` counts as failed (mirrors the
    # lifecycle stamping below).
    if task_id:
        try:
            from src.agent.task_runner import end_task
            end_task(
                task_id,
                "failed" if error else "done",
                answer_chars=len(answer or ""),
                error=repr(error)[:500] if error else None,
            )
        except Exception:
            pass
    sess.messages.append({
        "role": "assistant",
        "content": answer or f"[error] {error}",
        "ts": _now(), "sources": sources, "webhook_id": w.id,
    })
    sess.updated_at = _now()
    sm.save(sess)
    # Lifecycle bookkeeping on a FRESH read (atomic with the
    # on-disk state; concurrent manager edits don't race).
    try:
        fresh = mgr.get(w.id)
    except ValueError:
        fresh = None
    if fresh is None:
        fresh = w
    fresh.last_fire_at = _now()
    fresh.fire_count += 1
    fresh.last_session_id = sess.id
    fresh.status = "active"
    # max_fires cap → auto status + disable (same precedence as the
    # scheduler's one-shot handling).
    if (
        fresh.max_fires is not None
        and fresh.fire_count >= fresh.max_fires
    ):
        fresh.status = "exhausted"
        fresh.enabled = False
    if fresh.expires_at:
        try:
            if datetime.now() >= _parse_iso(fresh.expires_at):
                fresh.status = "expired"
                fresh.enabled = False
        except ValueError:
            pass
    if error:
        fresh.last_error = repr(error)[:500]
        fresh.last_error_at = _now()
    else:
        fresh.last_error = None
        fresh.last_error_at = None
    try:
        mgr.save(fresh)
    except Exception as e:
        logger.warning(
            "webhooks: lifecycle save(%s) failed: %r", w.id, e,
        )
    mgr.append_history(fresh.id, {
        "ts": fresh.last_fire_at,
        "prompt": final_prompt,
        "answer": answer,
        "sources": sources,
        "session_id": sess.id,
        "error": error,
        "payload": payload if isinstance(payload, str) else None,
    })
    logger.info(
        "webhooks: fired %s -> session %s (answer=%d chars)",
        fresh.id, sess.id, len(answer or ""),
    )
    return {
        "ok": True,
        "trigger_id": fresh.id,
        "trigger_name": fresh.name,
        "answer": answer,
        "sources": sources,
        "session_id": sess.id,
        "fire_count": fresh.fire_count,
        "status": fresh.status or "active",
        "duration_s": round(
            (datetime.now() - started).total_seconds(), 2,
        ),
    }


# ─── HTTP listener (daemon thread) ─────────────────────────────


def _webhook_host() -> str:
    return (os.environ.get("QDRANT_WEBHOOK_HOST", "")
            or "127.0.0.1").strip()


def _webhook_port() -> int:
    raw = (os.environ.get("QDRANT_WEBHOOK_PORT", "") or "").strip()
    try:
        p = int(raw)
    except (TypeError, ValueError):
        return 8335
    return p if 1 <= p <= 65535 else 8335


class WebhookServer:
    """Daemon-thread HTTP listener over the stdlib
    ``ThreadingHTTPServer`` — no new dependency.

    Routes:
      * ``GET  /healthz``          → ``{"ok": true}``
      * ``GET|POST /trigger/<id>`` → fire the trigger

    Auth: ``?key=<access_key>`` query param OR ``X-Access-Key``
    header, constant-time compared against the trigger's key. No
    key / wrong key → 401; unknown trigger → 404; lifecycle-gated
    (disabled / expired / exhausted / capped) → 403.

    Fires run synchronously in the request thread by default (the
    HTTP response carries the LLM answer) — pass ``?async=1`` to
    get an immediate 202 while the fire runs in a background
    thread. Bind host comes from ``QDRANT_WEBHOOK_HOST`` (default
    127.0.0.1; non-loopback binds log a loud WARNING) and the port
    from ``QDRANT_WEBHOOK_PORT`` (default 8335).
    """

    def __init__(
        self,
        manager: "WebhookManager | None" = None,
        fire: Callable[[WebhookTrigger, Any], dict] | None = None,
        host: str | None = None,
        port: int | None = None,
    ):
        self.mgr = manager or WebhookManager()
        self.fire = fire
        self.host = host or _webhook_host()
        self.port = int(port or _webhook_port())
        self._httpd: Any = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            from http.server import ThreadingHTTPServer

            host, port = self.host, self.port
            try:
                self._httpd = ThreadingHTTPServer(
                    (host, port), _make_handler_class(self),
                )
            except OSError as e:
                logger.warning(
                    "webhooks: server bind failed on %s:%s "
                    "(port busy or no permission) — triggers will "
                    "not be reachable until the port frees up: %r",
                    host, port, e,
                )
                self._httpd = None
                return
            if host not in ("127.0.0.1", "localhost", "::1"):
                logger.warning(
                    "webhooks: NON-LOOPBACK bind %s:%s — each "
                    "trigger URL is a REMOTE prompt-execution "
                    "endpoint protected only by its access key; "
                    "use only on trusted networks.",
                    host, port,
                )
            self._thread = threading.Thread(
                target=self._run, name="webhook-server", daemon=True,
            )
            self._thread.start()
            logger.info(
                "webhooks: server started at %s:%s "
                "(trigger path /trigger/<id>)",
                host, port,
            )

    def stop(self, timeout: float = 2.0) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("webhooks: server stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def trigger_url(self, w: WebhookTrigger, reveal: bool = False) -> str:
        base = f"http://{self.host}:{self.port}"
        key = w.access_key if reveal else w.masked_key()
        return f"{base}/trigger/{w.id}?key={key}"

    def _run(self) -> None:
        try:
            self._httpd.serve_forever(poll_interval=0.2)
        except Exception as e:
            logger.exception("webhooks: server thread died: %r", e)

    # -- routing (shared by GET / POST) ---------------------------

    def _route(self, request) -> None:
        from urllib.parse import parse_qs, urlparse

        try:
            parsed = urlparse(request.path)
            path = parsed.path.rstrip("/")
            qs = parse_qs(parsed.query)
            if path == "/healthz":
                self._json_response(request, 200, {
                    "ok": True,
                    "server": "divine-sense-agent-webhook",
                })
                return
            m = re.fullmatch(r"/trigger/([A-Za-z0-9_-]{1,32})", path)
            if not m:
                self._json_response(request, 404, {
                    "ok": False, "error": "unknown path",
                })
                return
            wid = m.group(1)
            provided = (qs.get("key") or [None])[0] or \
                request.headers.get("X-Access-Key")
            w = self.mgr.get(wid)
            if w is None:
                self._json_response(request, 404, {
                    "ok": False, "error": f"unknown trigger {wid!r}",
                })
                return
            # Auth BEFORE any lifecycle detail leaks.
            if not w.key_matches(provided):
                self._json_response(request, 401, {
                    "ok": False, "error": "invalid access key",
                })
                return
            refusal = w.check_lifecycle()
            if refusal:
                self._json_response(request, 403, {
                    "ok": False,
                    "error": f"trigger is not fireable: {refusal}",
                })
                return
            if self.fire is None:
                self._json_response(request, 503, {
                    "ok": False,
                    "error": "webhook fire engine not wired (server "
                             "started without on_fire)",
                })
                return
            payload = self._read_payload(request)
            if payload == "__INVALID_JSON__":
                self._json_response(request, 400, {
                    "ok": False,
                    "error": "request body is not valid JSON",
                })
                return
            if (qs.get("async") or ["0"])[0] in ("1", "true"):
                threading.Thread(
                    target=self._safe_fire, args=(w, payload),
                    daemon=True,
                ).start()
                self._json_response(request, 202, {
                    "ok": True, "queued": True, "trigger_id": w.id,
                })
                return
            result = self._safe_fire(w, payload)
            if result.get("ok"):
                self._json_response(request, 200, result)
            else:
                self._json_response(request, 500, result)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            logger.exception("webhooks: request error: %r", e)
            try:
                self._json_response(request, 500, {
                    "ok": False, "error": repr(e),
                })
            except Exception:
                pass

    def _read_payload(self, request) -> Any:
        """Extract the caller payload: ``{"payload": ...}`` JSON
        envelope, a bare JSON body, or raw text."""
        length = int(request.headers.get("Content-Length") or 0)
        if length <= 0:
            return None
        raw = request.rfile.read(min(length, 1_000_000))
        text = raw.decode("utf-8", errors="replace")
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return text  # plain-text payload
        if isinstance(body, dict) and "payload" in body:
            return body["payload"]
        return body

    def _safe_fire(self, w: WebhookTrigger, payload: Any) -> dict:
        try:
            if self.fire is None:
                return {
                    "ok": False, "error": "no fire engine wired",
                }
            return self.fire(w, payload)
        except Exception as e:
            logger.exception(
                "webhooks: fire(%s) failed: %r", w.id, e,
            )
            return {"ok": False, "error": repr(e)}

    def _json_response(self, request, code: int, payload: dict) -> None:
        body = json.dumps(
            payload, ensure_ascii=False,
        ).encode("utf-8")
        request.send_response(code)
        request.send_header(
            "Content-Type", "application/json; charset=utf-8",
        )
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)


def _make_handler_class(ws: "WebhookServer"):
    """Build a BaseHTTPRequestHandler subclass that routes every
    method into the singleton ``WebhookServer._route``."""
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def _do(self) -> None:
            ws._route(self)

        do_GET = _do
        do_POST = _do
        do_PUT = _do
        do_DELETE = _do
        do_PATCH = _do

        def log_message(self, fmt, *args):  # quiet per-request lines
            logger.debug("webhooks: " + fmt, *args)

    return Handler