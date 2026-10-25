"""v0.9.5+: independent OS-window draw.io viewer/editor.

Spawns a separate ``pywebview`` window that embeds draw.io for
live render + edit of ``.drawio`` files. The window is fully
decoupled from the main Divine Sense Agent GUI window:

* Multiple viewer windows can coexist (each holds its own diagram)
* Closing the viewer window does NOT close the main GUI
* Closing the main GUI does NOT auto-close viewers (we register
  a ``closed`` handler on the main window that explicitly
  destroys every viewer; otherwise pywebview leaves zombie
  processes hanging in the OS)

Three ways to feed a diagram into the viewer:

1. **From sandbox path** — ``open_with_path(title, path)``
   reads the file from ``QDRANT_FILESYSTEM_WRITE_ROOT``.
2. **From XML string** — ``open_with_xml(title, xml)``
   pushes the XML directly to the JS side via
   ``window.evaluate_js`` after the page mounts.
3. **From drag-drop** — the JS side renders an HTML5 drop
   zone; a dropped ``.drawio`` file is read by JS and fed to
   draw.io without ever hitting Python.

Editing workflow:

* draw.io's editor mode is enabled (default in the embed URL)
* The "Save back to sandbox" button calls
  ``save_to_sandbox(filename, xml)`` which:
  - resolves ``filename`` against ``QDRANT_FILESYSTEM_WRITE_ROOT``
    via the same ``_fs_resolve_path`` shape used by the
    filesystem MCP host (write sandbox enforced)
  - writes atomically via ``tempfile.mkstemp`` + ``os.replace``
  - dangerous-executable extensions (``.exe``, ``.bat``, ...)
    are refused even at the write root

No external dependency beyond ``pywebview`` (already a project
requirement for the main GUI).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from src.mcp_client.host import (
    _FS_DANGEROUS_WRITE_EXTS,
    _fs_resolve_path,
    _resolve_filesystem_root,
)

logger = logging.getLogger(__name__)


# Hard cap on incoming XML payload — mirrors the filesystem MCP
# 4 MB read cap so a malicious / runaway LLM cannot push a 1 GB
# blob through ``open_with_xml`` and exhaust the GUI memory.
_DIAGRAM_MAX_XML_BYTES = 4 * 1024 * 1024  # 4 MB


# v0.9.5.4: single source of truth for the drawio embed URL.
# The Python side uses it for the ``open_drawio_in_browser``
# fallback (system browser). The JS side ALSO reads it via
# ``window.pywebview.api.get_drawio_url()`` so there's no
# duplication — a future URL change only needs one edit
# here.
#
# ``embed=1`` switches the page into postMessage-only mode;
# ``proto=json`` swaps the legacy key=value framing for JSON
# postMessage payloads; ``spin=1`` shows drawio's spinner
# while booting; ``ui=min`` strips the chrome down to the
# editor surface.
_DRAWIO_EMBED_URL = (
    "https://embed.diagrams.net/"
    "?embed=1&proto=json&spin=1&ui=min"
)


class DiagramsWindowManager:
    """Track every open drawio viewer window + provide lifecycle helpers.

    Stored as ``gui.api.Api._diagrams_manager`` so the main window's
    JS bridge can route ``open_*`` calls here without re-creating
    the manager each time.
    """

    def __init__(self) -> None:
        # ``list[dict]`` rather than ``list[webview.Window]`` because
        # the windows may not exist yet when this class is imported
        # (headless / --no-gui mode), and we don't want to import
        # pywebview at module-import time.
        self._windows: list[dict[str, Any]] = []
        # Cached filesystem write root resolved once at startup.
        # ``_fs_resolve_path`` will mkdir() the root for us.
        self._write_root: Path = _resolve_filesystem_root(
            "QDRANT_FILESYSTEM_WRITE_ROOT",
        )

    # ── public API ─────────────────────────────────────────

    def open_blank(
        self, title: str = "Drawio Viewer",
    ) -> dict[str, Any]:
        """Open a viewer with no diagram loaded yet (just the toolbar)."""
        return self._open(
            title=title, initial_xml=None, source_path=None,
        )

    def open_with_xml(
        self,
        title: str,
        xml: str,
    ) -> dict[str, Any]:
        """Open a viewer and load ``xml`` immediately."""
        if not isinstance(xml, str):
            return {"error": "'xml' must be a string."}
        if not xml:
            return {"error": "'xml' must be non-empty."}
        if len(xml.encode("utf-8")) > _DIAGRAM_MAX_XML_BYTES:
            return {
                "error": (
                    f"xml is {len(xml.encode('utf-8'))} bytes, "
                    f"exceeds the {_DIAGRAM_MAX_XML_BYTES} byte cap."
                ),
            }
        return self._open(
            title=title, initial_xml=xml, source_path=None,
        )

    def open_with_path(
        self,
        title: str,
        sandbox_path: str,
    ) -> dict[str, Any]:
        """Open a viewer, loading the file at ``sandbox_path``
        relative to ``QDRANT_FILESYSTEM_WRITE_ROOT``."""
        if not sandbox_path:
            return {"error": "'path' is required."}
        try:
            target = _fs_resolve_path(
                sandbox_path, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return {"error": str(e)}
        if not target.exists():
            return {"error": f"file not found: {target.as_posix()!r}"}
        if not target.is_file():
            return {
                "error": (
                    f"path is not a regular file: "
                    f"{target.as_posix()!r}"
                ),
            }
        ext = target.suffix.lower()
        if ext not in {".drawio", ".xml", ".dio"}:
            return {
                "error": (
                    f"unsupported extension {ext!r}; expected "
                    f".drawio / .xml / .dio."
                ),
            }
        try:
            xml = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            return {"error": f"file is not valid UTF-8: {e}"}
        if len(xml.encode("utf-8")) > _DIAGRAM_MAX_XML_BYTES:
            return {
                "error": (
                    f"file is {len(xml.encode('utf-8'))} bytes, "
                    f"exceeds the {_DIAGRAM_MAX_XML_BYTES} byte cap."
                ),
            }
        return self._open(
            title=title, initial_xml=xml,
            source_path=target.as_posix(),
        )

    def close_all(self) -> None:
        """Destroy every tracked viewer window. Called from the
        main GUI's ``closed`` event so the OS doesn't end up
        with orphaned pywebview processes."""
        for entry in list(self._windows):
            try:
                win = entry.get("window")
                if win is not None:
                    win.destroy()
            except Exception as e:  # pragma: no cover
                logger.warning(
                    "diagrams_window: destroy failed: %r", e,
                )
        self._windows.clear()

    @property
    def open_count(self) -> int:
        return len(self._windows)

    def save_to_sandbox(
        self,
        filename: str,
        xml: str,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """Atomically write ``xml`` to ``filename`` inside the
        write sandbox. ``overwrite`` defaults to ``True`` so
        the editor's "Save" button doesn't have to worry about
        race conditions; callers wanting strict no-clobber
        behaviour can pass ``overwrite=False``.
        """
        if not filename:
            return {"error": "'filename' is required."}
        if not isinstance(xml, str):
            return {"error": "'xml' must be a string."}
        if not xml:
            return {"error": "'xml' must be non-empty."}
        if len(xml.encode("utf-8")) > _DIAGRAM_MAX_XML_BYTES:
            return {
                "error": (
                    f"xml is {len(xml.encode('utf-8'))} bytes, "
                    f"exceeds the {_DIAGRAM_MAX_XML_BYTES} byte cap."
                ),
            }
        try:
            target = _fs_resolve_path(
                filename, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return {"error": str(e)}
        if not target.suffix:
            # Default to .drawio so the user doesn't have to type it.
            target = target.with_suffix(".drawio")
        ext = target.suffix.lower()
        if ext not in {".drawio", ".xml", ".dio"}:
            return {
                "error": (
                    f"unsupported extension {ext!r}; expected "
                    f".drawio / .xml / .dio."
                ),
            }
        if ext in _FS_DANGEROUS_WRITE_EXTS:
            return {
                "error": (
                    f"refusing to save with dangerous extension "
                    f"{ext!r}."
                ),
            }
        if target.exists() and not overwrite:
            return {
                "error": (
                    f"file exists: {target.as_posix()!r}; "
                    f"pass overwrite=true to replace."
                ),
            }
        if not target.parent.exists():
            return {
                "error": (
                    f"parent missing: "
                    f"{target.parent.as_posix()!r}; call mkdir first."
                ),
            }
        new_bytes = xml.encode("utf-8")
        try:
            parent = target.parent
            fd, tmp_path_str = tempfile.mkstemp(
                prefix=".drawio.", suffix=target.suffix,
                dir=str(parent),
            )
            tmp_path = Path(tmp_path_str)
            try:
                with os.fdopen(fd, "wb") as tmp_f:
                    tmp_f.write(new_bytes)
                    tmp_f.flush()
                    try:
                        os.fsync(tmp_f.fileno())
                    except OSError:
                        pass
                os.replace(tmp_path, target)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            return {"error": f"save failed: {e}"}
        try:
            rel_str = target.relative_to(self._write_root).as_posix()
        except ValueError:
            rel_str = target.name
        return {
            "ok": True,
            "path": target.as_posix(),
            "relative_path": rel_str,
            "bytes_written": len(new_bytes),
            "write_root": self._write_root.as_posix(),
            "overwrite": overwrite,
        }

    def read_from_sandbox(
        self, sandbox_path: str,
    ) -> dict[str, Any]:
        """Read a diagram file from the write sandbox and return
        its XML so the caller (typically the JS editor's
        "Reload from sandbox" button) can refresh the editor
        without closing + reopening the window."""
        if not sandbox_path:
            return {"error": "'path' is required."}
        try:
            target = _fs_resolve_path(
                sandbox_path, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return {"error": str(e)}
        if not target.exists():
            return {"error": f"file not found: {target.as_posix()!r}"}
        ext = target.suffix.lower()
        if ext not in {".drawio", ".xml", ".dio"}:
            return {
                "error": (
                    f"unsupported extension {ext!r}; expected "
                    f".drawio / .xml / .dio."
                ),
            }
        try:
            xml = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return {"error": f"read failed: {e}"}
        return {
            "ok": True,
            "path": target.as_posix(),
            "xml": xml,
            "bytes": len(xml.encode("utf-8")),
        }

    # ── internals ──────────────────────────────────────────

    def _open(
        self,
        title: str,
        initial_xml: str | None,
        source_path: str | None,
    ) -> dict[str, Any]:
        """Spawn the actual ``pywebview`` window + register a
        per-window ``Api`` instance so the JS side can call
        back to ``save_to_sandbox`` etc. without re-entering
        the main GUI's ``Api`` (which is bound to the main
        window only)."""
        try:
            import webview  # pywebview — lazy import
        except ImportError:
            return {
                "error": (
                    "pywebview not installed; cannot open diagrams "
                    "window. Run: pip install pywebview"
                ),
            }
        assets_dir = _get_assets_dir()
        html = assets_dir / "diagrams.html"
        if not html.exists():
            return {
                "error": (
                    f"diagrams.html not found at {html}; the GUI "
                    f"assets are incomplete."
                ),
            }
        # Per-window API surface (only the methods the viewer
        # actually needs — keeps the JS bridge small).
        viewer_api = _DiagramsViewerApi(self, self._write_root)

        window = webview.create_window(
            title=title or "Drawio Viewer",
            url=str(html.as_uri()),
            width=1100,
            height=760,
            js_api=viewer_api,
            text_select=True,
        )

        # Track + lifecycle cleanup so we don't leak.
        entry: dict[str, Any] = {
            "window": window,
            "title": title,
            "source_path": source_path,
        }

        def _on_closed():
            try:
                self._windows.remove(entry)
            except ValueError:
                pass
            logger.info(
                "diagrams_window: closed (title=%r remaining=%d)",
                title, len(self._windows),
            )

        # pywebview's Window.events.closed is a list-like event;
        # ``+=`` appends our handler.
        try:
            window.events.closed += _on_closed
        except Exception:  # pragma: no cover
            logger.warning(
                "diagrams_window: could not register closed handler",
                exc_info=True,
            )

        self._windows.append(entry)
        logger.info(
            "diagrams_window: opened title=%r source=%r "
            "open_count=%d",
            title, source_path, len(self._windows),
        )

        # If the caller already knows the initial XML, push it
        # via evaluate_js once the page mounts. pywebview's
        # ``evaluate_js`` is safe to call BEFORE start() — it
        # gets queued and fires after the document loads.
        if initial_xml is not None:
            try:
                window.evaluate_js(
                    _wrap_xml_for_js(initial_xml),
                )
            except Exception as e:  # pragma: no cover
                logger.warning(
                    "diagrams_window: evaluate_js queue failed: %r", e,
                )

        return {
            "ok": True,
            "title": title,
            "source_path": source_path,
            "open_count": len(self._windows),
        }


class _DiagramsViewerApi:
    """Per-window Api binding. The viewer JS side calls these
    methods directly via ``window.pywebview.api.<name>(...)``.

    Kept tiny on purpose: only the calls the editor needs.
    Anything heavier (LLM dispatch, Qdrant search, …) goes
    through the main window's ``Api`` instead.
    """

    def __init__(
        self,
        manager: "DiagramsWindowManager",
        write_root: Path,
    ) -> None:
        self._mgr = manager
        self._write_root = write_root

    def save_to_sandbox(
        self,
        filename: str,
        xml: str,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        return self._mgr.save_to_sandbox(
            filename=filename, xml=xml, overwrite=overwrite,
        )

    def read_from_sandbox(
        self, sandbox_path: str,
    ) -> dict[str, Any]:
        return self._mgr.read_from_sandbox(sandbox_path)

    def get_write_root(self) -> str:
        """JS-side wants to show the user *where* diagrams will
        land. Return the absolute posix path."""
        return self._write_root.as_posix()

    def get_drawio_url(self) -> str:
        """v0.9.5.4: return the canonical drawio embed URL so
        ``diagrams.js`` doesn't have to hardcode it (single
        source of truth = the Python constant
        ``_DRAWIO_EMBED_URL``).
        """
        return _DRAWIO_EMBED_URL

    def ping(self) -> dict[str, Any]:
        """Trivial liveness probe so the JS side can confirm
        the Python bridge is alive (used by the startup
        indicator)."""
        return {"ok": True, "write_root": self._write_root.as_posix()}

    def open_drawio_in_browser(self) -> dict[str, Any]:
        """v0.9.5.4 fallback: open drawio's embed URL in the
        system default browser.

        Why: ``drawio``'s full editor bundle is ~9.6 MB of
        minified JS (see
        ``https://embed.diagrams.net/js/app.min.js``).
        WebView2 — the Chromium-based backend pywebview uses
        on Windows — boots that bundle MUCH slower than
        regular Edge/Chrome (no warm caches, single-process,
        no GPU acceleration by default). On a mid-range laptop
        the editor can take 30 s – several minutes to finish
        loading, leaving the user staring at drawio's white
        "Loading…" spinner with no visible feedback.

        Rather than fight WebView2, give the user an
        escape hatch: a button that opens the same embed URL
        in their default browser (where it's typically
        cached + JIT-optimised + finished in 2–5 s). They
        can then drag the saved XML back into our sandbox via
        the main GUI's file picker.

        The URL is the SAME one ``diagrams.js`` mounts in
        the iframe — both code paths share
        ``_DRAWIO_EMBED_URL`` so a future URL change only
        needs one edit.
        """
        try:
            import webbrowser
            webbrowser.open(_DRAWIO_EMBED_URL)
            return {"ok": True, "url": _DRAWIO_EMBED_URL}
        except Exception as e:
            logger.warning(
                "diagrams_window: open_drawio_in_browser failed: %r", e,
            )
            return {"error": f"open_drawio_in_browser failed: {e!r}"}


# ── module helpers ───────────────────────────────────────


def _get_assets_dir() -> Path:
    """Locate ``gui/assets`` regardless of source / frozen mode.
    Mirrors ``gui.app._get_assets_dir`` so a PyInstaller-bundled
    GUI still finds the viewer's HTML."""
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", "."))  # type: ignore[attr-defined]
        candidate = meipass / "gui" / "assets"
        if candidate.exists():
            return candidate
        return Path(sys.executable).parent / "gui" / "assets"
    return Path(__file__).resolve().parent / "assets"


def _wrap_xml_for_js(xml: str) -> str:
    """JSON-encode ``xml`` for ``window.evaluate_js`` so the JS
    side receives a valid JS expression with no quoting bugs.

    We dispatch via a custom event (``drawio-load-xml``) so the
    embed's own event handlers don't conflict with us. The JS
    side (``gui/assets/diagrams.js``) listens on this event.
    """
    payload = json.dumps(xml, ensure_ascii=False)
    return (
        "window.dispatchEvent(new CustomEvent("
        "'drawio-load-xml', {detail: " + payload + "}));"
    )
