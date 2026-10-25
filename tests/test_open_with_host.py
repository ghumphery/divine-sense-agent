"""Tests for OpenWithLocalHost (v0.9.6.0+).

Windows-Explorer-style "open with the associated application" for
chat-produced artifacts. All tests are mock-driven: ``os.startfile``
is monkeypatched so nothing actually opens on the test machine, and
the sandbox root is pinned to a tmp dir via
``QDRANT_FILESYSTEM_WRITE_ROOT``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ─── fixtures ────────────────────────────────────────────────


@pytest.fixture
def write_root(tmp_path, monkeypatch):
    """Pin all sandbox env vars to a tmp dir so the host's lazy
    resolution (and any accidental env fallback) lands there."""
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(tmp_path))
    monkeypatch.setenv("WATCH_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def host(write_root):
    from src.mcp_client.host import OpenWithLocalHost
    h = OpenWithLocalHost()
    h.attach_write_root(write_root)
    return h


@pytest.fixture
def startfile_calls(monkeypatch):
    """Record every ``os.startfile`` call instead of launching the
    OS association (nothing may actually open during tests)."""
    calls: list[str] = []

    def _fake(path):
        calls.append(str(path))
        return None

    if os.name == "nt":
        monkeypatch.setattr(os, "startfile", _fake)
    else:
        # Non-Windows dev boxes: stub the whole OS branch out so the
        # routing tests still exercise the Windows association path.
        monkeypatch.setattr("src.mcp_client.host.os.name", "nt")
        monkeypatch.setattr("os.startfile", _fake, raising=False)
    return calls


# ─── tool surface ────────────────────────────────────────────


class TestOpenWithToolSurface:
    def test_list_tools_advertises_three(self, host):
        tools = host.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "open_file", "open_content", "list_openable_types",
        }
        assert all(t.server_name == "openwith-local" for t in tools)

    def test_handlers_follow_args_dict_convention(self, host):
        import inspect
        for t in host.list_tools():
            # Bound method — ``self`` is already excluded by
            # ``inspect.signature``. The v0.8.6.1 convention is a
            # single positional ``args: dict`` parameter.
            params = list(
                inspect.signature(t._handler).parameters.values(),
            )
            assert len(params) == 1, t.name
            assert params[0].name == "args", t.name

    def test_attach_diagrams_opener_round_trip(self, host):
        assert host._diagrams_opener is None
        host.attach_diagrams_opener(lambda p: {"ok": True})
        assert callable(host._diagrams_opener)
        host.attach_diagrams_opener(None)
        assert host._diagrams_opener is None

    def test_attach_write_root_accepts_string(self, tmp_path):
        from src.mcp_client.host import OpenWithLocalHost
        h = OpenWithLocalHost()
        h.attach_write_root(str(tmp_path))
        assert h._write_root == tmp_path.resolve()

    def test_attach_write_root_garbage_ignored(self, host):
        host.attach_write_root(42)
        assert host._write_root is None


# ─── open_file ───────────────────────────────────────────────


class TestOpenFile:
    def test_missing_path_arg(self, host):
        assert "error" in host._open_file({})

    def test_nonexistent_file(self, host, write_root):
        out = host._open_file({"path": "ghost.md"})
        assert "error" in out

    def test_outside_sandbox_rejected(self, host, tmp_path):
        outside = tmp_path.parent / "outside.md"
        outside.write_text("x", encoding="utf-8")
        out = host._open_file({"path": str(outside)})
        assert "error" in out

    def test_executable_refused(
        self, host, write_root, startfile_calls,
    ):
        evil = write_root / "evil.bat"
        evil.write_text("@echo off", encoding="utf-8")
        out = host._open_file({"path": "evil.bat"})
        assert "error" in out
        assert "executable" in out["error"].lower()
        assert startfile_calls == []

    def test_md_opens_via_startfile(
        self, host, write_root, startfile_calls,
    ):
        f = write_root / "notes.md"
        f.write_text("# hi", encoding="utf-8")
        out = host._open_file({"path": "notes.md"})
        assert out.get("ok") is True
        assert "os.startfile" in out["opened_with"]
        assert startfile_calls == [str(f)]

    def test_drawio_routes_to_viewer_callback(
        self, host, write_root, startfile_calls,
    ):
        f = write_root / "circle.drawio"
        f.write_text("<mxfile/>", encoding="utf-8")
        seen: list[str] = []
        host.attach_diagrams_opener(
            lambda p: (seen.append(p), {"ok": True})[1],
        )
        out = host._open_file({"path": "circle.drawio"})
        assert out.get("ok") is True
        assert out["opened_with"] == "drawio-viewer-window"
        assert seen == [f.as_posix()]
        assert startfile_calls == []

    def test_drawio_without_callback_falls_back_to_startfile(
        self, host, write_root, startfile_calls,
    ):
        f = write_root / "circle.drawio"
        f.write_text("<mxfile/>", encoding="utf-8")
        out = host._open_file({"path": "circle.drawio"})
        assert out.get("ok") is True
        assert "os.startfile" in out["opened_with"]
        assert startfile_calls == [str(f)]

    def test_drawio_callback_error_falls_back_with_note(
        self, host, write_root, startfile_calls,
    ):
        f = write_root / "circle.drawio"
        f.write_text("<mxfile/>", encoding="utf-8")
        host.attach_diagrams_opener(
            lambda p: {"error": "viewer exploded"},
        )
        out = host._open_file({"path": "circle.drawio"})
        assert out.get("ok") is True
        assert "os.startfile" in out["opened_with"]
        assert "viewer exploded" in out["note"]
        assert len(startfile_calls) == 1

    def test_drawio_callback_raise_falls_back(
        self, host, write_root, startfile_calls,
    ):
        f = write_root / "circle.drawio"
        f.write_text("<mxfile/>", encoding="utf-8")

        def _boom(p):
            raise RuntimeError("boom")

        host.attach_diagrams_opener(_boom)
        out = host._open_file({"path": "circle.drawio"})
        assert out.get("ok") is True
        assert len(startfile_calls) == 1

    def test_xml_sniffed_as_drawio_routes_to_viewer(
        self, host, write_root, startfile_calls,
    ):
        f = write_root / "chart.xml"
        f.write_text(
            "<mxGraphModel><root/></mxGraphModel>", encoding="utf-8",
        )
        host.attach_diagrams_opener(lambda p: {"ok": True})
        out = host._open_file({"path": "chart.xml"})
        assert out["opened_with"] == "drawio-viewer-window"
        assert startfile_calls == []

    def test_xml_non_drawio_goes_to_startfile(
        self, host, write_root, startfile_calls,
    ):
        f = write_root / "page.xml"
        f.write_text("<html><body/></html>", encoding="utf-8")
        out = host._open_file({"path": "page.xml"})
        assert out.get("ok") is True
        assert "os.startfile" in out["opened_with"]

    def test_startfile_oserror_returns_clean_error(
        self, host, write_root, monkeypatch,
    ):
        f = write_root / "a.md"
        f.write_text("x", encoding="utf-8")

        def _boom(p):
            raise OSError("no assoc")

        if os.name == "nt":
            monkeypatch.setattr(os, "startfile", _boom)
        out = host._open_file({"path": "a.md"})
        assert "error" in out
        assert "no assoc" in out["error"]

    def test_relative_subdir_path_works(
        self, host, write_root, startfile_calls,
    ):
        sub = write_root / "sub"
        sub.mkdir()
        f = sub / "deep.md"
        f.write_text("x", encoding="utf-8")
        out = host._open_file({"path": "sub/deep.md"})
        assert out.get("ok") is True
        assert startfile_calls == [str(f)]


# ─── open_content ────────────────────────────────────────────


class TestOpenContent:
    def test_missing_content(self, host):
        assert "error" in host._open_content({})
        assert "error" in host._open_content({"content": "   "})

    def test_writes_and_opens(
        self, host, write_root, startfile_calls,
    ):
        out = host._open_content({
            "name": "notes.md", "content": "# hello",
        })
        assert out.get("ok") is True
        assert out["written"] is True
        assert out["bytes"] > 0
        target = write_root / "notes.md"
        assert target.read_text(encoding="utf-8") == "# hello"
        assert startfile_calls == [str(target)]

    def test_drawio_sniff_forces_drawio_ext(
        self, host, write_root, startfile_calls,
    ):
        seen: list[str] = []
        host.attach_diagrams_opener(
            lambda p: (seen.append(p), {"ok": True})[1],
        )
        out = host._open_content({
            "name": "noext",
            "content": "<mxGraphModel><root/></mxGraphModel>",
        })
        assert out.get("ok") is True
        assert out["opened_with"] == "drawio-viewer-window"
        assert out["path"].endswith(".drawio")
        assert seen == [out["path"]]

    def test_drawio_default_name_when_missing(
        self, host, write_root, startfile_calls,
    ):
        host.attach_diagrams_opener(lambda p: {"ok": True})
        out = host._open_content({
            "content": "<mxfile><diagram/></mxfile>",
        })
        assert out.get("ok") is True
        assert Path(out["path"]).name.startswith("diagram-")
        assert Path(out["path"]).suffix == ".drawio"

    def test_text_default_name_when_missing(
        self, host, write_root, startfile_calls,
    ):
        out = host._open_content({"content": "hello world"})
        assert out.get("ok") is True
        assert Path(out["path"]).name.startswith("artifact-")
        assert Path(out["path"]).suffix == ".txt"

    def test_executable_name_refused(
        self, host, write_root, startfile_calls,
    ):
        out = host._open_content({
            "name": "evil.bat", "content": "@echo off",
        })
        assert "error" in out
        assert "executable" in out["error"].lower()
        assert startfile_calls == []

    def test_path_traversal_name_sanitized_into_sandbox(
        self, host, write_root, startfile_calls,
    ):
        """``_fs_safe_basename`` strips (not rejects) path
        separators — the same sandbox semantics as the filesystem
        host. ``../evil.md`` must land at ``<root>/evil.md``, never
        outside the sandbox."""
        out = host._open_content({
            "name": "../evil.md", "content": "x",
        })
        assert out.get("ok") is True
        from src.mcp_client.host import _is_within
        assert _is_within(Path(out["path"]), write_root)
        assert Path(out["path"]).name == "evil.md"

    def test_overwrite_same_name(
        self, host, write_root, startfile_calls,
    ):
        for i in range(2):
            out = host._open_content({
                "name": "same.md", "content": f"v{i}",
            })
            assert out.get("ok") is True
        assert (write_root / "same.md").read_text(
            encoding="utf-8",
        ) == "v1"

    def test_oversized_content_refused(self, host):
        big = "x" * (4 * 1024 * 1024 + 1)
        out = host._open_content({"name": "big.md", "content": big})
        assert "error" in out
        assert "cap" in out["error"]

    def test_drawio_named_xml_reroutes_to_drawio(
        self, host, write_root, startfile_calls,
    ):
        host.attach_diagrams_opener(lambda p: {"ok": True})
        out = host._open_content({
            "name": "chart.xml",
            "content": "<mxGraphModel><root/></mxGraphModel>",
        })
        assert out.get("ok") is True
        assert out["path"].endswith(".drawio")


# ─── list_openable_types ─────────────────────────────────────


class TestListOpenable:
    def test_reports_root_and_wiring(self, host, write_root):
        out = host._list_openable({})
        assert out["ok"] is True
        assert out["write_root"] == write_root.as_posix()
        assert out["drawio_viewer_wired"] is False
        assert ".drawio" in out["drawio_viewer_exts"]
        assert ".exe" in out["refused_exts"]
        assert out["max_content_bytes"] == 4 * 1024 * 1024

    def test_reports_wired_after_attach(self, host):
        host.attach_diagrams_opener(lambda p: {"ok": True})
        out = host._list_openable({})
        assert out["drawio_viewer_wired"] is True


# ─── McpManager wiring ───────────────────────────────────────


class TestManagerWiring:
    def test_from_config_registers_openwith_tools(self):
        from src.mcp_client.manager import McpManager
        mgr = McpManager.from_config(path=None)
        names = {t.name for t in mgr.list_tools()}
        for want in (
            "open_file", "open_content", "list_openable_types",
        ):
            assert want in names, want
        assert mgr._openwith_local_host is not None
        assert "openwith-local" not in mgr.failed

    def test_set_diagrams_opener_pushes_down_after_config(self):
        from src.mcp_client.manager import McpManager
        mgr = McpManager.from_config(path=None)
        calls: list[str] = []
        mgr.set_diagrams_opener(
            lambda p: (calls.append(p), {"ok": True})[1],
        )
        assert mgr.diagrams_opener_callback is not None
        assert mgr._openwith_local_host._diagrams_opener is not None
        # End-to-end through the manager dispatch: a .drawio file
        # in the sandbox reaches the GUI callback.
        host = mgr._openwith_local_host
        root = host._ensure_write_root()
        f = root / "wired.drawio"
        f.write_text("<mxfile/>", encoding="utf-8")
        r = mgr.call("open_file", {"path": "wired.drawio"})
        assert not r.is_error
        assert "drawio-viewer-window" in r.text
        assert calls == [f.as_posix()]

    def test_set_diagrams_opener_before_host_registered(self):
        """GUI wires the callback on a manager whose host may not
        exist yet (e.g. replace_config flows). The setter must not
        raise when the host is absent, and clearing must work."""
        from src.mcp_client.manager import McpManager
        mgr = McpManager()
        mgr.set_diagrams_opener(lambda p: {"ok": True})
        assert mgr.diagrams_opener_callback is not None
        mgr.set_diagrams_opener(None)
        assert mgr.diagrams_opener_callback is None


# ─── stdio MCP server ────────────────────────────────────────


class TestOpenWithStdioServer:
    def test_server_tool_specs(self, tmp_path):
        from src.mcp_server.openwith_server import OpenWithMCPServer
        s = OpenWithMCPServer(write_root=str(tmp_path))
        specs = s._tool_specs()
        assert {t.name for t in specs} == {
            "open_file", "open_content", "list_openable_types",
        }
        schema = {t.name: t.inputSchema for t in specs}
        assert schema["open_file"]["required"] == ["path"]
        assert schema["open_content"]["required"] == ["content"]

    def test_server_do_open_file_routes(
        self, tmp_path, monkeypatch,
    ):
        from src.mcp_server.openwith_server import OpenWithMCPServer
        calls: list[str] = []

        def _fake(path):
            calls.append(str(path))

        if os.name == "nt":
            monkeypatch.setattr(os, "startfile", _fake)
        s = OpenWithMCPServer(write_root=str(tmp_path))
        f = tmp_path / "hello.md"
        f.write_text("hi", encoding="utf-8")
        out = s._do_open_file({"path": "hello.md"})
        payload = json.loads(out[0].text)
        assert payload.get("ok") is True
        assert len(calls) == 1

    def test_server_do_open_content_drawio(
        self, tmp_path,
    ):
        from src.mcp_server.openwith_server import OpenWithMCPServer
        s = OpenWithMCPServer(write_root=str(tmp_path))
        seen: list[str] = []
        s.host.attach_diagrams_opener(
            lambda p: (seen.append(p), {"ok": True})[1],
        )
        out = s._do_open_content({
            "name": "circle.drawio",
            "content": "<mxGraphModel><root/></mxGraphModel>",
        })
        payload = json.loads(out[0].text)
        assert payload.get("ok") is True
        assert payload["opened_with"] == "drawio-viewer-window"
        assert (tmp_path / "circle.drawio").exists()

    def test_server_do_list_openable(self, tmp_path):
        from src.mcp_server.openwith_server import OpenWithMCPServer
        s = OpenWithMCPServer(write_root=str(tmp_path))
        out = s._do_list_openable({})
        payload = json.loads(out[0].text)
        assert payload.get("ok") is True
        assert payload["write_root"] == tmp_path.resolve().as_posix()

    def test_server_module_imports_clean(self):
        mod = __import__(
            "src.mcp_server.openwith_server", fromlist=["x"],
        )
        assert hasattr(mod, "OpenWithMCPServer")
        assert hasattr(mod, "main")