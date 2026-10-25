"""OS-level REAL input (v0.9.105.0) — SendInput keyboard/mouse.

WHY: web apps ignore synthetic DOM/JS events when they gate on
``isTrusted`` (Microsoft 365 login tile, Teams & Outlook compose boxes,
Teams 傳送鈕…). The ONLY software path that produces trusted input is
OS-level injection (SendInput) — the same driver path a physical
keyboard/mouse uses. The firefox bridge computes exact physical screen
coordinates of any element (``browser_element_point``); these tools
turn them into real input.

SAFETY (fail-closed):
* master gate ``QDRANT_OS_INPUT_ALLOW=on`` (default off → every tool
  refuses with a hint)
* acting tools (focus / click / type / key) require per-call
  ``confirm=true``
* ``title_regex`` MUST match the CURRENT foreground window title right
  before acting — refuse otherwise (anti-wrong-window anchor)
* ``os_click_point(preview=true)`` is passive: it only captures a crop
  around the target into the sandbox (``os_input/preview-*.png``) for
  ``describe_image`` review — no cursor move, no focus steal
* ``os_click_point`` restores the previous cursor position
* text cap ``QDRANT_OS_INPUT_MAX_TEXT`` (default 2000) + key whitelist
  + per-char pacing ``QDRANT_OS_INPUT_PACING_S`` (0.03 s)
* acting calls append audit lines to ``os_input/audit.jsonl``

Pure execution logic: no MCP wiring here — ``ExecLocalHost`` in
``src/mcp_client/host.py`` declares the tool surface. Low-level win
functions are module attrs so tests can monkeypatch them (no real
mouse/keyboard movement under pytest).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

__version__ = "0.9.105.0"

__all__ = [
    "KEY_WHITELIST", "os_gates", "os_input_status", "windows_list",
    "focus_window", "click_point", "type_text", "press_key", "preview_crop",
]

# key 名 → pynput Key 屬性（白名單；送出類組合鍵一律不收）
KEY_WHITELIST = {
    "enter": "enter", "escape": "esc", "esc": "esc", "tab": "tab",
    "space": "space", "backspace": "backspace", "delete": "delete",
    "home": "home", "end": "end", "pageup": "page_up",
    "pagedown": "page_down", "arrowup": "up", "arrowdown": "down",
    "arrowleft": "left", "arrowright": "right",
    "ctrl+enter": None, "control+enter": None,
    "meta+enter": None, "alt+enter": None,   # 送出類組合鍵 — 永遠拒絕
}


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(float(os.environ.get(name) or default))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(v, hi))


def _float_env(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(v, hi))


def os_gates() -> dict:
    allow = os.environ.get("QDRANT_OS_INPUT_ALLOW", "off").strip().lower() == "on"
    return {
        "allow": allow,
        "max_text": _int_env("QDRANT_OS_INPUT_MAX_TEXT", 2000, 1, 100000),
        "pacing_s": _float_env("QDRANT_OS_INPUT_PACING_S", 0.03, 0.0, 2.0),
        "max_windows": _int_env("QDRANT_OS_INPUT_MAX_WINDOWS", 200, 1, 2000),
    }


def _sandbox_root() -> Path:
    return Path(os.environ.get("QDRANT_FILESYSTEM_WRITE_ROOT") or "./sandbox").resolve()


def os_input_root() -> Path:
    p = _sandbox_root() / "os_input"
    p.mkdir(parents=True, exist_ok=True)
    return p


def audit(action: str, **kw: Any) -> None:
    try:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action}
        rec.update(kw)
        with (os_input_root() / "audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _refuse(action: str, why: str) -> dict:
    return {"ok": False, "action": action, "error": why}


# ─── low-level win layer (module attrs ⇒ monkeypatchable) ────────

def _list_windows() -> list[dict]:
    """Visible top-level windows with a real title."""
    import win32gui
    import win32process
    out = []

    def cb(hwnd, _acc):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            if not title.strip():
                return True
            _t, pid = win32process.GetWindowThreadProcessId(hwnd)
            out.append({"hwnd": int(hwnd), "title": title, "pid": int(pid)})
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return out


def _fg_title() -> str:
    import win32gui
    try:
        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:
        return ""


def _bring_to_front(hwnd: int) -> None:
    # Alt-trick 規避 Windows 前景鎖：ALT down → SetForegroundWindow → ALT up
    import win32gui
    import win32con
    import ctypes
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        user32 = ctypes.windll.user32
        user32.keybd_event(0x12, 0, 0, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        finally:
            user32.keybd_event(0x12, 0, 0x0002, 0)
    except Exception:
        win32gui.SetForegroundWindow(hwnd)


def _set_cursor(x: int, y: int) -> None:
    import win32api
    win32api.SetCursorPos((int(x), int(y)))


def _get_cursor() -> tuple[int, int] | None:
    try:
        import win32api
        p = win32api.GetCursorPos()
        return int(p[0]), int(p[1])
    except Exception:
        return None


def _screen_size() -> tuple[int, int]:
    import ctypes
    u = ctypes.windll.user32
    return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))


def _pynput():
    try:
        from pynput.mouse import Controller as MouseC, Button
        from pynput.keyboard import Controller as KeyC, Key
        return MouseC(), KeyC(), Key
    except Exception as e:
        raise RuntimeError(f"pynput unavailable: {e!r} — pip install pynput")


def _real_click(x: int, y: int) -> None:
    from pynput.mouse import Button
    mouse, _k, _kk = _pynput()
    _set_cursor(int(x), int(y))
    mouse.click(Button.left, 1)


def _real_type(text: str, pacing: float) -> None:
    _m, kb, _k = _pynput()
    for ch in text:
        kb.type(ch)
        if pacing > 0:
            time.sleep(pacing)


def _real_key(name: str) -> None:
    _m, kb, Key = _pynput()
    attr = KEY_WHITELIST.get(name.lower().strip())
    if not attr:
        raise ValueError(f"key {name!r} not on whitelist")
    kobj = getattr(Key, attr)
    kb.press(kobj)
    kb.release(kobj)


# ─── capability read ─────────────────────────────────────────────

def os_input_status() -> dict:
    g = os_gates()
    out: dict[str, Any] = {"ok": True, "allow": g["allow"],
                           "max_text": g["max_text"],
                           "pacing_s": g["pacing_s"]}
    try:
        cw = _get_cursor()
        out["current_cursor"] = {"x": cw[0], "y": cw[1]} if cw else None
    except Exception:
        out["current_cursor"] = None
    try:
        out["screen"] = {"w": _screen_size()[0], "h": _screen_size()[1]}
    except Exception:
        out["screen"] = None
    out["foreground"] = _fg_title()
    try:
        _pynput()
        out["pynput"] = "ok"
    except Exception as e:
        out["pynput"] = str(e)
    out["note"] = (
        "流程：browser_element_point 取實體座標 → os_click_point"
        "（可先 preview=true 存沙箱裁圖供 describe_image 目視）→"
        " os_type_text → os_key_press Enter；全部需 "
        "QDRANT_OS_INPUT_ALLOW=on＋confirm=true＋title_regex 命中前景。"
    )
    return out


def windows_list(regex: str = "", limit: int = 0) -> dict:
    g = os_gates()
    rows = _list_windows()
    if regex:
        try:
            rx = re.compile(regex, re.IGNORECASE)
        except re.error as e:
            return _refuse("os_windows_list", f"bad regex: {e!r}")
        rows = [r for r in rows if rx.search(r["title"])]
    rows = rows[: g["max_windows"] if limit <= 0 else min(limit, g["max_windows"])]
    return {"ok": True, "count": len(rows), "windows": rows,
            "foreground": _fg_title()}


# ─── acting tools ────────────────────────────────────────────────

def _check_gate(action: str) -> dict | None:
    if not os_gates()["allow"]:
        return _refuse(
            action,
            "QDRANT_OS_INPUT_ALLOW 未開啟（fail-closed）— 本工具組會"
            "接管實體鍵鼠，須環境變數明確 opt-in：QDRANT_OS_INPUT_ALLOW=on。",
        )
    return None


def _check_confirm(action: str, confirm: Any) -> dict | None:
    if confirm is not True:
        return _refuse(
            action,
            "需要 confirm=true — OS 輸入以真人事件接管鍵鼠數秒；請先"
            " os_input_status／os_click_point(preview=true) 核對無誤後再 confirm。",
        )
    return None


def _resolve_target(title_regex: str, action: str,
                    focus: bool = True) -> dict | None:
    """（focus=true 時）把最佳匹配視窗帶到前景，並驗證前景標題符合錨點。"""
    if not title_regex:
        return _refuse(action, "'title_regex' is required（防錯窗錨點）")
    try:
        rx = re.compile(title_regex, re.IGNORECASE)
    except re.error as e:
        return _refuse(action, f"bad title_regex: {e!r}")
    if focus:
        hits = [w for w in _list_windows() if rx.search(w["title"])]
        if not hits:
            return _refuse(
                action,
                f"找不到符合 title_regex={title_regex!r} 的視窗 — 先用 "
                "os_windows_list 券對可用標題。",
            )
        hits.sort(key=lambda w: len(w["title"]))
        target = hits[0]
        if _fg_title() != target["title"]:
            try:
                _bring_to_front(target["hwnd"])
            except Exception:
                pass  # 前景鎖失敗不致命 — 底下 fg 驗證後備把關
            time.sleep(0.35)
    fg = _fg_title()
    if not (fg and rx.search(fg)):
        return _refuse(
            action,
            f"前景視窗不是目標（fg={fg!r} 不匹配 title_regex="
            f"{title_regex!r}）— 中止以保安全。",
        )
    return None


def focus_window(title_regex: str, confirm: bool = False) -> dict:
    a = "os_focus_window"
    for bad in (_check_gate(a), _check_confirm(a, confirm)):
        if bad:
            return bad
    bad = _resolve_target(title_regex, a, focus=True)
    if bad:
        return bad
    audit(a, title=_fg_title()[:160], confirm=True)
    return {"ok": True, "focused": _fg_title()}


def _check_point(x: Any, y: Any) -> tuple[int, int] | None:
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return None
    sw, sh = _screen_size()
    if x < 0 or y < 0 or x >= sw or y >= sh:
        return None
    return x, y


def preview_crop(x: Any, y: Any, half: int = 60) -> dict:
    """被動預覽：不動滑鼠不搶焦點 — 把 (x,y) 周邊裁圖寫入 sandbox。"""
    a = "os_click_point[preview]"
    if _check_gate(a):
        return _check_gate(a)
    pt = _check_point(x, y)
    if pt is None:
        return _refuse(a, f"point 非法或超出螢幕（{_screen_size()}）")
    x, y = pt
    try:
        half = max(10, min(int(half), 300))
    except (TypeError, ValueError):
        half = 60
    from PIL import ImageGrab
    img = ImageGrab.grab()
    box = [max(0, x - half), max(0, y - half),
           min(img.width, x + half), min(img.height, y + half)]
    dest = os_input_root() / f"preview-{time.strftime('%H%M%S')}.png"
    img.crop(tuple(box)).save(dest)
    return {"ok": True, "preview": dest.as_posix(), "crop_box": box,
            "note": "被動預覽（未移鼠未點擊）— describe_image 核對後再 confirm 下真擊。"}


def click_point(x: Any, y: Any, title_regex: str = "",
                confirm: bool = False, restore_cursor: bool = True) -> dict:
    a = "os_click_point"
    for bad in (_check_gate(a), _check_confirm(a, confirm)):
        if bad:
            return bad
    pt = _check_point(x, y)
    if pt is None:
        return _refuse(a, f"point 非法或超出螢幕（{_screen_size()}）— "
                          "座標應來自 browser_element_point 的 physX/physY")
    x, y = pt
    bad = _resolve_target(title_regex, a, focus=True)
    if bad:
        return bad
    before, btitle = _get_cursor(), _fg_title()
    try:
        _real_click(x, y)
    except Exception as e:
        return {"ok": False, "action": a, "error": f"click failed: {e!r}"}
    if restore_cursor and before:
        _set_cursor(*before)
    audit(a, title=btitle[:160], x=x, y=y, confirm=True)
    return {"ok": True, "clicked": [x, y], "foreground": btitle,
            "cursor_restored": bool(before and restore_cursor),
            "note": "已真點擊 — 後續 os_type_text/os_key_press 進入剛聚焦元件。"}


def type_text(text: str, title_regex: str = "",
              confirm: bool = False) -> dict:
    a = "os_type_text"
    g = os_gates()
    for bad in (_check_gate(a), _check_confirm(a, confirm)):
        if bad:
            return bad
    text = str(text or "")
    if not text:
        return _refuse(a, "'text' is required")
    if len(text) > g["max_text"]:
        return _refuse(a, f"text {len(text)} chars > cap {g['max_text']}")
    bad = _resolve_target(title_regex, a, focus=False)
    if bad:
        return bad
    try:
        _real_type(text, g["pacing_s"])
    except Exception as e:
        return {"ok": False, "action": a, "error": f"type failed: {e!r}"}
    audit(a, title=_fg_title()[:160], chars=len(text), confirm=True)
    return {"ok": True, "typed": len(text), "foreground": _fg_title(),
            "note": "已真人級鍵入（SendInput）— 可用橋端 snapshot 複核文字已入元件。"}


def press_key(key: str, title_regex: str = "",
              confirm: bool = False) -> dict:
    a = "os_key_press"
    for bad in (_check_gate(a), _check_confirm(a, confirm)):
        if bad:
            return bad
    name = str(key or "").strip().lower()
    if not name:
        return _refuse(a, "'key' is required")
    if KEY_WHITELIST.get(name, "") is None:
        return _refuse(a, f"key {name!r} 為送出類組合鍵 — 一律拒絕（安全設計）")
    if name not in KEY_WHITELIST:
        return _refuse(
            a,
            "key %r 不在白名單（可用：%s；送出類組合鍵不收）" % (
                name,
                ", ".join(sorted(k for k, v in KEY_WHITELIST.items() if v)),
            ),
        )
    bad = _resolve_target(title_regex, a, focus=False)
    if bad:
        return bad
    try:
        _real_key(name)
    except Exception as e:
        return {"ok": False, "action": a, "error": f"key failed: {e!r}"}
    audit(a, title=_fg_title()[:160], key=name, confirm=True)
    return {"ok": True, "key": name, "foreground": _fg_title()}