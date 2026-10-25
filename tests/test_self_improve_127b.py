# -*- coding: utf-8 -*-
"""v0.9.127.0 — self-improvement items ③④⑤: memory maintenance,
corrective-turn verifier alt-pin, ToolRoute pattern overrides."""
import time as _time

import pytest


@pytest.fixture()
def rb_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(tmp_path))
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    for v in ("QDRANT_TOOLROUTE_OVERRIDES", "QDRANT_TOOLROUTE_OVERRIDES_FILE",
              "QDRANT_MEMORY_MAINTENANCE", "QDRANT_VERIFIER_ALT_PIN"):
        monkeypatch.delenv(v, raising=False)
    yield tmp_path


class _Pt:
    def __init__(self, pid, payload):
        self.id = pid
        self.payload = payload


class _ScrollClient:
    def __init__(self, payloads):
        self.payloads = payloads

    def scroll(self, collection_name, limit, with_payload, offset):
        return ([_Pt("p%d" % i, p) for i, p in enumerate(self.payloads)],
                None)


def _memstore(payloads, client=None):
    from src.agent.memory_store import MemoryStore

    ms = MemoryStore.__new__(MemoryStore)
    ms._client = client or _ScrollClient(payloads)
    ms.collection = "memory_docs"
    return ms


class TestMemoryCleanupCandidates:
    def test_filters_hit_and_age(self, rb_home):
        now = _time.time()
        ms = _memstore([
            {"text": "舊零命中", "kind": "query",
             "stored_at_epoch": now - 40 * 86400, "hit_count": 0},
            {"text": "新的零命中", "kind": "query",
             "stored_at_epoch": now - 100, "hit_count": 0},
            {"text": "有命中的", "kind": "fact",
             "stored_at_epoch": now - 90 * 86400, "hit_count": 3},
        ])
        rep = ms.cleanup_candidates(older_than_s=2_592_000, limit=50)
        assert rep["ok"] and rep["total_never_hit"] == 1
        assert rep["candidates"][0]["text"].startswith("舊零命中")
        assert rep["candidates"][0]["id"].startswith("p")

    def test_error_report_not_raise(self, rb_home):
        class _Boom:
            def scroll(self, *a, **kw):
                raise RuntimeError("down")

        ms = _memstore([], client=_Boom())
        rep = ms.cleanup_candidates()
        assert "error" in rep

    def test_host_tool_dry_run(self, rb_home):
        from src.mcp_client.host import MemoryLocalHost

        now = _time.time()
        h = MemoryLocalHost(store=_memstore([
            {"text": "舊零命中", "kind": "query",
             "stored_at_epoch": now - 40 * 86400, "hit_count": 0},
        ]))
        names = [t.name for t in h.list_tools()]
        assert "suggest_memory_cleanup" in names
        out = h._suggest_cleanup({})
        assert out["ok"] is True and out["candidates"]
        assert "suggest only" in out["usage_note"]
        # and the point payloads still exist (dry run — nothing deleted)
        assert h.store.cleanup_candidates()["total_never_hit"] == 1


class _CandAgent:
    def __init__(self, cands):
        self._cands = cands

    def _provider_candidates(self):
        return self._cands


class TestVerifierAltPin:
    def test_whitelisted_first_fallback(self, rb_home):
        from src.agent.scheduler import _alt_verifier_pin

        a = _CandAgent([("openrouter", "m1", object()),
                        ("siliconflow", "s1", object()),
                        ("ollama", "glm-5.3-flash:cloud", object())])
        # cands[0] = the PRIMARY (skipped); siliconflow is not task_llm-
        # pinnable → the first eligible fallback is ollama.
        assert _alt_verifier_pin(a) == "ollama:glm-5.3-flash:cloud"

    def test_single_provider_none(self, rb_home):
        from src.agent.scheduler import _alt_verifier_pin

        assert _alt_verifier_pin(
            _CandAgent([("openrouter", "m1", 1)])) is None

    def test_broken_agent_none(self, rb_home):
        from src.agent.scheduler import _alt_verifier_pin

        assert _alt_verifier_pin(object()) is None

    def test_env_off(self, rb_home, monkeypatch):
        from src.agent import scheduler as sched_mod

        monkeypatch.setenv("QDRANT_VERIFIER_ALT_PIN", "off")
        assert sched_mod._verifier_alt_pin_enabled() is False
        monkeypatch.delenv("QDRANT_VERIFIER_ALT_PIN")
        assert sched_mod._verifier_alt_pin_enabled() is True


class TestToolrouteOverrides:
    def test_add_parse_match_lifecycle(self, rb_home):
        from src.agent import toolroute_overrides as tro

        out = tro.add_override(
            "firefox_craw", "加密貨幣|crypto", "加密貨幣抓取",
            evidence="三次 crypto 題都漏掛",
        )
        assert out["ok"] and out["id"] == "t1"
        tro.reset_overrides_cache()
        hits = tro.matches("幫我抓 crypto 新聞")
        assert hits and hits[0]["server"] == "firefox_craw"
        assert tro.matches("股票報價") == []

    def test_bad_regex_fail_closed(self, rb_home):
        from src.agent import toolroute_overrides as tro

        assert "regex" in tro.add_override("x", "(未閉合")["error"]
        tro.reset_overrides_cache()
        assert tro.load_overrides() == []

    def test_snapshot_ring_grows(self, rb_home):
        from src.agent import toolroute_overrides as tro

        tro.add_override("a-local", "aaa")
        resp = tro.add_override("b-local", "bbb")
        tro.reset_overrides_cache()
        assert resp["ok"] and resp["id"] == "t2"
        # the first write CREATED the file (nothing to snapshot yet);
        # the second write snapshots it = 1 entry in the ring
        assert tro.overrides_history()["snapshots"] == 1

    def test_off_mode(self, rb_home, monkeypatch):
        from src.agent import toolroute_overrides as tro

        tro.add_override("c-local", "ccc")
        tro.reset_overrides_cache()
        monkeypatch.setenv("QDRANT_TOOLROUTE_OVERRIDES", "off")
        assert tro.matches("ccc") == []
        assert "off" in tro.add_override("d-local", "ddd")["error"]

    def test_routing_integration_shadow_vs_on(self, rb_home, monkeypatch):
        from src.agent.agent import _toolroute_servers_for
        from src.agent import toolroute_overrides as tro

        tro.add_override("firefox_craw", "加密貨幣|crypto")
        tro.reset_overrides_cache()
        q = "幫我抓加密貨幣新聞"
        # shadow (default): NOT mounted
        servers, _ = _toolroute_servers_for(None, question=q)
        assert "firefox_craw" not in servers
        # on: unioned in
        monkeypatch.setenv("QDRANT_TOOLROUTE_OVERRIDES", "on")
        servers2, _ = _toolroute_servers_for(None, question=q)
        assert "firefox_craw" in servers2
        # off: gone again
        monkeypatch.setenv("QDRANT_TOOLROUTE_OVERRIDES", "off")
        servers3, _ = _toolroute_servers_for(None, question=q)
        assert "firefox_craw" not in servers3

    def test_host_tools_confirm_gated(self, rb_home):
        from src.mcp_client.host import RulebookLocalHost

        h = RulebookLocalHost()
        names = [t.name for t in h.list_tools()]
        assert "toolroute_override_add" in names
        assert "toolroute_override_list" in names
        no_confirm = h._op_tro_add(
            {"server": "x-local", "patterns": "abc", "confirm": False})
        assert "confirm" in no_confirm.get("error", "")


class TestGuiMemoryMaintenanceSchedule:
    def test_optin_creates_once_and_starts(self, rb_home, monkeypatch):
        from gui.api import Api

        monkeypatch.setenv("QDRANT_MEMORY_MAINTENANCE", "on")

        class _Stub:
            started = False

            def start_scheduler(self):
                self.started = True

        stub = _Stub()
        api = Api.__new__(Api)
        api._ensure_memory_maintenance_schedule(stub)
        from src.agent.scheduler import ScheduleManager

        mgr = ScheduleManager()
        s = mgr.find_by_name("記憶維護（每週）")
        assert s is not None and s.enabled and s.cron == "0 9 * * 1"
        assert "suggest_memory_cleanup" in s.prompt
        assert stub.started is True
        # second run = idempotent (same schedule id, no rebuild)
        stub2 = _Stub()
        api._ensure_memory_maintenance_schedule(stub2)
        again = mgr.find_by_name("記憶維護（每週）")
        assert again is not None and again.id == s.id

    def test_env_off_noop(self, rb_home, monkeypatch):
        from gui.api import Api
        from src.agent.scheduler import ScheduleManager

        Api.__new__(Api)._ensure_memory_maintenance_schedule(object())
        assert ScheduleManager().list() == []