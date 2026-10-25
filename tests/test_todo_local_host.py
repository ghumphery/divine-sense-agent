"""Tests for src/mcp_client/host.py::TodoLocalHost.

Covers:
      tool registry, create/list/get, update/delete,
      status transitions, dependencies + reorder,
      progress / tree / journal / execute,
      error paths + missing arg handling, input schema.
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
def host(tmp_home):
    from src.mcp_client.host import TodoLocalHost
    from src.core.todo_manager import TodoManager
    h = TodoLocalHost()
    h.attach_manager(TodoManager(root=tmp_home / "todos"))
    return h


def _call(host, name, **kwargs):
    for t in host.list_tools():
        if t.name == name:
            res = t(kwargs)
            assert not res.is_error, f"{name} errored: {res.text}"
            return res
    raise KeyError(f"tool {name!r} not registered")
# --- tool registry ---


def test_list_tools_has_16_entries(host):
    """v0.8.8+: 16 todo tools exposed in-process."""
    names = [t.name for t in host.list_tools()]
    expected = {
        "create_task", "add_subtask", "update_task", "delete_task",
        "list_tasks", "get_task", "start_task", "complete_task",
        "skip_task", "fail_task", "add_task_dependency", "reorder_task",
        "get_todo_progress", "list_task_journal", "render_task_tree",
        "execute_plan",
    }
    assert expected.issubset(set(names)), (
        f"missing tools: {expected - set(names)}"
    )


def test_all_tools_have_required_keys(host):
    for t in host.list_tools():
        assert t.name
        assert t.description.strip()
        assert t._handler is not None


def test_all_tools_use_todo_local_server(host):
    for t in host.list_tools():
        assert t.server_name == "todo-local", (
            f"{t.name} declared server_name={t.server_name!r}"
        )


# --- create / list / get ---


def test_create_root_task_via_tool(host):
    res = _call(host, "create_task", title="Plan X")
    payload = json.loads(res.text)
    assert payload["ok"] is True
    assert payload["parent_id"] is None
    assert payload["status"] == "pending"
    assert payload["title"] == "Plan X"


def test_create_then_list_root(host):
    _call(host, "create_task", title="Plan A")
    _call(host, "create_task", title="Plan B")
    res = _call(host, "list_tasks")
    payload = json.loads(res.text)
    titles = [t["title"] for t in payload["tasks"]]
    assert set(titles) == {"Plan A", "Plan B"}


def test_create_with_parent_makes_subtask(host):
    root = _call(host, "create_task", title="root")
    root_payload = json.loads(root.text)
    res = _call(host, "add_subtask",
                parent_id=root_payload["task_id"], title="step 1")
    sub = json.loads(res.text)
    assert sub["ok"] is True
    assert sub["parent_id"] == root_payload["task_id"]


def test_get_task_returns_full(host):
    created = json.loads(_call(host, "create_task", title="full").text)
    res = _call(host, "get_task", task_id=created["task_id"])
    payload = json.loads(res.text)
    assert payload["task"]["title"] == "full"
    assert payload["task"]["status"] == "pending"
    assert "notes" in payload["task"]
    assert "result" in payload["task"]


def test_create_missing_title_returns_error(host):
    res = _call(host, "create_task")
    payload = json.loads(res.text)
    assert "error" in payload


def test_get_missing_task_returns_error(host):
    res = _call(host, "get_task", task_id="nonexistent")
    payload = json.loads(res.text)
    assert "error" in payload


# --- update / delete ---


def test_update_task_title(host):
    created = json.loads(_call(host, "create_task", title="old").text)
    res = _call(host, "update_task",
                task_id=created["task_id"], title="new")
    assert json.loads(res.text)["ok"] is True
    g = json.loads(_call(host, "get_task", task_id=created["task_id"]).text)
    assert g["task"]["title"] == "new"


def test_update_invalid_priority_returns_error(host):
    created = json.loads(_call(host, "create_task", title="x").text)
    res = _call(host, "update_task",
                task_id=created["task_id"], priority="urgent")
    payload = json.loads(res.text)
    assert "error" in payload


def test_delete_task_cascades(host):
    root = json.loads(_call(host, "create_task", title="root").text)
    sub = json.loads(_call(host, "add_subtask",
                           parent_id=root["task_id"], title="sub").text)
    res = _call(host, "delete_task", task_id=root["task_id"])
    payload = json.loads(res.text)
    assert payload["ok"] is True
    g = json.loads(_call(host, "get_task", task_id=sub["task_id"]).text)
    assert "error" in g



# --- status transitions ---


def test_start_then_complete(host):
    created = json.loads(_call(host, "create_task", title="x").text)
    _call(host, "start_task", task_id=created["task_id"])
    res = _call(host, "complete_task",
                task_id=created["task_id"], result="done")
    payload = json.loads(res.text)
    assert payload["task"]["status"] == "completed"
    assert payload["task"]["result"] == "done"
    assert payload["task"]["completed_at"] is not None


def test_skip_task(host):
    created = json.loads(_call(host, "create_task", title="x").text)
    res = _call(host, "skip_task",
                task_id=created["task_id"], reason="not needed")
    payload = json.loads(res.text)
    assert payload["task"]["status"] == "skipped"
    assert "not needed" in payload["task"]["notes"]


def test_fail_task(host):
    created = json.loads(_call(host, "create_task", title="x").text)
    res = _call(host, "fail_task",
                task_id=created["task_id"], error="boom")
    payload = json.loads(res.text)
    assert payload["task"]["status"] == "failed"
    assert payload["task"]["error"] == "boom"


def test_invalid_transition_returns_error(host):
    """completed -> in_progress surfaces a clean error.
    LocalTool marks handler exceptions is_error=True so the
    LLM loop can retry with a different transition."""
    from src.mcp_client.host import LocalTool
    created = json.loads(_call(host, "create_task", title="x").text)
    _call(host, "complete_task", task_id=created["task_id"])
    t = next(t for t in host.list_tools() if t.name == "start_task")
    res = t({"task_id": created["task_id"]})
    assert res.is_error, f"expected error, got: {res.text!r}"
    assert "cannot transition" in res.text or "ValueError" in res.text


# --- dependencies + reorder ---


def test_add_task_dependency(host):
    a = json.loads(_call(host, "create_task", title="a").text)
    b = json.loads(_call(host, "create_task", title="b").text)
    res = _call(host, "add_task_dependency",
                task_id=b["task_id"], depends_on=a["task_id"])
    payload = json.loads(res.text)
    assert a["task_id"] in payload["task"]["depends_on"]


def test_add_task_dependency_unknown_raises(host):
    a = json.loads(_call(host, "create_task", title="a").text)
    res = _call(host, "add_task_dependency",
                task_id=a["task_id"], depends_on="ghost")
    payload = json.loads(res.text)
    assert "error" in payload


def test_reorder_task(host):
    root = json.loads(_call(host, "create_task", title="root").text)
    a = json.loads(_call(host, "add_subtask",
                          parent_id=root["task_id"], title="a").text)
    _ = json.loads(_call(host, "add_subtask",
                          parent_id=root["task_id"], title="b").text)
    c = json.loads(_call(host, "add_subtask",
                          parent_id=root["task_id"], title="c").text)
    res = _call(host, "reorder_task",
                task_id=c["task_id"], before_id=a["task_id"])
    assert json.loads(res.text)["ok"] is True
    listing = json.loads(_call(host, "list_tasks",
                                 parent_id=root["task_id"]).text)
    titles = [t["title"] for t in listing["tasks"]]
    assert titles.index("c") < titles.index("a")


# --- progress / tree / journal / execute ---


def test_get_todo_progress_no_args(host):
    _call(host, "create_task", title="a")
    res = _call(host, "get_todo_progress")
    payload = json.loads(res.text)
    assert payload["ok"] is True
    assert payload["progress"]["total"] >= 1
    assert "by_status" in payload["progress"]
    assert "completion_pct" in payload["progress"]


def test_get_todo_progress_for_subtree(host):
    root = json.loads(_call(host, "create_task", title="root").text)
    a = json.loads(_call(host, "add_subtask",
                          parent_id=root["task_id"], title="a").text)
    _ = json.loads(_call(host, "add_subtask",
                          parent_id=root["task_id"], title="b").text)
    _call(host, "complete_task", task_id=a["task_id"])
    res = _call(host, "get_todo_progress", task_id=root["task_id"])
    payload = json.loads(res.text)
    assert payload["progress"]["total"] == 3
    assert payload["progress"]["by_status"]["pending"] == 2


def test_list_task_journal(host):
    created = json.loads(_call(host, "create_task", title="x").text)
    _call(host, "start_task", task_id=created["task_id"])
    _call(host, "complete_task", task_id=created["task_id"])
    res = _call(host, "list_task_journal", task_id=created["task_id"])
    payload = json.loads(res.text)
    assert payload["count"] >= 3


def test_render_task_tree(host):
    root = json.loads(_call(host, "create_task", title="root").text)
    _call(host, "add_subtask", parent_id=root["task_id"], title="a")
    _call(host, "add_subtask", parent_id=root["task_id"], title="b")
    res = _call(host, "render_task_tree", task_id=root["task_id"])
    payload = json.loads(res.text)
    titles = [t["title"] for t in payload["tree"]]
    assert "root" in titles and "a" in titles and "b" in titles


def test_execute_plan_cascades_complete(host):
    root = json.loads(_call(host, "create_task", title="r").text)
    a = json.loads(_call(host, "add_subtask",
                          parent_id=root["task_id"], title="a").text)
    b = json.loads(_call(host, "add_subtask",
                          parent_id=root["task_id"], title="b").text)
    _call(host, "complete_task", task_id=a["task_id"])
    _call(host, "complete_task", task_id=b["task_id"])
    res = _call(host, "execute_plan", task_id=root["task_id"])
    payload = json.loads(res.text)
    assert payload["progress"]["by_status"]["completed"] == 3



# --- error paths + missing arg handling ---


def test_invalid_task_id_type_handled(host):
    """Non-string task_id returns a clean error JSON rather
    than raising inside the handler."""
    from src.mcp_client.host import LocalTool
    t = next(t for t in host.list_tools() if t.name == "get_task")
    res = t({"task_id": 12345})
    payload = json.loads(res.text)
    assert "error" in payload
    assert "task_id" in payload["error"]


def test_negative_limit_rejected(host):
    from src.mcp_client.host import LocalTool
    t = next(t for t in host.list_tools() if t.name == "list_task_journal")
    created = json.loads(_call(host, "create_task", title="x").text)
    res = t({"task_id": created["task_id"], "limit": 0})
    payload = json.loads(res.text)
    assert "error" in payload
    assert "limit" in payload["error"]


def test_complete_invalid_task_id_handled(host):
    """Bad task_id in complete_task returns a clean error JSON."""
    from src.mcp_client.host import LocalTool
    t = next(t for t in host.list_tools() if t.name == "complete_task")
    res = t({"task_id": 999})  # not a string
    payload = json.loads(res.text)
    assert "error" in payload
    assert "task_id" in payload["error"]


def test_delete_missing_task_returns_false(host):
    """delete_task on a missing id returns ok=False rather than
    raising."""
    res = _call(host, "delete_task", task_id="does-not-exist")
    payload = json.loads(res.text)
    assert payload["ok"] is False


# --- input schema sanity ---


def test_input_schema_has_correct_types(host):
    """create_task inputSchema is an object with a single
    ``args`` property (handlers take a single dict). This is
    the convention every LocalHost follows so the LLM can pass
    arbitrary fields without us hand-authoring per-tool JSON
    schemas."""
    t = next(t for t in host.list_tools() if t.name == "create_task")
    schema = t.input_schema
    assert schema["type"] == "object"
    props = schema["properties"]
    assert "args" in props
    assert props["args"]["type"] == "object"
