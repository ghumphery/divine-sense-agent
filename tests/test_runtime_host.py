"""Tests for v0.9.43.0 — RuntimeLocalHost (runtime-local MCP tools).

The reload/restart core functions (v0.9.42.0) exposed to the LLM:
surface, cooldown, ops-callback delegation, manager wiring,
Agent._runtime_ops, prompt mention and the stdio mirror."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestSurface:
    def test_four_tools_advertised(self):
        from src.mcp_client.host import RuntimeLocalHost

        names = [t.name for t in RuntimeLocalHost().list_tools()]
        assert names == [
            "reload_config", "restart_services",
            "restart_mcp_servers", "get_runtime_status",
        ]

    def test_server_name(self):
        from src.mcp_client.host import RuntimeLocalHost

        assert RuntimeLocalHost.SERVER_NAME == "runtime-local"


class TestHandlers:
    def _host(self):
        from src.mcp_client.host import RuntimeLocalHost

        return RuntimeLocalHost()

    def test_reload_delegates_and_cooldown(self):
        host = self._host()
        calls = []

        def fake_ops(op, args=None):
            calls.append((op, args or {}))
            return {"ok": True, "changed": ["LLM_MODEL"]}

        host.attach_ops(fake_ops)
        out = host._reload({"restart_services": False,
                            "reconnect_mcp": False})
        assert out["ok"] is True
        assert calls == [("reload", {"restart_services": False,
                                     "reconnect_mcp": False})]
        # cooldown blocks the immediate second call
        out2 = host._reload({})
        assert "cooldown" in out2["error"]
        assert len(calls) == 1

    def test_reload_not_wired(self):
        host = self._host()
        out = host._reload({})
        assert "not wired" in out["error"]

    def test_restart_services_filter(self):
        host = self._host()
        host.attach_ops(lambda op, args=None: {
            "ok": True,
            "services": {"scheduler": "restarted",
                         "webhook": "skipped",
                         "telegram": "skipped",
                         "onvif_watches": "resumed 0"},
        })
        out = host._restart_services({"services": "scheduler,webhook"})
        assert set(out["services"]) == {"scheduler", "webhook"}
        out_all = host._restart_services({})
        assert len(out_all["services"]) == 4

    def test_restart_mcp_uses_manager_cb(self):
        host = self._host()
        seen = []

        def fake_reconnect(p=None):
            seen.append(p)
            return {"ok": True, "rebuilt": 2}

        host.attach_reconnect(fake_reconnect)
        out = host._restart_mcp({})
        assert out["rebuilt"] == 2
        assert seen == [None]  # default path
        host._restart_mcp({"path": "other.json"})
        assert seen[-1] == "other.json"

    def test_status(self):
        host = self._host()
        host.attach_ops(lambda op, args=None: {
            "ok": True, "provider": "openrouter",
            "model": "m1", "client_ready": True,
            "last_provider": "openrouter",
        })
        out = host._status({})
        assert out["provider"] == "openrouter"
        # unwired → graceful note
        host2 = self._host()
        note = host2._status({})
        assert note["ok"] is True and "not wired" in note["note"]

    # --- part 2 below ---


class TestWiring:
    def test_manager_registers_runtime(self):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        names = {t.name for t in mgr.list_tools()}
        assert "reload_config" in names
        assert "restart_mcp_servers" in names
        assert mgr._runtime_local_host is not None
        # reconnect wired at seed time (no Agent needed)
        assert mgr._runtime_local_host._reconnect_cb is not None
        mgr.close_all()

    def test_set_runtime_ops_callback_pushes_down(self):
        from src.mcp_client.host import RuntimeLocalHost
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        mgr._runtime_local_host = MagicMock(spec=RuntimeLocalHost)
        cb = lambda op, args=None: {}  # noqa: E731
        mgr.set_runtime_ops_callback(cb)
        mgr._runtime_local_host.attach_ops.assert_called_once_with(cb)
        assert mgr.runtime_ops_callback is cb
        mgr.close_all()

    def test_agent_runtime_ops(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a._client = None
        a._provider = "openrouter"
        a._model = "m1"
        a.last_provider = "openrouter"
        monkeypatch.setattr(
            type(a), "mcp_manager",
            property(lambda self: type(
                "M", (), {"_onvif_local_host": None},
            )()),
        )
        st = a._runtime_ops("status")
        assert st["provider"] == "openrouter"
        assert st["client_ready"] is False
        assert "unknown" in a._runtime_ops("nope")["error"]


class TestPromptAndStdio:
    def test_prompt_mentions_runtime(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        a._mcp_manager = None
        prompt = a._system_prompt(tools_enabled=True)
        assert "reload_config" in prompt
        assert "restart_services" in prompt

    def test_stdio_mirror_handlers(self):
        src = Path("src/mcp_server/runtime_server.py").read_text(
            encoding="utf-8",
        )
        for key in ("reload_config", "restart_services",
                    "restart_mcp_servers", "get_runtime_status"):
            assert f'"{key}"' in src


class TestGate:
    def test_gate_smoke_exposes_runtime(self):
        os.environ["QDRANT_RAG_HOME"] = str(
            Path(__file__).parent / "_runtime_home",
        )
        os.environ["MCP_ACTIVE_SERVERS"] = "runtime-local,meta-local"
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        exposed = {t.server_name for t in mgr.active_tools()}
        assert "runtime-local" in exposed
        assert mgr._runtime_local_host._reconnect_cb is not None
        mgr.close_all()