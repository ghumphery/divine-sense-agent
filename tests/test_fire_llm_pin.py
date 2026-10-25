# -*- coding: utf-8 -*-
"""v0.9.111.0 — fire-time llm pin：Schedule.llm / WebhookTrigger.llm。

鎖定：
* dataclass 新欄位 + from_dict 舊檔相容（缺 key → ""）。
* make_agent_on_fire / make_webhook_fire：pin 經 factory(llm=pin)
  生效；無 pin = 零參 legacy factory 呼叫（byte-identical）；
  無效 pin → 降級預設鏈 + 警告（排程韌性優先於 pin 嚴格性）；
  task registry 帶 pin 欄位。
* ADD 端 fail-closed：host._add / stdio schema 對 catalog 驗證，
  壞 pin 是乾淨錯誤而非壞排程。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    return tmp_path


class _FireAgent:
    def __init__(self, answer="fire done"):
        self.answer = answer
        self.kwargs = None

    def chat_turn(self, **kw):
        self.kwargs = kw
        return (self.answer, "", [])


def _pinned_validate(monkeypatch, ok=True, provider="openrouter",
                     model="z-ai/glm-4.6"):
    from src.agent import task_llm

    if ok:
        monkeypatch.setattr(
            task_llm, "validate_task_llm",
            lambda s: {"ok": True, "provider": provider,
                       "model": model},
        )
    else:
        monkeypatch.setattr(
            task_llm, "validate_task_llm",
            lambda s: {"ok": False, "error": "model not in catalog",
                       "provider": "openrouter", "model": str(s)},
        )


def _schedule(sid="fs1", llm=""):
    from src.agent.scheduler import Schedule

    return Schedule(
        id=sid, name="每日股訊", prompt="分析今天大盤",
        schedule_type="interval_seconds", interval_seconds=60,
        enabled=True, llm=llm,
    )


def _rows(tr):
    return list(tr.list_tasks(limit=50).get("tasks") or [])


# ─── scheduler fire pin ──────────────────────────────────────────

class TestScheduleFirePin:
    def test_pin_factory_honoured(self, tmp_home, monkeypatch):
        import src.agent.task_runner as tr

        tr.reset_tasks()
        _pinned_validate(monkeypatch)
        from src.agent.scheduler import make_agent_on_fire

        got = {}

        def factory(llm=None):
            got["llm"] = llm
            return _FireAgent()

        make_agent_on_fire(factory)(_schedule("ps1", "openrouter:x"))
        assert got["llm"] == "openrouter:z-ai/glm-4.6"
        assert any(r["llm"] == "openrouter:z-ai/glm-4.6"
                   for r in _rows(tr))

    def test_no_pin_is_zero_arg_factory(self, tmp_home, monkeypatch):
        from src.agent.scheduler import make_agent_on_fire

        got = {}

        def factory(llm=None):
            got["llm"] = llm
            return _FireAgent()

        make_agent_on_fire(factory)(_schedule("ps2", llm=""))
        assert got["llm"] is None
class TestScheduleFirePinDegrade:
    def test_invalid_pin_degrades(self, tmp_home, monkeypatch):
        import src.agent.task_runner as tr

        tr.reset_tasks()
        _pinned_validate(monkeypatch, ok=False)
        from src.agent.scheduler import make_agent_on_fire

        got = {}

        def factory(llm=None):
            got["llm"] = llm
            return _FireAgent("照跑")

        make_agent_on_fire(factory)(_schedule("pd1", "openrouter:dead"))
        # DEGRADED (never refused): the fire ran on the default chain
        assert got["llm"] is None
        assert any(r["status"] == "done" for r in _rows(tr))

    def test_validate_probe_failure_degrades(self, tmp_home,
                                             monkeypatch):
        from src.agent import task_llm
        from src.agent.scheduler import make_agent_on_fire

        def _boom(_spec):
            raise RuntimeError("catalog exploded")

        monkeypatch.setattr(task_llm, "validate_task_llm", _boom)
        got = {}

        def factory(llm=None):
            got["llm"] = llm
            return _FireAgent()

        make_agent_on_fire(factory)(_schedule("pd2", "openrouter:x"))
        assert got["llm"] is None

    def test_from_dict_defaults_and_roundtrip(self, tmp_home):
        from src.agent.scheduler import Schedule, ScheduleManager

        s = _schedule("rd1", "ollama:gpt-oss:120b-cloud")
        ScheduleManager().save(s)
        back = Schedule.from_dict(
            ScheduleManager().get("rd1").to_dict())
        assert back.llm == "ollama:gpt-oss:120b-cloud"
        # old on-disk JSON without the key loads with llm=""
        old = Schedule.from_dict({
            "id": "old1", "name": "n", "prompt": "p",
            "schedule_type": "interval_seconds",
            "interval_seconds": 60,
        })
        assert old.llm == ""
# ─── webhook fire pin ────────────────────────────────────────────

class TestWebhookFirePin:
    def _trigger(self, llm=""):
        from src.agent.webhook import WebhookTrigger

        return WebhookTrigger(
            id="wp1", name="行情推送", prompt="推送最新行情", llm=llm,
        )

    def test_pin_factory_honoured(self, tmp_home, monkeypatch):
        _pinned_validate(monkeypatch)
        from src.agent.webhook import make_webhook_fire

        got = {}

        def factory(llm=None):
            got["llm"] = llm
            return _FireAgent()

        out = make_webhook_fire(factory)(self._trigger("openrouter:x"))
        assert got["llm"] == "openrouter:z-ai/glm-4.6"
        assert (out.get("ok") is True
                or out.get("answer") == "fire done")

    def test_invalid_pin_degrades(self, tmp_home, monkeypatch):
        _pinned_validate(monkeypatch, ok=False)
        from src.agent.webhook import make_webhook_fire

        got = {}

        def factory(llm=None):
            got["llm"] = llm
            return _FireAgent("webhook done")

        out = make_webhook_fire(factory)(
            self._trigger("openrouter:gone"))
        assert got["llm"] is None
        assert out.get("answer") == "webhook done"

    def test_trigger_from_dict_defaults(self, tmp_home):
        from src.agent.webhook import WebhookTrigger, WebhookManager

        w = self._trigger("ollama:gpt-oss:120b-cloud")
        WebhookManager().save(w)
        back = WebhookTrigger.from_dict(
            WebhookManager().get("wp1").to_dict())
        assert back.llm == "ollama:gpt-oss:120b-cloud"
# ─── ADD-path fail-closed validation ─────────────────────────────

class TestAddPathPins:
    def test_scheduler_host_add_valid_pin(self, tmp_home, monkeypatch):
        from src.mcp_client.host import SchedulerLocalHost
        from src.agent.scheduler import ScheduleManager

        pin = "ollama:gpt-oss:120b-cloud"
        _pinned_validate(monkeypatch, provider="ollama",
                         model=pin.split(":", 1)[1])
        r = SchedulerLocalHost()._add({
            "prompt": "每小時檢查", "interval_seconds": 3600,
            "llm": pin,
        })
        assert r.get("ok") is True, r
        assert ScheduleManager().get(r["id"]).llm == pin

    def test_scheduler_host_add_bad_pin_rejected(
        self, tmp_home, monkeypatch,
    ):
        from src.mcp_client.host import SchedulerLocalHost
        from src.agent import task_llm

        monkeypatch.setattr(
            task_llm, "coerce_llm_pin",
            lambda spec: (
                None, "task llm pin rejected: model not in catalog",
            ),
        )
        r = SchedulerLocalHost()._add({
            "prompt": "x", "interval_seconds": 60, "llm": "bad:input",
        })
        assert "task llm pin rejected" in (r.get("error") or "")
    def test_webhook_host_add_valid_pin(self, tmp_home, monkeypatch):
        from src.mcp_client.host import WebhookLocalHost
        from src.agent.webhook import WebhookManager

        pin = "openrouter:z-ai/glm-4.6"
        _pinned_validate(monkeypatch)
        r = WebhookLocalHost()._add({
            "name": "w", "prompt": "p {payload}", "llm": pin,
        })
        assert r.get("ok") is True, r
        assert WebhookManager().get(r["id"]).llm == pin

    def test_webhook_host_add_bad_pin_rejected(
        self, tmp_home, monkeypatch,
    ):
        from src.mcp_client.host import WebhookLocalHost
        from src.agent import task_llm

        monkeypatch.setattr(
            task_llm, "coerce_llm_pin",
            lambda spec: (None, "task llm pin rejected: nope"),
        )
        r = WebhookLocalHost()._add({
            "name": "w2", "prompt": "p", "llm": "siliconflow:x",
        })
        assert "task llm pin rejected" in (r.get("error") or "")

    def test_stdio_and_host_schemas_have_llm(self):
        from src.mcp_server.scheduler_server import _build_tool_specs
        from src.mcp_client.host import WebhookLocalHost

        specs = {t.name: t for t in _build_tool_specs()}
        assert "llm" in specs["add_scheduled_task"] \
            .inputSchema["properties"]
        h = WebhookLocalHost()
        wadd = next(t for t in h.list_tools()
                    if t.name == "add_webhook_trigger")
        assert "llm" in wadd.input_schema["properties"]


# ─── CLI flag source guards ──────────────────────────────────────

class TestCliLlmFlag:
    def test_flag_and_validation_wired(self):
        src = open("cli.py", encoding="utf-8").read()
        assert '"--llm", dest="llm"' in src
        assert "invalid --llm" in src
        assert "llm=llm_pin" in src