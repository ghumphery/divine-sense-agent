"""Tests for v0.9.11.1+ GUI agent prewarm.

The first chat turn after a GUI boot paid the entire lazy-build cost:
``agent.mcp_manager`` (~15 s — 11 LocalHost registrations + every
remote MCP connect in config/mcp.json) plus the provider resolution.
The fix kicks a ONE-SHOT daemon thread from the first ``get_status``
poll (the GUI front-end polls it right after boot) so the build runs
while the user is still reading the screen. ``Agent.mcp_manager`` is
double-checked-locked for the race.

v0.9.16.3 — user log 2026-09-06: in web mode TWO pages booted at once
and two get_status polls raced the UNLOCKED ``_kick_prewarm`` flag →
two prewarm threads → both raced ``_get_agent``'s check-then-set →
TWO Agent instances → ``McpManager.from_config`` ran TWICE (every MCP
server connected twice; the losing Agent's stdio subprocesses
lingered). The flag + the lazy Agent init are now guarded by one
class-level lock — the race tests below spawn real threads with a
barrier to prove exactly-once semantics.
"""
from __future__ import annotations

import threading
import time

import gui.api as gui_api
import pytest
from gui.api import Api


@pytest.fixture(autouse=True)
def _reset_flag():
    """The _prewarm_started flag is process-global — reset around
    every test so tests are order-independent."""
    Api._prewarm_started = False
    yield
    Api._prewarm_started = False


class _StubAgent:
    """Records which lazy properties the prewarm touched."""

    def __init__(self):
        self.mcp_manager_touched = False
        self.client_touched = False

    @property
    def mcp_manager(self):
        self.mcp_manager_touched = True
        return object()

    @property
    def client(self):
        self.client_touched = True
        return object()


class TestKickPrewarm:
    def test_starts_thread_exactly_once(self, monkeypatch):
        created = []

        class _FakeThread:
            def __init__(self, target=None, daemon=None, name=None):
                created.append(self)
                self.target = target
                self.daemon = daemon
                self.name = name

            def start(self):
                self.started = True

        monkeypatch.setattr(gui_api.threading, "Thread", _FakeThread)
        monkeypatch.setattr(Api, "_prewarm_started", False)

        api = Api.__new__(Api)
        api._kick_prewarm()
        api._kick_prewarm()  # second call must be a no-op

        assert len(created) == 1
        assert created[0].started is True
        assert created[0].daemon is True
        assert created[0].name == "agent-prewarm"
        assert Api._prewarm_started is True

    def test_flag_true_skips_thread(self, monkeypatch):
        monkeypatch.setattr(Api, "_prewarm_started", True)
        started = []

        class _FakeThread:
            def __init__(self, *a, **kw):
                started.append(self)

            def start(self):
                started.append(self)

        monkeypatch.setattr(gui_api.threading, "Thread", _FakeThread)
        Api.__new__(Api)._kick_prewarm()
        assert started == []


class TestPrewarmAgent:
    def test_builds_manager_and_client(self):
        api = Api.__new__(Api)
        agent = _StubAgent()
        api._get_agent = lambda: agent
        api._prewarm_agent()  # runs synchronously here
        assert agent.mcp_manager_touched
        assert agent.client_touched

    def test_failure_swallowed(self):
        api = Api.__new__(Api)

        def boom():
            raise RuntimeError("no provider configured")

        api._get_agent = boom
        api._prewarm_agent()  # must NOT raise

    def test_thread_target_is_prewarm_agent(self, monkeypatch):
        captured = {}

        class _FakeThread:
            def __init__(self, target=None, daemon=None, name=None):
                captured["target"] = target

            def start(self):
                pass

        monkeypatch.setattr(gui_api.threading, "Thread", _FakeThread)
        monkeypatch.setattr(Api, "_prewarm_started", False)
        Api.__new__(Api)._kick_prewarm()
        # the thread body must be the real builder method
        assert callable(captured["target"])
        assert captured["target"].__name__ == "_prewarm_agent"


class TestPrewarmRaceSafety:
    """v0.9.16.3 — two pages booting at once must not double-build.
    Real threads + a barrier reproduce the exact interleaving that
    produced the duplicated McpManager builds in app.log (web mode:
    desktop auto-open + an open phone tab)."""

    def test_concurrent_kick_prewarm_starts_one_thread(self, monkeypatch):
        created = []
        real_thread = threading.Thread  # capture BEFORE patching

        class _FakeThread:
            def __init__(self, target=None, daemon=None, name=None):
                created.append(self)

            def start(self):
                pass

        monkeypatch.setattr(gui_api.threading, "Thread", _FakeThread)
        Api._prewarm_started = False
        barrier = threading.Barrier(8)
        errors: list = []

        def worker():
            try:
                barrier.wait(timeout=5)
                Api.__new__(Api)._kick_prewarm()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [real_thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors, errors
        assert len(created) == 1, (
            f"{len(created)} prewarm threads spawned by 8 racing polls"
        )
        assert Api._prewarm_started is True

    def test_concurrent_get_agent_creates_one_agent(self, monkeypatch):
        monkeypatch.setattr(Api, "_agent", None)
        counter = {"n": 0}

        class _CountingAgent:
            def __init__(self):
                counter["n"] += 1
                time.sleep(0.05)  # widen the race window

        from src.agent import agent as agent_mod
        monkeypatch.setattr(agent_mod, "Agent", _CountingAgent)

        barrier = threading.Barrier(8)
        results: list = []
        errors: list = []

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(Api.__new__(Api)._get_agent())
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors, errors
        assert counter["n"] == 1, (
            f"{counter['n']} Agent instances created by 8 racing polls"
        )
        assert all(r is results[0] for r in results)
        monkeypatch.setattr(Api, "_agent", None)