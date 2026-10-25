"""User-importable system rules (v0.9.104.6+).

使用者可以把自訂規則寫進一個 Markdown / TXT 檔，Agent 每次
``_system_prompt`` 建構時自動附加成 `USER SYSTEM RULES` 區塊：

* **預設路徑** — ``<filesystem READ root>/system_rules.md``
  （檔案存在才載入；把檔案丟進 read sandbox 即完成「匯入」，
  改存即生效、免重啟 — mtime 快取）。
* **指定路徑** — `.env` 設 ``QDRANT_SYSTEM_RULES_FILE`` 指向任何
  檔案（支援 ``~`` 展開）。
* **截斷保護** — ``QDRANT_SYSTEM_RULES_MAX_CHARS``（預設 8000），
  超過截斷並附註記，避免規則檔把 system prompt 撐爆。
* **永不 raise** — 讀不到 / 是目錄 / 解碼失敗一律回空字串，
  system prompt 與未設定時 byte-identical。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

DEFAULT_BASENAME = "system_rules.md"

_LOCK = threading.Lock()
_CACHE: dict = {"path": None, "mtime": None, "text": None}


def _rules_max_chars() -> int:
    raw = (os.environ.get("QDRANT_SYSTEM_RULES_MAX_CHARS", "")
           or "").strip()
    try:
        v = int(raw)
    except ValueError:
        return 8000
    return v if v > 0 else 8000


def _rules_path() -> Path | None:
    """Explicit env path wins; otherwise the default file inside the
    filesystem READ sandbox (import: drop the file in, done)."""
    env = (os.environ.get("QDRANT_SYSTEM_RULES_FILE", "") or "").strip()
    if env:
        return Path(os.path.expanduser(env))
    try:
        from src.mcp_client.host import _resolve_filesystem_root
        return (
            _resolve_filesystem_root("QDRANT_FILESYSTEM_READ_ROOT")
            / DEFAULT_BASENAME
        )
    except Exception:
        return None


def load_system_rules() -> str:
    """The imported user rules text ('' when unset / blank / error).

    mtime-cached: editing + saving the file takes effect on the NEXT
    system-prompt build — no restart. Never raises.
    """
    try:
        path = _rules_path()
        if path is None or not path.is_file():
            return ""
        with _LOCK:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                return ""
            if (_CACHE["path"] == str(path)
                    and _CACHE["mtime"] == mtime
                    and isinstance(_CACHE["text"], str)):
                return _CACHE["text"]
            try:
                text = path.read_text(encoding="utf-8",
                                      errors="replace").strip()
            except OSError:
                return ""
            cap = _rules_max_chars()
            if len(text) > cap:
                text = (
                    text[:cap]
                    + f"\n\n…[user rules truncated at {cap} chars — "
                      "trim the rules file to fit]"
                )
            _CACHE.update(path=str(path), mtime=mtime, text=text)
            return text
    except Exception:
        return ""


def reset_rules_cache() -> None:
    """Drop the mtime cache (config hot-reload / tests)."""
    with _LOCK:
        _CACHE.update(path=None, mtime=None, text=None)