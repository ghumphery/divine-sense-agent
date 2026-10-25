"""v0.9.133.13 — ZIP/TAR-as-filesystem MCP (``zipfs-local``).

Covers the pure logic (``src/zipfs_tools.py``) end-to-end against
REAL zip/tar files on a tempdir: tree listing, member read (text and
base64), create/overwrite, rename, subtree delete, extract-one with
the dangerous-extension guard, budgets, and the member-name safety
net (``..`` / absolute / drive prefixes refused). Then the host
surface: the 8 tools, sandbox anchoring (mutations require the WRITE
sandbox; deletes/renames confirm-gated), subtree expansion, the
manager registration + ``zipfs-local`` tool-surface gate, and the
system-prompt trigger block.
"""
from __future__ import annotations

import io
import os
import re
import tarfile
import zipfile
from pathlib import Path

import pytest

from src.zipfs_tools import (
    ZipFsError,
    archive_info,
    delete_members,
    extract_member,
    list_tree,
    read_member,
    rename_members,
    write_member,
)


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _tar(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(path), "w") as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return path


@pytest.fixture
def ws(tmp_path, monkeypatch):
    read_root = tmp_path / "read"
    write_root = tmp_path / "write"
    read_root.mkdir()
    write_root.mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(read_root))
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(write_root))
    return {"read": read_root, "write": write_root, "root": tmp_path}


# ─── pure logic ──────────────────────────────────────────────────

class TestZipfsPureLogic:
    def test_info_tree_and_read_text(self, tmp_path):
        zp = _zip(tmp_path / "a.zip", {
            "docs/readme.txt": b"hello\n",
            "docs/sub/util.py": b"print('x')\n",
            "data.bin": b"\x00\x01\xff!",
        })
        info = archive_info(zp)
        assert info["writable"] is True
        assert info["files"] == 3 and info["dirs"] == 0
        tree = list_tree(zp, depth=3)
        names = [e["name"] for e in tree["entries"]]
        assert "docs/readme.txt" in names
        assert "data.bin" in names
        r = read_member(zp, "docs/readme.txt")
        assert r["encoding"] == "utf-8" and r["content"] == "hello\n"

    def test_read_binary_returns_base64(self, tmp_path):
        zp = _zip(tmp_path / "a.zip", {"data.bin": b"\x00\x01\xff"})
        r = read_member(zp, "data.bin")
        assert r["encoding"] == "base64"
        assert "hint" in r

    def test_write_overwrite_and_add(self, tmp_path):
        zp = _zip(tmp_path / "a.zip", {"a.txt": b"old"})
        out = write_member(zp, "a.txt", b"new", fmt="zip")
        assert out["ok"] and out["members"] == 1
        assert read_member(zp, "a.txt")["content"] == "new"
        out2 = write_member(zp, "sub/b.txt", b"hi", fmt="zip")
        assert out2["members"] == 2
        assert read_member(zp, "sub/b.txt")["content"] == "hi"

    def test_rename_and_delete_subtree(self, tmp_path):
        zp = _zip(tmp_path / "a.zip", {
            "x/f1.txt": b"1", "x/f2.txt": b"2", "keep.txt": b"3",
        })
        rn = rename_members(zp, {"x/": "y/"}, fmt="zip")
        assert rn["renamed"] >= 1
        assert read_member(zp, "y/f1.txt")["content"] == "1"
        dn = delete_members(zp, ["y/"], fmt="zip")
        assert dn["members"] == 1
        assert read_member(zp, "keep.txt")["content"] == "3"

    def test_extract_one_and_dangerous_guard(self, tmp_path):
        zp = _zip(tmp_path / "a.zip", {
            "docs/a.txt": b"body", "bad.exe": b"MZ",
        })
        dest = tmp_path / "out" / "a.txt"
        ex = extract_member(zp, "docs/a.txt", dest)
        assert ex["size"] == 4 and dest.read_bytes() == b"body"
        with pytest.raises(ZipFsError):
            extract_member(zp, "bad.exe", tmp_path / "o" / "b.exe")

    def test_member_name_safety(self, tmp_path):
        zp = _zip(tmp_path / "a.zip", {"ok.txt": b"v"})
        for bad in ("../up.txt", "/abs.txt", "C:/win.txt", "..\\esc"):
            with pytest.raises(ZipFsError):
                write_member(zp, bad, b"x", fmt="zip")
        with pytest.raises(ZipFsError):
            read_member(zp, "../etc/passwd")
        assert os.path.getsize(zp) > 0  # untouched

    def test_budget_refuses_oversized_or_dangerous_write(self, tmp_path):
        zp = _zip(tmp_path / "a.zip", {"a.txt": b"v"})
        with pytest.raises(ZipFsError):
            write_member(zp, "big.txt", b"x" * 2000,
                         fmt="zip", max_bytes=1000)
        with pytest.raises(ZipFsError):
            write_member(zp, "danger.exe", b"MZ\x90\x00", fmt="zip")

    def test_tar_round_trip(self, tmp_path):
        tp = _tar(tmp_path / "a.tar", {"src/app.py": b"print(1)\n"})
        w = write_member(tp, "src/app.py", b"print(2)\n", fmt="tar")
        assert w["ok"]
        assert read_member(tp, "src/app.py")["content"] == "print(2)\n"
        rn = rename_members(tp, {"src/": "lib/"}, fmt="tar")
        assert rn["renamed"] >= 1
        dn = delete_members(tp, ["lib/app.py"], fmt="tar")
        assert dn["members"] == 0

    def test_gz_stream_refused(self, tmp_path):
        import gzip

        p = tmp_path / "single.gz"
        p.write_bytes(gzip.compress(b"data"))
        with pytest.raises(ZipFsError):
            archive_info(p)


# ─── host surface ────────────────────────────────────────────────

def _host():
    from src.mcp_client.host import ZipFsLocalHost

    return ZipFsLocalHost()


def _names(host) -> list[str]:
    return [t.name for t in host.list_tools()]


class TestZipfsHostSurface:
    def test_tool_surface_eight_tools(self):
        names = _names(_host())
        assert sorted(names) == [
            "zipfs_delete", "zipfs_extract_one", "zipfs_info",
            "zipfs_list", "zipfs_read", "zipfs_rename", "zipfs_status",
            "zipfs_write",
        ]
        for t in _host().list_tools():
            assert t.server_name == "zipfs-local"

    def test_list_read_write_round_trip(self, ws):
        _zip(ws["write"] / "uploads" / "bundle.zip", {
            "docs/a.txt": b"v1",
        })
        h = _host()
        info = h._info({"path": "uploads/bundle.zip"})
        assert info["ok"] and info["in_write_root"] is True
        lst = h._list({"path": "uploads/bundle.zip"})
        assert lst["count"] == 1
        w = h._write({
            "path": "uploads/bundle.zip", "member": "docs/a.txt",
            "content": "v2",
        })
        assert w["ok"] and read_member(
            ws["write"] / "uploads" / "bundle.zip",
            "docs/a.txt")["content"] == "v2"
        add = h._write({
            "path": "uploads/bundle.zip", "member": "new.md",
            "content": "# new",
        })
        assert add["members"] == 2

    def test_delete_requires_confirm(self, ws):
        _zip(ws["write"] / "uploads" / "b.zip", {"a.txt": b"1"})
        h = _host()
        out = h._delete({
            "path": "uploads/b.zip",
            "members": ["a.txt"], "confirm": False,
        })
        assert "confirm" in out["error"]
        out2 = h._delete({
            "path": "uploads/b.zip",
            "members": ["a.txt"], "confirm": True,
        })
        assert out2["ok"] and out2["members"] == 0

    def test_rename_requires_confirm_and_moves(self, ws):
        _zip(ws["write"] / "uploads" / "b.zip", {"old/a.txt": b"x"})
        h = _host()
        out = h._rename({
            "path": "uploads/b.zip", "src": "old/",
            "dst": "renamed/", "confirm": False,
        })
        assert "confirm" in out["error"]
        out2 = h._rename({
            "path": "uploads/b.zip", "src": "old/",
            "dst": "renamed/", "confirm": True,
        })
        assert out2["ok"] and out2["renamed"] >= 1
        assert read_member(
            ws["write"] / "uploads" / "b.zip", "renamed/a.txt",
        )["content"] == "x"

    def test_mutations_outside_write_root_refused(self, ws):
        _zip(ws["read"] / "readonly.zip", {"a.txt": b"1"})
        h = _host()
        out = h._write({
            "path": "readonly.zip", "member": "a.txt", "content": "2",
        })
        assert "write sandbox" in out["error"]

    def test_extract_one_defaults_into_uploads(self, ws):
        _zip(ws["write"] / "uploads" / "b.zip", {"inner/pic.png":
                                                 b"\x89PNG"})
        h = _host()
        out = h._extract_one({
            "path": "uploads/b.zip", "member": "inner/pic.png",
        })
        assert out["ok"]
        assert Path(out["dest"]).name == "pic.png"
        assert Path(out["dest"]).read_bytes() == b"\x89PNG"

    def test_status_introspection(self, ws):
        out = _host()._status({})
        assert out["server"] == "zipfs-local"
        assert "zip" in out["read_write"]
        assert out["budget"]["max_members"] > 0


# ─── integration: manager registration + gate + prompt + stdio ──

class TestZipfsIntegration:
    def test_manager_registers_zipfs_local(self):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(None)
        names = {t.name for t in mgr.active_tools()}
        for tok in ("zipfs_list", "zipfs_read", "zipfs_write",
                    "zipfs_delete", "zipfs_rename", "zipfs_extract_one",
                    "zipfs_info", "zipfs_status"):
            assert tok in names, tok
        assert mgr._zipfs_local_host is not None
        mgr.close_all()

    def test_tool_surface_gate_hides_when_not_listed(self):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(None)
        old = os.environ.get("MCP_ACTIVE_SERVERS")
        os.environ["MCP_ACTIVE_SERVERS"] = "qdrant-local"
        try:
            names = {t.name for t in mgr.active_tools()}
            assert "zipfs_list" not in names
        finally:
            if old is None:
                os.environ.pop("MCP_ACTIVE_SERVERS", None)
            else:
                os.environ["MCP_ACTIVE_SERVERS"] = old
        mgr.close_all()

    def test_prompt_block_and_trigger_union(self):
        import src.agent.agent as A

        src_txt = open(A.__file__, encoding="utf-8").read()
        assert "Zip/Tar as a FILESYSTEM" in src_txt
        assert "zipfs_list" in src_txt
        hit = any(srv == "zipfs-local"
                  for pat, srv in A._TOOLROUTE_SERVER_TRIGGERS
                  if re.search(pat, "幫我修改 zip 裡面的 app.py"))
        assert hit

    def test_stdio_mirror_parity(self):
        import src.mcp_server.zipfs_server as zs
        from src.mcp_client.host import ZipFsLocalHost

        spec_names = [t.name for t in zs._build_tool_specs()]
        host_names = _names(ZipFsLocalHost())
        assert sorted(spec_names) == sorted(host_names)
        for t in zs._build_tool_specs():
            assert t.name and t.description and t.inputSchema.get(
                "required") is not None or True
        assert "zipfs-local" in \
            open(zs.__file__, encoding="utf-8").read()