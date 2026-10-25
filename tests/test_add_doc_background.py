"""v0.9.133.5 — oversized ``add_document`` → background ingest task.

The 600 s tool watchdog (`QDRANT_TOOL_CALL_TIMEOUT_S`) previously
aborted an 800-page PDF mid-run while the abandoned thread kept
writing silently. Now oversized files (over
``QDRANT_ADD_DOCUMENT_INLINE_MAX_MB``, default 8 MB) are promoted to a
DETACHED daemon thread registered in the process-wide task registry:
the tool returns immediately with ``{ok, background: true, task_id}``,
the registry stop flag halts the embed loop BETWEEN batches
(``ingest_file(stop_check=...)``), and the terminal notify hook (wired
in gui/api.py to Telegram + the task panel) tells the user what
happened. Inline files keep the exact legacy behaviour.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.agent import task_runner
from src.ingest.chunker import DocumentChunk
from src.ingest.pipeline import IngestPipeline
from src.mcp_client.host import QdrantLocalHost

_PATCH_INGEST = (
    "src.mcp_client.host.QdrantLocalHost._ingest_via_pipeline")


@pytest.fixture(autouse=True)
def _clean_registry():
    task_runner.reset_tasks()
    task_runner.set_notify_callback(None)
    task_runner.stop_monitor()
    yield
    task_runner.reset_tasks()
    task_runner.set_notify_callback(None)
    task_runner.stop_monitor()


def _file(tmp_path, name="book.pdf", mb=2.0):
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4 fixture\n" + b"0" * int(mb * 1024 * 1024))
    return p


def _wait_terminal(task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = task_runner.get_task(task_id)
        if rec and rec["status"] != task_runner.RUNNING:
            return rec
        time.sleep(0.02)
    rec = task_runner.get_task(task_id)
    assert rec is not None, task_id
    assert rec["status"] != task_runner.RUNNING, (
        f"task {task_id} never finished")
    return rec


class TestInlineThreshold:
    def test_small_file_stays_inline(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        monkeypatch.setenv("QDRANT_ADD_DOCUMENT_INLINE_MAX_MB", "8")
        p = _file(tmp_path, mb=2)
        host = QdrantLocalHost()
        with patch(_PATCH_INGEST, return_value=3) as pi:
            r = host._add_document(
                {"mode": "path", "file_path": str(p)})
        assert r.get("background") is None
        assert r["ok"] is True
        assert r["chunks"] == 3
        pi.assert_called_once()
        assert task_runner.list_tasks()["total"] == 0

    def test_threshold_zero_is_legacy_never_background(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        monkeypatch.setenv("QDRANT_ADD_DOCUMENT_INLINE_MAX_MB", "0")
        p = _file(tmp_path, mb=5)
        host = QdrantLocalHost()
        with patch(_PATCH_INGEST, return_value=4) as pi:
            r = host._add_document(
                {"mode": "path", "file_path": str(p)})
        assert r.get("background") is None
        assert r["chunks"] == 4
        pi.assert_called_once()

    def test_bad_threshold_falls_back_to_default(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        monkeypatch.setenv("QDRANT_ADD_DOCUMENT_INLINE_MAX_MB", "banana")
        p = _file(tmp_path, mb=2)
        host = QdrantLocalHost()
        with patch(_PATCH_INGEST, return_value=1):
            r = host._add_document(
                {"mode": "path", "file_path": str(p)})
        # default threshold is 8 MB → 2 MB stays inline
        assert r.get("background") is None
        assert r["ok"] is True


class TestBackgroundPromotion:
    def test_oversized_promoted_and_completes(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        monkeypatch.setenv("QDRANT_ADD_DOCUMENT_INLINE_MAX_MB", "1")
        p = _file(tmp_path, mb=2)
        host = QdrantLocalHost()
        with patch(_PATCH_INGEST, return_value=99) as pi:
            r = host._add_document(
                {"mode": "path", "file_path": str(p)})
        assert r["ok"] is True
        assert r["background"] is True
        assert r["task_id"]
        assert r["size_mb"] == 2.0
        assert "task_id" in r["message"]
        pi.assert_called_once()  # thread runs the SAME pipeline path
        rec = _wait_terminal(r["task_id"])
        assert rec["status"] == task_runner.DONE
        assert rec["answer_chars"] == 99
        assert rec["source"] == "ingest"
        assert rec["sid"] == f"task-{r['task_id']}"

    def test_background_failure_marks_failed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        monkeypatch.setenv("QDRANT_ADD_DOCUMENT_INLINE_MAX_MB", "1")
        p = _file(tmp_path, mb=2)
        host = QdrantLocalHost()
        with patch(_PATCH_INGEST,
                   side_effect=RuntimeError("embed server down")):
            r = host._add_document(
                {"mode": "path", "file_path": str(p)})
        assert r["background"] is True
        rec = _wait_terminal(r["task_id"])
        assert rec["status"] == task_runner.FAILED
        assert "embed server down" in (rec.get("error") or "")

    def test_stop_flag_marks_stopped_with_written_prefix(
            self, tmp_path, monkeypatch):
        """The thread honours the registry stop flag: once set mid-run,
        the already-written chunk count is stamped STOPPED."""
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        monkeypatch.setenv("QDRANT_ADD_DOCUMENT_INLINE_MAX_MB", "1")
        p = _file(tmp_path, mb=2)
        host = QdrantLocalHost()

        def _side_effect(pp, *, stop_check=None):
            # user hits 🛑 while the ingest is running
            for t in task_runner.list_tasks(
                    status=task_runner.RUNNING)["tasks"]:
                task_runner.request_stop(t["task_id"])
            return 250  # the prefix written before the stop landed

        with patch(_PATCH_INGEST, side_effect=_side_effect):
            r = host._add_document(
                {"mode": "path", "file_path": str(p)})
        assert r["background"] is True
        rec = _wait_terminal(r["task_id"])
        assert rec["status"] == task_runner.STOPPED
        assert rec["answer_chars"] == 250


class TestPipelineStopCheck:
    """``IngestPipeline._embed_and_upsert(stop_check=...)`` — the embed
    loop breaks between batches and keeps the written prefix."""

    def _mini_pipe(self, upserted):
        p = IngestPipeline.__new__(IngestPipeline)
        p.EMBED_BATCH_SIZE = 2
        p.INSERT_BATCH_SIZE = 2

        def _embed_batch(texts):
            return [[0.1] for _ in texts]

        p.embedder = type("E", (), {
            "embed_batch": staticmethod(_embed_batch),
            "last_backends": {},
        })()
        p.store = type("S", (), {"upsert": staticmethod(
            lambda points: upserted.append(len(points)))})()
        return p

    def _chunks(self, n):
        return [DocumentChunk(
            text=f"chunk {i}",
            metadata={"file_path": "x.md", "filename": "x.md",
                      "chunk_index": i, "total_chunks": n},
        ) for i in range(n)]

    def test_stop_check_breaks_between_batches(self, tmp_path):
        import hashlib
        from pathlib import Path

        upserted = []
        p = self._mini_pipe(upserted)
        chunks = self._chunks(4)
        hashes = [
            hashlib.sha256(c.text.encode()).hexdigest()[:32]
            for c in chunks]

        calls = {"n": 0}

        def stop():
            calls["n"] += 1
            return calls["n"] >= 2  # stop at the 2nd batch boundary

        n = p._embed_and_upsert(
            Path("x.md"), chunks, hashes,
            "C:/x.md", stop_check=stop,
        )
        assert n == 2          # first batch landed, second never ran
        assert sum(upserted) == 2

    def test_no_stop_check_runs_all(self, tmp_path):
        import hashlib
        from pathlib import Path

        upserted = []
        p = self._mini_pipe(upserted)
        chunks = self._chunks(4)
        hashes = [
            hashlib.sha256(c.text.encode()).hexdigest()[:32]
            for c in chunks]
        n = p._embed_and_upsert(Path("x.md"), chunks, hashes, "C:/x.md")
        assert n == 4
        assert sum(upserted) == 4

    def test_ingest_file_stop_check_is_kwonly_none(self):
        """Legacy callers are byte-identical (keyword-only, default
        None — nobody is forced to pass it)."""
        import inspect

        sig = inspect.signature(IngestPipeline.ingest_file)
        pc = sig.parameters["stop_check"]
        assert pc.default is None
        assert pc.kind == inspect.Parameter.KEYWORD_ONLY