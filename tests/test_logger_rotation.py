"""v0.9.106.1 — app.log ONE-WEEK retention (daily midnight rotation).

gui/logger.py must use TimedRotatingFileHandler (when=midnight) with
backupCount=7 so exactly one week of logs is kept, replacing the old
size-based 2MB x 3 scheme that had no notion of time.
"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import gui.logger as L
import pytest


@pytest.fixture()
def wired(tmp_path: Path):
    """Run setup_logging against a temp log path, then unwire."""
    root = logging.getLogger()
    before = list(root.handlers)
    saved_resolver = L._resolve_log_path
    saved_initialised = L._initialised
    L._initialised = False
    L._resolve_log_path = lambda: tmp_path / "app.log"
    try:
        path = L.setup_logging()
        added = [h for h in root.handlers if h not in before]
        yield path, added
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        L._resolve_log_path = saved_resolver
        L._initialised = saved_initialised


def test_daily_midnight_rotation_keeps_seven_days(wired):
    path, added = wired
    file_handlers = [
        h for h in added if isinstance(h, TimedRotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    h = file_handlers[0]
    # when="midnight" normalises to the MIDNIGHT specifier
    assert h.when == "MIDNIGHT"
    # one week retention
    assert h.backupCount == 7
    # daily suffix → app.log.2026-10-04 style backups
    assert h.suffix == "%Y-%m-%d"
    # the active log file is created immediately (no delay)
    assert Path(str(path)).exists()


def test_no_size_rotator_left(wired):
    _path, added = wired
    from logging.handlers import RotatingFileHandler

    assert not any(isinstance(h, RotatingFileHandler) for h in added)


def test_source_guards():
    source = Path(L.__file__).read_text(encoding="utf-8")
    assert "TimedRotatingFileHandler" in source
    assert "RotatingFileHandler(" not in source.replace(
        "TimedRotatingFileHandler(", ""
    )
    assert "_ROTATION_DAYS = 7" in source
