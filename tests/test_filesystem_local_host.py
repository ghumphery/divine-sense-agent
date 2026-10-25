"""Tests for ``FilesystemLocalHost`` (v0.9.3+).

Covers:

* tool surface (15 tools, correct ``server_name``)
* read sandbox vs write sandbox separation
* handler happy paths (list_dir / stat_file / read_file /
  read_multiple / search_files / get_tree / search_content /
  write_file / append_file / delete_file / move_file / mkdir)
* sandbox escape (``..`` traversal, absolute path outside
  root, symlink pointing outside)
* binary-file refusal in ``read_file``
* dangerous executable extension refusal in ``write_file`` /
  ``append_file``
* atomic write guarantees
* non-dict / missing-args error envelopes
* ``McpManager.from_config`` auto-registers the host

All file I/O goes through ``tmp_path``; no real Qdrant / LLM
needed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.mcp_client.host import (
    FilesystemLocalHost,
    _FS_BINARY_EXTENSIONS,
    _FS_DANGEROUS_WRITE_EXTS,
    _FS_READ_HARD_MAX_BYTES,
    _FS_TEXT_EXTENSIONS,
    _is_within,
    _resolve_filesystem_root,
    _fs_resolve_path,
    _fs_walk,
)
from src.mcp_client.manager import McpManager


# ── fixtures ────────────────────────────────────────────


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """Pin both filesystem roots under tmp_path."""
    r = tmp_path / "r"
    w = tmp_path / "w"
    r.mkdir()
    w.mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(r))
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(w))
    return {"read": r, "write": w}


@pytest.fixture()
def host(roots):
    return FilesystemLocalHost()


# ── tool-surface registration ────────────────────────────


class TestToolSurface:
    def test_fifteen_tools(self, host):
        names = {t.name for t in host.list_tools()}
        expected = {
            "list_dir", "stat_file", "read_file", "read_multiple",
            "search_files", "get_tree", "search_content",
            "write_file", "append_file", "delete_file", "move_file",
            "mkdir", "copy_file", "diff_files", "patch_file",
        }
        assert names == expected

    def test_all_use_filesystem_local_server(self, host):
        for t in host.list_tools():
            assert t.server_name == "filesystem-local", t.name

    def test_no_qdrant_or_web_or_scheduler(self, host):
        names = {t.name for t in host.list_tools()}
        for forbidden in (
            "search_documents", "web_search", "list_mcp_tools",
            "add_scheduled_task",
        ):
            assert forbidden not in names

    def test_each_tool_has_handler(self, host):
        for t in host.list_tools():
            assert callable(t._handler), t.name
            assert t._handler.__name__ == f"_{t.name}", t.name

    def test_input_schemas_well_formed(self, host):
        for t in host.list_tools():
            schema = t.input_schema
            assert schema["type"] == "object"
            assert "properties" in schema
            if t.name in (
                "write_file", "append_file", "move_file",
                "stat_file", "delete_file", "mkdir",
                "read_file", "search_content", "search_files",
            ):
                assert "required" in schema, t.name


# ── root resolution ──────────────────────────────────────


class TestResolveFilesystemRoot:
    def test_explicit_env_wins(self, tmp_path, monkeypatch):
        target = tmp_path / "explicit"
        target.mkdir()
        monkeypatch.setenv(
            "QDRANT_FILESYSTEM_READ_ROOT", str(target),
        )
        assert _resolve_filesystem_root(
            "QDRANT_FILESYSTEM_READ_ROOT",
        ) == target.resolve()

    def test_falls_back_to_watch_dir(self, tmp_path, monkeypatch):
        wd = tmp_path / "wd"
        wd.mkdir()
        monkeypatch.delenv(
            "QDRANT_FILESYSTEM_READ_ROOT", raising=False,
        )
        monkeypatch.setenv("WATCH_DIR", str(wd))
        assert _resolve_filesystem_root(
            "QDRANT_FILESYSTEM_READ_ROOT",
        ) == wd.resolve()

    def test_creates_missing_root(self, tmp_path, monkeypatch):
        target = tmp_path / "fresh"
        monkeypatch.setenv(
            "QDRANT_FILESYSTEM_READ_ROOT", str(target),
        )
        assert not target.exists()
        _resolve_filesystem_root("QDRANT_FILESYSTEM_READ_ROOT")
        assert target.exists()

    def test_relative_anchored_at_workspace(self, monkeypatch):
        monkeypatch.delenv("WATCH_DIR", raising=False)
        monkeypatch.setenv(
            "QDRANT_FILESYSTEM_READ_ROOT", "documents",
        )
        p = _resolve_filesystem_root(
            "QDRANT_FILESYSTEM_READ_ROOT",
        )
        assert p.is_absolute()
        assert (p / "..").resolve() != p


class TestIsWithin:
    def test_inside(self, tmp_path):
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        assert _is_within(child, tmp_path)

    def test_outside(self, tmp_path):
        sibling = tmp_path.parent / "totally_outside"
        sibling.mkdir(exist_ok=True)
        assert not _is_within(sibling, tmp_path)

    def test_dotdot_escape(self, tmp_path):
        inside = tmp_path / "a"
        inside.mkdir()
        escape = (inside / ".." / "evil").resolve()
        assert not _is_within(escape, inside)


class TestFsResolvePath:
    def test_absolute_path_within_root(self, tmp_path):
        f = tmp_path / "x.md"
        f.write_text("hi")
        assert _fs_resolve_path(
            str(f), tmp_path, must_be_file=True,
        ) == f.resolve()

    def test_relative_path_joined(self, tmp_path):
        assert _fs_resolve_path(
            "a/b.md", tmp_path, allow_relative=True,
        ) == (tmp_path / "a" / "b.md").resolve()

    def test_relative_path_without_flag_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="not absolute"):
            _fs_resolve_path("foo.md", tmp_path)

    def test_outside_sandbox_rejected(self, tmp_path):
        other = tmp_path.parent / "evil.md"
        with pytest.raises(ValueError, match="outside"):
            _fs_resolve_path(str(other), tmp_path)

    def test_dotdot_escape_rejected(self, tmp_path):
        escape = (tmp_path / ".." / "evil").resolve()
        with pytest.raises(ValueError):
            _fs_resolve_path(str(escape), tmp_path)

    def test_none_raises(self, tmp_path):
        with pytest.raises(ValueError, match="required"):
            _fs_resolve_path(None, tmp_path)

    def test_must_be_file(self, tmp_path):
        d = tmp_path / "dir"
        d.mkdir()
        with pytest.raises(ValueError, match="not a regular file"):
            _fs_resolve_path(
                str(d), tmp_path, must_be_file=True,
            )

    def test_must_be_dir(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hi")
        with pytest.raises(ValueError, match="not a directory"):
            _fs_resolve_path(
                str(f), tmp_path, must_be_dir=True,
            )

    def test_must_exist(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _fs_resolve_path(
                str(tmp_path / "ghost"), tmp_path,
                must_exist=True,
            )


class TestFsWalk:
    def test_max_depth(self, tmp_path):
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        (tmp_path / "a" / "b" / "c" / "leaf.txt").write_text("x")
        # depth 0 = only the root itself.
        paths, truncated = _fs_walk(
            tmp_path, max_entries=100, max_depth=0,
            include_dirs=True, include_files=True,
        )
        assert len(paths) == 1
        assert paths[0] == tmp_path
        # depth 1 = root + direct children.
        paths, _ = _fs_walk(
            tmp_path, max_entries=100, max_depth=1,
            include_dirs=True, include_files=False,
        )
        assert len(paths) == 2
        assert tmp_path in paths

    def test_max_entries_truncates(self, tmp_path):
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        paths, truncated = _fs_walk(
            tmp_path, max_entries=3, max_depth=99,
            include_files=True, include_dirs=False,
        )
        assert len(paths) == 3
        assert truncated is True

    def test_symlink_escape_ignored(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("secret")
        sandbox = tmp_path / "s"
        sandbox.mkdir()
        link = sandbox / "escape"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported on this FS")
        paths, _ = _fs_walk(
            sandbox, max_entries=100, max_depth=99,
            include_files=True,
        )
        names = [p.name for p in paths]
        assert "escape" not in names


# ── READ handlers ───────────────────────────────────────


class TestListDir:
    def test_default_lists_root(self, host, roots):
        (roots["read"] / "a.md").write_text("a")
        (roots["read"] / "b.txt").write_text("b")
        r = host._list_dir({})
        assert r["count"] == 2
        assert {e["name"] for e in r["entries"]} == {"a.md", "b.txt"}

    def test_pattern_filter(self, host, roots):
        (roots["read"] / "a.md").write_text("a")
        (roots["read"] / "b.txt").write_text("b")
        r = host._list_dir({"pattern": "*.md"})
        assert r["count"] == 1
        assert r["entries"][0]["name"] == "a.md"

    def test_explicit_path(self, host, roots):
        sub = roots["read"] / "sub"
        sub.mkdir()
        (sub / "deep.md").write_text("d")
        r = host._list_dir({"path": "sub"})
        assert r["count"] == 1
        assert r["entries"][0]["name"] == "deep.md"

    def test_include_dirs_false(self, host, roots):
        (roots["read"] / "sub").mkdir()
        (roots["read"] / "a.md").write_text("a")
        r = host._list_dir({"include_dirs": False})
        names = [e["name"] for e in r["entries"]]
        assert "sub" not in names
        assert "a.md" in names

    def test_sandbox_escape_rejected(self, host, tmp_path):
        outside = tmp_path.parent / "evil.md"
        r = host._list_dir({"path": str(outside)})
        assert "error" in r
        assert "outside" in r["error"]

    def test_nonexistent_dir(self, host):
        r = host._list_dir({"path": "nope"})
        assert "error" in r

    def test_non_dict_args(self, host):
        r = host._list_dir("not a dict")
        assert "error" in r


class TestStatFile:
    def test_existing_file(self, host, roots):
        f = roots["read"] / "a.md"
        f.write_text("hello")
        r = host._stat_file({"path": "a.md"})
        assert r["exists"] is True
        assert r["type"] == "file"
        assert r["size_bytes"] == 5
        assert r["is_symlink"] is False

    def test_missing(self, host):
        r = host._stat_file({"path": "ghost.md"})
        assert r["exists"] is False
        assert r["path"].endswith("ghost.md")

    def test_directory(self, host, roots):
        (roots["read"] / "sub").mkdir()
        r = host._stat_file({"path": "sub"})
        assert r["type"] == "directory"
        assert r["size_bytes"] == 0

    def test_outside_sandbox(self, host, tmp_path):
        r = host._stat_file({"path": str(tmp_path.parent)})
        assert "error" in r

    def test_missing_path(self, host):
        r = host._stat_file({})
        assert "error" in r
        assert "'path' is required" in r["error"]


class TestReadFile:
    def test_basic(self, host, roots):
        (roots["read"] / "a.md").write_text(
            "hello world", encoding="utf-8",
        )
        r = host._read_file({"path": "a.md"})
        assert r["content"] == "hello world"
        assert r["encoding"] == "utf-8"
        assert r["truncated"] is False
        assert r["size_bytes"] == 11

    def test_offset_and_max_bytes(self, host, roots):
        (roots["read"] / "a.md").write_text(
            "0123456789", encoding="utf-8",
        )
        r = host._read_file(
            {"path": "a.md", "offset_bytes": 3, "max_bytes": 4},
        )
        assert r["content"] == "3456"
        assert r["truncated"] is True
        assert r["next_offset"] == 7

    def test_binary_extension_rejected(self, host, roots):
        (roots["read"] / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        r = host._read_file({"path": "x.png"})
        assert "error" in r
        assert "binary" in r["error"]

    def test_max_bytes_hard_cap(self, host, roots):
        (roots["read"] / "a.txt").write_text("x")
        r = host._read_file(
            {
                "path": "a.txt",
                "max_bytes": _FS_READ_HARD_MAX_BYTES + 1,
            },
        )
        assert "error" in r
        assert "capped" in r["error"]

    def test_missing(self, host):
        r = host._read_file({"path": "ghost.md"})
        assert "error" in r

    def test_outside_sandbox(self, host, tmp_path):
        r = host._read_file({"path": str(tmp_path.parent)})
        assert "error" in r

    def test_negative_offset(self, host, roots):
        (roots["read"] / "a.md").write_text("hi")
        r = host._read_file({"path": "a.md", "offset_bytes": -1})
        assert "error" in r


class TestReadMultiple:
    def test_two_files(self, host, roots):
        (roots["read"] / "a.md").write_text("A", encoding="utf-8")
        (roots["read"] / "b.txt").write_text("B", encoding="utf-8")
        r = host._read_multiple({"paths": ["a.md", "b.txt"]})
        assert r["count"] == 2
        contents = sorted(f["content"] for f in r["files"])
        assert contents == ["A", "B"]

    def test_per_file_error_doesnt_abort(self, host, roots):
        (roots["read"] / "good.md").write_text("hi", encoding="utf-8")
        (roots["read"] / "bad.png").write_bytes(b"\x89PNG")
        r = host._read_multiple(
            {"paths": ["good.md", "bad.png", "ghost.md"]},
        )
        assert r["count"] == 3
        with_content = [f for f in r["files"] if "content" in f]
        assert len(with_content) == 1
        assert with_content[0]["content"] == "hi"

    def test_max_bytes_each(self, host, roots):
        (roots["read"] / "big.txt").write_text(
            "x" * 100, encoding="utf-8",
        )
        r = host._read_multiple(
            {"paths": ["big.txt"], "max_bytes_each": 10},
        )
        assert r["files"][0]["returned_bytes"] == 10

    def test_max_bytes_each_too_big_clamped(self, host, roots):
        big = _FS_READ_HARD_MAX_BYTES + 100
        (roots["read"] / "x.txt").write_text("hi", encoding="utf-8")
        r = host._read_multiple(
            {"paths": ["x.txt"], "max_bytes_each": big},
        )
        assert "error" not in r
        assert r["files"][0]["content"] == "hi"

    def test_paths_not_list(self, host):
        r = host._read_multiple({"paths": "oops"})
        assert "error" in r


# ── SEARCH handlers ──────────────────────────────────────


class TestSearchFiles:
    def test_glob(self, host, roots):
        (roots["read"] / "a.md").write_text("a")
        (roots["read"] / "b.txt").write_text("b")
        (roots["read"] / "c.md").write_text("c")
        r = host._search_files({"pattern": "*.md"})
        assert r["match_count"] == 2
        names = {Path(m["path"]).name for m in r["matches"]}
        assert names == {"a.md", "c.md"}

    def test_recursive(self, host, roots):
        sub = roots["read"] / "deep"
        sub.mkdir()
        (sub / "x.md").write_text("x")
        (roots["read"] / "y.md").write_text("y")
        r = host._search_files({"pattern": "*.md", "recursive": True})
        assert r["match_count"] == 2

    def test_non_recursive(self, host, roots):
        sub = roots["read"] / "deep"
        sub.mkdir()
        (sub / "x.md").write_text("x")
        (roots["read"] / "y.md").write_text("y")
        r = host._search_files(
            {"pattern": "*.md", "recursive": False},
        )
        names = {Path(m["path"]).name for m in r["matches"]}
        assert "x.md" not in names
        assert "y.md" in names

    def test_max_results(self, host, roots):
        for i in range(5):
            (roots["read"] / f"f{i}.md").write_text(str(i))
        r = host._search_files(
            {"pattern": "*.md", "max_results": 2},
        )
        assert r["match_count"] == 2
        assert r["truncated"] is True

    def test_no_pattern(self, host):
        r = host._search_files({})
        assert "error" in r


class TestGetTree:
    def test_basic(self, host, roots):
        (roots["read"] / "a.md").write_text("a")
        (roots["read"] / "sub").mkdir()
        (roots["read"] / "sub" / "b.md").write_text("b")
        r = host._get_tree({})
        assert r["total_count"] >= 4

    def test_max_depth(self, host, roots):
        deep = roots["read"] / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "leaf.md").write_text("x")
        # depth 1 = root + 1 subdir.
        r = host._get_tree({"max_depth": 1, "include_files": False})
        assert r["total_count"] == 2
        assert r["max_depth"] == 1

    def test_include_dirs_false(self, host, roots):
        (roots["read"] / "sub").mkdir()
        r = host._get_tree({"include_dirs": False})
        names = [e["name"] for e in r["entries"]]
        assert "sub" not in names

    def test_invalid_max_depth(self, host):
        r = host._get_tree({"max_depth": -1})
        assert "error" in r


class TestSearchContent:
    def test_plain_match(self, host, roots):
        src = roots["read"] / "src"
        src.mkdir()
        (src / "hello.py").write_text(
            "def hello():\n    print('hello world')\n",
            encoding="utf-8",
        )
        r = host._search_content({"path": "src", "query": "hello"})
        assert r["match_count"] >= 1
        assert r["files_scanned"] == 1

    def test_case_insensitive_default(self, host, roots):
        (roots["read"] / "a.txt").write_text(
            "Hello World", encoding="utf-8",
        )
        r = host._search_content({"query": "hello"})
        assert r["match_count"] == 1

    def test_case_sensitive(self, host, roots):
        (roots["read"] / "a.txt").write_text(
            "Hello World", encoding="utf-8",
        )
        r = host._search_content(
            {"query": "hello", "case_sensitive": True},
        )
        assert r["match_count"] == 0

    def test_regex(self, host, roots):
        (roots["read"] / "a.txt").write_text(
            "foo123bar", encoding="utf-8",
        )
        r = host._search_content(
            {"query": r"foo\d+bar", "is_regex": True},
        )
        assert r["match_count"] == 1

    def test_bad_regex_rejected(self, host, roots):
        (roots["read"] / "a.txt").write_text("hi", encoding="utf-8")
        r = host._search_content(
            {"query": "[unclosed", "is_regex": True},
        )
        assert "error" in r
        assert "regex" in r["error"]

    def test_binary_file_skipped(self, host, roots):
        (roots["read"] / "doc.txt").write_text(
            "needle here", encoding="utf-8",
        )
        (roots["read"] / "blob.png").write_bytes(b"needle in binary")
        r = host._search_content({"query": "needle"})
        assert r["match_count"] == 1
        assert r["files_scanned"] == 1

    def test_file_pattern_filter(self, host, roots):
        (roots["read"] / "a.py").write_text("hit", encoding="utf-8")
        (roots["read"] / "a.md").write_text("hit", encoding="utf-8")
        r = host._search_content(
            {"query": "hit", "file_pattern": "*.py"},
        )
        assert r["match_count"] == 1

    def test_no_query(self, host):
        r = host._search_content({})
        assert "error" in r

    def test_context_chars_truncates_line(self, host, roots):
        (roots["read"] / "a.txt").write_text(
            "x" * 200 + "MARKER" + "y" * 200,
            encoding="utf-8",
        )
        r = host._search_content(
            {"query": "MARKER", "context_chars": 20},
        )
        snippet = r["matches"][0]["line"]
        assert "MARKER" in snippet
        assert len(snippet) < 50


# ── WRITE handlers ───────────────────────────────────────


class TestWriteFile:
    def test_creates_file(self, host, roots):
        r = host._write_file({"path": "new.md", "content": "draft"})
        assert r["ok"] is True
        assert (roots["write"] / "new.md").read_text() == "draft"

    def test_overwrite_refused_by_default(self, host, roots):
        (roots["write"] / "x.md").write_text("old")
        r = host._write_file({"path": "x.md", "content": "new"})
        assert "error" in r
        assert (roots["write"] / "x.md").read_text() == "old"

    def test_overwrite_true_replaces(self, host, roots):
        (roots["write"] / "x.md").write_text("old")
        r = host._write_file(
            {"path": "x.md", "content": "new", "overwrite": True},
        )
        assert r["ok"] is True
        assert (roots["write"] / "x.md").read_text() == "new"

    def test_missing_parent(self, host, roots):
        r = host._write_file(
            {"path": "missing/sub/x.md", "content": "x"},
        )
        assert "error" in r
        assert "mkdir" in r["error"]

    def test_dangerous_extension_rejected(self, host, roots):
        for ext in (".exe", ".bat", ".sh", ".ps1"):
            r = host._write_file(
                {"path": f"hack{ext}", "content": "x"},
            )
            assert "error" in r, ext
            assert "dangerous" in r["error"], ext

    def test_non_string_content(self, host):
        r = host._write_file({"path": "x.md", "content": 123})
        assert "error" in r

    def test_sandbox_escape(self, host, tmp_path):
        r = host._write_file(
            {
                "path": str(tmp_path.parent / "evil.md"),
                "content": "x",
            },
        )
        assert "error" in r


class TestAppendFile:
    def test_creates_when_missing(self, host, roots):
        r = host._append_file({"path": "log.txt", "content": "hello"})
        assert r["ok"] is True
        assert (roots["write"] / "log.txt").read_text() == "hello"

    def test_appends_existing(self, host, roots):
        (roots["write"] / "log.txt").write_text("hi", encoding="utf-8")
        r = host._append_file({"path": "log.txt", "content": "+bye"})
        assert r["ok"] is True
        assert (roots["write"] / "log.txt").read_text() == "hi+bye"

    def test_dangerous_extension(self, host, roots):
        r = host._append_file({"path": "hacker.sh", "content": "rm -rf /"})
        assert "error" in r
        assert "dangerous" in r["error"]

    def test_parent_missing(self, host):
        r = host._append_file({"path": "nope/sub/x.txt", "content": "x"})
        assert "error" in r


class TestDeleteFile:
    def test_deletes_file(self, host, roots):
        (roots["write"] / "doomed.txt").write_text("bye")
        r = host._delete_file({"path": "doomed.txt"})
        assert r["ok"] is True
        assert not (roots["write"] / "doomed.txt").exists()

    def test_missing(self, host):
        r = host._delete_file({"path": "ghost.txt"})
        assert "error" in r

    def test_directory_rejected(self, host, roots):
        (roots["write"] / "sub").mkdir()
        r = host._delete_file({"path": "sub"})
        assert "error" in r
        assert "directory" in r["error"]

    def test_sandbox_escape(self, host, tmp_path):
        r = host._delete_file({"path": str(tmp_path.parent)})
        assert "error" in r


class TestMoveFile:
    def test_basic_rename(self, host, roots):
        src = roots["write"] / "from.txt"
        src.write_text("hi")
        r = host._move_file({"src": "from.txt", "dst": "to.txt"})
        assert r["ok"] is True
        assert (roots["write"] / "to.txt").exists()
        assert not src.exists()

    def test_overwrite_false_default(self, host, roots):
        (roots["write"] / "a.txt").write_text("src")
        (roots["write"] / "b.txt").write_text("dst")
        r = host._move_file({"src": "a.txt", "dst": "b.txt"})
        assert "error" in r
        assert (roots["write"] / "a.txt").exists()
        assert (roots["write"] / "b.txt").read_text() == "dst"

    def test_overwrite_true(self, host, roots):
        (roots["write"] / "a.txt").write_text("src")
        (roots["write"] / "b.txt").write_text("dst")
        r = host._move_file(
            {"src": "a.txt", "dst": "b.txt", "overwrite": True},
        )
        assert r["ok"] is True
        assert (roots["write"] / "b.txt").read_text() == "src"

    def test_directory_rejected(self, host, roots):
        (roots["write"] / "sub").mkdir()
        r = host._move_file({"src": "sub", "dst": "newsub"})
        assert "error" in r
        assert "directories" in r["error"]

    def test_missing_source(self, host):
        r = host._move_file({"src": "ghost", "dst": "new"})
        assert "error" in r

    def test_missing_parent(self, host, roots):
        (roots["write"] / "a.txt").write_text("x")
        r = host._move_file({"src": "a.txt", "dst": "nope/x.txt"})
        assert "error" in r


class TestMkdir:
    def test_basic(self, host, roots):
        r = host._mkdir({"path": "newdir"})
        assert r["ok"] is True
        assert r["created"] is True
        assert (roots["write"] / "newdir").is_dir()

    def test_parents(self, host, roots):
        r = host._mkdir({"path": "deep/nested/path"})
        assert r["ok"] is True
        assert (roots["write"] / "deep" / "nested" / "path").is_dir()

    def test_existing_default_ok(self, host, roots):
        (roots["write"] / "exists").mkdir()
        r = host._mkdir({"path": "exists"})
        assert r["ok"] is True
        assert r["created"] is False

    def test_existing_with_exist_ok_false(self, host, roots):
        (roots["write"] / "exists").mkdir()
        r = host._mkdir({"path": "exists", "exist_ok": False})
        assert "error" in r

    def test_path_is_file_rejected(self, host, roots):
        (roots["write"] / "x.txt").write_text("hi")
        r = host._mkdir({"path": "x.txt"})
        assert "error" in r

    def test_sandbox_escape(self, host, tmp_path):
        r = host._mkdir({"path": str(tmp_path.parent / "evil")})
        assert "error" in r


# ── copy_file (v0.9.4+) ────────────────────────────────


class TestCopyFile:
    def test_basic_copy(self, host, roots):
        src = roots["write"] / "src.txt"
        src.write_text("hello copy", encoding="utf-8")
        r = host._copy_file({"src": "src.txt", "dst": "dst.txt"})
        assert r["ok"] is True, r
        assert r["bytes_copied"] == 10
        assert r["relative_src"] == "src.txt"
        assert r["relative_dst"] == "dst.txt"
        assert r["overwrite"] is False
        assert r["follow_symlinks"] is True
        assert src.read_text(encoding="utf-8") == "hello copy"
        assert (roots["write"] / "dst.txt").read_text(
            encoding="utf-8"
        ) == "hello copy"

    def test_preserves_source_after_overwrite(
        self, host, roots,
    ):
        src = roots["write"] / "src.txt"
        src.write_text("current", encoding="utf-8")
        dst = roots["write"] / "dst.txt"
        dst.write_text("stale", encoding="utf-8")
        r = host._copy_file({
            "src": "src.txt", "dst": "dst.txt", "overwrite": True,
        })
        assert r["ok"] is True
        assert dst.read_text(encoding="utf-8") == "current"
        assert src.read_text(encoding="utf-8") == "current"

    def test_refuses_overwrite_without_flag(self, host, roots):
        src = roots["write"] / "src.txt"
        src.write_text("new", encoding="utf-8")
        dst = roots["write"] / "dst.txt"
        dst.write_text("old", encoding="utf-8")
        r = host._copy_file({"src": "src.txt", "dst": "dst.txt"})
        assert "error" in r
        assert "destination exists" in r["error"]
        assert dst.read_text(encoding="utf-8") == "old"

    def test_missing_source(self, host, roots):
        r = host._copy_file({
            "src": "missing.txt", "dst": "dst.txt",
        })
        assert "error" in r
        assert "source not found" in r["error"]

    def test_source_is_directory_rejected(self, host, roots):
        (roots["write"] / "somedir").mkdir()
        r = host._copy_file({
            "src": "somedir", "dst": "somedir_copy",
        })
        assert "error" in r
        assert "refuses to copy directories" in r["error"]

    def test_destination_is_directory_rejected(
        self, host, roots,
    ):
        (roots["write"] / "src.txt").write_text("x")
        (roots["write"] / "outdir").mkdir()
        r = host._copy_file({
            "src": "src.txt", "dst": "outdir",
        })
        assert "error" in r
        assert "is a directory" in r["error"]

    def test_dangerous_source_extension(self, host, roots):
        src = roots["write"] / "src.exe"
        src.write_bytes(b"MZ\x00")
        r = host._copy_file({
            "src": "src.exe", "dst": "dst.txt",
        })
        assert "error" in r
        assert "dangerous source" in r["error"]

    def test_dangerous_destination_extension(self, host, roots):
        (roots["write"] / "src.txt").write_text("x")
        r = host._copy_file({
            "src": "src.txt", "dst": "dst.bat",
        })
        assert "error" in r
        assert "dangerous extension" in r["error"]

    def test_destination_parent_missing(self, host, roots):
        (roots["write"] / "src.txt").write_text("x")
        r = host._copy_file({
            "src": "src.txt", "dst": "missingdir/dst.txt",
        })
        assert "error" in r
        assert "parent missing" in r["error"]

    def test_src_and_dst_same_rejected(self, host, roots):
        (roots["write"] / "src.txt").write_text("x")
        r = host._copy_file({
            "src": "src.txt", "dst": "src.txt",
        })
        assert "error" in r
        assert "same file" in r["error"]

    def test_bytes_copied_reflects_size(self, host, roots):
        (roots["write"] / "src.txt").write_text("x" * 4096)
        r = host._copy_file({
            "src": "src.txt", "dst": "dst.txt",
        })
        assert r["ok"] is True
        assert r["bytes_copied"] == 4096

    def test_sandbox_escape_src(self, host, tmp_path):
        outside = tmp_path.parent / "evil.txt"
        outside.write_text("nope")
        try:
            r = host._copy_file({
                "src": str(outside), "dst": "dst.txt",
            })
            assert "error" in r
        finally:
            outside.unlink(missing_ok=True)

    def test_sandbox_escape_dst(self, host, tmp_path):
        write_root = host._write_root
        (write_root / "src.txt").write_text("x")
        outside = tmp_path.parent / "evil_dst.txt"
        try:
            r = host._copy_file({
                "src": "src.txt", "dst": str(outside),
            })
            assert "error" in r
            assert not outside.exists()
        finally:
            outside.unlink(missing_ok=True)

    def test_follow_symlinks_false_rejects_outside(
        self, host, roots, tmp_path,
    ):
        outside = tmp_path / "outside_file.txt"
        outside.write_text("secret")
        link = roots["write"] / "link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this fs")
        r = host._copy_file({
            "src": "link.txt", "dst": "copy_link.txt",
            "follow_symlinks": False,
        })
        assert "error" in r
        assert "symlink" in r["error"].lower()

    def test_follow_symlinks_true_follows(
        self, host, roots,
    ):
        target = roots["write"] / "real.txt"
        target.write_text("data")
        link = roots["write"] / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this fs")
        r = host._copy_file({
            "src": "link.txt", "dst": "copy_link.txt",
        })
        assert r["ok"] is True, r
        assert (roots["write"] / "copy_link.txt").read_text(
            encoding="utf-8"
        ) == "data"

    def test_dangerous_extensions_full_list(self, host, roots):
        (roots["write"] / "src.txt").write_text("x")
        for ext in (
            ".exe", ".dll", ".so", ".dylib", ".bat", ".cmd",
            ".sh", ".ps1", ".vbs", ".js", ".wsf", ".scr",
            ".com", ".cpl",
        ):
            r = host._copy_file({
                "src": "src.txt", "dst": f"dst{ext}",
            })
            assert "error" in r, f"should reject {ext}"
            assert "dangerous" in r["error"].lower(), ext

    def test_create_destination_subdirs_via_mkdir_first(
        self, host, roots,
    ):
        (roots["write"] / "src.txt").write_text("hi")
        host._mkdir({"path": "sub/deep"})
        r = host._copy_file({
            "src": "src.txt", "dst": "sub/deep/dst.txt",
        })
        assert r["ok"] is True
        assert (roots["write"] / "sub" / "deep" / "dst.txt").read_text(
            encoding="utf-8"
        ) == "hi"

    def test_overwrite_replaces_existing(self, host, roots):
        (roots["write"] / "src.txt").write_text("newer")
        (roots["write"] / "dst.txt").write_text("older")
        r = host._copy_file({
            "src": "src.txt", "dst": "dst.txt", "overwrite": True,
        })
        assert r["ok"] is True
        assert (roots["write"] / "dst.txt").read_text(
            encoding="utf-8"
        ) == "newer"
        assert r["overwrite"] is True

    def test_missing_src_field(self, host):
        r = host._copy_file({"dst": "x.txt"})
        assert "error" in r
        assert "'src' is required" in r["error"]

    def test_missing_dst_field(self, host):
        r = host._copy_file({"src": "x.txt"})
        assert "error" in r
        assert "'dst' is required" in r["error"]

    def test_non_dict_args(self, host):
        assert "error" in host._copy_file(None)
        assert "error" in host._copy_file("oops")
        assert "error" in host._copy_file(42)

    def test_write_root_in_response(self, host, roots):
        (roots["write"] / "src.txt").write_text("x")
        r = host._copy_file({
            "src": "src.txt", "dst": "dst.txt",
        })
        assert r["write_root"] == roots["write"].as_posix()


# ── diff_files (v0.9.4+) ───────────────────────────────


class TestDiffFiles:
    def test_basic_diff(self, host, roots):
        (roots["read"] / "a.txt").write_text(
            "line 1\nline 2\nline 3\n", encoding="utf-8",
        )
        (roots["read"] / "b.txt").write_text(
            "line 1\nline TWO\nline 3\n", encoding="utf-8",
        )
        r = host._diff_files({"a": "a.txt", "b": "b.txt"})
        assert r["ok"] is True
        assert r["identical"] is False
        assert r["hunks"] >= 1
        assert "-line 2" in r["diff"]
        assert "+line TWO" in r["diff"]
        assert r["a_path"].endswith("a.txt")
        assert r["b_path"].endswith("b.txt")

    def test_identical_files(self, host, roots):
        (roots["read"] / "a.txt").write_text("same\n", encoding="utf-8")
        (roots["read"] / "b.txt").write_text("same\n", encoding="utf-8")
        r = host._diff_files({"a": "a.txt", "b": "b.txt"})
        assert r["ok"] is True
        assert r["identical"] is True
        assert r["hunks"] == 0
        assert r["diff"] == ""

    def test_context_zero(self, host, roots):
        (roots["read"] / "a.txt").write_text(
            "x\ny\nz\n", encoding="utf-8",
        )
        (roots["read"] / "b.txt").write_text(
            "x\nY\nz\n", encoding="utf-8",
        )
        r = host._diff_files({
            "a": "a.txt", "b": "b.txt", "context": 0,
        })
        assert r["ok"] is True
        assert r["context"] == 0

    def test_context_invalid_rejected(self, host, roots):
        (roots["read"] / "a.txt").write_text("x", encoding="utf-8")
        (roots["read"] / "b.txt").write_text("y", encoding="utf-8")
        for bad in (-1, 101, "three", None):
            r = host._diff_files({
                "a": "a.txt", "b": "b.txt", "context": bad,
            })
            assert "error" in r, bad

    def test_max_output_bytes_must_be_positive(self, host, roots):
        (roots["read"] / "a.txt").write_text("x", encoding="utf-8")
        (roots["read"] / "b.txt").write_text("y", encoding="utf-8")
        for bad in (0, -10, "lots"):
            r = host._diff_files({
                "a": "a.txt", "b": "b.txt",
                "max_output_bytes": bad,
            })
            assert "error" in r, bad

    def test_truncation_flag(self, host, roots):
        a_lines = "\n".join(f"line {i}" for i in range(200)) + "\n"
        b_lines = "\n".join(f"LINE {i}" for i in range(200)) + "\n"
        (roots["read"] / "a.txt").write_text(a_lines, encoding="utf-8")
        (roots["read"] / "b.txt").write_text(b_lines, encoding="utf-8")
        r = host._diff_files({
            "a": "a.txt", "b": "b.txt",
            "max_output_bytes": 256,
        })
        assert r["ok"] is True
        assert r["truncated"] is True
        assert r["diff_bytes"] <= 256

    def test_binary_file_rejected(self, host, roots):
        (roots["read"] / "a.png").write_bytes(b"\x89PNG\r\n")
        (roots["read"] / "b.png").write_bytes(b"\x89PNG\r\n")
        r = host._diff_files({"a": "a.png", "b": "b.png"})
        assert "error" in r
        assert "non-text" in r["error"]

    def test_missing_file_rejected(self, host, roots):
        (roots["read"] / "a.txt").write_text("x", encoding="utf-8")
        r = host._diff_files({"a": "a.txt", "b": "missing.txt"})
        assert "error" in r
        assert "not found" in r["error"]

    def test_directory_rejected(self, host, roots):
        (roots["read"] / "somedir").mkdir()
        (roots["read"] / "a.txt").write_text("x", encoding="utf-8")
        r = host._diff_files({"a": "somedir", "b": "a.txt"})
        assert "error" in r
        assert "not a regular file" in r["error"]

    def test_invalid_utf8_rejected(self, host, roots):
        (roots["read"] / "a.txt").write_text(
            "hello", encoding="utf-8",
        )
        (roots["read"] / "b.txt").write_bytes(b"\xff\xfe\x00")
        r = host._diff_files({"a": "a.txt", "b": "b.txt"})
        assert "error" in r
        assert "UTF-8" in r["error"] or "utf-8" in r["error"]

    def test_sandbox_escape(self, host, tmp_path):
        outside = tmp_path / "evil.txt"
        outside.write_text("x", encoding="utf-8")
        try:
            r = host._diff_files({
                "a": str(outside), "b": str(outside),
            })
            assert "error" in r
        finally:
            outside.unlink(missing_ok=True)

    def test_missing_field(self, host, roots):
        (roots["read"] / "a.txt").write_text("x", encoding="utf-8")
        (roots["read"] / "b.txt").write_text("y", encoding="utf-8")
        assert "error" in host._diff_files({"a": "a.txt"})
        assert "error" in host._diff_files({"b": "b.txt"})

    def test_non_dict_args(self, host):
        assert "error" in host._diff_files(None)
        assert "error" in host._diff_files("oops")
        assert "error" in host._diff_files(42)

    def test_read_root_in_response(self, host, roots):
        (roots["read"] / "a.txt").write_text("x", encoding="utf-8")
        (roots["read"] / "b.txt").write_text("y", encoding="utf-8")
        r = host._diff_files({"a": "a.txt", "b": "b.txt"})
        assert r["read_root"] == roots["read"].as_posix()


# ── patch_file (v0.9.4+) ────────────────────────────────


class TestPatchFile:
    def test_basic_replace(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        patch = (
            "--- t.txt\n+++ t.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " alpha\n"
            "-beta\n"
            "+BETA\n"
            " gamma\n"
        )
        r = host._patch_file({"path": "t.txt", "patch": patch})
        assert r["ok"] is True
        assert r["hunks_applied"] == 1
        assert target.read_text(
            encoding="utf-8",
        ) == "alpha\nBETA\ngamma\n"

    def test_insertion_only(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("a\nc\n", encoding="utf-8")
        patch = (
            "@@ -1,2 +1,3 @@\n"
            " a\n"
            "+b\n"
            " c\n"
        )
        r = host._patch_file({"path": "t.txt", "patch": patch})
        assert r["ok"] is True
        assert target.read_text(
            encoding="utf-8",
        ) == "a\nb\nc\n"

    def test_deletion_only(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("a\nb\nc\n", encoding="utf-8")
        patch = (
            "@@ -1,3 +1,2 @@\n"
            " a\n"
            "-b\n"
            " c\n"
        )
        r = host._patch_file({"path": "t.txt", "patch": patch})
        assert r["ok"] is True
        assert target.read_text(
            encoding="utf-8",
        ) == "a\nc\n"

    def test_multi_hunk(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text(
            "a\nb\nc\nd\ne\n", encoding="utf-8",
        )
        patch = (
            "@@ -1,2 +1,2 @@\n"
            "-a\n"
            "+A\n"
            " b\n"
            "@@ -4,2 +4,2 @@\n"
            "-d\n"
            "+D\n"
            " e\n"
        )
        r = host._patch_file({"path": "t.txt", "patch": patch})
        assert r["ok"] is True
        assert r["hunks_applied"] == 2
        assert target.read_text(
            encoding="utf-8",
        ) == "A\nb\nc\nD\ne\n"

    def test_dry_run_does_not_write(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("old\n", encoding="utf-8")
        patch = "@@ -1 +1 @@\n-old\n+new\n"
        r = host._patch_file({
            "path": "t.txt", "patch": patch, "dry_run": True,
        })
        assert r["ok"] is True
        assert r["dry_run"] is True
        assert "preview" in r
        assert r["bytes_would_write"] == 4
        assert target.read_text(encoding="utf-8") == "old\n"

    def test_context_mismatch_rejected(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("alpha\nbeta\n", encoding="utf-8")
        patch = "@@ -1 +1 @@\n-WRONG\n+right\n"
        r = host._patch_file({"path": "t.txt", "patch": patch})
        assert "error" in r
        assert "context mismatch" in r["error"]

    def test_overlap_rejected(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("a\nb\nc\nd\n", encoding="utf-8")
        patch = (
            "@@ -1,1 +1,1 @@\n-a\n+A\n"
            "@@ -1,1 +1,1 @@\n-a\n+B\n"
        )
        r = host._patch_file({"path": "t.txt", "patch": patch})
        assert "error" in r
        assert "overlaps" in r["error"] or "hunk anchor" in r["error"]

    def test_past_end_of_file_rejected(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("a\nb\n", encoding="utf-8")
        patch = "@@ -99,1 +99,1 @@\n-x\n+y\n"
        r = host._patch_file({"path": "t.txt", "patch": patch})
        assert "error" in r
        assert "past end" in r["error"]

    def test_no_hunks_rejected(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("a\n", encoding="utf-8")
        r = host._patch_file({"path": "t.txt", "patch": "no markers\n"})
        assert "error" in r
        assert "no hunks" in r["error"]

    def test_malformed_hunk_header_rejected(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("a\n", encoding="utf-8")
        patch = "@@ not a header @@\n-a\n+b\n"
        r = host._patch_file({"path": "t.txt", "patch": patch})
        assert "error" in r

    def test_unknown_line_tag_rejected(self, host, roots):
        target = roots["write"] / "t.txt"
        target.write_text("a\n", encoding="utf-8")
        # "!" is git's "NO_COMMIT" marker; our strict parser
        # rejects anything outside " +-/\".
        patch = "@@ -1 +1 @@\n!a\nb\n"
        r = host._patch_file({"path": "t.txt", "patch": patch})
        assert "error" in r
        assert "unknown" in r["error"]

    def test_dangerous_ext_rejected(self, host, roots):
        target = roots["write"] / "x.bat"
        target.write_text("a\n", encoding="utf-8")
        patch = "@@ -1 +1 @@\n-a\n+b\n"
        r = host._patch_file({"path": "x.bat", "patch": patch})
        assert "error" in r
        assert "dangerous extension" in r["error"]

    def test_missing_target_rejected(self, host, roots):
        patch = "@@ -1 +1 @@\n-a\n+b\n"
        r = host._patch_file({
            "path": "missing.txt", "patch": patch,
        })
        assert "error" in r
        assert "not found" in r["error"]

    def test_directory_target_rejected(self, host, roots):
        (roots["write"] / "dir").mkdir()
        patch = "@@ -1 +1 @@\n-a\n+b\n"
        r = host._patch_file({"path": "dir", "patch": patch})
        assert "error" in r
        assert "not a regular file" in r["error"]

    def test_binary_target_rejected(self, host, roots):
        (roots["write"] / "x.png").write_bytes(b"\x89PNG\r\n")
        patch = "@@ -1 +1 @@\n-a\n+b\n"
        r = host._patch_file({"path": "x.png", "patch": patch})
        assert "error" in r
        assert "non-text" in r["error"]

    def test_oversized_patch_rejected(self, host, roots):
        (roots["write"] / "t.txt").write_text("a\n", encoding="utf-8")
        big = "@@ -1 +1 @@\n-a\n+b\n" + ("x" * 5_000_000)
        r = host._patch_file({"path": "t.txt", "patch": big})
        assert "error" in r
        assert "byte cap" in r["error"]

    def test_empty_patch_rejected(self, host, roots):
        (roots["write"] / "t.txt").write_text("a\n", encoding="utf-8")
        r = host._patch_file({"path": "t.txt", "patch": ""})
        assert "error" in r
        assert "non-empty" in r["error"]

    def test_non_string_patch_rejected(self, host, roots):
        (roots["write"] / "t.txt").write_text("a\n", encoding="utf-8")
        for bad in (None, 42, ["@@"], {"@@": True}):
            r = host._patch_file({"path": "t.txt", "patch": bad})
            assert "error" in r, bad

    def test_missing_field(self, host):
        assert "error" in host._patch_file(
            {"patch": "@@ -1 +1 @@\n-a\n+b\n"},
        )
        assert "error" in host._patch_file({"path": "x.txt"})

    def test_non_dict_args(self, host):
        assert "error" in host._patch_file(None)
        assert "error" in host._patch_file("oops")
        assert "error" in host._patch_file(42)

    def test_sandbox_escape(self, host, tmp_path):
        outside = tmp_path / "evil.txt"
        outside.write_text("a\n", encoding="utf-8")
        try:
            r = host._patch_file({
                "path": str(outside),
                "patch": "@@ -1 +1 @@\n-a\n+b\n",
            })
            assert "error" in r
        finally:
            outside.unlink(missing_ok=True)

    def test_write_root_in_response(self, host, roots):
        (roots["write"] / "t.txt").write_text("a\n", encoding="utf-8")
        r = host._patch_file({
            "path": "t.txt", "patch": "@@ -1 +1 @@\n-a\n+b\n",
        })
        assert r["write_root"] == roots["write"].as_posix()

    def test_diff_then_patch_roundtrip(self, host, roots):
        # Build two versions in read sandbox, diff them, apply
        # the diff back to a copy inside the write sandbox,
        # expect the modified copy.
        a = roots["read"] / "src.py"
        a.write_text(
            "def f():\n    return 1\n", encoding="utf-8",
        )
        b = roots["read"] / "dst.py"
        b.write_text(
            "def f():\n    return 2\n", encoding="utf-8",
        )
        d = host._diff_files({"a": "src.py", "b": "dst.py"})
        assert d["ok"]
        import shutil
        target = roots["write"] / "copy.py"
        shutil.copyfile(a, target)
        r = host._patch_file({"path": "copy.py", "patch": d["diff"]})
        assert r["ok"], r
        assert target.read_text(
            encoding="utf-8",
        ) == "def f():\n    return 2\n"


# ── error envelope ───────────────────────────────────────


class TestErrorEnvelope:
    def test_dict_required(self, host):
        # Every handler must accept None / non-dict without
        # raising — return {"error": ...}.
        for name in (
            "list_dir", "stat_file", "read_file", "read_multiple",
            "search_files", "get_tree", "search_content",
            "write_file", "append_file", "delete_file", "move_file",
            "mkdir", "copy_file", "diff_files", "patch_file",
        ):
            method = getattr(host, f"_{name}")
            assert "error" in method(None), name
            assert "error" in method("oops"), name
            assert "error" in method(42), name


# ── McpManager auto-registration ─────────────────────────


class TestMcpManagerRegistration:
    def test_registered_in_from_config(self, roots):
        mgr = McpManager.from_config(include_local_hosts=True)
        names = {
            t.name for t in mgr.list_tools()
            if t.server_name == "filesystem-local"
        }
        assert "list_dir" in names
        assert "write_file" in names
        assert "delete_file" in names
        assert "mkdir" in names
        assert "copy_file" in names
        assert "diff_files" in names
        assert "patch_file" in names
        # Host reference is preserved.
        assert mgr._filesystem_local_host is not None

    def test_replace_config_keeps_filesystem_host(self, roots):
        mgr = McpManager.from_config(include_local_hosts=True)
        mgr.replace_config(servers=[])
        names = {
            t.name for t in mgr.list_tools()
            if t.server_name == "filesystem-local"
        }
        assert "list_dir" in names

    def test_filesystem_host_attaches_sandbox_roots(self, roots):
        mgr = McpManager.from_config(include_local_hosts=True)
        host = mgr._filesystem_local_host
        assert host.read_root == roots["read"].resolve()
        assert host.write_root == roots["write"].resolve()


# ── cross-server-name hygiene ───────────────────────────


class TestServerNameHygiene:
    def test_no_qdrant_local_tool_in_filesystem_list(self, host):
        names = {t.name for t in host.list_tools()}
        for q in (
            "search_documents", "list_documents",
            "remove_documents", "get_document_stats",
            "add_document", "update_document",
            "describe_image", "resize_image",
        ):
            assert q not in names


# ── extension classification sanity ──────────────────────


class TestExtensionTables:
    def test_text_extensions_have_canonical(self):
        for ext in (".md", ".py", ".json", ".yaml", ".csv", ".txt"):
            assert ext in _FS_TEXT_EXTENSIONS, ext

    def test_binary_extensions_have_canonical(self):
        for ext in (".png", ".jpg", ".pdf", ".exe", ".zip"):
            assert ext in _FS_BINARY_EXTENSIONS, ext

    def test_dangerous_extensions_have_canonical(self):
        for ext in (".exe", ".bat", ".sh", ".ps1", ".js"):
            assert ext in _FS_DANGEROUS_WRITE_EXTS, ext

    def test_text_and_binary_disjoint(self):
        assert _FS_TEXT_EXTENSIONS.isdisjoint(_FS_BINARY_EXTENSIONS)

 
