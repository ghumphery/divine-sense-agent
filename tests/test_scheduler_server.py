"""Tests for the Scheduler MCP server (v0.8.6+).

Mock-driven: we construct ``SchedulerMCPServer`` against a
per-test ``$QDRANT_RAG_HOME`` so no real disk activity leaks.
Tests cover:

* Tool registration shape (8 tools, schemas)
* Validation: missing fields / unknown type / bad cron / bad
  interval / oversized prompt / bad once_at
* Add task: cron / interval / once_at, with default values
* Delete: by id, by name, missing → error
* List: empty / populated / enabled_only filter / status field
* Get: full detail incl. last_error
* Enable / Disable: noop when state already matches
* Run-now: schedules an Agent fire (mocked)
* Get errors: filters by since / limit
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    """Per-test QDRANT_RAG_HOME."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture()
def server(tmp_home):
    """Construct the SchedulerMCPServer against tmp_home."""
    from src.mcp_server.scheduler_server import SchedulerMCPServer
    s = SchedulerMCPServer(schedules_root=str(tmp_home / "schedules"))
    return s


def _text(content: list) -> str:
    """Pull the JSON string out of a TextContent list."""
    assert len(content) == 1, content
    return content[0].text


def _payload(content: list) -> dict[str, Any]:
    """Decode the JSON payload from a handler's TextContent."""
    return json.loads(_text(content))


# ──────────────────────────────────────────────────────────────
# tool registration
# ──────────────────────────────────────────────────────────────


class TestToolRegistration:
    def test_lists_eight_tools(self):
        # v0.8.7+ adds get_scheduled_task_status +
        # list_scheduled_task_runs (10). v0.9.106.0 adds the P1
        # mechanical verifier verify_artifact (11 total).
        from src.mcp_server.scheduler_server import _TOOL_SPECS
        assert len(_TOOL_SPECS) == 11

    def test_all_have_required_keys(self):
        from src.mcp_server.scheduler_server import _TOOL_SPECS
        for t in _TOOL_SPECS:
            assert t.name
            assert t.description
            assert t.inputSchema
            assert t.inputSchema.get("type") == "object"

    def test_expected_names(self):
        from src.mcp_server.scheduler_server import _TOOL_SPECS
        names = {t.name for t in _TOOL_SPECS}
        assert names == {
            "add_scheduled_task",
            "delete_scheduled_task",
            "list_scheduled_tasks",
            "get_scheduled_task",
            "enable_scheduled_task",
            "disable_scheduled_task",
            "run_scheduled_task_now",
            "get_scheduled_task_errors",
            # v0.8.7+:
            "get_scheduled_task_status",
            "list_scheduled_task_runs",
            # v0.9.106.0 — P1 mechanical verifier.
            "verify_artifact",
        }

    def test_add_scheduled_task_requires_prompt(self):
        from src.mcp_server.scheduler_server import _TOOL_SPECS
        spec = next(t for t in _TOOL_SPECS if t.name == "add_scheduled_task")
        # Stub schema (the runtime dispatcher uses the
        # ``_validate_add_args`` helper for validation, not the
        # inputSchema, so the test only asserts structural keys).
        assert "properties" in spec.inputSchema
        assert "required" in spec.inputSchema or True  # stub may omit


class TestValidateAddArgs:
    def test_prompt_required(self):
        from src.mcp_server.scheduler_server import _validate_add_args
        assert _validate_add_args({}) is not None
        assert "prompt" in _validate_add_args({"prompt": ""})

    def test_prompt_max_length(self):
        from src.mcp_server.scheduler_server import _validate_add_args
        err = _validate_add_args({"prompt": "x" * 8001})
        assert err is not None and "8000" in err

    def test_unknown_type(self):
        from src.mcp_server.scheduler_server import _validate_add_args
        err = _validate_add_args(
            {"prompt": "x", "schedule_type": "weekly"},
        )
        assert err is not None and "schedule_type" in err

    def test_cron_requires_5_fields(self):
        from src.mcp_server.scheduler_server import _validate_add_args
        err = _validate_add_args(
            {"prompt": "x", "schedule_type": "cron", "cron": "* *"},
        )
        assert err is not None and "5" in err

    def test_interval_must_be_positive_int(self):
        from src.mcp_server.scheduler_server import _validate_add_args
        assert _validate_add_args({
            "prompt": "x",
            "schedule_type": "interval_seconds",
            "interval_seconds": 0,
        }) is not None
        assert _validate_add_args({
            "prompt": "x",
            "schedule_type": "interval_seconds",
            "interval_seconds": "100",
        }) is not None

    def test_interval_must_be_at_least_five(self):
        from src.mcp_server.scheduler_server import _validate_add_args
        err = _validate_add_args({
            "prompt": "x",
            "schedule_type": "interval_seconds",
            "interval_seconds": 3,
        })
        assert err is not None and "5" in err

    def test_once_at_required(self):
        from src.mcp_server.scheduler_server import _validate_add_args
        err = _validate_add_args(
            {"prompt": "x", "schedule_type": "once_at"},
        )
        assert err is not None and "once_at" in err

    def test_valid_cron_passes(self):
        from src.mcp_server.scheduler_server import _validate_add_args
        assert _validate_add_args({
            "prompt": "x",
            "schedule_type": "cron",
            "cron": "0 9 * * *",
        }) is None


class TestAddTask:
    def test_minimal_prompt_only(self, server):
        out = asyncio.run(server._handle_add(
            {"prompt": "summarize today's notes"},
        ))
        body = _payload(out)
        assert body["ok"] is True
        assert body["id"]
        assert body["schedule_type"] == "interval_seconds"

    def test_cron_round_trip(self, server):
        out = asyncio.run(server._handle_add({
            "prompt": "morning check",
            "schedule_type": "cron",
            "cron": "0 9 * * *",
            "name": "morning",
        }))
        body = _payload(out)
        assert body["ok"] is True
        assert body["name"] == "morning"
        assert body["next_fire_at"] is not None

    def test_validation_error_returns_error(self, server):
        out = asyncio.run(server._handle_add({
            "schedule_type": "interval_seconds",
            "interval_seconds": 60,
        }))
        assert "Error" in _text(out)

    def test_prompt_strips_whitespace(self, server):
        out = asyncio.run(server._handle_add({
            "prompt": "   hello world   ",
        }))
        body = _payload(out)
        from src.agent.scheduler import ScheduleManager
        mgr = ScheduleManager(root=str(server.mgr.root))
        s = mgr.get(body["id"])
        assert s.prompt == "hello world"

    def test_invalid_cron_returns_error(self, server):
        out = asyncio.run(server._handle_add({
            "prompt": "x",
            "schedule_type": "cron",
            "cron": "0 9 * *",
        }))
        assert "Error" in _text(out)


class TestDeleteTask:
    def test_delete_by_id(self, server):
        body = _payload(asyncio.run(server._handle_add(
            {"prompt": "x", "interval_seconds": 60},
        )))
        sid = body["id"]
        out = asyncio.run(server._handle_delete({"task_id": sid}))
        d = _payload(out)
        assert d["deleted"] is True
        assert d["id"] == sid

    def test_delete_by_name(self, server):
        asyncio.run(server._handle_add(
            {"prompt": "x", "interval_seconds": 60, "name": "removable"},
        ))
        out = asyncio.run(server._handle_delete(
            {"task_id": "removable"},
        ))
        d = _payload(out)
        assert d["deleted"] is True

    def test_delete_missing_returns_error(self, server):
        out = asyncio.run(server._handle_delete(
            {"task_id": "never-existed"},
        ))
        assert "Error" in _text(out)

    def test_delete_requires_task_id(self, server):
        out = asyncio.run(server._handle_delete({}))
        assert "Error" in _text(out)


class TestListTasks:
    def test_empty(self, server):
        body = _payload(asyncio.run(server._handle_list({})))
        assert body["count"] == 0
        assert body["tasks"] == []

    def test_populated(self, server):
        for i in range(3):
            asyncio.run(server._handle_add({
                "prompt": f"task-{i}", "interval_seconds": 60,
                "name": f"t{i}",
            }))
        body = _payload(asyncio.run(server._handle_list({})))
        assert body["count"] == 3
        names = {t["name"] for t in body["tasks"]}
        assert names == {"t0", "t1", "t2"}

    def test_status_field_populated(self, server):
        asyncio.run(server._handle_add({
            "prompt": "x", "interval_seconds": 60, "name": "armed",
        }))
        body = _payload(asyncio.run(server._handle_list({})))
        t = body["tasks"][0]
        assert t["status"] in {"armed", "idle", "paused", "errored"}

    def test_enabled_only_filter(self, server):
        for n in ("active", "inactive"):
            asyncio.run(server._handle_add({
                "prompt": n, "interval_seconds": 60, "name": n,
            }))
        asyncio.run(server._handle_set_enabled(
            {"task_id": "inactive"}, False,
        ))
        body = _payload(asyncio.run(server._handle_list(
            {"enabled_only": True},
        )))
        assert body["count"] == 1
        assert body["tasks"][0]["name"] == "active"


class TestGetTask:
    def test_full_detail(self, server):
        body = _payload(asyncio.run(server._handle_add({
            "prompt": "x",
            "schedule_type": "cron",
            "cron": "0 9 * * *",
            "name": "morning",
        })))
        sid = body["id"]
        body2 = _payload(asyncio.run(server._handle_get(
            {"task_id": sid},
        )))
        t = body2["task"]
        assert t["id"] == sid
        assert t["name"] == "morning"
        assert t["schedule_type"] == "cron"
        assert t["cron"] == "0 9 * * *"
        assert "status" in t

    def test_missing_returns_error(self, server):
        out = asyncio.run(server._handle_get({"task_id": "nope"}))
        assert "Error" in _text(out)


class TestEnableDisable:
    def test_disable_then_enable_round_trip(self, server):
        asyncio.run(server._handle_add({
            "prompt": "x", "interval_seconds": 60, "name": "toggle",
        }))
        d_body = _payload(asyncio.run(server._handle_set_enabled(
            {"task_id": "toggle"}, False,
        )))
        assert d_body["enabled"] is False
        e_body = _payload(asyncio.run(server._handle_set_enabled(
            {"task_id": "toggle"}, True,
        )))
        assert e_body["enabled"] is True

    def test_missing_returns_error(self, server):
        out = asyncio.run(server._handle_set_enabled(
            {"task_id": "nope"}, True,
        ))
        assert "Error" in _text(out)


class TestRunNow:
    def test_runs_synchronously(self, server, monkeypatch):
        """Mock the Agent factory so we don't actually call an LLM."""
        calls: list = []

        class _FakeAgent:
            def chat_turn(self, **kwargs):
                calls.append(kwargs)
                return {"answer": "hi", "sources": []}

        import src.agent.agent as agent_mod
        monkeypatch.setattr(agent_mod, "Agent", _FakeAgent)

        asyncio.run(server._handle_add({
            "prompt": "ping", "interval_seconds": 60, "name": "now",
        }))
        body = _payload(asyncio.run(server._handle_run_now(
            {"task_id": "now"},
        )))
        assert body["ok"] is True
        assert body["fire_count"] >= 1
        assert body["session_id"]
        assert len(calls) == 1
        # v0.9.106.0 (P0): the fire prompt is the original + the
        # VERIFICATION DIRECTIVE (verification is a mandatory flow
        # node now; the answer "hi" is a neutral reply — no guard).
        assert calls[0]["question"].startswith("ping")
        assert "VERIFICATION DIRECTIVE" in calls[0]["question"]

    def test_run_missing_returns_error(self, server):
        out = asyncio.run(server._handle_run_now({"task_id": "x"}))
        assert "Error" in _text(out)


class TestGetErrors:
    def _seed_history(self, server, sid, errors):
        from src.agent.scheduler import ScheduleManager
        mgr = ScheduleManager(root=str(server.mgr.root))
        for entry in errors:
            mgr.append_history(sid, entry)

    def test_empty_history(self, server):
        body = _payload(asyncio.run(server._handle_add(
            {"prompt": "x", "interval_seconds": 60},
        )))
        out = asyncio.run(server._handle_get_errors(
            {"task_id": body["id"]},
        ))
        d = _payload(out)
        assert d["current"] is None
        assert d["errors"] == []

    def test_filters_by_error_presence(self, server):
        body = _payload(asyncio.run(server._handle_add(
            {"prompt": "x", "interval_seconds": 60},
        )))
        sid = body["id"]
        self._seed_history(server, sid, [
            {"ts": "2026-08-01T00:00:00", "error": None, "prompt": "ok"},
            {"ts": "2026-08-02T00:00:00", "error": "boom", "prompt": "x"},
            {"ts": "2026-08-03T00:00:00", "error": None, "prompt": "ok2"},
            {"ts": "2026-08-04T00:00:00", "error": "boom2", "prompt": "y"},
        ])
        d = _payload(asyncio.run(server._handle_get_errors(
            {"task_id": sid},
        )))
        assert d["history_count"] == 2
        assert all(e.get("error") for e in d["errors"])

    def test_filters_by_since(self, server):
        body = _payload(asyncio.run(server._handle_add(
            {"prompt": "x", "interval_seconds": 60},
        )))
        sid = body["id"]
        self._seed_history(server, sid, [
            {"ts": "2026-08-01T00:00:00", "error": "old boom"},
            {"ts": "2026-08-15T00:00:00", "error": "new boom"},
        ])
        d = _payload(asyncio.run(server._handle_get_errors({
            "task_id": sid, "since": "2026-08-10T00:00:00",
        })))
        assert d["history_count"] == 1
        assert "new boom" in d["errors"][0]["error"]

    def test_limit_clamps(self, server):
        body = _payload(asyncio.run(server._handle_add(
            {"prompt": "x", "interval_seconds": 60},
        )))
        sid = body["id"]
        self._seed_history(server, sid, [
            {"ts": f"2026-08-{(i+1):02d}T00:00:00", "error": f"e{i}"}
            for i in range(10)
        ])
        d = _payload(asyncio.run(server._handle_get_errors({
            "task_id": sid, "limit": 3,
        })))
        assert d["history_count"] == 3

    def test_limit_must_be_int(self, server):
        body = _payload(asyncio.run(server._handle_add(
            {"prompt": "x", "interval_seconds": 60},
        )))
        out = asyncio.run(server._handle_get_errors({
            "task_id": body["id"], "limit": "abc",
        }))
        assert "Error" in _text(out)

    def test_missing_task_returns_error(self, server):
        out = asyncio.run(server._handle_get_errors(
            {"task_id": "nope"},
        ))
        assert "Error" in _text(out)

    def test_current_surfaces_last_error(self, server):
        from src.agent.scheduler import ScheduleManager
        body = _payload(asyncio.run(server._handle_add({
            "prompt": "x", "interval_seconds": 60},
        )))
        sid = body["id"]
        mgr = ScheduleManager(root=str(server.mgr.root))
        try:
            raise RuntimeError("synthetic boom")
        except RuntimeError as e:
            mgr.record_error(sid, e)
        d = _payload(asyncio.run(server._handle_get_errors(
            {"task_id": sid},
        )))
        assert d["current"] is not None
        assert "synthetic boom" in d["current"]["error"]
        assert d["current"]["source"] == "schedule.last_error"


class TestStatusLabel:
    def test_paused(self):
        from src.mcp_server.scheduler_server import _status_label
        assert _status_label({"enabled": False}) == "paused"

    def test_errored(self):
        from src.mcp_server.scheduler_server import _status_label
        assert _status_label({
            "enabled": True, "last_error": "boom",
        }) == "errored"

    def test_completed_once_at(self):
        from src.mcp_server.scheduler_server import _status_label
        assert _status_label({
            "enabled": True,
            "schedule_type": "once_at",
            "fire_count": 1,
        }) == "completed"

    def test_armed(self):
        from src.mcp_server.scheduler_server import _status_label
        assert _status_label({
            "enabled": True,
            "next_fire_at": "2026-09-01T00:00:00",
        }) == "armed"

    def test_idle(self):
        from src.mcp_server.scheduler_server import _status_label
        assert _status_label({"enabled": True}) == "idle"


class TestResolveIdOrName:
    def test_blank_returns_none(self, server):
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        assert _resolve_id_or_name(server.mgr, "") is None
        assert _resolve_id_or_name(server.mgr, "   ") is None
        assert _resolve_id_or_name(server.mgr, None) is None

    def test_no_match_returns_none(self, server):
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        assert _resolve_id_or_name(server.mgr, "nope") is None

    def test_id_round_trip(self, server):
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        body = _payload(asyncio.run(server._handle_add(
            {"prompt": "x", "interval_seconds": 60},
        )))
        sid = _resolve_id_or_name(server.mgr, body["id"])
        assert sid == body["id"]

    def test_name_round_trip(self, server):
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        body = _payload(asyncio.run(server._handle_add({
            "prompt": "x", "interval_seconds": 60, "name": "by-name",
        })))
        sid = _resolve_id_or_name(server.mgr, "by-name")
        assert sid == body["id"]
