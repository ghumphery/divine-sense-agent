"""Tests for the os-input tools (v0.9.105.0) — REAL SendInput helpers.

Fail-closed gates (QDRANT_OS_INPUT_ALLOW + confirm + title_regex), the
tool surface (6 os_* tools on exec-local), window listing / focus
resolution, click restore-cursor, type cap/pacing, key whitelist, and
the passive preview. ALL low-level win/pynput functions are
monkeypatched — pytest never moves the real mouse or keyboard.
"""
from __future__ import annotations

import pathlib

import pytest

import src.os_input_tools as osi
from src.os_input_tools import (
    click_point,
    focus_window,
    os_input_status,
    press_key,
    preview_crop,
    type_text,
    windows_list,
)


def _arm(monkeypatch, title="Target Window - Firefox",
         cursor=(100, 200), screen=(1920, 1080)):
    """Gate on + fake the entire win layer with recorders."""
    monkeypatch.setenv("QDRANT_OS_INPUT_ALLOW", "on")
    monkeypatch.setattr(osi, "_list_windows", lambda: [
        {"hwnd": 1, "title": title, "pid": 42},
        {"hwnd": 2, "title": "Other Window", "pid": 43},
    ])
    monkeypatch.setattr(osi, "_fg_title", lambda: title)
    monkeypatch.setattr(osi, "_bring_to_front", lambda hwnd: None)
    monkeypatch.setattr(osi, "_get_cursor", lambda: cursor)
    monkeypatch.setattr(osi, "_set_cursor", lambda x, y: None)
    monkeypatch.setattr(osi, "_screen_size",
                        lambda: (screen[0], screen[1]))
    calls = {"click": [], "type": [], "key": []}
    monkeypatch.setattr(osi, "_real_click",
                        lambda x, y: calls["click"].append((x, y)))
    monkeypatch.setattr(osi, "_real_type",
                        lambda t, p: calls["type"].append((t, p)))
    monkeypatch.setattr(osi, "_real_key",
                        lambda n: calls["key"].append(n))
    return calls


class TestGates:
    def test_allow_off_refuses_everything(self, monkeypatch, tmp_path):
        monkeypatch.delenv("QDRANT_OS_INPUT_ALLOW", raising=False)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        assert "QDRANT_OS_INPUT_ALLOW" in click_point(
            10, 10, "Target", confirm=True)["error"]
        assert "QDRANT_OS_INPUT_ALLOW" in type_text(
            "hi", "Target", confirm=True)["error"]
        assert "QDRANT_OS_INPUT_ALLOW" in press_key(
            "Enter", "Target", confirm=True)["error"]
        assert "QDRANT_OS_INPUT_ALLOW" in focus_window(
            "Target", confirm=True)["error"]

    def test_status_readonly_reports_gate_false(
            self, monkeypatch, tmp_path):
        monkeypatch.delenv("QDRANT_OS_INPUT_ALLOW", raising=False)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = os_input_status()
        assert r["ok"] is True and r["allow"] is False

    def test_missing_confirm_refuses(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        assert "confirm=true" in click_point(
            10, 10, "Target")["error"]
        assert "confirm=true" in type_text("hi", "Target")["error"]


class TestClickAndFocus:
    def test_click_ok_restores_cursor_and_audits(
            self, monkeypatch, tmp_path):
        calls = _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = click_point(500, 300, "Target Window", confirm=True)
        assert r["ok"] is True and calls["click"] == [(500, 300)]
        assert r["cursor_restored"] is True
        audit = (tmp_path / "os_input" / "audit.jsonl").read_text(
            encoding="utf-8")
        assert "os_click_point" in audit

    def test_click_offscreen_refused(self, monkeypatch, tmp_path):
        _arm(monkeypatch, screen=(800, 600))
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        assert click_point(9999, 10, "Target",
                           confirm=True)["error"]

    def test_click_requires_title_regex(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        assert click_point(10, 10, "", confirm=True)["error"]

    def test_click_fg_mismatch_refused(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setattr(osi, "_fg_title",
                            lambda: "Google Chrome")
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = click_point(10, 10, "Target Window", confirm=True)
        assert "前景視窗不是目標" in r["error"]

    def test_focus_window_flow(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        holder = {"t": "Target Window - Firefox"}
        monkeypatch.setattr(osi, "_fg_title", lambda: holder["t"])
        monkeypatch.setattr(
            osi, "_bring_to_front",
            lambda hwnd: holder.update(t="Other Window"))
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = focus_window("Other", confirm=True)
        assert r["ok"] is True and "Other Window" in r["focused"]

    def test_focus_no_match_lists_hint(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = focus_window("NoSuchWindow", confirm=True)
        assert "os_windows_list" in r["error"]


class TestType:
    def test_type_ok(self, monkeypatch, tmp_path):
        calls = _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = type_text("hello", "Target", confirm=True)
        assert (r["ok"] is True and r["typed"] == 5
                and calls["type"] and calls["type"][0][0] == "hello")

    def test_type_over_cap(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        monkeypatch.setenv("QDRANT_OS_INPUT_MAX_TEXT", "10")
        r = type_text("x" * 11, "Target", confirm=True)
        assert r["error"] and "cap" in r["error"]

    def test_type_regex_anchor_required(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        assert type_text("hi", "", confirm=True)["error"]


class TestKey:
    def test_key_whitelist_pass(self, monkeypatch, tmp_path):
        calls = _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = press_key("Enter", "Target", confirm=True)
        assert r["ok"] is True and calls["key"] == ["enter"]

    def test_submit_combo_refused(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = press_key("Control+Enter", "Target", confirm=True)
        assert "送出類組合鍵" in r["error"]

    def test_unknown_key_refused(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = press_key("F13", "Target", confirm=True)
        assert "白名單" in r["error"]


class TestPreview:
    def test_preview_passive_writes_crop(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))

        class FakeImg:
            width = 1920
            height = 1080

            def crop(self, box):
                class C:
                    def save(self, p):
                        pathlib.Path(p).write_bytes(b"PNG")
                return C()

        from PIL import ImageGrab
        monkeypatch.setattr(ImageGrab, "grab",
                            lambda: FakeImg(), raising=False)
        r = preview_crop(960, 540)
        assert r.get("ok") is True
        assert pathlib.Path(r["preview"]).exists()

    def test_windows_list_regex(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        r = windows_list("other")
        assert (r["ok"] is True and r["count"] == 1
                and r["windows"][0]["title"] == "Other Window")


class TestSurface:
    def test_exec_host_has_six_os_tools(self):
        from src.mcp_client.host import ExecLocalHost
        names = {t.name for t in ExecLocalHost().list_tools()}
        expect = {"os_input_status", "os_windows_list", "os_focus_window",
                  "os_click_point", "os_type_text", "os_key_press"}
        assert expect <= names