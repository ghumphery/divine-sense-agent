# -*- coding: utf-8 -*-
"""v0.9.111.0 — 記憶使用追蹤 + task-result sink（store-NOT-recall）。

鎖定：
* recall 命中 → touch_hits（fire-and-forget thread）回寫
  hit_count/last_hit_*（既有點缺欄位視為 0；環境閘 off=不寫）。
* search 的 exclude_kinds must_not 過濾；search_memory 不傳 exclude
  → 仍可發現 task-result（on-demand discovery）。
* _memory_block 自動召回：排除 kind=task-result + 觸發 touch。
* store_task_result：kind/text 形狀/環境閘/永不 raise。
* list_recent_memories 露出 usage 報表 + hit 欄位。
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("QDRANT_MEMORY_COLLECTION", "QDRANT_MEMORY_ENABLED",
                 "QDRANT_MEMORY_TOP_K",
                 "QDRANT_MEMORY_SESSION_INGEST",
                 "QDRANT_MEMORY_TOOLS", "QDRANT_MEMORY_HIT_STATS",
                 "QDRANT_TASK_MEMORY_SINK",
                 "QDRANT_TELEGRAM_VOICE_REPLY"):
        monkeypatch.delenv(name, raising=False)
    yield


class _FakeQdrant2:
    """Fake client with retrieve + set_payload."""

    def __init__(self):
        self.points: dict = {}
        self.set_payloads: list[dict] = []
        self.collection_exists = lambda name: True

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
        pts = []

        class _P:
            pass

        for r in rows[: int(limit)]:
            p = _P()
            p.id = next(k for k, v in self.points.items() if v is r)
            p.score = 0.9
            p.payload = r["payload"]
            pts.append(p)
        return MagicMock(points=pts)

    def scroll(self, collection_name, scroll_filter=None, limit=1000,
               with_payload=True, offset=None):
        class _P:
            pass

        pts = []
        for k, v in self.points.items():
            p = _P()
            p.id = k
            p.payload = v["payload"]
            pts.append(p)
        return pts, None

    def retrieve(self, collection_name, ids, with_payload=True,
                 with_vectors=False):
        class _P:
            pass

        pts = []
        for i in ids:
            rec = self.points.get(str(i))
            if rec is None:
                continue
            p = _P()
            p.id = str(i)
            p.payload = rec["payload"]
            pts.append(p)
        return pts

    def set_payload(self, collection_name, payload, points, wait=True):
        self.set_payloads.append({
            "payload": dict(payload), "points": list(points),
        })
        for pid in points:
            if pid in self.points:
                self.points[pid]["payload"] = dict(payload)

    def count(self, collection_name, count_filter=None, exact=True):
        class _C:
            count = len(self.points)
        return _C()

    def delete(self, collection_name, points_selector=None):
        self.points.clear()


class _FakeEmbedder:
    def embed(self, text):
        return [0.1] * 8


def _store():
    import src.agent.memory_store as ms

    st = ms.MemoryStore(embedder=_FakeEmbedder())
    st._client = _FakeQdrant2()
    ms._SESSION_STORE = st
    return ms, st
# ─── hit bookkeeping ─────────────────────────────────────────────

class TestTouchHits:
    def test_impl_bumps_hit_count_and_stamps(self):
        ms, st = _store()
        info = st.remember("咖啡豆存儲位置", kind="fact", source="manual")
        pid = info["id"]
        assert st._client.points[pid]["payload"]["hit_count"] == 0
        st._touch_hits_impl([pid])  # synchronous impl
        pay = st._client.points[pid]["payload"]
        assert pay["hit_count"] == 1
        assert pay["last_hit_at"]
        assert float(pay["last_hit_epoch"]) > 0
        assert any(sp["points"] == [pid]
                   for sp in st._client.set_payloads)
        st._touch_hits_impl([pid])
        assert st._client.points[pid]["payload"]["hit_count"] == 2

    def test_impl_legacy_point_without_fields_starts_at_1(self):
        ms, st = _store()
        pid = st.remember("舊格式記憶", kind="chat", source="s1")["id"]
        pay = st._client.points[pid]["payload"]
        pay.pop("hit_count", None)
        st._touch_hits_impl([pid])
        assert st._client.points[pid]["payload"]["hit_count"] == 1

    def test_touch_hits_async_wrapper_touched(self):
        ms, st = _store()
        pid = st.remember("非同步包裝", kind="fact")["id"]
        assert st.touch_hits([pid]) == {"touched": 1}
        deadline = time.monotonic() + 5.0
        while (st._client.points[pid]["payload"]["hit_count"] != 1
               and time.monotonic() < deadline):
            time.sleep(0.05)
        assert st._client.points[pid]["payload"]["hit_count"] == 1

    def test_env_gate_off_writes_nothing(self, monkeypatch):
        ms, st = _store()
        pid = st.remember("停用狀態", kind="fact")["id"]
        monkeypatch.setenv("QDRANT_MEMORY_HIT_STATS", "off")
        assert st.touch_hits([pid]) == {"touched": 0}
        assert st._client.set_payloads == []
        assert st._client.points[pid]["payload"]["hit_count"] == 0

    def test_broken_session_store_never_raises(self, monkeypatch):
        ms, st = _store()

        class _BoomClient:
            def retrieve(self, *a, **kw):
                raise RuntimeError("qdrant down")

        st._client = _BoomClient()
        assert st._touch_hits_impl(["whatever"]) is None  # swallowed

    def test_usage_report_counts(self):
        ms, st = _store()
        now = time.time()
        st.remember("舊且零命中的快照", kind="query",
                    ts_epoch=now - 40 * 24 * 3600)
        st.remember("新的未命中記憶", kind="query", ts_epoch=now - 100)
        pid = st.remember("常被引用的記憶", kind="fact")["id"]
        st._touch_hits_impl([pid])
        rep = st.usage_report()
        assert rep["total"] == 3
        assert rep["hit_tracked"] == 1
        assert rep["never_hit"] == 2
        assert rep["never_hit_older_than_s"]["count"] == 1
# ─── search exclude_kinds + task-result sink shape ───────────────

class TestSearchExclusion:
    def test_must_not_filter_built(self):
        ms, st = _store()
        captured = {}
        orig_qp = st._client.query_points

        def qp(collection_name, query=None, limit=10, with_payload=True,
               query_filter=None, score_threshold=0.0):
            captured["filter"] = query_filter
            return orig_qp(collection_name, query=query, limit=limit,
                           with_payload=with_payload,
                           query_filter=query_filter,
                           score_threshold=score_threshold)

        st._client.query_points = qp
        st.search("查什麼", top_k=3,
                  exclude_kinds=("task-result",))
        f = captured["filter"]
        kinds = [c.match.any for c in (f.must_not or [])
                 if getattr(getattr(c, "match", None), "any", None)]
        assert ["task-result"] in kinds

    def test_no_exclusion_no_must_not(self):
        ms, st = _store()
        captured = {}
        orig_qp = st._client.query_points

        def qp(collection_name, query=None, limit=10, with_payload=True,
               query_filter=None, score_threshold=0.0):
            captured["filter"] = query_filter
            return orig_qp(collection_name, query=query, limit=limit,
                           query_filter=query_filter)

        st._client.query_points = qp
        st.search("查什麼")  # default: no exclusion
        f = captured["filter"]
        assert not (getattr(f, "must_not", None) or [])

    def test_search_with_kind_filter_still_excludes(self):
        ms, st = _store()
        captured = {}
        orig_qp = st._client.query_points

        def qp(collection_name, query=None, limit=10, with_payload=True,
               query_filter=None, score_threshold=0.0):
            captured["filter"] = query_filter
            return orig_qp(collection_name, query=query, limit=limit,
                           query_filter=query_filter)

        st._client.query_points = qp
        st.search("查什麼", kind="fact",
                  exclude_kinds=("task-result",))
        f = captured["filter"]
        assert [c.match.value for c in f.must] == ["fact"]
        assert [c.match.any for c in f.must_not] == [["task-result"]]


class TestStoreTaskResult:
    def test_shape_and_gate(self, monkeypatch):
        ms, st = _store()
        monkeypatch.setenv("QDRANT_TASK_MEMORY_SINK", "off")
        assert ms.store_task_result(
            title="t", prompt="p", answer="a", source="task:x",
        ) is None
        monkeypatch.delenv("QDRANT_TASK_MEMORY_SINK")
        out = ms.store_task_result(
            title="股票分析",
            prompt="分析台積電今天走勢",
            answer="收盤價上漲 1.2%；外資買超。",
            source="task:z9", session_id="task-z9",
        )
        assert out and out["id"]
        payload = [v["payload"]
                   for v in st._client.points.values()][0]
        assert payload["kind"] == "task-result"
        assert payload["session_id"] == "task-z9"
        assert payload["content"].startswith("task: 股票分析")
        assert "result: 收盤價上漲" in payload["content"]

    def test_sink_failure_never_raises(self, monkeypatch):
        ms, st = _store()
        monkeypatch.setenv("QDRANT_TASK_MEMORY_SINK", "on")

        class _Boom:
            def remember(self, *a, **kw):
                raise RuntimeError("storage down")

        monkeypatch.setattr(ms, "_shared_store", lambda: _Boom())
        assert ms.store_task_result(
            title="t", prompt="p", answer="a", source="task:b",
        ) is None

    def test_gate_resolvers(self, monkeypatch):
        import src.agent.memory_store as ms

        assert ms.task_sink_enabled() is True
        assert ms.memory_hit_stats_enabled() is True
        monkeypatch.setenv("QDRANT_MEMORY_HIT_STATS", "0")
        assert ms.memory_hit_stats_enabled() is False
# ─── Agent._memory_block integration ─────────────────────────────

class TestMemoryBlockIntegration:
    """Agent._memory_block passes the exclusion + fires the touch."""

    def _fake_store(self, monkeypatch, rows):
        import src.agent.memory_store as ms

        calls = {}

        class _FakeStore:
            def search(self, query, top_k=5, **kw):
                calls["kw"] = kw
                return rows

            def touch_hits(self, ids):
                calls["touched"] = list(ids)

        monkeypatch.setattr(ms, "_shared_store", lambda: _FakeStore())
        return ms, calls

    def _agent(self):
        from src.agent.agent import Agent

        return Agent.__new__(Agent)

    def test_recall_excludes_and_touches(self, monkeypatch):
        rows = [{
            "id": "m1", "kind": "fact", "source": "manual",
            "content": "咖啡",
            "stored_at": "2026-09-15T10:00:00+08:00",
        }]
        ms, calls = self._fake_store(monkeypatch, rows)
        a = self._agent()
        block = a._memory_block("我最愛什麼?")
        assert "LONG-TERM MEMORY" in block
        assert calls["kw"].get("exclude_kinds") == ("task-result",)
        assert calls["touched"] == ["m1"]

    def test_task_result_rows_never_recalled(self, monkeypatch):
        from src.agent.memory_store import TASK_RESULT_KIND

        class _ExcludingStore:
            def search(self, query, top_k=5, **kw):
                excl = (kw or {}).get("exclude_kinds") or ()
                rows = [{
                    "id": "m9", "kind": TASK_RESULT_KIND,
                    "source": "task:x", "content": "舊結論",
                    "stored_at": "2026-10-01T10:00:00+08:00",
                }]
                return [r for r in rows if r["kind"] not in excl]

            def touch_hits(self, ids):
                pass

        import src.agent.memory_store as msmod

        monkeypatch.setattr(msmod, "_shared_store",
                            lambda: _ExcludingStore())
        a = self._agent()
        assert a._memory_block("任務結論?") == ""

    def test_prompt_mentions_task_result_discovery(self):
        from src.agent.agent import Agent

        prompt = Agent()._system_prompt(tools_enabled=True)
        assert "task-result" in prompt
        assert "search_memory" in prompt
# ─── surfacing: memory-local tools see hit fields + usage ────────

class TestListRecentUsage:
    def test_host_list_recent_has_usage_and_hits(self):
        from src.mcp_client.host import MemoryLocalHost

        class _FakeStore:
            def recent(self, limit=10, kind=None):
                return [{
                    "id": "m1", "kind": "fact", "source": "manual",
                    "stored_at": "2026-10-01T10:00:00+08:00",
                    "hit_count": 3,
                    "last_hit_at": "2026-10-06T09:00:00+08:00",
                    "content": "常被引用",
                }]

            def usage_report(self):
                return {
                    "total": 5, "never_hit": 4,
                    "never_hit_older_than_s": {
                        "secs": 2592000, "count": 2,
                    },
                }

        h = MemoryLocalHost(store=_FakeStore())
        out = h._list_recent({"limit": 10})
        row = out["memories"][0]
        assert row["hit_count"] == 3
        assert row["last_hit_at"]
        assert out["usage"]["never_hit"] == 4

    def test_host_usage_failure_keeps_list(self):
        from src.mcp_client.host import MemoryLocalHost

        class _FakeStore:
            def recent(self, limit=10, kind=None):
                return []

            def usage_report(self):
                raise RuntimeError("store down")

        h = MemoryLocalHost(store=_FakeStore())
        out = h._list_recent({})
        assert out["count"] == 0
        assert "usage" not in out

    def test_host_search_memory_rows_carry_hits(self):
        from src.mcp_client.host import MemoryLocalHost

        class _FakeStore:
            def search(self, query, top_k=5, **kw):
                return [{
                    "id": "m1", "kind": "fact", "source": "s",
                    "stored_at": "2026-10-01T10:00:00+08:00",
                    "hit_count": 7, "last_hit_at": "x",
                    "score": 0.8, "content": "c",
                }]

        h = MemoryLocalHost(store=_FakeStore())
        out = h._search_memory({"query": "q"})
        assert out["memories"][0]["hit_count"] == 7


# ─── run_task_once conclusion sink (store-NOT-recall) ────────────

class TestTaskSinkSite:
    def test_run_task_once_done_sinks_memory(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        import src.agent.task_runner as tr
        from src.mcp_client.host import TaskLocalHost

        tr.reset_tasks()
        tr.set_notify_callback(None)
        import src.agent.memory_store as ms

        seen = []

        class _SinkStore:
            def remember(self, text, kind="", source="",
                         session_id=""):
                seen.append({"kind": kind, "source": source,
                             "text": text,
                             "session_id": session_id})
                return {"id": "m"}

        monkeypatch.setattr(ms, "_shared_store", lambda: _SinkStore())

        class _FreshAgent:
            def chat_turn(self, **kw):
                return ("任務結論：已完成分析", "", [])

        h = TaskLocalHost(mcp_manager=object())
        monkeypatch.setattr(h, "_fresh_agent",
                            lambda llm=None: _FreshAgent())
        out = h._run_once({
            "title": "背景分析", "prompt": "做一份完整分析",
        })
        assert out.get("ok") is True, out
        deadline = time.monotonic() + 5.0
        while not seen and time.monotonic() < deadline:
            time.sleep(0.05)
        assert seen, "done task must sink its conclusion"
        rows = [r for r in seen if r["kind"] == "task-result"]
        assert rows, "the kind must be task-result"
        assert rows[0]["source"] == f"task:{out['task_id']}"
        assert "任務結論：已完成分析" in rows[0]["text"]