"""Tests for the v0.8.7+ GUI splash-screen readiness endpoint.

The splash overlay polls ``gui.api.Api.get_readiness()`` while
the WebView is booting. The endpoint must:

  * return a ``checks`` dict with three named slots (backend,
    qdrant, llm), each ``{ok, detail}``
  * aggregate ``ready`` true iff all three pass
  * never raise — even when Qdrant is unreachable the LLM slot
    should still report, and vice-versa
  * read env vars lazily (the test patches ``os.environ`` to
    force each branch)

Run: ``pytest tests/test_gui_readiness.py -v``
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_api():
    from gui.api import Api
    return Api()


@pytest.fixture(autouse=True)
def _stub_agent_build(monkeypatch):
    """v0.9.80.0: get_readiness touches the agent's mcp_manager —
    stub `_get_agent` class-wide so no test ever spawns a real stdio
    fleet (individual tests that need a specific stub still override
    with their own patch.object on the instance).
    v0.9.80.1: the mcp check is NON-BLOCKING (kicks the prewarm
    thread + polls) — stub `_kick_prewarm` to mark the prewarm done
    synchronously so the mcp slot resolves deterministically."""
    from gui.api import Api

    monkeypatch.setattr(
        Api, "_get_agent",
        lambda self: _stub_agent_with_mcp(),
        raising=False,
    )

    def _sync_prewarm(self):
        Api._prewarm_started = True
        Api._prewarm_done = True

    monkeypatch.setattr(
        Api, "_kick_prewarm", _sync_prewarm, raising=False,
    )


# ─── structure ───────────────────────────────────────────────────


def test_get_readiness_top_level_shape():
    r = _make_api().get_readiness()
    assert isinstance(r, dict)
    assert "checks" in r
    assert "ready" in r
    assert isinstance(r["ready"], bool)
    for slot in ("backend", "qdrant", "llm"):
        assert slot in r["checks"]
        slot_dict = r["checks"][slot]
        assert "ok" in slot_dict


def test_get_readiness_backend_slot_always_ok():
    # Backend = "the bridge answered". Trivially true since the
    # call reached us in the first place.
    r = _make_api().get_readiness()
    assert r["checks"]["backend"]["ok"] is True
    assert "py " in (r["checks"]["backend"]["detail"] or "")


# ─── Qdrant slot ────────────────────────────────────────────────


def test_get_readiness_qdrant_ok_when_store_alive():
    api = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.return_value = {
        "num_entities": 42,
        "vector_dim": 4096,
    }
    with patch.object(api, "_get_store", return_value=fake_store):
        r = api.get_readiness()
    assert r["checks"]["qdrant"]["ok"] is True
    detail = r["checks"]["qdrant"]["detail"]
    assert "42 pts" in detail
    assert "4096d" in detail


def test_get_readiness_qdrant_fail_surfaces_error():
    api = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.side_effect = ConnectionError(
        "qdrant server unreachable"
    )
    with patch.object(api, "_get_store", return_value=fake_store):
        r = api.get_readiness()
    assert r["checks"]["qdrant"]["ok"] is False
    assert "unreachable" in r["checks"]["qdrant"]["detail"]


def test_get_readiness_qdrant_fail_does_not_cascade():
    """A failed Qdrant probe must NOT blank out the LLM slot."""
    api = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.side_effect = RuntimeError("boom")
    env = {"OPENAI_API_KEY": "sk-test"}
    with patch.object(api, "_get_store", return_value=fake_store), \
         patch.dict(os.environ, env, clear=True):
        r = api.get_readiness()
    assert r["checks"]["qdrant"]["ok"] is False
    assert r["checks"]["llm"]["ok"] is True
    assert r["ready"] is False
  
  

# ─── LLM slot ──────────────────────────────────────────────────


def test_get_readiness_llm_openai_only():
    env = {"OPENAI_API_KEY": "sk-abc", "OPENAI_BASE_URL": "https://x"}
    with patch.dict(os.environ, env, clear=True):
        r = _make_api().get_readiness()
    assert r["checks"]["llm"]["ok"] is True
    assert r["checks"]["llm"]["detail"] == "openai"


def test_get_readiness_llm_ollama_only():
    env = {"OLLAMA_API_KEY": "ok-1", "OLLAMA_BASE_URL": "https://oll"}
    with patch.dict(os.environ, env, clear=True):
        r = _make_api().get_readiness()
    assert r["checks"]["llm"]["ok"] is True
    assert r["checks"]["llm"]["detail"] == "ollama"


def test_get_readiness_llm_both_providers():
    env = {
        "OLLAMA_API_KEY": "ok-1",
        "OPENAI_API_KEY": "sk-2",
    }
    with patch.dict(os.environ, env, clear=True):
        r = _make_api().get_readiness()
    assert r["checks"]["llm"]["ok"] is True
    assert r["checks"]["llm"]["detail"] == "ollama + openai"


def test_get_readiness_llm_base_url_alone_suffices():
    """Local llama.cpp users set OPENAI_BASE_URL with no API key."""
    with patch.dict(os.environ,
                    {"OPENAI_BASE_URL": "http://localhost:8080"},
                    clear=True):
        r = _make_api().get_readiness()
    assert r["checks"]["llm"]["ok"] is True


def test_get_readiness_llm_no_keys_at_all():
    env = {"PATH": "/usr/bin", "SYSTEMROOT": "C:\\Windows"}
    with patch.dict(os.environ, env, clear=True):
        r = _make_api().get_readiness()
    assert r["checks"]["llm"]["ok"] is False
    detail = r["checks"]["llm"]["detail"] or ""
    assert "no" in detail.lower() or "key" in detail.lower()


# ─── overall aggregation ───────────────────────────────────────


def _stub_agent_with_mcp(tools=8, failed=None):
    """A stub agent whose mcp_manager reports a healthy fleet without
    touching the real McpManager (v0.9.80.0: the readiness mcp check
    builds it synchronously — tests must never spawn stdio fleets)."""
    from unittest.mock import MagicMock

    agent = MagicMock()
    mgr = agent.mcp_manager
    mgr.active_tools.return_value = [
        MagicMock() for _ in range(tools)
    ]
    mgr.failed = dict(failed or {})
    return agent


def test_get_readiness_all_three_pass():
    api = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.return_value = {"num_entities": 1, "vector_dim": 768}
    env = {"OPENAI_API_KEY": "sk-yes"}
    with patch.object(api, "_get_store", return_value=fake_store), \
         patch.object(api, "_get_agent",
                      return_value=_stub_agent_with_mcp()), \
         patch.dict(os.environ, env, clear=True):
        r = api.get_readiness()
    assert r["ready"] is True
    assert r["mcp_ran"] is True
    assert "8 tools" in r["checks"]["mcp"]["detail"]


def test_get_readiness_mcp_failed_server_flags_not_ready():
    """v0.9.80.0: a failed MCP server makes the mcp slot ok=False —
    the splash shows ❌ but still dismisses (never strands)."""
    api = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.return_value = {"num_entities": 1, "vector_dim": 768}
    env = {"OPENAI_API_KEY": "sk-yes"}
    with patch.object(api, "_get_store", return_value=fake_store), \
         patch.object(
             api, "_get_agent",
             return_value=_stub_agent_with_mcp(
                 failed={"teams-assist": "boom"},
             ),
         ), \
         patch.dict(os.environ, env, clear=True):
        r = api.get_readiness()
    assert r["checks"]["mcp"]["ok"] is False
    assert "teams-assist" in r["checks"]["mcp"]["detail"]
    assert r["ready"] is False


def test_get_readiness_mcp_skipped_when_agent_unbuildable():
    """No LLM keys → the agent can't build → the mcp slot is marked
    skipped and EXCLUDED from ready (the llm row already flags it)."""
    api = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.return_value = {"num_entities": 1, "vector_dim": 768}
    env = {"OPENAI_API_KEY": "sk-yes"}

    def _boom():
        raise RuntimeError("No LLM provider configured")

    with patch.object(api, "_get_store", return_value=fake_store), \
         patch.object(api, "_get_agent", side_effect=_boom), \
         patch.dict(os.environ, env, clear=True):
        r = api.get_readiness()
    assert r["mcp_ran"] is False
    assert "skipped" in r["checks"]["mcp"]["detail"]
    assert r["ready"] is True  # mcp not counted when skipped


def test_get_readiness_mcp_pending_while_prewarm_running():
    """v0.9.80.1: while the prewarm thread is still building the
    fleet, the mcp slot reports a NON-BLOCKING pending (done=False)
    and is EXCLUDED from `ready` — the fast qdrant/llm rows must not
    be stalled by the MCP build."""
    from gui.api import Api

    api = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.return_value = {"num_entities": 1, "vector_dim": 768}
    env = {"OPENAI_API_KEY": "sk-yes"}

    def _running_prewarm(self):
        from gui.api import Api

        Api._prewarm_started = True
        Api._prewarm_done = False  # thread still building

    with patch.object(api, "_get_store", return_value=fake_store), \
         patch.object(Api, "_kick_prewarm", _running_prewarm), \
         patch.dict(os.environ, env, clear=True):
        r = api.get_readiness()
    assert r["checks"]["mcp"]["done"] is False
    assert "初始化中" in r["checks"]["mcp"]["detail"]
    assert r["mcp_ran"] is False
    assert r["ready"] is True  # pending mcp excluded from ready


def test_get_readiness_includes_version():
    """v0.9.87.4 — the readiness payload carries the pyproject version
    so the splash paints ``#splash-version`` from a live value (the
    HTML fallback literal used to be a hardcoded 'v0.8.6')."""
    a = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.return_value = {
        "num_entities": 10, "vector_dim": 8,
    }
    import io as _io
    import tomllib as _tomllib
    pyver = _tomllib.load(
        _io.open("pyproject.toml", "rb"))["project"]["version"]
    env = {
        "OLLAMA_API_KEY": "k",
        "QDRANT_URL": "http://127.0.0.1:6333",
        "QDRANT_RAG_HOME": "irrelevant",
    }
    with patch.object(a, "_get_store", return_value=fake_store), \
         patch.dict(os.environ, env, clear=True):
        r = a.get_readiness()
    assert r["version"] == pyver


def test_get_readiness_never_raises():
    """Even if _get_store itself crashes (import error, etc.),
    the endpoint must return a structured payload so the JS side
    can render the overlay without crashing the WebView."""
    api = _make_api()
    with patch.object(api, "_get_store",
                      side_effect=ImportError("no qdrant")):
        r = api.get_readiness()
    assert "error" not in r
    assert r["checks"]["qdrant"]["ok"] is False
    assert "no qdrant" in r["checks"]["qdrant"]["detail"]


def test_get_readiness_is_idempotent():
    """Two back-to-back calls produce consistent shape and don't
    flip-flop on the ok flags (the 30 s status poll in the main
    app relies on this stability)."""
    api = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.return_value = {"num_entities": 5, "vector_dim": 768}
    env = {"OPENAI_API_KEY": "sk-1"}
    with patch.object(api, "_get_store", return_value=fake_store), \
         patch.dict(os.environ, env, clear=True):
        a = api.get_readiness()
        b = api.get_readiness()
    assert a["ready"] == b["ready"]
    assert a["checks"]["llm"]["ok"] == b["checks"]["llm"]["ok"]
    assert a["checks"]["backend"]["ok"] == b["checks"]["backend"]["ok"]
# --- LLM slot --- 


# ─── v0.9.112.1 — bounded qdrant probe (stats timeout → readyz) ──


def test_get_readiness_qdrant_stats_slow_falls_back_to_readyz(monkeypatch):
    """When get_stats can't answer within the hard timeout (the cold
    boot: store build 2.7 s + prewarm CPU saturation), the endpoint
    falls back to the lightweight /readyz probe and still reports
    ok=True — the splash row must NOT print 「逾時」."""
    import time as _time

    import gui.api as _gam

    api = _make_api()
    fake_store = MagicMock()

    def _slow_stats():
        _time.sleep(1.5)  # > the patched 0.4 s timeout
        return {"num_entities": 1, "vector_dim": 768}

    fake_store.get_stats.side_effect = _slow_stats
    monkeypatch.setattr(_gam, "_READINESS_STATS_TIMEOUT_S", 0.4)
    monkeypatch.setattr(_gam, "_readyz_probe", lambda t=2.0: True)
    with patch.object(api, "_get_store", return_value=fake_store):
        r = api.get_readiness()
    slot = r["checks"]["qdrant"]
    assert slot["ok"] is True
    assert "readyz" in (slot["detail"] or "")


def test_get_readiness_timeout_and_readyz_down_reports_false(monkeypatch):
    """Slow stats AND a dead server → ok=False with an explicit
    'readyz unreachable' detail (never a silent ok)."""
    import time as _time

    import gui.api as _gam

    api = _make_api()
    fake_store = MagicMock()

    def _slow_stats():
        _time.sleep(1.5)
        return {}

    fake_store.get_stats.side_effect = _slow_stats
    monkeypatch.setattr(_gam, "_READINESS_STATS_TIMEOUT_S", 0.4)
    monkeypatch.setattr(_gam, "_readyz_probe", lambda t=2.0: False)
    with patch.object(api, "_get_store", return_value=fake_store):
        r = api.get_readiness()
    slot = r["checks"]["qdrant"]
    assert slot["ok"] is False
    assert "readyz" in (slot["detail"] or "")


def test_readyz_probe_true_on_200(monkeypatch):
    import gui.api as _gam

    import urllib.request as _ur

    class _R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(_ur, "urlopen",
                        lambda url, timeout=None: _R())
    assert _gam._readyz_probe(1.0) is True


def test_readyz_probe_false_on_refusal(monkeypatch):
    import gui.api as _gam

    import urllib.error as _ue
    import urllib.request as _ur

    def _refused(url, timeout=None):
        raise _ue.URLError("[WinError 10061] refused")
    monkeypatch.setattr(_ur, "urlopen", _refused)
    assert _gam._readyz_probe(1.0) is False
    assert _gam._readyz_probe(1.0) is False  # idempotent, no raise


def test_get_readiness_llm_detail_lists_all_configured_providers():
    """v0.9.112.1 — openrouter/siliconflow/openai-compat keys now count
    as "something configured" (the splash used to show a misleading
    ❌ for an openrouter-primary install)."""
    env = {
        "OPENROUTER_API_KEY": "or",
        "SILICONFLOW_API_KEY": "sf",
        "OPENAI_COMPAT_API_KEY": "c",
        "OPENAI_COMPAT_BASE_URL": "https://c.example/v1",
    }
    with patch.dict(os.environ, env, clear=True):
        r = _make_api().get_readiness()
    slot = r["checks"]["llm"]
    assert slot["ok"] is True
    d = slot["detail"] or ""
    for name in ("openrouter", "siliconflow", "openai-compat"):
        assert name in d


def test_get_readiness_qdrant_dim_key_fallback():
    """v0.9.112.1 — get_stats() returns ``dim`` (the real field name);
    ``vector_dim`` was a phantom key so the splash read ''?d''. Both
    spellings are accepted now."""
    api = _make_api()
    fake_store = MagicMock()
    fake_store.get_stats.return_value = {
        "num_entities": 5566, "dim": 4096,
    }
    with patch.object(api, "_get_store", return_value=fake_store):
        r = api.get_readiness()
    assert "5566 pts" in r["checks"]["qdrant"]["detail"]
    assert "4096d" in r["checks"]["qdrant"]["detail"]
