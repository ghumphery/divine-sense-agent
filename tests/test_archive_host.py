"""Tests for the archive (zip / 7z / tar / gzip) MCP host (v0.9.8+).

Covers the whole chain — ``src.archive_tools`` format logic
(create / list / extract, Zip-Slip guard, dangerous-extension skip,
zip-bomb budgets), ``ArchiveLocalHost`` tool surface + dual
sandbox, manager registration, the stdio MCP server, and the
system-prompt trigger block.

All disk work happens in per-test tmp sandboxes pinned via
``QDRANT_FILESYSTEM_READ_ROOT`` / ``_WRITE_ROOT``; 7z exercises the
real py7zr package (a project dependency since v0.9.8.0).
"""
from __future__ import annotations

import inspect
import os
import zipfile
from pathlib import Path

import pytest


# ─── fixtures ────────────────────────────────────────────────────


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Split read/write sandbox roots per test (mirrors the
    filesystem MCP's two-root model)."""
    r = tmp_path / "read"; r.mkdir()
    w = tmp_path / "write"; w.mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(r))
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(w))
    return {"read": r, "write": w}


@pytest.fixture
def host(roots):
    from src.mcp_client.host import ArchiveLocalHost

    h = ArchiveLocalHost()
    h.attach_read_root(roots["read"])
    h.attach_write_root(roots["write"])
    return h


@pytest.fixture
def sample_tree(roots):
    """A small source tree in the read sandbox."""
    r = roots["read"]
    (r / "docs").mkdir()
    (r / "docs" / "a.txt").write_text("alpha", encoding="utf-8")
    (r / "docs" / "b.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (r / "docs" / "nested").mkdir()
    (r / "docs" / "nested" / "c.md").write_text("nested", encoding="utf-8")
    return r / "docs"


def _require_7z():
    from src.archive_tools import sevenzip_available

    if not sevenzip_available():
        pytest.skip("py7zr not installed")


# ─── format sniffing ─────────────────────────────────────────────


class TestSniffFormat:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("a.zip", "zip"),
            ("a.7z", "7z"),
            ("a.tar", "tar"),
            ("a.tar.gz", "tar.gz"),
            ("a.tgz", "tar.gz"),
            ("a.tar.bz2", "tar.bz2"),
            ("a.tbz2", "tar.bz2"),
            ("a.gz", "gz"),
            ("a.txt", None),
            ("a", None),
        ],
    )
    def test_sniff(self, name, expected):
        from src.archive_tools import sniff_archive_format

        assert sniff_archive_format(name) == expected


# ─── create / list / extract roundtrips ──────────────────────────


class TestCreateAndExtract:
    def _bundle(self, tmp_path, sample_tree, fmt):
        from src.archive_tools import create_archive

        ext = {"zip": ".zip", "tar": ".tar", "tar.gz": ".tar.gz",
               "tar.bz2": ".tar.bz2", "7z": ".7z"}[fmt]
        out = tmp_path / f"bundle{ext}"
        files = [
            (p, p.relative_to(sample_tree.parent).as_posix())
            for p in sorted(sample_tree.rglob("*")) if p.is_file()
        ]
        return out, create_archive(files, out, fmt), files

    @pytest.mark.parametrize("fmt", ["zip", "tar", "tar.gz", "tar.bz2"])
    def test_roundtrip_preserves_tree(self, tmp_path, sample_tree, fmt):
        from src.archive_tools import extract_archive

        out, summary, = self._bundle(tmp_path, sample_tree, fmt)[:2]
        assert summary["format"] == fmt
        assert summary["file_count"] == 3
        assert out.exists() and out.stat().st_size > 0

        dest = tmp_path / f"out_{fmt.replace('.', '_')}"
        res = extract_archive(out, dest)
        assert res["count"] == 3
        assert (dest / "docs" / "a.txt").read_text(encoding="utf-8") == "alpha"
        assert (dest / "docs" / "nested" / "c.md").read_text(encoding="utf-8") == "nested"

    def test_roundtrip_7z(self, tmp_path, sample_tree):
        _require_7z()
        from src.archive_tools import extract_archive

        out, _summary = self._bundle(tmp_path, sample_tree, "7z")[:2]
        dest = tmp_path / "out_7z"
        res = extract_archive(out, dest)
        assert res["count"] == 3
        assert (dest / "docs" / "a.txt").read_text(encoding="utf-8") == "alpha"

    def test_7z_list_members(self, tmp_path, sample_tree):
        _require_7z()
        from src.archive_tools import list_members

        out, _ = self._bundle(tmp_path, sample_tree, "7z")[:2]
        members = list_members(out, "7z")
        names = {m["name"] for m in members}
        assert {"docs/a.txt", "docs/b.csv", "docs/nested/c.md"} <= names
        sizes = {m["name"]: m["size"] for m in members}
        assert sizes["docs/a.txt"] == 5  # len("alpha")

    def test_gz_single_file_roundtrip(self, tmp_path, sample_tree):
        from src.archive_tools import create_archive, extract_archive

        out = tmp_path / "a.txt.gz"
        create_archive([(sample_tree / "a.txt", "a.txt")], out, "gz")
        dest = tmp_path / "gz_out"
        res = extract_archive(out, dest)
        # gunzip convention: extracted name drops .gz from the
        # ARCHIVE name (a.txt.gz -> a.txt).
        assert res["count"] == 1
        assert (dest / "a.txt").read_text(encoding="utf-8") == "alpha"

    def test_gz_multi_file_rejected(self, sample_tree, tmp_path):
        from src.archive_tools import ArchiveError, create_archive

        with pytest.raises(ArchiveError, match="ONE file"):
            create_archive(
                [
                    (sample_tree / "a.txt", "a.txt"),
                    (sample_tree / "b.csv", "b.csv"),
                ],
                tmp_path / "x.gz",
                "gz",
            )

    def test_gz_directory_rejected(self, tmp_path, sample_tree):
        from src.archive_tools import ArchiveError, create_archive

        with pytest.raises(ArchiveError, match="regular file"):
            create_archive(
                [(sample_tree, "docs")], tmp_path / "x.gz", "gz",
            )

    def test_unknown_format_rejected(self, tmp_path, sample_tree):
        from src.archive_tools import ArchiveError, create_archive

        with pytest.raises(ArchiveError, match="unsupported format"):
            create_archive(
                [(sample_tree / "a.txt", "a.txt")],
                tmp_path / "x.rar",
                "rar",
            )

    def test_list_members_zip(self, tmp_path, sample_tree):
        from src.archive_tools import list_members

        out, _ = self._bundle(tmp_path, sample_tree, "zip")[:2]
        members = list_members(out, "zip")
        assert len(members) == 3
        assert all(m["size"] >= 0 for m in members)
        assert all("compressed" in m for m in members)


# ─── security guards ─────────────────────────────────────────────


class TestSecurityGuards:
    def _evil_zip(self, tmp_path):
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("ok.txt", "fine")
            zf.writestr("../evil.txt", "pwned")
            zf.writestr("..\\win_evil.txt", "pwned")
            zf.writestr("/abs_evil.txt", "pwned")
            zf.writestr("C:/drive_evil.txt", "pwned")
        return evil

    def test_zip_slip_blocked(self, tmp_path):
        from src.archive_tools import extract_archive

        evil = self._evil_zip(tmp_path)
        dest = tmp_path / "out"
        res = extract_archive(evil, dest)
        # only the benign member lands; the traversal/absolute ones
        # are skipped with a zip-slip reason.
        assert res["count"] == 1
        assert res["extracted"] == ["ok.txt"]
        slip = [s for s in res["skipped"] if "zip-slip" in s["reason"]]
        assert len(slip) == 4, res["skipped"]
        # nothing escaped the destination
        assert not (tmp_path / "evil.txt").exists()
        assert not (tmp_path / "win_evil.txt").exists()
        assert not (tmp_path / "abs_evil.txt").exists()
        assert not Path("C:/drive_evil.txt").exists()

    def test_dangerous_ext_skipped(self, tmp_path):
        from src.archive_tools import extract_archive

        arch = tmp_path / "dang.zip"
        with zipfile.ZipFile(arch, "w") as zf:
            zf.writestr("readme.txt", "fine")
            zf.writestr("payload.exe", "MZ...")
            zf.writestr("script.bat", "@echo off")
            zf.writestr("tool.dll", "MZ...")
        dest = tmp_path / "out"
        res = extract_archive(arch, dest)
        assert res["count"] == 1
        assert res["extracted"] == ["readme.txt"]
        reasons = {s["name"]: s["reason"] for s in res["skipped"]}
        assert reasons["payload.exe"] == "dangerous executable extension"
        assert reasons["tool.dll"] == "dangerous executable extension"
        assert not (dest / "payload.exe").exists()

    def test_max_bytes_budget(self, tmp_path):
        from src.archive_tools import extract_archive

        big = tmp_path / "big.zip"
        with zipfile.ZipFile(big, "w") as zf:
            zf.writestr("blob.bin", b"0" * 1000)
        res = extract_archive(big, tmp_path / "out", max_bytes=100)
        assert res["count"] == 0
        assert all("max_bytes" in s["reason"] for s in res["skipped"])
        assert res["total_bytes"] == 0

    def test_max_files_budget(self, tmp_path):
        from src.archive_tools import extract_archive

        many = tmp_path / "many.zip"
        with zipfile.ZipFile(many, "w") as zf:
            for i in range(10):
                zf.writestr(f"f{i}.txt", "x")
        dest = tmp_path / "out"
        res = extract_archive(many, dest, max_files=3)
        assert res["count"] == 3
        over = [s for s in res["skipped"] if "max_files" in s["reason"]]
        assert len(over) >= 1

    def test_members_allowlist(self, tmp_path, sample_tree):
        from src.archive_tools import extract_archive, create_archive

        out = tmp_path / "b.zip"
        create_archive(
            [(sample_tree / "a.txt", "a.txt"),
             (sample_tree / "b.csv", "b.csv")],
            out, "zip",
        )
        dest = tmp_path / "out"
        res = extract_archive(out, dest, members=["a.txt"])
        assert res["count"] == 1
        assert (dest / "a.txt").exists()
        assert not (dest / "b.csv").exists()

    def test_overwrite_false_skips_existing(self, tmp_path, sample_tree):
        from src.archive_tools import create_archive, extract_archive

        out = tmp_path / "b.zip"
        create_archive([(sample_tree / "a.txt", "a.txt")], out, "zip")
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "a.txt").write_text("ORIGINAL", encoding="utf-8")
        res = extract_archive(out, dest, overwrite=False)
        assert res["count"] == 0
        assert (dest / "a.txt").read_text(encoding="utf-8") == "ORIGINAL"
        res2 = extract_archive(out, dest, overwrite=True)
        assert res2["count"] == 1
        assert (dest / "a.txt").read_text(encoding="utf-8") == "alpha"


# ─── host layer ──────────────────────────────────────────────────


class TestHostToolSurface:
    def test_four_tools_advertised(self, host):
        names = [t.name for t in host.list_tools()]
        assert names == [
            "archive_create", "archive_extract", "archive_list",
            "list_archive_formats",
        ]

    def test_server_name_and_args_convention(self, host):
        for t in host.list_tools():
            assert t.server_name == "archive-local"
            params = list(
                inspect.signature(t._handler).parameters.values(),
            )
            assert len(params) == 1 and params[0].name == "args", t.name


class TestHostCreate:
    def test_missing_files_arg(self, host):
        assert "error" in host._create({})

    def test_create_zip_from_list(self, host, sample_tree):
        out = host._create({
            "files": ["docs/a.txt", "docs/b.csv"],
            "out_path": "b.zip",
        })
        assert out.get("ok") is True, out
        assert out["format"] == "zip"
        assert out["file_count"] == 2
        assert out["out_path"].endswith("b.zip")
        assert (Path(out["out_path"])).exists()

    def test_create_from_single_string(self, host, sample_tree):
        out = host._create({
            "files": "docs/a.txt",
            "out_path": "s.zip",
        })
        assert out.get("ok") is True, out
        assert out["file_count"] == 1

    def test_create_from_directory_walks_tree(self, host, sample_tree):
        out = host._create({"files": ["docs"], "out_path": "d.zip"})
        assert out.get("ok") is True, out
        assert out["file_count"] == 3  # a.txt, b.csv, nested/c.md

    def test_format_from_extension(self, host, sample_tree):
        out = host._create({
            "files": ["docs/a.txt"],
            "out_path": "auto.tar.gz",
        })
        assert out.get("ok") is True
        assert out["format"] == "tar.gz"

    def test_format_override(self, host, sample_tree):
        out = host._create({
            "files": ["docs/a.txt"],
            "out_path": "weird.bin",
            "format": "7z",
        })
        assert out.get("ok") is True
        assert out["format"] == "7z"

    def test_create_7z_without_extension(self, host, sample_tree):
        out = host._create({
            "files": ["docs/a.txt"],
            "out_path": "packed.7z",
        })
        assert out.get("ok") is True, out
        assert out["format"] == "7z"

    def test_dangerous_input_skipped(self, host, sample_tree, roots):
        (roots["read"] / "docs" / "bad.exe").write_text("MZ", encoding="utf-8")
        out = host._create({"files": ["docs"], "out_path": "s.zip"})
        assert out.get("ok") is True
        assert out["file_count"] == 3
        assert out["skipped_inputs"][0]["reason"] == "dangerous executable"

    def test_outside_read_sandbox_rejected(self, host, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        out = host._create({
            "files": [str(outside)], "out_path": "b.zip",
        })
        assert "error" in out
        assert "sandbox" in out["error"]

    def test_out_path_outside_write_sandbox_rejected(
        self, host, sample_tree, tmp_path,
    ):
        out = host._create({
            "files": ["docs/a.txt"],
            "out_path": str(tmp_path / "escape.zip"),
        })
        assert "error" in out
        assert "sandbox" in out["error"]

    def test_input_over_budget_rejected(self, host, sample_tree, monkeypatch):
        monkeypatch.setenv("QDRANT_ARCHIVE_MAX_BYTES", "2")
        out = host._create({
            "files": ["docs/a.txt"], "out_path": "b.zip",
        })
        assert "error" in out
        assert "max_bytes" in out["error"]

    def test_gz_multi_file_hint(self, host, sample_tree):
        out = host._create({
            "files": ["docs/a.txt", "docs/b.csv"],
            "out_path": "x.gz",
        })
        assert "error" in out
        assert "ONE file" in out["error"] or "tar.gz" in out["error"]


class TestHostExtract:
    def test_missing_path(self, host):
        assert "error" in host._extract({})

    def test_extract_roundtrip(self, host, sample_tree):
        made = host._create({
            "files": ["docs"],
            "out_path": "docs.zip",
        })
        assert made.get("ok") is True
        res = host._extract({"path": "docs.zip", "to_dir": "out"})
        assert res.get("ok") is True, res
        assert res["count"] == 3
        assert res["target_dir"].endswith("out")
        assert (Path(res["target_dir"]) / "docs" / "a.txt").read_text(
            encoding="utf-8",
        ) == "alpha"

    def test_default_to_dir_named_after_archive(self, host, sample_tree):
        host._create({"files": ["docs/a.txt"], "out_path": "p.zip"})
        res = host._extract({"path": "p.zip"})
        assert res.get("ok") is True
        assert res["target_dir"].endswith("p_extracted")

    def test_extract_zip_slip_reported(self, host, tmp_path):
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../escape.txt", "pwned")
        (host._ensure_read_root() / "evil.zip").write_bytes(
            evil.read_bytes(),
        )
        res = host._extract({"path": "evil.zip", "to_dir": "e"})
        assert res.get("ok") is True
        assert res["count"] == 0
        assert "zip-slip" in res["skipped"][0]["reason"]
        # nothing escaped the write sandbox
        assert not (tmp_path / "escape.txt").exists()

    def test_extract_dangerous_members_skipped(self, host, tmp_path):
        arch = tmp_path / "dang.zip"
        with zipfile.ZipFile(arch, "w") as zf:
            zf.writestr("ok.txt", "fine")
            zf.writestr("evil.bat", "@echo off")
        (host._ensure_read_root() / "dang.zip").write_bytes(
            arch.read_bytes(),
        )
        res = host._extract({"path": "dang.zip", "to_dir": "d"})
        assert res["count"] == 1
        assert "dangerous executable" in res["skipped"][0]["reason"]
        assert not (Path(res["target_dir"]) / "evil.bat").exists()

    def test_extract_unknown_format(self, host, sample_tree):
        (sample_tree / "plain.txt").write_text("nope", encoding="utf-8")
        res = host._extract({"path": "docs/plain.txt"})
        assert "error" in res

    def test_extract_outside_read_sandbox(self, host, tmp_path):
        out2 = tmp_path / "out.zip"
        with zipfile.ZipFile(out2, "w") as zf:
            zf.writestr("x.txt", "x")
        res = host._extract({"path": str(out2)})
        assert "error" in res
        assert "sandbox" in res["error"]

    def test_extract_to_dir_outside_write_sandbox(self, host, sample_tree):
        host._create({"files": ["docs/a.txt"], "out_path": "b.zip"})
        res = host._extract({"path": "b.zip", "to_dir": "../outside"})
        assert "error" in res


class TestHostListAndFormats:
    def test_list_roundtrip(self, host, sample_tree):
        host._create({"files": ["docs"], "out_path": "d.zip"})
        out = host._list({"path": "d.zip"})
        assert out.get("ok") is True
        assert out["format"] == "zip"
        assert out["count"] == 3
        names = {m["name"] for m in out["members"]}
        assert "docs/a.txt" in names

    def test_list_requires_path(self, host):
        assert "error" in host._list({})

    def test_list_unrecognised_extension(self, host, sample_tree):
        (sample_tree / "file.bin").write_text("xx", encoding="utf-8")
        out = host._list({"path": "docs/file.bin"})
        assert "error" in out

    def test_formats_introspection(self, host):
        out = host._formats({})
        assert out.get("ok") is True
        for fmt in ("zip", "7z", "tar", "tar.gz", "tar.bz2", "gz"):
            assert fmt in out["create_formats"]
        assert isinstance(out["sevenzip_available"], bool)
        assert out["max_bytes"] > 0
        assert out["max_files"] > 0
        assert out["read_root"].endswith("read")
        assert out["write_root"].endswith("write")


# ─── manager / stdio / prompt integration ────────────────────────


class TestManagerRegistration:
    def test_from_config_registers_archive_tools(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(None, include_local_hosts=True)
        names = {t.name for t in mgr.list_tools()}
        for expected in (
            "archive_create", "archive_extract", "archive_list",
            "list_archive_formats",
        ):
            assert expected in names, f"{expected} not registered"
        assert mgr._archive_local_host is not None

    def test_tool_dispatch_reaches_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(None, include_local_hosts=True)
        result = mgr.call("archive_create", {})
        assert "'files'" in result.text


class TestStdioServer:
    def test_tool_specs_match_host_surface(self):
        from src.mcp_server.archive_server import _build_tool_specs

        from src.mcp_client.host import ArchiveLocalHost

        spec_names = {s.name for s in _build_tool_specs()}
        host_names = {t.name for t in ArchiveLocalHost().list_tools()}
        assert spec_names == host_names

    def test_handlers_delegate_to_host(self):
        from unittest.mock import MagicMock

        from src.mcp_server.archive_server import ArchiveMCPServer

        s = ArchiveMCPServer()
        s.host = MagicMock()
        s.host._formats.return_value = {"ok": True}
        out = s._do_formats({})
        import json as _json

        assert _json.loads(out[0].text) == {"ok": True}
        s.host._formats.assert_called_once()


@pytest.fixture
def stub_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent.agent import Agent

    a = Agent()
    a._provider = "stub"
    a._model = "stub-model"
    a._mcp_manager = None
    return a


class TestSystemPromptTriggerBlock:
    def test_block_present_when_tools_enabled(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "File archive / compression" in prompt
        for tool in (
            "archive_create", "archive_extract", "archive_list",
            "list_archive_formats",
        ):
            assert tool in prompt, f"{tool} missing from prompt"

    def test_formats_listed(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        for fmt in ("zip", "7z", "tar", "tar.gz", "gz"):
            assert fmt in prompt, f"{fmt} missing"

    def test_trigger_keywords_zh_and_en(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        for kw in ("壓縮", "打包", "解壓縮", "解壓"):
            assert kw in prompt, f"zh trigger {kw!r} missing"
        for kw in ("compress", "zip it up", "unzip", "extract the 7z"):
            assert kw in prompt, f"en trigger {kw!r} missing"

    def test_safety_note_present(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "zip-bomb" in prompt
        assert "sandbox" in prompt

    def test_block_absent_in_legacy_mode(self, stub_agent):
        legacy = stub_agent._system_prompt(tools_enabled=False)
        assert "archive_create" not in legacy
        assert "File archive" not in legacy

    def test_composes_with_other_blocks(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Log anomaly analysis" in prompt
        assert "create_diagram" in prompt
        assert "send_telegram_message" in prompt
        assert "add_scheduled_task" in prompt