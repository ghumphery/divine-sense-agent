"""Tests for v0.9.42.0 — config hot reload + service restart.

Covers Config.reload (typed re-assign / bad-value protection),
Agent.reload_runtime (LLM chain reset + conditional daemon restarts),
McpManager.reconnect_remote_servers, ConfigWatcher (mtime polling),
the GUI reload bridge and the CLI handler."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.config as cfg_mod  # noqa: E402


@pytest.fixture()
def restore_cfg():
    """Snapshot every Config class attr; restore after the test so a
    class-level re-assignment never leaks into other test files."""
    cls = cfg_mod.Config
    snap = dict(vars(cls))
    yield
    for k, v in snap.items():
        try:
            setattr(cls, k, v)
        except (AttributeError, TypeError):
            pass


def _tmp_env(tmp_path: Path, pairs: dict) -> Path:
    f = tmp_path / ".env"
    f.write_text(
        "\n".join(f"{k}={v}" for k, v in pairs.items()) + "\n",
        encoding="utf-8",
    )
    return f


class TestConfigReload:
    def test_picks_up_env_change(self, tmp_path, restore_cfg):
        cfg = cfg_mod.config
        f = _tmp_env(tmp_path, {"LLM_MODEL": "reload-test-model"})
        changed = cfg.reload(path=f)
        assert "LLM_MODEL" in changed
        assert cfg.LLM_MODEL == "reload-test-model"

    def test_typed_converters(self, tmp_path, restore_cfg):
        cfg = cfg_mod.config
        f = _tmp_env(tmp_path, {
            "EMBEDDING_DIM": "768",
            "QDRANT_URL": "http://localhost:6333",
            "LLM_PROVIDER": "  OpenRouter ",
            "DESCRIBE_IMAGES": "on",
        })
        changed = cfg.reload(path=f)
        assert "EMBEDDING_DIM" in changed
        assert cfg.EMBEDDING_DIM == 768
        assert cfg.QDRANT_URL == "http://127.0.0.1:6333"
        assert cfg.LLM_PROVIDER == "openrouter"
        assert cfg.DESCRIBE_IMAGES is True

    def test_bad_value_kept(self, tmp_path, restore_cfg):
        cfg = cfg_mod.config
        good = cfg.EMBEDDING_DIM
        f = _tmp_env(tmp_path, {"EMBEDDING_DIM": "not-an-int"})
        changed = cfg.reload(path=f)
        assert "EMBEDDING_DIM" not in changed
        assert cfg.EMBEDDING_DIM == good  # old value survives

    def test_unset_env_kept(self, tmp_path, restore_cfg, monkeypatch):
        cfg = cfg_mod.config
        cur = cfg.LLM_MODEL
        monkeypatch.delenv("LLM_MODEL", raising=False)
        f = _tmp_env(tmp_path, {"CHUNK_SIZE": "600"})
        changed = cfg.reload(path=f)
        assert "LLM_MODEL" not in changed
        assert cfg.LLM_MODEL == cur

    def test_namespaces_still_live(self, monkeypatch):
        monkeypatch.setenv("CHUNK_SIZE", "123")
        assert cfg_mod.config.ingest.chunk_size == 123

    def test_reload_idempotent(self, tmp_path, restore_cfg):
        f = _tmp_env(tmp_path, {"LLM_MODEL": "same-model"})
        cfg_mod.config.reload(path=f)
        changed2 = cfg_mod.config.reload(path=f)
        assert "LLM_MODEL" not in changed2  # already applied


class TestAgentReloadRuntime:
    def _agent(self):
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a._client = object()
        a._provider = "stub"
        a._model = "stub-model"
        a._extra_body = {}
        a.last_provider = "stub"
        a.retriever = None
        a.web = None
        return a

    def test_resets_provider_state(self, monkeypatch):
        a = self._agent()
        monkeypatch.setattr(a, "_restart_daemon_services",
                            lambda: {"scheduler": "skipped"})
        out = a.reload_runtime(restart_services=True)
        assert out["ok"] is True
        assert a._client is None
        assert a._provider is None
        assert a._model is None
        assert a._extra_body is None
        assert a.last_provider is None

    def test_services_conditional(self, monkeypatch):
        a = self._agent()
        calls = {"stop": [], "start": []}
        a.stop_scheduler = lambda: calls["stop"].append("sch")
        a.start_scheduler = lambda: calls["start"].append("sch")
        a.stop_webhook_server = lambda: calls["stop"].append("wh")
        a.start_webhook_server = lambda: calls["start"].append("wh")
        a.stop_telegram_bot = lambda: calls["stop"].append("tg")
        a.start_telegram_bot = lambda: calls["start"].append("tg")
        # fake ScheduleManager with ONE enabled schedule
        sch = type("S", (), {"enabled": True, "status": None})()
        monkeypatch.setattr(
            "src.agent.scheduler.ScheduleManager.list",
            lambda self: [sch],
        )
        wh = type("W", (), {"enabled": True, "status": "active"})()
        monkeypatch.setattr(
            "src.agent.webhook.WebhookManager.list",
            lambda self: [wh],
        )
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
        # onvif: no local host → resumed 0 (monkeypatch the CLASS
        # property via monkeypatch so it restores after the test)
        monkeypatch.setattr(
            type(a), "mcp_manager",
            property(
                lambda self: type(
                    "M", (), {"_onvif_local_host": None},
                )(),
            ),
        )
        # neutralise the config reload itself: the REAL project .env
        # (loaded via dotenv) would otherwise re-inject the Telegram
        # token/allowlist and flip the telegram branch to "restarted".
        monkeypatch.setattr(
            cfg_mod.Config, "reload", lambda self, path=None: [],
        )
        out = a.reload_runtime()
        assert "sch" in calls["start"]            # enabled → restarted
        assert "wh" in calls["start"]             # enabled trigger
        assert "tg" not in calls["start"]         # no token/allowlist
        assert out["services"]["scheduler"].startswith("restarted")
        assert out["services"]["webhook"].startswith("restarted")
        assert out["services"]["telegram"].startswith("skipped")

    def test_no_services_flag(self, monkeypatch):
        a = self._agent()
        out = a.reload_runtime(restart_services=False)
        assert out["ok"] is True
        assert out["services"] == {}


class TestReconnectRemoteServers:
    def test_rebuilds_from_file_and_keeps_local(self, tmp_path,
                                                monkeypatch):
        import json

        from src.mcp_client.manager import McpManager

        cfg_path = tmp_path / "mcp.json"
        cfg_path.write_text(json.dumps({
            "servers": [{"name": "fake-remote", "transport": "stdio",
                         "command": ["whatever"]}],
        }), encoding="utf-8")

        mgr = McpManager.from_config(path=None,
                                     include_local_hosts=True)
        local_count = len(mgr.list_tools())

        from src.mcp_client.base import McpTool

        fake_tool = McpTool(
            name="fake_remote_tool", server_name="fake-remote",
            description="fake", input_schema={},
            call_format="openai",
        )
        fake_client = MagicMock()
        fake_client.server_name = "fake-remote"
        fake_client.list_tools = lambda: [fake_tool]
        monkeypatch.setattr(
            "src.mcp_client.manager._build_client_from_config",
            lambda entry: fake_client,
        )
        out = mgr.reconnect_remote_servers(path=cfg_path)
        assert out["ok"] is True
        assert out["rebuilt"] == 1
        assert out["failed"] == {}
        # local hosts survived the reconnect + remote tool registered
        assert len(mgr.list_tools()) == local_count + 1
        assert any(t.server_name == "fake-remote"
                   for t in mgr.list_tools())
        mgr.close_all()

    def test_missing_file_ok(self, tmp_path):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None,
                                     include_local_hosts=True)
        out = mgr.reconnect_remote_servers(
            path=tmp_path / "nope.json",
        )
        assert out["ok"] is True and out["rebuilt"] == 0
        mgr.close_all()


class TestConfigWatcher:
    def test_mtime_change_detected(self, tmp_path):
        from src.config_watch import ConfigWatcher

        f = tmp_path / "watched.txt"
        f.write_text("v1", encoding="utf-8")
        w = ConfigWatcher(lambda changed: None, paths=[f],
                          interval=1.0)
        assert w.changed_paths() == []
        old = f.stat().st_mtime_ns
        time.sleep(0.01)
        f.write_text("v2", encoding="utf-8")
        if f.stat().st_mtime_ns == old:
            os.utime(f, ns=(old + 1_000_000, old + 1_000_000))
        changed = w.changed_paths()
        assert len(changed) == 1 and changed[0].endswith("watched.txt")
        assert w.changed_paths() == []  # snapshot updated

    def test_loop_fires_callback(self, tmp_path):
        from src.config_watch import ConfigWatcher

        f = tmp_path / "watched.txt"
        f.write_text("v1", encoding="utf-8")
        fired = []
        w = ConfigWatcher(lambda changed: fired.append(changed),
                          paths=[f], interval=1.0)
        w.start()
        time.sleep(0.05)
        os.utime(f, ns=(f.stat().st_mtime_ns + 5_000_000,) * 2)
        for _ in range(20):
            time.sleep(0.3)
            if fired:
                break
        w.stop()
        assert fired, "watcher loop never fired the callback"

    def test_watcher_enabled_flag(self, monkeypatch):
        from src.config_watch import watcher_enabled

        monkeypatch.delenv("QDRANT_CONFIG_HOT_RELOAD", raising=False)
        assert watcher_enabled() is False
        monkeypatch.setenv("QDRANT_CONFIG_HOT_RELOAD", "on")
        assert watcher_enabled() is True


class TestGuiReloadBridge:
    def test_bridge_combines_agent_and_mcp(self, monkeypatch, tmp_path):
        from gui.api import Api

        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        api = Api.__new__(Api)
        fake_agent = MagicMock()
        fake_agent.reload_runtime.return_value = {
            "ok": True, "changed": ["LLM_MODEL"],
            "services": {"scheduler": "restarted"},
        }
        fake_agent.mcp_manager.reconnect_remote_servers.return_value = {
            "ok": True, "rebuilt": 2, "failed": {},
        }
        monkeypatch.setattr(api, "_get_agent", lambda: fake_agent)
        out = api.reload_runtime_config()
        assert out["status"] == "ok"
        assert out["changed"] == ["LLM_MODEL"]
        assert out["mcp"]["rebuilt"] == 2
        fake_agent.reload_runtime.assert_called_once()

    def test_bridge_error_envelope(self, monkeypatch):
        from gui.api import Api

        api = Api.__new__(Api)
        fake_agent = MagicMock()
        fake_agent.reload_runtime.return_value = {
            "ok": False, "error": "boom",
        }
        monkeypatch.setattr(api, "_get_agent", lambda: fake_agent)
        out = api.reload_runtime_config()
        assert out["status"] == "error"
        assert "boom" in out["error"]


class TestCliReload:
    def test_handler_prints_summary(self, monkeypatch, capsys):
        import cli

        fake_agent = MagicMock()
        fake_agent.reload_runtime.return_value = {
            "ok": True, "changed": ["LLM_MODEL"],
            "services": {"scheduler": "restarted (1 schedule(s))"},
        }
        fake_agent.mcp_manager.reconnect_remote_servers.return_value = {
            "ok": True, "rebuilt": 1, "failed": {},
        }
        fake_agent._mcp_manager = fake_agent.mcp_manager
        monkeypatch.setattr(
            "src.agent.agent.Agent", lambda *a, **kw: fake_agent,
        )
        cli._handle_reload(type("A", (), {"no_services": False})())
        blob = "\n".join(
            capsys.readouterr().out.splitlines(),
        )
        assert "LLM_MODEL" in blob
        assert "scheduler" in blob
        assert "rebuilt=1" in blob

    # --- part 2 below ---