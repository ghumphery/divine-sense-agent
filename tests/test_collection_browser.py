"""v0.9.60.0 — Search/Stats cross-collection + paged data browser.

Covers:
* ``QdrantStore.browse_points`` — paged scroll (native offset
  pagination), payload truncation, id normalisation, limit clamp.
* ``QdrantStore.delete_points`` — per-row data management deletes.
* ``gui.api.Api.search`` with the ``collection`` param (default vs
  dedicated retriever) + ``collection_browse`` + ``delete_points``
  bridges (guards + passthrough).
* Frontend source guards — collection pickers, paged browser table,
  pager buttons, delete wiring, i18n keys in all four locales, CSS.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import gui.api as gui_api
from src.vector_store.qdrant_client import QdrantStore

ROOT = Path(__file__).resolve().parent.parent


# ─── QdrantStore.browse_points ────────────────────────────────────
def _fake_store(collection: str = "c1") -> QdrantStore:
    """QdrantStore without touching the network (test_collections_ui
    pattern: __new__ + private attrs; `client` is a read-only
    property over _client)."""
    s = QdrantStore.__new__(QdrantStore)
    s.collection = collection
    s._client = MagicMock()
    s._list_cache_value = None
    s._list_cache_ts = 0.0
    s._stats_cache_value = None
    s._stats_cache_ts = 0.0
    return s


class TestBrowsePoints:
    def test_scroll_paged_params_and_rows(self):
        store = _fake_store()
        pt = SimpleNamespace(
            id="11111111-2222-3333-4444-555555555555",
            payload={"filename": "a.txt", "text": "x" * 400},
            score=None,
        )
        store._client.scroll.return_value = ([pt], "offset-token-2")
        rows, nxt = store.browse_points(limit=50, offset="token-1")
        args = store._client.scroll.call_args.kwargs
        assert args["collection_name"] == "c1"
        assert args["limit"] == 50
        assert args["offset"] == "token-1"
        assert args["with_vectors"] is False
        assert nxt == "offset-token-2"
        assert rows[0]["id"] == "11111111-2222-3333-4444-555555555555"
        # long string payload truncated with a marker
        assert rows[0]["payload"]["text"].endswith("chars]")
        assert len(rows[0]["payload"]["text"]) < 400

    def test_limit_clamped_to_200(self):
        store = _fake_store()
        store._client.scroll.return_value = ([], None)
        store.browse_points(limit=5000)
        assert store._client.scroll.call_args.kwargs["limit"] == 200

    def test_empty_payload_ok(self):
        store = _fake_store()
        pt = SimpleNamespace(id=42, payload=None, score=None)
        store._client.scroll.return_value = ([pt], None)
        rows, nxt = store.browse_points()
        assert rows == [{"id": "42", "payload": {}}]
        assert nxt is None

    def test_nested_payload_serialised_and_truncated(self):
        store = _fake_store()
        pt = SimpleNamespace(
            id="a", payload={"meta": {"k": "v" * 600}}, score=None)
        store._client.scroll.return_value = ([pt], None)
        rows, _ = store.browse_points(payload_chars=50)
        s = rows[0]["payload"]["meta"]
        assert len(s) < 100 and "chars]" in s


# ─── QdrantStore.delete_points ────────────────────────────────────
class TestDeletePoints:
    def test_empty_ids_no_http(self):
        store = _fake_store()
        assert store.delete_points([]) == 0
        assert store.delete_points(["", "  "]) == 0
        store._client.delete.assert_not_called()

    def test_deletes_by_id_and_invalidates_cache(self):
        store = _fake_store()
        n = store.delete_points(["id-1", "id-2"])
        assert n == 2
        sel = store._client.delete.call_args.kwargs["points_selector"]
        assert [str(p) for p in sel.points] == ["id-1", "id-2"]


# ─── gui.api surface ──────────────────────────────────────────────
class TestApiCollectionBrowser:
    def _api(self):
        api = gui_api.Api.__new__(gui_api.Api)
        return api

    def test_search_default_uses_cached_retriever(self, monkeypatch):
        api = self._api()
        retr = MagicMock()
        retr.search.return_value = [{"id": "p1", "score": 0.9}]
        monkeypatch.setattr(api, "_get_retriever", lambda: retr)
        monkeypatch.setattr(gui_api.config, "QDRANT_COLLECTION", "file_docs")
        r = api.search("hello", top_k=3, collection=None)
        assert r["results"][0]["id"] == "p1"
        assert r["collection"] == "file_docs"
        assert r["count"] == 1

    def test_search_other_collection_builds_retriever(self, monkeypatch):
        api = self._api()
        monkeypatch.setattr(gui_api.config, "QDRANT_COLLECTION", "file_docs")
        store = MagicMock()
        retriever = MagicMock()
        retriever.search.return_value = [{"id": "m1"}]
        import src.vector_store.qdrant_client as qc
        import src.vector_store.retriever as rt
        import src.ingest.embedder as emb
        monkeypatch.setattr(qc, "QdrantStore", MagicMock(return_value=store))
        monkeypatch.setattr(rt, "Retriever", lambda: retriever)
        monkeypatch.setattr(emb, "Embedder", lambda: MagicMock())
        r = api.search("hi", top_k=2, collection="memory_docs")
        assert r["collection"] == "memory_docs"
        assert store is retriever.store

    def test_collection_browse_passthrough(self, monkeypatch):
        api = self._api()
        store = MagicMock()
        store.browse_points.return_value = ([{"id": "x", "payload": {}}],
                                            "next-1")
        import src.vector_store.qdrant_client as qc
        monkeypatch.setattr(qc, "QdrantStore", MagicMock(return_value=store))
        r = api.collection_browse("log_docs", page_size=50, offset=None)
        assert r["items"][0]["id"] == "x"
        assert r["next_offset"] == "next-1"
        assert r["collection"] == "log_docs"

    def test_collection_browse_guards(self):
        api = self._api()
        assert "error" in api.collection_browse("")
        assert "error" in api.collection_browse("c", page_size=0)

    def test_delete_points_guards_and_ok(self, monkeypatch):
        api = self._api()
        assert "error" in api.delete_points("", ["a"])
        assert "error" in api.delete_points("c", [])
        store = MagicMock()
        store.delete_points.return_value = 1
        import src.vector_store.qdrant_client as qc
        monkeypatch.setattr(qc, "QdrantStore", MagicMock(return_value=store))
        r = api.delete_points("memory_docs", ["uuid-1"])
        assert r["status"] == "ok"
        assert r["deleted"] == 1

    def test_store_cache_reused_across_pages(self, monkeypatch):
        """v0.9.60.2 — the per-collection QdrantStore is REUSED so page
        turns on a million-point collection don't rebuild the client."""
        api = self._api()
        import src.vector_store.qdrant_client as qc
        built = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(qc, "QdrantStore", built)
        api._collection_store("log_docs")
        api._collection_store("log_docs")
        assert built.call_count == 1
        api._collection_store("memory_docs")
        assert built.call_count == 2
        assert api._collection_store("memory_docs") \
            is api._collection_store("memory_docs")

    def test_get_stats_other_collection_skips_doclist(self, monkeypatch):
        api = self._api()
        store = MagicMock()
        store.get_stats.return_value = {"collection": "memory_docs",
                                        "num_entities": 0}
        import src.vector_store.qdrant_client as qc
        monkeypatch.setattr(qc, "QdrantStore", MagicMock(return_value=store))
        monkeypatch.setattr(gui_api.config, "QDRANT_COLLECTION", "file_docs")
        r = api.get_stats("memory_docs")
        assert r["collection"] == "memory_docs"
        store.list_documents.assert_not_called()

    def test_get_stats_default_keeps_doclist(self, monkeypatch):
        api = self._api()
        store = MagicMock()
        store.get_stats.return_value = {"collection": "file_docs"}
        store.list_documents.return_value = ["a.txt"]
        monkeypatch.setattr(api, "_get_store", lambda: store)
        monkeypatch.setattr(gui_api.config, "QDRANT_COLLECTION", "file_docs")
        r = api.get_stats()
        assert r["doc_count"] == 1


# ─── frontend source guards ───────────────────────────────────────
class TestFrontendWiring:
    def _read(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_index_html_has_pickers_and_browser(self):
        html = self._read("gui/assets/index.html")
        assert 'id="search-collection"' in html
        assert 'id="stats-collections"' in html
        assert 'id="stats-load-data"' in html
        assert 'id="stats-data"' in html
        assert 'id="stats-data-next"' in html
        assert 'id="stats-data-prev"' in html
        assert 'data-i18n="dataBrowser"' in html

    def test_app_js_has_browser_functions(self):
        js = self._read("gui/assets/app.js")
        for name in ("refreshCollectionSelectors", "loadStatsData",
                     "statsDataNext", "statsDataPrev",
                     "resetStatsDataBrowser"):
            assert f"function {name}" in js
        # delete wiring on BOTH surfaces (search results + browser rows)
        assert js.count("api.call('delete_points'") == 2
        assert "api.call('collection_browse'" in js
        assert "api.call('search', q, topk, filter, collection)" in js
        # bindEvents wiring
        assert "statsDataNext" in js and "statsDataPrev" in js

    def test_app_js_textcontent_only_build(self):
        js = self._read("gui/assets/app.js")
        seg = js[js.index("async function loadStatsData"):
                 js.index("function statsDataNext")]
        assert "textContent" in seg
        assert ".innerHTML = ''" in seg  # only the clear-before-render
        assert "innerHTML +=" not in seg

    def test_page_indicator_wired(self):
        """v0.9.60.2 — the pager shows 第 X 頁 (stack depth + 1)."""
        js = self._read("gui/assets/app.js")
        assert "statsData.stack.length + 1" in js
        assert "t('dataPos'" in js

    def test_pickers_populated_without_visiting_stats(self):
        """v0.9.60.4 — direct-to-Search no longer shows an empty
        collection dropdown: boot fills it, switchTab('search')
        refreshes it, and runSearch self-heals an empty picker."""
        js = self._read("gui/assets/app.js")
        boot_seg = js[js.index("function boot()"):
                      js.index("setInterval(refreshStatus, 30000)")]
        assert "refreshCollectionSelectors();" in boot_seg
        tab_seg = js[js.index("function switchTab"):
                     js.index("function setStatus")]
        assert "if (tabName === 'search') refreshCollectionSelectors();" \
            in tab_seg
        run_seg = js[js.index("async function runSearch"):
                     js.index("const r = await api.call('search'")]
        assert "sel.options.length" in run_seg
        assert "await refreshCollectionSelectors();" in run_seg

    def test_i18n_keys_all_four_locales(self):
        js = self._read("gui/assets/app.js")
        for key in ("loadData", "pagerPrev", "pagerNext", "dataBrowser",
                    "dataPos", "dataEmpty", "deletePoint",
                    "confirmDeletePoint", "pointDeleted", "noDocIndex"):
            assert js.count(f"{key}: ") >= 4, key

    def test_style_css_browser_rules(self):
        css = self._read("gui/assets/style.css")
        assert "#stats-data" in css
        assert ".collection-select" in css
        assert ".pager" in css
        assert ".collection-toolbar" in css


# ─── v0.9.60.3 — cross-collection search result mapping ───────────
def _hit_store(payload: dict) -> QdrantStore:
    s = QdrantStore.__new__(QdrantStore)
    s.collection = "c"
    s._client = MagicMock()
    s._client.query_points.return_value = SimpleNamespace(
        points=[SimpleNamespace(id="11111111-2222", payload=payload,
                                score=0.9)])
    s._list_cache_value = None
    s._list_cache_ts = 0.0
    s._stats_cache_value = None
    s._stats_cache_ts = 0.0
    return s


class TestSearchResultMapping:
    def test_memory_payload_label_and_content(self):
        store = _hit_store({
            "kind": "chat", "source": "chat", "session_id": "s1",
            "content": "我的投資偏好是穩健型", "stored_at":
            "2026-09-15T14:30:00+08:00", "stored_at_epoch": 1.0,
            "meta": {"nested": True},
        })
        r = store.search(query_vector=[0.1] * 4)
        row = r[0]
        assert row["filename"] == "chat"
        assert row["text"] == "我的投資偏好是穩健型"
        assert row["meta"]["kind"] == "chat"
        assert row["meta"]["stored_at"].startswith("2026-09-15")
        # nested dict payloads are NOT dumped into the meta chips
        assert "meta" not in row["meta"]

    def test_log_payload(self):
        store = _hit_store({
            "source": "app.log", "level": "error",
            "message": "brute force detected", "raw_line":
            "Failed password for root", "ts_epoch": 1.0,
        })
        r = store.search(query_vector=[0.1] * 4)
        row = r[0]
        assert row["filename"] == "app.log"
        assert row["text"] == "brute force detected"
        assert row["meta"]["level"] == "error"

    def test_stock_payload(self):
        store = _hit_store({
            "tool": "stock_get_quote", "symbol": "2330",
            "ts": "2026-09-15T09:30:00+08:00", "text": "台積電報價",
        })
        r = store.search(query_vector=[0.1] * 4)
        row = r[0]
        assert row["filename"] == "2330"
        assert row["text"] == "台積電報價"
        assert row["meta"]["tool"] == "stock_get_quote"

    def test_file_docs_unchanged(self):
        store = _hit_store({
            "filename": "report.pdf", "text": "chunk body",
            "source": "/docs/report.pdf", "content_hash": "abc",
        })
        r = store.search(query_vector=[0.1] * 4)
        row = r[0]
        assert row["filename"] == "report.pdf"
        assert row["text"] == "chunk body"
        assert row["meta"] == {}

    def test_empty_payload_falls_back_to_id_label(self):
        store = _hit_store({})
        r = store.search(query_vector=[0.1] * 4)
        row = r[0]
        assert row["filename"] == "#11111111"
        assert row["text"] == ""

    def test_frontend_meta_chips_wired(self):
        js = (ROOT / "gui/assets/app.js").read_text(encoding="utf-8")
        assert "result-meta" in js
        assert "it.meta" in js
        css = (ROOT / "gui/assets/style.css").read_text(encoding="utf-8")
        assert ".result-meta" in css