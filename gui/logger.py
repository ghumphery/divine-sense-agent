"""Centralised logging setup for the GUI.

Single source of truth for ``app.log`` handling. Every GUI module should
import :func:`get_logger` (or just use the module-level ``logger`` below)
to ensure logs go to BOTH:

  * stderr (so users see errors in the terminal / console window)
  * ``gui/app.log`` (rotated, persistent, so we can debug after crashes)

Log format is deliberately compact so the file doesn't bloat — the GUI
can also tail the file from the Status tab (future enhancement).

v0.9.106.1 — ONE-WEEK RETENTION. The old size-based scheme
(RotatingFileHandler, 2 MB x 3 backups) had no notion of time: on a
quiet week logs older than a week survived while a busy day could
evict everything. The handler is now ``TimedRotatingFileHandler``
rotating DAILY at midnight with ``backupCount=7`` — exactly one week
of logs is kept (``app.log.2026-10-04`` naming), older files are
deleted automatically by the handler.

Why a custom handler instead of ``logging.basicConfig``:
    * ``basicConfig`` only configures the root logger once; we want
      per-module loggers that all share the same file handler.
    * The file path needs to resolve correctly under both:
        - ``python -m gui.app``   (dev mode)
        - PyInstaller-frozen .exe (sys.executable parent)
    * We want UTF-8 on Windows so CJK / emoji in messages survive.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


# ─── paths ────────────────────────────────────────────────────────

_LOG_FILE_NAME = "app.log"
_ROTATION_DAYS = 7               # keep ONE WEEK of daily-rotated logs


def _resolve_log_path() -> Path:
    """Locate ``app.log`` next to the running app.

    * Source mode → ``<repo_root>/gui/app.log``
    * Frozen mode (PyInstaller) → ``<exe_dir>/gui/app.log``
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent  # type: ignore[attr-defined]
    else:
        # This file lives at <repo>/gui/logger.py → parent = <repo>/gui
        base = Path(__file__).resolve().parent
    log_path = base / _LOG_FILE_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


# ─── formatter ────────────────────────────────────────────────────

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


# ─── public API ───────────────────────────────────────────────────

_initialised = False


def setup_logging(level: int = logging.INFO) -> Path:
    """Configure root logger with stderr + daily-rotating file handler.

    Idempotent — safe to call multiple times; only first call wires
    the handlers. Returns the resolved ``app.log`` path so callers
    can display it in the UI.

    v0.9.106.1: the file handler rotates at midnight every day and
    keeps ``_ROTATION_DAYS`` (7) days of logs — one full week.

    Usage (typically at the very top of ``gui/app.py``):

        from gui.logger import setup_logging
        setup_logging()

    Other modules just do:

        import logging
        logger = logging.getLogger(__name__)
        logger.info("…")
    """
    global _initialised
    root = logging.getLogger()
    root.setLevel(level)

    if _initialised:
        return _resolve_log_path()

    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    # ── stderr ──
    stderr_h = logging.StreamHandler(stream=sys.stderr)
    stderr_h.setFormatter(formatter)
    stderr_h.setLevel(level)
    # utf-8 on Windows (errors="replace" so a broken console doesn't crash).
    try:
        stderr_h.stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass
    root.addHandler(stderr_h)

    # ── daily-rotating file (one-week retention) ──
    log_path = _resolve_log_path()
    try:
        file_h = TimedRotatingFileHandler(
            log_path,
            when="midnight",
            backupCount=_ROTATION_DAYS,
            encoding="utf-8",
        )
        file_h.setFormatter(formatter)
        file_h.setLevel(level)
        root.addHandler(file_h)
    except Exception as e:
        # Don't let a log-init failure kill the GUI; surface to stderr.
        sys.stderr.write(f"[logger] failed to open {log_path}: {e!r}\n")

    # Quiet down noisy third-party loggers.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    _initialised = True
    return log_path


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — equivalent to ``logging.getLogger(name)``."""
    return logging.getLogger(name)


# Module-level logger for anyone who wants a quick handle without
# importing logging themselves.
logger = logging.getLogger("gui")
