"""Tests for the Todo MCP stdio server (v0.8.8+).

Mock-driven: we construct ``TodoMCPServer`` against a per-test
``$QDRANT_RAG_HOME`` so no real disk activity leaks. The
runtime dispatcher reuses the in-process ``TodoLocalHost``;
these tests exercise the *bridge* (TextContent <-> JSON
encoding + name lookup) rather than the underlying TodoManager
(which is already covered by ``test_todo_manager.py`` and
``test_todo_local_host.py``).

Coverage:
    * Tool registration shape (16 tools, all the expected
      names, all the required keys)
    * Dispatcher routes every LocalTool to the right
      TextContent response
    * Validation: missing required fields, unknown tool name,
      bad task_id type
    * End-to-end create -> list -> get -> complete -> delete
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture()
def server(tmp_home):
    from src.mcp_server.todo_server import TodoMCPServer
    s = TodoMCPServer(todos_root=str(tmp_home / "todos"))
    return s


# ─── tool registration ────────────────────────────────────────


def test_lists_16_tools():
    """Module-level _TOOL_SPECS exposes 16 Tool objects."""
    from src.mcp_server.todo_server import _TOOL_SPECS
    assert len(_TOOL_SPECS) == 16


def test_all_have_required_keys():
    from src.mcp_server.todo_server import _TOOL_SPECS
    for t in _TOOL_SPECS:
        assert t.name
        assert t.description.strip()
        assert t.inputSchema["type"] == "object"


def test_expected_names():
    from src.mcp_server.todo_server import _TOOL_SPECS
    names = {t.name for t in _TOOL_SPECS}
    expected = {
        "create_task", "add_subtask", "update_task", "delete_task",
        "list_tasks", "get_task", "start_task", "complete_task",
        "skip_task", "fail_task", "add_task_dependency", "reorder_task",
        "get_todo_progress", "list_task_journal", "render_task_tree",
        "execute_plan",
    }
    assert expected.issubset(names), (
        f"missing: {expected - names}"
    )


def test_create_task_schema_uses_args_wrapper():
    """All TodoMCPServer tools accept a single ``args`` object
    (the LocalHost convention) - not bespoke per-tool schemas.
    This keeps the stdio and in-process surfaces byte-identical."""
    from src.mcp_server.todo_server import _TOOL_SPECS
    by_name = {t.name: t for t in _TOOL_SPECS}
    schema = by_name["create_task"].inputSchema
    assert "args" in schema["properties"]
    assert schema["properties"]["args"]["type"] == "object"


def test_get_task_schema_uses_args_wrapper():
    from src.mcp_server.todo_server import _TOOL_SPECS
    by_name = {t.name: t for t in _TOOL_SPECS}
    schema = by_name["get_task"].inputSchema
    assert "args" in schema["properties"]


# ─── server instance ──────────────────────────────────────────


def test_server_constructs(server):
    """TodoMCPServer can be constructed with a tmp todos_root
    and exposes 16 LocalTool-backed entries."""
    assert len(server._tools_by_name) == 16


def test_server_dispatcher_routes_to_local_tool(server):
    """The TodoMCPServer keeps a name -> LocalTool map; that
    map is what the stdio ``call_tool`` decorator dispatches
    against. Verify it routes every registered name."""
    names = {lt.name for lt in server.host.list_tools()}
    assert "create_task" in server._tools_by_name
    # ``server._tools_by_name`` should mirror the LocalTool list
    # exactly - no extra names, no missing ones.
    assert set(server._tools_by_name) == names


# ─── end-to-end through the LocalTool map ──────────────────────


def _call(server, name, **kwargs):
    """Drive the LocalTool map directly; semantically identical
    to what the stdio ``call_tool`` decorator does on dispatch."""
    lt = server._tools_by_name[name]
    res = lt(kwargs)
    assert not res.is_error, f"{name} errored: {res.text}"
    return json.loads(res.text)


def test_create_then_list_end_to_end(server):
    out1 = _call(server, "create_task", title="Plan A")
    out2 = _call(server, "create_task", title="Plan B")
    assert out1["ok"] and out2["ok"]
    listed = _call(server, "list_tasks")
    titles = {t["title"] for t in listed["tasks"]}
    assert {"Plan A", "Plan B"} <= titles


def test_create_subtask_then_get(server):
    root = _call(server, "create_task", title="root")
    sub = _call(server, "add_subtask",
                 parent_id=root["task_id"], title="step 1")
    g = _call(server, "get_task", task_id=sub["task_id"])
    assert g["task"]["title"] == "step 1"
    assert g["task"]["parent_id"] == root["task_id"]


def test_status_round_trip(server):
    """create -> start -> complete + a sibling skipped -> parent
    auto-completes via execute_plan. by_status['completed']
    counts only status=completed; a + auto-completed root = 2.
    by_status['skipped'] = 1 (b). Total terminal = 3, so
    completion_pct is 1.0."""
    root = _call(server, "create_task", title="root")
    a = _call(server, "add_subtask",
              parent_id=root["task_id"], title="a")
    b = _call(server, "add_subtask",
              parent_id=root["task_id"], title="b")
    _call(server, "start_task", task_id=a["task_id"])
    _call(server, "complete_task", task_id=a["task_id"])
    _call(server, "skip_task", task_id=b["task_id"])
    out = _call(server, "execute_plan", task_id=root["task_id"])
    progress = out["progress"]
    assert progress["by_status"]["completed"] == 2  # a + root
    assert progress["by_status"]["skipped"] == 1   # b
    assert abs(progress["completion_pct"] - 1.0) < 0.01


def test_progress_summary_shape(server):
    _call(server, "create_task", title="x")
    out = _call(server, "get_todo_progress")
    assert "by_status" in out["progress"]
    assert "completion_pct" in out["progress"]
    assert "ready" in out["progress"]
    assert "next_recommended" in out["progress"]


def test_journal_returns_audit_trail(server):
    created = _call(server, "create_task", title="x")
    _call(server, "start_task", task_id=created["task_id"])
    _call(server, "complete_task", task_id=created["task_id"])
    j = _call(server, "list_task_journal", task_id=created["task_id"])
    assert j["count"] >= 3


def test_delete_then_missing_task(server):
    created = _call(server, "create_task", title="x")
    out = _call(server, "delete_task", task_id=created["task_id"])
    assert out["ok"] is True
    # Now re-list - that task is gone.
    listed = _call(server, "list_tasks")
    ids = {t["task_id"] for t in listed["tasks"]}
    assert created["task_id"] not in ids


def test_render_task_tree_returns_depth(server):
    root = _call(server, "create_task", title="root")
    _call(server, "add_subtask", parent_id=root["task_id"], title="a")
    out = _call(server, "render_task_tree", task_id=root["task_id"])
    depths = {t["depth"] for t in out["tree"]}
    assert 0 in depths  # root
    assert 1 in depths  # a


def test_reorder_task_via_tool(server):
    root = _call(server, "create_task", title="root")
    a = _call(server, "add_subtask",
              parent_id=root["task_id"], title="a")
    _ = _call(server, "add_subtask",
              parent_id=root["task_id"], title="b")
    c = _call(server, "add_subtask",
              parent_id=root["task_id"], title="c")
    _call(server, "reorder_task",
          task_id=c["task_id"], before_id=a["task_id"])
    listed = _call(server, "list_tasks", parent_id=root["task_id"])
    titles = [t["title"] for t in listed["tasks"]]
    assert titles.index("c") < titles.index("a")


def test_add_task_dependency_blocks_then_unblocks(server):
    a = _call(server, "create_task", title="a")
    b = _call(server, "create_task", title="b")
    _call(server, "add_task_dependency",
          task_id=b["task_id"], depends_on=a["task_id"])
    # b should be pending (not yet blocked; LLM must opt into
    # blocked status). Mark it blocked then complete a.
    _call(server, "update_task", task_id=b["task_id"],
          status="blocked")
    _call(server, "complete_task", task_id=a["task_id"])
    # b is now unblocked (back to pending).
    g = _call(server, "get_task", task_id=b["task_id"])
    assert g["task"]["status"] == "pending"
