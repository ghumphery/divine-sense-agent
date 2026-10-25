"""v0.9.45.0 — Phase-1 parallel-multi-task safety net.

Regression coverage for the session-based parallel multi-task plan:

* ``src/agent/turn_gate.py`` — the QDRANT_MAX_CONCURRENT_TURNS /
  QDRANT_TURN_WAIT_SECONDS resolvers + the re-entrant per-thread
  ``turn_slot()`` gate (unlimited = zero behaviour change; capped =
  serialize; nested same-thread turns reuse the slot so a mid-turn
  ``run_scheduled_task_now`` cannot deadlock).
* ``Agent.ask`` / ``Agent.chat_turn`` run inside the gate; public
  signatures unchanged.
* ``StdioMcpClient`` / ``HttpSseMcpClient`` serialize
  connect/list_tools/call_tool/close on a per-client RLock — the
  shared asyncio event loop's ``run_until_complete`` is not
  thread-safe and parallel turn sources share one McpManager.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fresh_gate(monkeypatch):
    """Every test starts with the env vars unset and a rebuilt gate —
    the singleton is process-wide, so tests would otherwise leak."""
    from src.agent import turn_gate as tg

    monkeypatch.delenv("QDRANT_MAX_CONCURRENT_TURNS", raising=False)
    monkeypatch.delenv("QDRANT_TURN_WAIT_SECONDS", raising=False)
    tg.reset_turn_gate()
    yield
    tg.reset_turn_gate()


# ─── resolvers ───────────────────────────────────────────────


class TestResolvers:
    def test_max_turns_unset_is_unlimited(self):
        from src.agent.turn_gate import resolve_max_concurrent_turns

        assert resolve_max_concurrent_turns() is None

    def test_max_turns_valid(self, monkeypatch):
        from src.agent.turn_gate import resolve_max_concurrent_turns

        monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", "3")
        assert resolve_max_concurrent_turns() == 3

    def test_max_turns_whitespace_stripped(self, monkeypatch):
        from src.agent.turn_gate import resolve_max_concurrent_turns

        monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", " 2 ")
        assert resolve_max_concurrent_turns() == 2

    def test_max_turns_invalid_falls_back_to_unlimited(
        self, monkeypatch, caplog,
    ):
        from src.agent.turn_gate import resolve_max_concurrent_turns

        monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", "abc")
        with caplog.at_level("WARNING"):
            assert resolve_max_concurrent_turns() is None
        assert "QDRANT_MAX_CONCURRENT_TURNS" in caplog.text

    def test_max_turns_non_positive_falls_back(self, monkeypatch):
        from src.agent.turn_gate import resolve_max_concurrent_turns

        for bad in ("0", "-4"):
            monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", bad)
            assert resolve_max_concurrent_turns() is None

    def test_wait_default_120(self):
        from src.agent.turn_gate import (
            DEFAULT_TURN_WAIT_SECONDS,
            resolve_turn_wait_seconds,
        )

        assert DEFAULT_TURN_WAIT_SECONDS == 120.0
        assert resolve_turn_wait_seconds() == 120.0

    def test_wait_valid(self, monkeypatch):
        from src.agent.turn_gate import resolve_turn_wait_seconds

        monkeypatch.setenv("QDRANT_TURN_WAIT_SECONDS", "0.25")
        assert resolve_turn_wait_seconds() == 0.25

    def test_wait_invalid_falls_back(self, monkeypatch, caplog):
        from src.agent.turn_gate import resolve_turn_wait_seconds

        monkeypatch.setenv("QDRANT_TURN_WAIT_SECONDS", "soon")
        with caplog.at_level("WARNING"):
            assert resolve_turn_wait_seconds() == 120.0
        assert "QDRANT_TURN_WAIT_SECONDS" in caplog.text

    def test_wait_non_positive_falls_back(self, monkeypatch):
        from src.agent.turn_gate import resolve_turn_wait_seconds

        monkeypatch.setenv("QDRANT_TURN_WAIT_SECONDS", "-1")
        assert resolve_turn_wait_seconds() == 120.0


# ─── turn_slot: unlimited + nesting ──────────────────────────


class TestUnlimitedSlot:
    def test_unlimited_never_blocks_or_raises(self):
        from src.agent.turn_gate import held_turns, turn_slot

        with turn_slot():
            assert held_turns() == 0  # unlimited keeps no counter
            with turn_slot():  # nested — pure depth bookkeeping
                pass

    def test_reset_rebuilds_gate(self, monkeypatch):
        from src.agent import turn_gate as tg

        monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", "2")
        tg.reset_turn_gate()
        with tg.turn_slot():
            assert tg.held_turns() == 1


# ─── turn_slot: capped behaviour ─────────────────────────────


class TestCappedSlot:
    def _capped(self, monkeypatch, max_turns: int, wait: float):
        from src.agent import turn_gate as tg

        monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", str(max_turns))
        monkeypatch.setenv("QDRANT_TURN_WAIT_SECONDS", str(wait))
        tg.reset_turn_gate()
        return tg

    def test_busy_raises_after_wait_budget(self, monkeypatch):
        from src.agent.turn_gate import TurnGateBusy

        tg = self._capped(monkeypatch, 1, 0.1)
        entered = threading.Event()
        release = threading.Event()

        def holder():
            with tg.turn_slot():
                entered.set()
                release.wait(2)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert entered.wait(2)
            with pytest.raises(TurnGateBusy) as exc:
                with tg.turn_slot():
                    pass
            # The error names both env knobs (actionable message).
            assert "QDRANT_MAX_CONCURRENT_TURNS" in str(exc.value)
            assert "QDRANT_TURN_WAIT_SECONDS" in str(exc.value)
        finally:
            release.set()
            t.join(2)

    def test_slot_freed_after_release(self, monkeypatch):
        from src.agent.turn_gate import TurnGateBusy

        tg = self._capped(monkeypatch, 1, 0.1)
        release = threading.Event()
        box: dict = {}

        def holder():
            with tg.turn_slot():
                release.wait(2)
            box["done"] = True

        t = threading.Thread(target=holder)
        t.start()
        release.set()
        t.join(2)
        assert box.get("done")
        # The holder released — a new slot must be acquirable now.
        with tg.turn_slot():
            pass

    def test_concurrent_threads_respect_cap(self, monkeypatch):
        tg = self._capped(monkeypatch, 2, 5.0)
        lock = threading.Lock()
        state = {"n": 0, "max": 0}
        errors: list = []

        def worker():
            try:
                with tg.turn_slot():
                    with lock:
                        state["n"] += 1
                        state["max"] = max(state["max"], state["n"])
                    time.sleep(0.05)
                    with lock:
                        state["n"] -= 1
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert not errors
        assert state["max"] <= 2

    def test_nested_same_thread_does_not_deadlock_or_raise(
        self, monkeypatch,
    ):
        """max=1 + nested turn (SchedulerLocalHost run-now pattern)."""
        tg = self._capped(monkeypatch, 1, 0.1)
        with tg.turn_slot():
            with tg.turn_slot():  # must reuse the slot, not raise
                with tg.turn_slot():
                    pass
        assert tg.held_turns() == 0
        # The slot was fully released (BoundedSemaphore sanity) — a
        # fresh slot acquires cleanly after the nested stack unwinds.
        with tg.turn_slot():
            pass


# ─── Agent wiring: ask / chat_turn run inside the gate ───────


class TestAgentTurnGateWiring:
    def _agent(self):
        from src.agent.agent import Agent

        # No __init__: the gate wrapper touches no instance state and
        # the impl below is monkeypatched — this isolates the wiring.
        return Agent.__new__(Agent)

    def test_ask_runs_inside_gate(self, monkeypatch):
        monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", "1")
        monkeypatch.setenv("QDRANT_TURN_WAIT_SECONDS", "5")
        from src.agent import turn_gate as tg
        from src.agent.agent import Agent

        tg.reset_turn_gate()
        a = self._agent()
        lock = threading.Lock()
        state = {"n": 0, "max": 0}

        def impl(*a_, **kw):
            with lock:
                state["n"] += 1
                state["max"] = max(state["max"], state["n"])
            time.sleep(0.05)
            with lock:
                state["n"] -= 1
            return "ok"

        monkeypatch.setattr(Agent, "_ask_impl", impl)
        threads = [
            threading.Thread(target=a.ask, args=("q",)) for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert state["max"] == 1  # serialized by the cap

    def test_chat_turn_runs_inside_gate(self, monkeypatch):
        monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", "2")
        monkeypatch.setenv("QDRANT_TURN_WAIT_SECONDS", "5")
        from src.agent import turn_gate as tg
        from src.agent.agent import Agent

        tg.reset_turn_gate()
        a = self._agent()
        lock = threading.Lock()
        state = {"n": 0, "max": 0}

        def impl(*a_, **kw):
            with lock:
                state["n"] += 1
                state["max"] = max(state["max"], state["n"])
            time.sleep(0.05)
            with lock:
                state["n"] -= 1
            return ("ok", "", [])

        monkeypatch.setattr(Agent, "_chat_turn_impl", impl)
        threads = [
            threading.Thread(target=a.chat_turn, args=("q",))
            for _ in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert 1 <= state["max"] <= 2  # capped, never over

    def test_nested_chat_turn_reuses_slot_no_deadlock(self, monkeypatch):
        """A mid-turn run_scheduled_task_now opens another chat_turn
        on the SAME thread — with max=1 the re-entrant slot must let
        it through (the historical deadlock shape)."""
        monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", "1")
        monkeypatch.setenv("QDRANT_TURN_WAIT_SECONDS", "0.2")
        from src.agent import turn_gate as tg
        from src.agent.agent import Agent

        tg.reset_turn_gate()
        a = self._agent()
        calls: list = []
        depth = {"n": 0}

        def impl(*a_, **kw):
            depth["n"] += 1
            if depth["n"] == 1:
                calls.append("outer")
                # The nested same-thread turn — would deadlock without
                # the re-entrant slot.
                return a.chat_turn("nested", use_tools=False)
            calls.append("inner")
            return ("inner-ok", "", [])

        monkeypatch.setattr(Agent, "_chat_turn_impl", impl)
        box: dict = {}
        t = threading.Thread(
            target=lambda: box.update(
                result=a.chat_turn("outer", use_tools=False)
            ),
        )
        t.start()
        t.join(10)  # a deadlock would blow this join
        assert t.is_alive() is False
        assert calls == ["outer", "inner"]
        assert box["result"][0] == "inner-ok"
        assert tg.held_turns() == 0

    def test_busy_error_surfaces_from_chat_turn(self, monkeypatch):
        from src.agent.turn_gate import TurnGateBusy

        monkeypatch.setenv("QDRANT_MAX_CONCURRENT_TURNS", "1")
        monkeypatch.setenv("QDRANT_TURN_WAIT_SECONDS", "0.1")
        from src.agent import turn_gate as tg
        from src.agent.agent import Agent

        tg.reset_turn_gate()
        a = self._agent()
        entered = threading.Event()
        release = threading.Event()

        def blocking_impl(*a_, **kw):
            entered.set()
            release.wait(2)
            return ("ok", "", [])

        monkeypatch.setattr(Agent, "_chat_turn_impl", blocking_impl)
        t = threading.Thread(
            target=lambda: a.chat_turn("busy", use_tools=False),
        )
        t.start()
        try:
            assert entered.wait(2)
            with pytest.raises(TurnGateBusy):
                a.ask("queued", use_tools=False)
        finally:
            release.set()
            t.join(2)


# ─── MCP client loop-serialization locks ─────────────────────


class _LoopState:
    def __init__(self):
        self.lock = threading.Lock()
        self.n = 0
        self.max = 0


class _FakeLoop:
    """Stands in for the client's asyncio loop: ``run_until_complete``
    records the TRUE concurrency of callers."""

    def __init__(self, state: _LoopState, delay: float = 0.02):
        self.state = state
        self.delay = delay

    def run_until_complete(self, coro):
        coro.close()  # never awaited by the fake
        with self.state.lock:
            self.state.n += 1
            self.state.max = max(self.state.max, self.state.n)
        time.sleep(self.delay)
        with self.state.lock:
            self.state.n -= 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            isError=False,
        )


class _FakeSession:
    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        return "ignored-by-fake-loop"

    async def list_tools(self):
        return "ignored-by-fake-loop"


def _swallow(fn):
    """Run ``fn`` and return a short error tag instead of raising —
    exceptions from worker threads must not kill the join loop."""
    try:
        fn()
        return None
    except Exception as e:  # pragma: no cover
        return repr(e)


class TestMcpClientSerialization:
    def _wired(self, client):
        state = _LoopState()
        client._connected = True
        client._loop = _FakeLoop(state)
        client._session = _FakeSession()
        return state

    def _run_parallel(self, client, fn, workers=5):
        errors: list = []

        def worker():
            err = _swallow(fn)
            if err:
                errors.append(err)

        threads = [
            threading.Thread(target=worker) for _ in range(workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        return errors

    def test_stdio_client_has_rlock(self):
        from src.mcp_client.stdio import StdioMcpClient

        c = StdioMcpClient("t", ["cmd"])
        assert isinstance(c._call_lock, type(threading.RLock()))

    def test_stdio_call_tool_serialized(self):
        from src.mcp_client.stdio import StdioMcpClient

        c = StdioMcpClient("t", ["cmd"])
        state = self._wired(c)
        errors = self._run_parallel(c, lambda: c.call_tool("tool", {}))
        assert not errors
        assert state.max == 1  # the RLock serialized the loop access

    def test_stdio_close_reentrant_under_lock(self):
        """connect() failure calls close() while the lock is held —
        the RLock must allow the same-thread re-entry (deadlock probe)."""
        from src.mcp_client.stdio import StdioMcpClient

        c = StdioMcpClient("t", ["cmd"])
        c._connected = False
        done: list = []
        with c._call_lock:
            c.close()
            done.append("closed")
        assert done == ["closed"]

    def test_http_client_call_tool_serialized(self):
        from src.mcp_client.http_sse import HttpSseMcpClient

        c = HttpSseMcpClient("t", "https://example.invalid/mcp")
        state = self._wired(c)
        errors = self._run_parallel(c, lambda: c.call_tool("tool", {}))
        assert not errors
        assert state.max == 1