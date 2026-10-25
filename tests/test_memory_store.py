"""v0.9.59.0 — Qdrant long-term memory (MemoryStore + hooks).

Locks down: deterministic uuid5 ids (re-saves = upserts), the
取得時間 (stored_at ISO + epoch) lifecycle payload, kind/source
filters on recall, the refuse-unfiltered-purge guard, the
SessionManager.save → ingest_session_pair hook (error/guarded
answers skipped, env toggle honoured), and the env parsing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import src.config  # noqa: F401 — .env loads BEFORE the wipe
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    for name in ("QDRANT_MEMORY_COLLECTION", "QDRANT_MEMORY_ENABLED",
                 "QDRANT_MEMORY_TOP_K", "QDRANT_MEMORY_SESSION_INGEST",
                 "QDRANT_MEMORY_TOOLS", "QDRANT_TELEGRAM_VOICE_REPLY"):
        monkeypatch.delenv(name, raising=False)
    yield


class _FakeQdrant:
    def __init__(self):
        self.points: dict = {}
        self.collection_exists = lambda name: True
        self.create_collection = MagicMock()
        self.create_payload_index = MagicMock()

    def upsert(self, collection_name, points):
        for p in points:
            self.points[str(p.id)] = {
                "vector": p.vector, "payload": dict(p.payload),
            }

    def query_points(self, collection_name, query=None, limit=10,
                     with_payload=True, query_filter=None,
                     score_threshold=0.0):
        rows = sorted(
            self.points.values(),
            key=lambda r: -float(r["payload"].get("stored_at_epoch", 0)),
        )
        rows = [
            r for r in rows
            if float(r["payload"].get("stored_at_epoch", 0)) > 0
        ]
        # trivial "semantic" order: newest first (tests only)
        pts = []
        for r in rows[: int(top_k if False else query or 5)]:
            m = MagicMock()
            m.id = "id-" + str(abs(hash(r["payload"]["content"])))
            m.score = 0.9
            m.payload = r["payload"]
            pts.append(m)
        return MagicMock(points=pts)

    def scroll(self, collection_name, scroll_filter=None, limit=1000,
               with_payload=True, offset=None):
        rows = sorted(
            self.points.values(),
            key=lambda r: -float(r["payload"].get("stored_at_epoch", 0)),
        )
        class _P:
            pass
        pts = []
        for r in rows:
            p = _P()
            p.id = "id-" + str(abs(hash(r["payload"]["content"])))
            p.payload = r["payload"]
            pts.append(p)
        return pts, None

    def count(self, collection_name, count_filter=None, exact=True):
        class _C:
            count = len(self.points)
        return _C()

    def delete(self, collection_name, points_selector=None):
        self.points.clear()


class _FakeEmbedder:
    def __init__(self):
        self.calls: list[str] = []

    def embed(self, text):
        self.calls.append(text)
        return [0.1] * 8


def _store() -> tuple:
    import src.agent.memory_store as ms

    st = ms.MemoryStore(embedder=_FakeEmbedder())
    st._client = _FakeQdrant()
    ms._SESSION_STORE = st
    return ms, st


def _session(sid="s1", pairs=None):
    from src.agent.session import Session

    s = Session(sid=sid, title="t")
    for q, a in (pairs or []):
        s.messages.append({"role": "user", "content": q})
        s.messages.append({"role": "assistant", "content": a})
    return s


class TestEchoArtifactFilter:
    """v0.9.83.5 — a stored tool-call ECHO teaches the next turn to
    imitate the shape (self-reinforcing fake-call loop, 17:02-17:31
    live E2E). Echo answers are never stored; repaired answers are."""

    def test_echo_answer_not_stored(self):
        ms, st = _store()
        assert ms.ingest_session_pair(_session(pairs=[
            ("中華電信董事長是誰", "search_documentsq中華電信董事長"),
        ])) is None
        assert not st._client.points

    def test_bare_tool_name_not_stored(self):
        ms, st = _store()
        assert ms.ingest_session_pair(_session(pairs=[
            ("今天是幾月幾日", "get_current_time"),
        ])) is None
        assert not st._client.points

    def test_multi_glued_not_stored(self):
        ms, st = _store()
        assert ms.ingest_session_pair(_session(pairs=[
            ("今天的天氣", "get_current_timetimeframetoday"
             "web_searchmax_results5query台北 今天 天氣"),
        ])) is None
        assert not st._client.points

    def test_guard_repaired_answer_still_stored(self):
        ms, st = _store()
        ms.ingest_session_pair(_session(pairs=[
            ("今天是幾月幾日",
             "get_current_time\n（系統代為執行 get_current_time → OK）\n"
             "今天是 2026-09-21"),
        ]))
        assert st._client.points

    def test_normal_short_answer_stored(self):
        ms, st = _store()
        ms.ingest_session_pair(_session(pairs=[
            ("今天是幾月幾日", "今天是 2026 年 9 月 21 日。"),
        ]))
        assert st._client.points

    def test_long_answer_starting_with_latin_stored(self):
        ms, st = _store()
        long_ans = ("get_current_time 的結果如下：\n今天日期 "
                    "2026-09-21，星期一，台北時間 17:30，"
                    "這是從系統時鐘直接取得的可信資料，供你參考使用。")
        ms.ingest_session_pair(_session(pairs=[("今天是幾月幾日", long_ans)]))
        assert st._client.points

    def test_citation_tail_not_counted(self):
        """the citation block is stripped BEFORE the length check — a
        good answer + citation stays storable, an echo + citation is
        still an echo artifact."""
        ms, st = _store()
        echo = ("web_searchq中華電信董事長 2026 現任\n"
                "— 依長期記憶作答（取得的時間：2026-09-15T07:57:21+08:00"
                " (source=web_search)）")
        assert ms.ingest_session_pair(
            _session(pairs=[("中華電信董事長是誰", echo)])) is None
        assert not st._client.points


class TestPromptEchoHardRule:
    def test_tools_prompt_has_hard_rule(self):
        from src.agent.agent import Agent

        prompt = Agent()._system_prompt(tools_enabled=True)
        assert "tool_calls mechanism" in prompt
        assert "v0.9.83.5 HARD RULE" in prompt

    def test_legacy_prompt_excluded(self):
        from src.agent.agent import Agent

        legacy = Agent()._system_prompt(tools_enabled=False)
        assert "tool_calls mechanism" not in legacy


class TestMemoryStore:
    def test_remember_upserts_payload_with_timestamp(self):
        ms, st = _store()
        info = st.remember("使用者喜歡簡短回覆", kind="fact",
                           source="manual")
        assert info["id"]
        assert info["stored_at"]
        point = next(iter(st._client.points.values()))
        payload = point["payload"]
        assert payload["kind"] == "fact"
        assert payload["source"] == "manual"
        assert payload["content"] == "使用者喜歡簡短回覆"
        assert payload["stored_at"]
        assert float(payload["stored_at_epoch"]) > 0

    def test_deterministic_id_overwrites(self):
        ms, st = _store()
        st.remember("同一句話", kind="fact")
        first = list(st._client.points.keys())
        st.remember("同一句話", kind="fact")
        assert list(st._client.points.keys()) == first  # no duplicate
        assert len(st._client.points) == 1

    def test_recent_sorted_by_time_desc(self):
        import time as _t

        ms, st = _store()
        st.remember("舊的記憶", kind="fact",
                    ts_epoch=_t.time() - 3600.0)
        st.remember("新的記憶", kind="fact", ts_epoch=_t.time())
        rows = st.recent(limit=10)
        assert rows[0]["content"] == "新的記憶"
        assert rows[1]["content"] == "舊的記憶"

    def test_forget_rejects_invalid_id(self):
        ms, st = _store()
        st.remember("要忘掉的", kind="fact")
        assert st.forget("not-a-uuid") is False
        assert len(st._client.points) == 1

    def test_purge_refuses_unfiltered(self):
        ms, st = _store()
        st.remember("x", kind="fact")
        assert st.purge() == 0
        assert len(st._client.points) == 1

    def test_env_parsing(self, monkeypatch):
        from src.agent.memory_store import memory_enabled

        assert memory_enabled() is True
        monkeypatch.setenv("QDRANT_MEMORY_ENABLED", "off")
        assert memory_enabled() is False
        monkeypatch.setenv("QDRANT_MEMORY_ENABLED", "OFF")
        assert memory_enabled() is False


class TestSessionIngest:
    def test_last_valid_pair_stored(self):
        ms, st = _store()
        sess = _session(pairs=[("第一次問題", "第一次回答"),
                               ("第二次問題", "第二次回答")])
        out = ms.ingest_session_pair(sess)
        assert out["id"]
        payload = next(iter(st._client.points.values()))["payload"]
        assert payload["kind"] == "chat"
        assert payload["source"] == "s1"
        assert "第二次問題" in payload["content"]
        assert "第二次回答" in payload["content"]
        assert "第一次" not in payload["content"]

    def test_error_and_guarded_answers_skipped(self):
        ms, st = _store()
        sess = _session(pairs=[("q", "[error] boom")])
        assert ms.ingest_session_pair(sess) is None
        sess.messages.append({"role": "assistant",
                              "content": "假成功",
                              "hallucination_guarded": True})
        assert ms.ingest_session_pair(sess) is None
        assert not st._client.points

    def test_session_ingest_disabled(self, monkeypatch):
        ms, st = _store()
        monkeypatch.setenv("QDRANT_MEMORY_SESSION_INGEST", "off")
        sess = _session(pairs=[("q", "a")])
        assert ms.ingest_session_pair(sess) is None
        assert not st._client.points


class TestSaveHookAndAgent:
    def test_save_hook_real(self, tmp_path):
        ms, st = _store()
        from src.agent.session import SessionManager

        root = tmp_path / "sessions"
        root.mkdir(parents=True, exist_ok=True)
        sm = SessionManager(root=root)
        sess = _session(sid="hook1", pairs=[("我的最愛是咖啡", "已記下")])
        sm.save(sess)
        # The hook ran through the REAL save path.
        assert st._client.points, "session hook did not ingest"
        payload = next(iter(st._client.points.values()))["payload"]
        assert "我的最愛是咖啡" in payload["content"]
        # And persistence itself is untouched.
        loaded = sm.load("hook1")
        assert loaded is not None and len(loaded.messages) == 2

    def test_memory_block_injects_hits(self, monkeypatch):
        import src.agent.memory_store as ms

        class _FakeStore:
            def search(self, query, top_k=5, **kw):
                return [{
                    "id": "m1", "kind": "chat", "source": "s1",
                    "stored_at": "2026-09-15T10:00:00+08:00",
                    "content": "Q: 最愛?\nA: 咖啡",
                }]

        monkeypatch.setattr(ms, "_SESSION_STORE", _FakeStore())
        monkeypatch.setattr(ms, "memory_enabled", lambda: True)
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        block = a._memory_block("我最愛什麼?")
        assert "LONG-TERM MEMORY" in block
        assert "取得的時間=2026-09-15T10:00:00+08:00" in block
        assert "咖啡" in block

    def test_memory_disabled_returns_empty(self, monkeypatch):
        import src.agent.memory_store as ms

        monkeypatch.setattr(ms, "memory_enabled", lambda: False)
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        assert a._memory_block("x") == ""

    def test_memory_block_disabled_flag_skips_recall(self, monkeypatch):
        """v0.9.67.2 — enabled=False returns "" WITHOUT touching the
        store (fire path: no old-price snapshots injected)."""
        import src.agent.memory_store as ms

        class _Boom:
            def search(self, *a, **kw):
                raise AssertionError("store must not be queried")

        monkeypatch.setattr(ms, "_SESSION_STORE", _Boom())
        monkeypatch.setattr(ms, "memory_enabled", lambda: True)
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a._last_memory_hits = []
        assert a._memory_block("x", enabled=False) == ""
        assert a._last_memory_hits == []

    def test_memory_block_historical_snapshot_rule(self, monkeypatch):
        import src.agent.memory_store as ms

        class _FakeStore:
            def search(self, query, top_k=5, **kw):
                return [{
                    "id": "m2", "kind": "query", "source": "web_search",
                    "stored_at": "2026-09-15T14:00:00+08:00",
                    "content": "加權指數收 47,326 點",
                }]

        monkeypatch.setattr(ms, "_SESSION_STORE", _FakeStore())
        monkeypatch.setattr(ms, "memory_enabled", lambda: True)
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        block = a._memory_block("今天股市行情?")
        assert "歷史快照" in block
        assert "禁止把記憶中的數值當作當前資料引用" in block
        assert "必須重新查詢工具" in block

    def test_messages_with_user_use_memory_false(self, monkeypatch):
        """v0.9.67.2 — the suppression threads through
        ``_messages_with_user`` (the exact call chain the scheduler
        fire uses)."""
        import src.agent.memory_store as ms

        calls: list[tuple] = []

        class _FakeStore:
            def search(self, query, top_k=5, **kw):
                calls.append((query, top_k))
                return [{
                    "id": "m3", "kind": "chat", "source": "s",
                    "stored_at": "2026-09-15T10:00:00+08:00",
                    "content": "舊資料",
                }]

        monkeypatch.setattr(ms, "_SESSION_STORE", _FakeStore())
        monkeypatch.setattr(ms, "memory_enabled", lambda: True)
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a.compressor = None
        a._system_prompt = lambda **kw: "SYS"
        # default (None) → recall ON
        a._messages_with_user("Q?", history=[],
                              web_enabled=False, tools_enabled=False)
        assert len(calls) == 1
        # False → skipped entirely
        a._messages_with_user("Q?", history=[],
                              web_enabled=False, tools_enabled=False,
                              use_memory=False)
        assert len(calls) == 1  # no second query

    def test_memory_tools_default_whitelist(self, monkeypatch):
        from src.agent.agent import _memory_tools, _memory_top_k

        monkeypatch.delenv("QDRANT_MEMORY_TOOLS", raising=False)
        assert _memory_tools() == ["web_search", "web_fetch"]
        monkeypatch.setenv("QDRANT_MEMORY_TOOLS", "web_search")
        assert _memory_tools() == ["web_search"]
        monkeypatch.setenv("QDRANT_MEMORY_TOP_K", "3")
        assert _memory_top_k() == 3
        monkeypatch.setenv("QDRANT_MEMORY_TOP_K", "abc")
        assert _memory_top_k() == 5

    def test_prompt_memory_block_present(self):
        from src.agent.agent import Agent

        p = Agent.__new__(Agent)._system_prompt(tools_enabled=True)
        assert "remember_info" in p
        assert "search_memory" in p
        assert "取得的時間" in p
        legacy = Agent.__new__(Agent)._system_prompt(
            tools_enabled=False,
        )
        assert "remember_info" not in legacy

    def test_manager_and_stdio_wiring(self):
        src = Path("src/mcp_client/manager.py").read_text(
            encoding="utf-8",
        )
        assert "MemoryLocalHost" in src
        assert '"memory-local"' in src
        srv = Path("src/mcp_server/memory_server.py").read_text(
            encoding="utf-8",
        )
        for tool in ("remember_info", "search_memory",
                     "list_recent_memories", "forget_memory",
                     "purge_memories", "suggest_memory_cleanup"):
            assert tool in srv

    def test_host_tools_surface_and_dispatch(self):
        from src.mcp_client.host import MemoryLocalHost

        ms, st = _store()
        host = MemoryLocalHost(store=st)
        names = [t.name for t in host.list_tools()]
        # v0.9.133.3 — the T+1 session-summary compactor joins the
        # memory-local surface (7th tool)
        assert names == [
            "remember_info", "search_memory", "list_recent_memories",
            "forget_memory", "purge_memories",
            "suggest_memory_cleanup",
            "summarize_sessions",
        ]
        out = host._remember_info({"text": "記住測試", "kind": "note"})
        assert out["ok"] is True
        assert len(st._client.points) == 1
        assert host._forget({"id": "not-a-uuid"}).get("error")
        assert host._purge({}).get("error")


class TestDeterministicCitation:
    """v0.9.59.2 — the answer post-process appends the 取得的時間
    citation line when the answer verbatim-uses an injected memory
    WITHOUT naming its acquisition date."""

    def _agent_with_hits(self, hits):
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a._last_memory_hits = hits
        return a

    def test_appends_citation_when_used_without_time(self):
        hits = [{
            "id": "m1", "kind": "fact", "source": "manual",
            "stored_at": "2026-09-15T02:59:50+08:00",
            "content": "使用者的投資偏好：以台股 ETF 為主，風險趨向保守。",
        }]
        a = self._agent_with_hits(hits)
        out = a._append_memory_citation(
            "您的投資偏好是：以台股 ETF 為主，風險趨向保守。"
        )
        assert "依長期記憶作答" in out
        assert "2026-09-15T02:59:50+08:00" in out
        assert "source=manual" in out
        # appended ONCE, at the end
        assert out.count("依長期記憶作答") == 1
        assert out.rstrip().endswith("）")

    def test_no_double_cite_when_date_already_named(self):
        hits = [{
            "id": "m1", "kind": "fact", "source": "manual",
            "stored_at": "2026-09-15T02:59:50+08:00",
            "content": "以台股 ETF 為主，風險趨向保守。",
        }]
        a = self._agent_with_hits(hits)
        answer = ("根據長期記憶（取得時間：2026-09-15T02:59:50+08:00），"
                  "以台股 ETF 為主、風險趨向保守。")
        assert a._append_memory_citation(answer) == answer

    def test_unrelated_answer_untouched(self):
        hits = [{
            "id": "m1", "kind": "fact", "source": "manual",
            "stored_at": "2026-09-15T02:59:50+08:00",
            "content": "以台股 ETF 為主，風險趨向保守。",
        }]
        a = self._agent_with_hits(hits)
        answer = "今天台北天氣多雲，氣溫 28 度。"
        assert a._append_memory_citation(answer) == answer

    def test_no_hits_untouched(self):
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a._last_memory_hits = []
        answer = "任意回答"
        assert a._append_memory_citation(answer) == answer

    def test_helpers(self):
        from src.agent.agent import _memory_phrases, _norm_for_match

        assert _norm_for_match("以台股 ETF，為主。") == "以台股ETF為主"
        probes = _memory_phrases("使用者的投資偏好：以台股 ETF 為主，"
                                 "風險趨向保守。")
        assert any("投資偏好" in p for p in probes)

    def test_memory_block_records_hits(self, monkeypatch):
        import src.agent.memory_store as ms

        class _FakeStore:
            def search(self, query, top_k=5, **kw):
                return [{
                    "id": "m1", "kind": "fact", "source": "manual",
                    "stored_at": "2026-09-15T02:59:50+08:00",
                    "content": "以台股 ETF 為主，風險趨向保守。",
                }]

        monkeypatch.setattr(ms, "_SESSION_STORE", _FakeStore())
        monkeypatch.setattr(ms, "memory_enabled", lambda: True)
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a._memory_block("投資偏好?")
        assert a._last_memory_hits and a._last_memory_hits[0]["id"] \
            == "m1"