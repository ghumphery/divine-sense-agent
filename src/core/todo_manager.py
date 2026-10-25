"""Multi-level todo list manager (v0.8.8+).

Powers the Agent's "plan -> execute -> track" loop. Every
complex chat request can be decomposed into a tree of TodoTask
nodes; the LLM drives CRUD + status transitions through MCP
tools (``src/mcp_client/host.py::TodoLocalHost`` +
``src/mcp_server/todo_server.py``), and reads progress back
through ``get_progress_summary``.

Design notes
============

* **Hierarchical**: each task carries an optional parent_id.
  Top-level tasks have parent_id=None; children carry the
  parent's task_id. No depth limit - LLM picks.

* **Append-only journal**: every state-changing op writes a
  line to ``$QDRANT_RAG_HOME/todos/<task_id>.journal.jsonl``.

* **Status machine**: pending -> in_progress -> completed
  (happy path). Also skipped / failed / blocked. The Agent
  auto-completes a parent only when ALL children are terminal.

* **Dependencies** (depends_on list): a task cannot move out
  of pending until every dependency is completed or skipped.

* **Reordering**: reorder(task_id, before_id/after_id) uses
  floats so insert-between-two is a single midpoint.

* **Storage layout**::

      $QDRANT_RAG_HOME/
          todos/
              <task_id>.json           # current snapshot
              <task_id>.journal.jsonl  # append-only audit
              index.json               # {id: {...minimal...}}

* **No external deps**: stdlib only.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("core.todo_manager")

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
ALL_STATUSES = {
    STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_COMPLETED,
    STATUS_SKIPPED, STATUS_FAILED, STATUS_BLOCKED,
}

PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"
ALL_PRIORITIES = {PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW}

DEFAULT_PRIORITY = PRIORITY_MEDIUM

_TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_SKIPPED, STATUS_FAILED}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _home() -> Path:
    return Path(os.environ.get("QDRANT_RAG_HOME", str(Path.home() / ".qdrant-rag-agent")))


def todos_dir() -> Path:
    d = _home() / "todos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _task_path(task_id: str) -> Path:
    return todos_dir() / f"{task_id}.json"


def _journal_path(task_id: str) -> Path:
    return todos_dir() / f"{task_id}.journal.jsonl"


def _index_path() -> Path:
    return todos_dir() / "index.json"


@dataclass
class TodoTask:
    title: str
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: str | None = None
    description: str = ""
    status: str = STATUS_PENDING
    priority: str = DEFAULT_PRIORITY
    depends_on: list[str] = field(default_factory=list)
    order: float = 0.0
    notes: str = ""
    result: str = ""
    error: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TodoTask":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in d.items() if k in known}
        return cls(**clean)

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    def progress_fraction(self) -> float:
        return 1.0 if self.is_terminal else 0.0



class TodoManager:
    """CRUD + status machine + tree ops over TodoTask records.
    Thread-safe (single mutex over all writes)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else todos_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._index = self._load_index()

    # --- Index helpers ---

    def _load_index(self) -> dict[str, dict[str, Any]]:
        p = _index_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_index(self) -> None:
        p = _index_path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, p)

    def _index_entry(self, t: TodoTask) -> dict[str, Any]:
        return {
            "task_id": t.task_id,
            "parent_id": t.parent_id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "order": t.order,
            "depends_on": list(t.depends_on),
            "created_at": t.created_at,
            "updated_at": t.updated_at,
            "completed_at": t.completed_at,
        }

    # --- Task file I/O ---

    def _save_task(self, t: TodoTask) -> None:
        p = _task_path(t.task_id)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(t.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, p)

    def _load_task(self, task_id: str) -> TodoTask | None:
        p = _task_path(task_id)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return TodoTask.from_dict(d)
        except Exception:
            return None



    # --- Journal ---

    def _append_journal(self, t: TodoTask, op: str, **extra: Any) -> None:
        line = {
            "ts": _now_iso(),
            "op": op,
            "task_id": t.task_id,
            "parent_id": t.parent_id,
            "status": t.status,
            **extra,
        }
        try:
            jp = _journal_path(t.task_id)
            with jp.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as e:
            _log.warning("journal append failed for %s: %r", t.task_id, e)

    def list_journal(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        jp = _journal_path(task_id)
        if not jp.exists():
            return []
        try:
            lines = jp.read_text(encoding="utf-8").splitlines()
            tail = lines[-limit:] if limit > 0 else lines
            out: list[dict[str, Any]] = []
            for ln in tail:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
            return out
        except Exception:
            return []

    # --- CRUD ---

    def create(
        self,
        title: str,
        parent_id: str | None = None,
        description: str = "",
        priority: str = DEFAULT_PRIORITY,
        depends_on: list[str] | None = None,
        order: float | None = None,
    ) -> TodoTask:
        with self._lock:
            t = TodoTask(
                title=title,
                parent_id=parent_id,
                description=description,
                priority=priority if priority in ALL_PRIORITIES else DEFAULT_PRIORITY,
                depends_on=list(depends_on or []),
            )
            if order is None:
                sib_orders = [
                    e["order"] for e in self._index.values()
                    if e.get("parent_id") == parent_id
                ]
                t.order = (max(sib_orders) + 1.0) if sib_orders else 1.0
            else:
                t.order = float(order)
            self._save_task(t)
            self._index[t.task_id] = self._index_entry(t)
            self._save_index()
            self._append_journal(t, "create", title=t.title)
            self._maybe_unblock(t)
            return t



    def get(self, task_id: str) -> TodoTask | None:
        return self._load_task(task_id)

    def update(
        self,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        notes: str | None = None,
        order: float | None = None,
        parent_id: str | None = None,
        depends_on: list[str] | None = None,
    ) -> TodoTask | None:
        with self._lock:
            t = self._load_task(task_id)
            if t is None:
                return None
            changes: dict[str, Any] = {}
            if title is not None:
                t.title = title
                changes["title"] = title
            if description is not None:
                t.description = description
                changes["description"] = description
            if priority is not None:
                if priority not in ALL_PRIORITIES:
                    raise ValueError(
                        f"priority must be one of {sorted(ALL_PRIORITIES)}; got {priority!r}"
                    )
                t.priority = priority
                changes["priority"] = priority
            if notes is not None:
                t.notes = notes
                changes["notes"] = notes
            if order is not None:
                t.order = float(order)
                changes["order"] = t.order
            if parent_id is not None:
                if parent_id == t.task_id:
                    raise ValueError("cannot reparent a task under itself")
                t.parent_id = parent_id
                changes["parent_id"] = parent_id
            if depends_on is not None:
                missing = [
                    d for d in depends_on
                    if d not in self._index and d != task_id
                ]
                if missing:
                    raise ValueError(
                        f"depends_on references unknown task_ids: {missing}"
                    )
                t.depends_on = list(depends_on)
                changes["depends_on"] = list(depends_on)
            t.updated_at = _now_iso()
            self._save_task(t)
            self._index[task_id] = self._index_entry(t)
            self._save_index()
            self._append_journal(t, "update", **changes)
            return t

    def delete(self, task_id: str, cascade: bool = True) -> bool:
        with self._lock:
            t = self._load_task(task_id)
            if t is None:
                return False
            removed = 0
            if cascade:
                queue = [task_id]
                seen: set[str] = set()
                while queue:
                    cur = queue.pop(0)
                    if cur in seen:
                        continue
                    seen.add(cur)
                    children = [
                        eid for eid, e in self._index.items()
                        if e.get("parent_id") == cur
                    ]
                    queue.extend(children)
                for tid in seen:
                    tp = _task_path(tid)
                    if tp.exists():
                        tp.unlink()
                        removed += 1
                    jp = _journal_path(tid)
                    if jp.exists():
                        jp.unlink()
                    self._index.pop(tid, None)
            else:
                tp = _task_path(task_id)
                if tp.exists():
                    tp.unlink()
                    removed += 1
                self._index.pop(task_id, None)
            self._save_index()
            _log.info("deleted task %s (cascade=%s; removed=%d)", task_id, cascade, removed)
            return removed > 0



    # --- Status transitions ---

    def set_status(
        self,
        task_id: str,
        new_status: str,
        result: str | None = None,
        error: str | None = None,
        notes_append: str | None = None,
    ) -> TodoTask | None:
        with self._lock:
            t = self._load_task(task_id)
            if t is None:
                return None
            if new_status not in ALL_STATUSES:
                raise ValueError(
                    f"status must be one of {sorted(ALL_STATUSES)}; got {new_status!r}"
                )
            allowed = self._allowed_transitions(t.status)
            if new_status not in allowed:
                raise ValueError(
                    f"cannot transition {t.task_id} from {t.status!r} to "
                    f"{new_status!r}; allowed: {sorted(allowed)}"
                )
            old = t.status
            t.status = new_status
            if result is not None:
                t.result = result
            if error is not None:
                t.error = error
            if notes_append is not None:
                stamp = _now_iso()
                sep = "\n\n" if t.notes else ""
                t.notes = f"{t.notes}{sep}[{stamp}] {notes_append}"
            t.updated_at = _now_iso()
            if new_status in _TERMINAL_STATUSES and not t.completed_at:
                t.completed_at = t.updated_at
            elif new_status not in _TERMINAL_STATUSES and t.completed_at:
                t.completed_at = None
            self._save_task(t)
            self._index[task_id] = self._index_entry(t)
            self._save_index()
            self._append_journal(
                t, "status",
                from_status=old, to_status=new_status,
                **( {"result": result} if result else {}),
                **( {"error": error} if error else {}),
            )
            if new_status in _TERMINAL_STATUSES and t.parent_id:
                self._auto_complete_parent_if_done(t.parent_id)
            self.unblock_ready()
            return t

    def _allowed_transitions(self, current: str) -> set[str]:
        """State machine. Re-opening back to pending is always
        allowed (LLM makes mistakes, plans change)."""
        if current == STATUS_PENDING:
            return {STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_SKIPPED, STATUS_BLOCKED, STATUS_COMPLETED, STATUS_FAILED}
        if current == STATUS_IN_PROGRESS:
            return {STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_FAILED, STATUS_SKIPPED}
        if current == STATUS_COMPLETED:
            return {STATUS_PENDING, STATUS_COMPLETED, STATUS_SKIPPED}
        if current == STATUS_SKIPPED:
            return {STATUS_PENDING, STATUS_SKIPPED, STATUS_IN_PROGRESS, STATUS_COMPLETED}
        if current == STATUS_FAILED:
            return {STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_FAILED, STATUS_SKIPPED, STATUS_COMPLETED}
        if current == STATUS_BLOCKED:
            return {STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_BLOCKED, STATUS_SKIPPED, STATUS_COMPLETED}
        return set(ALL_STATUSES)

    def _auto_complete_parent_if_done(self, parent_id: str) -> None:
        child_statuses = [
            e.get("status") for e in self._index.values()
            if e.get("parent_id") == parent_id
        ]
        if not child_statuses:
            return
        if all(s in _TERMINAL_STATUSES for s in child_statuses):
            parent = self._load_task(parent_id)
            if parent and parent.status not in _TERMINAL_STATUSES:
                _log.info("auto-completing parent %s - all children terminal", parent_id)
                self.set_status(parent_id, STATUS_COMPLETED)

    def _maybe_unblock(self, t: TodoTask) -> None:
        if t.status != STATUS_BLOCKED:
            return
        if self._deps_resolved(t):
            self.set_status(t.task_id, STATUS_PENDING)

    def unblock_ready(self) -> int:
        """Scan blocked tasks; flip to pending when their deps
        resolved. Returns count of unblocked tasks."""
        with self._lock:
            unblocked = 0
            for eid, e in list(self._index.items()):
                if e.get("status") != STATUS_BLOCKED:
                    continue
                t = self._load_task(eid)
                if t is None:
                    continue
                if self._deps_resolved(t):
                    self.set_status(eid, STATUS_PENDING)
                    unblocked += 1
            return unblocked

    def _deps_resolved(self, t: TodoTask) -> bool:
        for dep_id in t.depends_on:
            dep_entry = self._index.get(dep_id)
            if dep_entry is None:
                return False
            if dep_entry.get("status") not in (
                STATUS_COMPLETED, STATUS_SKIPPED,
            ):
                return False
        return True



    # --- Listing / tree views ---

    def list_tasks(
        self,
        parent_id: str | None = None,
        status: str | None = None,
        include_descendants: bool = False,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if include_descendants and parent_id is not None:
                descendants: set[str] = {parent_id}
                queue = [parent_id]
                while queue:
                    cur = queue.pop(0)
                    for eid, e in self._index.items():
                        if e.get("parent_id") == cur:
                            descendants.add(eid)
                            queue.append(eid)
                entries = [e for eid, e in self._index.items() if eid in descendants]
            else:
                entries = [
                    e for e in self._index.values()
                    if e.get("parent_id") == parent_id
                ]
            if status is not None:
                entries = [e for e in entries if e.get("status") == status]
            entries.sort(key=lambda e: (e.get("order", 0.0), e.get("created_at", "")))
            return [dict(e) for e in entries]

    def get_progress_summary(self, task_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if task_id is None:
                scope = list(self._index.values())
            else:
                descendants: set[str] = {task_id}
                queue = [task_id]
                while queue:
                    cur = queue.pop(0)
                    for eid, e in self._index.items():
                        if e.get("parent_id") == cur:
                            descendants.add(eid)
                            queue.append(eid)
                scope = [e for e in self._index.values() if e["task_id"] in descendants]
            by_status: dict[str, int] = {s: 0 for s in ALL_STATUSES}
            blocked: list[str] = []
            ready: list[dict[str, Any]] = []
            in_prog: list[dict[str, Any]] = []
            terminal = 0
            for e in scope:
                by_status[e.get("status", STATUS_PENDING)] = (
                    by_status.get(e.get("status", STATUS_PENDING), 0) + 1
                )
                if e.get("status") in _TERMINAL_STATUSES:
                    terminal += 1
                if e.get("status") == STATUS_BLOCKED:
                    blocked.append(e["task_id"])
                if e.get("status") == STATUS_PENDING:
                    t = self._load_task(e["task_id"])
                    if t and self._deps_resolved(t):
                        ready.append(dict(e))
                if e.get("status") == STATUS_IN_PROGRESS:
                    in_prog.append(dict(e))
            completion_pct = (
                (terminal / len(scope)) if scope else 0.0
            )
            next_id: str | None = None
            if ready:
                ready.sort(key=lambda e: (e.get("order", 0.0), e.get("created_at", "")))
                next_id = ready[0]["task_id"]
            return {
                "task_id": task_id,
                "total": len(scope),
                "by_status": by_status,
                "completion_pct": round(completion_pct, 4),
                "blocked": blocked,
                "ready": ready,
                "in_progress": in_prog,
                "next_recommended": next_id,
            }

    def render_tree(self, root_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if root_id is None:
                roots = [eid for eid, e in self._index.items() if e.get("parent_id") is None]
            else:
                roots = [root_id]
            out: list[dict[str, Any]] = []
            for rid in sorted(roots, key=lambda i: self._index[i].get("order", 0.0)):
                self._render_subtree(rid, depth=0, out=out)
            return out

    def _render_subtree(
        self,
        task_id: str,
        depth: int,
        out: list[dict[str, Any]],
    ) -> None:
        t = self._load_task(task_id)
        if t is None:
            return
        out.append({**t.to_dict(), "depth": depth})
        children = sorted(
            [eid for eid, e in self._index.items() if e.get("parent_id") == task_id],
            key=lambda i: self._index[i].get("order", 0.0),
        )
        for cid in children:
            self._render_subtree(cid, depth + 1, out)



    # --- Convenience ops used by the LLM through MCP ---

    def complete(self, task_id: str, result: str = "") -> TodoTask | None:
        return self.set_status(task_id, STATUS_COMPLETED, result=result)

    def fail(self, task_id: str, error: str = "") -> TodoTask | None:
        return self.set_status(task_id, STATUS_FAILED, error=error)

    def skip(self, task_id: str, reason: str = "") -> TodoTask | None:
        return self.set_status(task_id, STATUS_SKIPPED, notes_append=reason or "skipped")

    def start(self, task_id: str) -> TodoTask | None:
        return self.set_status(task_id, STATUS_IN_PROGRESS)

    def add_subtask(
        self,
        parent_id: str,
        title: str,
        description: str = "",
        priority: str = DEFAULT_PRIORITY,
    ) -> TodoTask | None:
        if parent_id not in self._index:
            raise ValueError(f"parent_id {parent_id!r} does not exist")
        return self.create(
            title=title,
            parent_id=parent_id,
            description=description,
            priority=priority,
        )

    def add_dependency(self, task_id: str, depends_on: str) -> TodoTask | None:
        if depends_on not in self._index:
            raise ValueError(f"depends_on {depends_on!r} does not exist")
        t = self._load_task(task_id)
        if t is None:
            raise ValueError(f"task_id {task_id!r} does not exist")
        if depends_on in t.depends_on:
            return t
        new_deps = list(t.depends_on) + [depends_on]
        return self.update(task_id, depends_on=new_deps)



    def reorder(self, task_id: str, before_id: str | None = None, after_id: str | None = None) -> TodoTask | None:
        """Insert ``task_id`` between two siblings. ``before_id``
        places it just above; ``after_id`` places it just below;
        pass only one. If both are None, the task moves to the
        bottom of its parent."""
        with self._lock:
            t = self._load_task(task_id)
            if t is None:
                return None
            if before_id is not None and after_id is not None:
                b = self._load_task(before_id)
                a = self._load_task(after_id)
                if b is None or a is None:
                    raise ValueError("before/after ids not found")
                if b.parent_id != t.parent_id or a.parent_id != t.parent_id:
                    raise ValueError("before/after must share parent")
                new_order = (b.order + a.order) / 2.0
            elif before_id is not None:
                b = self._load_task(before_id)
                if b is None:
                    raise ValueError("before_id not found")
                if b.parent_id != t.parent_id:
                    raise ValueError("before_id must share parent")
                sib_orders = [
                    e["order"] for e in self._index.values()
                    if e.get("parent_id") == t.parent_id and e["task_id"] != task_id
                ]
                below = [o for o in sib_orders if o < b.order]
                prev = max(below) if below else (b.order - 1.0)
                new_order = (prev + b.order) / 2.0
            elif after_id is not None:
                a = self._load_task(after_id)
                if a is None:
                    raise ValueError("after_id not found")
                if a.parent_id != t.parent_id:
                    raise ValueError("after_id must share parent")
                sib_orders = [
                    e["order"] for e in self._index.values()
                    if e.get("parent_id") == t.parent_id and e["task_id"] != task_id
                ]
                above = [o for o in sib_orders if o > a.order]
                nxt = min(above) if above else (a.order + 1.0)
                new_order = (a.order + nxt) / 2.0
            else:
                sib_orders = [
                    e["order"] for e in self._index.values()
                    if e.get("parent_id") == t.parent_id and e["task_id"] != task_id
                ]
                new_order = (max(sib_orders) + 1.0) if sib_orders else 1.0
            return self.update(task_id, order=new_order)

    def execute_plan(
        self,
        task_id: str,
        auto_complete_when: str = "all_children_terminal",
    ) -> dict[str, Any]:
        with self._lock:
            self.unblock_ready()
            self._auto_complete_cascading(task_id)
            return self.get_progress_summary(task_id=task_id)

    def _auto_complete_cascading(self, root_id: str) -> None:
        flat = self.render_tree(root_id)
        flat.sort(key=lambda d: -d.get("depth", 0))
        for d in flat:
            t = self._load_task(d["task_id"])
            if t is None:
                continue
            if t.status in _TERMINAL_STATUSES:
                continue
            self._auto_complete_parent_if_done(t.task_id)


def _summary_dict(t: TodoTask) -> dict[str, Any]:
    return {
        "task_id": t.task_id,
        "parent_id": t.parent_id,
        "title": t.title,
        "status": t.status,
        "priority": t.priority,
        "depends_on": list(t.depends_on),
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "completed_at": t.completed_at,
        "description": t.description[:200] if t.description else "",
    }
