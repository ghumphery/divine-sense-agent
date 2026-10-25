"""v0.9.5+ drawio viewer window tests.

No pywebview is ever actually spawned — we patch
``webview.create_window`` with a MagicMock so the manager's
``_open()`` returns immediately. Disk writes happen against
a tmp_path sandbox; ``QDRANT_FILESYSTEM_WRITE_ROOT`` is
patched in for each test.

Run: ``pytest tests/test_diagrams_window.py -v``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── fixtures ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _quiet_gui_loggers():
    """v0.9.96.1 — manager-failure tests deliberately make
    DiagramsWindowManager RAISE; ``gui.api``'s logger.exception then
    writes the traceback into the PRODUCTION gui/app.log. Detach the
    gui loggers from the root handlers for each test."""
    import logging

    for name in ("gui.web", "gui.api", "gui"):
        logging.getLogger(name).propagate = False
    yield
    for name in ("gui.web", "gui.api", "gui"):
        logging.getLogger(name).propagate = True


@pytest.fixture
def write_root(tmp_path, monkeypatch):
    """Point QDRANT_FILESYSTEM_WRITE_ROOT at a per-test tmp dir."""
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(root))
    return root


class _FakeWebview:
    """Bag of attributes for tests — avoids ``fake_webview.0``
    tuple unpacking dance. Holds the per-window fake plus a
    ``create_window`` MagicMock that is wired into the
    stubbed ``webview`` module so tests can introspect
    call_count / call_args while still getting a different
    ``window`` per call when needed."""

    def __init__(self) -> None:
        self.window = MagicMock()
        self.window.events.closed = MagicMock()
        # Default: every create_window call returns the SAME
        # fake window. Tests that need per-call distinct
        # windows set ``self.create_window.side_effect = [...]``.
        self.create_window = MagicMock(return_value=self.window)

    # Back-compat: the original fixture returned a tuple
    # ``(fake_module, fake_window)``. Several tests still
    # write ``_, _ = fake_webview`` / ``_, fake_window = fake_webview``
    # — keep those working by exposing the same 2-tuple shape
    # via __iter__.
    def __iter__(self):
        yield self
        yield self.window


@pytest.fixture
def fake_webview(monkeypatch):
    """Patch ``webview.create_window`` so we never spawn a real
    OS window during tests. Returns a ``_FakeWebview`` whose
    ``.create_window`` is the actual MagicMock the test can
    introspect (call_count, call_args, side_effect, ...)."""
    fw = _FakeWebview()
    module = MagicMock()
    # Wire the SAME MagicMock into the module so test-side
    # ``fw.create_window.call_count`` reflects what the
    # diagrams_window module sees.
    module.create_window = fw.create_window
    sys.modules["webview"] = module
    fw.module = module
    yield fw
    sys.modules.pop("webview", None)


def _make_manager(write_root, fake_webview):
    """Construct a DiagramsWindowManager with the patched
    webview module pre-injected."""
    from gui.diagrams_window import DiagramsWindowManager
    mgr = DiagramsWindowManager()
    # Replace the manager's resolved write_root with the test
    # sandbox — the constructor already mkdir()ed a fresh one.
    mgr._write_root = write_root
    return mgr


# ── module structure ───────────────────────────────────────────


def test_module_imports_cleanly():
    """Sanity: the module imports without side-effects."""
    import gui.diagrams_window as d
    assert hasattr(d, "DiagramsWindowManager")
    assert hasattr(d, "_DiagramsViewerApi")
    assert hasattr(d, "_DIAGRAM_MAX_XML_BYTES")
    # Cap is 4 MB by design.
    assert d._DIAGRAM_MAX_XML_BYTES == 4 * 1024 * 1024


def test_diagrams_viewer_api_surface():
    """The per-window Api class must expose exactly the
    methods called by gui/assets/diagrams.js."""
    from gui.diagrams_window import _DiagramsViewerApi
    expected = {
        "save_to_sandbox", "read_from_sandbox",
        "get_write_root", "ping",
    }
    actual = {m for m in dir(_DiagramsViewerApi)
              if not m.startswith("_")}
    assert expected <= actual, (
        f"_DiagramsViewerApi missing methods: {expected - actual}"
    )


# ── open_blank ─────────────────────────────────────────────────


class TestOpenBlank:
    def test_returns_ok_with_title(self, write_root, fake_webview):
        _, fake_window = fake_webview
        mgr = _make_manager(write_root, fake_webview)
        r = mgr.open_blank(title="Test Viewer")
        assert r.get("ok") is True
        assert r["title"] == "Test Viewer"
        assert r["open_count"] == 1
        # pywebview.create_window must have been called once with
        # the diagrams.html file (we don't open a real window).
        assert fake_webview.create_window.call_count == 1
        kw = fake_webview.create_window.call_args.kwargs
        assert kw["title"] == "Test Viewer"
        assert "diagrams.html" in kw["url"]
        assert kw["width"] == 1100
        assert kw["height"] == 760
        # No initial XML, so no evaluate_js queueing.
        assert fake_window.evaluate_js.call_count == 0

    def test_default_title(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        r = mgr.open_blank()
        assert r["title"] == "Drawio Viewer"


# ── open_with_xml ──────────────────────────────────────────────


class TestOpenWithXml:
    def test_happy_path(self, write_root, fake_webview):
        _, fake_window = fake_webview
        mgr = _make_manager(write_root, fake_webview)
        xml = '<mxfile><diagram id="d1"/></mxfile>'
        r = mgr.open_with_xml("From chat", xml)
        assert r.get("ok") is True
        # evaluate_js must have been queued with the load event.
        assert fake_window.evaluate_js.call_count == 1
        js_expr = fake_window.evaluate_js.call_args.args[0]
        # The custom event name + escaped XML.
        assert "drawio-load-xml" in js_expr
        assert "mxfile" in js_expr  # json-encoded XML inlined

    def test_rejects_empty(self, write_root, fake_webview):
        _, fake_window = fake_webview
        mgr = _make_manager(write_root, fake_webview)
        r = mgr.open_with_xml("X", "")
        assert "error" in r
        assert "non-empty" in r["error"]
        assert fake_webview.create_window.call_count == 0

    def test_rejects_non_string(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        r = mgr.open_with_xml("X", 12345)
        assert "error" in r
        assert "string" in r["error"]

    def test_rejects_oversize(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        big = "x" * (4 * 1024 * 1024 + 1)
        r = mgr.open_with_xml("X", big)
        assert "error" in r
        assert "cap" in r["error"].lower()

    def test_unicode_preserved(self, write_root, fake_webview):
        _, fake_window = fake_webview
        mgr = _make_manager(write_root, fake_webview)
        xml = '<mxfile 中文="x"></mxfile>'
        mgr.open_with_xml("Unicode", xml)
        js_expr = fake_window.evaluate_js.call_args.args[0]
        assert "中文" in js_expr or "u4e2d" in js_expr.lower()


# ── open_with_path ─────────────────────────────────────────────


class TestOpenWithPath:
    def _write_xml(self, root, rel="hello.drawio"):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            '<mxfile><diagram id="d"/></mxfile>',
            encoding="utf-8",
        )
        return target

    def test_happy_path(self, write_root, fake_webview):
        _, fake_window = fake_webview
        mgr = _make_manager(write_root, fake_webview)
        target = self._write_xml(write_root, "foo.drawio")
        r = mgr.open_with_path("Loaded", "foo.drawio")
        assert r.get("ok") is True
        assert r["source_path"] == target.as_posix()
        assert r["open_count"] == 1
        # XML pushed to the new window.
        assert fake_window.evaluate_js.call_count == 1

    def test_relative_with_subdir(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        self._write_xml(write_root, "nested/sub/hello.drawio")
        r = mgr.open_with_path("Nested", "nested/sub/hello.drawio")
        assert r.get("ok") is True

    def test_missing_file(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        r = mgr.open_with_path("X", "ghost.drawio")
        assert "error" in r
        assert "not found" in r["error"]
        assert fake_webview.create_window.call_count == 0

    def test_rejects_directory(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        (write_root / "subdir.drawio").mkdir()
        r = mgr.open_with_path("X", "subdir.drawio")
        assert "error" in r
        assert "regular file" in r["error"]

    def test_wrong_extension(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        bad = write_root / "foo.txt"
        bad.write_text("hello", encoding="utf-8")
        r = mgr.open_with_path("X", "foo.txt")
        assert "error" in r
        assert "extension" in r["error"]

    def test_accepts_xml_extension(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        (write_root / "a.xml").write_text(
            '<mxfile/>', encoding="utf-8",
        )
        r = mgr.open_with_path("X", "a.xml")
        assert r.get("ok") is True

    def test_accepts_dio_extension(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        (write_root / "a.dio").write_text(
            '<mxfile/>', encoding="utf-8",
        )
        r = mgr.open_with_path("X", "a.dio")
        assert r.get("ok") is True

    def test_rejects_non_utf8(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        bad = write_root / "broken.drawio"
        bad.write_bytes(b'\xff\xfe\xff' + b'<mxfile/>')
        r = mgr.open_with_path("X", "broken.drawio")
        assert "error" in r
        assert "utf-8" in r["error"].lower()

    def test_empty_path(self, write_root, fake_webview):
        mgr = _make_manager(write_root, fake_webview)
        r = mgr.open_with_path("X", "")
        assert "error" in r


# ── close_all ──────────────────────────────────────────────────


class TestCloseAll:
    def test_destroy_each_window(self, write_root, fake_webview):
        _, _ = fake_webview
        # We need 3 distinct fake windows.
        win1, win2, win3 = MagicMock(), MagicMock(), MagicMock()
        for w in (win1, win2, win3):
            w.events.closed = MagicMock()
        fake_webview.create_window.side_effect = [
            win1, win2, win3,
        ]
        mgr = _make_manager(write_root, fake_webview)
        mgr.open_blank("a")
        mgr.open_blank("b")
        mgr.open_blank("c")
        assert mgr.open_count == 3
        mgr.close_all()
        win1.destroy.assert_called_once()
        win2.destroy.assert_called_once()
        win3.destroy.assert_called_once()
        assert mgr.open_count == 0

    def test_destroy_swallows_exceptions(
        self, write_root, fake_webview,
    ):
        _, _ = fake_webview
        win1, win2 = MagicMock(), MagicMock()
        for w in (win1, win2):
            w.events.closed = MagicMock()
        win2.destroy.side_effect = RuntimeError("already gone")
        fake_webview.create_window.side_effect = [win1, win2]
        mgr = _make_manager(write_root, fake_webview)
        mgr.open_blank("a")
        mgr.open_blank("b")
        # Must not raise even if one destroy() fails.
        mgr.close_all()
        win1.destroy.assert_called_once()
        win2.destroy.assert_called_once()
        assert mgr.open_count == 0


# ── save_to_sandbox ────────────────────────────────────────────


class TestSaveToSandbox:
    def _setup(self, write_root, fake_webview):
        return _make_manager(write_root, fake_webview)

    def test_happy_path(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        xml = '<mxfile><diagram id="d"/></mxfile>'
        r = mgr.save_to_sandbox("hello.drawio", xml)
        assert r.get("ok") is True
        assert r["bytes_written"] == len(xml.encode("utf-8"))
        target = write_root / "hello.drawio"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == xml
        assert r["relative_path"] == "hello.drawio"

    def test_default_extension(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        r = mgr.save_to_sandbox("no-ext", "<mxfile/>")
        assert r.get("ok") is True
        assert (write_root / "no-ext.drawio").exists()

    def test_rejects_wrong_extension(
        self, write_root, fake_webview,
    ):
        mgr = self._setup(write_root, fake_webview)
        r = mgr.save_to_sandbox("a.txt", "<mxfile/>")
        assert "error" in r
        assert "extension" in r["error"]
        assert not (write_root / "a.txt").exists()

    def test_rejects_dangerous_extension(
        self, write_root, fake_webview,
    ):
        """Files with dangerous-write extensions (``.bat``,
        ``.sh``, ``.ps1``, ``.exe``, ``.vbs``, ...) are
        refused before any disk I/O. The error message must
        mention either ``dangerous`` or ``unsupported`` —
        the diagram-allowed whitelist is checked first, and
        dangerous exts aren't in it anyway. Either rejection
        path is acceptable."""
        mgr = self._setup(write_root, fake_webview)
        for ext in (".bat", ".sh", ".ps1", ".exe", ".vbs"):
            r = mgr.save_to_sandbox(f"a{ext}", "<mxfile/>")
            assert "error" in r, f"no error for {ext}: {r}"
            low = r["error"].lower()
            assert ("dangerous" in low) or ("unsupported" in low), (
                f"unexpected error for {ext}: {r['error']!r}"
            )
            assert not (write_root / f"a{ext}").exists()

    def test_rejects_oversize(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        big = "x" * (4 * 1024 * 1024 + 1)
        r = mgr.save_to_sandbox("big.drawio", big)
        assert "error" in r

    def test_overwrite_default_true(
        self, write_root, fake_webview,
    ):
        mgr = self._setup(write_root, fake_webview)
        mgr.save_to_sandbox("a.drawio", "<mxfile>v1</mxfile>")
        r = mgr.save_to_sandbox("a.drawio", "<mxfile>v2</mxfile>")
        assert r.get("ok") is True
        assert r["overwrite"] is True
        txt = (write_root / "a.drawio").read_text(encoding="utf-8")
        assert "v2" in txt
        assert "v1" not in txt

    def test_overwrite_false_blocks(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        mgr.save_to_sandbox("a.drawio", "<mxfile>v1</mxfile>")
        r = mgr.save_to_sandbox(
            "a.drawio", "<mxfile>v2</mxfile>", overwrite=False,
        )
        assert "error" in r
        assert "exists" in r["error"]
        txt = (write_root / "a.drawio").read_text(encoding="utf-8")
        assert "v1" in txt

    def test_atomic_no_partial_on_overwrite(
        self, write_root, fake_webview,
    ):
        """Even if os.replace fails mid-stream, the destination
        must remain the original content (no half-written
        'a.drawio' with 0 bytes)."""
        mgr = self._setup(write_root, fake_webview)
        original = "<mxfile>orig</mxfile>"
        mgr.save_to_sandbox("a.drawio", original)
        with patch(
            "gui.diagrams_window.os.replace",
            side_effect=OSError("disk full"),
        ):
            r = mgr.save_to_sandbox("a.drawio", "<mxfile>new</mxfile>")
        assert "error" in r
        assert (write_root / "a.drawio").read_text(
            encoding="utf-8",
        ) == original
        leftover = list(write_root.glob(".drawio.*"))
        assert leftover == []

    def test_writes_into_nested_subdir(
        self, write_root, fake_webview,
    ):
        mgr = self._setup(write_root, fake_webview)
        (write_root / "diagrams" / "sub").mkdir(parents=True)
        r = mgr.save_to_sandbox(
            "diagrams/sub/h.drawio", "<mxfile/>",
        )
        assert r.get("ok") is True
        assert (write_root / "diagrams" / "sub" / "h.drawio").exists()

    def test_missing_parent_dir(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        # The parent dir doesn't exist — save must refuse
        # (no recursive mkdir; that would surprise the user).
        r = mgr.save_to_sandbox(
            "nope/missing/a.drawio", "<mxfile/>",
        )
        assert "error" in r
        assert "parent" in r["error"].lower()

    def test_empty_inputs(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        assert "error" in mgr.save_to_sandbox("", "<mxfile/>")
        assert "error" in mgr.save_to_sandbox("a.drawio", "")
        assert "error" in mgr.save_to_sandbox("a.drawio", None)
        assert "error" in mgr.save_to_sandbox("a.drawio", 123)


# ── read_from_sandbox ─────────────────────────────────────────


class TestReadFromSandbox:
    def _setup(self, write_root, fake_webview):
        return _make_manager(write_root, fake_webview)

    def test_roundtrip(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        text = "<mxfile>x</mxfile>"
        (write_root / "x.drawio").write_text(
            text, encoding="utf-8",
        )
        r = mgr.read_from_sandbox("x.drawio")
        assert r.get("ok") is True
        assert r["xml"] == text
        assert r["bytes"] == len(text.encode("utf-8"))

    def test_missing_file(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        r = mgr.read_from_sandbox("ghost.drawio")
        assert "error" in r
        assert "not found" in r["error"]

    def test_wrong_extension(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        (write_root / "a.png").write_bytes(b"\x89PNG\r\n")
        r = mgr.read_from_sandbox("a.png")
        assert "error" in r
        assert "extension" in r["error"]

    def test_empty_path(self, write_root, fake_webview):
        mgr = self._setup(write_root, fake_webview)
        r = mgr.read_from_sandbox("")
        assert "error" in r

    def test_escape_attempt_blocked(
        self, write_root, fake_webview,
    ):
        """Path traversal: ``../etc/passwd`` must NOT escape
        the sandbox even though the literal ``..`` exists in
        the filename."""
        mgr = self._setup(write_root, fake_webview)
        r = mgr.read_from_sandbox("../etc/passwd.drawio")
        assert "error" in r
        outside = write_root.parent / "etc"
        assert not outside.exists()


# ── manager lifecycle ──────────────────────────────────────────


class TestManagerLifecycle:
    def test_write_root_is_absolute(
        self, write_root, fake_webview,
    ):
        mgr = _make_manager(write_root, fake_webview)
        assert mgr._write_root.is_absolute()

    def test_open_count_starts_at_zero(
        self, write_root, fake_webview,
    ):
        mgr = _make_manager(write_root, fake_webview)
        assert mgr.open_count == 0

    def test_multiple_opens_increment(
        self, write_root, fake_webview,
    ):
        mgr = _make_manager(write_root, fake_webview)
        mgr.open_blank("a")
        mgr.open_blank("b")
        mgr.open_blank("c")
        assert mgr.open_count == 3

    def test_per_window_api_bindings(
        self, write_root, fake_webview,
    ):
        """Each open window must get its own per-window Api
        (not the main API) so the viewer's save_to_sandbox
        calls don't re-enter the main GUI's Api."""
        from gui.diagrams_window import _DiagramsViewerApi
        api_calls = []

        def _capture(**kw):
            # Capture js_api, then return our pre-built fake
            # window. We deliberately do NOT re-enter
            # ``fake_webview.create_window`` (that would
            # recurse / double-count).
            api_calls.append(kw.get("js_api"))
            return fake_webview.window

        fake_webview.create_window.side_effect = _capture
        mgr = _make_manager(write_root, fake_webview)
        mgr.open_blank("a")
        mgr.open_blank("b")
        assert len(api_calls) == 2
        for api_obj in api_calls:
            assert isinstance(api_obj, _DiagramsViewerApi)
        # Each must be its own instance.
        assert api_calls[0] is not api_calls[1]


# ── gui/api.py integration ─────────────────────────────────────


class TestApiBinding:
    """The Api methods exposed to the main JS bridge must
    route through the manager and return ``{"error": ...}``
    on any failure."""

    @pytest.fixture
    def api_obj(self, write_root, fake_webview):
        from gui.api import Api
        Api._diagrams_manager = None  # reset cache
        api = Api()
        return api

    def test_open_diagrams_window_method_exists(self, api_obj):
        assert callable(api_obj.open_diagrams_window)
        assert callable(api_obj.open_diagrams_with_xml)
        assert callable(api_obj.open_diagrams_with_path)
        assert callable(api_obj.diagrams_window_status)

    def test_open_diagrams_window_routes(
        self, api_obj, fake_webview,
    ):
        r = api_obj.open_diagrams_window()
        assert r.get("ok") is True
        assert fake_webview.create_window.call_count == 1

    def test_open_diagrams_with_xml_routes(
        self, api_obj, fake_webview,
    ):
        r = api_obj.open_diagrams_with_xml(
            "From chat", "<mxfile/>",
        )
        assert r.get("ok") is True
        assert fake_webview.create_window.call_count == 1

    def test_open_diagrams_with_path_routes(
        self, api_obj, fake_webview, write_root,
    ):
        (write_root / "x.drawio").write_text(
            "<mxfile/>", encoding="utf-8",
        )
        r = api_obj.open_diagrams_with_path("X", "x.drawio")
        assert r.get("ok") is True

    def test_diagrams_window_status_ok(self, api_obj):
        r = api_obj.diagrams_window_status()
        assert r.get("ok") is True
        assert isinstance(r["open_count"], int)
        assert r["open_count"] >= 0

    def test_manager_exception_returns_error_dict(
        self, api_obj,
    ):
        """A crashing manager must NOT propagate; the Api
        method must swallow and return ``{"error": ...}``."""
        with patch.object(
            api_obj, "_get_diagrams_manager",
            side_effect=RuntimeError("manager blew up"),
        ):
            r = api_obj.open_diagrams_window()
        assert r.get("ok") != True
        assert "error" in r
        assert "manager blew up" in r["error"]

    def test_lazy_manager_cache_is_class_level(self, api_obj):
        """The handle is a class attribute so a second Api()
        instance reuses the same manager."""
        from gui.api import Api
        m1 = api_obj._get_diagrams_manager()
        m2 = Api._diagrams_manager
        assert m1 is m2

    # ── v0.9.5.4: get_drawio_url + open_drawio_in_browser ────

    def test_get_drawio_url_returns_constant(self, write_root):
        """``_DiagramsViewerApi.get_drawio_url`` returns the
        single source of truth (``_DRAWIO_EMBED_URL``) so JS
        doesn't have to hardcode the URL.
        """
        from gui.diagrams_window import (
            _DRAWIO_EMBED_URL, _DiagramsViewerApi,
            DiagramsWindowManager,
        )
        mgr = DiagramsWindowManager()
        api = _DiagramsViewerApi(mgr, write_root)
        assert api.get_drawio_url() == _DRAWIO_EMBED_URL
        assert "embed.diagrams.net" in api.get_drawio_url()
        assert "proto=json" in api.get_drawio_url()

    def test_open_drawio_in_browser_invokes_webbrowser(
        self, write_root, monkeypatch,
    ):
        """``open_drawio_in_browser`` delegates to the stdlib
        ``webbrowser.open`` so we don't need a GUI to verify
        it. Returns ``{"ok": True, "url": ...}`` with the
        same URL constant.
        """
        from gui.diagrams_window import (
            _DRAWIO_EMBED_URL, _DiagramsViewerApi,
            DiagramsWindowManager,
        )
        mgr = DiagramsWindowManager()
        api = _DiagramsViewerApi(mgr, write_root)
        captured: list[str] = []
        # Patch webbrowser.open inside the diagrams_window
        # module (it's imported lazily inside the method).
        import webbrowser as _wb
        monkeypatch.setattr(_wb, "open", lambda url, *a, **k: captured.append(url) or True)
        # The function does ``import webbrowser`` lazily — the
        # module-level ``webbrowser`` is the same object so the
        # patch propagates.
        r = api.open_drawio_in_browser()
        assert r.get("ok") is True
        assert r["url"] == _DRAWIO_EMBED_URL
        assert captured == [_DRAWIO_EMBED_URL]

    def test_open_drawio_in_browser_exception_returns_error(
        self, write_root, monkeypatch,
    ):
        """If ``webbrowser.open`` raises (e.g. no DISPLAY on
        Linux / headless), the API still returns ``{"error": ...}``
        rather than letting the exception propagate (the
        pywebview JS bridge would otherwise lose the message).
        """
        from gui.diagrams_window import (
            _DiagramsViewerApi, DiagramsWindowManager,
        )
        mgr = DiagramsWindowManager()
        api = _DiagramsViewerApi(mgr, write_root)
        import webbrowser as _wb
        def _boom(url):
            raise RuntimeError("no browser registered")
        monkeypatch.setattr(_wb, "open", _boom)
        r = api.open_drawio_in_browser()
        assert "error" in r
        assert "no browser registered" in r["error"]


# ── app.py close hook ─────────────────────────────────────────


class TestAppCloseHook:
    """gui/app.py's ``closed`` event handler must invoke
    ``close_all`` on the diagrams manager so the OS doesn't
    end up with orphan viewer processes."""

    def test_app_close_handler_calls_close_all(self):
        import gui.app as app_mod
        import inspect
        src = inspect.getsource(app_mod)
        assert "close_all" in src
        assert "diagrams_manager" in src


# ── index.html + JS source sanity ──────────────────────────────


class TestGuiAssetsWiring:
    def test_index_html_toolbar_diagrams_button_removed(self):
        """v0.9.5.8: the useless chat-toolbar "📊 Diagrams Viewer"
        button (blank viewer opener) was removed from the main
        GUI. The per-message "View in Diagrams" buttons in the
        chat log remain — those carry the actual XML."""
        text = Path(
            "gui/assets/index.html",
        ).read_text(encoding="utf-8")
        assert "chat-diagrams-btn" not in text
        assert "diagramsViewer" not in text

    def test_app_js_toolbar_handler_removed(self):
        """v0.9.5.8: the toolbar button's click handler (and its
        ``open_diagrams_window`` call) must be gone from app.js,
        while the extraction machinery stays for the per-message
        buttons."""
        text = Path(
            "gui/assets/app.js",
        ).read_text(encoding="utf-8")
        assert "chat-diagrams-btn" not in text
        assert "open_diagrams_window" not in text
        assert "diagramsViewer" not in text
        assert "_extractDiagramXml" in text
        assert "mxfile" in text

    def test_app_js_renders_view_button_per_message(self):
        text = Path(
            "gui/assets/app.js",
        ).read_text(encoding="utf-8")
        assert "open_diagrams_with_xml" in text
        assert "btn-msg-diagrams" in text
        assert "View in Diagrams" in text

    def test_diagrams_assets_exist(self):
        assert Path("gui/assets/diagrams.html").exists()
        assert Path("gui/assets/diagrams.js").exists()
        assert Path("gui/assets/diagrams.css").exists()

    def test_diagrams_js_calls_api_methods(self):
        text = Path(
            "gui/assets/diagrams.js",
        ).read_text(encoding="utf-8")
        assert "drawio-load-xml" in text
        assert "window.pywebview.api" in text
        assert "save_to_sandbox" in text
        assert "read_from_sandbox" in text
        assert "get_write_root" in text
        assert ".ping(" in text


# ── JS extraction logic (run via Node) ────────────────────────


@pytest.fixture(scope="module")
def _extract_fn_js():
    """Extract the ``_extractDiagramXml`` body from app.js so
    we can eval it in a pristine Node VM. This lets us test
    the regex against real-world LLM output without spawning
    a browser.

    Skips the entire module if Node is not on PATH."""
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node not on PATH; skipping JS extraction tests")
    import re
    text = Path("gui/assets/app.js").read_text(encoding="utf-8")
    m = re.search(
        r"function _extractDiagramXml\([\s\S]+?\n  \}",
        text,
    )
    if not m:
        pytest.fail("_extractDiagramXml not found in app.js")
    return m.group(0)


class TestJsExtractDiagramXml:
    """Pure-JS regex logic for picking ``<mxfile>`` blocks out
    of chat messages. We exec the function body via Node and
    exercise the cases we know LLMs actually produce."""

    def _run(self, fn_body, cases):
        import json, subprocess
        # Build a JS one-liner that returns a JSON map of
        # case_name -> result.
        cases_json = json.dumps(cases, ensure_ascii=False)
        script = (
            f"{fn_body}\n"
            f"var cases = {cases_json};\n"
            "var out = {};\n"
            "for (var k in cases) {\n"
            "  out[k] = _extractDiagramXml(cases[k]);\n"
            "}\n"
            "process.stdout.write(JSON.stringify(out));"
        )
        r = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert r.returncode == 0, (
            f"node crashed: stdout={r.stdout!r} "
            f"stderr={r.stderr!r}"
        )
        return json.loads(r.stdout)

    def test_bare_mxfile(self, _extract_fn_js):
        out = self._run(_extract_fn_js, {
            "a": "<mxfile><diagram id='d1'/></mxfile>",
        })
        assert "<mxfile" in out["a"]
        assert "<diagram" in out["a"]

    def test_fenced_xml_block(self, _extract_fn_js):
        body = (
            "Sure, here's the diagram:\n\n"
            "```xml\n"
            '<mxfile version="1.0">'
            "<diagram id='d1'>"
            "<mxCell/></diagram></mxfile>\n"
            "```\n\n"
            "Does this help?"
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"].startswith("<mxfile")
        assert "</mxfile>" in out["a"]

    def test_fenced_no_language_hint(self, _extract_fn_js):
        body = (
            "Here:\n"
            "```\n"
            "<mxfile>x</mxfile>\n"
            "```\n"
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"] is not None
        assert "<mxfile" in out["a"]

    def test_no_diagram(self, _extract_fn_js):
        out = self._run(_extract_fn_js, {
            "a": "Hello, here is my answer about graphs.",
        })
        assert out["a"] is None

    def test_empty_content(self, _extract_fn_js):
        out = self._run(_extract_fn_js, {"a": ""})
        assert out["a"] is None

    def test_multiple_diagrams_returns_first(
        self, _extract_fn_js,
    ):
        body = (
            "First: <mxfile id='1'></mxfile>\n"
            "Second: <mxfile id='2'></mxfile>"
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"] is not None
        assert 'id="1"' in out["a"] or "id='1'" in out["a"]
        # Second mxfile should not be in the returned text.
        assert "Second" not in out["a"]

    def test_whitespace_around_tags(self, _extract_fn_js):
        """Our bare regex ``<\\s*mxfile`` permits a leading
        whitespace run before ``mxfile`` — vanilla drawio XML
        never has this, but we tolerate it defensively so a
        stray indent in a markdown render doesn't get
        dropped. The contract here is ``extract returns
        *some* substring or null``; the exact shape isn't
        pinned (the regex is generous on purpose)."""
        out = self._run(_extract_fn_js, {
            "a": "< mxfile >< /mxfile >",
        })
        # Either null (didn't match) or some non-empty
        # substring — we don't pin the exact text since
        # whitespace tolerance is intentionally loose.
        assert out["a"] is None or isinstance(out["a"], str)

    # v0.9.5.1 regression: user reported the create_diagram
    # tool returns ``{"xml": "<mxGraphModel>..."}`` JSON, NOT
    # a bare ``<mxfile>`` block. The original regex missed
    # both shapes. These tests guard the fix.

    def test_create_diagram_json_wrapped(self, _extract_fn_js):
        """The exact shape the user logged at 2026-09-01 22:27:
        the create_diagram MCP tool returns ``{"xml": "..."}``
        JSON, and the assistant reply contains it verbatim."""
        # Substring from the user's log:
        # "{\"xml\":\"<mxGraphModel>\\n  <root>\\n    <mxCell id=\\\"0\\\"/>..."
        body = (
            '{"xml":"<mxGraphModel>\\n  <root>\\n    '
            '<mxCell id=\\"0\\"/>\\n    <mxCell id=\\"1\\" parent=\\"0\\"/>\\n  '
            '</root>\\n</mxGraphModel>"}'
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"] is not None, (
            "regex missed JSON-wrapped XML — this is the "
            "user-reported bug"
        )
        assert "mxGraphModel" in out["a"]
        # JSON wrapper must be stripped.
        assert '{"xml"' not in out["a"]
        assert '"}' not in out["a"]

    def test_bare_mxgraphmodel(self, _extract_fn_js):
        """Even when not JSON-wrapped, bare mxGraphModel
        (no mxfile) must be picked up."""
        body = (
            "Here is the diagram:\n"
            "<mxGraphModel>\n"
            "  <root>\n"
            '    <mxCell id="0"/>\n'
            '    <mxCell id="1" parent="0"/>\n'
            "  </root>\n"
            "</mxGraphModel>\n"
            "Done."
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"] is not None
        assert "mxGraphModel" in out["a"]
        assert "Here is" not in out["a"]
        assert "Done" not in out["a"]

    def test_fenced_mxgraphmodel(self, _extract_fn_js):
        body = (
            "```xml\n"
            "<mxGraphModel>\n"
            "  <root>\n"
            '    <mxCell id="0"/>\n'
            "  </root>\n"
            "</mxGraphModel>\n"
            "```"
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"] is not None
        assert "mxGraphModel" in out["a"]
        assert "```" not in out["a"]

    def test_mxfile_still_preferred_over_mxgraphmodel(
        self, _extract_fn_js,
    ):
        """When the response has BOTH shapes, mxfile wins
        (it's the canonical form, less risky to ship)."""
        body = (
            "<mxGraphModel>x</mxGraphModel>\n"
            "<mxfile>y</mxfile>\n"
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert "mxfile" in out["a"]
        assert "mxGraphModel" not in out["a"]

    def test_json_with_full_mxfile_string(
        self, _extract_fn_js,
    ):
        """JSON-wrapped MXFILE (some drawio servers do this)
        must also be unwrapped."""
        body = '{"xml":"<mxfile><diagram id=\\"d\\"/></mxfile>"}'
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"] is not None
        assert "mxfile" in out["a"]
        assert '{"xml"' not in out["a"]

    # v0.9.5.2 — pure-prose guard. LLMs sometimes describe a
    # diagram in plain Chinese / English text without ever
    # embedding any drawio XML. In that case there is nothing
    # for the viewer to render, and the chat UI should not
    # even try (rather than showing a broken button). These
    # tests pin the negative case so the regex stays focused
    # on actual XML and doesn't drift into false positives.

    def test_pure_prose_chinese_no_xml(self, _extract_fn_js):
        """Exact user-reported scenario at 2026-09-01 22:43:
        the LLM wrote '已完成!我用 draw.io 繪製了一個 hello 方塊:
        形狀:圓角矩形(240 × 120)文字:hello(28px 粗體)配色:
        淡藍色背景(#dae8fc)+ 藍色邊框(#6c8ebf)' and the chat UI
        saw zero XML. The button must not appear — there is
        nothing to open."""
        body = (
            "已完成!✅ 我用 draw.io 繪製了一個 hello 方塊:\n\n"
            "**形狀**:圓角矩形(240 × 120)\n"
            "**文字**:hello(28px 粗體)\n"
            "**配色**:淡藍色背景(#dae8fc)+ 藍色邊框(#6c8ebf)\n\n"
            "圖表已在上方顯示出來,你可以直接在 draw.io 編輯器"
            "中查看或複製 XML 使用。"
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"] is None, (
            "pure-prose LLM reply must not produce a button "
            "(nothing for the viewer to open); got: "
            f"{out['a']!r}"
        )

    def test_json_unicode_escaped_xml(self, _extract_fn_js):
        """Some LLMs JSON-encode the angle brackets when
        echoing a tool result verbatim:
        ``{"xml":"\\u003cmxGraphModel\\u003e..."}``. v0.9.5.2
        adds a pre-normalisation step that converts those
        unicode escapes to literal angle brackets BEFORE the
        regex runs, so the existing patterns can match."""
        body = (
            'Tool returned: ```json\n'
            '{"xml":"\\u003cmxGraphModel\\u003e'
            '\\u003croot\\u003e'
            '\\u003cmxCell id=\\"0\\"/\\u003e'
            '\\u003c/root\\u003e'
            '\\u003c/mxGraphModel\\u003e"}\n'
            '```'
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"] is not None, (
            "JSON-escaped mxGraphModel must be unwrapped"
        )
        assert "<mxGraphModel" in out["a"]
        assert "</mxGraphModel>" in out["a"]
        # JSON envelope must be stripped.
        assert "\\u003c" not in out["a"]
        assert '{"xml"' not in out["a"]

    def test_fenced_json_does_not_match_fenced_xml(
        self, _extract_fn_js,
    ):
        """A ```json {...} ``` fence must NOT be matched by the
        fenced-mxfile / fenced-mxGraphModel patterns (those
        require ``xml`` / ``drawio`` as the language tag now,
        not ``json``). The JSON envelope pattern below is the
        one that should win and unwrap only the inner XML."""
        body = (
            "I drew it:\n"
            "```json\n"
            '{"xml":"<mxGraphModel>\n'
            "  <root>\n"
            '    <mxCell id=\\"0\\"/>\n'
            "  </root>\n"
            "</mxGraphModel>\"}\n"
            "```\n"
        )
        out = self._run(_extract_fn_js, {"a": body})
        assert out["a"] is not None
        assert out["a"].startswith("<mxGraphModel")
        assert out["a"].endswith("</mxGraphModel>")
        # JSON wrapper must be stripped.
        assert '{"xml"' not in out["a"]
        assert "```" not in out["a"]


# ── viewer wrapping logic ─────────────────────────────────────


@pytest.fixture(scope="module")
def _wrap_fn_js():
    """Pull ``_wrapForDrawio`` out of diagrams.js so we can
    drive it via Node VM. Skips if node is missing."""
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node not on PATH; skipping wrap tests")
    import re
    text = Path("gui/assets/diagrams.js").read_text(encoding="utf-8")
    m = re.search(
        r"function _wrapForDrawio\([\s\S]+?\n  \}",
        text,
    )
    if not m:
        pytest.fail("_wrapForDrawio not found in diagrams.js")
    return m.group(0)


class TestJsWrapForDrawio:
    """The viewer's ``_wrapForDrawio`` must accept any of
    the shapes the LLM may produce and turn them into a
    drawio-compatible envelope."""

    def _run(self, fn_body, inputs):
        import json, subprocess
        inputs_json = json.dumps(inputs, ensure_ascii=False)
        script = (
            f"{fn_body}\n"
            f"var inputs = {inputs_json};\n"
            "var out = {};\n"
            "for (var k in inputs) {\n"
            "  out[k] = _wrapForDrawio(inputs[k]);\n"
            "}\n"
            "process.stdout.write(JSON.stringify(out));"
        )
        r = subprocess.run(
            ["node", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, (
            f"node failed: stdout={r.stdout!r} stderr={r.stderr!r}"
        )
        return json.loads(r.stdout)

    def test_mxfile_passthrough(self, _wrap_fn_js):
        out = self._run(_wrap_fn_js, {
            "a": '<mxfile><diagram/></mxfile>',
        })
        assert out["a"] == '<mxfile><diagram/></mxfile>'

    def test_mxgraphmodel_wrapped(self, _wrap_fn_js):
        """The exact user-reported shape: bare mxGraphModel
        with no mxfile wrapper. Must be wrapped to be
        drawio-embed-compatible."""
        body = (
            "<mxGraphModel>\n"
            "  <root>\n"
            '    <mxCell id="0"/>\n'
            '    <mxCell id="1" parent="0"/>\n'
            "  </root>\n"
            "</mxGraphModel>"
        )
        out = self._run(_wrap_fn_js, {"a": body})
        assert "<mxfile" in out["a"]
        assert "<mxGraphModel" in out["a"]
        # v0.9.5.7: the mxfile child element MUST be the canonical
        # <diagram> — drawio's extractGraphModel() finds pages via
        # getElementsByTagName('diagram'). The pre-0.9.5.7
        # <mxDiagram> typo matched nothing and drawio rejected the
        # whole file with the 「非绘图文件」 error dialog.
        assert "<diagram" in out["a"]
        assert "mxDiagram" not in out["a"]
        assert "mxdiagram" not in out["a"]

    def test_wrap_bare_mxgraphmodel_canonical_element(self, _wrap_fn_js):
        """v0.9.5.7 regression — user-reported 2026-09-02: the
        exact create_diagram circle shape (bare, attribute-less
        <mxGraphModel>) opened with drawio's 「非绘图文件」 error
        because _wrapForDrawio emitted <mxDiagram>. The envelope
        must now use the canonical <diagram> element."""
        body = (
            "<mxGraphModel>\n"
            "  <root>\n"
            '    <mxCell id="0"/>\n'
            '    <mxCell id="1" parent="0"/>\n'
            '    <mxCell id="circle1" value="圓" '
            'style="ellipse;whiteSpace=wrap;html=1;'
            'fillColor=#dae8fc;strokeColor=#6c8ebf;'
            'strokeWidth=2;" vertex="1" parent="1">\n'
            '      <mxGeometry x="200" y="120" width="160" '
            'height="160" as="geometry"/>\n'
            "    </mxCell>\n"
            "  </root>\n"
            "</mxGraphModel>"
        )
        out = self._run(_wrap_fn_js, {"a": body})
        w = out["a"]
        assert w.startswith('<mxfile host="divine-sense-agent"')
        assert '<diagram id="root" name="Page-1">' in w
        assert "</diagram></mxfile>" in w
        # The typo's exact signatures are gone (both casings).
        assert "mxDiagram" not in w
        assert "mxdiagram" not in w
        # The user's circle cell survives verbatim.
        assert 'id="circle1"' in w
        assert "ellipse;whiteSpace=wrap" in w

    def test_extract_then_wrap_user_message(self, _extract_fn_js, _wrap_fn_js):
        """v0.9.5.7 end-to-end regression for the user-reported
        chat message: prose + bare <mxGraphModel> + trailing prose.
        The whole chain (chat extraction → viewer wrap) must yield
        a canonical <mxfile><diagram> envelope with zero prose and
        zero <mxDiagram>/<mxdiagram> typo elements."""
        import json as _json
        import subprocess as _sp
        msg = (
            "圓已畫好！這是一個 160×160 的正圓（ellipse 形狀），"
            "藍色填充、藍色邊框，中間標註「圓」。\n\n"
            "<mxGraphModel>\n"
            "  <root>\n"
            '    <mxCell id="0"/>\n'
            '    <mxCell id="1" parent="0"/>\n'
            '    <mxCell id="circle1" value="圓" '
            'style="ellipse;whiteSpace=wrap;html=1;'
            'fillColor=#dae8fc;strokeColor=#6c8ebf;'
            'strokeWidth=2;" vertex="1" parent="1">\n'
            '      <mxGeometry x="200" y="120" width="160" '
            'height="160" as="geometry"/>\n'
            "    </mxCell>\n"
            "  </root>\n"
            "</mxGraphModel>\n\n"
            "點擊上方的「📊 View in Diagrams」按鈕即可在 "
            "draw.io 檢視器中開啟。"
        )
        script = (
            f"{_extract_fn_js}\n{_wrap_fn_js}\n"
            f"var msg = {_json.dumps(msg, ensure_ascii=False)};\n"
            "var xml = _extractDiagramXml(msg);\n"
            "var wrapped = xml ? _wrapForDrawio(xml) : null;\n"
            "process.stdout.write("
            "JSON.stringify({xml: xml, wrapped: wrapped}));"
        )
        r = _sp.run(
            ["node", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, (
            f"node failed: stdout={r.stdout!r} stderr={r.stderr!r}"
        )
        res = _json.loads(r.stdout)
        xml, w = res["xml"], res["wrapped"]
        # Extraction picked ONLY the model block — no prose.
        assert xml.startswith("<mxGraphModel")
        assert xml.endswith("</mxGraphModel>")
        assert "圓已畫好" not in xml
        # Wrap produced the canonical envelope.
        assert w.startswith('<mxfile host="divine-sense-agent"')
        assert '<diagram id="root" name="Page-1">' in w
        assert "</diagram></mxfile>" in w
        assert "mxDiagram" not in w
        assert "mxdiagram" not in w
        # Neither the leading nor the trailing prose leaks in.
        assert "圓已畫好" not in w
        assert "點擊上方" not in w
        assert 'id="circle1"' in w
        assert "ellipse;whiteSpace=wrap" in w

    def test_no_wrong_diagram_element_anywhere_in_source(self):
        """v0.9.5.7 guard: <mxDiagram> / <mxdiagram> must not
        exist anywhere in diagrams.js — including the
        emptyDiagramXml() template (merge-action fallback)."""
        text = Path("gui/assets/diagrams.js").read_text(
            encoding="utf-8",
        )
        assert "mxDiagram" not in text
        assert "mxdiagram" not in text
        # The canonical element must be present.
        assert "<diagram" in text

    def test_bare_mxcell_wrapped(self, _wrap_fn_js):
        body = (
            '<mxCell id="2" value="hello" '
            'style="rounded=1;fillColor=#dae8fc" vertex="1">'
            '<mxGeometry x="40" y="40" width="180" height="90" '
            'as="geometry"/>'
            "</mxCell>"
        )
        out = self._run(_wrap_fn_js, {"a": body})
        # Wrapped in mxfile + diagram + graphmodel + root.
        # v0.9.5.7: child element is the canonical <diagram>.
        assert "<mxfile" in out["a"]
        assert "<mxGraphModel" in out["a"]
        assert "<diagram" in out["a"]
        assert "mxDiagram" not in out["a"]
        assert "<root>" in out["a"]
        # The user-supplied cell survives.
        assert 'id="2"' in out["a"]
        assert "hello" in out["a"]
        # Default id=0 / id=1 cells are added.
        assert '<mxCell id="0"/>' in out["a"]
        assert '<mxCell id="1" parent="0"/>' in out["a"]

    def test_strips_duplicate_root_cells(self, _wrap_fn_js):
        """If the LLM supplied its own id=0 / id=1 cells,
        the wrapper must strip them (drawio always wants
        exactly one canonical root + default parent layer)."""
        body = (
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="2" value="real"/>'
        )
        out = self._run(_wrap_fn_js, {"a": body})
        # Should have exactly one id="0" and one id="1".
        assert out["a"].count('id="0"') == 1
        assert out["a"].count('id="1"') == 1
        # The user cell survives.
        assert 'id="2"' in out["a"]

    def test_empty_passthrough(self, _wrap_fn_js):
        """Empty input returns empty (don't synthesise an
        envelope for no content)."""
        out = self._run(_wrap_fn_js, {"a": ""})
        assert out["a"] == ""

    def test_unrecognised_passthrough(self, _wrap_fn_js):
        """An XML string drawio doesn't understand is
        returned verbatim — the user gets drawio's own
        error in the editor rather than a silent rewrap."""
        body = "<unknown>blah</unknown>"
        out = self._run(_wrap_fn_js, {"a": body})
        assert out["a"] == body

    def test_mxfile_with_whitespace_passthrough(
        self, _wrap_fn_js,
    ):
        out = self._run(_wrap_fn_js, {
            "a": '  <mxfile><diagram/></mxfile>\n',
        })
        # Trimmed and passed through (still a complete mxfile).
        assert out["a"].startswith("<mxfile")
        assert out["a"].endswith("</mxfile>")


# ── drawio JSON-protocol iframe-mount (v0.9.5.3) ──────────────


@pytest.fixture(scope="module")
def _js_protocol_driver():
    """Spin up a JSDOM + hand-crafted drawio-iframe stub and
    execute ``gui/assets/diagrams.js`` inside it.

    Returns a callable ``drive(scenario)`` that runs the JS in a
    controlled environment, lets the scenario simulate the
    drawio iframe's first ``init`` event, and returns what
    our viewer actually posted into the iframe.
    """
    import json
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not on PATH; skipping iframe protocol tests")
    # Probe jsdom by writing a tiny JS script to a tmp file and
    # running it via ``node``. Avoids quoting headaches on
    # cmd.exe (Windows) where ``node -e "..."`` argument
    # parsing can mangle the inline script. We force ``cwd``
    # and the tmp file location to the project root so node's
    # module resolution finds our local ``node_modules/jsdom``.
    import tempfile
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8",
        dir=str(project_root),
    ) as f:
        f.write("try { require('jsdom'); } catch (_) { process.exit(1); }")
        probe_path = f.name
    try:
        has_jsdom = subprocess.run(
            ["node", probe_path],
            capture_output=True, timeout=30,
            cwd=str(project_root),
        ).returncode == 0
    finally:
        try:
            Path(probe_path).unlink()
        except OSError:
            pass
    if not has_jsdom:
        pytest.skip(
            "jsdom not installed (npm i jsdom); "
            "skipping iframe protocol tests",
        )

    def drive(scenario_name: str, commands: list[dict]) -> dict:
        """Run ``commands`` inside the simulated viewer env.
        Supported ops:
        * ``{"op": "init_event"}`` — simulate the drawio iframe
          sending its ``{event: 'init'}`` message from our
          mounted iframe.
        * ``{"op": "load_xml_event", "xml": "..."}`` — simulate
          the ``drawio-load-xml`` CustomEvent from Python.
        * ``{"op": "flush"}`` — let pending microtasks settle.
        Returns ``{"posts": [...], "src": "..."}``.
        """
        cmds_json = json.dumps(commands, ensure_ascii=False)
        return _jsdom_run_drive(scenario_name, cmds_json)

    return drive


def _jsdom_run_drive(
    scenario_name: str,
    cmds_json: str,
    bridge_mode: str = "stub",
    bridge_delay_ms: int = 250,
    bridge_timeout_override_ms: int = 600,
) -> dict:
    """Lower-level: build the JSDOM+Node driver script and run
    it via ``node``. The tmp-file mechanics + the Windows JSDOM
    teardown tolerance live in ``_run_node_jsdom_script``.

    ``bridge_mode`` (v0.9.5.6) selects how / when the
    ``window.pywebview`` stub is injected — see
    ``_driver_head_js``.
    """
    script = _build_jsdom_driver_script(
        scenario_name,
        cmds_json,
        bridge_mode=bridge_mode,
        bridge_delay_ms=bridge_delay_ms,
        bridge_timeout_override_ms=bridge_timeout_override_ms,
    )
    return _run_node_jsdom_script(script)


# ── JSDOM driver head: bridge-mode parameterised (v0.9.5.6) ─────
# pywebview injects ``window.pywebview`` only AFTER the page
# finishes loading, i.e. AFTER ``DOMContentLoaded`` fires
# (user-reported 2026-09-02: "Cannot read properties of undefined
# (reading 'api')" on the status pill). The driver's pywebview
# stub therefore becomes a MODE:
#
#   * "stub" (default) — bridge pre-defined BEFORE ``diagrams.js``
#     runs (legacy behaviour; every pre-0.9.5.6 test relies on
#     this, so it stays the default).
#   * "none" — the bridge NEVER appears;
#     ``window._BRIDGE_WAIT_MS_OVERRIDE`` shortens
#     ``_waitForBridge``'s 15 s default so the non-fatal
#     bridge-wait note is reachable in test time.
#   * "late" — the bridge is injected ``bridge_delay_ms`` after
#     boot, mirroring the real WebView2 lifecycle.
_JS_BRIDGE_STUB_JS = (
    "window.pywebview = { api: {"
    "  ping: () => Promise.resolve("
    "    { ok: true, write_root: '/tmp/sandbox' }),"
    "  get_write_root: () => Promise.resolve('/tmp/sandbox'),"
    "  save_to_sandbox: () => Promise.resolve({ok: true}),"
    "  read_from_sandbox: () => Promise.resolve("
    "    {ok: true, path: '', bytes: 0, text: ''}),"
    # v0.9.5.4: extend the bridge with the loading-overlay
    # callbacks the new HTML overlay expects. ``get_drawio_url``
    # returns the same URL the iframe is mounted with so
    # ``diagrams.js`` doesn't have to hardcode it; tests that
    # don't care just ignore the new methods.
    "  get_drawio_url: () => 'https://embed.diagrams.net/?embed=1&proto=json&spin=1&ui=min',"
    "  open_drawio_in_browser: () => Promise.resolve({ok: true}),"
    "} };\n"
)

_JS_IFRAME_PATCH_JS = (
    "document.createElement = function(tag) {\n"
    "  const el = realCreate(tag);\n"
    "  if (String(tag).toLowerCase() === 'iframe') {\n"
    "    const fakeCw = { postMessage: function(msg, origin) {\n"
    "      window._posts.push(JSON.parse(msg));\n"
    "    } };\n"
    "    Object.defineProperty(el, 'contentWindow', {"
    "      value: fakeCw, writable: false });\n"
    "    Object.defineProperty(el, 'src', {"
    "      get: function() { return el.getAttribute('src') || ''; },"
    "      set: function(v) { el.setAttribute('src', v); } });\n"
    "  }\n"
    "  return el;\n"
    "};\n"
)

_JS_EVAL_BOOT_JS = (
    "const src = fs.readFileSync("
    "'gui/assets/diagrams.js', 'utf8');\n"
    "window.eval(src);\n"
    "document.dispatchEvent(new window.Event('DOMContentLoaded'));\n"
)


def _driver_head_js(
    bridge_mode: str = "stub",
    bridge_delay_ms: int = 250,
    bridge_timeout_override_ms: int = 600,
) -> str:
    """Compose the common JSDOM driver head for a bridge mode."""
    parts = [
        "const { JSDOM } = require('jsdom');\n",
        "const fs = require('fs');\n",
        "const html = fs.readFileSync("
        "'gui/assets/diagrams.html', 'utf8');\n",
        # v0.9.5.5: NO resource loading — deterministic tests.
        # An earlier revision used ``resources: 'usable'`` which made
        # JSDOM fetch the REAL ``https://embed.diagrams.net`` URL over
        # the network; whenever that HTTP round-trip completed it fired
        # the iframe ``load`` handler and overwrote the status pill
        # mid-scenario — the root cause of the cross-file flakiness in
        # the defensive-fallback tests (``test_unhandled_rejection_
        # populates_status_pill`` flipping to ``⏳ iframe loaded…``
        # whenever the fetch landed after the synthetic rejection).
        # With no ``resources`` option the iframe never navigates and
        # never fires ``load``, so pill state is driven ONLY by the
        # synthetic events each test dispatches. (jsdom 29 removed the
        # ``ResourceLoader`` subclass API, so stubbing fetches that way
        # is no longer possible anyway.) The page's ``<script
        # src=diagrams.js>`` is intentionally NOT loaded — the driver
        # executes ``diagrams.js`` explicitly via ``window.eval``.
        "const dom = new JSDOM(html, {"
        "url: 'http://localhost/',"
        "runScripts: 'outside-only',"
        "});\n",
        "const window = dom.window;\n",
        "const document = window.document;\n",
        "window._posts = [];\n",
        "const realCreate = document.createElement.bind(document);\n",
    ]
    if bridge_mode == "stub":
        parts.append(_JS_BRIDGE_STUB_JS)
    elif bridge_mode == "none":
        parts.append(
            "window._BRIDGE_WAIT_MS_OVERRIDE = "
            f"{int(bridge_timeout_override_ms)};\n"
        )
    elif bridge_mode == "late":
        parts.append(
            "setTimeout(() => {\n"
            + _JS_BRIDGE_STUB_JS
            + f"}}, {int(bridge_delay_ms)});\n"
        )
    else:
        raise ValueError(f"unknown bridge_mode: {bridge_mode!r}")
    parts.append(_JS_IFRAME_PATCH_JS)
    parts.append(_JS_EVAL_BOOT_JS)
    return "".join(parts)


_DRIVER_SUFFIX_TEMPLATE = (
    "const cmds = {cmds_json};\n"
    "async function run() {{\n"
    "  for (const c of cmds) {{\n"
    "    if (c.op === 'init_event') {{\n"
    "      const iframe = document.getElementById("
    "'drawio-mount').querySelector('iframe');\n"
    "      window.dispatchEvent(new window.MessageEvent("
    "'message', {{"
    "source: iframe.contentWindow,"
    "data: JSON.stringify({{event: 'init'}}),"
    "}}));\n"
    "    }} else if (c.op === 'load_xml_event') {{\n"
    "      window.dispatchEvent(new window.CustomEvent("
    "'drawio-load-xml', {{ detail: c.xml }}));\n"
    "    }} else if (c.op === 'flush') {{\n"
    "      await new Promise(r => setTimeout(r, 50));"
    "    }}"
    "  }}\n"
    "  await new Promise(r => setTimeout(r, 50));"
    "}}\n"
    "run().then(() => {{\n"
    "  const iframe = document.getElementById("
    "'drawio-mount').querySelector('iframe');\n"
    "  console.log(JSON.stringify({{"
    "posts: window._posts,"
    "src: iframe ? (iframe.getAttribute('src') || '') : '',"
    "}}));\n"
    "  // Explicit exit — JSDOM keeps a few internal timers alive\n"
    "  // under Node ≥ 18, so without this the script just\n"
    "  // hangs forever waiting for the event loop to drain.\n"
    "  setTimeout(() => process.exit(0), 50);\n"
    "}}).catch(e => {{"
    "process.stderr.write('SCENARIO '"
    " + {scenario_name_repr} + ' FAIL: ' + e);"
    "process.exit(1);"
    "}});\n"
)


def _build_jsdom_driver_script(
    scenario_name: str,
    cmds_json: str,
    bridge_mode: str = "stub",
    bridge_delay_ms: int = 250,
    bridge_timeout_override_ms: int = 600,
) -> str:
    """Compose the full Node script. ``bridge_mode`` selects how
    (and when) the ``window.pywebview`` stub is injected — see
    ``_driver_head_js`` (v0.9.5.6)."""
    return (
        _driver_head_js(
            bridge_mode=bridge_mode,
            bridge_delay_ms=bridge_delay_ms,
            bridge_timeout_override_ms=bridge_timeout_override_ms,
        )
        + _DRIVER_SUFFIX_TEMPLATE.format(
            cmds_json=cmds_json,
            scenario_name_repr=repr(scenario_name),
        )
    )


class TestJsDrawioEmbedProtocol:
    """v0.9.5.3: the viewer mounts its own raw iframe and talks
    the JSON protocol directly (init → load). These tests pin
    down each leg of that contract."""

    def test_iframe_mounted_with_embed_url(self, _js_protocol_driver):
        """``startDrawio()`` must build an iframe with the
        documented embed URL params (embed=1, proto=json)."""
        out = _js_protocol_driver("mount", [])
        assert "embed=1" in out["src"]
        assert "proto=json" in out["src"]
        assert "spin=1" in out["src"]
        # No postMessages until init arrives.
        assert out["posts"] == []

    def test_load_queued_until_init(self, _js_protocol_driver):
        """If Python pushes XML BEFORE the iframe sends its
        ``init`` event, the viewer must queue and flush it
        once init arrives — the JSON protocol silently drops
        ``load`` posted before init."""
        out = _js_protocol_driver(
            "queue-then-flush",
            [
                {"op": "load_xml_event",
                 "xml": "<mxGraphModel><root/></mxGraphModel>"},
                {"op": "init_event"},
                {"op": "flush"},
            ],
        )
        assert len(out["posts"]) == 1, out["posts"]
        post = out["posts"][0]
        assert post.get("action") == "load", post
        xml = post.get("xml", "")
        assert "<mxGraphModel" in xml
        assert "<mxfile" in xml  # _wrapForDrawio wrapped it

    def test_load_immediate_after_init(self, _js_protocol_driver):
        """After init arrives, a subsequent ``load_xml_event``
        postMessages ``load`` immediately."""
        out = _js_protocol_driver(
            "init-then-load",
            [
                {"op": "init_event"},
                {"op": "load_xml_event",
                 "xml": "<mxCell id='5'/>"},
                {"op": "flush"},
            ],
        )
        loads = [p for p in out["posts"] if p.get("action") == "load"]
        assert len(loads) == 1, out["posts"]
        xml = loads[0]["xml"]
        # Bare <mxCell> gets wrapped into a full envelope.
        # v0.9.5.7: the envelope child element must be the
        # canonical <diagram> (drawio getElementsByTagName lookup).
        assert "<mxfile" in xml
        assert "<diagram" in xml
        assert "<mxGraphModel" in xml
        assert "mxDiagram" not in xml
        assert "mxdiagram" not in xml
        # The user cell survives — accept either single or double
        # quoted id because we hand whatever the LLM sent back.
        assert 'id="5"' in xml or "id='5'" in xml

    def test_load_dropped_if_init_never_arrives(
        self, _js_protocol_driver,
    ):
        """If init never fires, the queue keeps the XML for
        later — no postMessage leak into a non-existent iframe.
        (Belt-and-suspenders check: the protocol is *required*
        to wait for init before posting load.)"""
        out = _js_protocol_driver(
            "load-without-init",
            [
                {"op": "load_xml_event",
                 "xml": "<mxGraphModel/>"},
                # NO init_event. No flush microtask-pump either.
            ],
        )
        # No posts — we cannot post load without an init.
        assert out["posts"] == []


# ── drawio loading overlay (v0.9.5.4) ──────────────────────────


def _require_jsdom_or_skip() -> None:
    """Skip when node / jsdom are unavailable."""
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not on PATH; skipping overlay tests")
    project_root = Path(__file__).resolve().parents[1]
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8",
        dir=str(project_root),
    ) as f:
        f.write(
            "try { require('jsdom'); } catch (_) "
            "{ process.exit(1); }"
        )
        probe = f.name
    try:
        ok = subprocess.run(
            ["node", probe], capture_output=True, timeout=30,
            cwd=str(project_root),
        ).returncode == 0
    finally:
        try:
            Path(probe).unlink()
        except OSError:
            pass
    if not ok:
        pytest.skip("jsdom not installed (npm i jsdom)")


def _run_node_jsdom_script(script: str) -> dict:
    """Write ``script`` to a tmp .js under the project root (so
    node's module resolver finds ``node_modules/jsdom``), run it,
    parse stdout as JSON. Tolerates the Windows JSDOM teardown
    ``STATUS_STACK_OVERFLOW`` (``0xC00000FD`` = 3221226505) —
    stdout is already flushed by the time the exit fires.
    """
    import json
    import subprocess
    import tempfile
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8",
        dir=str(project_root),
    ) as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run(
            ["node", path],
            capture_output=True, text=True, timeout=45,
            cwd=str(project_root),
        )
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass
    if r.returncode != 0 and r.returncode != 3221226505:
        raise AssertionError(
            f"jsdom node run failed: stdout={r.stdout!r} "
            f"stderr={r.stderr!r}"
        )
    return json.loads(r.stdout)


def _build_overlay_driver_script(
    scenario_name: str,
    cmds_json: str,
    bridge_mode: str = "stub",
    bridge_delay_ms: int = 250,
    bridge_timeout_override_ms: int = 600,
) -> str:
    """Overlay-flavour driver (snapshot includes the loading
    overlay + status-pill state). ``bridge_mode`` — see
    ``_driver_head_js`` (v0.9.5.6)."""
    return (
        _driver_head_js(
            bridge_mode=bridge_mode,
            bridge_delay_ms=bridge_delay_ms,
            bridge_timeout_override_ms=bridge_timeout_override_ms,
        )
        + _OVERLAY_DRIVER_SUFFIX_TEMPLATE.format(
            cmds_json=cmds_json,
            scenario_name_repr=repr(scenario_name),
        )
    )


def _run_overlay_scenario(
    scenario_name: str,
    commands: list[dict],
    bridge_mode: str = "stub",
    bridge_delay_ms: int = 250,
    bridge_timeout_override_ms: int = 600,
) -> dict:
    """Run an overlay-driver scenario with an explicit bridge
    mode. Used directly by ``TestJsBridgeBootSafety`` — the
    ``_js_overlay_driver`` fixture can only pass the default.
    Callers that need the skip-guard should call
    ``_require_jsdom_or_skip()`` first.
    """
    import json
    script = _build_overlay_driver_script(
        scenario_name,
        json.dumps(commands, ensure_ascii=False),
        bridge_mode=bridge_mode,
        bridge_delay_ms=bridge_delay_ms,
        bridge_timeout_override_ms=bridge_timeout_override_ms,
    )
    return _run_node_jsdom_script(script)


@pytest.fixture(scope="module")
def _js_overlay_driver():
    """Same shape as ``_js_protocol_driver`` but the harness
    returns the loading-overlay's state at the end of the
    scenario. Used by ``TestJsLoadingOverlay`` below.
    """
    _require_jsdom_or_skip()

    def drive(scenario_name: str, commands: list[dict]) -> dict:
        return _run_overlay_scenario(scenario_name, commands)

    return drive


# Differs from _DRIVER_SUFFIX_TEMPLATE in that the
# final snapshot also captures the loading-overlay state
# (visible / hidden / timer text / stuck-actions visible).
_OVERLAY_DRIVER_SUFFIX_TEMPLATE = (
    "const cmds = {cmds_json};\n"
    "async function run() {{\n"
    "  // Give the DOMContentLoaded handler a tick to mount the\n"
    "  // iframe before we try to dispatch init_event into it.\n"
    "  // Without this the message arrives BEFORE the iframe\n"
    "  // listener is registered, so the overlay stays visible.\n"
    "  await new Promise(r => setTimeout(r, 50));\n"
    "  for (const c of cmds) {{\n"
    "    if (c.op === 'init_event') {{\n"
    "      const iframe = document.getElementById("
    "'drawio-mount').querySelector('iframe');\n"
    "      window.dispatchEvent(new window.MessageEvent("
    "'message', {{"
    "source: iframe.contentWindow,"
    "data: JSON.stringify({{event: 'init'}}),"
    "}}));\n"
    "    }} else if (c.op === 'load_xml_event') {{\n"
    "      window.dispatchEvent(new window.CustomEvent("
    "'drawio-load-xml', {{ detail: c.xml }}));\n"
    "    }} else if (c.op === 'flush') {{\n"
    "      await new Promise(r => setTimeout(r, c.ms || 50));\n"
    "    }} else if (c.op === 'click_open_in_browser') {{\n"
    "      const b = document.getElementById("
    "'btn-open-in-browser');\n"
    "      if (b) b.click();\n"
    "    }} else if (c.op === 'click_reload_iframe') {{\n"
    "      const b = document.getElementById("
    "'btn-reload-iframe');\n"
    "      if (b) b.click();\n"
    # v0.9.5.5: synthetic error / rejection dispatchers so
    # we can exercise the global ``error`` /
    # ``unhandledrejection`` watchdog handlers without
    # actually throwing inside ``diagrams.js``. ``kind``
    # selects which event to fire; ``msg`` is the payload.
    "    }} else if (c.op === 'dispatch_error') {{\n"
    "      window.dispatchEvent(new window.ErrorEvent("
    "'error', {{ message: c.msg, error: new Error(c.msg) }}));\n"
    "    }} else if (c.op === 'dispatch_rejection') {{\n"
    # ``Promise.reject`` without a catch bubbles into
    # ``unhandledrejection`` on the window — that's what we
    # want to test.
        "      window._lastRejectionReason = new Error(c.msg);\n"
    "      window.dispatchEvent(new window.Event('unhandledrejection'));\n"
    "      await new Promise(r => setTimeout(r, 5));\n"
    "    }}"
    "  }}\n"
    "  await new Promise(r => setTimeout(r, 50));\n"
    "  const overlay = document.getElementById('loading-overlay');\n"
    "  const stuck = document.getElementById("
    "'loading-stuck-actions');\n"
    "  const timer = document.getElementById('loading-timer');\n"
    "  const pill = document.getElementById('status-pill');\n"
    "  const iframe = document.getElementById("
    "'drawio-mount').querySelector('iframe');\n"
    # v0.9.5.6: surface the sandbox-path label so bridge-mode
    # tests can assert the late ``get_write_root`` fill.
    "  const sp = document.getElementById('sandbox-path');\n"
    "  return {{"
    "posts: window._posts,"
    "src: iframe ? (iframe.getAttribute('src') || '') : '',"
    "sandbox_path_text: sp ? sp.textContent : null,"
    "overlay_hidden: overlay ? overlay.hidden : null,"
    "stuck_hidden: stuck ? stuck.hidden : null,"
    "timer_text: timer ? timer.textContent : null,"
    # v0.9.5.5: surface the status-pill text + class so tests
    # can assert on the watchdog / error-watchdog text.
    "status_pill_text: pill ? pill.textContent : null,"
    "status_pill_kind: pill ? ("
    "pill.classList.contains('err') ? 'err' : "
    "pill.classList.contains('ok') ? 'ok' : '') : null,"
    "}};\n"
    "}}\n"
    "run().then((snapshot) => {{\n"
    "  console.log(JSON.stringify(snapshot));\n"
    "  // Explicit exit — JSDOM keeps a few internal timers alive\n"
    "  // under Node ≥ 18, so without this the script just\n"
    "  // hangs forever waiting for the event loop to drain.\n"
    "  setTimeout(() => process.exit(0), 50);\n"
    "}}).catch(e => {{"
    "process.stderr.write('SCENARIO '"
    " + {scenario_name_repr} + ' FAIL: ' + e);"
    "process.exit(1);"
    "}});\n"
)





class TestJsLoadingOverlay:
    """v0.9.5.4: the viewer shows a visible loading overlay on
    top of the iframe so the user has feedback while drawio's
    10 MB JS bundle boots. After 15 s of waiting the overlay
    reveals two escape-hatch buttons. These tests pin down
    each leg of that UX contract.
    """

    def test_overlay_visible_on_mount(self, _js_overlay_driver):
        """Right after ``startDrawio()`` runs (DOMContentLoaded),
        the loading overlay must be visible (``hidden=false``)
        and the stuck-actions hidden.
        """
        out = _js_overlay_driver("mount-shows-overlay", [])
        assert out["overlay_hidden"] is False, (
            "loading overlay must be visible on mount"
        )
        assert out["stuck_hidden"] is True, (
            "stuck-actions must be hidden until 15 s elapsed"
        )
        assert "已等候" in (out["timer_text"] or ""), (
            "timer must read '已等候 N 秒' on mount"
        )
        assert "embed.diagrams.net" in out["src"], (
            "iframe URL must come from Python bridge"
        )

    def test_overlay_hidden_after_init(self, _js_overlay_driver):
        """When drawio's ``init`` event arrives the overlay
        disappears so it doesn't intercept clicks.
        """
        out = _js_overlay_driver(
            "init-hides-overlay",
            [
                {"op": "init_event"},
                {"op": "flush"},
            ],
        )
        assert out["overlay_hidden"] is True, (
            "overlay must hide once init arrives"
        )

    def test_open_in_browser_click_doesnt_hide_overlay(
        self, _js_overlay_driver,
    ):
        """Clicking **在瀏覽器中開啟 drawio** invokes the
        Python ``open_drawio_in_browser`` bridge. The overlay
        stays visible — clicking it doesn't hide the iframe.
        """
        out = _js_overlay_driver(
            "open-in-browser-click",
            [
                {"op": "click_open_in_browser"},
                {"op": "flush"},
            ],
        )
        assert out["overlay_hidden"] is False

    def test_reload_button_tears_down_iframe(
        self, _js_overlay_driver,
    ):
        """Clicking **重新載入 iframe** must rebuild the iframe
        (overlay re-appears because the new iframe is still
        booting).
        """
        out = _js_overlay_driver(
            "reload-iframe-click",
            [
                {"op": "init_event"},
                {"op": "flush"},
                {"op": "click_reload_iframe"},
                {"op": "flush"},
            ],
        )
        assert out["overlay_hidden"] is False
        assert "embed.diagrams.net" in out["src"]

class TestJsDefensiveFallback:
    """v0.9.5.5: the viewer should never silently leave the user
    staring at a "connecting..." pill + a blank white pane.

    Defensive improvements covered here:

    * HTML loading-overlay is DEFAULT-VISIBLE so even if
      diagrams.js never executes, the user sees the spinner.
    * HTML status pill starts with the neutral text
      "Drawio Viewer" (not "connecting...").
    * JS-side status pill flips to "JS 啟動中..." the moment
      DOMContentLoaded fires, then to the watchdog text after
      2 s if init has not arrived.
    * Global error / unhandledrejection listener catches
      uncaught throws and surfaces them on the pill.

    Whatever happens (JS file failed to load, syntax error
    mid-script, runtime throw, drawio CDN never responds) the
    user has SOMETHING on the status pill that explains the
    state.
    """

    def test_loading_overlay_html_default_visible(self):
        """diagrams.html's loading-overlay MUST NOT have the
        hidden attribute set.
        """
        from pathlib import Path
        html = Path("gui/assets/diagrams.html").read_text(encoding="utf-8")
        overlay_line = [
            line for line in html.splitlines()
            if "id=" in line and "loading-overlay" in line
        ]
        assert overlay_line, (
            "diagrams.html missing loading-overlay div"
        )
        assert "hidden" not in overlay_line[0], (
            f"loading-overlay must be default-visible; got "
            f"{overlay_line[0]!r}"
        )

    def test_status_pill_html_default_is_neutral(self):
        """The static HTML status-pill text must NOT contain
        "connecting..." — that text was misleading users.
        """
        from pathlib import Path
        html = Path("gui/assets/diagrams.html").read_text(encoding="utf-8")
        assert "connecting..." not in html, (
            "diagrams.html still contains the misleading "
            "connecting... static text"
        )
        assert "Drawio Viewer" in html

    def test_status_pill_initial_setstatus_runs(self, _js_overlay_driver):
        """When the JS executes, the FIRST thing DOMContentLoaded
        does is flip the pill to "JS 啟動中...". This pins that
        contract so a future refactor cannot quietly remove it.
        """
        out = _js_overlay_driver("init-setstatus", [])
        assert out["status_pill_text"] is not None
        assert "Drawio Viewer" not in out["status_pill_text"], (
            "setStatus never fired — JS boot is broken. "
            f"Got: {out['status_pill_text']!r}"
        )

    def test_watchdog_pill_text_after_no_init(
        self, _js_overlay_driver,
    ):
        """The 2-second watchdog must surface the
        "drawio 回應緩慢" text on the status pill if drawio's init
        event has not arrived within _WATCHDOG_MS.
        """
        out = _js_overlay_driver(
            "watchdog-no-init",
            [
                {"op": "flush", "ms": 2100},
            ],
        )
        text = out["status_pill_text"] or ""
        assert "回應緩慢" in text or "JS 啟動中" in text, (
            "watchdog did not flip the status pill. "
            f"Got: {text!r}"
        )

    def test_window_error_event_populates_status_pill(
        self, _js_overlay_driver,
    ):
        """A synthetic error event dispatched on the window must
        populate the status pill with "JS error: <msg>".
        """
        out = _js_overlay_driver(
            "synthetic-error",
            [
                {"op": "dispatch_error", "msg": "synthetic boom"},
                {"op": "flush"},
            ],
        )
        assert out["status_pill_text"] is not None
        assert "JS error" in out["status_pill_text"], (
            "global error handler did not update the pill. "
            f"Got: {out['status_pill_text']!r}"
        )
        assert "synthetic boom" in out["status_pill_text"]
        assert out["status_pill_kind"] == "err"

    def test_unhandled_rejection_populates_status_pill(
        self, _js_overlay_driver,
    ):
        """An unhandled promise rejection must also surface on
        the pill.
        """
        out = _js_overlay_driver(
            "synthetic-rejection",
            [
                # First let the iframe-load handler finish so it
                # doesn't overwrite our pill text afterwards.
                {"op": "flush", "ms": 100},
                {"op": "dispatch_rejection", "msg": "async kaboom"},
                {"op": "flush"},
            ],
        )
        text = out["status_pill_text"] or ""
        assert (
            "Promise rejected" in text or "JS error" in text
        ), (
            "unhandled rejection handler did not update the pill. "
            f"Got: {text!r}"
        )

    def test_anchor_fallback_visible_without_js(self):
        """The link-open-in-browser anchor must be OUTSIDE the
        loading-stuck-actions hidden div so it stays visible
        even when diagrams.js never executes.
        """
        from pathlib import Path
        import re
        html = Path("gui/assets/diagrams.html").read_text(encoding="utf-8")
        anchor_pat = '<a[^>]*id="link-open-in-browser"[^>]*>'
        m = re.search(anchor_pat, html)
        assert m is not None, (
            "diagrams.html missing link-open-in-browser anchor"
        )
        assert "embed.diagrams.net" in m.group(0)
        assert 'target="_blank"' in m.group(0)
        # Verify the nearest enclosing div is NOT hidden.
        prefix = html[max(0, m.start() - 600): m.start()]
        divs = re.findall(r"<div\s[^>]*>", prefix)
        assert divs, "no div wrapping the anchor"
        assert "hidden" not in divs[-1], (
            f"anchor is inside a hidden parent div: {divs[-1]!r}"
        )

    def test_noscript_fallback_present(self):
        """A noscript block must be present in diagrams.html so
        a user with JS fully disabled gets an actionable message
        instead of a silently broken UI.
        """
        from pathlib import Path
        html = Path("gui/assets/diagrams.html").read_text(encoding="utf-8")
        assert "<noscript>" in html, (
            "diagrams.html missing noscript fallback"
        )


# ── v0.9.5.6: pywebview bridge injection timing ─────────────────


class TestJsBridgeBootSafety:
    """v0.9.5.6 regression: the v0.9.5.5 boot block read
    ``window.pywebview.api`` directly inside the DOMContentLoaded
    handler. pywebview injects ``window.pywebview`` only AFTER the
    page finishes loading, so on a real WebView2 the read threw
    "Cannot read properties of undefined (reading 'api')"
    (user-reported 2026-09-02) — and, because the throw aborted
    the handler, ``startDrawio()`` never ran and the iframe never
    mounted.

    The pre-0.9.5.6 JSDOM tests never caught this because the
    driver defined the bridge stub BEFORE evaluating diagrams.js.
    The new ``bridge_mode`` variants re-create the real lifecycle:

    * ``none`` — bridge never injected → boot must NOT crash, the
      iframe must still mount with the fallback URL, and the pill
      must show the non-fatal bridge-wait note.
    * ``late`` — bridge injected ~150 ms after boot (real
      WebView2 timing) → ``get_write_root`` must fill the sandbox
      path and ``probeBridge`` must flip the pill to "bridge OK".
    """

    # ── source-level guards ─────────────────────────────────
    def test_boot_block_has_no_direct_bridge_api_read(self):
        """The exact unguarded shapes that crashed in production
        must be gone from diagrams.js — every bridge access goes
        through the null-safe ``bridgeApi()`` helper.
        """
        from pathlib import Path
        js = Path("gui/assets/diagrams.js").read_text(encoding="utf-8")
        assert "window.pywebview.api.get_write_root" not in js, (
            "boot still reads window.pywebview.api.get_write_root "
            "directly (the v0.9.5.5 crash shape)"
        )
        assert "window.pywebview.api.ping" not in js, (
            "probeBridge still reads window.pywebview.api.ping "
            "directly"
        )

    def test_bridge_wait_helpers_exist(self):
        """``bridgeApi()`` + ``_waitForBridge()`` + the official
        ``pywebviewready`` event must be wired, and the boot block
        must defer its bridge calls behind the waiter.
        """
        from pathlib import Path
        js = Path("gui/assets/diagrams.js").read_text(encoding="utf-8")
        assert "function bridgeApi()" in js
        assert "function _waitForBridge(" in js
        assert 'addEventListener("pywebviewready"' in js
        assert "_waitForBridge().then(" in js

    def test_probe_bridge_uses_guarded_api_read(self):
        """probeBridge reads through ``bridgeApi()`` and bails out
        with a pill note when the bridge is absent — a stray early
        call must never throw into the global error handler.
        """
        import re
        from pathlib import Path
        js = Path("gui/assets/diagrams.js").read_text(encoding="utf-8")
        m = re.search(
            r"async function probeBridge\(\) \{(.*?)\n  \}",
            js, re.DOTALL,
        )
        assert m is not None, "probeBridge body not found"
        body = m.group(1)
        assert "bridgeApi()" in body
        assert "if (!api)" in body
        assert "await api.ping()" in body

    # ── jsdom: bridge never injected ─────────────────────────
    def test_boot_without_bridge_does_not_crash(self):
        """With NO pywebview bridge at all the boot must not
        produce a "JS error" pill, the iframe must still mount
        with the fallback URL, and the pill must end on the
        non-fatal bridge-wait note (v0.9.5.5's bug aborted the
        handler BEFORE startDrawio() — no iframe at all).
        """
        _require_jsdom_or_skip()
        out = _run_overlay_scenario(
            "bridge-none-boot",
            [{"op": "flush", "ms": 800}],
            bridge_mode="none",
            bridge_timeout_override_ms=500,
        )
        text = out["status_pill_text"] or ""
        assert "JS error" not in text, (
            f"boot crashed without a bridge: {text!r}"
        )
        assert "embed.diagrams.net" in (out["src"] or ""), (
            "iframe not mounted with the fallback embed URL; "
            f"src={out['src']!r}"
        )
        assert "未就緒" in text, (
            f"non-fatal bridge-wait note missing from pill: "
            f"{text!r}"
        )
        assert out["status_pill_kind"] == "err"

    # ── jsdom: bridge injected shortly after boot ────────────
    def test_late_bridge_injection_recovers(self):
        """Bridge arriving ~150 ms after boot (real WebView2
        timing) must be picked up by ``_waitForBridge``: the
        ``get_write_root`` result fills the sandbox path and
        ``probeBridge`` flips the pill to "bridge OK" — no error
        pill anywhere.
        """
        _require_jsdom_or_skip()
        out = _run_overlay_scenario(
            "bridge-late-boot",
            [{"op": "flush", "ms": 600}],
            bridge_mode="late",
            bridge_delay_ms=150,
        )
        text = out["status_pill_text"] or ""
        assert "JS error" not in text, (
            f"late bridge still crashed boot: {text!r}"
        )
        assert out["sandbox_path_text"] == "/tmp/sandbox", (
            f"get_write_root result missing from sandbox path: "
            f"{out['sandbox_path_text']!r}"
        )
        assert "bridge OK" in text, (
            f"probeBridge did not confirm the bridge: {text!r}"
        )
        assert out["status_pill_kind"] == "ok"
