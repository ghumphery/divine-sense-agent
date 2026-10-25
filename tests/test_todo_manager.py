"""Tests for src.core.todo_manager.TodoManager + TodoTask.

Coverage:
      basic CRUD, status machine, auto-complete cascade,
      dependencies + blocked, reorder, list / tree / progress,
      journal audit, add_subtask / add_dependency, edge cases.
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
def mgr(tmp_home):
    from src.core.todo_manager import TodoManager
    yield TodoManager(root=tmp_home / "todos")


# --- basic CRUD ---


def test_create_root_task_persists(mgr, tmp_home):
    t = mgr.create(title="plan project X")
    assert t.task_id
    assert t.parent_id is None
    assert t.status == "pending"
    assert t.priority == "medium"
    assert t.order == 1.0
    p = tmp_home / "todos" / f"{t.task_id}.json"
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["title"] == "plan project X"


def test_create_subtask_inherits_parent(mgr):
    parent = mgr.create(title="project")
    child = mgr.add_subtask(parent_id=parent.task_id, title="step 1")
    assert child.parent_id == parent.task_id
    assert child.order == 1.0


def test_create_subtask_unknown_parent_raises(mgr):
    with pytest.raises(ValueError, match="does not exist"):
        mgr.add_subtask(parent_id="nope", title="x")


def test_update_title_and_notes(mgr):
    t = mgr.create(title="a")
    mgr.update(t.task_id, title="b", notes="more context")
    fresh = mgr.get(t.task_id)
    assert fresh.title == "b"
    assert fresh.notes == "more context"


def test_update_priority_invalid_raises(mgr):
    t = mgr.create(title="a")
    with pytest.raises(ValueError, match="priority"):
        mgr.update(t.task_id, priority="urgent")


def test_update_priority_valid_passes(mgr):
    t = mgr.create(title="a")
    mgr.update(t.task_id, priority="high")
    assert mgr.get(t.task_id).priority == "high"


def test_update_depends_on_unknown_id_raises(mgr):
    t = mgr.create(title="a")
    with pytest.raises(ValueError, match="unknown task_ids"):
        mgr.update(t.task_id, depends_on=["does-not-exist"])


def test_update_self_reparent_raises(mgr):
    t = mgr.create(title="a")
    with pytest.raises(ValueError, match="under itself"):
        mgr.update(t.task_id, parent_id=t.task_id)


def test_delete_cascade_removes_children(mgr, tmp_home):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    c = mgr.add_subtask(a.task_id, "a.c")
    assert mgr.delete(root.task_id, cascade=True)
    for tid in (root.task_id, a.task_id, b.task_id, c.task_id):
        assert not (tmp_home / "todos" / f"{tid}.json").exists()
    assert mgr.list_tasks() == []


def test_delete_no_cascade_keeps_children(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    assert mgr.delete(root.task_id, cascade=False)
    assert mgr.get(a.task_id) is not None


def test_delete_missing_returns_false(mgr):
    assert not mgr.delete("does-not-exist")



# --- status machine ---


def test_complete_stamps_completed_at(mgr):
    t = mgr.create(title="a")
    mgr.complete(t.task_id, result="done")
    fresh = mgr.get(t.task_id)
    assert fresh.status == "completed"
    assert fresh.completed_at is not None
    assert fresh.result == "done"


def test_invalid_transition_raises(mgr):
    t = mgr.create(title="a")
    mgr.complete(t.task_id)
    with pytest.raises(ValueError, match="cannot transition"):
        mgr.set_status(t.task_id, "in_progress")


def test_reopen_via_pending(mgr):
    t = mgr.create(title="a")
    mgr.complete(t.task_id)
    mgr.set_status(t.task_id, "pending")
    assert mgr.get(t.task_id).status == "pending"
    assert mgr.get(t.task_id).completed_at is None


def test_notes_append_stamped(mgr):
    t = mgr.create(title="a")
    mgr.set_status(t.task_id, "in_progress", notes_append="started")
    mgr.set_status(t.task_id, "completed", notes_append="finished")
    n = mgr.get(t.task_id).notes
    assert "started" in n and "finished" in n
    assert n.count("[20") == 2


def test_unknown_status_raises(mgr):
    t = mgr.create(title="a")
    with pytest.raises(ValueError, match="status must be one of"):
        mgr.set_status(t.task_id, "weird")


def test_set_status_missing_id_returns_none(mgr):
    assert mgr.set_status("nope", "completed") is None


# --- auto-complete cascade ---


def test_parent_auto_completes_when_all_children_terminal(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    mgr.complete(a.task_id)
    assert mgr.get(root.task_id).status == "pending"
    mgr.complete(b.task_id)
    assert mgr.get(root.task_id).status == "completed"


def test_parent_does_not_complete_with_pending_child(mgr):
    root = mgr.create(title="r")
    mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    mgr.complete(b.task_id)
    assert mgr.get(root.task_id).status == "pending"


def test_skipped_child_counts_as_terminal(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    mgr.skip(a.task_id, reason="n/a")
    mgr.complete(b.task_id)
    assert mgr.get(root.task_id).status == "completed"


def test_failed_child_counts_as_terminal(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    mgr.fail(a.task_id, error="boom")
    assert mgr.get(root.task_id).status == "completed"


# --- dependencies + blocked ---


def test_deps_resolve_after_complete(mgr):
    a = mgr.create(title="a")
    b = mgr.create(title="b", depends_on=[a.task_id])
    mgr.complete(a.task_id)
    assert mgr.get(b.task_id).status == "pending"


def test_blocked_status_unblocks_when_dep_resolved(mgr):
    a = mgr.create(title="a")
    b = mgr.create(title="b", depends_on=[a.task_id])
    mgr.set_status(b.task_id, "blocked")
    assert mgr.get(b.task_id).status == "blocked"
    mgr.complete(a.task_id)
    assert mgr.get(b.task_id).status == "pending"


def test_unknown_dep_treated_unresolved(mgr):
    b = mgr.create(title="b", depends_on=["ghost"])
    mgr.set_status(b.task_id, "blocked")
    summary = mgr.get_progress_summary()
    assert "ghost" not in [t["task_id"] for t in summary["ready"]]


# --- reorder ---


def test_reorder_before_sibling(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    c = mgr.add_subtask(root.task_id, "c")
    mgr.reorder(c.task_id, before_id=a.task_id)
    children = [t["title"] for t in mgr.list_tasks(parent_id=root.task_id)]
    assert children.index("c") < children.index("a")


def test_reorder_after_sibling(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    c = mgr.add_subtask(root.task_id, "c")
    mgr.reorder(a.task_id, after_id=c.task_id)
    children = [t["title"] for t in mgr.list_tasks(parent_id=root.task_id)]
    assert children.index("a") > children.index("c")


def test_reorder_between_two_siblings(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    c = mgr.add_subtask(root.task_id, "c")
    mgr.reorder(c.task_id, before_id=b.task_id, after_id=a.task_id)
    children = [t["title"] for t in mgr.list_tasks(parent_id=root.task_id)]
    assert children.index("c") < children.index("b")


def test_reorder_to_bottom_when_no_args(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    mgr.reorder(a.task_id)
    children = [t["title"] for t in mgr.list_tasks(parent_id=root.task_id)]
    assert children[-1] == "a"


def test_reorder_cross_parent_rejected(mgr):
    a = mgr.create(title="a")
    b = mgr.create(title="b")
    x = mgr.add_subtask(a.task_id, "x")
    y = mgr.add_subtask(b.task_id, "y")
    # y cannot be reordered relative to x - they have different parents
    with pytest.raises(ValueError, match="share parent"):
        mgr.reorder(y.task_id, before_id=x.task_id)



# --- list + tree + progress ---


def test_list_tasks_filters_parent_and_status(mgr):
    root = mgr.create(title="r")
    mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    mgr.complete(b.task_id)
    only_root = mgr.list_tasks(parent_id=None)
    assert {t["title"] for t in only_root} == {"r"}
    pending = mgr.list_tasks(parent_id=root.task_id, status="pending")
    assert {t["title"] for t in pending} == {"a"}
    completed = mgr.list_tasks(parent_id=root.task_id, status="completed")
    assert {t["title"] for t in completed} == {"b"}


def test_list_tasks_include_descendants(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    mgr.add_subtask(a.task_id, "a.x")
    mgr.add_subtask(root.task_id, "b")
    flat = mgr.list_tasks(parent_id=root.task_id, include_descendants=True)
    titles = {t["title"] for t in flat}
    assert titles == {"r", "a", "a.x", "b"}


def test_get_progress_summary_basic(mgr):
    root = mgr.create(title="r")
    mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    mgr.complete(b.task_id)
    summary = mgr.get_progress_summary(task_id=root.task_id)
    assert summary["total"] == 3
    assert summary["by_status"]["pending"] == 2
    assert summary["by_status"]["completed"] == 1
    assert abs(summary["completion_pct"] - 1/3) < 0.01
    assert summary["next_recommended"] is not None


def test_get_progress_summary_empty(mgr):
    s = mgr.get_progress_summary(task_id=None)
    assert s["total"] == 0
    assert s["completion_pct"] == 0.0
    assert s["next_recommended"] is None


def test_render_tree_returns_pre_order(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    mgr.add_subtask(a.task_id, "a.1")
    mgr.add_subtask(a.task_id, "a.2")
    b = mgr.add_subtask(root.task_id, "b")
    tree = mgr.render_tree(root.task_id)
    titles = [(t["title"], t["depth"]) for t in tree]
    assert titles == [
        ("r", 0),
        ("a", 1),
        ("a.1", 2),
        ("a.2", 2),
        ("b", 1),
    ]


def test_execute_plan_cascades_to_parent(mgr):
    root = mgr.create(title="r")
    a = mgr.add_subtask(root.task_id, "a")
    b = mgr.add_subtask(root.task_id, "b")
    mgr.complete(a.task_id)
    mgr.complete(b.task_id)
    summary = mgr.execute_plan(root.task_id)
    assert summary["by_status"]["completed"] == summary["total"]
    assert mgr.get(root.task_id).status == "completed"


# --- journal audit ---


def test_journal_records_create_and_status(mgr):
    t = mgr.create(title="a")
    mgr.start(t.task_id)
    mgr.complete(t.task_id, result="ok")
    j = mgr.list_journal(t.task_id)
    ops = [line["op"] for line in j]
    assert ops == ["create", "status", "status"]
    assert j[-1]["to_status"] == "completed"
    assert j[-1].get("result") == "ok"


def test_journal_missing_task_returns_empty(mgr):
    assert mgr.list_journal("ghost") == []


def test_journal_tail_limit(mgr):
    t = mgr.create(title="a")
    # Each cycle: pending -> in_progress -> completed.
    # create counts as 1 journal line; 10 cycles = 20 more.
    for _ in range(10):
        mgr.set_status(t.task_id, "pending")
        mgr.start(t.task_id)
        mgr.complete(t.task_id)
    j = mgr.list_journal(t.task_id, limit=5)
    assert len(j) == 5


# --- add_subtask / add_dependency ---


def test_add_dependency_idempotent(mgr):
    a = mgr.create(title="a")
    b = mgr.create(title="b")
    mgr.add_dependency(b.task_id, a.task_id)
    mgr.add_dependency(b.task_id, a.task_id)
    assert mgr.get(b.task_id).depends_on == [a.task_id]


def test_add_dependency_unknown_raises(mgr):
    a = mgr.create(title="a")
    with pytest.raises(ValueError, match="does not exist"):
        mgr.add_dependency(a.task_id, "ghost")


# --- home dir / env var / dataclass round-trip ---


def test_todos_dir_uses_qdrant_rag_home_env(tmp_home):
    from src.core.todo_manager import todos_dir
    assert todos_dir() == tmp_home / "todos"


def test_todo_task_dataclass_roundtrip(mgr):
    t = mgr.create(
        title="x", description="long desc",
        priority="high", depends_on=[],
    )
    t2 = mgr.get(t.task_id)
    assert t.to_dict() == t2.to_dict()


def test_from_dict_drops_unknown_fields():
    """Forward-compat: extra JSON keys are silently dropped."""
    from src.core.todo_manager import TodoTask
    raw = {
        "title": "x", "task_id": "abc",
        "unknown_future_field": "ignored",
    }
    t = TodoTask.from_dict(raw)
    assert t.title == "x"
    assert t.task_id == "abc"


# --- edge cases ---


def test_get_missing_returns_none(mgr):
    assert mgr.get("nope") is None


def test_create_then_index_lists_root(mgr):
    a = mgr.create(title="a")
    b = mgr.create(title="b")
    roots = mgr.list_tasks(parent_id=None)
    assert {r["title"] for r in roots} == {"a", "b"}


def test_delete_then_index_removes(mgr):
    a = mgr.create(title="a")
    mgr.delete(a.task_id)
    assert mgr.list_tasks(parent_id=None) == []


def test_status_machine_self_loop_pending(mgr):
    t = mgr.create(title="a")
    mgr.set_status(t.task_id, "pending")
    assert mgr.get(t.task_id).status == "pending"


def test_summary_summary_dict_helper(mgr):
    from src.core.todo_manager import _summary_dict
    t = mgr.create(title="a", description="long" * 50)
    sd = _summary_dict(t)
    assert sd["title"] == "a"
    assert sd["task_id"] == t.task_id
    # Description is truncated to 200 chars
    assert len(sd["description"]) <= 200
