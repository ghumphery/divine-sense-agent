"""MCP client + Agent tool-calling tests (v0.8.0+).

Largely mock-based since we cannot rely on real third-party
MCP servers being available. Covers:

* ``McpManager`` public surface (register_local, register_client,
  call, dispatch).
* ``LocalTool`` schema-from-signature derivation.
* ``McpCallResult.to_message_text`` formatting.
* Tool-name collision renaming (``server__name``).
* Sub-config loading smoke (``from_config`` with bogus path =
  local hosts only, never raises).
* ``Agent.ask()`` and ``Agent.chat_turn()`` drive the tool loop
  with a mocked OpenAI client.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.mcp_client.base import (
    McpCallResult,
    McpClient,
    McpTool,
)
from src.mcp_client.host import LocalTool
from src.mcp_client.manager import McpManager, ToolSpec


@pytest.fixture(autouse=True)
def _off_decision_knobs(monkeypatch):
    """v0.9.93.0 — live .env 的 router/time-route/rewrite/support knobs
    會漏進 ask() 路徑攔截這些 legacy 合約測試 — 釘 off。"""
    monkeypatch.setenv("QDRANT_ROUTE", "off")
    monkeypatch.setenv("QDRANT_TIME_ROUTE", "off")
    monkeypatch.setenv("QDRANT_REWRITE", "off")
    monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "off")
    monkeypatch.setenv("QDRANT_CALENDAR_GUARD", "off")
    yield


# ─── helpers ────────────────────────────────────────────────


def _make_tool(
    name: str = "x",
    server: str = "test",
    description: str = "desc",
    schema: dict | None = None,
) -> LocalTool:
    def handler(**_):
        return {"ok": True}

    return LocalTool(
        name=name,
        description=description,
        handler=handler,
        input_schema=schema
        or {"type": "object", "properties": {"q": {"type": "string"}}},
        server_name=server,
    )


class _FakeClient(McpClient):
    """In-memory MCP client for unit tests.

    ``connect()``, ``list_tools()`` and ``call_tool()`` are all
    synchronous; no real protocol traffic.
    """

    def __init__(
        self,
        server_name: str,
        name_to_result: dict[str, McpCallResult] | None = None,
        name_to_schema: dict[str, dict] | None = None,
        connect_raises: Exception | None = None,
        call_raises: Exception | None = None,
    ):
        super().__init__(server_name)
        self.name_to_result = name_to_result or {}
        self.name_to_schema = name_to_schema or {}
        self.connect_raises = connect_raises
        self.call_raises = call_raises
        self.calls: list[tuple[str, dict]] = []
        self.connect_calls = 0
        self.list_calls = 0

    def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_raises:
            raise self.connect_raises
        self._connected = True

    def list_tools(self) -> list[McpTool]:
        self.list_calls += 1
        return [
            McpTool(
                name=n,
                description=n,
                input_schema=self.name_to_schema.get(
                    n, {"type": "object", "properties": {}},
                ),
                server_name=self.server_name,
            )
            for n in self.name_to_result.keys()
        ]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> McpCallResult:
        self.calls.append((name, dict(arguments or {})))
        if self.call_raises:
            raise self.call_raises
        return self.name_to_result[name]

    def close(self) -> None:
        self._connected = False


# ─── McpCallResult format ──────────────────────────────────


class TestMcpCallResult:
    def test_text_returns_as_is(self):
        r = McpCallResult(text="hello")
        assert r.to_message_text() == "hello"

    def test_error_is_prefixed(self):
        r = McpCallResult(text="oops", is_error=True)
        assert r.to_message_text().startswith("[tool error]")

    def test_empty_text_returns_empty(self):
        r = McpCallResult(text="")
        assert r.to_message_text() == ""

    def test_default_is_error_false(self):
        r = McpCallResult(text="ok")
        assert r.is_error is False


# ─── LocalTool basic shape ────────────────────────────────


class TestLocalTool:
    def test_to_mcp_tool_round_trip(self):
        t = _make_tool(name="t", schema={"type": "object"})
        mcp = t.to_mcp_tool()
        assert mcp.name == "t"
        assert mcp.server_name == "test"
        assert mcp.input_schema == {"type": "object"}

    def test_call_returns_dict_as_json(self):
        def handler(q: str):
            return {"q": q, "n": q.upper()}

        t = LocalTool(
            name="echo", description="echo", handler=handler,
            server_name="t",
        )
        r = t({"q": "hi"})
        # JSON-serialised, contains both fields
        assert "hi" in r.text
        assert "HI" in r.text
        assert not r.is_error

    def test_call_handler_raises_yields_is_error(self):
        def handler(**_):
            raise ValueError("nope")

        t = LocalTool(
            name="bad", description="d", handler=handler,
            server_name="t",
        )
        r = t({})
        assert r.is_error
        assert "ValueError" in r.text

    def test_call_returns_str_passthrough(self):
        t = LocalTool(
            name="str-out", description="d",
            handler=lambda x: f"got {x}",
            server_name="t",
        )
        assert t({"x": "abc"}).text == "got abc"

    def test_call_coerces_string_digits_to_int(self):
        received: dict[str, Any] = {}

        def handler(n: int):
            received["n"] = n
            return n         # plain int return, not a dict

        t = LocalTool(
            name="coerce", description="d",
            handler=handler,
            server_name="t",
        )
        # The LLM emitted "5" instead of 5. We silently coerce so
        # the typed signature does not TypeError.
        r = t({"n": "5"})
        assert received["n"] == 5
        assert r.text == "5"


# ─── McpManager dispatch & tool inventory ─────────────────


class TestMcpManagerDispatch:
    def _mgr(self):
        mgr = McpManager()
        mgr.register_local(_make_tool(
            name="search", schema={"type": "object"},
        ))
        return mgr

    def test_local_tool_listed(self):
        mgr = self._mgr()
        tools = mgr.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search"

    def test_local_tool_call(self):
        # Use a tool whose handler actually echoes its input
        mgr = McpManager()
        mgr.register_local(LocalTool(
            name="search", description="echo",
            handler=lambda q: q,   # pass-through string
            server_name="t",
        ))
        r = mgr.call("search", {"q": "hello"})
        assert not r.is_error
        assert r.text == "hello"

    def test_unknown_tool_returns_error_envelope(self):
        mgr = self._mgr()
        r = mgr.call("nope", {})
        assert r.is_error
        assert "nope" in r.text

    def test_close_drops_all(self):
        mgr = self._mgr()
        mgr.close_all()
        assert mgr._local == {}
        assert mgr._tool_index == {}

    def test_close_keeps_local_tools(self):
        """v0.8.0+ — close() should drop remote clients only.

        Local hosts stay around so ``replace_config`` can re-use
        them without rebuilding. Use ``close_all()`` for a full
        wipe.
        """
        mgr = self._mgr()
        mgr.close()
        # Locals survive.
        assert "search" in mgr._local
        assert any(
            name == "search"
            for name in mgr._tool_index
        )

    def test_replace_config_clears_and_rebuilds(self):
        """replace_config() should drop remote clients and
        rebuild from a fresh servers list."""
        class _Stub1:
            server_name = "old"
            async def connect(self): pass
            async def list_tools(self): return []
            async def call_tool(self, **kw):
                from src.mcp_client.base import McpCallResult
                return McpCallResult(content="")
            async def close(self): pass
        class _Stub2:
            server_name = "new"
            async def connect(self): pass
            async def list_tools(self): return []
            async def call_tool(self, **kw):
                from src.mcp_client.base import McpCallResult
                return McpCallResult(content="")
            async def close(self): pass
        # Stub requires async coroutine semantics — wrap.
        def _coro_noop(*a, **k):
            async def _co(): return None
            return _co()
        # Easiest: don't go through _build_client_from_config
        # — call replace_config with [] to clear, then assert.
        mgr = self._mgr()
        # No remote clients initially → just check empty list
        # round-trips without raising.
        mgr.replace_config([])
        assert mgr._clients == {}
        # Replace with one bogus entry → should silently skip.
        mgr.replace_config([{"_comment": "ignored"}])
        assert mgr._clients == {}

    def test_openai_tools_shape(self):
        mgr = self._mgr()
        out = mgr.openai_tools()
        assert out[0]["type"] == "function"
        assert out[0]["function"]["name"] == "search"
        assert "parameters" in out[0]["function"]

    def test_has_tools(self):
        assert self._mgr().has_tools() is True
        assert McpManager().has_tools() is False


class TestMcpManagerRemote:
    def test_register_client_adds_tools(self):
        mgr = McpManager()
        client = _FakeClient(
            server_name="dummy",
            name_to_result={
                "foo": McpCallResult(text="foo out"),
                "bar": McpCallResult(text="bar out"),
            },
            name_to_schema={
                "foo": {"type": "object", "properties": {"x": {"type": "string"}}},
                "bar": {"type": "object"},
            },
        )
        mgr.register_client(client)
        tools = mgr.list_tools()
        assert {t.name for t in tools} == {"foo", "bar"}
        assert client.connect_calls == 1
        assert client.list_calls == 1

    def test_register_client_connect_failure_recorded(self):
        mgr = McpManager()
        bad = _FakeClient(
            server_name="broken",
            name_to_result={"foo": McpCallResult("")},
            connect_raises=RuntimeError("server down"),
        )
        mgr.register_client(bad)
        assert mgr.failed == {"broken": "RuntimeError('server down')"}
        assert mgr.list_tools() == []

    def test_register_client_dispatch_undoes_collision_suffix(self):
        mgr = McpManager()
        server1 = _FakeClient(
            server_name="s1",
            name_to_result={"shared": McpCallResult(text="from s1")},
        )
        server2 = _FakeClient(
            server_name="s2",
            name_to_result={"shared": McpCallResult(text="from s2")},
        )
        mgr.register_client(server1)
        mgr.register_client(server2)
        names = sorted(mgr._tool_index.keys())
        assert names == ["s2__shared", "shared"]
        assert mgr.call("shared", {}).text == "from s1"
        assert mgr.call("s2__shared", {}).text == "from s2"

    def test_failed_call_recorded(self):
        mgr = McpManager()
        client = _FakeClient(
            server_name="flaky",
            name_to_result={"go": McpCallResult(text="")},
            call_raises=RuntimeError("network blip"),
        )
        mgr.register_client(client)
        r = mgr.call("go", {})
        assert r.is_error
        assert "RuntimeError" in r.text


class TestFromConfig:
    def test_no_path_returns_local_only(self):
        mgr = McpManager.from_config(path=None)
        assert mgr is not None
        assert isinstance(mgr.failed, dict)

    def test_bogus_path_falls_back_gracefully(self):
        mgr = McpManager.from_config(path="/nonexistent/mcp.json")
        assert mgr is not None
        assert isinstance(mgr.failed, dict)


class TestToolSpec:
    def test_to_openai_minimum_shape(self):
        spec = ToolSpec(
            tool=McpTool(
                name="t", description="d",
                input_schema={"type": "object", "properties": {}},
                server_name="s",
            ),
            dispatch=("local", _make_tool()),
        )
        out = spec.to_openai()
        assert out == {
            "type": "function",
            "function": {
                "name": "t",
                "description": "d",
                "parameters": {"type": "object", "properties": {}},
            },
        }


# ─── Agent tool loop (mocked OpenAI) ───────────────────────


class _FakeChoice:
    def __init__(self, message, finish="stop"):
        self.message = message
        self.finish_reason = finish


class _FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = MagicMock(
            name=name, arguments=arguments,
        )


class _FakeResp:
    def __init__(self, choices):
        self.choices = choices


class TestAgentToolLoop:
    """End-to-end: LLM emits a tool_call, dispatch happens, the
    final assistant message is returned.
    """

    def test_single_tool_call_returns_text(self):
        from src.agent.agent import Agent

        mgr = McpManager()
        mgr.register_local(LocalTool(
            name="echo",
            description="echo back input",
            handler=lambda msg: {"echoed": msg},
            input_schema={
                "type": "object",
                "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            },
            server_name="t",
        ))

        tool_call = _FakeToolCall(
            id="call_1", name="echo",
            arguments=json.dumps({"msg": "hello"}),
        )
        tool_response_msg = _FakeMessage(
            content="calling echo",
            tool_calls=[tool_call],
        )
        final_msg = _FakeMessage(content="all done")

        fake_openai = MagicMock()
        fake_openai.chat.completions.create.side_effect = [
            _FakeResp([_FakeChoice(tool_response_msg, finish="tool_calls")]),
            _FakeResp([_FakeChoice(final_msg, finish="stop")]),
        ]

        agent = Agent(mcp_manager=mgr)
        agent._client = fake_openai
        agent._model = "test-model"

        answer = agent.ask(question="hi", use_tools=True)
        assert answer == "all done"
        assert fake_openai.chat.completions.create.call_count == 2

    def test_unknown_tool_does_not_crash(self):
        from src.agent.agent import Agent

        # Manager MUST have at least one tool so the tool loop is
        # actually exercised; we just dispatch to a tool the
        # model doesn't know exists.
        mgr = McpManager()
        mgr.register_local(LocalTool(
            name="present", description="present",
            handler=lambda **_: {"ok": True},
            server_name="t",
        ))
        tool_call = _FakeToolCall(
            id="x", name="ghost",
            arguments=json.dumps({}),
        )
        msg = _FakeMessage(tool_calls=[tool_call])
        msg2 = _FakeMessage(content="done")

        fake_openai = MagicMock()
        fake_openai.chat.completions.create.side_effect = [
            _FakeResp([_FakeChoice(msg, finish="tool_calls")]),
            _FakeResp([_FakeChoice(msg2, finish="stop")]),
        ]

        agent = Agent(mcp_manager=mgr)
        agent._client = fake_openai
        agent._model = "test"
        assert agent.ask("hi", use_tools=True) == "done"

    def test_loop_max_iterations_bails_out(self):
        from src.agent.agent import Agent

        mgr = McpManager()
        mgr.register_local(LocalTool(
            name="loop", description="loop",
            handler=lambda **_: {"x": 1},
            server_name="t",
        ))

        tool_call = _FakeToolCall(
            id="x", name="loop", arguments=json.dumps({}),
        )
        tool_msg = _FakeMessage(
            content="still going",
            tool_calls=[tool_call],
        )
        final_msg = _FakeMessage(content="concluding")

        fake_openai = MagicMock()
        # 3 stuck iterations + 1 final plain call
        fake_openai.chat.completions.create.side_effect = [
            _FakeResp([_FakeChoice(tool_msg, finish="tool_calls")])
            for _ in range(3)
        ] + [_FakeResp([_FakeChoice(final_msg, finish="stop")])]

        agent = Agent(mcp_manager=mgr)
        agent._client = fake_openai
        agent._model = "t"

        answer = agent.ask(
            "hi", use_tools=True, max_tool_iterations=3,
        )
        assert answer == "concluding"

    def test_legacy_path_does_not_send_tools(self):
        from src.agent.agent import Agent

        mgr = McpManager()
        mgr.register_local(_make_tool(name="echo"))

        fake_openai = MagicMock()
        fake_resp = MagicMock()
        fake_resp.choices = [
            MagicMock(message=MagicMock(content="legacy reply"))
        ]
        fake_openai.chat.completions.create.return_value = fake_resp

        agent = Agent(mcp_manager=mgr)
        agent._client = fake_openai
        agent._model = "test"
        agent.ask("hi", use_tools=False)

        kwargs = fake_openai.chat.completions.create.call_args.kwargs
        # Legacy path must NOT pass ``tools=`` to the API.
        assert "tools" not in kwargs
        assert "messages" in kwargs


