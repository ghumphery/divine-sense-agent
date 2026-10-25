"""MCP server: schedule management (v0.8.6+).

Exposes the local ``ScheduleManager`` as MCP tools so an LLM agent
can drive the scheduler conversationally:

* ``add_scheduled_task(name, prompt, schedule_type, cron | interval_seconds | once_at, use_tools, use_web_search, web_max_results)``  -  register a task; returns ``{id, next_fire_at}``.
* ``delete_scheduled_task(task_id | name)``  -  remove a task; returns ``{deleted: bool}``.
* ``list_scheduled_tasks(enabled_only?)``  -  table view (id, name, type, enabled, next_fire_at, fire_count, last_status).
* ``get_scheduled_task(task_id | name)``  -  full detail incl. ``last_error`` / ``last_error_at``.
* ``enable_scheduled_task(task_id)`` / ``disable_scheduled_task(task_id)``  -  toggle.
* ``run_scheduled_task_now(task_id)``  -  fire synchronously; returns ``{session_id, fire_count}``.
* ``get_scheduled_task_errors(task_id, since?, limit?)``  -  query history.jsonl for past failures.
* ``get_scheduled_task_status(task_id)``  -  v0.8.7+ lifecycle state (status / fire_count / max_fires / fires_remaining / expires_at / seconds_until_next_fire / last_error + last 5 runs).
* ``list_scheduled_task_runs(task_id, limit?)``  -  v0.8.7+ tail of history.jsonl (newest last, answer truncated to 500 chars).
* ``verify_artifact(path, checks?)``  -  v0.9.106.0+ mechanical (ZERO-LLM) artifact verification — exists / min_bytes / JSON parse / per-field requirements (dotted path, unknown types fail closed). Shared engine with the in-process host and the schedule-fire verification node.

Transport
---------
Run as a stdio MCP server so it composes with the existing fleet:

    python -m src.mcp_server.scheduler_server

Add to ``config/mcp.json``::

    {
      "name": "scheduler",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.scheduler_server"]
    }

Note: the **in-process** LocalHost (``SchedulerLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically  -  most users won't need this standalone server at
all. The stdio server exists for external MCP clients (Claude
Desktop, Cline, etc.) that want to drive the scheduler without
the Divine Sense agent in the loop.

All storage is local  -  ``$QDRANT_RAG_HOME/schedules/``  -  so the
server inherits the same atomic JSON / ring-buffered history
guarantees as the CLI / GUI surfaces.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)


_ALLOWED_TYPES = ("once_at", "cron", "interval_seconds")


def _resolve_id_or_name(mgr: Any, ident: str) -> str | None:
    """Return the canonical schedule id for a user-supplied identifier.

    Accepts either the 12-char hex id (exact match) or the
    human-readable ``name`` (first match wins). Returns ``None``
    when nothing matches.
    """
    if not ident or not isinstance(ident, str):
        return None
    ident = ident.strip()
    if not ident:
        return None
    s = mgr.get(ident)
    if s is not None:
        return s.id
    s = mgr.find_by_name(ident)
    if s is not None:
        return s.id
    return None


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


def _ok(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(
        type="text", text=json.dumps(payload, ensure_ascii=False),
    )]


def _status_label(d: dict[str, Any]) -> str:
    if not d.get("enabled", True):
        return "paused"
    if d.get("last_error"):
        return "errored"
    if d.get("schedule_type") == "once_at" and d.get("fire_count", 0) > 0:
        return "completed"
    if d.get("next_fire_at"):
        return "armed"
    return "idle"


def _schedule_to_dict(s: Any) -> dict[str, Any]:
    """Serialise a ``Schedule`` to a JSON-friendly dict for tool output."""
    d = s.to_dict()
    return {
        "id": d.get("id"),
        "name": d.get("name"),
        "prompt": d.get("prompt"),
        "schedule_type": d.get("schedule_type"),
        "cron": d.get("cron"),
        "interval_seconds": d.get("interval_seconds"),
        "once_at": d.get("once_at"),
        "enabled": d.get("enabled"),
        "fire_count": d.get("fire_count", 0),
        "last_fire_at": d.get("last_fire_at"),
        "next_fire_at": d.get("next_fire_at"),
        "last_session_id": d.get("last_session_id"),
        "use_tools": d.get("use_tools", True),
        "use_web_search": d.get("use_web_search", False),
        "web_max_results": d.get("web_max_results", 5),
        "last_error": d.get("last_error"),
        "last_error_at": d.get("last_error_at"),
        "created_at": d.get("created_at"),
        "status": _status_label(d),
    }


def _validate_add_args(args: dict[str, Any]) -> str | None:
    """Return an error message string (or ``None`` on success)."""
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return "'prompt' must be a non-empty string."
    if len(prompt) > 8000:
        return f"'prompt' is {len(prompt)} chars; max 8000."
    schedule_type = args.get("schedule_type", "interval_seconds")
    if schedule_type not in _ALLOWED_TYPES:
        return (
            f"'schedule_type' must be one of "
            f"{list(_ALLOWED_TYPES)}, got {schedule_type!r}."
        )
    name = args.get("name", "")
    if name is not None and not isinstance(name, str):
        return "'name' must be a string."
    if isinstance(name, str) and len(name) > 80:
        return f"'name' is {len(name)} chars; max 80."
    if schedule_type == "cron":
        cron = args.get("cron")
        if not isinstance(cron, str) or not cron.strip():
            return "'cron' is required when schedule_type='cron'."
        if len(cron.split()) != 5:
            return (
                f"'cron' must be 5 space-separated fields "
                f"(minute hour dom month dow), got {cron!r}."
            )
    if schedule_type == "interval_seconds":
        # Allow the LLM to call add with just a prompt  -  when
        # interval_seconds is omitted we default to 60 s in
        # ``_do_add`` (mirrors "set up a recurring check" UX).
        # Only complain if the caller provided a wrong value.
        iv = args.get("interval_seconds", 60)
        if not isinstance(iv, int) or iv <= 0:
            return (
                "'interval_seconds' must be a positive integer "
                "when schedule_type='interval_seconds'."
            )
        if iv < 5:
            return (
                f"'interval_seconds' must be >= 5 (got {iv}); "
                "the scheduler ticks once per second and smaller "
                "intervals would be wasted."
            )
    if schedule_type == "once_at":
        once = args.get("once_at")
        if not isinstance(once, str) or not once.strip():
            return (
                "'once_at' must be an ISO-8601 string when "
                "schedule_type='once_at'."
            )
    return None


# ──────────────────────────────────────────────────────────────
# Server
# ──────────────────────────────────────────────────────────────


class SchedulerMCPServer:
    """MCP server exposing ``ScheduleManager`` to an LLM agent."""

    def __init__(self, schedules_root: str | None = None):
        from src.agent.scheduler import ScheduleManager
        if schedules_root:
            self.mgr = ScheduleManager(root=schedules_root)
        else:
            self.mgr = ScheduleManager()
        self.server = Server("divine-sense-agent-scheduler")
        self._register_tools()

    # Tool list lives in a module-level method so unit tests can
    # call it without spinning up the full Server.
    def _tool_specs(self) -> list[Tool]:
        return _TOOL_SPECS

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return list(_TOOL_SPECS)

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            try:
                if name == "add_scheduled_task":
                    return await self._handle_add(arguments)
                if name == "delete_scheduled_task":
                    return await self._handle_delete(arguments)
                if name == "list_scheduled_tasks":
                    return await self._handle_list(arguments)
                if name == "get_scheduled_task":
                    return await self._handle_get(arguments)
                if name == "enable_scheduled_task":
                    return await self._handle_set_enabled(arguments, True)
                if name == "disable_scheduled_task":
                    return await self._handle_set_enabled(arguments, False)
                if name == "run_scheduled_task_now":
                    return await self._handle_run_now(arguments)
                if name == "get_scheduled_task_errors":
                    return await self._handle_get_errors(arguments)
                # v0.8.7+: lifecycle snapshot + raw history tail.
                # Shared with the in-process SchedulerLocalHost
                # handlers so stdio and in-process paths stay in lockstep.
                if name == "get_scheduled_task_status":
                    return await self._handle_get_status(arguments)
                if name == "list_scheduled_task_runs":
                    return await self._handle_list_runs(arguments)
                # v0.9.106.0 — P1 機械式檢查（獨立評估器）。
                if name == "verify_artifact":
                    return await self._handle_verify_artifact(arguments)
            except Exception as e:
                logger.exception(
                    "[scheduler-mcp] tool %r failed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")
            return _err(f"Unknown tool: {name}")

    # ── handlers (sync, wrapped in to_thread) ──────────────────
    async def _handle_add(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_add, args)

    def _do_add(self, args: dict) -> list[TextContent]:
        err = _validate_add_args(args)
        if err:
            return _err(err)
        from src.agent.scheduler import Schedule
        kwargs: dict[str, Any] = {
            "prompt": args["prompt"].strip(),
            "schedule_type": args.get("schedule_type", "interval_seconds"),
        }
        name = args.get("name")
        if isinstance(name, str) and name.strip():
            kwargs["name"] = name.strip()
        if "cron" in args:
            kwargs["cron"] = args["cron"]
        # v0.9.106.0 (P0 驗證前置化): optional per-schedule opt-out
        # of the fire-time verification directive + machine guard.
        if "verify_required" in args and args["verify_required"] is not None:
            kwargs["verify_required"] = bool(args["verify_required"])
        # v0.9.111.0 — optional fire-time model pin (catalog-validated,
        # fail-closed ADD; ""/None = default chain; fire-time staleness
        # degrades to the default chain inside make_agent_on_fire).
        # NOTE: local names must not shadow the module-level ``_err``.
        if "llm" in args and args["llm"]:
            from src.agent.task_llm import coerce_llm_pin

            pin_val, pin_err = coerce_llm_pin(args["llm"])
            if pin_err:
                return _err(str(pin_err))
            kwargs["llm"] = pin_val
        # Default interval to 60 s when only prompt is provided  - 
        # this matches the LLM's mental model of "set up a recurring
        # task" without forcing the caller to specify a number.
        if "interval_seconds" in args:
            kwargs["interval_seconds"] = args["interval_seconds"]
        elif kwargs["schedule_type"] == "interval_seconds":
            kwargs["interval_seconds"] = 60
        if "once_at" in args:
            kwargs["once_at"] = args["once_at"]
        if "use_tools" in args:
            kwargs["use_tools"] = bool(args["use_tools"])
        if "use_web_search" in args:
            kwargs["use_web_search"] = bool(args["use_web_search"])
        if "web_max_results" in args:
            kwargs["web_max_results"] = int(args["web_max_results"])
        # v0.8.7+: lifecycle caps. Validation happens in the
        # dataclass + ``mgr.save`` (max_fires must be positive int;
        # expires_at must be a valid ISO-8601). Bad input raises
        # so the LLM gets a clean error rather than a silent
        # ignore.
        if "max_fires" in args and args["max_fires"] is not None:
            try:
                mf = int(args["max_fires"])
            except (TypeError, ValueError):
                return _err("'max_fires' must be a positive integer or null.")
            if mf <= 0:
                return _err("'max_fires' must be a positive integer or null.")
            kwargs["max_fires"] = mf
        if "expires_at" in args and args["expires_at"] is not None:
            kwargs["expires_at"] = args["expires_at"]
        s = Schedule(**kwargs)
        try:
            s = self.mgr.save(s)
        except ValueError as e:
            return _err(f"invalid schedule: {e}")
        return _ok({
            "ok": True,
            "id": s.id,
            "name": s.name,
            "schedule_type": s.schedule_type,
            "next_fire_at": s.next_fire_at,
            "enabled": s.enabled,
            "status": "armed" if s.enabled and s.next_fire_at else "idle",
        })

    async def _handle_delete(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_delete, args)

    def _do_delete(self, args: dict) -> list[TextContent]:
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return _err("'task_id' must be a non-empty string.")
        sid = _resolve_id_or_name(self.mgr, ident)
        if sid is None:
            return _err(
                f"no schedule found for {ident!r}. Use "
                "list_scheduled_tasks to see available ids."
            )
        removed = self.mgr.delete(sid)
        return _ok({"ok": True, "deleted": removed, "id": sid})

    async def _handle_list(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_list, args)

    def _do_list(self, args: dict) -> list[TextContent]:
        enabled_only = bool(args.get("enabled_only", False))
        tasks = []
        for s in self.mgr.list():
            if enabled_only and not s.enabled:
                continue
            d = _schedule_to_dict(s)
            tasks.append({
                "id": d["id"],
                "name": d["name"],
                "schedule_type": d["schedule_type"],
                "enabled": d["enabled"],
                "fire_count": d["fire_count"],
                "last_fire_at": d["last_fire_at"],
                "next_fire_at": d["next_fire_at"],
                "status": d["status"],
                "last_error": d["last_error"],
            })
        return _ok({"count": len(tasks), "tasks": tasks})

    async def _handle_get(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_get, args)

    def _do_get(self, args: dict) -> list[TextContent]:
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return _err("'task_id' must be a non-empty string.")
        sid = _resolve_id_or_name(self.mgr, ident)
        if sid is None:
            return _err(f"no schedule found for {ident!r}.")
        s = self.mgr.get(sid)
        if s is None:
            return _err(f"schedule {sid!r} disappeared mid-call.")
        return _ok({"ok": True, "task": _schedule_to_dict(s)})

    async def _handle_set_enabled(
        self, args: dict, enabled: bool,
    ) -> list[TextContent]:
        return await asyncio.to_thread(
            self._do_set_enabled, args, enabled,
        )

    def _do_set_enabled(
        self, args: dict, enabled: bool,
    ) -> list[TextContent]:
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return _err("'task_id' must be a non-empty string.")
        sid = _resolve_id_or_name(self.mgr, ident)
        if sid is None:
            return _err(f"no schedule found for {ident!r}.")
        s = self.mgr.set_enabled(sid, enabled)
        if s is None:
            return _err(f"schedule {sid!r} disappeared mid-call.")
        return _ok({
            "ok": True, "id": sid,
            "enabled": s.enabled, "next_fire_at": s.next_fire_at,
        })

    async def _handle_run_now(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_run_now, args)

    def _do_run_now(self, args: dict) -> list[TextContent]:
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return _err("'task_id' must be a non-empty string.")
        sid = _resolve_id_or_name(self.mgr, ident)
        if sid is None:
            return _err(f"no schedule found for {ident!r}.")
        s = self.mgr.get(sid)
        if s is None:
            return _err(f"schedule {sid!r} disappeared mid-call.")
        from src.agent.agent import Agent
        from src.agent.scheduler import make_agent_on_fire
        agent = Agent()
        # v0.9.111.0 — pin-aware fire factory (host parity).
        on_fire = make_agent_on_fire(
            agent_factory=lambda llm=None: (
                agent if not llm else Agent(task_llm=llm)
            ),
        )
        try:
            on_fire(s)
        except Exception as e:
            return _err(
                f"fired {sid} but the prompt raised: "
                f"{type(e).__name__}: {e}. Use "
                "get_scheduled_task_errors to inspect the history."
            )
        return _ok({
            "ok": True, "id": sid,
            "session_id": s.last_session_id,
            "fire_count": s.fire_count,
            "last_fire_at": s.last_fire_at,
            "next_fire_at": s.next_fire_at,
        })

    async def _handle_get_errors(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_get_errors, args)

    def _do_get_errors(self, args: dict) -> list[TextContent]:
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return _err("'task_id' must be a non-empty string.")
        sid = _resolve_id_or_name(self.mgr, ident)
        if sid is None:
            return _err(f"no schedule found for {ident!r}.")
        try:
            limit = int(args.get("limit", 20))
        except (TypeError, ValueError):
            return _err("'limit' must be an integer.")
        if limit <= 0 or limit > 100:
            return _err("'limit' must be between 1 and 100.")
        since = args.get("since")
        if since is not None and not isinstance(since, str):
            return _err("'since' must be an ISO-8601 string.")
        history = self.mgr.get_history(sid, limit=max(limit * 4, 50))
        errors: list[dict[str, Any]] = []
        for entry in history:
            if not entry.get("error"):
                continue
            if since:
                ts = entry.get("ts") or ""
                if isinstance(ts, str) and ts < since:
                    continue
            errors.append({
                "ts": entry.get("ts"),
                "error": entry.get("error"),
                "prompt": entry.get("prompt"),
                "session_id": entry.get("session_id"),
            })
            if len(errors) >= limit:
                break
        s = self.mgr.get(sid)
        head: dict[str, Any] | None = None
        if s and s.last_error:
            head = {
                "ts": s.last_error_at,
                "error": s.last_error,
                "prompt": s.prompt,
                "session_id": s.last_session_id,
                "source": "schedule.last_error",
            }
        return _ok({
            "ok": True, "id": sid, "current": head,
            "history_count": len(errors), "errors": errors,
        })


    # ── v0.8.7+ lifecycle snapshot / run history ────────────────

    async def _handle_get_status(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_get_status, args)

    async def _handle_list_runs(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_list_runs, args)

    def _do_get_status(self, args: dict) -> list[TextContent]:
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return _err("'task_id' must be a non-empty string.")
        sid = _resolve_id_or_name(self.mgr, ident)
        if sid is None:
            return _err(f"no schedule found for {ident!r}.")
        prog = self.mgr.get_progress(sid)
        if prog is None:
            return _err(f"schedule {sid!r} disappeared mid-call.")
        return _ok({"ok": True, "id": sid, "progress": prog})

    def _do_list_runs(self, args: dict) -> list[TextContent]:
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return _err("'task_id' must be a non-empty string.")
        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            return _err("'limit' must be an integer.")
        if limit <= 0 or limit > 100:
            return _err("'limit' must be between 1 and 100.")
        sid = _resolve_id_or_name(self.mgr, ident)
        if sid is None:
            return _err(f"no schedule found for {ident!r}.")
        runs = self.mgr.list_runs(sid, limit=limit)
        if runs is None:
            return _err(f"schedule {sid!r} disappeared mid-call.")
        return _ok({"ok": True, "id": sid, "count": len(runs), "runs": runs})

    # ── v0.9.106.0 — P1 機械式檢查（獨立評估器） ──────────────────

    async def _handle_verify_artifact(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_verify_artifact, args)

    def _do_verify_artifact(self, args: dict) -> list[TextContent]:
        from src.agent.scheduler import verify_artifact
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return _err("'path' must be a non-empty string.")
        checks = args.get("checks")
        if checks is not None and not isinstance(checks, list):
            return _err(
                "'checks' must be a list of check objects "
                '(e.g. [{"type": "min_bytes", "value": 100}]).'
            )
        try:
            return _ok(verify_artifact(path, checks))
        except Exception as e:
            return _err(f"verify_artifact failed: {type(e).__name__}: {e}")

    # ── stdio entry point ─────────────────────────────────────
    async def run_stdio(self) -> None:
        """Run the MCP server over stdio."""
        async with stdio_server() as (r, w):
            await self.server.run(
                r, w, self.server.create_initialization_options(),
            )





# Module-level tool specs  -  populated lazily so importing this
# module without an MCP server runtime is cheap. v0.8.7+ adds
# ``get_scheduled_task_status`` + ``list_scheduled_task_runs``;
# the dispatcher in ``SchedulerMCPServer._call`` is the source
# of truth for tool behaviour  -  this list only feeds the test-
# time ``_TOOL_SPECS`` introspection.
_TOOL_SPECS: list[Tool] = []


def _build_tool_specs() -> list[Tool]:
    """Construct the 11 Tool() objects describing the public surface.

    Only used for introspection / unit tests; the runtime
    dispatcher in ``SchedulerMCPServer._call`` does NOT consume
    this list  -  it dispatches by ``if name == "..."`` lookup.
    """
    return [
        Tool(
            name="add_scheduled_task",
            description="Register a new scheduled task.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "schedule_type": {"type": "string"},
                    "name": {"type": "string"},
                    "interval_seconds": {"type": "integer"},
                    "cron": {"type": "string"},
                    "once_at": {"type": "string"},
                    "use_tools": {"type": "boolean"},
                    "use_web_search": {"type": "boolean"},
                    "web_max_results": {"type": "integer"},
                    # v0.8.7+:
                    "max_fires": {
                        "type": ["integer", "null"],
                        "description": (
                            "Cap total fire count. "
                            "Auto-disables the schedule "
                            "with status=exhausted when "
                            "fire_count reaches this."
                        ),
                    },
                    "expires_at": {
                        "type": ["string", "null"],
                        "description": (
                            "ISO-8601 timestamp after which "
                            "the schedule stops firing. "
                            "status=expired once now >= "
                            "expires_at."
                        ),
                    },
                    # v0.9.106.0 (P0 驗證前置化):
                    "verify_required": {
                        "type": ["boolean", "null"],
                        "description": (
                            "Default true. When true, every fire "
                            "appends a VERIFICATION DIRECTIVE to "
                            "the prompt (verify_artifact / "
                            "get_chat_session read-back before "
                            "any success conclusion) and a "
                            "post-fire machine guard runs a "
                            "corrective verification turn when "
                            "the reply claims success with zero "
                            "verification evidence. Pass false "
                            "to restore the legacy free-form "
                            "prompt for this schedule."
                        ),
                    },
                    # v0.9.111.0 — optional fire-time model pin.
                    "llm": {
                        "type": "string",
                        "description": (
                            "Optional fire-time chat-model pin "
                            "('provider:model', openrouter | ollama; "
                            "catalog-validated now, fail-closed; "
                            "omit = the default provider chain)."
                        ),
                    },
                },
                "required": ["prompt"],
            },
        ),
        Tool(name="delete_scheduled_task", description="Remove a scheduled task.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="list_scheduled_tasks", description="List all scheduled tasks.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_scheduled_task", description="Fetch a single task by id/name.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="enable_scheduled_task", description="Re-enable a paused task.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="disable_scheduled_task", description="Pause a task.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="run_scheduled_task_now", description="Fire a task synchronously.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_scheduled_task_errors", description="Query past failures.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(
            name="get_scheduled_task_status",
            description=(
                "v0.8.7+ lifecycle snapshot of a single schedule."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="list_scheduled_task_runs",
            description=(
                "v0.8.7+ tail of history.jsonl (newest last)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="verify_artifact",
            description=(
                "v0.9.106.0+ mechanical (ZERO-LLM) artifact "
                "verification: existence, min_bytes, JSON parse, "
                "per-field requirements (dotted path, unknown "
                "types fail closed). Returns {ok, size, passed, "
                "errors}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File to verify — inside the "
                            "filesystem sandbox (relative paths "
                            "anchor at the write sandbox)."
                        ),
                    },
                    "checks": {
                        "type": "array",
                        "description": (
                            "Optional list of checks, e.g. "
                            '[{"type": "min_bytes", "value": 100}, '
                            '{"field": "stage1.status", '
                            '"type": "nonempty"}, {"field": '
                            '"summary.confidence", "type": '
                            '"json_number", "min": 0, "max": 1}].'
                        ),
                        "items": {"type": "object"},
                    },
                },
                "required": ["path"],
            },
        ),
    ]


_TOOL_SPECS = _build_tool_specs()

def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout is reserved for JSON-RPC
    )
    server = SchedulerMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()





