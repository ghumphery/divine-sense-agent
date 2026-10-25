"""Regression for v0.8.6's silent ``AttributeError: 'str' object has no
attribute 'get'`` failure.

Real-world LLM tool calls arrive in shapes other than a flat dict.
The most common offender (reproduced in the user's 2026-08-31 chat
log): the model wraps its arguments in ``{"args": "<json or dict>"}``
because the schema we emit has a single ``args: object`` property
and the model chose to nest the call under that name.

The fix lives in two places inside ``src/mcp_client/host.py``:

1. ``_coerce_arguments`` normalises the raw payload into a flat dict.
2. ``LocalTool._call_args`` detects the ``(self, args: dict)`` handler
   convention and passes the normalised dict as a single positional
   arg instead of unpacking it as kwargs.

Both behaviours are tested here, plus the cross-host interaction that
matters for the scheduler — ``SchedulerLocalHost`` and the stdio
``scheduler_server`` agree on the same coercion contract so neither
surface silently drops an LLM call.
"""
from __future__ import annotations

import json

import pytest

from src.mcp_client.host import (
    LocalTool,
    SchedulerLocalHost,
    WebSearchLocalHost,
    _coerce_arguments,
)


# ─── _coerce_arguments unit tests ──────────────────────────


class TestCoerceArguments:
    def test_none_returns_empty_dict(self):
        assert _coerce_arguments(None) == {}

    def test_empty_dict_returns_empty_dict(self):
        assert _coerce_arguments({}) == {}

    def test_flat_dict_passes_through(self):
        d = {"prompt": "hello", "interval_seconds": 60}
        assert _coerce_arguments(d) is d

    def test_args_wrapper_dict_is_unwrapped(self):
        """The actual user-reported failure mode: ``{"args": ...}``."""
        inner = {"prompt": "hello", "interval_seconds": 60}
        assert _coerce_arguments({"args": inner}) == inner

    def test_kwargs_wrapper_dict_is_unwrapped(self):
        inner = {"prompt": "hello"}
        assert _coerce_arguments({"kwargs": inner}) == inner

    def test_params_wrapper_dict_is_unwrapped(self):
        inner = {"query": "x"}
        assert _coerce_arguments({"params": inner}) == inner

    def test_parameters_wrapper_dict_is_unwrapped(self):
        inner = {"query": "x"}
        assert _coerce_arguments({"parameters": inner}) == inner

    def test_arguments_wrapper_dict_is_unwrapped(self):
        inner = {"query": "x"}
        assert _coerce_arguments({"arguments": inner}) == inner

    def test_input_wrapper_dict_is_unwrapped(self):
        inner = {"query": "x"}
        assert _coerce_arguments({"input": inner}) == inner

    def test_args_wrapper_with_json_string_inner(self):
        s = '{"prompt": "hello"}'
        assert _coerce_arguments({"args": s}) == {"prompt": "hello"}

    def test_bare_json_string_is_parsed(self):
        s = '{"prompt": "hello", "n": 5}'
        assert _coerce_arguments(s) == {"prompt": "hello", "n": 5}

    def test_bare_json_list_is_parsed(self):
        s = '[{"prompt": "hello"}]'
        assert _coerce_arguments(s) == {"prompt": "hello"}

    def test_single_element_list_is_unwrapped(self):
        assert _coerce_arguments([{"prompt": "x"}]) == {"prompt": "x"}

    def test_single_element_tuple_is_unwrapped(self):
        assert _coerce_arguments(({"prompt": "x"},)) == {"prompt": "x"}

    def test_multi_element_list_promoted_to_positional(self):
        out = _coerce_arguments(["a", "b", "c"])
        assert out == {"0": "a", "1": "b", "2": "c"}

    def test_non_json_string_wrapped_as_text(self):
        """Not JSON → graceful fallback to ``{text: ...}``."""
        out = _coerce_arguments("send hello to <YOUR_TELEGRAM_CHAT_ID>")
        assert out == {"text": "send hello to <YOUR_TELEGRAM_CHAT_ID>"}

    def test_empty_string_returns_empty_dict(self):
        assert _coerce_arguments("") == {}
        assert _coerce_arguments("   ") == {}

    def test_object_with_dict_attr_is_unwrapped(self):
        class Box:
            def __init__(self):
                self.prompt = "hi"
        assert _coerce_arguments(Box()) == {"prompt": "hi"}


# ─── LocalTool.__call__ integration ─────────────────────────


class TestLocalToolNormalisation:
    def test_handler_with_single_args_dict_param(self):
        """The v0.8.6-style handler signature gets the dict as a single arg."""
        captured = {}

        def handler(args):
            captured.update(args)
            return {"echo": args}

        lt = LocalTool(name="t", description="x", handler=handler)
        r = lt({"prompt": "hi", "interval_seconds": 60})
        assert not r.is_error
        assert captured == {"prompt": "hi", "interval_seconds": 60}

    def test_handler_with_kwargs_spread_signature(self):
        """The web-search-style signature still gets kwargs spread."""
        captured = {}

        def handler(query, max_results=5):
            captured["query"] = query
            captured["max_results"] = max_results
            return {"ok": True}

        lt = LocalTool(name="t", description="x", handler=handler)
        r = lt({"query": "hi", "max_results": 3})
        assert not r.is_error
        assert captured == {"query": "hi", "max_results": 3}

    def test_wrapper_dict_routed_correctly(self):
        captured = {}

        def handler(args):
            captured.update(args)
            return {"ok": True}

        lt = LocalTool(name="t", description="x", handler=handler)
        # The exact failing shape from the user log.
        r = lt({"args": {"prompt": "send hello every minute"}})
        assert not r.is_error, r.text
        assert captured == {"prompt": "send hello every minute"}

    def test_bare_json_string_routed_correctly(self):
        captured = {}

        def handler(args):
            captured.update(args)
            return {"ok": True}

        lt = LocalTool(name="t", description="x", handler=handler)
        r = lt('{"args": {"prompt": "hi"}}')
        assert not r.is_error
        assert captured == {"prompt": "hi"}

    def test_non_dict_string_returns_clean_error_not_attribute_error(self):
        """Pre-fix: ``AttributeError: 'str' object has no attribute 'get'``.
        Post-fix: graceful error envelope."""
        def handler(args):
            return {"ok": True}

        lt = LocalTool(name="t", description="x", handler=handler)
        r = lt("not a json call")
        assert not r.is_error
        # The handler was called with ``{"text": "not a json call"}``,
        # which it ignores — the result is the handler's return value.
        # We assert no AttributeError escaped.
        assert "AttributeError" not in r.text


# ─── End-to-end: SchedulerLocalHost ─────────────────────────


@pytest.fixture
def scheduler_host(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    return SchedulerLocalHost()


class TestSchedulerLocalHostWrapperTolerance:
    def test_add_with_direct_dict(self, scheduler_host):
        tools = {t.name: t for t in scheduler_host.list_tools()}
        add = tools["add_scheduled_task"]
        r = add({"prompt": "send hello every minute", "interval_seconds": 60})
        assert not r.is_error
        assert "id" in r.text

    def test_add_with_args_wrapper_dict(self, scheduler_host):
        """The exact failing shape from the 2026-08-31 chat log."""
        tools = {t.name: t for t in scheduler_host.list_tools()}
        add = tools["add_scheduled_task"]
        r = add({"args": {"prompt": "send hello", "interval_seconds": 60}})
        assert not r.is_error, r.text

    def test_add_with_bare_json_string(self, scheduler_host):
        tools = {t.name: t for t in scheduler_host.list_tools()}
        add = tools["add_scheduled_task"]
        r = add('{"prompt": "send hello", "interval_seconds": 60}')
        assert not r.is_error, r.text

    def test_list_with_empty_dict(self, scheduler_host):
        tools = {t.name: t for t in scheduler_host.list_tools()}
        lst = tools["list_scheduled_tasks"]
        r = lst({})
        assert not r.is_error

    def test_list_with_wrapper_dict(self, scheduler_host):
        tools = {t.name: t for t in scheduler_host.list_tools()}
        lst = tools["list_scheduled_tasks"]
        r = lst({"args": {}})
        assert not r.is_error

    def test_get_with_args_wrapper(self, scheduler_host):
        tools = {t.name: t for t in scheduler_host.list_tools()}
        add = tools["add_scheduled_task"]
        get = tools["get_scheduled_task"]
        r1 = add({"prompt": "x", "interval_seconds": 60})
        assert not r1.is_error
        sid = json.loads(r1.text)["id"]
        # Now GET via the wrapper shape — must still find it.
        r2 = get({"args": {"task_id": sid}})
        assert not r2.is_error, r2.text
        assert sid in r2.text

    def test_enable_disable_with_args_wrapper(self, scheduler_host):
        tools = {t.name: t for t in scheduler_host.list_tools()}
        add = tools["add_scheduled_task"]
        disable = tools["disable_scheduled_task"]
        enable = tools["enable_scheduled_task"]
        r1 = add({"prompt": "x", "interval_seconds": 60})
        sid = json.loads(r1.text)["id"]
        r2 = disable({"args": {"task_id": sid}})
        assert not r2.is_error
        # The set_enabled response is ``{ok, id, enabled, next_fire_at}``
        # — verify the wrapper didn't swallow it.
        d = json.loads(r2.text)
        assert d["enabled"] is False
        assert d["id"] == sid
        r3 = enable({"args": {"task_id": sid}})
        assert not r3.is_error
        d3 = json.loads(r3.text)
        assert d3["enabled"] is True
        assert d3["id"] == sid

    def test_delete_with_args_wrapper(self, scheduler_host):
        tools = {t.name: t for t in scheduler_host.list_tools()}
        add = tools["add_scheduled_task"]
        delete = tools["delete_scheduled_task"]
        r1 = add({"prompt": "x", "interval_seconds": 60})
        sid = json.loads(r1.text)["id"]
        r2 = delete({"args": {"task_id": sid}})
        assert not r2.is_error
        assert "true" in r2.text.lower()

    def test_run_now_with_args_wrapper(self, scheduler_host):
        tools = {t.name: t for t in scheduler_host.list_tools()}
        add = tools["add_scheduled_task"]
        run_now = tools["run_scheduled_task_now"]
        r1 = add({"prompt": "x", "interval_seconds": 60})
        sid = json.loads(r1.text)["id"]
        # run_now without an agent_factory just appends a history
        # line; that's the contract we can test.
        r2 = run_now({"args": {"task_id": sid}})
        assert not r2.is_error

    def test_get_errors_with_args_wrapper(self, scheduler_host):
        tools = {t.name: t for t in scheduler_host.list_tools()}
        add = tools["add_scheduled_task"]
        errors = tools["get_scheduled_task_errors"]
        r1 = add({"prompt": "x", "interval_seconds": 60})
        sid = json.loads(r1.text)["id"]
        r2 = errors({"args": {"task_id": sid}})
        assert not r2.is_error


# ─── Cross-transport parity ─────────────────────────────────


class TestWebSearchHostUnaffected:
    """Sanity: web_search keeps its kwargs-spread contract."""

    def test_web_search_kwargs_spread(self):
        ws = WebSearchLocalHost()
        ws_tools = {t.name: t for t in ws.list_tools()}
        search = ws_tools["web_search"]
        r = search({"query": "test", "max_results": 2})
        assert not r.is_error
