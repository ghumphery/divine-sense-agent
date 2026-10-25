"""Tests for v0.8.7+ schedule lifecycle MCP tools.

Covers:
  * SchedulerLocalHost._status / _runs.
  * LocalHost add forwards max_fires / expires_at.
  * Stdio SchedulerMCPServer._do_get_status / _do_list_runs.
  * Bad input handling.
  * Tool list includes the v0.8.7+ names.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    yield tmp_path


# ─── LocalHost _status ──────────────────────────────────────────


def test_localhost_status_returns_progress(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager
    from src.mcp_client.host import SchedulerLocalHost

    mgr = ScheduleManager()
    s = Schedule(
        name="sample", prompt="hello",
        schedule_type="interval_seconds", interval_seconds=60,
        max_fires=5,
    )
    s.fire_count = 1
    mgr.save(s)

    hl = SchedulerLocalHost()
    hl.attach_manager(mgr)

    out = hl._status({"task_id": s.id})
    assert out["ok"] is True
    prog = out["progress"]
    assert prog["max_fires"] == 5
    assert prog["fires_remaining"] == 4
    assert prog["schedule_type"] == "interval_seconds"
    assert "recent_runs" in prog


def test_localhost_status_resolves_by_name(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager
    from src.mcp_client.host import SchedulerLocalHost

    mgr = ScheduleManager()
    s = Schedule(name="daily", prompt="p",
                 schedule_type="interval_seconds", interval_seconds=60)
    mgr.save(s)

    hl = SchedulerLocalHost()
    hl.attach_manager(mgr)

    out = hl._status({"task_id": "daily"})
    assert out["ok"] is True
    assert out["id"] == s.id


def test_localhost_status_missing_task_id(tmp_home):
    from src.mcp_client.host import SchedulerLocalHost

    hl = SchedulerLocalHost()
    out = hl._status({})
    assert "error" in out


def test_localhost_status_unknown_id(tmp_home):
    from src.mcp_client.host import SchedulerLocalHost

    hl = SchedulerLocalHost()
    out = hl._status({"task_id": "no-such"})
    assert "error" in out


# ─── LocalHost _runs ────────────────────────────────────────────


def test_localhost_runs_returns_tail(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager
    from src.mcp_client.host import SchedulerLocalHost

    mgr = ScheduleManager()
    s = Schedule(name="hist", prompt="p",
                 schedule_type="interval_seconds", interval_seconds=60)
    mgr.save(s)
    for i in range(5):
        mgr.append_history(s.id, {
            "ts": f"2026-08-{i+1:02d}T00:00:00",
            "prompt": f"q{i}",
            "answer": f"a{i}",
            "sources": [],
            "session_id": f"x{i}",
            "error": None,
        })

    hl = SchedulerLocalHost()
    hl.attach_manager(mgr)

    out = hl._runs({"task_id": s.id, "limit": 3})
    assert out["ok"] is True
    assert out["count"] == 3
    assert out["runs"][-1]["prompt"] == "q4"


def test_localhost_runs_rejects_bad_limit(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager
    from src.mcp_client.host import SchedulerLocalHost

    mgr = ScheduleManager()
    s = Schedule(name="x", prompt="p",
                 schedule_type="interval_seconds", interval_seconds=60)
    mgr.save(s)

    hl = SchedulerLocalHost()
    hl.attach_manager(mgr)

    assert "error" in hl._runs({"task_id": s.id, "limit": 0})
    assert "error" in hl._runs({"task_id": s.id, "limit": 101})
    assert "error" in hl._runs({"task_id": s.id, "limit": "abc"})


# ─── LocalHost add forwards lifecycle caps ──────────────────────


def test_localhost_add_forwards_max_fires(tmp_home):
    from src.agent.scheduler import ScheduleManager
    from src.mcp_client.host import SchedulerLocalHost

    hl = SchedulerLocalHost()
    out = hl._add({
        "prompt": "p", "schedule_type": "interval_seconds",
        "interval_seconds": 60, "name": "x", "max_fires": 3,
    })
    assert out.get("ok") is True
    loaded = ScheduleManager().get(out["id"])
    assert loaded.max_fires == 3


def test_localhost_add_forwards_expires_at(tmp_home):
    from src.agent.scheduler import ScheduleManager
    from src.mcp_client.host import SchedulerLocalHost

    hl = SchedulerLocalHost()
    future = (datetime.now() + timedelta(days=7)).isoformat()
    out = hl._add({
        "prompt": "p", "schedule_type": "interval_seconds",
        "interval_seconds": 60, "name": "y", "expires_at": future,
    })
    assert out.get("ok") is True
    loaded = ScheduleManager().get(out["id"])
    assert loaded.expires_at == future


def test_localhost_add_rejects_bad_max_fires(tmp_home):
    from src.mcp_client.host import SchedulerLocalHost

    hl = SchedulerLocalHost()
    out = hl._add({
        "prompt": "p", "schedule_type": "interval_seconds",
        "interval_seconds": 60, "max_fires": 0,
    })
    assert "error" in out

    out = hl._add({
        "prompt": "p", "schedule_type": "interval_seconds",
        "interval_seconds": 60, "max_fires": "abc",
    })
    assert "error" in out


# ─── stdio MCP server end-to-end ────────────────────────────────


def test_stdio_get_status_end_to_end(tmp_home):
    from src.mcp_server.scheduler_server import SchedulerMCPServer

    srv = SchedulerMCPServer(schedules_root=str(tmp_home))
    add = srv._do_add({
        "prompt": "p", "schedule_type": "interval_seconds",
        "interval_seconds": 60, "name": "stdio-sample",
        "max_fires": 7,
    })
    payload = json.loads(add[0].text)
    assert payload["ok"] is True
    sid = payload["id"]

    res = srv._do_get_status({"task_id": sid})
    parsed = json.loads(res[0].text)
    assert parsed["ok"] is True
    assert parsed["progress"]["max_fires"] == 7
    assert parsed["progress"]["fires_remaining"] == 7


def test_stdio_list_runs_end_to_end(tmp_home):
    from src.mcp_server.scheduler_server import SchedulerMCPServer
    from src.agent.scheduler import ScheduleManager

    srv = SchedulerMCPServer(schedules_root=str(tmp_home))
    add = srv._do_add({
        "prompt": "p", "schedule_type": "interval_seconds",
        "interval_seconds": 60, "name": "run-sample",
    })
    payload = json.loads(add[0].text)
    sid = payload["id"]

    mgr = ScheduleManager(root=str(tmp_home))
    for i in range(3):
        mgr.append_history(sid, {
            "ts": f"2026-08-{i+1:02d}T00:00:00",
            "prompt": f"q{i}",
            "answer": f"a{i}",
            "sources": [],
            "session_id": f"x{i}",
            "error": None,
        })

    res = srv._do_list_runs({"task_id": sid, "limit": 5})
    parsed = json.loads(res[0].text)
    assert parsed["ok"] is True
    assert parsed["count"] == 3


def test_stdio_get_status_bad_id(tmp_home):
    from src.mcp_server.scheduler_server import SchedulerMCPServer

    srv = SchedulerMCPServer(schedules_root=str(tmp_home))
    res = srv._do_get_status({"task_id": "no-such"})
    # Error responses use ``_err()`` which prefixes the text with
    # ``"Error: "``; success responses are JSON. We just look for
    # the substring.
    assert res[0].text.startswith("Error:")
    assert "no schedule found" in res[0].text


# ─── Tool list registration ─────────────────────────────────────


def test_tool_specs_includes_v087_tools():
    from src.mcp_server.scheduler_server import _TOOL_SPECS

    names = {t.name for t in _TOOL_SPECS}
    assert "get_scheduled_task_status" in names
    assert "list_scheduled_task_runs" in names
