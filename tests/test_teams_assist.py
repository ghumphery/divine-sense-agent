"""Teams desktop-assist tests (v0.9.69.0+).

Covers the full mock chain: engine resolvers / anti-fabrication guard /
screen watcher (skip conditions + fire + fail-safe) / fire engine
(session + task registry + corrective guard) / TeamsAssistLocalHost
tool surface + reply gate / manager + agent wiring / boot resume /
stdio parity. Mock-first: no real screen grab, no network.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

TEAMS_ENV_VARS = (
    "QDRANT_TEAMS_ASSIST_MODE",
    "QDRANT_TEAMS_ASSIST_POLL_INTERVAL",
    "QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL",
    "QDRANT_TEAMS_ASSIST_AUTO",
    "QDRANT_TEAMS_ASSIST_AUTORESUME",
    "QDRANT_TEAMS_ASSIST_SEND_MODE",
    "QDRANT_TEAMS_ASSIST_WATCH_MODE",
    "QDRANT_TEAMS_ASSIST_FIRE_HISTORY",
    "QDRANT_TEAMS_ASSIST_UIA_SCRIPT",
    "TEAMS_ASSIST_ALLOW_CHATS",
    "TEAMS_ASSIST_WINDOW_RECT",
    "TEAMS_MY_NAME",
    "TEAMS_MY_ROLE",
    "TEAMS_REPLY_STYLE",
)


@pytest.fixture()
def ta_home(monkeypatch, tmp_path):
    """Hermetic QDRANT_RAG_HOME + wiped Teams env."""
    home = tmp_path / "ta_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QDRANT_RAG_HOME", str(home))
    for var in TEAMS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return home


def _png(gradient: bool = True, size: tuple = (80, 60)) -> bytes:
    buf = io.BytesIO()
    if gradient:
        img = Image.new("L", size)
        for x in range(size[0]):
            for y in range(size[1]):
                img.putpixel((x, y), (x * 3) % 256)
    else:
        img = Image.new("L", size, 128)
    img.save(buf, format="PNG")
    return buf.getvalue()


# ─── engine resolvers + guard ─────────────────────────────────────


class TestResolvers:
    def test_mode_default_mock_and_invalid_falls_back(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import _assist_mode

        assert _assist_mode() == "mock"
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "live")
        assert _assist_mode() == "live"
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "bogus")
        assert _assist_mode() == "mock"

    def test_interval_clamps(self, ta_home, monkeypatch):
        from src.teams_assist_tools import (
            _min_fire_interval, _poll_interval,
        )

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_POLL_INTERVAL", "5")
        assert _poll_interval() == 10.0
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_POLL_INTERVAL", "999")
        assert _poll_interval() == 300.0
        monkeypatch.delenv("QDRANT_TEAMS_ASSIST_POLL_INTERVAL")
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL", "-3")
        assert _min_fire_interval() == 0.0
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL", "x")
        assert _min_fire_interval() == 120.0

    def test_auto_off_by_default(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _auto_enabled

        assert _auto_enabled() is False
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_AUTO", "on")
        assert _auto_enabled() is True
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_AUTO", "OFF")
        assert _auto_enabled() is False

    def test_allow_chats_parsing(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _allow_chats

        assert _allow_chats() == []
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", " 專案區 , 瑣事 ,")
        assert _allow_chats() == ["專案區", "瑣事"]

    def test_window_rect_parsing(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _window_rect

        assert _window_rect() is None
        monkeypatch.setenv("TEAMS_ASSIST_WINDOW_RECT", "10,20,800,600")
        assert _window_rect() == (10, 20, 800, 600)
        monkeypatch.setenv("TEAMS_ASSIST_WINDOW_RECT", "10,20,0,600")
        assert _window_rect() is None
        monkeypatch.setenv("TEAMS_ASSIST_WINDOW_RECT", "bad")
        assert _window_rect() is None


class TestReplyGuard:
    def test_claim_without_dispatch_fires(self):
        from src.teams_assist_tools import _needs_reply_guard

        assert _needs_reply_guard("已回覆對方了", []) is True
        assert _needs_reply_guard("已送出", ["describe_image"]) is True

    def test_claim_with_teams_dispatch_does_not(self):
        from src.teams_assist_tools import _needs_reply_guard

        assert _needs_reply_guard(
            "已回覆", ["teams_assist_reply"],
        ) is False

    def test_no_claim_no_guard(self):
        from src.teams_assist_tools import _needs_reply_guard

        assert _needs_reply_guard("(無新訊息)", []) is False
        assert _needs_reply_guard("", []) is False

# ─── screen watcher ───────────────────────────────────────────────


class TestWatcher:
    def _watcher(self, pngs, fire=None):
        from src.teams_assist_tools import TeamsAssistWatcher

        grabs = list(pngs)
        return TeamsAssistWatcher(
            grab=lambda: grabs.pop(0) if grabs else pngs[-1],
            fire=fire,
        )

    def test_first_change_fires_then_no_change_skips(
        self, ta_home,
    ):
        fired = []
        png = _png()
        w = self._watcher([png], fire=lambda p: fired.append(p))
        r1 = w.poll_once()
        assert r1["fired"] is True and r1["changed"] is True
        assert fired and fired[0]["source"] == "teams-assist-watch"
        assert fired[0]["mode"] == "mock"
        r2 = w.poll_once()
        assert r2["fired"] is False and r2["reason"] == "no change"

    def test_paused_skips(self, ta_home):
        from src.teams_assist_tools import _save_state, _load_state

        st = _load_state()
        st["paused"] = True
        _save_state(st)
        png = _png()
        w = self._watcher([png], fire=lambda p: None)
        r = w.poll_once()
        assert r["reason"] == "paused"
        assert _load_state()["last_check_at"] is None

    def test_uniform_screen_skips(self, ta_home):
        w = self._watcher([_png(gradient=False)], fire=lambda p: None)
        r = w.poll_once()
        assert r["fired"] is False
        assert r["reason"].startswith("uniform-screen")

    def test_cooldown_gate(self, ta_home, monkeypatch):
        from datetime import datetime

        from src.teams_assist_tools import (
            _load_state, _save_state,
        )

        st = _load_state()
        st["last_fire_at"] = datetime.now().isoformat(
            timespec="seconds",
        )
        _save_state(st)
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL", "600")
        w = self._watcher([_png()], fire=lambda p: None)
        r = w.poll_once()
        assert r["fired"] is False and r["changed"] is True
        assert "cooldown" in r["reason"]

    def test_grab_failures_auto_pause(self, ta_home):
        from src.teams_assist_tools import (
            MAX_GRAB_FAILURES, _load_state,
        )

        def boom():
            raise OSError("no screen")

        w = self._watcher([], fire=None)
        w._grab = boom
        for _ in range(MAX_GRAB_FAILURES):
            r = w.poll_once()
            assert r["ok"] is False
        st = _load_state()
        assert st["paused"] is True
        assert st["grab_failures"] == MAX_GRAB_FAILURES
        kinds = [row["kind"] for row in st["audit"]]
        assert "auto-paused" in kinds
        # once paused, poll skips entirely
        assert w.poll_once()["reason"] == "paused"

    def test_start_refuses_empty_allowlist(self, ta_home):
        from src.teams_assist_tools import TeamsAssistWatcher

        w = TeamsAssistWatcher(grab=lambda: _png(), fire=None)
        assert w.start() is False
        assert w.is_running is False

    def test_start_stop_lifecycle(self, ta_home, monkeypatch):
        from src.teams_assist_tools import (
            TeamsAssistWatcher, _load_state,
        )

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區")
        w = TeamsAssistWatcher(grab=lambda: _png(), fire=lambda p: None)
        assert w.start() is True
        assert w.is_running is True
        assert w.start() is False  # idempotent
        assert w.stop() is True
        assert w.is_running is False
        st = _load_state()
        assert st["watch"]["running"] is False
        assert st["watch"]["started_at"]

    def test_no_fire_engine_wired(self, ta_home):
        w = self._watcher([_png()], fire=None)
        r = w.poll_once()
        assert r["changed"] is True
        assert r["fired"] is False
        assert r["reason"] == "no fire engine wired"

    def test_fire_exception_contained(self, ta_home):
        def boom(p):
            raise RuntimeError("fire exploded")

        w = self._watcher([_png()], fire=boom)
        r = w.poll_once()
        assert r["ok"] is False and "fire exploded" in r["error"]

# ─── UIA watch (detection) mode (v0.9.71.0+) ──────────────────────


class TestUiaWatchMode:
    def _watcher(self, texts=None, pngs=None, fire=None,
                 uia_read=None):
        from src.teams_assist_tools import TeamsAssistWatcher

        ts = list(texts or [])
        grabs = list(pngs or [])
        return TeamsAssistWatcher(
            grab=lambda: (grabs.pop(0) if grabs else _png()),
            uia_read=(uia_read or (
                lambda: ts.pop(0) if ts else ""
            )),
            fire=fire,
        )

    def test_resolver_default_screen_and_invalid_falls_back(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import _watch_mode

        assert _watch_mode() == "screen"
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_WATCH_MODE", "uia")
        assert _watch_mode() == "uia"
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_WATCH_MODE", "bogus")
        assert _watch_mode() == "screen"

    def test_uia_signature_normalises_crlf(self, ta_home):
        from src.teams_assist_tools import _uia_signature

        a = _uia_signature("hi\r\nthere")
        b = _uia_signature("hi\nthere")
        assert a["hash"] == b["hash"]
        assert a["chars"] == len("hi\nthere")

    def test_uia_change_fires_then_unchanged_skips(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import (
            _load_state, _uia_signature,
        )

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_WATCH_MODE", "uia")
        fired = []
        w = self._watcher(
            texts=["聊天內容 v1", "聊天內容 v1"],
            fire=fired.append,
        )
        r1 = w.poll_once()
        assert r1["fired"] is True and r1["changed"] is True
        p = fired[0]
        assert p["watch_mode"] == "uia"
        assert p["chat_text_hash"] == \
            _uia_signature("聊天內容 v1")["hash"][:12]
        assert p["text_chars"] == len("聊天內容 v1")
        assert _load_state()["last_uia_hash"] == \
            _uia_signature("聊天內容 v1")["hash"]
        r2 = w.poll_once()
        assert r2["fired"] is False
        assert r2["reason"] == "no change (uia)"
        assert len(fired) == 1

    def test_uia_cooldown_gate(self, ta_home, monkeypatch):
        from datetime import datetime

        from src.teams_assist_tools import _load_state, _save_state

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_WATCH_MODE", "uia")
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL",
                           "600")
        st = _load_state()
        st["last_fire_at"] = datetime.now().isoformat(
            timespec="seconds",
        )
        _save_state(st)
        w = self._watcher(texts=["新訊息！"], fire=lambda p: None)
        r = w.poll_once()
        assert r["changed"] is True and r["fired"] is False
        assert "cooldown" in r["reason"]
        # watermark still advanced during cooldown
        assert _load_state()["last_uia_hash"] is not None

    def test_uia_read_failure_falls_back_to_pixel_path(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import _load_state

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_WATCH_MODE", "uia")
        fired = []

        def broken_read():
            raise RuntimeError("uia read failed: no-teams-window")

        # a fresh non-uniform PNG every tick → the pixel path fires
        w = self._watcher(
            pngs=[_png(), _png()],
            fire=lambda p: fired.append(p),
            uia_read=broken_read,
        )
        r = w.poll_once()
        assert r["fired"] is True
        assert fired and fired[0]["watch_mode"] == "screen"
        st = _load_state()
        assert st["last_uia_error"] and "no-teams-window" in \
            st["last_uia_error"]
        # the watch is NOT paused by UIA failures
        assert st["paused"] is False

    def test_uia_empty_text_falls_back(self, ta_home, monkeypatch):
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_WATCH_MODE", "uia")
        fired = []
        w = self._watcher(
            texts=["", "  "],
            pngs=[_png()],
            fire=lambda p: fired.append(p),
        )
        r = w.poll_once()
        assert r["fired"] is True  # pixel fallback fired

    def test_start_records_watch_mode(self, ta_home, monkeypatch):
        from src.teams_assist_tools import (
            TeamsAssistWatcher, _load_state,
        )

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區")
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_WATCH_MODE", "uia")
        w = TeamsAssistWatcher(
            grab=lambda: _png(),
            uia_read=lambda: "x",
            fire=lambda p: None,
        )
        assert w.start() is True
        st = _load_state()
        assert st["watch"]["running"] is True
        assert st["watch"]["watch_mode"] == "uia"
        w.stop()

    def test_uia_failure_never_pauses_watch(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _load_state

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_WATCH_MODE", "uia")
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "*")
        calls = []

        def broken_read():
            calls.append(1)
            raise RuntimeError("boom")

        w = self._watcher(
            pngs=[_png(gradient=False)] * 10,  # uniform = no fire
            fire=lambda p: None,
            uia_read=broken_read,
        )
        for _ in range(8):
            w.poll_once()
        st = _load_state()
        assert st["paused"] is False
        assert len(calls) == 8

    def test_host_status_exposes_watch_mode(self, ta_home):
        from src.mcp_client.host import TeamsAssistLocalHost

        r = TeamsAssistLocalHost()._status({})
        assert r["watch_mode"] == "screen"
        assert r["send_mode"] == "windows-mcp"

# ─── runtime config (LLM-facing, process-only) (v0.9.72.0+) ───────


class TestRuntimeConfig:
    def _pin(self, monkeypatch, *keys):
        """Pre-register every env var _set_config may touch so
        monkeypatch teardown restores the pre-test value (no leaks
        into the real os.environ)."""
        import os

        from src.teams_assist_tools import _ENV_OF

        for k in keys:
            env = _ENV_OF[k]
            monkeypatch.setenv(env, os.environ.get(env, ""))

    def test_multi_key_updates_resolvers(self, ta_home, monkeypatch):
        from src.teams_assist_tools import (
            _allow_chats, _auto_enabled, _set_config, _watch_mode,
        )

        self._pin(monkeypatch, "watch_mode", "auto", "allow_chats")
        out = _set_config({
            "watch_mode": "uia", "auto": "on",
            "allow_chats": "專案區, 瑣事",
        })
        assert out["ok"] is True
        assert _watch_mode() == "uia"
        assert _auto_enabled() is True
        assert _allow_chats() == ["專案區", "瑣事"]
        assert out["applied"]["watch_mode"]["new"] == "uia"
        assert out["effective"]["watch_mode"] == "uia"
        assert out["effective"]["auto"] is True
        assert "process" in out["note"]

    def test_unknown_key_rejected_nothing_applied(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import _set_config, _watch_mode

        self._pin(monkeypatch, "watch_mode")
        out = _set_config({"watch_mode": "uia", "bogus": "x"})
        assert "unknown key" in out["error"]
        assert "bogus" in out["error"]
        assert "watch_mode" in out["valid_keys"]
        assert _watch_mode() == "screen"  # all-or-nothing

    def test_invalid_value_all_or_nothing(self, ta_home, monkeypatch):
        from src.teams_assist_tools import (
            _auto_enabled, _set_config,
        )

        self._pin(monkeypatch, "auto", "watch_mode")
        out = _set_config({"watch_mode": "bogus", "auto": "on"})
        assert "invalid value" in out["error"]
        assert "watch_mode" in out["error"]
        assert _auto_enabled() is False  # valid key NOT applied either

    def test_allow_chats_list_and_empty(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _allow_chats, _set_config

        self._pin(monkeypatch, "allow_chats")
        assert _set_config({"allow_chats": ["a", "b"]})["ok"] is True
        assert _allow_chats() == ["a", "b"]
        assert _set_config({"allow_chats": ""})["ok"] is True
        assert _allow_chats() == []

    def test_window_rect_set_and_clear(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _set_config, _window_rect

        self._pin(monkeypatch, "window_rect")
        out = _set_config({"window_rect": "10,20,800,600"})
        assert out["ok"] is True
        assert _window_rect() == (10, 20, 800, 600)
        out = _set_config({"window_rect": None})
        assert out["ok"] is True
        assert out["applied"]["window_rect"]["new"] is None
        assert _window_rect() is None

    def test_window_rect_bad(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _set_config

        self._pin(monkeypatch, "window_rect")
        assert "invalid value" in _set_config(
            {"window_rect": "10,20,0,600"},
        )["error"]
        assert "invalid value" in _set_config(
            {"window_rect": "bad"},
        )["error"]

    def test_intervals_clamped_effective(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _set_config

        self._pin(monkeypatch, "poll_interval", "min_fire_interval")
        out = _set_config({"poll_interval": 5, "min_fire_interval": 9999})
        assert out["ok"] is True
        assert out["effective"]["poll_interval_s"] == 10.0
        assert out["effective"]["min_fire_interval_s"] == 3600.0
        assert "must be a positive number" in _set_config(
            {"poll_interval": -1},
        )["error"]

    def test_auto_accepts_bool_words(self, ta_home, monkeypatch):
        from src.teams_assist_tools import (
            _auto_enabled, _set_config,
        )

        self._pin(monkeypatch, "auto")
        assert _set_config({"auto": "TRUE"})["ok"] is True
        assert _auto_enabled() is True
        assert _set_config({"auto": "0"})["ok"] is True
        assert _auto_enabled() is False
        assert "must be on|off" in _set_config({"auto": "maybe"})["error"]

    def test_persona_keys(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _persona, _set_config

        self._pin(monkeypatch, "my_name", "my_role", "reply_style")
        out = _set_config({
            "my_name": "王小明", "my_role": "PM",
            "reply_style": "輕鬆",
        })
        assert out["ok"] is True
        assert _persona() == {
            "name": "王小明", "role": "PM", "style": "輕鬆",
        }

    def test_host_tool_dispatch_and_status(self, ta_home, monkeypatch):
        from src.mcp_client.host import TeamsAssistLocalHost

        self._pin(monkeypatch, "watch_mode")
        h = TeamsAssistLocalHost()
        out = h._config({"watch_mode": "uia"})
        assert out["ok"] is True
        assert h._status({})["watch_mode"] == "uia"
        assert h._config({}).get("error") == "nothing to set"

    def test_system_prompt_mentions_config_tool(self, ta_home):
        from src.agent.agent import Agent

        prompt = Agent()._system_prompt(tools_enabled=True)
        assert "teams_assist_config" in prompt
        assert "TEMPORARILY" in prompt
        legacy = Agent()._system_prompt(tools_enabled=False)
        assert "teams_assist_config" not in legacy

    def test_wildcard_allowlist_resolver(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _allow_all, _allow_chats

        assert _allow_all() is False  # empty = fail-closed, not all
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "*")
        assert _allow_all() is True
        assert _allow_chats() == ["*"]

    def test_wildcard_fire_prompt_engages_all(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import _compose_fire_prompt

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "*")
        p = _compose_fire_prompt(None)
        assert "萬用字元" in p
        assert "任何聊天" in p
        assert "白名單外 — 未處理" not in p

    def test_wildcard_start_watch_allowed(self, ta_home, monkeypatch):
        from src.teams_assist_tools import TeamsAssistWatcher

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "*")
        w = TeamsAssistWatcher(
            grab=lambda: _png(),
            uia_read=lambda: "x",
            fire=lambda p: None,
        )
        assert w.start() is True  # '*' is non-empty → passes fail-closed
        w.stop()

    def test_config_all_synonyms_map_to_wildcard(
        self, ta_home, monkeypatch,
    ):
        from src.mcp_client.host import TeamsAssistLocalHost

        self._pin(monkeypatch, "allow_chats")
        h = TeamsAssistLocalHost()
        out = h._config({"allow_chats": "所有"})
        assert out["ok"] is True
        assert out["applied"]["allow_chats"]["new"] == "*"
        st = h._status({})
        assert st["allow_all"] is True
        assert h._config({"allow_chats": "all"})["applied"][
            "allow_chats"]["new"] == "*"

    def test_chat_allowed_filter_match(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _chat_allowed

        # empty allowlist → fail-closed
        assert _chat_allowed("任何聊天") is False
        # exact match
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區")
        assert _chat_allowed("專案區") is True
        assert _chat_allowed("專案區討論") is False
        # glob filter: prefix / contains
        monkeypatch.setenv(
            "TEAMS_ASSIST_ALLOW_CHATS", "加密分持*, *會議*, 二科一股*",
        )
        assert _chat_allowed("加密分持需求討論會議") is True
        assert _chat_allowed("公雲技術雙週會 - 科內追蹤") is False
        assert _chat_allowed("跨科會議追蹤") is True
        assert _chat_allowed("二科一股資安討論群") is True
        assert _chat_allowed("hicloud 雲二科一股") is False
        # case-sensitive glob
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "Hicloud*")
        assert _chat_allowed("hicloud 討論") is False
        assert _chat_allowed("Hicloud 討論") is True
        # '*' alone → all
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "*")
        assert _chat_allowed("任何聊天") is True
        # None / empty title → fail-closed
        assert _chat_allowed(None) is False
        assert _chat_allowed("") is False

# ─── per-chat sessions + fire history cap (v0.9.74.0+) ────────────


class TestPerChatSessions:
    def _live_uia(self, monkeypatch, window, ok=True):
        import src.teams_assist_tools as tat

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "live")
        monkeypatch.setattr(
            tat, "_uia_ops",
            lambda action, *a: {"ok": ok, "action": action,
                                "window": window, "text": "x"},
        )

    def test_chat_slug(self, ta_home):
        from src.teams_assist_tools import _chat_slug

        assert _chat_slug("a/b\\c:d*?") == "a b c d"
        assert _chat_slug("") == ""
        assert len(_chat_slug("x" * 100)) == 40
        assert "酷英網" in _chat_slug("聊天 | (VMware) 酷英網 x CHT")

    def test_active_chat_title_from_window(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _active_chat_title

        self._live_uia(
            monkeypatch,
            "聊天 | (VMware) 酷英網 x CHT | Chunghwa Telecom | "
            "Microsoft Teams",
        )
        assert _active_chat_title() == "(VMware) 酷英網 x CHT"

    def test_active_chat_title_fails_gracefully(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import _active_chat_title

        self._live_uia(monkeypatch, "Microsoft Teams", ok=False)
        assert _active_chat_title() is None
        self._live_uia(monkeypatch, "設定 | Microsoft Teams")
        assert _active_chat_title() is None
        # mock mode never touches UIA
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "mock")

        def _boom(*a, **k):
            raise AssertionError("no UIA in mock")

        monkeypatch.setattr(
            "src.teams_assist_tools._uia_ops", _boom,
        )
        assert _active_chat_title() is None

    def test_fire_history_limit_resolver(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _fire_history_limit

        assert _fire_history_limit() == -1  # default = full
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_FIRE_HISTORY", "2")
        assert _fire_history_limit() == 2
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_FIRE_HISTORY", "0")
        assert _fire_history_limit() == 0
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_FIRE_HISTORY", "x")
        assert _fire_history_limit() == -1

    def test_fire_uses_per_chat_session(self, ta_home, monkeypatch):
        from src.agent.session import SessionManager
        from src.teams_assist_tools import make_teams_assist_fire

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS",
                           "(VMware) 酷英網 x CHT")
        self._live_uia(
            monkeypatch, "聊天 | (VMware) 酷英網 x CHT | Teams",
        )
        agent = _StubAgent(["ok"])
        out = make_teams_assist_fire(lambda: agent)({"source": "x"})
        assert out["ok"] is True
        assert out["chat"] == "(VMware) 酷英網 x CHT"
        sid = out["session_id"]
        assert sid.startswith("teamsassist-") and sid != \
            "teamsassist-watch"
        assert "(VMware) 酷英網 x CHT" in sid
        sess = SessionManager().load(sid)
        assert sess is not None
        assert sess.title == "[teams-assist] (VMware) 酷英網 x CHT"

    def test_fire_falls_back_to_master_session(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import (
            SESSION_ID, make_teams_assist_fire,
        )

        self._live_uia(monkeypatch, "Microsoft Teams", ok=False)
        agent = _StubAgent(["ok"])
        out = make_teams_assist_fire(lambda: agent)({})
        assert out["ok"] is True
        assert out["session_id"] == SESSION_ID
        assert out["chat"] is None

    def test_fire_unallowed_active_chat_falls_back_to_master(
        self, ta_home, monkeypatch,
    ):
        """v0.9.77.0: a background-chat fire (the active chat is
        unrelated) must NOT file into that chat's session."""
        from src.agent.session import SessionManager
        from src.teams_assist_tools import (
            SESSION_ID, make_teams_assist_fire,
        )

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "黃宜軒")
        self._live_uia(
            monkeypatch, "聊天 | 智凱與中華小夥伴 <3 | Teams",
        )
        agent = _StubAgent(["(白名單外 — 未處理)"])
        out = make_teams_assist_fire(lambda: agent)({})
        assert out["session_id"] == SESSION_ID
        # the unrelated chat's session was never created
        assert SessionManager().load(
            "teamsassist-智凱與中華小夥伴 3",
        ) is None

    def test_fire_separates_sessions_per_chat(
        self, ta_home, monkeypatch,
    ):
        from src.agent.session import SessionManager
        import src.teams_assist_tools as tat
        from src.teams_assist_tools import make_teams_assist_fire

        windows = iter([
            "聊天 | A 討論 | Teams",
            "聊天 | B 討論 | Teams",
        ])
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "live")
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "*")
        monkeypatch.setattr(
            tat, "_uia_ops",
            lambda action, *a: {"ok": True, "window": next(windows)},
        )
        agent = _StubAgent(["ok", "ok"])
        out1 = make_teams_assist_fire(lambda: agent)({})
        out2 = make_teams_assist_fire(lambda: agent)({})
        assert out1["session_id"] != out2["session_id"]
        sm = SessionManager()
        assert sm.load(out1["session_id"]) is not None
        assert sm.load(out2["session_id"]) is not None

    def test_fire_history_cap(self, ta_home, monkeypatch):
        from src.agent.session import Session, SessionManager
        from src.teams_assist_tools import make_teams_assist_fire

        # pre-populate the per-chat session with 6 messages
        sid = "teamsassist-capchat"
        sm = SessionManager()
        sess = Session(sid=sid, title="[teams-assist] capchat")
        for i in range(6):
            sess.messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"m{i}", "ts": f"2026-01-01T00:0{i}",
            })
        sm.save(sess)

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "capchat")
        self._live_uia(monkeypatch, "聊天 | capchat | Teams")
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_FIRE_HISTORY", "2")
        seen = {}

        class _HistAgent(_StubAgent):
            def chat_turn(self, question, history=None, use_tools=True):
                seen["history"] = list(history or [])
                return super().chat_turn(
                    question, history=history, use_tools=use_tools,
                )

        agent = _HistAgent(["ok"])
        out = make_teams_assist_fire(lambda: agent)({})
        assert out["session_id"] == sid
        # 6 pre-existing + 1 framed user = 7 → cap 2 keeps the last 2
        assert len(seen["history"]) == 2
        assert [m["content"] for m in seen["history"]] == ["m4", "m5"]
        # session file still keeps everything
        assert len(sm.load(sid).messages) == 8

    def test_fire_history_zero_independent(self, ta_home, monkeypatch):
        from src.teams_assist_tools import make_teams_assist_fire

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "indep")
        self._live_uia(monkeypatch, "聊天 | indep | Teams")
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_FIRE_HISTORY", "0")
        seen = {}

        class _HistAgent(_StubAgent):
            def chat_turn(self, question, history=None, use_tools=True):
                seen["history"] = list(history or [])
                return super().chat_turn(
                    question, history=history, use_tools=use_tools,
                )

        agent = _HistAgent(["ok"])
        out = make_teams_assist_fire(lambda: agent)({})
        assert out["session_id"] == "teamsassist-indep"
        assert seen["history"] == []

# ─── list chats (v0.9.76.0+) ──────────────────────────────────────


class TestListChats:
    def _uia(self, monkeypatch, text, ok=True):
        import src.teams_assist_tools as tat

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "live")
        monkeypatch.setattr(
            tat, "_uia_ops",
            lambda action, *a: {"ok": ok, "action": action,
                                "window": "聊天 | X | Teams",
                                "text": text},
        )

    _SAMPLE = (
        "聊天\n其他清單選項\n顯示篩選文字方塊 (Ctrl+Shift+F)\n"
        "新訊息 (Ctrl+N)\n頻道 (Ctrl+Alt+A)\n聊天 (Ctrl+Alt+C)\n"
        "快速檢視\n提及\n探索\n草稿\n已儲存\n我的最愛\n￼\n"
        "蔡其宏 (您)\n柯智凱\n加密分持需求討論會議\n"
        "(VMware) 酷英網 x CHT\n￼\n董君樸\n查看更多\n"
        "Teams 和頻道\n調整 左 面板大小\n9/3 下午 05:41\n"
        "蔡其宏\n訊息內容不該被當成標題這句話很長超過限制.........."
    )

    def test_uia_chat_titles_parses_list(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _uia_chat_titles

        self._uia(monkeypatch, self._SAMPLE)
        titles = _uia_chat_titles()
        assert "柯智凱" in titles
        assert "加密分持需求討論會議" in titles
        assert "(VMware) 酷英網 x CHT" in titles
        # chrome / noise / message-pane excluded
        assert "蔡其宏 (您)" not in titles
        assert "聊天" not in titles
        assert "查看更多" not in titles
        assert "9/3 下午 05:41" not in titles
        assert len(titles) == 4

    def test_uia_chat_titles_mock_and_failure(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import _uia_chat_titles

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "mock")

        def _boom(*a, **k):
            raise AssertionError("no UIA in mock")

        monkeypatch.setattr(
            "src.teams_assist_tools._uia_ops", _boom,
        )
        assert _uia_chat_titles() == []
        self._uia(monkeypatch, self._SAMPLE, ok=False)
        assert _uia_chat_titles() == []

    def test_list_chats_marks_allowed_and_engaged(
        self, ta_home, monkeypatch,
    ):
        from src.agent.session import Session, SessionManager
        from src.mcp_client.host import TeamsAssistLocalHost

        # one engaged per-chat session
        sm = SessionManager()
        sess = Session(
            sid="teamsassist-柯智凱", title="[teams-assist] 柯智凱",
        )
        sess.messages.append({"role": "user", "content": "x"})
        sm.save(sess)

        self._uia(monkeypatch, self._SAMPLE)
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "加密分持*, 柯智凱")
        r = TeamsAssistLocalHost()._list_chats({})
        assert r["ok"] is True
        assert r["count"] == 4
        by_title = {c["title"]: c for c in r["chats"]}
        assert by_title["柯智凱"]["allowed"] is True
        assert by_title["柯智凱"]["engaged"] is True
        assert by_title["加密分持需求討論會議"]["allowed"] is True
        assert by_title["加密分持需求討論會議"]["engaged"] is False
        assert by_title["(VMware) 酷英網 x CHT"]["allowed"] is False
        assert by_title["董君樸"]["engaged"] is False
        assert r["engaged_sessions"] and r["engaged_sessions"][0][
            "sid"] == "teamsassist-柯智凱"
        assert r["active_chat"] == "X"
        assert r["active_chat_allowed"] is False

    def test_list_chats_summary_table(self, ta_home, monkeypatch):
        from src.agent.session import Session, SessionManager
        from src.mcp_client.host import TeamsAssistLocalHost

        SessionManager().save(Session(
            sid="teamsassist-柯智凱", title="[teams-assist] 柯智凱",
        ))
        self._uia(monkeypatch, self._SAMPLE)
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "柯智凱")
        s = TeamsAssistLocalHost()._list_chats({})["summary"]
        assert "watch：" in s and "watch_mode=" in s
        assert "過濾條件：柯智凱" in s
        assert "• 柯智凱\u3000✅ 符合過濾\u3000👁 已watch" in s
        assert "• 董君樸\u3000❌ 不符合過濾\u3000— 未watch" in s
        assert "已 watch 的 session（1 個）" in s

    def test_list_chats_mock_still_reports_sessions(
        self, ta_home, monkeypatch,
    ):
        from src.agent.session import Session, SessionManager
        from src.mcp_client.host import TeamsAssistLocalHost

        SessionManager().save(Session(
            sid="teamsassist-某群", title="[teams-assist] 某群",
        ))
        r = TeamsAssistLocalHost()._list_chats({})
        assert r["ok"] is True
        assert r["chats"] == []  # mock = no UIA list
        assert r["engaged_sessions"][0]["sid"] == "teamsassist-某群"

    def test_system_prompt_mentions_list_chats(self, ta_home):
        from src.agent.agent import Agent

        prompt = Agent()._system_prompt(tools_enabled=True)
        assert "teams_assist_list_chats" in prompt
        legacy = Agent()._system_prompt(tools_enabled=False)
        assert "teams_assist_list_chats" not in legacy

    def test_list_chats_summary_deterministic_postprocess(
        self, ta_home,
    ):
        """v0.9.76.3/79.0: when the LLM compresses the list into a
        one-liner, the agent appends the deterministic summary; when
        the table is present, the answer is untouched."""
        from src.agent.agent import Agent

        a = Agent()
        a._last_list_chats_summary = (
            "watch：執行中（watch_mode=uia）\n"
            "• 柯智凱\u3000✅ 符合過濾\u3000👁 已watch"
        )
        # LLM one-liner WITHOUT the table → summary appended
        out = a._ensure_list_chats_summary("目前有 46 個聊天。")
        assert "目前有 46 個聊天。" in out
        assert "watch：執行中" in out and "柯智凱" in out
        # LLM already echoed the table → untouched
        out2 = a._ensure_list_chats_summary(
            "watch：執行中\n• 柯智凱 ✅ 符合過濾 👁 已watch"
        )
        assert out2 == "watch：執行中\n• 柯智凱 ✅ 符合過濾 👁 已watch"
        # a bare "watch：" one-liner does NOT count as the table
        out3 = a._ensure_list_chats_summary("watch：執行中。")
        assert "柯智凱" in out3
        # no list_chats this turn → untouched
        a._last_list_chats_summary = None
        assert a._ensure_list_chats_summary("你好") == "你好"

    def test_list_chats_echo_runs_tool_deterministically(
        self, ta_home,
    ):
        """v0.9.79.0: the model ECHOES the tool name as text instead
        of dispatching (glm-flash fake-call quirk) → the agent runs
        the tool itself and appends the summary."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text=json.dumps({
                "ok": True,
                "summary": "watch：執行中\n• A　✅ 符合過濾　👁 已watch",
            }),
            is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        out = a._ensure_list_chats_summary("teams_assist_list_chats")
        assert mgr.call.called is True
        assert mgr.call.call_args[0][0] == "teams_assist_list_chats"
        assert "watch：執行中" in out and "A" in out
        # error result → no append, answer unchanged
        mgr.call.reset_mock()
        mgr.call.return_value = McpCallResult(
            text="boom", is_error=True,
        )
        out2 = a._ensure_list_chats_summary("teams_assist_list_chats")
        assert mgr.call.called is True
        assert out2 == "teams_assist_list_chats"

    def test_echo_safe_tools_config_and_write_exclusion(
        self, ta_home,
    ):
        """v0.9.79.1: echoed config/start_watch are run with parsed
        args; reply/send_uia echoes are NEVER auto-run."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text='{"ok": true}', is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        out = a._ensure_list_chats_summary(
            'teams_assist_config(allow_chats=["*"], watch_mode="uia")'
        )
        assert mgr.call.called is True
        assert mgr.call.call_args[0][0] == "teams_assist_config"
        assert mgr.call.call_args[0][1] == {
            "allow_chats": ["*"], "watch_mode": "uia",
        }
        assert "系統代為執行 teams_assist_config" in out
        # write tools are excluded — never auto-run
        mgr.call.reset_mock()
        out2 = a._ensure_list_chats_summary(
            'teams_assist_reply(text="x", chat="黃宜軒", confirm=true) '
            'teams_assist_send_uia(text="y", confirm=true)'
        )
        assert mgr.call.called is False
        assert out2.strip().startswith("teams_assist_reply")
        # an ALREADY dispatched tool is not re-run on echo
        mgr.call.reset_mock()
        a.last_tool_dispatches = ["teams_assist_config"]
        out3 = a._ensure_list_chats_summary(
            'teams_assist_config(allow_chats=["*"])'
        )
        assert mgr.call.called is False

    def test_generic_echo_get_current_time_surfaces_result(
        self, ta_home,
    ):
        """v0.9.82.0 — glm-flash echoed get_current_time(args) as text
        instead of dispatching; the agent runs it deterministically and
        surfaces the RESULT so the user actually gets the time."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text='{"local": "2026-09-19T01:04:00+08:00"}',
            is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        out = a._ensure_list_chats_summary(
            'get_current_time({"format": "datetime", '
            '"timezone": "Asia/Taipei"})'
        )
        assert mgr.call.called is True
        assert mgr.call.call_args[0][0] == "get_current_time"
        # hallucinated args are parsed tolerantly and passed through
        assert mgr.call.call_args[0][1].get("format") == "datetime"
        assert "系統代為執行 get_current_time" in out
        # the RESULT is surfaced so the user sees the actual time
        assert "2026-09-19T01:04:00" in out
        # a non-safe lookalike is NEVER auto-run
        mgr.call.reset_mock()
        out2 = a._ensure_list_chats_summary(
            'send_telegram_message(chat_id=1, text="hi")'
        )
        assert mgr.call.called is False
        assert "send_telegram_message" in out2

    def test_google_calendar_echo_and_write_exclusion(self, ta_home):
        """v0.9.82.0 — google-calendar READ-ONLY tools are echo-safe
        (schedule answers must come from real fetches); the WRITE
        tools (create/update/delete-event, respond-to-event,
        manage-accounts) are NEVER auto-run."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text='{"events": ["下星期行程X"]}', is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        out = a._ensure_list_chats_summary(
            'search-events({"query": "", "timeMin": "2026-09-21", '
            '"timeMax": "2026-09-28"})'
        )
        assert mgr.call.called is True
        assert mgr.call.call_args[0][0] == "search-events"
        assert "系統代為執行 search-events" in out
        assert "下星期行程X" in out  # result surfaced
        # write tools excluded
        mgr.call.reset_mock()
        out2 = a._ensure_list_chats_summary(
            'create-event(summary="x", calendarId="primary") '
            'delete-event(eventId="1")'
        )
        assert mgr.call.called is False

    def test_web_search_echo_runs_and_surfaces_result(self, ta_home):
        """v0.9.83.2 — live E2E 2026-09-21 16:52: the model ECHOED
        `web_search({"query": "中華電信董事長 2026 現任"})` as text with
        ZERO dispatch and answered from a WEEK-OLD memory — the echo
        guard must really run web_search (read-only) and surface the
        fresh result; an already-dispatched web_search is not re-run."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text='- 中華電信董事長：郭水義（2026-09-21 報導）', is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        out = a._ensure_list_chats_summary(
            'web_search({"query": "中華電信董事長 2026 現任"})'
        )
        assert mgr.call.called is True
        assert mgr.call.call_args[0][0] == "web_search"
        assert mgr.call.call_args[0][1] == {
            "query": "中華電信董事長 2026 現任",
        }
        assert "系統代為執行 web_search" in out
        assert "郭水義" in out  # fresh result surfaced
        # already dispatched this turn → not re-run
        mgr.call.reset_mock()
        a.last_tool_dispatches = ["web_search"]
        out2 = a._ensure_list_chats_summary(
            'web_search({"query": "x"})'
        )
        assert mgr.call.called is False

    def test_web_fetch_echo_runs(self, ta_home):
        """v0.9.83.2 — web_fetch echoes are read-only too: parsed args
        (url first, bare-string tolerated) and really fetched."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text='<html>… 郭水義就任 …</html>', is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        out = a._ensure_list_chats_summary(
            'web_fetch({"url": "https://www.cht.com.tw/about"})'
        )
        assert mgr.call.called is True
        assert mgr.call.call_args[0][0] == "web_fetch"
        assert mgr.call.call_args[0][1] == {
            "url": "https://www.cht.com.tw/about",
        }
        assert "系統代為執行 web_fetch" in out

    def test_glued_search_documents_echo_runs(self, ta_home):
        """v0.9.83.3 — live E2E 17:02: `search_documentsq中華電信 董事長`
        echoed GLUED (tool name + arg key, no parens). The guard strips
        the latin arg-key prefix and really runs the corpus search."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text='[Document 1] 中華電信 2026 年報：董事長郭水義', is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        out = a._ensure_list_chats_summary(
            "search_documentsq中華電信 董事長"
        )
        assert mgr.call.called is True
        assert mgr.call.call_args[0][0] == "search_documents"
        assert mgr.call.call_args[0][1] == {"query": "中華電信 董事長"}
        assert "系統代為執行 search_documents" in out
        assert "郭水義" in out

    def test_bare_get_current_time_echo_runs(self, ta_home):
        """v0.9.83.3 — live E2E 17:03: the answer was ONLY the literal
        string `get_current_time` (↓7 tok) — a bare tool-name echo is
        run with {} and the result surfaced."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text='{"local": "2026-09-21T17:03:00+08:00"}', is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        out = a._ensure_list_chats_summary("get_current_time")
        assert mgr.call.called is True
        assert mgr.call.call_args[0][0] == "get_current_time"
        assert mgr.call.call_args[0][1] == {}
        assert "2026-09-21T17:03:00" in out

    def test_glued_query_falls_back_to_turn_question(self, ta_home):
        """v0.9.83.3 — a bare search echo with no usable tail derives
        the query from the stashed turn question."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text="- 搜尋結果：…", is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        a._last_turn_question = "中華電信董事長是誰"
        out = a._ensure_list_chats_summary("web_search")
        assert mgr.call.called is True
        assert mgr.call.call_args[0][0] == "web_search"
        assert mgr.call.call_args[0][1] == {"query": "中華電信董事長是誰"}
        assert "系統代為執行 web_search" in out

    def test_multi_glued_weather_shape(self, ta_home):
        """v0.9.83.4 — live E2E 17:21: TWO tools glued into one line:
        `get_current_timetimeframetodayweb_searchmax_results5query台北
        今天 天氣 預報 中央氣象署` — get_current_time runs {} and
        web_search extracts the CJK value after the glued key run."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text="台北 多雲 28 度", is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        a._last_turn_question = "今天的天氣"
        out = a._ensure_list_chats_summary(
            "get_current_timetimeframetoday"
            "web_searchmax_results5query台北 今天 天氣 預報 中央氣象署"
        )
        calls = {c.args[0]: c.args[1] for c in mgr.call.call_args_list}
        assert calls["get_current_time"] == {}
        assert calls["web_search"] == {
            "query": "台北 今天 天氣 預報 中央氣象署",
        }
        assert "系統代為執行 web_search" in out

    def test_web_fetch_glued_url_not_eaten(self, ta_home):
        """v0.9.83.4 — the greedy latin strip must not eat the https://
        of a glued web_fetch URL."""
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text="…", is_error=False,
        )
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        a._ensure_list_chats_summary(
            'web_fetchurlhttps://www.cht.com.tw/about')
        calls = {c.args[0]: c.args[1] for c in mgr.call.call_args_list}
        assert calls["web_fetch"] == {
            "url": "https://www.cht.com.tw/about",
        }

    def test_prose_mention_never_autoruns(self, ta_home):
        """v0.9.83.3 — a tool name in prose (followed by a space/CJK)
        must NOT trigger a deterministic run."""
        from src.agent.agent import Agent

        a = Agent()
        mgr = MagicMock()
        a._mcp_manager = mgr
        a._last_list_chats_summary = None
        a.last_tool_dispatches = []
        out = a._ensure_list_chats_summary("你可以用 web_search 查一下")
        assert mgr.call.called is False
        assert out == "你可以用 web_search 查一下"

    def test_system_prompt_google_calendar_first(self, ta_home):
        from src.agent.agent import Agent

        prompt = Agent()._system_prompt(tools_enabled=True)
        assert "Google Calendar FIRST" in prompt
        assert "search-events" in prompt and "list-events" in prompt
        legacy = Agent()._system_prompt(tools_enabled=False)
        assert "Google Calendar FIRST" not in legacy

    def test_list_chats_summary_stashed_in_run_tool_call(
        self, ta_home,
    ):
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text=json.dumps({
                "ok": True,
                "summary": "watch：執行中（watch_mode=uia）\n• A　✅",
            }),
            is_error=False,
        )
        a._mcp_manager = mgr
        a._run_tool_call(self._tool_call("teams_assist_list_chats"))
        assert a._last_list_chats_summary and "watch：" in \
            a._last_list_chats_summary
        # error result is NOT stashed
        mgr.call.return_value = McpCallResult(
            text="boom", is_error=True,
        )
        a._run_tool_call(self._tool_call("teams_assist_list_chats"))
        assert a._last_list_chats_summary and "watch：" in \
            a._last_list_chats_summary  # unchanged (still the ok one)

    @staticmethod
    def _tool_call(name, args="{}"):
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = name
        tc.function.arguments = args
        return tc

# ─── drafts queue (v0.9.78.0+) ────────────────────────────────────


class TestDraftsQueue:
    def test_drafts_lists_and_orders(self, ta_home, monkeypatch):
        from src.agent.session import Session, SessionManager
        from src.mcp_client.host import TeamsAssistLocalHost

        # seed: a draft recorded via the normal reply gate
        SessionManager().save(Session(sid="teamsassist-x"))
        h = TeamsAssistLocalHost()
        h._reply({"text": "草稿全文內容", "confirm": False,
                  "chat": "黃宜軒"})
        h._reply({"text": "第二筆", "confirm": False, "chat": "A 群"})
        r = h._drafts({})
        assert r["ok"] is True and r["count"] == 2
        assert r["drafts"][0]["chat"] == "A 群"  # newest first
        assert r["drafts"][1]["chat"] == "黃宜軒"
        assert r["drafts"][1]["text"] == "草稿全文內容"
        assert "草稿佇列（2 筆" in r["summary"]
        assert "黃宜軒" in r["summary"] and "草稿全文內容" in r["summary"]
        assert "✏ 草稿（未送出）" in r["summary"]

    def test_drafts_empty(self, ta_home):
        from src.mcp_client.host import TeamsAssistLocalHost

        r = TeamsAssistLocalHost()._drafts({})
        assert r["ok"] is True and r["count"] == 0
        assert r["drafts"] == []
        assert "（空）" in r["summary"]

    def test_end_task_carries_answer_chars(self, ta_home, monkeypatch):
        """v0.9.78.0: the fire must stamp answer_chars (was None)."""
        import src.teams_assist_tools as tat

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "capchat")
        tat2 = tat
        saved = tat2._active_chat_title
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "mock")
        # mock mode → _active_chat_title returns None → master session
        agent = _StubAgent(["ok"])
        out = tat2.make_teams_assist_fire(lambda: agent)({})
        assert out["answer"] == "ok"
        from src.agent.task_runner import list_tasks

        recs = [
            r for r in list_tasks()["tasks"] if r["source"] ==
            "teams-assist"
        ]
        assert recs and recs[0]["answer_chars"] == 2
        assert saved is tat2._active_chat_title

    def test_system_prompt_mentions_drafts(self, ta_home):
        from src.agent.agent import Agent

        prompt = Agent()._system_prompt(tools_enabled=True)
        assert "teams_assist_drafts" in prompt
        legacy = Agent()._system_prompt(tools_enabled=False)
        assert "teams_assist_drafts" not in legacy

# ─── fire engine ──────────────────────────────────────────────────


class _StubAgent:
    def __init__(self, answers, dispatches=None):
        self._answers = list(answers)
        self.calls: list[str] = []
        self.last_tool_dispatches = list(dispatches or [])

    def chat_turn(self, question, history=None, use_tools=True):
        self.calls.append(question)
        return (self._answers.pop(0), "", [])


class TestWatchGate:
    """v0.9.84.0 — the Jev watch gate (plan §5.3): deterministic UIA
    read + ONE noul decision before an LLM turn; every failure fails
    OPEN to the LLM turn (plan §7)."""

    def _live(self, monkeypatch, jev=None,
              uia_text="夠長的聊天內容，包含多則對話訊息與時間戳。",
              gate=True):
        import src.teams_assist_tools as tat

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "live")
        if gate:
            monkeypatch.setenv("QDRANT_WATCH_GATE", "on")
        else:
            monkeypatch.delenv("QDRANT_WATCH_GATE", raising=False)
        monkeypatch.setattr(tat, "_default_uia_read",
                            lambda: uia_text)
        monkeypatch.setattr(tat, "_active_chat_title", lambda: "聊天A")
        seen = {"jev": 0, "factory": 0}

        def _jev(instr, state, **k):
            seen["jev"] += 1
            seen["state_len"] = len(str(state or ""))
            return (0.9, {"elapsed_s": 0.2}) if jev is None else jev

        monkeypatch.setattr("src.agent.rerank.jev_noul", _jev)

        class _Agent:
            def __init__(self):
                self.calls: list[str] = []
                self.last_tool_dispatches: list = []

            def chat_turn(self, question, **k):
                self.calls.append(question)
                return ("(無新訊息)", "", [])

        agent = _Agent()

        def _factory():
            seen["factory"] += 1
            return agent

        from src.teams_assist_tools import make_teams_assist_fire
        return make_teams_assist_fire(_factory), agent, seen

    def test_gate_off_runs_llm(self, ta_home, monkeypatch):
        fire, agent, seen = self._live(monkeypatch, gate=False)
        out = fire({})
        assert out["ok"] is True and "gate" not in out
        assert seen["jev"] == 0 and seen["factory"] == 1
        assert len(agent.calls) == 1

    def test_gate_skips_when_no_reply_needed(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _load_state

        fire, agent, seen = self._live(monkeypatch)  # jev → 0.9
        out = fire({})
        assert out["ok"] is True
        assert out["fired"] is False
        assert out["gate"] == "skipped"
        assert out["p"] == 0.9
        assert seen["jev"] == 1 and seen["factory"] == 0
        assert agent.calls == []
        state = _load_state()
        assert state["gate"]["skipped"] == 1
        assert state["gate"]["last_p"] == 0.9
        kinds = [a["kind"] for a in state["audit"]]
        assert "watch-gate-skip" in kinds

    def test_gate_passes_when_reply_needed(self, ta_home, monkeypatch):
        fire, agent, seen = self._live(
            monkeypatch, jev=(0.2, {"elapsed_s": 0.2}))
        out = fire({})
        assert out["ok"] is True and "gate" not in out
        assert seen["jev"] == 1 and seen["factory"] == 1
        assert len(agent.calls) == 1

    def test_gate_uia_failure_runs_llm(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        monkeypatch.setattr(tat, "_default_uia_read",
                            lambda: (_ for _ in ()).throw(
                                RuntimeError("no-teams-window")))
        fire, agent, seen = self._live(monkeypatch, uia_text="x")
        out = fire({})
        assert out["ok"] is True and "gate" not in out
        assert seen["jev"] == 0 and len(agent.calls) == 1

    def test_gate_jev_failure_runs_llm(self, ta_home, monkeypatch):
        fire, agent, seen = self._live(
            monkeypatch, jev=(None, {"error": "HTTP 402: credits"}))
        out = fire({})
        assert out["ok"] is True and "gate" not in out
        assert len(agent.calls) == 1

    def test_gate_mock_mode_skipped(self, ta_home, monkeypatch):
        monkeypatch.setenv("QDRANT_WATCH_GATE", "on")  # mode stays mock
        fire, agent, seen = self._live(monkeypatch)
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "mock")
        out = fire({})
        assert out["ok"] is True and "gate" not in out
        assert seen["jev"] == 0 and len(agent.calls) == 1

    def test_skip_prob_resolver(self, monkeypatch):
        from src.teams_assist_tools import _watch_gate_skip_prob

        assert _watch_gate_skip_prob() == 0.7
        monkeypatch.setenv("QDRANT_WATCH_GATE_SKIP_PROB", "0.85")
        assert _watch_gate_skip_prob() == 0.85
        monkeypatch.setenv("QDRANT_WATCH_GATE_SKIP_PROB", "abc")
        assert _watch_gate_skip_prob() == 0.7
        monkeypatch.setenv("QDRANT_WATCH_GATE_SKIP_PROB", "3")
        assert _watch_gate_skip_prob() == 0.7
        monkeypatch.setenv("QDRANT_WATCH_GATE_SKIP_PROB", "1")
        assert _watch_gate_skip_prob() == 1.0

    def test_status_exposes_gate(self, ta_home, monkeypatch):
        from src.mcp_client.host import TeamsAssistLocalHost
        from src.teams_assist_tools import _load_state

        self._live(monkeypatch)
        from src.teams_assist_tools import make_teams_assist_fire
        make_teams_assist_fire(lambda: _StubAgent(["(無新訊息)"]))({})
        state = _load_state()
        assert state["gate"]["skipped"] == 1


class TestFireEngine:
    def test_fire_runs_turn_and_persists_session(self, ta_home):
        from src.agent.session import SessionManager
        from src.teams_assist_tools import make_teams_assist_fire

        agent = _StubAgent(["(無新訊息)"])
        fire = make_teams_assist_fire(lambda: agent)
        out = fire({"source": "x"})
        assert out["ok"] is True
        assert out["answer"] == "(無新訊息)"
        assert out["session_id"] == "teamsassist-watch"
        assert out["mode"] == "mock"
        assert out["auto"] is False
        assert len(agent.calls) == 1
        assert "teams_assist_check" in agent.calls[0]
        sess = SessionManager().load("teamsassist-watch")
        assert sess is not None
        assert sess.messages[-1]["content"] == "(無新訊息)"

    def test_auto_flag_recorded_when_auto_on(self, ta_home, monkeypatch):
        from src.teams_assist_tools import (
            _load_state, make_teams_assist_fire,
        )

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_AUTO", "on")
        agent = _StubAgent(["ok"])
        fire = make_teams_assist_fire(lambda: agent)
        out = fire(None)
        assert out["auto"] is True
        assert _load_state()["auto_send_allowed"] is True

    def test_fabrication_guard_corrective_turn(self, ta_home):
        from src.agent.session import SessionManager
        from src.teams_assist_tools import make_teams_assist_fire

        agent = _StubAgent(
            ["已回覆對方了！"],  # claims delivery, no dispatch
        )
        # second answer for the corrective turn
        agent._answers.append("已透過 windows-mcp 送出並註冊")
        fire = make_teams_assist_fire(lambda: agent)
        out = fire(None)
        assert out["ok"] is True
        assert len(agent.calls) == 2
        assert "糾正回合" in agent.calls[1]
        assert out["answer"] == "已透過 windows-mcp 送出並註冊"
        sess = SessionManager().load("teamsassist-watch")
        flags = [m.get("fabrication_guarded") for m in sess.messages]
        assert True in flags

    def test_real_dispatch_no_guard(self, ta_home):
        from src.teams_assist_tools import make_teams_assist_fire

        agent = _StubAgent(
            ["已回覆並註冊"], dispatches=["teams_assist_reply"],
        )
        fire = make_teams_assist_fire(lambda: agent)
        out = fire(None)
        assert len(agent.calls) == 1
        assert out["answer"] == "已回覆並註冊"

    def test_chat_turn_failure_returns_error(self, ta_home):
        from src.teams_assist_tools import make_teams_assist_fire

        class _BoomAgent:
            last_tool_dispatches: list = []

            def chat_turn(self, *a, **k):
                raise RuntimeError("llm down")

        fire = make_teams_assist_fire(lambda: _BoomAgent())
        out = fire(None)
        assert out["ok"] is False
        assert "llm down" in out["error"]

    def test_corrective_retry_failure_keeps_original(self, ta_home):
        from src.teams_assist_tools import make_teams_assist_fire

        class _HalfAgent:
            def __init__(self):
                self.calls = []
                self.last_tool_dispatches = []

            def chat_turn(self, question, **k):
                self.calls.append(question)
                if len(self.calls) == 1:
                    return ("已送出了", "", [])
                raise RuntimeError("retry dead")

        agent = _HalfAgent()
        fire = make_teams_assist_fire(lambda: agent)
        out = fire(None)
        assert out["answer"] == "已送出了"
        assert len(agent.calls) == 2

# ─── TeamsAssistLocalHost tools ───────────────────────────────────


class TestHost:
    def _host(self):
        from src.mcp_client.host import TeamsAssistLocalHost

        return TeamsAssistLocalHost()

    def _tools(self, host):
        return {t.name: t for t in host.list_tools()}

    def test_tool_surface_ten_tools(self):
        host = self._host()
        tools = self._tools(host)
        expected = {
            "teams_assist_status", "teams_assist_config",
            "teams_assist_list_chats", "teams_assist_drafts",
            "teams_assist_check",
            "teams_assist_start_watch", "teams_assist_stop_watch",
            "teams_assist_reply", "teams_assist_pause",
            "teams_assist_resume",
            "teams_assist_send_uia", "teams_assist_read_uia",
        }
        assert set(tools) == expected
        assert all(t.server_name == "teams-assist"
                   for t in tools.values())
        assert len(tools) == 12

    def test_status_shape(self, ta_home):
        out = self._host()._status({})
        assert out["ok"] is True
        assert out["mode"] == "mock"
        assert out["auto"] is False
        assert out["allow_chats"] == []
        assert out["watch"]["running"] is False
        assert out["recent_audit"] == []

    def test_reply_requires_text(self, ta_home):
        out = self._host()._reply({"confirm": False})
        assert "error" in out

    def test_reply_draft_then_gate_then_manual_then_auto(
        self, ta_home,
    ):
        from src.teams_assist_tools import _load_state

        h = self._host()
        out = h._reply({"text": "草稿內容", "confirm": False,
                        "chat": "專案區"})
        assert out["recorded"] == "draft"
        # AUTO off + no manual → refused
        out = h._reply({"text": "x", "confirm": True})
        assert "AUTO" in out["error"]
        # manual bypass
        out = h._reply({"text": "x", "confirm": True, "manual": True})
        assert out["recorded"] == "sent"
        # auto_send_allowed state (set by the fire engine)
        from src.teams_assist_tools import _save_state

        st = _load_state()
        st["auto_send_allowed"] = True
        _save_state(st)
        out = h._reply({"text": "y", "confirm": True})
        assert out["recorded"] == "sent"
        kinds = [r["kind"] for r in _load_state()["audit"]]
        assert kinds == ["draft", "sent", "sent"]

    def test_pause_resume_roundtrip(self, ta_home):
        from src.teams_assist_tools import _load_state

        h = self._host()
        assert h._pause({})["paused"] is True
        assert _load_state()["paused"] is True
        assert h._resume({})["paused"] is False
        assert _load_state()["paused"] is False

    def test_start_watch_fail_closed_and_success(
        self, ta_home, monkeypatch,
    ):
        h = self._host()
        out = h._start_watch({})
        assert "error" in out
        assert "ALLOW_CHATS" in out["error"]
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區")
        out = h._start_watch({"interval_s": 10})
        assert out["started"] is True
        assert out["mode"] == "mock"
        # idempotent
        out = h._start_watch({})
        assert out["started"] is False
        # bad interval
        out = h._start_watch({"interval_s": "abc"})
        assert "error" in out
        h.stop_watch()

    def test_start_watch_interval_clamped(self, ta_home, monkeypatch):
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區")
        h = self._host()
        out = h._start_watch({"interval_s": 1})
        assert out["started"] is True
        assert out["interval_s"] == 10
        h.stop_watch()

    def test_check_mock_mode_no_path(self, ta_home):
        (ta_home / "teams_assist").mkdir(parents=True, exist_ok=True)
        (ta_home / "teams_assist" / "mock_screen.txt").write_text(
            "畫面內容", encoding="utf-8",
        )
        out = self._host()._check({})
        assert out["ok"] is True
        assert out["mode"] == "mock"
        assert out["path"] is None

    def test_check_live_mode_writes_sandbox(self, ta_home, monkeypatch,
                                            tmp_path):
        import src.teams_assist_tools as tat

        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "live")
        monkeypatch.setenv(
            "QDRANT_FILESYSTEM_WRITE_ROOT", str(sandbox),
        )
        monkeypatch.setattr(tat, "_default_grab", lambda: _png())
        out = self._host()._check({})
        assert out["ok"] is True and out["mode"] == "live"
        p = Path(out["path"])
        assert p.exists()
        assert sandbox / "teams_assist" in p.parents
        assert p.read_bytes()[:4] == b"\x89PNG"

    def test_check_live_grab_failure(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_MODE", "live")

        def boom():
            raise OSError("grab dead")

        monkeypatch.setattr(tat, "_default_grab", boom)
        out = self._host()._check({})
        assert "error" in out and "grab dead" in out["error"]

    def test_resume_watch_respects_persisted_running(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import _load_state, _save_state

        h = self._host()
        assert h.resume_watch() == 0  # never running
        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區")
        st = _load_state()
        st["watch"]["running"] = True
        _save_state(st)
        assert h.resume_watch() == 1
        assert h._ensure_watcher().is_running is True
        h.stop_watch()

# ─── manager + agent wiring ───────────────────────────────────────


class TestWiring:
    def test_from_config_registers_teams_assist(self, ta_home,
                                                tmp_path):
        from src.mcp_client.manager import McpManager

        cfg = tmp_path / "mcp.json"
        cfg.write_text("{}", encoding="utf-8")
        mgr = McpManager.from_config(
            str(cfg), include_local_hosts=True,
        )
        assert "teams_assist_status" in mgr._local
        assert mgr._teams_assist_local_host is not None
        assert mgr.failed.get("teams-assist") is None

    def test_set_teams_assist_fire_callback_pushes_down(self, ta_home):
        from src.mcp_client.host import TeamsAssistLocalHost
        from src.mcp_client.manager import McpManager

        mgr = McpManager()
        mgr._teams_assist_local_host = MagicMock(
            spec=TeamsAssistLocalHost,
        )
        cb = lambda p=None: {}  # noqa: E731
        mgr.set_teams_assist_fire_callback(cb)
        assert mgr.teams_assist_fire_callback is cb
        mgr._teams_assist_local_host.attach_fire.assert_called_once_with(
            cb,
        )

    def test_callback_before_host_none_is_safe(self, ta_home):
        from src.mcp_client.manager import McpManager

        mgr = McpManager()
        mgr.set_teams_assist_fire_callback(lambda p=None: {})
        assert mgr.teams_assist_fire_callback is not None

    def test_agent_mcp_manager_wires_teams_fire(self, ta_home,
                                                monkeypatch):
        from src.agent.agent import Agent
        from src.mcp_client.manager import McpManager

        fake_mgr = MagicMock(spec=McpManager)
        fake_mgr.failed = {}
        monkeypatch.setattr(
            McpManager, "from_config",
            classmethod(lambda cls, *a, **k: fake_mgr),
        )
        a = Agent()
        _ = a.mcp_manager
        fake_mgr.set_teams_assist_fire_callback.assert_called_once()
        # bound-method equality (a fresh bound object is created per
        # attribute access — `is` would be wrong here)
        assert (
            fake_mgr.set_teams_assist_fire_callback.call_args[0][0]
            == a._teams_assist_fire
        )

    def test_agent_wiring_e2e_local_hosts_only(
        self, ta_home, tmp_path, monkeypatch,
    ):
        """End-to-end WITHOUT the live MCP fleet: DEFAULT_MCP_CONFIG
        pointed at an absent file → local hosts only; the teams-assist
        fire callback + host ref are wired on the first mcp_manager
        access, and the pre-existing scheduler callback stays intact."""
        from src.agent.agent import Agent
        from src.mcp_client.host import TeamsAssistLocalHost

        monkeypatch.setattr(
            Agent, "DEFAULT_MCP_CONFIG",
            str(tmp_path / "absent.json"),
        )
        a = Agent()
        mgr = a.mcp_manager
        assert mgr._teams_assist_local_host is not None
        assert isinstance(
            mgr._teams_assist_local_host, TeamsAssistLocalHost,
        )
        assert (
            mgr.teams_assist_fire_callback == a._teams_assist_fire
        )
        # pre-existing canonical callbacks still wired
        assert mgr.auto_start_callback is not None
        assert mgr.webhook_fire_callback is not None
        assert mgr.runtime_ops_callback is not None

    def test_agent_fire_delegates_to_engine(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        from src.agent.agent import Agent

        captured = {}

        def fake_make(agent_factory):
            captured["factory"] = agent_factory
            return lambda payload=None: {"ok": True, "answer": "x"}

        monkeypatch.setattr(tat, "make_teams_assist_fire", fake_make)
        a = Agent()
        out = a._teams_assist_fire({"source": "t"})
        assert out == {"ok": True, "answer": "x"}
        assert isinstance(captured["factory"](), Agent)

    def test_agent_resume_teams_assist_no_host(self, ta_home,
                                               monkeypatch):
        from src.agent.agent import Agent
        from src.mcp_client.manager import McpManager

        fake_mgr = MagicMock(spec=McpManager)
        fake_mgr._teams_assist_local_host = None
        monkeypatch.setattr(
            McpManager, "from_config",
            classmethod(lambda cls, *a, **k: fake_mgr),
        )
        a = Agent()
        assert a.resume_teams_assist() == 0

    def test_shutdown_stops_watch_no_thread(self, ta_home):
        from src.agent.agent import Agent
        from src.mcp_client.host import TeamsAssistLocalHost
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(
            None, include_local_hosts=True,
        )
        assert isinstance(
            mgr._teams_assist_local_host, TeamsAssistLocalHost,
        )
        a = Agent()
        a._mcp_manager = mgr
        a.shutdown()  # no thread running — must not raise

# ─── boot resume ──────────────────────────────────────────────────


class TestBootResume:
    def _agent_stub(self, resume=None):
        a = MagicMock()
        a.resume_teams_assist = (
            MagicMock(return_value=1) if resume is None else resume
        )
        return a

    def test_resume_when_ready(self, ta_home, monkeypatch):
        import gui.api as api

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區")
        a = self._agent_stub()
        api.Api._resume_teams_assist(api, a)
        a.resume_teams_assist.assert_called_once()

    def test_resume_skipped_env_off(self, ta_home, monkeypatch):
        import gui.api as api

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區")
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_AUTORESUME", "off")
        a = self._agent_stub()
        api.Api._resume_teams_assist(api, a)
        a.resume_teams_assist.assert_not_called()

    def test_resume_skipped_empty_allowlist(self, ta_home, monkeypatch):
        import gui.api as api

        a = self._agent_stub()
        api.Api._resume_teams_assist(api, a)
        a.resume_teams_assist.assert_not_called()

    def test_resume_failure_swallowed(self, ta_home, monkeypatch):
        import gui.api as api

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區")
        a = self._agent_stub(
            MagicMock(side_effect=RuntimeError("boom")),
        )
        api.Api._resume_teams_assist(api, a)  # must not raise

    def test_prewarm_calls_resume(self, ta_home, monkeypatch):
        import gui.api as api

        calls: list = []
        monkeypatch.setattr(
            api.Api, "_resume_teams_assist",
            # staticmethod (the call site is `self._resume_teams_
            # assist(agent)` — a staticmethod receives only `agent`)
            staticmethod(lambda agent: calls.append(agent)),
        )
        monkeypatch.setattr(
            api.Api, "_get_agent", lambda self: MagicMock(),
        )
        for name in (
            "_resume_scheduler_daemon", "_resume_webhook_server",
            "_resume_telegram_bot", "_resume_onvif_watches",
            "_start_config_watcher",
        ):
            monkeypatch.setattr(
                api.Api, name, staticmethod(lambda agent: None),
            )
        api.Api()._prewarm_agent()
        assert len(calls) == 1

# ─── stdio mirror parity ──────────────────────────────────────────


class TestStdioParity:
    def test_stdio_server_exposes_same_surface(self, ta_home):
        from src.mcp_client.host import TeamsAssistLocalHost
        from src.mcp_server.teams_assist_server import (
            _TOOL_SPECS, TeamsAssistMCPServer,
        )

        srv = TeamsAssistMCPServer()
        host_names = {
            t.name for t in TeamsAssistLocalHost().list_tools()
        }
        spec_names = {t.name for t in _TOOL_SPECS}
        assert spec_names == host_names
        assert len(_TOOL_SPECS) == 12
        # every spec name has a dispatch handler on the server
        assert srv.host is not None

    def test_stdio_handler_map_covers_all_tools(self):
        from src.mcp_client.host import TeamsAssistLocalHost
        from src.mcp_server.teams_assist_server import (
            _TOOL_SPECS, TeamsAssistMCPServer,
        )

        srv = TeamsAssistMCPServer()
        handler_map = {
            "teams_assist_status": srv.host._status,
            "teams_assist_config": srv.host._config,
            "teams_assist_list_chats": srv.host._list_chats,
            "teams_assist_drafts": srv.host._drafts,
            "teams_assist_check": srv.host._check,
            "teams_assist_start_watch": srv.host._start_watch,
            "teams_assist_stop_watch": srv.host._stop_watch,
            "teams_assist_reply": srv.host._reply,
            "teams_assist_send_uia": srv.host._send_uia,
            "teams_assist_read_uia": srv.host._read_uia,
            "teams_assist_pause": srv.host._pause,
            "teams_assist_resume": srv.host._resume,
        }
        assert {t.name for t in _TOOL_SPECS} == set(handler_map)
        # a couple of dispatch round-trips through the shared handlers
        assert handler_map["teams_assist_status"]({})["ok"] is True
        assert handler_map["teams_assist_reply"](
            {"text": "", "confirm": False},
        ).get("error")

    def test_prompts_embed_allowlist_and_persona(
        self, ta_home, monkeypatch,
    ):
        from src.teams_assist_tools import _compose_fire_prompt

        monkeypatch.setenv("TEAMS_ASSIST_ALLOW_CHATS", "專案區,瑣事")
        monkeypatch.setenv("TEAMS_MY_NAME", "王小明")
        monkeypatch.setenv("TEAMS_MY_ROLE", "PM")
        prompt = _compose_fire_prompt({"source": "x"})
        assert "專案區、瑣事" in prompt
        assert "王小明" in prompt
        assert "PM" in prompt
        assert "teams_assist_check" in prompt
        assert "teams_assist_reply" in prompt
        assert "mock" in prompt

    def test_prompt_auto_off_draft_only(self, ta_home):
        from src.teams_assist_tools import _compose_fire_prompt

        prompt = _compose_fire_prompt(None)
        assert "AUTO=off" in prompt
        assert "不得打字送出" in prompt

# ─── UIA channel (v0.9.70.0+) ─────────────────────────────────────


class TestSendModeResolver:
    def test_default_invalid_and_uia(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _send_mode

        assert _send_mode() == "windows-mcp"
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_SEND_MODE", "uia")
        assert _send_mode() == "uia"
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_SEND_MODE", "bogus")
        assert _send_mode() == "windows-mcp"


class TestUiaOps:
    def test_script_present_with_safety_chain(self, ta_home):
        from src.teams_assist_tools import _uia_script_path

        p = _uia_script_path()
        assert p is not None and p.name == "uia_teams_ops.ps1"
        body = p.read_text(encoding="utf-8")
        assert "compose-not-empty-yield" in body
        assert "InvokePattern" in body
        assert "send-button-still-present" in body

    def test_script_missing(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        monkeypatch.setenv(
            "QDRANT_TEAMS_ASSIST_UIA_SCRIPT",
            str(ta_home / "absent.ps1"),
        )
        assert tat._uia_script_path() is None
        assert tat._uia_ops("read") == {
            "ok": False, "reason": "uia-script-missing",
        }

    def test_bad_action_and_empty_text(self, ta_home):
        from src.teams_assist_tools import _uia_ops

        assert _uia_ops("bogus") == {"ok": False, "reason": "bad-action"}
        assert _uia_ops("send", "   ") == {
            "ok": False, "reason": "empty-text",
        }

    def test_search_action_allowed_and_textfile(self, ta_home, monkeypatch):
        """v0.9.105.1 — search 動作：合法＋目標名照 TextFile 傳遞（CJK 安全）"""
        import src.teams_assist_tools as tat

        captured = {}

        class _Proc:
            returncode = 0
            stdout = ('{"ok": true, "action": "search", "navigated": '
                      '"select", "picked": "董君樸", "doc_chars": 1797}')
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            if "-TextFile" in cmd:
                tf = cmd[cmd.index("-TextFile") + 1]
                with open(tf, encoding="utf-8") as f:
                    captured["textfile"] = f.read()
            return _Proc()

        monkeypatch.setattr("subprocess.run", fake_run)
        out = tat._uia_ops("search", "董君樸")
        assert out["ok"] is True and out["navigated"] == "select"
        cmd = captured["cmd"]
        assert "-Action" in cmd and "search" in cmd
        assert captured["textfile"] == "董君樸"
        tf = cmd[cmd.index("-TextFile") + 1]
        assert not os.path.exists(tf)  # temp cleaned up

    def test_search_empty_text_rejected(self, ta_home):
        from src.teams_assist_tools import _uia_ops

        assert _uia_ops("search", " ") == {
            "ok": False, "reason": "empty-text",
        }

    def test_send_collapses_newlines_and_parses_json(
        self, ta_home, monkeypatch,
    ):
        import src.teams_assist_tools as tat

        captured = {}

        class _Proc:
            returncode = 0
            stdout = '{"ok": true, "sent": true, "chars": 5}'
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            if "-TextFile" in cmd:
                tf = cmd[cmd.index("-TextFile") + 1]
                with open(tf, encoding="utf-8") as f:
                    captured["textfile"] = f.read()
            return _Proc()

        monkeypatch.setattr("subprocess.run", fake_run)
        out = tat._uia_ops("send", "第一行\n第二行")
        assert out == {"ok": True, "sent": True, "chars": 5}
        cmd = captured["cmd"]
        assert "-Action" in cmd and "send" in cmd
        tf = cmd[cmd.index("-TextFile") + 1]
        assert captured["textfile"] == "第一行 第二行"
        assert not os.path.exists(tf)  # temp cleaned up
        kwargs = captured["kwargs"]
        assert kwargs["encoding"] == "utf-8"
        if os.name == "nt":
            assert "creationflags" in kwargs

    def test_run_failure_shape(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "boom"

        monkeypatch.setattr("subprocess.run", lambda cmd, **k: _Proc())
        out = tat._uia_ops("read")
        assert out["ok"] is False
        assert out["reason"] == "uia-run-failed"

    def test_bad_json_and_reason_hint(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        class _Bad:
            returncode = 0
            stdout = "not-json"
            stderr = ""

        monkeypatch.setattr("subprocess.run", lambda cmd, **k: _Bad())
        assert tat._uia_ops("read")["reason"] == "bad-json"

        class _Yield:
            returncode = 0
            stdout = '{"ok": false, "reason": "compose-not-empty-yield"}'
            stderr = ""

        monkeypatch.setattr("subprocess.run", lambda cmd, **k: _Yield())
        out = tat._uia_ops("send", "x")
        assert out["ok"] is False
        assert out["hint"]  # UIA_REASON_HINTS attached

    def test_prompt_auto_on_send_flow(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _compose_fire_prompt

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_AUTO", "on")
        prompt = _compose_fire_prompt(None)
        assert "AUTO=on" in prompt
        assert "讓路" in prompt
        assert "confirm=true" in prompt

    def test_prompt_uia_mode_uses_uia_read(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _compose_fire_prompt

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_SEND_MODE", "uia")
        p = _compose_fire_prompt(None)
        assert "teams_assist_read_uia" in p
        assert "AUTO=off" in p  # draft wording unchanged
        assert "teams_assist_send_uia" not in p  # AUTO off → no send

    def test_prompt_uia_auto_flow(self, ta_home, monkeypatch):
        from src.teams_assist_tools import _compose_fire_prompt

        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_SEND_MODE", "uia")
        monkeypatch.setenv("QDRANT_TEAMS_ASSIST_AUTO", "on")
        p = _compose_fire_prompt(None)
        assert "AUTO=on + UIA" in p
        assert "teams_assist_send_uia" in p
        assert "compose-not-empty-yield" in p
        assert "單一段落" in p


class TestHostUia:
    def _host(self):
        from src.mcp_client.host import TeamsAssistLocalHost

        return TeamsAssistLocalHost()

    def test_send_uia_requires_text(self, ta_home):
        assert self._host()._send_uia({"confirm": True}).get("error")

    def test_send_uia_draft_and_gate(self, ta_home):
        from src.teams_assist_tools import _load_state

        h = self._host()
        out = h._send_uia({"text": "x", "confirm": False})
        assert out["recorded"] == "draft"
        out = h._send_uia({"text": "x", "confirm": True})
        assert "AUTO" in out["error"]
        kinds = [r["kind"] for r in _load_state()["audit"]]
        assert kinds == ["draft"]

    def test_send_uia_manual_success(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        monkeypatch.setattr(
            tat, "_uia_ops",
            lambda a, t=None: {"ok": True, "sent": True,
                               "chars": 2, "elapsed_s": 0.5},
        )
        from src.teams_assist_tools import _load_state

        h = self._host()
        out = h._send_uia({
            "text": "回覆", "confirm": True, "manual": True,
            "chat": "專案區",
        })
        assert out["sent"] is True
        kinds = [r["kind"] for r in _load_state()["audit"]]
        assert "sent-uia" in kinds

    def test_send_uia_yield_failure(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        monkeypatch.setattr(
            tat, "_uia_ops",
            lambda a, t=None: {"ok": False, "sent": False,
                               "reason": "compose-not-empty-yield"},
        )
        out = self._host()._send_uia(
            {"text": "x", "confirm": True, "manual": True},
        )
        assert out["sent"] is False
        assert out["uia"]["reason"] == "compose-not-empty-yield"

    def test_send_uia_run_failure_audited(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        monkeypatch.setattr(
            tat, "_uia_ops",
            lambda a, t=None: {"ok": False, "sent": False,
                               "reason": "no-teams-process"},
        )
        from src.teams_assist_tools import _load_state

        out = self._host()._send_uia(
            {"text": "x", "confirm": True, "manual": True},
        )
        assert out["sent"] is False
        kinds = [r["kind"] for r in _load_state()["audit"]]
        assert "send-uia-failed" in kinds

    def test_read_uia_passthrough(self, ta_home, monkeypatch):
        import src.teams_assist_tools as tat

        monkeypatch.setattr(
            tat, "_uia_ops",
            lambda a: {"ok": True, "chars": 4,
                       "text": "聊天內容", "window": "w"},
        )
        out = self._host()._read_uia({})
        assert out["ok"] is True and out["chars"] == 4
        monkeypatch.setattr(
            tat, "_uia_ops",
            lambda a: {"ok": False, "reason": "no-teams-process"},
        )
        out = self._host()._read_uia({})
        assert out["ok"] is False