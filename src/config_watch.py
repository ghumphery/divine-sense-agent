"""v0.9.42.0+ — opt-in config hot-reload watcher.

Polls the mtimes of ``.env`` and ``config/mcp.json``; on change it
invokes the injected callback (``Agent.reload_runtime`` + MCP
reconnect, wired from ``gui/api.py``). Polling (no OS events) keeps
it stdlib-only and Windows-safe.

Enable with ``QDRANT_CONFIG_HOT_RELOAD=on`` (default off — a bad
mid-edit save reloaded live can surprise; the GUI button and the
CLI ``reload`` command always work). Interval via
``QDRANT_CONFIG_WATCH_INTERVAL`` seconds (1-300, default 10).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable


def _watch_paths() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return [root / ".env", root / "config" / "mcp.json"]


def _watch_interval() -> float:
    try:
        return max(1.0, min(300.0, float(
            os.environ.get("QDRANT_CONFIG_WATCH_INTERVAL") or 10.0,
        )))
    except (TypeError, ValueError):
        return 10.0


class ConfigWatcher:
    """Daemon thread: poll file mtimes → fire the reload callback."""

    def __init__(self, on_change: Callable[[list[str]], Any],
                 paths: list[Path] | None = None,
                 interval: float | None = None):
        self._on_change = on_change
        self._paths = paths or _watch_paths()
        self._interval = max(1.0, min(300.0, float(
            interval or _watch_interval(),
        )))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, float] = self._take_snapshot()

    def _take_snapshot(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self._paths:
            try:
                out[str(p)] = p.stat().st_mtime_ns
            except OSError:
                out[str(p)] = 0.0
        return out

    def changed_paths(self) -> list[str]:
        """Paths whose mtime differs from the last snapshot."""
        now = self._take_snapshot()
        changed = [
            p for p, m in now.items() if m != self._snapshot.get(p)
        ]
        self._snapshot = now
        return changed

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                changed = self.changed_paths()
                if changed:
                    self._on_change(changed)
            except Exception:
                # never kill the watcher thread on a callback error
                pass

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="config-watcher",
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            try:
                self._thread.join(timeout=timeout)
            except RuntimeError:
                pass
        self._thread = None

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def watcher_enabled() -> bool:
    return (os.environ.get("QDRANT_CONFIG_HOT_RELOAD") or ""
            ).strip().lower() in ("1", "on", "true", "yes")