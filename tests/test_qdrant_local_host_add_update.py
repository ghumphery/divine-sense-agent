"""Tests for v0.9.0 QdrantLocalHost additions.

Covers the two new LLM-driven ingest tools exposed on the
``qdrant-local`` server:

* ``add_document``  — large-file (``mode="path"``) and small-file
  (``mode="content"``) ingestion. Both paths must be sandboxed
  under ``QDRANT_IMPORT_ALLOWED_ROOT`` / ``WATCH_DIR``.
* ``update_document`` — replace an already-indexed document with
  new content; old chunks must be removed before re-ingest.

All real network / Qdrant / ingest-pipeline work is mocked at the
``QdrantStore`` and ``IngestPipeline`` boundary so the suite runs
without a server. End-to-end behaviour against a real Qdrant is
covered by the existing CLI smoke docs.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.mcp_client.host import (
    QdrantLocalHost,
    _ADD_DOC_ALLOWED_EXTS,
    _is_within,
    _resolve_import_root,
    _write_content_to_sandbox,
)


# Short alias: _ingest_via_pipeline is a bound method on
# QdrantLocalHost — patching the module-level attribute doesn't
# work because the class holds the only reference. We use
# ``patch.object`` at every call site so the tests are obvious
# about what they're stubbing.
_PATCH_INGEST = (
    "src.mcp_client.host.QdrantLocalHost._ingest_via_pipeline"
)


# ── sandbox helpers ─────────────────────────────────────────


class TestResolveImportRoot:
    def test_defaults_to_watch_dir_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("QDRANT_IMPORT_ALLOWED_ROOT", raising=False)
        monkeypatch.setenv("WATCH_DIR", str(tmp_path))
        root = _resolve_import_root()
        assert root == tmp_path.resolve()

    def test_env_override_wins(self, monkeypatch, tmp_path):
        target = tmp_path / "imports"
        target.mkdir()
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(target))
        monkeypatch.setenv("WATCH_DIR", str(tmp_path / "ignored"))
        assert _resolve_import_root() == target.resolve()

    def test_falls_back_to_documents_when_both_unset(self, monkeypatch):
        monkeypatch.delenv("QDRANT_IMPORT_ALLOWED_ROOT", raising=False)
        monkeypatch.delenv("WATCH_DIR", raising=False)
        # Fallback is ``./documents`` resolved against cwd.
        root = _resolve_import_root()
        assert isinstance(root, Path)
        assert root.is_absolute()


class TestIsWithin:
    def test_inside(self, tmp_path):
        child = tmp_path / "a" / "b.txt"
        assert _is_within(child, tmp_path) is True

    def test_outside(self, tmp_path):
        outside = tmp_path.parent / "elsewhere.txt"
        assert _is_within(outside, tmp_path) is False

    def test_dotdot_escape_blocked(self, tmp_path):
        # ``tmp_path/../escape.txt`` resolves outside tmp_path.
        escape = tmp_path / ".." / "escape.txt"
        assert _is_within(escape, tmp_path) is False

    def test_self_is_within(self, tmp_path):
        assert _is_within(tmp_path, tmp_path) is True

    def test_parent_is_not_within(self, tmp_path):
        assert _is_within(tmp_path.parent, tmp_path) is False


class TestWriteContentToSandbox:
    def test_writes_atomically(self, tmp_path):
        target = _write_content_to_sandbox(
            allowed_root=tmp_path,
            filename="hello.md",
            content="# hello\n",
            overwrite=False,
        )
        assert target == (tmp_path / "hello.md").resolve()
        assert target.read_text(encoding="utf-8") == "# hello\n"

    def test_rejects_path_separator(self, tmp_path):
        with pytest.raises(ValueError, match="leaf name"):
            _write_content_to_sandbox(
                allowed_root=tmp_path,
                filename="sub/file.md",
                content="x",
                overwrite=False,
            )

    def test_rejects_dotdot(self, tmp_path):
        with pytest.raises(ValueError, match="leaf name"):
            _write_content_to_sandbox(
                allowed_root=tmp_path,
                filename="..",
                content="x",
                overwrite=False,
            )

    def test_rejects_unsupported_extension(self, tmp_path):
        with pytest.raises(ValueError, match="unsupported extension"):
            _write_content_to_sandbox(
                allowed_root=tmp_path,
                filename="blob.exe",
                content="x",
                overwrite=False,
            )

    def test_existing_file_without_overwrite_raises(self, tmp_path):
        (tmp_path / "x.txt").write_text("original", encoding="utf-8")
        with pytest.raises(FileExistsError):
            _write_content_to_sandbox(
                allowed_root=tmp_path,
                filename="x.txt",
                content="new",
                overwrite=False,
            )
        # Original content preserved.
        assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "original"

    def test_existing_file_with_overwrite_replaces(self, tmp_path):
        (tmp_path / "x.txt").write_text("original", encoding="utf-8")
        _write_content_to_sandbox(
            allowed_root=tmp_path,
            filename="x.txt",
            content="new",
            overwrite=True,
        )
        assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "new"

    def test_creates_sandbox_root_if_missing(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        # 'a/b/c' doesn't exist yet.
        target = _write_content_to_sandbox(
            allowed_root=deep,
            filename="note.txt",
            content="hi",
            overwrite=False,
        )
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hi"

    def test_no_half_written_files_on_error(self, tmp_path):
        """An OSError mid-write should clean up the temp file and
        leave the target untouched."""

        def boom_open(*a, **kw):
            raise OSError("disk full")

        with patch("os.fdopen", side_effect=boom_open):
            with pytest.raises(OSError, match="disk full"):
                _write_content_to_sandbox(
                    allowed_root=tmp_path,
                    filename="ghost.md",
                    content="x",
                    overwrite=False,
                )
        # No .tmp leftovers in the sandbox.
        leftover = list(tmp_path.glob(".ghost.md.*.tmp"))
        assert leftover == []
        # Target was never created.
        assert not (tmp_path / "ghost.md").exists()


@pytest.fixture(autouse=True)
def _pin_sandbox_roots(monkeypatch, tmp_path):
    """v0.9.133.5 — pin every filesystem-sandbox root to an ISOLATED
    nonexistent spot: the live ``.env`` carries a machine-wide
    ``QDRANT_FILESYSTEM_WRITE_ROOT`` (C:/Users/<user>) that silently
    CONTAINS pytest's tmp_path and defeats every "outside the sandbox"
    rejection test in this module. Pinned to tmp_path/_isolation_root
    (never created, never contains a test file) so positive-write tests
    whose allowed_root is tmp_path itself are unaffected."""
    iso = str(tmp_path / "_isolation_root")
    for k in ("QDRANT_FILESYSTEM_READ_ROOT",
              "QDRANT_FILESYSTEM_WRITE_ROOT",
              "QDRANT_LOG_ALLOWED_ROOT"):
        monkeypatch.setenv(k, iso)
    yield


# ── QdrantLocalHost tool listing ─────────────────────────────


class TestQdrantLocalHostListTools:
    def test_adds_two_new_tools(self):
        host = QdrantLocalHost()
        tools = {t.name: t for t in host.list_tools()}
        assert "add_document" in tools
        assert "update_document" in tools
        assert tools["add_document"].server_name == "qdrant-local"
        assert tools["update_document"].server_name == "qdrant-local"

    def test_add_document_schema(self):
        host = QdrantLocalHost()
        tools = {t.name: t for t in host.list_tools()}
        schema = tools["add_document"].input_schema
        assert schema is not None
        props = schema["properties"]
        assert "mode" in props
        assert props["mode"]["enum"] == ["path", "content"]
        for k in ("file_path", "filename", "content"):
            assert k in props
        assert schema["required"] == ["mode"]

    def test_update_document_schema(self):
        host = QdrantLocalHost()
        tools = {t.name: t for t in host.list_tools()}
        schema = tools["update_document"].input_schema
        props = schema["properties"]
        assert "filename" in props
        assert "content" in props
        assert set(schema["required"]) == {"filename", "content"}


# ── _add_document (path mode) ────────────────────────────────


def _make_host_with_mocks(tmp_path, existing_files=None, monkeypatch=None):
    """Build a QdrantLocalHost whose QdrantStore / pipeline are mocked.

    v0.9.133.5 — every sandbox root env is PINNED to the test's own
    tmp_path (hermetic, like tests/test_resize_image.py): the live
    ``.env`` carries a machine-wide ``QDRANT_FILESYSTEM_WRITE_ROOT``
    (= C:/Users/<user>) that would silently contain pytest's tmp_path
    and turn every "outside the sandbox" rejection test green.
    """
    if monkeypatch is not None:
        for k in ("QDRANT_FILESYSTEM_READ_ROOT",
                  "QDRANT_FILESYSTEM_WRITE_ROOT",
                  "QDRANT_LOG_ALLOWED_ROOT"):
            monkeypatch.setenv(k, str(tmp_path))
    host = QdrantLocalHost()
    host._store = MagicMock()
    host._store.list_documents.return_value = existing_files or []
    host._retriever = MagicMock()
    return host


class TestAddDocumentPathMode:
    def test_success_ingests_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        target = tmp_path / "report.pdf"
        target.write_bytes(b"%PDF-1.4 fake content\n")

        host = _make_host_with_mocks(tmp_path)
        with patch(
            _PATCH_INGEST,
            return_value=7,
        ) as mock_ingest:
            result = host._add_document(
                {"mode": "path", "file_path": str(target)},
            )
        assert result["ok"] is True
        assert result["mode"] == "path"
        assert result["filename"] == "report.pdf"
        assert result["chunks"] == 7
        assert result["sandbox_root"] == tmp_path.resolve().as_posix()
        mock_ingest.assert_called_once()
        # First positional arg is the Path that was ingested.
        ingested_path = mock_ingest.call_args.args[0]
        assert Path(ingested_path).resolve() == target.resolve()

    def test_rejects_path_outside_sandbox(self, tmp_path, monkeypatch):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "secret.pdf"
        outside.write_bytes(b"x")
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(sandbox))

        host = _make_host_with_mocks(sandbox)
        with patch(
            _PATCH_INGEST,
        ) as mock_ingest:
            result = host._add_document(
                {"mode": "path", "file_path": str(outside)},
            )
        assert "error" in result
        # v0.9.16.1 wording — the message names the allowed roots.
        assert "outside the allowed sandbox" in result["error"]
        assert "QDRANT_IMPORT_ALLOWED_ROOT" in result["error"]
        mock_ingest.assert_not_called()

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document(
            {"mode": "path", "file_path": str(tmp_path / "absent.md")},
        )
        assert "error" in result
        assert "not found" in result["error"]

    def test_directory_not_a_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document(
            {"mode": "path", "file_path": str(tmp_path)},
        )
        assert "error" in result
        assert "not a regular file" in result["error"]

    def test_missing_file_path_param(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document({"mode": "path"})
        assert "error" in result
        assert "requires 'file_path'" in result["error"]

    def test_dotdot_traversal_blocked(self, tmp_path, monkeypatch):
        # Sandbox is tmp_path/sandbox; attempt tmp_path/sandbox/../escape.
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        escape = sandbox / ".." / "escape.md"
        escape.write_text("hi", encoding="utf-8")
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(sandbox))

        host = _make_host_with_mocks(sandbox)
        result = host._add_document(
            {"mode": "path", "file_path": str(escape)},
        )
        assert "error" in result
        # v0.9.16.1 wording — the message names the allowed roots.
        assert "outside the allowed sandbox" in result["error"]


# ── _add_document (content mode) ──────────────────────────────


class TestAddDocumentContentMode:
    def test_success_writes_and_ingests(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)

        with patch(
            _PATCH_INGEST,
            return_value=3,
        ) as mock_ingest:
            result = host._add_document({
                "mode": "content",
                "filename": "note.md",
                "content": "# Title\n\nBody paragraph.\n",
            })
        assert result["ok"] is True
        assert result["mode"] == "content"
        assert result["filename"] == "note.md"
        assert result["chunks"] == 3

        # File actually written to disk.
        written = tmp_path / "note.md"
        assert written.exists()
        assert written.read_text(encoding="utf-8") == "# Title\n\nBody paragraph.\n"
        mock_ingest.assert_called_once()

    def test_missing_filename(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document({
            "mode": "content",
            "content": "hi",
        })
        assert "error" in result
        assert "requires 'filename'" in result["error"]

    def test_missing_content(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document({
            "mode": "content",
            "filename": "x.txt",
        })
        assert "error" in result
        assert "requires 'content'" in result["error"]

    def test_content_must_be_string(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document({
            "mode": "content",
            "filename": "x.txt",
            "content": 12345,  # not a str
        })
        assert "error" in result

    def test_path_separator_in_filename_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document({
            "mode": "content",
            "filename": "sub/file.md",
            "content": "x",
        })
        assert "error" in result
        assert "leaf name" in result["error"]

    def test_unsupported_extension_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document({
            "mode": "content",
            "filename": "binary.exe",
            "content": "x",
        })
        assert "error" in result
        assert "unsupported extension" in result["error"]

    def test_overwrite_false_protects_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        existing = tmp_path / "kept.md"
        existing.write_text("ORIGINAL", encoding="utf-8")
        host = _make_host_with_mocks(tmp_path)

        result = host._add_document({
            "mode": "content",
            "filename": "kept.md",
            "content": "NEW",
        })
        assert "error" in result
        assert "already exists" in result["error"]
        assert "overwrite=true" in result["error"]
        # Original content untouched.
        assert existing.read_text(encoding="utf-8") == "ORIGINAL"

    def test_overwrite_true_replaces(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        existing = tmp_path / "kept.md"
        existing.write_text("ORIGINAL", encoding="utf-8")
        host = _make_host_with_mocks(tmp_path)

        with patch(
            _PATCH_INGEST,
            return_value=1,
        ):
            result = host._add_document({
                "mode": "content",
                "filename": "kept.md",
                "content": "NEW",
                "overwrite": True,
            })
        assert result["ok"] is True
        assert existing.read_text(encoding="utf-8") == "NEW"


# ── _add_document (mode validation) ───────────────────────────


class TestAddDocumentModeValidation:
    def test_missing_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document({"file_path": "x"})
        assert "error" in result
        assert "'mode' must be" in result["error"]

    def test_invalid_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._add_document({"mode": "magic", "file_path": "x"})
        assert "error" in result
        assert "'mode' must be" in result["error"]


# ── LocalTool wrapper integration ────────────────────────────


class TestLocalToolWrapperIntegration:
    """The end-to-end LocalTool.__call__ dispatch path that the
    Agent's tool-calling loop uses. Exercises _coerce_arguments +
    _call_args with realistic LLM-shaped payloads."""

    def test_add_document_wrapper_dict_args(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        target = tmp_path / "ok.md"
        target.write_text("hello", encoding="utf-8")
        host = _make_host_with_mocks(tmp_path)
        tool = next(t for t in host.list_tools()
                    if t.name == "add_document")

        with patch(
            _PATCH_INGEST,
            return_value=2,
        ):
            # LLM wraps call under ``{"args": {...}}`` — the
            # common shape we have to normalise.
            result = tool({
                "args": {
                    "mode": "path",
                    "file_path": str(target),
                },
            })
        assert result.is_error is False
        assert "ok" in result.text
        assert "2" in result.text  # chunks value embedded

    def test_update_document_wrapper_dict_args(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path, existing_files=["x.md"])
        tool = next(t for t in host.list_tools()
                    if t.name == "update_document")

        host._store.delete_by_filename.return_value = 4
        with patch(
            _PATCH_INGEST,
            return_value=5,
        ):
            result = tool({
                "args": {"filename": "x.md", "content": "new"},
            })
        assert result.is_error is False
        assert "ok" in result.text


# ── _update_document ────────────────────────────────────────


class TestUpdateDocument:
    def _patched_host(self, tmp_path, existing):
        host = _make_host_with_mocks(tmp_path, existing_files=existing)
        host._store.delete_by_filename.return_value = 4
        return host

    def test_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = self._patched_host(tmp_path, existing=["a.md"])
        with patch(
            _PATCH_INGEST,
            return_value=6,
        ) as mock_ingest:
            result = host._update_document({
                "filename": "a.md",
                "content": "new body\n",
            })
        assert result["ok"] is True
        assert result["filename"] == "a.md"
        assert result["chunks_deleted"] == 4
        assert result["chunks_added"] == 6
        # Disk side effect: file written to sandbox.
        assert (tmp_path / "a.md").read_text(encoding="utf-8") == "new body\n"
        # Delete was called first, before the re-ingest.
        host._store.delete_by_filename.assert_called_once_with("a.md")
        mock_ingest.assert_called_once()

    def test_missing_filename_in_collection(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path, existing_files=["b.md"])
        result = host._update_document({
            "filename": "ghost.md",
            "content": "new",
        })
        assert "error" in result
        assert result.get("missing") is True
        assert "not in the Qdrant collection" in result["error"]

    def test_missing_filename_param(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._update_document({"content": "x"})
        assert "error" in result
        assert "'filename' is required" in result["error"]

    def test_missing_content_param(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._update_document({"filename": "x.md"})
        assert "error" in result
        assert "'content' must be a string" in result["error"]

    def test_filename_with_path_separator_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._update_document({
            "filename": "sub/x.md",
            "content": "y",
        })
        assert "error" in result
        assert "leaf basename" in result["error"]

    def test_unsupported_extension_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path, existing_files=["a.exe"])
        result = host._update_document({
            "filename": "a.exe",
            "content": "y",
        })
        assert "error" in result
        assert "unsupported extension" in result["error"]

    def test_ingest_failure_after_delete_reports_recovery_hint(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = self._patched_host(tmp_path, existing=["a.md"])
        with patch(
            _PATCH_INGEST,
            side_effect=RuntimeError("qdrant down"),
        ):
            result = host._update_document({
                "filename": "a.md",
                "content": "new",
            })
        assert "error" in result
        assert "delete ok" in result["error"]
        assert "add_document to recover" in result["error"]


# ── LLM-style error reporting: result is a dict, not raised ─


class TestNoExceptionsLeak:
    """``_add_document`` / ``update_document`` must NEVER raise —
    they convert any internal failure to ``{"error": ...}`` so
    the LocalTool wrapper can format the failure for the LLM
    without crashing the chat loop."""

    def test_add_document_path_pipeline_exception(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        target = tmp_path / "x.md"
        target.write_text("x", encoding="utf-8")
        host = _make_host_with_mocks(tmp_path)
        with patch(
            _PATCH_INGEST,
            side_effect=RuntimeError("boom"),
        ):
            result = host._add_document(
                {"mode": "path", "file_path": str(target)},
            )
        assert "error" in result
        assert "boom" in result["error"]

    def test_add_document_content_pipeline_exception(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        with patch(
            _PATCH_INGEST,
            side_effect=RuntimeError("boom"),
        ):
            result = host._add_document({
                "mode": "content",
                "filename": "x.md",
                "content": "y",
            })
        assert "error" in result
        assert "boom" in result["error"]


# ── sanity: supported extensions match ingest config ────────


def test_allowed_exts_includes_canonical_text():
    """The sandbox writer should accept the same extensions the
    ingest pipeline advertises, otherwise a successful write
    would silently produce a file the loader can't read."""
    # Spot-check the most common ones — full list is in
    # config.ingest.supported_extensions, mirrored here.
    for ext in (".txt", ".md", ".pdf", ".docx", ".pptx", ".epub",
                ".py", ".js", ".json", ".csv", ".html"):
        assert ext in _ADD_DOC_ALLOWED_EXTS, f"missing {ext}"  
 
