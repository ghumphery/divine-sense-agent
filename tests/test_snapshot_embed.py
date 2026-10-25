"""v0.9.102.7 — deterministic camera-snapshot embed.

Live 2026-09-29 12:18 「get 五股大門畫面」 (user: 沒有 取得大門畫面):
the turn REALLY dispatched onvif_get_snapshot (OK, snapshot saved to
the write sandbox) but the final answer was 479 chars of prose with NO
path → the chat UI's renderSandboxImages() had nothing to match → no
inline preview. The v0.9.76.3 list-chats deterministic post-process
pattern applied to snapshots: _run_tool_call stashes the path,
_ensure_snapshot_embed appends a previewable path line when the answer
dropped it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.mcp_client.base import McpCallResult  # noqa: E402

_PATH = "C:/Users/humphery/uploads/onvif-snapshot-20260929-122021.jpg"
_RESULT = '{"ok": true, "name": "main_door", "path": "%s"}' % _PATH


@pytest.fixture()
def agent():
    from src.agent.agent import Agent

    a = Agent.__new__(Agent)
    a._mcp_manager = MagicMock()
    a._mcp_manager.call.return_value = McpCallResult(
        text=_RESULT, is_error=False,
    )
    a.last_tool_dispatches = []
    a._snapshot_paths = []
    return a


def _tool_call(name, args='{"name": "cam1"}'):
    return SimpleNamespace(
        id="c1",
        function=SimpleNamespace(name=name, arguments=args),
    )


class TestPathExtraction:
    def test_json_path_key(self):
        from src.agent.agent import _snapshot_path_from_result as f
        assert f(_RESULT) == _PATH

    def test_json_alternate_keys(self):
        from src.agent.agent import _snapshot_path_from_result as f
        assert f('{"ok": true, "snapshot": "C:/x/snap.jpg"}') == \
            "C:/x/snap.jpg"
        assert f('{"file": "C:\\\\x\\\\s.jpg"}') == "C:\\x\\s.jpg"

    def test_regex_fallback_backslash(self):
        from src.agent.agent import _snapshot_path_from_result as f
        assert f('bad json { but C:\\\\u\\\\snap.PNG inside text.') == \
            "C:\\\\u\\\\snap.PNG"

    def test_regex_fallback_forward_slash(self):
        from src.agent.agent import _snapshot_path_from_result as f
        assert f("prose C:/x/snap.webp tail") == "C:/x/snap.webp"

    def test_skips_video_then_finds_image(self):
        from src.agent.agent import _snapshot_path_from_result as f
        assert f("C:/x/row1.mp4 then C:/x/gate.jpg end") == "C:/x/gate.jpg"

    def test_no_image_returns_empty(self):
        from src.agent.agent import _snapshot_path_from_result as f
        assert f("no path here at all") == ""
        assert f("") == ""

    def test_non_image_ext_refused(self):
        from src.agent.agent import _snapshot_path_from_result as f
        assert f('{"ok": true, "path": "C:/x/clip.mp4"}') == ""


class TestRunToolCallStash:
    def test_stashes_path(self, agent):
        agent._run_tool_call(_tool_call("onvif_get_snapshot"))
        assert agent._snapshot_paths == [_PATH]

    def test_rtsp_snapshot_covered(self, agent):
        r = '{"ok": true, "path": "C:/x/rtsp.jpg"}'
        agent._mcp_manager.call.return_value = McpCallResult(
            text=r, is_error=False,
        )
        agent._run_tool_call(_tool_call("onvif_rtsp_snapshot"))
        assert agent._snapshot_paths == ["C:/x/rtsp.jpg"]

    def test_error_result_not_stashed(self, agent):
        agent._mcp_manager.call.return_value = McpCallResult(
            text=_RESULT, is_error=True,
        )
        agent._run_tool_call(_tool_call("onvif_get_snapshot"))
        assert agent._snapshot_paths == []

    def test_non_snapshot_tool_not_stashed(self, agent):
        agent._run_tool_call(_tool_call("get_current_time", "{}"))
        assert agent._snapshot_paths == []

    def test_dedup_same_path(self, agent):
        agent._run_tool_call(_tool_call("onvif_get_snapshot"))
        agent._run_tool_call(_tool_call("onvif_get_snapshot"))
        assert agent._snapshot_paths == [_PATH]

    def test_missing_attr_recovers(self, agent):
        del agent._snapshot_paths
        agent._run_tool_call(_tool_call("onvif_get_snapshot"))
        assert agent._snapshot_paths == [_PATH]


class TestEmbed:
    def test_missing_path_appended(self, agent):
        agent._snapshot_paths = [_PATH]
        out = agent._ensure_snapshot_embed("已取得大門畫面。")
        assert out.startswith("已取得大門畫面。")
        assert "📷 快照畫面：" + _PATH in out

    def test_answer_with_path_untouched(self, agent):
        txt = "已取得大門畫面：" + _PATH
        assert agent._ensure_snapshot_embed(txt) == txt

    def test_no_stash_untouched(self, agent):
        assert agent._ensure_snapshot_embed("任意答案") == "任意答案"

    def test_missing_attr_untouched(self, agent):
        del agent._snapshot_paths
        assert agent._ensure_snapshot_embed("任意答案") == "任意答案"

    def test_none_and_empty_safe(self, agent):
        agent._snapshot_paths = [_PATH]
        assert "📷 快照畫面：" + _PATH in agent._ensure_snapshot_embed(None)
        assert "📷 快照畫面：" + _PATH in agent._ensure_snapshot_embed("")

    def test_multiple_snapshots_all_lines(self, agent):
        agent._snapshot_paths = ["C:/x/a.jpg", "C:/x/b.jpg"]
        out = agent._ensure_snapshot_embed("畫面")
        assert "📷 快照畫面：C:/x/a.jpg" in out
        assert "📷 快照畫面：C:/x/b.jpg" in out


class TestIntegration:
    def test_end_to_end_run_then_embed(self, agent):
        agent._run_tool_call(_tool_call("onvif_get_snapshot"))
        out = agent._ensure_snapshot_embed(
            agent._ensure_list_chats_summary("畫面好了。")
        )
        assert "📷 快照畫面：" + _PATH in out
        assert out.startswith("畫面好了。")

    def test_both_entries_reset_present(self):
        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        assert src.count("self._snapshot_paths = []") >= 2

    def test_return_sites_wired(self):
        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        assert src.count("self._ensure_snapshot_embed(") == 4
        assert "def _ensure_snapshot_embed(self, content" in src

    def test_tool_names_covered(self):
        from src.agent.agent import _SNAPSHOT_TOOLS
        assert "onvif_get_snapshot" in _SNAPSHOT_TOOLS
        assert "onvif_rtsp_snapshot" in _SNAPSHOT_TOOLS


class TestNoFrontendChange:
    def test_app_js_untouched_by_this_fix(self):
        js = Path("gui/assets/app.js").read_text(encoding="utf-8")
        # the preview machinery already existed (v0.9.30.x)
        assert "renderSandboxImages()" in js
        assert "uploads[\\\\/]" in js  # the branch-b regex shape