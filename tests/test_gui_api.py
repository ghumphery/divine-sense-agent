"""Backend API tests for gui.api (no GUI window needed).

Run: ``pytest tests/test_gui_api.py -v``
or:   ``python -m pytest tests/test_gui_api.py -v``

These tests bypass pywebview entirely and verify the Api class
behaves correctly. We mock out the network calls so the suite
runs offline against any Qdrant instance that's available.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make src/ and gui/ importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_api():
    """Lazy import so test failures don't crash the whole module."""
    from gui.api import Api
    return Api()


# ─── structure ─────────────────────────────────────────────────


def test_api_has_all_expected_methods():
    """API surface must match the JS frontend's expectations."""
    expected = {
        'get_status', 'chat_turn', 'ask',
        'ingest_file', 'ingest_directory',
        'search', 'get_stats', 'list_documents',
        'list_sessions', 'load_session', 'save_session',
        'create_session', 'delete_session',
        'delete_collection', 'recreate_collection',
        # v0.6.0 embedding cache surface
        'backend_info', 'cache_clear_all',
        # v0.7.0 per-document removal
        'remove_documents',
        # v0.8.0+ MCP tool surface
        'list_mcp_tools',
        # v0.8.0+ MCP fleet editor
        'get_mcp_config', 'save_mcp_config',
        'restart_mcp_server', 'get_mcp_server_logs',
        # v0.8.0+ Schedules tab
        'list_schedules', 'add_schedule', 'update_schedule',
        'delete_schedule', 'run_schedule_now',
        'get_schedule_history',
        'start_scheduler', 'stop_scheduler',
        # v0.8.7+ splash screen readiness
        'get_readiness',
    }
    api = _make_api()
    actual = {m for m in dir(api) if not m.startswith('_')}
    missing = expected - actual
    assert not missing, f"missing methods: {missing}"


def test_get_status_returns_expected_keys():
    api = _make_api()
    r = api.get_status()
    assert 'error' not in r, f"unexpected error: {r.get('error')}"
    # v0.9.14.1: the GUI version must track pyproject.toml (the
    # documented single source of truth, ARCHITECTURE.md App. B) —
    # never a drifting literal (it used to be hardcoded '0.8.0').
    import re
    pp = Path(__file__).resolve().parents[1] / "pyproject.toml"
    m = re.search(
        r'^version\s*=\s*"([^"]+)"', pp.read_text(encoding="utf-8"), re.M,
    )
    assert m, "pyproject.toml version not found"
    assert r.get('version') == m.group(1)
    assert 'qdrant_url' in r
    assert 'collection' in r
    assert 'embedding_dim' in r
    assert 'llm_provider' in r
    assert 'qdrant_ok' in r


# ─── error handling ────────────────────────────────────────────


def test_chat_turn_returns_error_dict_on_failure():
    """chat_turn must never raise; returns ``{"error": "..."}`` instead."""
    api = _make_api()
    bad_agent = MagicMock()
    bad_agent.chat_turn.side_effect = RuntimeError('network down')
    with patch.object(api, '_get_agent', return_value=bad_agent):
        r = api.chat_turn('hello', [], False, 5, 5)
    assert 'error' in r
    assert 'network down' in r['error']


def test_search_returns_error_dict_on_failure():
    api = _make_api()
    bad = MagicMock()
    bad.search.side_effect = ConnectionError('qdrant offline')
    with patch.object(api, '_get_retriever', return_value=bad):
        r = api.search('hnsw', 5, None)
    assert 'error' in r
    assert 'qdrant offline' in r['error']


def test_ingest_file_returns_error_dict_on_failure():
    api = _make_api()
    bad = MagicMock()
    bad.ingest_file.side_effect = FileNotFoundError('no such file')
    with patch('gui.api._get_pipeline', return_value=bad):
        r = api.ingest_file('Z:/nonexistent.pdf')
    assert 'error' in r
    assert 'no such file' in r['error']


def test_admin_ops_return_error_dict_on_failure():
    """Both recreate and delete must surface errors, not crash."""
    api = _make_api()

# ─── success paths (against running Qdrant) ───────────────────


def test_get_stats_when_qdrant_up():
    """If Qdrant is reachable, get_stats returns a valid dict.

    Skipped (passes) when Qdrant is down OR we're on Windows where
    SIGALRM doesn't exist (Qdrant's list_documents scroll can take
    minutes on 100K+ point collections).
    """
    import signal as _sig
    if not hasattr(_sig, 'SIGALRM'):
        import pytest
        pytest.skip("SIGALRM unavailable on Windows; scroll timing not enforceable")

    class _Timeout(Exception):
        pass

    def _alarm(*_):
        raise _Timeout()

    _sig.signal(_sig.SIGALRM, _alarm)
    _sig.alarm(10)

    api = _make_api()
    try:
        r = api.get_stats()
    except _Timeout:
        import pytest
        pytest.skip("get_stats timed out (>10s) - collection too large for CI")
    finally:
        _sig.alarm(0)

    if 'error' in r:
        import pytest
        pytest.skip(f"Qdrant unreachable: {r['error']}")
    assert 'collection' in r
    assert 'dim' in r
    assert 'documents' in r or 'num_entities' in r


def test_session_roundtrip():
    """create -> save -> load -> delete should round-trip cleanly."""
    api = _make_api()
    created = api.create_session('unit test')
    if 'error' in created:
        import pytest
        pytest.skip(f"SessionManager failed: {created['error']}")
    sid = created['id']
    try:
        messages = [
            {'role': 'user', 'content': 'hi', 'ts': '2025-01-01T00:00:00'},
            {'role': 'assistant', 'content': 'hello', 'ts': '2025-01-01T00:00:01'},
        ]
        r = api.save_session(sid, 'unit test', messages)
        assert r.get('status') == 'ok'
        loaded = api.load_session(sid)
        assert loaded.get('id') == sid
        assert len(loaded.get('messages', [])) == 2
        assert loaded['messages'][0]['content'] == 'hi'
    finally:
        api.delete_session(sid)


# ─── logging ───────────────────────────────────────────────────


def test_error_logged_to_app_log(tmp_path):
    """Failing call must produce a log line with FAILED in gui/app.log.

    We point a fresh handler at a temp file so we don't pollute the
    real app.log.
    """
    import logging
    from logging.handlers import RotatingFileHandler

    log_file = tmp_path / 'test.log'
    handler = RotatingFileHandler(log_file, maxBytes=1024*1024, backupCount=1)
    handler.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    try:
        api = _make_api()
        bad = MagicMock()
        bad.chat_turn.side_effect = ValueError('test failure')
        with patch.object(api, '_get_agent', return_value=bad):
            api.chat_turn('q', [], False, 5, 5)

        handler.flush()
        content = log_file.read_text(encoding='utf-8', errors='replace')
        assert 'chat_turn FAILED' in content
        assert 'test failure' in content
    finally:
        root.removeHandler(handler)
        handler.close()


def test_admin_collection_ops_error_handling():
    """recreate_collection / delete_collection must return error dict on failure."""
    api = _make_api()

    bad = MagicMock()
    bad.recreate_collection.side_effect = PermissionError('locked')
    bad.delete_collection.side_effect = PermissionError('locked')
    with patch.object(api, '_get_store', return_value=bad):
        assert 'error' in api.recreate_collection()
        assert 'error' in api.delete_collection()


# ─── v0.6.0 embedding cache tests ──────────────────────────────


def test_disk_cache_roundtrip():
    """DiskEmbeddingCache.get(put(key, vec)) must return same vec."""
    from src.ingest.disk_cache import DiskEmbeddingCache
    cache = DiskEmbeddingCache(in_memory=True, max_entries=100)
    vec = [0.1, -0.2, 0.3, 0.4, 0.5] * 100  # 500-dim
    key = DiskEmbeddingCache.make_key("hello", "hash", "hash")
    cache.put(key, "hash", "hash", vec)
    got = cache.get(key)
    assert got is not None, "cache missed right after put"
    # Allow tiny float32 round-trip noise (struct.pack/unpack).
    diffs = [abs(a - b) for a, b in zip(got, vec)]
    assert max(diffs) < 1e-6, f"vec drift: max diff {max(diffs)}"
    cache.close()


def test_disk_cache_backend_isolation():
    """Same text under different backends must NOT collide."""
    from src.ingest.disk_cache import DiskEmbeddingCache
    cache = DiskEmbeddingCache(in_memory=True)
    vec_a = [0.1] * 10
    vec_b = [0.9] * 10
    key_a = DiskEmbeddingCache.make_key("hi", "ollama", "nomic-embed-text")
    key_b = DiskEmbeddingCache.make_key("hi", "local", "Qwen3-Embedding-8B")
    cache.put(key_a, "ollama", "nomic-embed-text", vec_a)
    cache.put(key_b, "local", "Qwen3-Embedding-8B", vec_b)
    # float32 round-trip incurs a ~1.5e-7 absolute error.
    assert cache.get(key_a)[0] == pytest.approx(0.1, abs=1e-6)
    assert cache.get(key_b)[0] == pytest.approx(0.9, abs=1e-6)
    cache.close()


def test_disk_cache_hit_ratio_tracking():
    """get() must bump n_hits so stats() shows a non-zero ratio."""
    from src.ingest.disk_cache import DiskEmbeddingCache
    cache = DiskEmbeddingCache(in_memory=True)
    cache.put("k1", "b", "m", [0.0] * 8)
    cache.get("k1")
    cache.get("k1")
    cache.get("k1")
    s = cache.stats()
    assert s["n_entries"] == 1
    assert s["total_hits"] == 3
    assert 0.0 < s["hit_ratio"] < 1.0
    cache.close()


def test_cache_clear_all_returns_counts():
    """API.cache_clear_all returns {'lru_cleared': int, 'disk_cleared': int}."""
    api = _make_api()

    mock_embedder = MagicMock()
    mock_embedder.cache_clear_all.return_value = {
        "lru_cleared": 5, "disk_cleared": 12,
    }
    with patch.object(api, '_get_embedder', return_value=mock_embedder):
        r = api.cache_clear_all()
    assert r == {"lru_cleared": 5, "disk_cleared": 12}


def test_backend_info_passes_disk_cache_section_through():
    """API.backend_info propagates Embedder.backend_info() including disk_cache."""
    api = _make_api()

    mock_embedder = MagicMock()
    mock_embedder.backend_info.return_value = {
        "backend": "hash", "configured_dim": 384,
        "memory_cache": {"size": 0, "max": 4096},
        "disk_cache": {"enabled": True, "n_entries": 7,
                       "total_hits": 4, "hit_ratio": 0.36,
                       "max_entries": 200000, "path": ".../embed_cache.db"},
    }
    with patch.object(api, '_get_embedder', return_value=mock_embedder):
        r = api.backend_info()
    assert "disk_cache" in r
    assert r["disk_cache"]["n_entries"] == 7
    assert r["disk_cache"]["hit_ratio"] == 0.36

# ─── v0.6.1 GUI Stats fix (list_documents use native group-by) ─────


class _FakeStore:
    """Minimal QdrantStore stand-in for the get_stats path."""
    def __init__(self):
        self.stats_calls = 0
        self.docs_calls = 0

    def get_stats(self):
        self.stats_calls += 1
        return {
            "collection": "file_docs",
            "num_entities": 100,
            "dim": 4096,
            "mode": "Qdrant HTTP server",
        }

    def list_documents(self):
        self.docs_calls += 1
        return ["a.pdf", "b.pdf", "c.pdf"]


def test_get_stats_passes_through_collection_stats():
    """get_stats must surface collection / num_entities / dim from the store."""
    api = _make_api()
    fake = _FakeStore()
    with patch.object(api, '_get_store', return_value=fake):
        r = api.get_stats()
    assert "error" not in r
    assert r["collection"] == "file_docs"
    assert r["num_entities"] == 100
    assert r["dim"] == 4096
    assert r["doc_count"] == 3
    assert sorted(r["documents"]) == ["a.pdf", "b.pdf", "c.pdf"]


def test_get_stats_survives_list_documents_failure():
    """A failing list_documents must NOT blank out the chunks / dim cards."""
    api = _make_api()

    fake = MagicMock()
    fake.get_stats.return_value = {
        "collection": "file_docs", "num_entities": 42, "dim": 4096,
        "mode": "Qdrant HTTP server",
    }
    fake.list_documents.side_effect = RuntimeError("boom")
    with patch.object(api, '_get_store', return_value=fake):
        r = api.get_stats()
    assert r["collection"] == "file_docs"
    assert r["num_entities"] == 42
    assert r["doc_count"] is None
    assert r["documents"] == []
    assert "boom" in r["docs_error"]


def test_qdrant_store_list_documents_uses_groups():
    """list_documents() should call query_points_groups (not scroll)."""
    from src.vector_store.qdrant_client import QdrantStore
    store = QdrantStore.__new__(QdrantStore)  # skip __init__/connect
    store._list_cache_value = None
    store._list_cache_ts = 0.0

    # Fake the underlying client + group response shape.
    class _G:
        groups = [type('G', (), {'id': 'a.pdf'})(),
                  type('G', (), {'id': 'b.pdf'})()]

    class _Client:
        def __init__(self):
            self.calls = []

        def query_points_groups(self, **kw):
            self.calls.append(kw)
            return _G()

        # Other methods used by __init__ / _connect.
        def collection_exists(self, *a, **k): return True

    store._client = _Client()
    store.collection = "file_docs"
    store.dim = 4096
    files = store.list_documents()
    assert files == ["a.pdf", "b.pdf"]
    # Verify we hit the new API (not scroll).
    assert store._client.calls, "query_points_groups was never called"
    kw = store._client.calls[0]
    assert kw["group_by"] == "filename"
    assert kw["group_size"] == 1


def test_qdrant_store_upsert_invalidates_cache():
    """Successful upsert must bust both list_documents and stats caches."""
    from src.vector_store.qdrant_client import QdrantStore
    store = QdrantStore.__new__(QdrantStore)
    # Pre-populate caches as if a previous call had succeeded.
    store._list_cache_value = ["x.pdf"]
    store._list_cache_ts = 1.0
    store._stats_cache_value = {"num_entities": 5, "collection": "c"}
    store._stats_cache_ts = 1.0
    store.collection = "file_docs"
    store._client = MagicMock()

    store.upsert([{
        "id": "abc", "vector": [0.0] * 4096,
        "payload": {"filename": "new.pdf", "text": "..."},
    }])
    # Both caches must be reset to None so the next call re-fetches.
    assert store._list_cache_value is None
    assert store._stats_cache_value is None


# ─── v0.7.0 — per-document removal (delete by filename) ──────────────


class _FakeCount:
    """Stand-in for qdrant_client.http.models.CountResult."""
    def __init__(self, n):
        self.count = n


def test_qdrant_store_delete_by_filename_uses_filter():
    """delete_by_filename must call client.count() then client.delete()
    with a FilterSelector on ``filename`` field."""
    from src.vector_store.qdrant_client import QdrantStore

    store = QdrantStore.__new__(QdrantStore)
    store.collection = "file_docs"
    store.dim = 4096
    store._list_cache_value = ["a.pdf"]
    store._list_cache_ts = 1.0  # pre-populated to test invalidation
    store._stats_cache_value = {"x": 1}
    store._stats_cache_ts = 1.0

    client = MagicMock()
    client.count.return_value = _FakeCount(5)
    store._client = client

    n = store.delete_by_filename("a.pdf")
    assert n == 5
    client.count.assert_called_once()
    client.delete.assert_called_once()
    # Make sure delete used FilterSelector on 'filename' field.
    delete_kwargs = client.delete.call_args.kwargs
    sel = delete_kwargs["points_selector"]
    cond = sel.filter.must[0]
    assert cond.key == "filename"
    assert cond.match.value == "a.pdf"
    # Cache must have been invalidated.
    assert store._list_cache_value is None
    assert store._stats_cache_value is None


def test_qdrant_store_delete_by_filename_zero_matches_skips_delete():
    """If count returns 0, no delete call should be made and 0 returned."""
    from src.vector_store.qdrant_client import QdrantStore

    store = QdrantStore.__new__(QdrantStore)
    store.collection = "file_docs"
    store.dim = 4096
    store._list_cache_value = None
    store._list_cache_ts = 0.0
    store._stats_cache_value = None
    store._stats_cache_ts = 0.0
    client = MagicMock()
    client.count.return_value = _FakeCount(0)
    store._client = client

    n = store.delete_by_filename("nope.pdf")
    assert n == 0
    client.count.assert_called_once()
    client.delete.assert_not_called()


def test_qdrant_store_delete_by_filename_falls_back_on_count_error():
    """Transient count() error → still attempt delete, return -1."""
    from src.vector_store.qdrant_client import QdrantStore

    store = QdrantStore.__new__(QdrantStore)
    store.collection = "file_docs"
    store.dim = 4096
    store._list_cache_value = None
    store._list_cache_ts = 0.0
    store._stats_cache_value = None
    store._stats_cache_ts = 0.0
    client = MagicMock()
    client.count.side_effect = RuntimeError("transient")
    store._client = client

    n = store.delete_by_filename("foo.pdf")
    assert n == -1
    client.delete.assert_called_once()  # we still attempted it


def test_qdrant_store_delete_by_filename_empty_string_is_noop():
    """An empty filename must short-circuit to 0 with no network calls."""
    from src.vector_store.qdrant_client import QdrantStore

    store = QdrantStore.__new__(QdrantStore)
    store.collection = "file_docs"
    store.dim = 4096
    store._list_cache_value = None
    store._list_cache_ts = 0.0
    store._stats_cache_value = None
    store._stats_cache_ts = 0.0
    client = MagicMock()
    store._client = client

    assert store.delete_by_filename("") == 0
    client.count.assert_not_called()
    client.delete.assert_not_called()


def test_api_remove_documents_dedupes_and_reports_split():
    """API.remove_documents returns {removed: dict, missing: list}.

    Duplicate names collapse; missing ones go to 'missing'; present
    ones go to 'removed' with their delete count.
    """
    api = _make_api()
    fake = MagicMock()
    # Mock list_documents and delete_by_filename.
    fake.list_documents.return_value = {"a.pdf", "b.pdf", "c.pdf"}
    fake.delete_by_filename.side_effect = lambda n: {
        "a.pdf": 3,
        "b.pdf": 0,  # somehow present but empty
        "c.pdf": 7,
    }.get(n, 0)
    with patch.object(api, '_get_store', return_value=fake):
        r = api.remove_documents(
            ["a.pdf", "a.pdf", "b.pdf", "ghost.pdf", "c.pdf"])
    assert r["missing"] == ["ghost.pdf"]  # not in collection
    # removed is in present order; duplicates collapsed.
    assert list(r["removed"].keys()) == ["a.pdf", "b.pdf", "c.pdf"]
    assert r["removed"]["a.pdf"] == 3
    assert r["removed"]["b.pdf"] == 0
    assert r["removed"]["c.pdf"] == 7


def test_api_remove_documents_empty_list_errors():
    """Passing an empty list surfaces an error envelope."""
    api = _make_api()
    r = api.remove_documents([])
    assert "error" in r


def test_api_remove_documents_filters_non_strings():
    """Non-string / empty entries are silently dropped before round-trip."""
    api = _make_api()
    fake = MagicMock()
    fake.list_documents.return_value = {"a.pdf"}
    fake.delete_by_filename.return_value = 5
    with patch.object(api, '_get_store', return_value=fake):
        # 123 (int) and "" (empty) and None must be filtered out.
        r = api.remove_documents(["a.pdf", 123, "", None])
    assert "a.pdf" in r["removed"]
    # Only the surviving name was passed through to the store.
    calls = fake.delete_by_filename.call_args_list
    assert all(c.args == ("a.pdf",) for c in calls)


def test_api_remove_documents_returns_error_on_qdrant_failure():
    """A qdrant exception must surface as {error: ...}."""
    api = _make_api()
    fake = MagicMock()
    fake.list_documents.side_effect = RuntimeError("qdrant down")
    with patch.object(api, '_get_store', return_value=fake):
        r = api.remove_documents(["a.pdf"])
    assert "error" in r
    assert "qdrant down" in r["error"]


# ─── MCP fleet editor (v0.8.0+) ─────────────────────────────


def test_validate_server_entry_ok_stdio():
    api = _make_api()
    entry = {
        "name": "my-server",
        "transport": "stdio",
        "command": ["python", "-m", "srv"],
    }
    assert api._validate_server_entry(entry) is None


def test_validate_server_entry_ok_http():
    api = _make_api()
    entry = {
        "name": "remote",
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer x"},
    }
    assert api._validate_server_entry(entry) is None


def test_validate_server_entry_rejects_missing_command():
    api = _make_api()
    assert api._validate_server_entry(
        {"name": "x", "transport": "stdio"}
    ) is not None


def test_validate_server_entry_rejects_unknown_transport():
    api = _make_api()
    err = api._validate_server_entry(
        {"name": "x", "transport": "smoke_signals"}
    )
    assert err is not None
    assert "smoke_signals" in err


def test_validate_server_entry_rejects_missing_url():
    api = _make_api()
    assert api._validate_server_entry(
        {"name": "x", "transport": "streamable_http"}
    ) is not None


def test_atomic_write_json_round_trip(tmp_path):
    api = _make_api()
    target = tmp_path / "config" / "mcp.json"
    api._atomic_write_json(target, {"servers": [{"name": "a"}]})
    assert target.exists()
    import json as _json
    assert _json.loads(target.read_text(encoding="utf-8")) == {
        "servers": [{"name": "a"}]
    }


def test_get_mcp_config_returns_structure(tmp_path, monkeypatch):
    """End-to-end: write a config, read it back, validate shape."""
    api = _make_api()
    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        '{"servers": [{"name": "s1", "transport": "stdio", '
        '"command": ["python", "-m", "srv"]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.agent.agent.Agent.DEFAULT_MCP_CONFIG", str(cfg),
    )
    r = api.get_mcp_config()
    assert r.get("status") == "ok"
    assert r.get("path") == str(cfg)
    assert r.get("exists") is True
    assert len(r["servers"]) == 1
    srv = r["servers"][0]
    assert srv["entry"]["name"] == "s1"
    assert srv["error"] is None


def test_get_mcp_config_strips_comment_entries(tmp_path, monkeypatch):
    """``_comment`` / ``_*`` placeholder entries are ignored."""
    api = _make_api()
    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        '{"servers": ['
        '{"_comment": "example placeholder"}, '
        '{"name": "real", "transport": "stdio", "command": ["x"]}'
        ']}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.agent.agent.Agent.DEFAULT_MCP_CONFIG", str(cfg),
    )
    r = api.get_mcp_config()
    assert len(r["servers"]) == 1
    assert r["servers"][0]["entry"]["name"] == "real"


def test_save_mcp_config_validates_and_writes(tmp_path, monkeypatch):
    """save_mcp_config must reject missing required fields
    before touching disk."""
    api = _make_api()
    cfg = tmp_path / "mcp.json"
    monkeypatch.setattr(
        "src.agent.agent.Agent.DEFAULT_MCP_CONFIG", str(cfg),
    )
    # Bad entry — stdio without command.
    bad = {"servers": [{"name": "x", "transport": "stdio"}]}
    r = api.save_mcp_config(bad)
    assert "error" in r
    assert "validation" in r["error"].lower()
    assert not cfg.exists()  # never written


def test_save_mcp_config_writes_atomically(tmp_path, monkeypatch):
    """Good payload is written, then hot-reloaded."""
    api = _make_api()
    cfg = tmp_path / "mcp.json"
    monkeypatch.setattr(
        "src.agent.agent.Agent.DEFAULT_MCP_CONFIG", str(cfg),
    )
    payload = {
        "servers": [{
            "name": "ok",
            "transport": "stdio",
            "command": ["echo", "hi"],
        }]
    }
    r = api.save_mcp_config(payload)
    assert r.get("status") == "ok"
    assert r.get("saved") == 1
    assert cfg.exists()
    import json as _json
    saved = _json.loads(cfg.read_text(encoding="utf-8"))
    assert saved == payload


def test_get_mcp_server_logs_returns_status():
    """Per-server status returns connection + tool info."""
    api = _make_api()
    r = api.get_mcp_server_logs("nonexistent")
    assert r.get("status") == "ok"
    assert r.get("server") == "nonexistent"
    assert r.get("connected") is False
    assert r.get("tool_count") == 0


def test_get_mcp_config_returns_raw_text(tmp_path, monkeypatch):
    """``raw_text`` is the on-disk file contents verbatim so the
    GUI can offer a "raw JSON edit" mode (v0.8.2+)."""
    api = _make_api()
    cfg = tmp_path / "mcp.json"
    body = (
        '{\n'
        '  "$comment": "kept in raw_text",\n'
        '  "servers": [\n'
        '    {"name": "s1", "transport": "stdio",'
        ' "command": ["x"]}\n'
        '  ]\n'
        '}\n'
    )
    cfg.write_text(body, encoding="utf-8")
    monkeypatch.setattr(
        "src.agent.agent.Agent.DEFAULT_MCP_CONFIG", str(cfg),
    )
    r = api.get_mcp_config()
    assert r.get("raw_text") == body
    # Comment placeholders still stripped from ``servers``.
    assert len(r["servers"]) == 1
    assert r["servers"][0]["entry"]["name"] == "s1"


def test_get_mcp_config_raw_text_empty_when_no_file(tmp_path, monkeypatch):
    """``raw_text`` is ``""`` if the on-disk file doesn't exist
    yet (the GUI falls back to synthesising a JSON skeleton)."""
    api = _make_api()
    cfg = tmp_path / "does-not-exist.json"
    monkeypatch.setattr(
        "src.agent.agent.Agent.DEFAULT_MCP_CONFIG", str(cfg),
    )
    # Also point the cwd fallback at a path that doesn't exist,
    # otherwise the test would pick up the repo's real
    # ``config/mcp.json`` (telegram example) and fail.
    monkeypatch.setattr(
        "gui.api.Api.DEFAULT_MCP_CONFIG_PATH",
        str(tmp_path / "absent.json"),
    )
    r = api.get_mcp_config()
    assert r.get("raw_text") == ""
    assert r.get("exists") is False

