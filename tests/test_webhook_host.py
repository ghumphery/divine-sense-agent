"""Tests for the webhook trigger core (v0.9.25.0+).

Every trigger has its OWN URL + access key; HTTP calls fire the
pre-configured prompt — the event-driven counterpart to the
scheduler. All tests run against a REAL ThreadingHTTPServer on an
ephemeral loopback port with a STUB fire engine (no LLM, no
network beyond 127.0.0.1).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def wh_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    return tmp_path


def _make_trigger(mgr, **kw):
    from src.agent.webhook import WebhookTrigger

    defaults = dict(name="deploy-notify", prompt="處理:{payload}")
    defaults.update(kw)
    return mgr.save(WebhookTrigger(**defaults))


# ─── core: WebhookTrigger / WebhookManager ───────────────────


class TestTriggerDataclass:
    def test_auto_id_and_key(self, wh_home):
        from src.agent.webhook import WebhookTrigger

        w = WebhookTrigger(prompt="hi")
        assert w.id
        assert len(w.access_key) >= 32  # secrets.token_urlsafe(32)
        assert WebhookTrigger(prompt="hi").access_key != w.access_key

    def test_key_matches_constant_time(self, wh_home):
        from src.agent.webhook import WebhookTrigger

        w = WebhookTrigger(prompt="p")
        assert w.key_matches(w.access_key) is True
        assert w.key_matches("wrong") is False
        assert w.key_matches(None) is False
        assert w.key_matches("") is False

    def test_from_dict_backward_compat(self, wh_home):
        from src.agent.webhook import WebhookTrigger

        w = WebhookTrigger.from_dict({
            "id": "abc123", "name": "n", "prompt": "p",
        })
        assert w.id == "abc123"
        assert w.access_key  # generated when absent
        assert w.enabled is True

    def test_check_lifecycle_gates(self, wh_home):
        from src.agent.webhook import WebhookTrigger

        assert WebhookTrigger(prompt="p").check_lifecycle() is None
        assert WebhookTrigger(prompt="p", enabled=False).check_lifecycle() \
            == "disabled"
        assert WebhookTrigger(prompt="p", status="exhausted").check_lifecycle() \
            == "exhausted"
        assert WebhookTrigger(
            prompt="p", max_fires=3, fire_count=3,
        ).check_lifecycle() == "exhausted"
        assert WebhookTrigger(
            prompt="p", expires_at="2000-01-01T00:00:00",
        ).check_lifecycle() == "expired"
        # future expiry still fine
        assert WebhookTrigger(
            prompt="p", expires_at="2999-01-01T00:00:00",
        ).check_lifecycle() is None


class TestManager:
    def test_save_get_list_delete(self, wh_home):
        from src.agent.webhook import WebhookManager

        mgr = WebhookManager()
        w = _make_trigger(mgr)
        assert mgr.get(w.id).name == "deploy-notify"
        assert len(mgr.list()) == 1
        assert mgr.delete(w.id) is True
        assert mgr.get(w.id) is None
        assert mgr.delete(w.id) is False

    def test_save_requires_prompt(self, wh_home):
        from src.agent.webhook import WebhookManager, WebhookTrigger

        with pytest.raises(ValueError):
            WebhookManager().save(WebhookTrigger(prompt="  "))

    def test_set_enabled_roundtrip(self, wh_home):
        from src.agent.webhook import WebhookManager

        mgr = WebhookManager()
        w = _make_trigger(mgr)
        mgr.set_enabled(w.id, False)
        assert mgr.get(w.id).enabled is False
        mgr.set_enabled(w.id, True)
        assert mgr.get(w.id).enabled is True
        # re-enable clears the manual "disabled" status
        cur = mgr.get(w.id)
        cur.status = "disabled"
        mgr.save(cur)
        out = mgr.set_enabled(w.id, True)
        assert out.status == "active"

    def test_find_by_name(self, wh_home):
        from src.agent.webhook import WebhookManager

        mgr = WebhookManager()
        _make_trigger(mgr, name="unique-name")
        assert mgr.find_by_name("unique-name").prompt == "處理:{payload}"
        assert mgr.find_by_name("nope") is None

    def test_history_ring_buffer(self, wh_home):
        from src.agent.webhook import MAX_HISTORY, WebhookManager

        mgr = WebhookManager()
        w = _make_trigger(mgr)
        for i in range(MAX_HISTORY + 5):
            mgr.append_history(w.id, {"ts": i, "answer": str(i)})
        rows = mgr.get_history(w.id, limit=100)
        assert len(rows) == MAX_HISTORY
        assert rows[-1]["ts"] == MAX_HISTORY + 4  # newest kept

    def test_path_traversal_rejected(self, wh_home):
        from src.agent.webhook import WebhookManager

        mgr = WebhookManager()
        with pytest.raises(ValueError):
            mgr.get("../evil")


# ─── HTTP server round-trips (real socket, stub fire) ────────


PORT = 18331


@pytest.fixture()
def http_srv(wh_home):
    from src.agent.webhook import WebhookManager, WebhookServer

    mgr = WebhookManager()
    calls = []

    def fire(w, payload=None):
        from src.agent.webhook import _compose_prompt

        calls.append((w.id, payload))
        # mimic the real engine's lifecycle stamp so max_fires /
        # expiry gates behave across sequential HTTP fires.
        fresh = mgr.get(w.id)
        if fresh is not None:
            fresh.fire_count += 1
            try:
                mgr.save(fresh)
            except Exception:
                pass
        return {
            "ok": True, "trigger_id": w.id,
            "answer": f"done:{_compose_prompt(w, payload)[:30]}",
            "sources": [], "session_id": "s1",
            "fire_count": w.fire_count + 1, "status": "active",
            "duration_s": 0.05,
        }

    srv = WebhookServer(manager=mgr, fire=fire, host="127.0.0.1",
                        port=PORT)
    srv.start()
    import time

    for _ in range(50):
        if srv.is_running():
            break
        time.sleep(0.05)
    yield mgr, srv, calls
    srv.stop()


def _hit(path, method="GET", body=None, headers=None, port=PORT):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class TestHttpServer:
    def test_healthz(self, http_srv):
        code, body = _hit("/healthz")
        assert code == 200 and body["ok"] is True

    def test_fire_with_payload_replaces_placeholder(self, http_srv):
        mgr, srv, calls = http_srv
        w = _make_trigger(mgr, prompt="處理:{payload}")
        code, body = _hit(f"/trigger/{w.id}?key={w.access_key}",
                          "POST", {"payload": "hello"})
        assert code == 200 and body["ok"] is True
        assert body["answer"] == "done:處理:hello"
        assert calls[-1] == (w.id, "hello")

    def test_wrong_key_401(self, http_srv):
        mgr, _, _ = http_srv
        w = _make_trigger(mgr)
        code, _ = _hit(f"/trigger/{w.id}?key=bad")
        assert code == 401

    def test_missing_key_401(self, http_srv):
        mgr, _, _ = http_srv
        w = _make_trigger(mgr)
        code, _ = _hit(f"/trigger/{w.id}")
        assert code == 401

    def test_unknown_trigger_404(self, http_srv):
        code, _ = _hit("/trigger/nope123?key=x")
        assert code == 404

    def test_unknown_path_404(self, http_srv):
        code, _ = _hit("/nope?key=x")
        assert code == 404

    def test_disabled_403(self, http_srv):
        mgr, _, _ = http_srv
        w = _make_trigger(mgr)
        mgr.set_enabled(w.id, False)
        code, body = _hit(f"/trigger/{w.id}?key={w.access_key}")
        assert code == 403
        assert "not fireable" in body["error"]

    def test_max_fires_403(self, http_srv):
        mgr, _, _ = http_srv
        w = _make_trigger(mgr, max_fires=1, prompt="go")
        code, _ = _hit(f"/trigger/{w.id}?key={w.access_key}")  # fires
        assert code == 200
        code, body = _hit(f"/trigger/{w.id}?key={w.access_key}")
        assert code == 403
        assert "exhausted" in body["error"]

    def test_header_auth(self, http_srv):
        mgr, _, _ = http_srv
        w = _make_trigger(mgr, prompt="go")
        code, body = _hit(
            f"/trigger/{w.id}", "POST", {"payload": "h"},
            {"X-Access-Key": w.access_key},
        )
        assert code == 200 and body["ok"] is True

    def test_payload_placeholder_and_append(self, wh_home):
        from src.agent.webhook import WebhookTrigger, _compose_prompt

        w = WebhookTrigger(prompt="處理:{payload}")
        assert _compose_prompt(w, "x") == "處理:x"
        w2 = WebhookTrigger(prompt="plain")
        assert _compose_prompt(w2, "y").endswith("[Webhook payload]\ny")
        assert _compose_prompt(w2, None) == "plain"

    def test_plain_text_body(self, http_srv):
        mgr, _, calls = http_srv
        w = _make_trigger(mgr, prompt="go")
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/trigger/{w.id}?key={w.access_key}",
            method="POST", data=b"raw text payload",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200
        assert calls[-1][1] == "raw text payload"

    def test_async_202(self, http_srv):
        mgr, _, calls = http_srv
        w = _make_trigger(mgr, prompt="go")
        code, body = _hit(
            f"/trigger/{w.id}?key={w.access_key}&async=1", "POST",
            {"payload": "bg"},
        )
        assert code == 202 and body["queued"] is True
        import time

        for _ in range(40):
            if calls:
                break
            time.sleep(0.05)
        assert calls and calls[-1][1] == "bg"

    def test_fire_failure_500(self, http_srv):
        mgr, srv, _ = http_srv
        w = _make_trigger(mgr, prompt="go")
        srv.fire = lambda t, p=None: {"ok": False, "error": "boom"}
        code, body = _hit(f"/trigger/{w.id}?key={w.access_key}")
        assert code == 500 and body["ok"] is False


# ─── make_webhook_fire (session + history + lifecycle) ───────


class _FakeAgent:
    def __init__(self, fail=False, answer="LLM answer"):
        self.fail = fail
        self.answer = answer

    def chat_turn(self, question, history=None, use_tools=True, **kw):
        if self.fail:
            raise RuntimeError("qdrant down")
        return (self.answer, "ctx", ["doc.txt"])


class TestFireEngine:
    def test_fire_writes_session_and_history(self, wh_home):
        from src.agent.session import SessionManager
        from src.agent.webhook import WebhookManager, make_webhook_fire

        mgr = WebhookManager()
        w = _make_trigger(mgr)
        fire = make_webhook_fire(lambda: _FakeAgent())
        out = fire(w, "payload-x")
        assert out["ok"] is True
        assert out["answer"] == "LLM answer"
        assert out["fire_count"] == 1
        # session persisted with a webhook- prefix
        sm = SessionManager()
        sess = sm.load(out["session_id"])
        assert sess.id.startswith("webhook-")
        assert len(sess.messages) == 2  # user + assistant
        # history row written
        rows = mgr.get_history(w.id)
        assert len(rows) == 1
        assert rows[0]["payload"] == "payload-x"
        # lifecycle stamped
        fresh = mgr.get(w.id)
        assert fresh.fire_count == 1
        assert fresh.status == "active"
        assert fresh.last_session_id == sess.id

    def test_fire_failure_stamps_failed(self, wh_home):
        from src.agent.webhook import WebhookManager, make_webhook_fire

        mgr = WebhookManager()
        w = _make_trigger(mgr)
        fire = make_webhook_fire(lambda: _FakeAgent(fail=True))
        out = fire(w)
        assert out["ok"] is False
        fresh = mgr.get(w.id)
        assert fresh.status == "failed"
        assert fresh.last_error and "RuntimeError" in fresh.last_error
        assert fresh.check_lifecycle() == "failed"

    def test_max_fires_auto_disable(self, wh_home):
        from src.agent.webhook import WebhookManager, make_webhook_fire

        mgr = WebhookManager()
        w = _make_trigger(mgr, max_fires=1, prompt="go")
        fire = make_webhook_fire(lambda: _FakeAgent())
        fire(w)
        fresh = mgr.get(w.id)
        assert fresh.status == "exhausted"
        assert fresh.enabled is False
        assert fresh.check_lifecycle() == "exhausted"


# ─── LocalHost surface + manager wiring ──────────────────────


class TestHostSurface:
    def test_ten_tools_exposed(self, wh_home):
        from src.mcp_client.host import WebhookLocalHost

        host = WebhookLocalHost()
        tools = host.list_tools()
        assert [t.name for t in tools] == [
            "add_webhook_trigger", "list_webhook_triggers",
            "get_webhook_trigger", "get_webhook_trigger_url",
            "delete_webhook_trigger", "enable_webhook_trigger",
            "disable_webhook_trigger", "run_webhook_trigger_now",
            "list_webhook_trigger_runs", "get_webhook_trigger_errors",
        ]
        assert all(t.server_name == "webhook-local" for t in tools)

    def test_add_returns_url_and_key_once(self, wh_home):
        from src.mcp_client.host import WebhookLocalHost

        host = WebhookLocalHost()
        out = host._add({"name": "ci", "prompt": "run ci {payload}"})
        assert out["ok"] is True
        assert out["url"].startswith("http://")
        assert f"/trigger/{out['id']}" in out["url"]
        assert out["access_key"] in out["url"]
        assert "只顯示這一次" in out["note"]

    def test_list_masks_key(self, wh_home):
        from src.mcp_client.host import WebhookLocalHost

        host = WebhookLocalHost()
        host._add({"name": "ci", "prompt": "p"})
        listed = host._list({})
        row = listed["triggers"][0]
        assert "…" in row["url"]  # masked, never the full key

    def test_url_reveal(self, wh_home):
        from src.mcp_client.host import WebhookLocalHost

        host = WebhookLocalHost()
        out = host._add({"name": "ci", "prompt": "p"})
        hidden = host._url({"trigger_id": out["id"]})
        assert hidden["access_key"] is None
        revealed = host._url({"trigger_id": out["id"], "reveal": True})
        assert revealed["access_key"] == out["access_key"]

    def test_enable_disable_delete_and_errors(self, wh_home):
        from src.mcp_client.host import WebhookLocalHost

        host = WebhookLocalHost()
        out = host._add({"name": "ci", "prompt": "p"})
        tid = out["id"]
        assert host._disable({"trigger_id": tid})["enabled"] is False
        assert host._enable({"trigger_id": tid})["enabled"] is True
        assert host._delete({"trigger_id": tid})["deleted"] is True
        assert "error" in host._get({"trigger_id": tid})
        # by-name resolution
        host._add({"name": "by-name", "prompt": "p"})
        assert host._get({"trigger_id": "by-name"})["ok"] is True

    def test_run_now_requires_fire_engine(self, wh_home):
        from src.mcp_client.host import WebhookLocalHost

        host = WebhookLocalHost()
        out = host._add({"name": "ci", "prompt": "p"})
        body = host._run_now({"trigger_id": out["id"]})
        assert "error" in body
        assert "fire engine" in body["error"]

    def test_run_now_with_fire_engine(self, wh_home):
        from src.mcp_client.host import WebhookLocalHost

        host = WebhookLocalHost()
        out = host._add({"name": "ci", "prompt": "p"})
        host.attach_fire(
            lambda t, p=None: {"ok": True, "answer": "fired"},
        )
        body = host._run_now({"trigger_id": out["id"], "payload": "x"})
        assert body["ok"] is True and body["answer"] == "fired"


class TestManagerWiring:
    def test_from_config_registers_webhook_host(self, wh_home):
        from src.mcp_client.manager import McpManager

        cfg = wh_home / "mcp.json"
        cfg.write_text('{"servers": []}', encoding="utf-8")
        mgr = McpManager.from_config(str(cfg))
        names = {t.name for t in mgr.list_tools()}
        assert "add_webhook_trigger" in names
        assert "run_webhook_trigger_now" in names
        assert mgr._webhook_local_host is not None
        assert mgr._webhook_local_host.SERVER_NAME == "webhook-local"
        mgr.close_all()

    def test_setters_push_callbacks_down(self, wh_home):
        from src.mcp_client.host import WebhookLocalHost
        from src.mcp_client.manager import McpManager

        mgr = McpManager()
        cb1, cb2 = MagicMock(), MagicMock()
        mgr._webhook_local_host = MagicMock(spec=WebhookLocalHost)
        mgr.set_webhook_auto_start_callback(cb1)
        mgr.set_webhook_fire_callback(cb2)
        assert mgr.webhook_auto_start_callback is cb1
        assert mgr.webhook_fire_callback is cb2
        mgr._webhook_local_host.attach_auto_start.assert_called_once_with(cb1)
        mgr._webhook_local_host.attach_fire.assert_called_once_with(cb2)


# ─── stdio parity + system prompt ────────────────────────────


class TestStdioParity:
    def test_specs_match_host(self, wh_home):
        from src.mcp_client.host import WebhookLocalHost
        from src.mcp_server.webhook_server import WebhookMCPServer

        server = WebhookMCPServer()
        host_names = [t.name for t in WebhookLocalHost().list_tools()]
        server_names = [t.name for t in server._tool_specs()]
        assert server_names == host_names


class TestSystemPrompt:
    def _agent(self):
        from src.agent.agent import Agent

        a = Agent()
        a._client = MagicMock()
        a._model = "mock-model"
        a._provider = "mock"
        return a

    def test_webhook_block_present_with_tools(self):
        prompt = self._agent()._system_prompt(tools_enabled=True)
        assert "add_webhook_trigger" in prompt
        assert "run_webhook_trigger_now" in prompt
        assert "ONLY ONCE" in prompt  # key-shown-once honesty rule

    def test_webhook_block_absent_legacy(self):
        prompt = self._agent()._system_prompt(tools_enabled=False)
        assert "add_webhook_trigger" not in prompt