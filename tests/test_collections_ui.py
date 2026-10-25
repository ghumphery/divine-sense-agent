"""v0.9.59.1 — Admin Collections table (all-collections inventory +
per-row delete like the file_docs card).

Locks down: QdrantStore.list_all_collections / _collection_dim /
delete_collection_by_name, the Api list_collections /
delete_collection_by_name bridges (kind labels + configured flag),
and the frontend wiring (index.html zone + app.js refreshCollections /
delete confirm + i18n ×4 + css table styles).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeQdrant:
    def __init__(self):
        self.deleted: list[str] = []
        from types import SimpleNamespace

        self._cols = {
            "file_docs": self._info(4096, 1234),
            "log_docs": self._info(4096, 5),
            "stock_docs": self._info(4096, 66),
            "memory_docs": self._info(4096, 3),
        }

    @staticmethod
    def _info(dim, points):
        from types import SimpleNamespace

        return SimpleNamespace(
            points_count=points,
            config=SimpleNamespace(params=SimpleNamespace(
                vectors=SimpleNamespace(size=dim),
            )),
            status=SimpleNamespace(name="green"),
        )

    def get_collections(self):
        from types import SimpleNamespace

        return SimpleNamespace(collections=[
            SimpleNamespace(name=n) for n in self._cols
        ])

    def get_collection(self, name):
        return self._cols[name]

    def collection_exists(self, name):
        return name in self._cols

    def delete_collection(self, name):
        self._cols.pop(name, None)
        self.deleted.append(name)


def _store() -> object:
    from src.vector_store.qdrant_client import QdrantStore

    s = QdrantStore.__new__(QdrantStore)
    s.collection = "file_docs"
    s._client = _FakeQdrant()
    s._list_cache_value = None
    s._list_cache_ts = 0.0
    s._stats_cache_value = None
    s._stats_cache_ts = 0.0
    return s


class TestQdrantStoreInventory:
    def test_list_all_collections(self):
        s = _store()
        cols = s.list_all_collections()
        names = [c["name"] for c in cols]
        assert names == ["file_docs", "log_docs", "memory_docs",
                         "stock_docs"]
        by = {c["name"]: c for c in cols}
        assert by["file_docs"]["points"] == 1234
        assert by["file_docs"]["dim"] == 4096
        assert by["memory_docs"]["points"] == 3

    def test_delete_collection_by_name(self):
        s = _store()
        assert s.delete_collection_by_name("log_docs") is True
        assert s._client.collection_exists("log_docs") is False
        assert s.delete_collection_by_name("nope") is False
        assert s.delete_collection_by_name("") is False

    def test_dim_handles_named_vectors(self):
        from src.vector_store.qdrant_client import QdrantStore

        info = MagicMock()
        vectors = {"default": MagicMock(size=768)}
        info.config.params.vectors = vectors
        assert QdrantStore._collection_dim(info) == 768


class TestApiBridges:
    def _api(self, monkeypatch):
        from gui.api import Api

        api = Api.__new__(Api)
        monkeypatch.setattr(Api, "_get_store", lambda self: _store())
        return api

    def test_list_collections_flags_configured(self, monkeypatch):
        from src.config import config

        api = self._api(monkeypatch)
        out = api.list_collections()
        by = {c["name"]: c for c in out["collections"]}
        assert by["file_docs"]["configured"] is True
        assert by["file_docs"]["kind"] == "rag"
        assert by["log_docs"]["kind"] == "log"
        assert by["stock_docs"]["kind"] == "stock"
        assert by["memory_docs"]["kind"] == "memory"
        # the configured collection follows the env, not the hard map
        monkeypatch.setattr(config, "QDRANT_COLLECTION", "log_docs")
        out2 = api.list_collections()
        by2 = {c["name"]: c for c in out2["collections"]}
        assert by2["log_docs"]["configured"] is True
        assert by2["log_docs"]["kind"] == "log"

    def test_delete_collection_by_name(self, monkeypatch):
        api = self._api(monkeypatch)
        out = api.delete_collection_by_name("stock_docs")
        assert out == {"status": "ok", "name": "stock_docs"}
        assert api.delete_collection_by_name("nope")["status"] \
            == "not_found"
        assert "error" in api.delete_collection_by_name("  ")


class TestFrontendWiring:
    def test_index_html_has_collections_table(self):
        src = Path("gui/assets/index.html").read_text(encoding="utf-8")
        assert 'id="admin-collections"' in src
        assert 'data-i18n="collectionsZone"' in src

    def test_app_js_wiring(self):
        src = Path("gui/assets/app.js").read_text(encoding="utf-8")
        assert "list_collections" in src
        assert "delete_collection_by_name" in src
        assert "refreshCollections" in src
        assert "adminDeleteCollection" in src
        # XSS safety: the table is built with textContent (no innerHTML
        # of untrusted collection names)
        assert "tdName.textContent" in src

    def test_i18n_keys_in_all_four_locales(self):
        src = Path("gui/assets/app.js").read_text(encoding="utf-8")
        for key in ("collectionsZone", "confirmDeleteCollection",
                    "colDeleted", "colPoints", "colKind",
                    "kind_memory"):
            assert src.count(key + ":") == 4, key

    def test_css_table_styles(self):
        css = Path("gui/assets/style.css").read_text(encoding="utf-8")
        assert ".admin-table" in css
        assert ".collections-card" in css