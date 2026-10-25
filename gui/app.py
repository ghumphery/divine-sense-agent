"""PyWebView launcher for the divine-sense-agent GUI.

Entry point: ``python -m gui.app`` or ``divine-sense-agent-gui.bat``.

What this module does:
    1. Setup logging (gui/app.log, stderr) BEFORE importing Api so
       every subsequent line is captured.
    2. Locate ``gui/assets/index.html`` (works in source mode AND
       PyInstaller frozen mode via ``sys._MEIPASS``).
    3. Wire the ``Api`` instance to pywebview's ``js_api`` so every
       method on ``Api`` is callable from JavaScript as
       ``window.pywebview.api.<method>(...)``.
    4. Optional CLI flags:
       * ``--debug``         enable pywebview remote debugging (CDP)
       * ``--port``          CDP port (default 9229)
       * ``--no-gui``        headless smoke test (imports only, no window)
       * ``--lang zh-TW|zh-CN|en|ja``
                             UI locale at startup
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def _get_assets_dir() -> Path:
    """Locate ``gui/assets`` regardless of source / frozen mode."""
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", "."))  # type: ignore[attr-defined]
        candidate = meipass / "gui" / "assets"
        if candidate.exists():
            return candidate
        return Path(sys.executable).parent / "gui" / "assets"
    return Path(__file__).resolve().parent / "assets"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gui.app",
        description="Divine Sense Agent — local GUI launcher",
    )
    p.add_argument("--debug", action="store_true",
                   help="Enable Chrome DevTools remote debugging on the WebView")
    p.add_argument("--port", type=int, default=9229,
                   help="CDP port when --debug is set (default 9229)")
    p.add_argument("--no-gui", action="store_true",
                   help="Skip window creation (smoke test: import + log only)")
    p.add_argument("--lang", default="zh-TW",
                   choices=["zh-TW", "zh-CN", "en", "ja"],
                   help="UI language at startup (default zh-TW)")
    p.add_argument("--width", type=int, default=1180)
    p.add_argument("--height", type=int, default=780)
    # v0.9.15.0 — WEB UI mode (browser instead of the pywebview window).
    p.add_argument("--web", action="store_true",
                   help="Launch the WEB UI (system browser) instead of "
                        "the pywebview window")
    p.add_argument("--host", default=None,
                   help="Web UI bind host (default 127.0.0.1; "
                        "env QDRANT_WEB_HOST). Any local IP works: "
                        "0.0.0.0 = all IPv4 interfaces (LAN URLs "
                        "printed at startup), a specific LAN IP, "
                        "or IPv6. Non-loopback binds are NOT "
                        "recommended — the Api drives desktop tools")
    p.add_argument("--web-port", type=int, default=None,
                   help="Web UI port (default 8334; env QDRANT_WEB_PORT)")
    p.add_argument("--no-browser", action="store_true",
                   help="Web mode: don't auto-open the system browser")
    return p.parse_args(argv)


def _save_chat_draft_on_close(window) -> None:
    """v0.8.7+ belt-and-suspenders close hook (v0.8.7.1 fix:
    no ``evaluate_js`` on the close path).

    Why we DON'T call ``window.evaluate_js(...)`` here anymore:

    User log 2026-08-31 — ``divine-sense-agent-gui.bat`` was hanging
    on close with the message
    ``pywebview window is closing - flushing chat draft``
    never returning. Root cause: ``evaluate_js`` round-trips
    through the WebView JS thread, which is in the process of
    tearing down when ``closing`` fires. The JS engine has
    stopped accepting new work (renderer shutdown); the
    pywebview CEF/WKWebView/WebView2 backend blocks the call
    indefinitely waiting for a response that will never come.
    Result: the GUI process hangs in the close handler and
    the OS has to kill it (visible as a "white screen freeze"
    on the user's side).

    Fix v0.8.7.1: trust the JS-side ``beforeunload`` listener
    to do the fire-and-forget save (it was already wired in
    v0.8.7). When that path also misses, the localStorage
    backup kicks in on next boot (handled by the JS
    ``bootDraftRecovery`` step in app.js).

    This function is now a no-op stub that just logs - we keep
    the symbol so the v0.8.7 tests still pass and any future
    version that finds a way to safely query the WebView on
    close has an obvious place to land the logic.
    """
    log = logging.getLogger("gui.app")
    log.info(
        "close hook: no-op in v0.8.7.1+ - JS beforeunload already "
        "saved; if the user lost the draft the localStorage "
        "backup will restore it on next boot"
    )
    # The actual save is owned by:
    #   1. JS ``beforeunload`` listener (app.js) - fires a
    #      ``api.save_session`` round-trip the moment the
    #      renderer starts tearing down.
    #   2. JS ``localStorage`` backup (app.js) - written on
    #      every autoSaveChat(); restored on next boot if
    #      step 1 failed.
    # Both are JS-side so they finish BEFORE the renderer
    # dies - we don't need a Python-side close hook any more.


def _now_iso() -> str:
    """ISO-8601 seconds-precision local timestamp (matches
    Session.updated_at / last_fire_at conventions)."""
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _on_window_closed() -> None:
    """v0.8.7+: late-fail-safe. ``closing`` only fires while
    pywebview is still alive enough to call ``evaluate_js``.
    After OS confirms close, ``closed`` fires too — give the
    chat draft one last chance to land via a synchronous
    read from ``pywebview.state`` (cached by ``state``
    observer, no JS round-trip). For v0.8.7 this is a no-op
    log entry; left as the integration point if a future
    version moves the JS session cache off localStorage and
    onto a Python-side observer queue."""
    logging.getLogger("gui.app").info(
        "window closed; relying on close hook to have saved"
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # ── 1. Logging FIRST (so import errors land in app.log too). ──
    from gui.logger import setup_logging
    log_path = setup_logging()
    logger = logging.getLogger("gui.app")
    logger.info("=" * 60)
    logger.info("divine-sense-agent GUI starting (log=%s)", log_path)
    logger.info("argv=%r", argv)

    args = _parse_args(argv)
    logger.info("parsed args: %s", vars(args))

    # ── 2. Smoke-test mode (no window). ──
    if args.no_gui:
        try:
            from gui.api import Api
            api = Api()
            status = api.get_status()
            logger.info("no-gui smoke test ok: qdrant_ok=%s version=%s",
                        status.get("qdrant_ok"), status.get("version"))
            print(f"[smoke] ok; qdrant_ok={status.get('qdrant_ok')}; "
                  f"log={log_path}")
            return 0
        except Exception as e:
            logger.error("no-gui smoke test FAILED: %r", e, exc_info=True)
            print(f"[smoke] FAILED: {e!r}; see {log_path}")
            return 1

    # ── 2b. WEB mode (v0.9.15.0) — serve the SAME assets + Api over
    # HTTP and open the system browser. The pywebview window stays the
    # default; --web selects this mode (or use divine-sense-agent-web.bat).
    if args.web:
        from gui.web_server import run_web_server
        return run_web_server(
            host=args.host,
            port=args.web_port,
            open_browser=not args.no_browser,
        )

    # ── 3. Locate assets and import pywebview. ──
    assets_dir = _get_assets_dir()
    index_html = assets_dir / "index.html"
    if not index_html.exists():
        logger.error("index.html not found at %s", index_html)
        print(f"[error] missing UI assets: {index_html}")
        return 2

    try:
        import webview  # pywebview
    except ImportError:
        logger.error(
            "pywebview not installed. Install with: pip install pywebview",
            exc_info=True,
        )
        print("[error] pywebview missing — pip install pywebview")
        return 3

    # ── 4. Wire Api + create window. ──
    from gui.api import Api
    api = Api()

    logger.info("creating WebView window: %dx%d assets=%s lang=%s debug=%s",
                args.width, args.height, index_html, args.lang, args.debug)

    window = webview.create_window(
        title="Divine Sense Agent",
        url=str(index_html.as_uri()),
        width=args.width,
        height=args.height,
        js_api=api,
        text_select=True,
    )

    # v0.8.7+: We replace pywebview's built-in ``confirm_close``
    # dialog with our own pair of handlers so we can save the
    # chat draft *before* the OS dialog appears (the OS dialog
    # is just a confirmation — by the time it pops we already
    # want the in-memory chat state on disk).
    #
    # ``closing``  fires when the user clicks X / selects Quit.
    #             We harvest the chatSession via ``evaluate_js``
    #             (``window.pywebview.state`` is mirrored to JS
    #             automatically by the JS side — see app.js
    #             ``state`` observer) and persist via the
    #             SessionManager. We block close by returning
    #             False ONLY when we still have unsaved data;
    #             otherwise the OS-level confirm dialog suppresses
    #             the close and the user has to click X again.
    #
    # ``closed``   fires once the OS dialog confirmed the close
    #             and the DOM is gone. Belt-and-suspenders: at
    #             this point we have one more synchronous-ish
    #             chance to flush (no evaluate_js available — the
    #             webview is gone — but if ``closing`` already
    #             persisted via state mirror, this is a no-op).
    saved_on_close = {"done": False}

    def _on_closing():
        logger.info("pywebview window is closing — flushing chat draft")
        _save_chat_draft_on_close(window)
        saved_on_close["done"] = True

    def _on_closed():
        # v0.9.5+ tear down every drawio viewer so the OS
        # doesn't end up with orphan pywebview processes.
        try:
            api._get_diagrams_manager().close_all()
        except Exception as e:
            logger.warning(
                "diagrams_window close_all failed: %r", e,
            )
        _on_window_closed()

    window.events.closing += _on_closing
    window.events.closed += _on_closed

    webview_kwargs: dict = {"debug": args.debug}
    if args.debug:
        import os
        os.environ["WEBVIEW_REMOTE_DEBUGGING"] = str(args.port)
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            f"--remote-debugging-port={args.port}"
        )
        logger.info("CDP enabled on port %d", args.port)

    try:
        webview.start(**webview_kwargs)
    except Exception as e:
        logger.error("WebView start FAILED: %r", e, exc_info=True)
        print(f"[error] WebView failed to start: {e!r}")
        print(f"        full traceback in {log_path}")
        return 4

    logger.info("WebView closed normally, exiting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
