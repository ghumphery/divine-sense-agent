"""McpManager: top-level orchestrator for the agent's tool surface (v0.8.0+)."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .base import McpCallResult, McpClient, McpTool
from .host import LocalTool

logger = logging.getLogger(__name__)


# v0.9.81.0 (精簡核心 + 按需展開 / Two-stage Prompting) —
# ``MCP_SURFACE_MODE`` = full (default, byte-identical) | index.
# In index mode the per-request tool surface carries LEAN schemas
# (parameter keys + types only) for all non-exempt servers; the
# full definition (enums, property descriptions) is delivered ON
# DEMAND — a failed call appends the tool's 參數定義 hint to the
# error text (manager.call). Servers whose semantics live in their
# schema stay full.
_LEAN_EXEMPT_SERVERS = frozenset({"qdrant-local", "meta-local"})


def _surface_mode() -> str:
    mode = (os.environ.get("MCP_SURFACE_MODE", "full")
            or "full").strip().lower()
    return mode if mode in ("full", "index") else "full"


def _surface_tool_limit() -> int:
    """v0.9.82.2 — ``MCP_SURFACE_MAX_TOOLS``: hard cap on the number
    of tools attached to a request. Many providers (OpenAI: max 128)
    reject requests with larger arrays — 400 array_above_max_length.
    0 (default) = no cap; the cap TRIMS the tail of the tool list
    (registration order = priority order) and records the hidden
    servers in ``last_hidden_servers``."""
    raw = (os.environ.get("MCP_SURFACE_MAX_TOOLS", "") or "").strip()
    if not raw:
        return 0
    try:
        n = int(raw)
        return n if n > 0 else 0
    except ValueError:
        return 0


def _schema_hint(schema: Any) -> str:
    """The 擴充層 (Dynamic Fetching) feedback — the full parameter
    definition appended to a failed tool call so the model can retry
    with correct arguments without a second discovery round."""
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties") or {}
    parts: list[str] = []
    for key, p in props.items():
        if not isinstance(p, dict):
            parts.append(f"{key}:string")
            continue
        t = str(p.get("type", "string"))
        if p.get("enum"):
            t += "（" + "|".join(str(x) for x in p["enum"]) + "）"
        desc = str(p.get("description") or "").strip()
        parts.append(f"{key}:{t}" + (f"—{desc}" if desc else ""))
    if not parts:
        return ""
    return "參數定義：" + "；".join(parts)


@dataclass
class ToolSpec:
    """Unified tool descriptor used inside the Agent loop."""

    tool: McpTool
    dispatch: Any  # ("local", LocalTool) or ("remote", McpClient)
    # v0.9.81.0 — MCP_SURFACE_MODE=index: the request carries a LEAN
    # schema (name + one-line description + parameter keys/types only;
    # enum / property descriptions / examples stripped). The full
    # definition is delivered on demand through the tool's own error
    # feedback (manager.call appends the 參數定義 hint to errors).
    lean: bool = False

    def to_openai(self) -> dict[str, Any]:
        description = self.tool.description
        if self.lean:
            # v0.9.81.0: index mode also SHORTENS the description —
            # the first line, ≤60 chars — since the full wording is
            # always available on demand (error hint + meta catalog).
            first_line = (description or "").strip().split("\n")[0]
            description = first_line[:60]
        return {
            "type": "function",
            "function": {
                "name": self.tool.name,
                "description": description,
                "parameters": (
                    self._lean_schema(self.tool.input_schema)
                    if self.lean
                    else self.tool.input_schema
                    or {"type": "object", "properties": {}}
                ),
            },
        }

    @staticmethod
    def _lean_schema(schema: Any) -> dict[str, Any]:
        """v0.9.81.0 (精簡核心) — parameter KEYS + TYPES only: enum
        lists, property descriptions, nested examples and defaults are
        stripped so the initial tool index stays light on context."""
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}
        props = schema.get("properties") or {}
        lean_props: dict[str, Any] = {}
        for key, p in props.items():
            lean_props[key] = {
                "type": (
                    p.get("type", "string")
                    if isinstance(p, dict) else "string"
                )
            }
        lean: dict[str, Any] = {
            "type": "object", "properties": lean_props,
        }
        if schema.get("required"):
            lean["required"] = list(schema["required"])
        return lean


class McpManager:
    """Holds local tools + remote MCP clients, dispatches tool calls."""

    def __init__(self):
        self._local: dict[str, LocalTool] = {}
        self._clients: dict[str, McpClient] = {}
        self._tool_index: dict[str, ToolSpec] = {}
        self.failed: dict[str, str] = {}
        # v0.8.6.3: optional callback to spin up the scheduler
        # daemon after a successful ``add_scheduled_task`` tool
        # call. Wired by ``Agent`` so an LLM-built schedule fires
        # without the user having to manually click "Start daemon"
        # in the GUI. ``None`` = no auto-start (CLI ``schedules
        # daemon`` still works the old way).
        self.auto_start_callback: Callable[[], None] | None = None
        # v0.8.6.3: keep a reference to the in-process scheduler
        # host (if one was registered) so we can push the callback
        # down AFTER the Agent has set it. ``None`` = no scheduler
        # host registered (typical for ``replace_config`` flows).
        self._scheduler_local_host: Any | None = None
        # v0.9.3+: reference to the FilesystemLocalHost so tests
        # and downstream callers can introspect the read /
        # write sandbox roots without re-instantiating the host.
        self._filesystem_local_host: Any | None = None
        # v0.9.6.0: reference to the OpenWithLocalHost so the GUI
        # can push the drawio-viewer opener callback down AFTER
        # ``from_config`` has wired the host (mirrors the scheduler
        # auto-start callback pattern above).
        self._openwith_local_host: Any | None = None
        # v0.9.7.0: reference to the LogAnalysisLocalHost so tests
        # and downstream callers can introspect the log-analysis
        # tool surface without re-instantiating the host.
        self._log_local_host: Any | None = None
        # v0.9.8.0: reference to the ArchiveLocalHost so tests and
        # downstream callers can introspect the archive tool surface
        # without re-instantiating the host.
        self._archive_local_host: Any | None = None
        # v0.9.133.13: reference to the ZipFsLocalHost (archive-as-
        # filesystem member surgery) so tests can introspect the tool
        # surface without re-instantiating the host.
        self._zipfs_local_host: Any | None = None
        # v0.9.9.0: reference to the ExecLocalHost so tests and
        # downstream callers can introspect the exec tool surface
        # without re-instantiating the host.
        self._exec_local_host: Any | None = None
        # v0.9.11.0: reference to the DateTimeLocalHost so tests and
        # downstream callers can introspect the date/time tool surface
        # without re-instantiating the host.
        self._datetime_local_host: Any | None = None
        # v0.9.21.0: reference to the ChatHistoryLocalHost so tests
        # and downstream callers can introspect the chat-history tool
        # surface without re-instantiating the host.
        self._chat_history_local_host: Any | None = None
        # v0.9.59.0: reference to the MemoryLocalHost so tests /
        # introspection paths can resolve the memory tools.
        self._memory_local_host: Any | None = None
        # v0.9.45.0 Phase 2: reference to the TaskLocalHost so tests
        # and downstream callers can introspect the background-task
        # tool surface (run_task_once / list_tasks / get_task_result)
        # without re-instantiating the host.
        self._task_local_host: Any | None = None
        # v0.9.24.0: reference to the MegaStockLocalHost (兆豐 stock
        # query + mock order tools) so tests and downstream callers
        # can introspect the stock tool surface.
        self._stock_local_host: Any | None = None
        # v0.9.64.0: reference to the HicloudLocalHost (中華電信
        # hicloud CaaS/CVPC cloud tools — mock-first) so tests and
        # downstream callers can introspect the hicloud tool surface.
        self._hicloud_local_host: Any | None = None
        self._keep_local_host: Any | None = None
        # v0.9.93.0: Jev 分類器（jev-classifier-local — mock-first）
        # for the same introspection reason.
        self._jev_local_host: Any | None = None
        # v0.9.104.0: ImageGenLocalHost (imagegen-local — OpenRouter
        # Image API 產圖, mock-first).
        self._imagegen_local_host: Any | None = None
        # v0.9.25.0: reference to the WebhookLocalHost (webhook
        # trigger tools — event-driven prompt firing).
        self._webhook_local_host: Any | None = None
        # v0.9.37.0: reference to the OnvifLocalHost (ONVIF camera
        # discovery / favorites / snapshot tools).
        self._onvif_local_host: Any | None = None
        # v0.9.39.0: reference to the CredLocalHost (central
        # credential-set tools — MCP/UI/CLI shared store).
        self._cred_local_host: Any | None = None
        # v0.9.40.0: reference to the NetScanLocalHost (LAN IP + port
        # scanner tools — private networks only by default).
        self._net_scan_local_host: Any | None = None
        # v0.9.43.0: reference to the RuntimeLocalHost (config reload
        # + service restart MCP tools).
        self._runtime_local_host: Any | None = None
        # v0.9.43.0: canonical runtime-ops callback (set by Agent via
        # set_runtime_ops_callback; pushed down to the host).
        self.runtime_ops_callback: Any = None
        # v0.9.13.2+: servers hidden by the MCP_ACTIVE_SERVERS
        # gate during the LAST active_tools() call — surfaced by
        # MetaLocalHost so the LLM never sees tools it cannot
        # call (it faked the call in plain text live).
        self.last_hidden_servers: list[str] = []
        # v0.9.6.0: GUI-side callback for opening .drawio artifacts
        # in the in-app Drawio Viewer window. Set by ``gui/api.py``
        # via ``set_diagrams_opener``; ``None`` = CLI / stdio mode
        # where ``open_file`` / ``open_content`` fall back to the
        # OS file association.
        self.diagrams_opener_callback: Callable[[str], dict] | None = None
        # v0.9.25.0: webhook trigger wiring — auto-start callback
        # (starts the HTTP listener after add) + fire engine
        # (``fire(trigger, payload) -> dict`` for run_webhook_
        # trigger_now). Both set by ``Agent`` via the canonical
        # setters below.
        self.webhook_auto_start_callback: Callable[[], None] | None = None
        self.webhook_fire_callback: Any = None
        # v0.9.69.0: reference to the TeamsAssistLocalHost (Teams
        # desktop-assist tools — screen-watch + reply gate, mock-first)
        # and the canonical fire callback (set by Agent via
        # set_teams_assist_fire_callback; the watch thread calls it on
        # a screen change).
        self._teams_assist_local_host: Any | None = None
        self.teams_assist_fire_callback: Any = None

    def register_local(self, tool: LocalTool) -> None:
        self._local[tool.name] = tool
        self._tool_index[tool.name] = ToolSpec(
            tool=tool.to_mcp_tool(),
            dispatch=("local", tool),
        )

    def register_local_many(self, tools: Iterable[LocalTool]) -> None:
        for t in tools:
            self.register_local(t)

    def register_client(self, client: McpClient) -> None:
        """Connect, list tools, add to index. Best-effort.

        Failures are recorded in ``self.failed`` rather than raised,
        so a partial MCP fleet still works.
        """
        server_name = client.server_name
        try:
            client.connect()
            tools = client.list_tools()
        except Exception as e:
            reason = repr(e)
            logger.warning(
                "[mcp:manager] could not connect to %r: %s",
                server_name, reason,
            )
            self.failed[server_name] = reason
            try:
                client.close()
            except Exception:
                pass
            return

        self._clients[server_name] = client
        for t in tools:
            name = t.name
            if name in self._tool_index:
                old = self._tool_index[name].tool.server_name
                if old == server_name:
                    continue
                suffixed = f"{server_name}__{name}"
                logger.warning(
                    "[mcp:manager] tool %r collides between %r and %r; "
                    "renaming this copy to %r",
                    name, old, server_name, suffixed,
                )
                t = McpTool(
                    name=suffixed,
                    description=t.description,
                    input_schema=t.input_schema,
                    server_name=server_name,
                )
                name = suffixed
            self._tool_index[name] = ToolSpec(
                tool=t,
                dispatch=("remote", client),
            )

    def close(self) -> None:
        """Tear down every remote client.

        Local tools have no lifecycle and are kept around so the
        manager can be re-used without rebuilding the hosts (e.g.
        ``replace_config``). For a full wipe use ``close_all``.
        """
        for c in self._clients.values():
            try:
                c.close()
            except Exception as e:
                logger.debug("[mcp:manager] close %r: %r", c.server_name, e)
        self._clients.clear()
        # Drop entries that point to the now-closed remote clients.
        # Local tool entries stay.
        self._tool_index = {
            name: spec
            for name, spec in self._tool_index.items()
            if spec.dispatch[0] == "local"
        }

    def close_all(self) -> None:
        """Full teardown — clients AND local tools."""
        self.close()
        self._local.clear()
        self._tool_index.clear()

    def reconnect_remote_servers(
        self, path: str | Path | None = None,
    ) -> dict[str, Any]:
        """v0.9.42.0 — re-read the MCP config file and rebuild ALL
        remote clients (close + re-register). Local hosts untouched;
        the MCP_ACTIVE_SERVERS gate re-evaluates per call so a changed
        gate list applies to the very next turn.

        Returns {ok, rebuilt, failed: {name: reason}}. Used by
        ``Agent.reload_runtime`` / the GUI reload button / the config
        watcher (config/mcp.json mtime change).
        """
        import json as _json

        p = Path(path) if path else Path("config/mcp.json")
        servers: list[dict] = []
        if p.exists():
            try:
                data = _json.loads(p.read_text(encoding="utf-8"))
                servers = [
                    s for s in (data.get("servers") or [])
                    if isinstance(s, dict) and s.get("name")
                ]
            except (OSError, ValueError) as e:
                return {"ok": False, "error": repr(e)}
        self.close()  # remote clients only; local hosts survive
        rebuilt = 0
        failed: dict[str, str] = {}
        for s in servers:
            try:
                client = _build_client_from_config(s)
                if client is None:
                    failed[str(s["name"])] = "build failed"
                    continue
                self.register_client(client)
                rebuilt += 1
            except Exception as e:
                failed[str(s.get("name", "?"))] = repr(e)
        return {
            "ok": True, "rebuilt": rebuilt, "failed": failed,
        }

    def replace_config(self, servers: list[dict]) -> None:
        """Drop all remote clients and rebuild from a new servers list.

        Useful for the GUI / API layer to make an edit take effect
        immediately without restarting the Agent. Local hosts
        (``self._local``) are untouched.

        Parameters
        ----------
        servers : list[dict]
            The new ``servers`` array from ``config/mcp.json``.
            Each entry must be a valid ``McpClient`` spec.

        Notes:
            Connect failures during this rebuild are recorded in
            ``self.failed`` exactly as ``from_config()`` does.
        """
        # Tear down current remote clients only.
        self.close()
        for entry in servers or []:
            client = _build_client_from_config(entry)
            if client is None:
                continue
            self.register_client(client)

    def list_tools(self) -> list[McpTool]:
        return [spec.tool for spec in self._tool_index.values()]

    # v0.8.6.3: explicit setter so callers (Agent) can push the
    # auto-start callback AFTER ``from_config`` has finished wiring
    # the scheduler host. Setting ``auto_start_callback`` directly
    # as an attribute still works for backwards compat — this is
    # the canonical path used by ``Agent``.
    def set_auto_start_callback(
        self, callback: Callable[[], None] | None,
    ) -> None:
        self.auto_start_callback = callback
        if (
            callback is not None
            and self._scheduler_local_host is not None
        ):
            try:
                self._scheduler_local_host.attach_auto_start(callback)
            except Exception as e:
                logger.debug(
                    "[mcp:manager] scheduler host auto-start wire "
                    "failed: %r",
                    e,
                )

    def set_diagrams_opener(
        self, callback: Callable[[str], dict] | None,
    ) -> None:
        """v0.9.6.0: canonical setter for the GUI-side drawio
        opener — mirrors ``set_auto_start_callback`` above.

        ``gui/api.py`` wires ``DiagramsWindowManager.open_with_path``
        here so ``open_file`` / ``open_content`` pop the in-app
        Drawio Viewer window for ``.drawio`` artifacts. Safe to
        call before or after ``from_config`` (both orders converge).
        """
        self.diagrams_opener_callback = callback

    def set_webhook_auto_start_callback(
        self, callback: Callable[[], None] | None,
    ) -> None:
        """v0.9.25.0: canonical setter for the webhook HTTP-listener
        auto-start — mirrors ``set_auto_start_callback`` above.

        ``Agent`` wires ``self._auto_start_webhook_server`` here so
        ``add_webhook_trigger`` spins the listener up without an
        explicit ``Agent.start_webhook_server()`` call (the
        v0.8.6.3 scheduler pattern). Safe to call before or after
        ``from_config`` (both orders converge).
        """
        self.webhook_auto_start_callback = callback
        if (
            callback is not None
            and self._webhook_local_host is not None
        ):
            try:
                self._webhook_local_host.attach_auto_start(callback)
            except Exception as e:
                logger.debug(
                    "[mcp:manager] webhook host auto-start wire "
                    "failed: %r",
                    e,
                )

    def set_webhook_fire_callback(self, callback: Any | None) -> None:
        """v0.9.25.0: canonical setter for the webhook fire engine
        (``fire(trigger, payload) -> dict``) used by
        ``run_webhook_trigger_now``.

        ``Agent`` wires a ``make_webhook_fire``-built callable here;
        the host only needs it when the user asks to fire a trigger
        by hand without an HTTP round-trip. Safe to call before or
        after ``from_config`` (both orders converge).
        """
        self.webhook_fire_callback = callback
        if (
            callback is not None
            and self._webhook_local_host is not None
        ):
            try:
                self._webhook_local_host.attach_fire(callback)
            except Exception as e:
                logger.debug(
                    "[mcp:manager] webhook host fire wire "
                    "failed: %r",
                    e,
                )
        # v0.9.41.0: ONVIF event watches fire the SAME engine —
        # motion detected → registered webhook trigger → prompt.
        if (
            callback is not None
            and self._onvif_local_host is not None
        ):
            try:
                self._onvif_local_host.attach_webhook_fire(callback)
            except Exception as e:
                logger.debug(
                    "[mcp:manager] onvif host webhook-fire wire "
                    "failed: %r",
                    e,
                )

    def set_teams_assist_fire_callback(self, callback: Any | None) -> None:
        """v0.9.69.0: canonical setter for the Teams desktop-assist
        fire engine (``fire(payload) -> dict``) that the watch thread
        calls when the screen changed. ``Agent`` wires a
        ``make_teams_assist_fire``-built callable here. Safe to call
        before or after ``from_config`` (both orders converge)."""
        self.teams_assist_fire_callback = callback
        if (
            callback is not None
            and self._teams_assist_local_host is not None
        ):
            try:
                self._teams_assist_local_host.attach_fire(callback)
            except Exception as e:
                logger.debug(
                    "[mcp:manager] teams-assist host fire wire "
                    "failed: %r",
                    e,
                )

    def set_runtime_ops_callback(self, callback: Any | None) -> None:
        """v0.9.43.0: canonical setter for the runtime-ops engine
        (``ops(op, args) -> dict``) used by RuntimeLocalHost's
        reload_config / restart_services / get_runtime_status tools.
        Safe to call before or after ``from_config`` (both orders
        converge)."""
        self.runtime_ops_callback = callback
        if (
            callback is not None
            and self._runtime_local_host is not None
        ):
            try:
                self._runtime_local_host.attach_ops(callback)
            except Exception as e:
                logger.debug(
                    "[mcp:manager] runtime host ops wire failed: %r", e,
                )
        if (
            callback is not None
            and self._openwith_local_host is not None
        ):
            try:
                self._openwith_local_host.attach_diagrams_opener(
                    callback,
                )
            except Exception as e:
                logger.debug(
                    "[mcp:manager] openwith host diagrams-opener "
                    "wire failed: %r",
                    e,
                )

    def list_tool_specs(self) -> list[ToolSpec]:
        return list(self._tool_index.values())

    def openai_tools(self) -> list[dict[str, Any]]:
        return [s.to_openai() for s in self._tool_index.values()]

    # v0.9.13.2+: single source of truth for the
    # MCP_ACTIVE_SERVERS gate. Agent._chat_tools (per-request
    # OpenAI surface) AND MetaLocalHost's catalog (what the LLM
    # may discover) must agree — otherwise the LLM sees tools it
    # cannot call and fakes the call in plain text (observed
    # live 2026-09-04: a launch tool call was never dispatched,
    # the model just echoed the JSON as text).
    def active_specs(self, tool_names=None, lean: bool | None = None
                     ) -> list[ToolSpec]:
        """The gated ToolSpec list: only servers listed in
        ``MCP_ACTIVE_SERVERS`` (empty = all). Unknown names are
        warned; a gate matching ZERO tools falls back to the
        full catalog. The hidden server names are tracked in
        ``last_hidden_servers`` for the meta catalog note.

        v0.9.14.0+: ``tool_names`` — an explicit per-TOOL
        allow-list (the GUI chat tool picker) OVERRIDES the env
        server gate for that request. None = the env gate
        applies. An explicit EMPTY list means the caller
        disabled every tool (honoured — the request simply
        carries no tools)."""
        specs = list(self._tool_index.values())
        # v0.9.81.0 (精簡核心 + 按需展開) — ``MCP_SURFACE_MODE=index``
        # attaches a LEAN schema (keys/types only) for every tool whose
        # server is not in the always-full exempt set; the full
        # definition is delivered on demand via the error-feedback
        # hint in ``call``. Default "full" = byte-identical legacy.
        if lean is None:
            lean = _surface_mode() == "index"
        if lean:
            specs = [
                ToolSpec(
                    tool=s.tool, dispatch=s.dispatch,
                    lean=s.tool.server_name not in _LEAN_EXEMPT_SERVERS,
                )
                for s in specs
            ]
        if tool_names is not None:
            allowed_tools = {str(n) for n in tool_names}
            exposed = [
                s for s in specs if s.tool.name in allowed_tools
            ]
            exposed_servers = {s.tool.server_name for s in exposed}
            self.last_hidden_servers = sorted(
                {s.tool.server_name for s in specs}
                - exposed_servers
            )
            return self._apply_tool_cap(exposed)
        raw = (os.environ.get("MCP_ACTIVE_SERVERS", "") or "").strip()
        if not raw:
            self.last_hidden_servers = []
            return self._apply_tool_cap(specs)
        allowed = {s.strip() for s in raw.split(",") if s.strip()}
        known = {s.tool.server_name for s in specs}
        unknown = allowed - known
        if unknown:
            logger.warning(
                "[mcp:manager] MCP_ACTIVE_SERVERS unknown servers "
                "skipped: %s (known: %s)",
                sorted(unknown), sorted(known),
            )
        exposed = [
            s for s in specs if s.tool.server_name in allowed
        ]
        if not exposed:
            logger.warning(
                "[mcp:manager] MCP_ACTIVE_SERVERS matched 0 tools "
                "— falling back to the full catalog",
            )
            self.last_hidden_servers = []
            return self._apply_tool_cap(specs)
        self.last_hidden_servers = sorted(known - allowed)
        return self._apply_tool_cap(exposed)

    def _apply_tool_cap(self, exposed: list[ToolSpec]) -> list[ToolSpec]:
        """v0.9.82.2/82.3 — enforce ``MCP_SURFACE_MAX_TOOLS`` by
        trimming the TAIL after a priority sort: servers listed
        EARLIER in ``MCP_ACTIVE_SERVERS`` are kept first (the user
        controls priority by list order); servers not listed keep
        their tool-index registration order after them. Hidden
        servers are added to ``last_hidden_servers``."""
        limit = _surface_tool_limit()
        if limit <= 0 or len(exposed) <= limit:
            return exposed
        raw = (os.environ.get("MCP_ACTIVE_SERVERS", "") or "").strip()
        priority = {
            name.strip(): idx
            for idx, name in enumerate(raw.split(","))
            if name.strip()
        }

        def rank(s: ToolSpec):
            return (priority.get(s.tool.server_name, 10 ** 9),)

        ordered = sorted(exposed, key=rank)
        kept, dropped = ordered[:limit], ordered[limit:]
        dropped_servers = sorted({s.tool.server_name for s in dropped})
        logger.warning(
            "[mcp:manager] tool surface %d > MCP_SURFACE_MAX_TOOLS=%d "
            "— trimmed to %d (hidden servers: %s)",
            len(exposed), limit, limit, dropped_servers,
        )
        self.last_hidden_servers = sorted(
            set(self.last_hidden_servers or []) | set(dropped_servers)
        )
        return kept

    def active_tools(self, tool_names=None) -> list[McpTool]:
        """The gated McpTool catalog — MetaLocalHost's
        ``list_mcp_tools`` uses this so the LLM only discovers
        tools it can actually call."""
        return [s.tool for s in self.active_specs(tool_names)]

    def raw_tools(self) -> list[McpTool]:
        """v0.9.133.15 — the UN-gated, UN-capped full catalog (read
        snapshot of ``_tool_index``). Used by
        ``Agent._toolroute_tool_names`` so routed tool NAMES are mined
        from the whole fleet BEFORE any trim — mining from the capped
        catalog silently dropped late-registered servers (live:
        wordcraft trimmed at the 500 cap → the docx form tool
        vanished from routed turn surfaces)."""
        return [s.tool for s in list(self._tool_index.values())]

    def has_tools(self) -> bool:
        return bool(self._tool_index)

    def call(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        """Dispatch a tool call by name, regardless of origin.

        ``is_error`` is True for both "unknown tool" and "tool
        raised an exception"; the Agent loop treats both as
        recoverable so the model can either retry or pivot.
        """
        spec = self._tool_index.get(name)
        if spec is None:
            return McpCallResult(
                text=f"Unknown tool {name!r}",
                is_error=True,
            )
        kind, target = spec.dispatch
        result: McpCallResult
        try:
            if kind == "local":
                result = target(arguments or {})
            else:
                client: McpClient = target
                actual_name = (
                    spec.tool.name.split("__", 1)[-1]
                    if "__" in spec.tool.name
                    else spec.tool.name
                )
                result = client.call_tool(
                    name=actual_name,
                    arguments=arguments or {},
                    timeout=60.0,
                )
        except Exception as e:
            logger.warning(
                "[mcp:manager] tool %r failed: %r", name, e,
            )
            result = McpCallResult(
                text=f"{type(e).__name__}: {e}",
                is_error=True,
            )
        # v0.9.81.0 (按需展開) — a failed call carries the tool's full
        # parameter definition so the model can self-correct with a
        # retry (in index mode the attached schema is lean). Covers
        # BOTH shapes: exceptions (is_error) AND the LocalTool
        # validation-error envelope ({"error": ...}).
        err_text = result.text or ""
        looks_failed = result.is_error or (
            err_text.lstrip().startswith("{")
            and '"error"' in err_text[:160]
        )
        if looks_failed and err_text and "參數定義：" not in err_text:
            hint = _schema_hint(spec.tool.input_schema)
            if hint:
                result.text = f"{err_text}\n（{hint}）"
        return result

    # ─── factory: from config file ─────────────────────────────

    @classmethod
    def from_config(
        cls,
        path: str | Path | None = None,
        *,
        include_local_hosts: bool = True,
    ) -> "McpManager":
        """Build a manager from ``config/mcp.json``.

        Schema::

            {
              "servers": [
                {
                  "name": "qdrant-external",
                  "transport": "stdio",
                  "command": ["python", "-m", "src.mcp_server.server"]
                },
                {
                  "name": "notion",
                  "transport": "streamable_http",
                  "url": "https://mcp.notion.com/v1/mcp",
                  "headers": {"Authorization": "Bearer ${NOTION_TOKEN}"}
                },
                {
                  "name": "legacy-thing",
                  "transport": "sse",
                  "url": "https://example.com/sse"
                }
              ]
            }

        If ``path`` is missing / file empty / malformed, returns
        a manager seeded only with the local hosts.
        """
        mgr = cls()
        if include_local_hosts:
            from .host import (
                FilesystemLocalHost,
                OpenWithLocalHost,
                QdrantLocalHost,
                WebSearchLocalHost,
                MetaLocalHost,
                SchedulerLocalHost,
                TodoLocalHost,
                LogAnalysisLocalHost,
                ArchiveLocalHost,
                ZipFsLocalHost,
                ExecLocalHost,
                DateTimeLocalHost,
                ChatHistoryLocalHost,
                MemoryLocalHost,
                TaskLocalHost,
                MegaStockLocalHost,
                HicloudLocalHost,
                KeepLocalHost,
                JevLocalHost,
                ImageGenLocalHost,
                WebhookLocalHost,
                OnvifLocalHost,
                CredLocalHost,
                NetScanLocalHost,
                RuntimeLocalHost,
                TeamsAssistLocalHost,
                TactiLocalHost,
                SkillLibraryLocalHost,
                SnippetsLocalHost,
                RulebookLocalHost,
            )
            try:
                mgr.register_local_many(QdrantLocalHost().list_tools())
            except Exception as e:
                logger.warning(
                    "[mcp:manager] QdrantLocalHost seed failed: %r", e,
                )
                mgr.failed["qdrant-local"] = repr(e)
            try:
                mgr.register_local_many(WebSearchLocalHost().list_tools())
            except Exception as e:
                logger.warning(
                    "[mcp:manager] WebSearchLocalHost seed failed: %r", e,
                )
                mgr.failed["web-local"] = repr(e)
            # v0.9.3+ (expanded v0.9.4+): FilesystemLocalHost
            # exposes 15 file / directory / search / copy / diff
            # / patch tools. Registered after Qdrant/Web but
            # before Scheduler so its tools show up in
            # MetaLocalHost's catalog dump and so an LLM can call
            # write_file / read_file / diff_files / patch_file
            # from inside a schedule.on_fire handler if it wants.
            try:
                fs = FilesystemLocalHost()
                mgr.register_local_many(fs.list_tools())
                # Keep the host reference so future test /
                # introspection paths can resolve the read /
                # write roots without re-instantiating.
                mgr._filesystem_local_host = fs
            except Exception as e:
                logger.warning(
                    "[mcp:manager] FilesystemLocalHost seed failed: %r",
                    e,
                )
                mgr.failed["filesystem-local"] = repr(e)
            # v0.9.6.0+: OpenWithLocalHost exposes the Explorer-style
            # "open with associated application" tools (open_file /
            # open_content / list_openable_types). Shares the
            # filesystem write sandbox with FilesystemLocalHost;
            # .drawio artifacts are routed to the in-GUI Drawio
            # Viewer when gui/api.py has wired a diagrams opener
            # callback, otherwise they fall back to the OS file
            # association. Registered before Scheduler / Todo / Meta
            # so MetaLocalHost's catalog dump includes it.
            try:
                openwith = OpenWithLocalHost()
                mgr.register_local_many(openwith.list_tools())
                mgr._openwith_local_host = openwith
                # If the caller already set a callback (rare —
                # GUI wiring normally happens after this returns),
                # push it now. Both orders converge.
                if mgr.diagrams_opener_callback is not None:
                    openwith.attach_diagrams_opener(
                        mgr.diagrams_opener_callback,
                    )
            except Exception as e:
                logger.warning(
                    "[mcp:manager] OpenWithLocalHost seed failed: %r", e,
                )
                mgr.failed["openwith-local"] = repr(e)
            # v0.9.9+: ExecLocalHost exposes the sandboxed local
            # compute tools (run_shell / run_bash / run_grep /
            # run_awk / run_python) — cwd pinned to the write
            # sandbox, timeouts + output caps + disaster blocklist.
            # Registered before Scheduler / Todo / Meta so
            # MetaLocalHost's catalog dump includes it.
            try:
                exechost = ExecLocalHost()
                mgr.register_local_many(exechost.list_tools())
                mgr._exec_local_host = exechost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] ExecLocalHost seed failed: %r", e,
                )
                mgr.failed["exec-local"] = repr(e)
            # v0.9.11.0+: DateTimeLocalHost exposes get_current_time
            # (LOCAL + UTC) so the LLM never guesses the clock.
            # Registered before Meta so its catalog dump includes it.
            try:
                dthost = DateTimeLocalHost()
                mgr.register_local_many(dthost.list_tools())
                mgr._datetime_local_host = dthost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] DateTimeLocalHost seed failed: %r", e,
                )
                mgr.failed["datetime-local"] = repr(e)
            # v0.9.21.0+: ChatHistoryLocalHost exposes the chat-history
            # query tools (list_chat_sessions / search_chat_history /
            # get_chat_session) over the persisted sessions, so the
            # LLM can answer 「上次聊過什麼」 / search past chats.
            # Registered before Meta so its catalog dump includes it.
            try:
                chhost = ChatHistoryLocalHost()
                mgr.register_local_many(chhost.list_tools())
                mgr._chat_history_local_host = chhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] ChatHistoryLocalHost seed failed: %r", e,
                )
                mgr.failed["chat-history-local"] = repr(e)
            # v0.9.59.0: MemoryLocalHost exposes the long-term-memory
            # tools (remember_info / search_memory / list_recent_
            # memories / forget_memory / purge_memories) over the
            # Qdrant memory collection. Registered before Meta so its
            # catalog dump includes it.
            try:
                mhost = MemoryLocalHost()
                mgr.register_local_many(mhost.list_tools())
                mgr._memory_local_host = mhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] MemoryLocalHost seed failed: %r", e,
                )
                mgr.failed["memory-local"] = repr(e)
            # v0.9.45.0 Phase 2: TaskLocalHost exposes the background
            # task tools (run_task_once / list_tasks / get_task_result)
            # over the process-wide task registry. The host receives
            # THIS manager so background agents share the same tool
            # fleet instead of rebuilding one per task. Registered
            # before Meta so its catalog dump includes it.
            try:
                thost = TaskLocalHost(mcp_manager=mgr)
                mgr.register_local_many(thost.list_tools())
                mgr._task_local_host = thost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] TaskLocalHost seed failed: %r", e,
                )
                mgr.failed["task-local"] = repr(e)
            # v0.9.24.0+: MegaStockLocalHost exposes the 兆豐 stock
            # query + order tools (mock-first — real orders stay
            # locked). Registered before Meta so its catalog dump
            # includes it.
            try:
                sthost = MegaStockLocalHost()
                mgr.register_local_many(sthost.list_tools())
                mgr._stock_local_host = sthost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] MegaStockLocalHost seed failed: %r", e,
                )
                mgr.failed["stock-local"] = repr(e)
            # v0.9.64.0+: HicloudLocalHost exposes the 中華電信 hicloud
            # CaaS/CVPC cloud tools (mock-first — HICLOUD_MODE=mock
            # default runs a fully simulated in-memory VM book; NO
            # HTTP). Registered before Meta so its catalog dump
            # includes it.
            try:
                hichost = HicloudLocalHost()
                mgr.register_local_many(hichost.list_tools())
                mgr._hicloud_local_host = hichost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] HicloudLocalHost seed failed: %r", e,
                )
                mgr.failed["hicloud-local"] = repr(e)
            # v0.9.88.0+: KeepLocalHost exposes the Google Keep 記事本
            # tools (mock-first — KEEP_MODE=mock default runs a fully
            # simulated in-memory note book; NO network). Registered
            # before Meta so its catalog dump includes it.
            try:
                khhost = KeepLocalHost()
                mgr.register_local_many(khhost.list_tools())
                mgr._keep_local_host = khhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] KeepLocalHost seed failed: %r", e,
                )
                mgr.failed["keep-local"] = repr(e)
            # v0.9.93.0+: JevLocalHost exposes the Jev 分類器 tools
            # (mock-first — JEV_MODE=mock default = deterministic hash
            # pick; NO network). Registered before Meta so its catalog
            # dump includes it.
            try:
                jehost = JevLocalHost()
                mgr.register_local_many(jehost.list_tools())
                mgr._jev_local_host = jehost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] JevLocalHost seed failed: %r", e,
                )
                mgr.failed["jev-classifier-local"] = repr(e)
            # v0.9.104.0+: ImageGenLocalHost exposes the OpenRouter
            # Image API 產圖 tools (mock-first — IMAGEGEN_MODE=mock
            # default writes a Pillow placeholder with NO network;
            # live bills per image via usage.cost). Registered before
            # Meta so its catalog dump includes it.
            try:
                imghost = ImageGenLocalHost()
                mgr.register_local_many(imghost.list_tools())
                mgr._imagegen_local_host = imghost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] ImageGenLocalHost seed failed: %r", e,
                )
                mgr.failed["imagegen-local"] = repr(e)
            # v0.9.109.0+: TactiLocalHost exposes the tactical/workflow
            # plan COMPILER + plan executor (compile →
            # wave-plan → multi-turn execution with verification
            # nodes). Registered before Meta so its catalog dump
            # includes it.
            try:
                skhost = TactiLocalHost(mcp_manager=mgr)
                mgr.register_local_many(skhost.list_tools())
                mgr._tacti_local_host = skhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] TactiLocalHost seed failed: %r", e,
                )
                mgr.failed["tacti-local"] = repr(e)
            # v0.9.116.0+: SkillLibraryLocalHost exposes the skill FILE
            # library tools (import / list / get / search / remove /
            # use) over QDRANT_SKILLS_DIR — the STORAGE half of
            # tacti-local plans. Registered before Meta so its
            # catalog dump includes it.
            try:
                slhost = SkillLibraryLocalHost()
                mgr.register_local_many(slhost.list_tools())
                mgr._skill_library_local_host = slhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] SkillLibraryLocalHost seed failed: %r",
                    e,
                )
                mgr.failed["skill-library-local"] = repr(e)
            # v0.9.122.0 — SnippetsLocalHost: the ⚡ quick-phrases
            # store gets MCP management (list/add/edit/delete/move;
            # entries are MULTI-LINE strings) over the SAME global
            # file the chat panel uses. Registered before Meta so its
            # catalog dump includes it.
            try:
                snhost = SnippetsLocalHost()
                mgr.register_local_many(snhost.list_tools())
                mgr._snippets_local_host = snhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] SnippetsLocalHost seed failed: %r",
                    e,
                )
                mgr.failed["snippets-local"] = repr(e)
            # v0.9.124.0+: RulebookLocalHost exposes the agent-maintained
            # RULE BOOK (rules_list/add/revise/rollback/history) — the
            # Memento-3 rulebook工件: the LLM revises its own reusable
            # rules when the environment breaks its expectations.
            # Registered before Meta so its catalog dump includes it.
            try:
                rbhost = RulebookLocalHost()
                mgr.register_local_many(rbhost.list_tools())
                mgr._rulebook_local_host = rbhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] RulebookLocalHost seed failed: %r", e,
                )
                mgr.failed["rulebook-local"] = repr(e)
            # v0.9.25.0+: WebhookLocalHost exposes the webhook trigger
            # tools (event-driven prompt firing via per-trigger URL +
            # access key). Registered before Meta so its catalog dump
            # includes it.
            try:
                whhost = WebhookLocalHost()
                mgr.register_local_many(whhost.list_tools())
                mgr._webhook_local_host = whhost
                # If the caller (Agent) had already wired the webhook
                # callbacks before from_config, attach them now.
                if mgr.webhook_auto_start_callback is not None:
                    whhost.attach_auto_start(
                        mgr.webhook_auto_start_callback,
                    )
                if mgr.webhook_fire_callback is not None:
                    whhost.attach_fire(mgr.webhook_fire_callback)
            except Exception as e:
                logger.warning(
                    "[mcp:manager] WebhookLocalHost seed failed: %r", e,
                )
                mgr.failed["webhook-local"] = repr(e)
            # v0.9.37.0+: OnvifLocalHost exposes the ONVIF camera
            # tools (discovery / favorites / snapshot — Phase A
            # READ-ONLY). Registered before Meta so its catalog dump
            # includes it.
            try:
                onvifhost = OnvifLocalHost()
                mgr.register_local_many(onvifhost.list_tools())
                mgr._onvif_local_host = onvifhost
                # v0.9.41.0: ONVIF event watches reuse the webhook
                # fire engine (both orders converge via
                # set_webhook_fire_callback below).
                if mgr.webhook_fire_callback is not None:
                    onvifhost.attach_webhook_fire(
                        mgr.webhook_fire_callback,
                    )
            except Exception as e:
                logger.warning(
                    "[mcp:manager] OnvifLocalHost seed failed: %r", e,
                )
                mgr.failed["onvif-local"] = repr(e)
            # v0.9.69.0+: TeamsAssistLocalHost exposes the Teams
            # desktop-assist tools (screen-watch + reply gate;
            # mock-first — QDRANT_TEAMS_ASSIST_MODE=mock default never
            # grabs the real screen). Registered before Meta so its
            # catalog dump includes it.
            try:
                tahost = TeamsAssistLocalHost()
                mgr.register_local_many(tahost.list_tools())
                mgr._teams_assist_local_host = tahost
                # If the caller (Agent) had already wired the fire
                # callback before from_config, attach it now.
                if mgr.teams_assist_fire_callback is not None:
                    tahost.attach_fire(mgr.teams_assist_fire_callback)
            except Exception as e:
                logger.warning(
                    "[mcp:manager] TeamsAssistLocalHost seed failed: %r",
                    e,
                )
                mgr.failed["teams-assist"] = repr(e)
            # v0.9.39.0+: CredLocalHost exposes the central
            # credential-set tools (cred_add / cred_list / cred_get /
            # cred_remove / cred_resolve) — the SAME store the GUI
            # Credentials tab and the CLI manage. Registered before
            # Meta so its catalog dump includes it.
            try:
                credhost = CredLocalHost()
                mgr.register_local_many(credhost.list_tools())
                mgr._cred_local_host = credhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] CredLocalHost seed failed: %r", e,
                )
                mgr.failed["cred-local"] = repr(e)
            # v0.9.40.0+: NetScanLocalHost exposes the LAN IP scanner
            # + port scanner tools (private networks only by default;
            # public targets need QDRANT_NETSCAN_ALLOW_PUBLIC=on).
            try:
                netscanhost = NetScanLocalHost()
                mgr.register_local_many(netscanhost.list_tools())
                mgr._net_scan_local_host = netscanhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] NetScanLocalHost seed failed: %r", e,
                )
                mgr.failed["netscan-local"] = repr(e)
            # v0.9.43.0+: RuntimeLocalHost exposes the config-reload /
            # service-restart MCP tools. The MCP-reconnect op wires
            # straight to the manager (no Agent needed); the ops
            # callback (reload / restart / status) is pushed down by
            # Agent via set_runtime_ops_callback.
            try:
                runtimehost = RuntimeLocalHost()
                mgr.register_local_many(runtimehost.list_tools())
                runtimehost.attach_reconnect(
                    mgr.reconnect_remote_servers,
                )
                if mgr.runtime_ops_callback is not None:
                    runtimehost.attach_ops(mgr.runtime_ops_callback)
                mgr._runtime_local_host = runtimehost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] RuntimeLocalHost seed failed: %r", e,
                )
                mgr.failed["runtime-local"] = repr(e)
            # v0.9.8+: ArchiveLocalHost exposes the filesystem
            # archive tools (archive_create / archive_extract /
            # archive_list / list_archive_formats) for zip / 7z /
            # tar / tar.gz / tar.bz2 / gz inside the filesystem
            # read/write sandboxes. Registered before Scheduler /
            # Todo / Meta so MetaLocalHost's catalog dump includes
            # it.
            try:
                archhost = ArchiveLocalHost()
                mgr.register_local_many(archhost.list_tools())
                mgr._archive_local_host = archhost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] ArchiveLocalHost seed failed: %r", e,
                )
                mgr.failed["archive-local"] = repr(e)
            # v0.9.133.13: ZipFsLocalHost exposes the archive-as-
            # filesystem tools (zipfs_list/read/write/delete/rename/
            # extract_one/info/status) so an uploaded .zip/.tar can be
            # LISTED, EDITED, RENAMED and DELETED inside WITHOUT
            # extraction (cp-on-write atomic rebuilds). Registered
            # right after the archive tools, before Meta's catalog.
            try:
                zipfshost = ZipFsLocalHost()
                mgr.register_local_many(zipfshost.list_tools())
                mgr._zipfs_local_host = zipfshost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] ZipFsLocalHost seed failed: %r", e,
                )
                mgr.failed["zipfs-local"] = repr(e)
            # v0.9.7+: LogAnalysisLocalHost exposes the access-log /
            # syslog anomaly-analysis tools in-process (ingest_log /
            # analyze_anomalies / search_logs / get_log_stats /
            # list_log_sources / remove_logs) against the dedicated
            # QDRANT_LOG_COLLECTION. Registered before Scheduler /
            # Todo / Meta so MetaLocalHost's catalog dump includes it.
            try:
                loghost = LogAnalysisLocalHost()
                mgr.register_local_many(loghost.list_tools())
                mgr._log_local_host = loghost
            except Exception as e:
                logger.warning(
                    "[mcp:manager] LogAnalysisLocalHost seed failed: %r", e,
                )
                mgr.failed["log-analyzer-local"] = repr(e)
            # v0.8.6+: SchedulerLocalHost exposes the 8 schedule
            # management tools in-process so the LLM can drive
            # add/list/disable/run_now/etc. via plain chat without
            # needing the standalone stdio MCP server. Attached
            # after Qdrant / Web so it can compose with their
            # tool surfaces (e.g. schedule fires call into
            # Agent.chat_turn which may use either).
            try:
                sched = SchedulerLocalHost()
                mgr.register_local_many(sched.list_tools())
                # Hand the manager to the scheduler so it can
                # answer "我有哪些 MCP 功能" / future expansion hooks
                # from inside schedule.fire logic. We attach the
                # manager itself (not just a path) so the
                # LocalHost can lazy-init a ScheduleManager on
                # first call.
                sched.attach_manager(mgr)
                # v0.8.6.3: keep the host reference so we can
                # push the auto-start callback down LATER (the
                # Agent wires it lazily on first ``mcp_manager``
                # access — after ``from_config`` has returned).
                mgr._scheduler_local_host = sched
                # If the caller already set a callback (rare —
                # CLI builds the manager before wiring), push it
                # now.
                if mgr.auto_start_callback is not None:
                    sched.attach_auto_start(mgr.auto_start_callback)
            except Exception as e:
                logger.warning(
                    "[mcp:manager] SchedulerLocalHost seed failed: %r", e,
                )
                mgr.failed["scheduler-local"] = repr(e)
            # v0.8.8+: TodoLocalHost exposes the multi-level todo
            # list in-process so the LLM can drive plan / execute
            # loops through chat without spawning the stdio MCP
            # server. Attached after Scheduler so MetaLocalHost
            # (registered below) sees a complete catalog.
            try:
                todo = TodoLocalHost()
                todo.attach_manager(mgr)
                mgr.register_local_many(todo.list_tools())
                mgr._todo_local_host = todo
            except Exception as e:
                logger.warning(
                    "[mcp:manager] TodoLocalHost seed failed: %r", e,
                )
                mgr.failed["todo-local"] = repr(e)
            # v0.8.2+: MetaLocalHost (list_mcp_tools) lets the LLM
            # ask "本地有哪些 MCP 功能" and get an authoritative
            # answer from the live catalog instead of a web search
            # guess. Must be attached AFTER all other hosts are
            # registered so its tool listing is complete.
            try:
                meta = MetaLocalHost()
                mgr.register_local_many(meta.list_tools())
                meta.attach_manager(mgr)
            except Exception as e:
                logger.warning(
                    "[mcp:manager] MetaLocalHost seed failed: %r", e,
                )
                mgr.failed["meta-local"] = repr(e)

        if not path:
            return mgr

        path = Path(path)
        if not path.exists():
            logger.info(
                "[mcp:manager] config %s does not exist; "
                "running with local hosts only.",
                path,
            )
            return mgr

        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(
                "[mcp:manager] cannot parse %s: %r", path, e,
            )
            mgr.failed[f"<config:{path}>"] = repr(e)
            return mgr

        for entry in cfg.get("servers", []) or []:
            client = _build_client_from_config(entry)
            if client is None:
                continue
            mgr.register_client(client)
        return mgr


def _build_client_from_config(entry: dict) -> McpClient | None:
    """Instantiate the right ``McpClient`` subclass for a config entry."""
    name = entry.get("name")
    transport = (entry.get("transport") or "stdio").lower()
    if not name:
        logger.warning(
            "[mcp:manager] config entry missing 'name': %r", entry,
        )
        return None
    try:
        if transport in ("stdio", "local"):
            from .stdio import StdioMcpClient
            cmd = entry.get("command")
            if not cmd or not isinstance(cmd, list):
                logger.warning(
                    "[mcp:manager] stdio server %r missing command",
                    name,
                )
                return None
            env_overlay = entry.get("env") or None
            if isinstance(env_overlay, dict):
                env_overlay = _substitute_env(env_overlay)
            else:
                env_overlay = None
            return StdioMcpClient(
                server_name=name,
                command=[str(x) for x in cmd],
                env=env_overlay,
                cwd=entry.get("cwd"),
            )

        if transport in ("streamable_http", "http", "http_sse"):
            from .http_sse import HttpSseMcpClient
            url = entry.get("url")
            if not url:
                logger.warning(
                    "[mcp:manager] %s server %r missing url",
                    transport, name,
                )
                return None
            headers = entry.get("headers") or None
            if isinstance(headers, dict):
                headers = _substitute_env(headers)
            return HttpSseMcpClient(
                server_name=name,
                url=url,
                headers=headers,
                transport="streamable_http",
                timeout=float(entry.get("timeout", 30.0)),
                sse_read_timeout=float(
                    entry.get("sse_read_timeout", 300.0)
                ),
            )

        if transport == "sse":
            from .http_sse import HttpSseMcpClient
            url = entry.get("url")
            if not url:
                logger.warning(
                    "[mcp:manager] legacy-sse server %r missing url",
                    name,
                )
                return None
            headers = entry.get("headers") or None
            if isinstance(headers, dict):
                headers = _substitute_env(headers)
            return HttpSseMcpClient(
                server_name=name,
                url=url,
                headers=headers,
                transport="sse",
                timeout=float(entry.get("timeout", 5.0)),
                sse_read_timeout=float(
                    entry.get("sse_read_timeout", 300.0)
                ),
            )

        logger.warning(
            "[mcp:manager] %r: unknown transport %r",
            name, transport,
        )
        return None
    except Exception as e:
        logger.error(
            "[mcp:manager] %r build failed: %r", name, e,
        )
        return None


def _substitute_env(d: dict) -> dict:
    """Replace ``${VAR}`` references in string values."""
    import re
    pat = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
    out: dict = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = pat.sub(
                lambda m: os.environ.get(m.group(1), m.group(0)),
                v,
            )
        else:
            out[k] = v
    return out
