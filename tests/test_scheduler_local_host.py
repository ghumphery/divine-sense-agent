"""Tests for SchedulerLocalHost (v0.8.6+).

Mirrors the stdio MCP server surface but runs in-process. We
verify:

* The 8 LocalTool entries are well-formed and routed to the
  right handler methods.
* ``attach_manager`` accepts ScheduleManager / path / McpManager.
* Handlers delegate to a shared ``ScheduleManager`` (state
  persists across calls).
* Round-trip CRUD works through the same payload shapes the
  stdio server uses.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture()
def host(tmp_home):
    """SchedulerLocalHost wired to a fresh in-tmpdir manager."""
    from src.mcp_client.host import SchedulerLocalHost
    h = SchedulerLocalHost()
    h.attach_manager(tmp_home)
    return h


# ──────────────────────────────────────────────────────────────
# Tool registration
# ──────────────────────────────────────────────────────────────


class TestToolRegistration:
    def test_ten_tools(self, host):
        """v0.8.6 = 8 tools (CRUD + lifecycle + run-now + errors).
        v0.8.7 added ``get_scheduled_task_status`` and
        ``list_scheduled_task_runs`` (10). v0.9.106.0 added the P1
        mechanical verifier ``verify_artifact`` -> 11 tools total."""
        tools = host.list_tools()
        assert len(tools) == 11

    def test_all_have_handlers(self, host):
        for t in host.list_tools():
            assert callable(t._handler), t.name
            assert t.server_name == "scheduler-local"

    def test_expected_names(self, host):
        names = {t.name for t in host.list_tools()}
        assert names == {
            "add_scheduled_task",
            "delete_scheduled_task",
            "list_scheduled_tasks",
            "get_scheduled_task",
            "enable_scheduled_task",
            "disable_scheduled_task",
            "run_scheduled_task_now",
            "get_scheduled_task_errors",
            # v0.8.7 additions — lifecycle status + per-run history.
            "get_scheduled_task_status",
            "list_scheduled_task_runs",
            # v0.9.106.0 — P1 機械式檢查（獨立評估器）.
            "verify_artifact",
        }

    def test_descriptions_are_substantial(self, host):
        """Tool descriptions drive LLM tool selection — keep
        them informative (>100 chars each)."""
        for t in host.list_tools():
            assert len(t.description) > 100, (
                f"{t.name} description too short: "
                f"{len(t.description)} chars"
            )


class TestAttachManager:
    def test_accepts_path(self, tmp_home):
        from src.mcp_client.host import SchedulerLocalHost
        h = SchedulerLocalHost()
        h.attach_manager(tmp_home)
        assert h._mgr is not None
        assert h._mgr.root.exists()

    def test_accepts_string_path(self, tmp_home):
        from src.mcp_client.host import SchedulerLocalHost
        h = SchedulerLocalHost()
        h.attach_manager(str(tmp_home))
        assert h._mgr is not None

    def test_accepts_schedule_manager(self, tmp_home):
        from src.agent.scheduler import ScheduleManager
        from src.mcp_client.host import SchedulerLocalHost
        existing = ScheduleManager(root=tmp_home / "schedules")
        h = SchedulerLocalHost()
        h.attach_manager(existing)
        assert h._mgr is existing

    def test_invalid_argument_falls_back_to_default(self):
        """Anything that isn't a path or ScheduleManager gets
        a default-constructed manager (graceful degradation)."""
        from src.mcp_client.host import SchedulerLocalHost
        h = SchedulerLocalHost()
        h.attach_manager(42)
        assert h._mgr is not None


# ──────────────────────────────────────────────────────────────
# CRUD round-trip
# ──────────────────────────────────────────────────────────────


class TestCRUD:
    def test_add_then_get(self, host):
        body = host._add({
            "prompt": "morning check",
            "schedule_type": "cron",
            "cron": "0 9 * * *",
            "name": "morning",
        })
        assert body["ok"] is True
        sid = body["id"]
        body2 = host._get({"task_id": sid})
        assert body2["task"]["name"] == "morning"
        assert body2["task"]["cron"] == "0 9 * * *"

    def test_add_then_list(self, host):
        for i in range(3):
            host._add({"prompt": f"t{i}", "interval_seconds": 60})
        body = host._list({})
        assert body["count"] == 3

    def test_delete_removes(self, host):
        body = host._add({"prompt": "x", "interval_seconds": 60})
        out = host._delete({"task_id": body["id"]})
        assert out["deleted"] is True
        body2 = host._list({})
        assert body2["count"] == 0

    def test_enable_disable_round_trip(self, host):
        host._add({"prompt": "x", "interval_seconds": 60, "name": "x"})
        d = host._disable({"task_id": "x"})
        assert d["enabled"] is False
        e = host._enable({"task_id": "x"})
        assert e["enabled"] is True

    def test_list_with_enabled_only(self, host):
        host._add({"prompt": "a", "interval_seconds": 60, "name": "a"})
        host._add({"prompt": "b", "interval_seconds": 60, "name": "b"})
        host._disable({"task_id": "b"})
        out = host._list({"enabled_only": True})
        assert out["count"] == 1
        assert out["tasks"][0]["name"] == "a"

    def test_get_errors_empty(self, host):
        body = host._add({"prompt": "x", "interval_seconds": 60})
        out = host._errors({"task_id": body["id"]})
        assert out["errors"] == []
        assert out["current"] is None


# ──────────────────────────────────────────────────────────────
# Integration with McpManager.from_config
# ──────────────────────────────────────────────────────────────


class TestManagerIntegration:
    def test_registered_via_from_config(self, tmp_home):
        """End-to-end: from_config seeds SchedulerLocalHost."""
        from src.mcp_client import McpManager
        mgr = McpManager.from_config(
            path=None,
            include_local_hosts=True,
        )
        names = {t.name for t in mgr.list_tools()}
        for required in {
            "add_scheduled_task",
            "list_scheduled_tasks",
            "get_scheduled_task_errors",
        }:
            assert required in names, (
                f"{required} missing from from_config catalog"
            )
