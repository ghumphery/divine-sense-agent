"""v0.9.95.0 — Notes 筆記本 Web/手機 UI（keep local 本地筆記本）。

Covers:
* KeepEngine.local_search — paged + filtered browse (keyword /
  category / date range), total count, newest-first, never dumps all
* KeepEngine.local_categories — sorted name+count list
* gui/api.Api notes_* — CRUD round trip on an isolated tmp store
* Frontend source guards — index.html tab + panel ids, app.js
  functions + api calls + i18n keys ×4 locales, style.css classes +
  mobile breakpoint
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _clean_notes_env(tmp):
    os.environ["QDRANT_RAG_HOME"] = str(tmp)
    os.environ["KEEP_MODE"] = "local"
    os.environ["KEEP_NOTES_PATH"] = str(tmp / "notes" / "notes.json")


class TestLocalSearch(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = Path(tempfile.mkdtemp(prefix="notes-ui-"))
        self._old = {k: os.environ.get(k)
                     for k in ("QDRANT_RAG_HOME", "KEEP_MODE",
                               "KEEP_NOTES_PATH")}
        _clean_notes_env(self._tmp)
        import importlib

        import src.keep_tools

        importlib.reload(src.keep_tools)
        self.eng = src.keep_tools.KeepEngine()

    def tearDown(self):
        import importlib

        import src.keep_tools

        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(src.keep_tools)

    def test_paged_total_and_newest_first(self):
        for i in range(7):
            self.eng.local_create(f"note-{i}", f"body {i}")
        out = self.eng.local_search(offset=0, limit=3)
        self.assertEqual(out["total"], 7)
        self.assertEqual(len(out["notes"]), 3)
        self.assertEqual(out["notes"][0]["title"], "note-6")  # newest first
        out2 = self.eng.local_search(offset=3, limit=3)
        self.assertEqual(out2["notes"][0]["title"], "note-3")
        self.assertEqual(out2["total"], 7)  # total is global, not page size

    def test_keyword_filter_title_and_content(self):
        self.eng.local_create("會議記錄", "body")
        self.eng.local_create("其他", "提到會議記錄關鍵字")
        self.eng.local_create("無關", "nothing")
        out = self.eng.local_search(query="會議記錄")
        self.assertEqual(out["total"], 2)

    def test_local_categories_counts(self):
        self.eng.local_create("A", "x", category="工作")
        self.eng.local_create("B", "y", category="工作")
        self.eng.local_create("C", "z", category="生活")
        cats = self.eng.local_categories()
        self.assertEqual(cats, [{"name": "工作", "count": 2},
                                {"name": "生活", "count": 1}])

    def test_bad_offset_limit_tolerated(self):
        self.eng.local_create("A", "x")
        out = self.eng.local_search(offset="x", limit=None)
        self.assertEqual(out["total"], 1)
        out = self.eng.local_search(offset=-5, limit=999)
        self.assertEqual(out["total"], 1)


class TestNotesApi(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = Path(tempfile.mkdtemp(prefix="notes-api-"))
        self._old = {k: os.environ.get(k)
                     for k in ("QDRANT_RAG_HOME", "KEEP_MODE",
                               "KEEP_NOTES_PATH")}
        _clean_notes_env(self._tmp)
        from gui.api import Api

        self.api = Api()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_create_list_edit_delete_roundtrip(self):
        made = self.api.notes_save(
            {"title": "第一篇", "body": "# 標題\n內容", "category": "工作"})
        self.assertIn("id", made)
        nid = made["id"]
        page = self.api.notes_page({})
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["notes"][0]["title"], "第一篇")
        got = self.api.notes_get({"note_id": nid})
        self.assertEqual(got["text"], "# 標題\n內容")
        self.assertEqual(got["category"], "工作")
        upd = self.api.notes_save({"note_id": nid, "title": "第一篇(改)",
                                   "body": "new body", "category": "生活"})
        self.assertEqual(upd["title"], "第一篇(改)")
        self.assertEqual(upd["category"], "生活")
        self.assertTrue(self.api.notes_delete({"note_id": nid})["ok"])
        self.assertEqual(self.api.notes_page({})["total"], 0)

    def test_bridge_shape_single_dict_positional(self):
        """v0.9.96.3 regression — the pywebview/web bridges are
        positional-only (`fn(*args)`): the whole JS object arrives as
        ONE positional argument. The v0.9.95.0 named-param signature
        received it as `page` -> TypeError("int() argument ... not
        'dict'"). The payload-first signatures must handle it."""
        # exact shape the bridge delivers: fn(payload_dict)
        made = self.api.notes_save(
            {"title": "bridge", "body": "b", "category": ""})
        self.assertIn("id", made)
        page = self.api.notes_page(
            {"page": 1, "page_size": 50, "query": "bridge"})
        self.assertEqual(page["total"], 1)
        got = self.api.notes_get({"note_id": made["id"]})
        self.assertEqual(got["title"], "bridge")
        self.assertTrue(self.api.notes_delete({"note_id": made["id"]})["ok"])

    def test_page_params_passed_through(self):
        for i in range(5):
            self.api.notes_save({"title": f"n{i}", "body": "b"})
        out = self.api.notes_page({"page": 2, "page_size": 2})
        self.assertEqual(out["total"], 5)
        self.assertEqual(len(out["notes"]), 2)
        self.assertEqual(out["page"], 2)
        self.assertEqual(out["page_size"], 2)
        self.assertIn("categories", out)

    def test_bad_page_params_tolerated(self):
        out = self.api.notes_page({"page": "x", "page_size": None})
        self.assertNotIn("error", out)
        self.assertEqual(out["page"], 1)
        self.assertEqual(out["page_size"], 50)

    def test_save_validation(self):
        self.assertIn("error", self.api.notes_save({"title": "  "}))
        self.assertIn("error", self.api.notes_get("missing-id"))
        self.assertIn("error", self.api.notes_delete("missing-id"))

    def test_categories_endpoint(self):
        self.api.notes_save({"title": "A", "body": "x", "category": "工作"})
        self.api.notes_save({"title": "B", "body": "y", "category": "工作"})
        cats = self.api.notes_categories()["categories"]
        self.assertEqual(cats, [{"name": "工作", "count": 2}])

    def test_store_is_the_shared_local_json(self):
        """UI 寫入的必須是 LLM keep-local local 模式同一份 notes.json"""
        made = self.api.notes_save({"title": "shared", "body": "b"})
        raw = json.loads(
            (self._tmp / "notes" / "notes.json").read_text("utf-8"))
        self.assertIn(made["id"], raw["notes"])
        eng = self.api._notes_engine()
        self.assertEqual(eng, self.api._notes_engine())  # lazy singleton


class TestNotesFrontendGuards(unittest.TestCase):
    def _read(self, rel):
        with open(os.path.join(ROOT, *rel.split("/")),
                  encoding="utf-8") as f:
            return f.read()

    def test_index_html_tab_and_panel(self):
        html = self._read("gui/assets/index.html")
        self.assertIn('data-tab="notes"', html)
        self.assertIn('id="panel-notes"', html)
        self.assertIn('id="notes-list"', html)
        self.assertIn('id="ne-body"', html)
        self.assertIn('id="ne-preview"', html)
        # v0.9.99.3 — expand/collapse-all buttons
        self.assertIn('id="notes-expand-all"', html)
        self.assertIn('id="notes-collapse-all"', html)
        # v0.9.99.4 — list visibility toggle button
        self.assertIn('id="notes-list-toggle"', html)
        self.assertIn('data-i18n="notesListShow"', html)

    def test_app_js_notes_logic(self):
        js = self._read("gui/assets/app.js")
        for fn in ("function notesLoad", "async function notesOpen",
                   "async function notesSave", "async function notesDelete",
                   "function notesMdInsert", "function notesRenderPreview"):
            self.assertIn(fn, js)
        for call in ("'notes_page'", "'notes_get'", "'notes_save'",
                     "'notes_delete'"):
            self.assertIn(call, js)
        self.assertIn("notesPageSize", js)  # paged, never a full dump
        # textContent-only list build (no raw HTML of note data)
        self.assertNotIn("row.innerHTML", js)
        # v0.9.99.3 — merged collapse behaviour
        self.assertIn("row.classList.add('collapsed')", js)
        self.assertIn("row.classList.toggle('collapsed')", js)
        self.assertIn("notes-expand-all", js)
        self.assertIn("notes-collapse-all", js)
        # v0.9.99.4 — list hidden by default + toggle button
        self.assertIn("let notesListVisible = false", js)
        self.assertIn("function notesListApply", js)
        self.assertIn("function notesListToggle", js)
        self.assertIn("notesListApply();", js)  # re-applied on load
        self.assertIn("notes-list-toggle", js)
        self.assertIn("notesListShow", js)
        # v0.9.99.4 — list/tools/pager start hidden in the HTML
        html = self._read("gui/assets/index.html")
        self.assertIn('id="notes-list" class="notes-list" hidden', html)
        self.assertIn('id="notes-pager" class="notes-pager" hidden', html)
        self.assertIn('id="notes-list-tools"', html)
        # v0.9.99.4 fix — [hidden] must beat display:flex (the
        # author .notes-pager{display:flex} rule overrides the UA
        # hidden rule without this)
        css = self._read("gui/assets/style.css")
        for sel in (".notes-list[hidden]", ".notes-pager[hidden]",
                    ".notes-list-tools[hidden]"):
            self.assertIn(sel, css)
        self.assertIn("display: none;", css[css.find(".notes-list[hidden]"):][:120])

    def test_i18n_keys_in_all_four_locales(self):
        js = self._read("gui/assets/app.js")
        for key in ("tabNotes", "notesTitle", "notesPageOf",
                    "notesConfirmDelete", "notesPreview", "notesEdit",
                    "notesExpandAll", "notesCollapseAll",
                    "notesListShow", "notesListHide"):
            self.assertEqual(js.count(key + ":"), 4, key)

    def test_notes_offline_first_layer(self):
        """v0.9.124.9 — web-mode offline-first Notes: localStorage
        cache (notes_cache_v1), pending FIFO queue (notes_pending_v1),
        pre-overwrite conflict backup ring (notes_conflict_backup_v1),
        the offline banners/buttons, and the replay sync. Zero backend
        change (offline-created notes are CREATED on connect — the
        server assigns the id)."""
        js = self._read("gui/assets/app.js")
        for key in ("notes_cache_v1", "notes_pending_v1",
                    "notes_conflict_backup_v1"):
            self.assertIn(key, js)
        for fn in ("function notesOfflineActive",
                   "function notesCacheSave", "function notesCacheGet",
                   "function notesPendingLoad",
                   "function notesOfflineEnqueue",
                   "async function notesOfflineSync",
                   "function notesOfflineBannerRefresh",
                   "async function notesConflictBackupBeforeSave",
                   "function notesRender"):
            self.assertIn(fn, js)
        # web-mode gate — the offline layer is a NO-OP in pywebview
        self.assertIn("window.__WEB_MODE__", js)
        # offline branch lives INSIDE notesLoad (bridge-null → cache)
        self.assertIn("notesCacheGet() || {}", js)
        self.assertIn("notesCacheSave(r);", js)
        # offline-created ids are client-side placeholders
        self.assertIn("'pending:' + i", js)
        # sync replay: create passes an EMPTY note_id (server assigns)
        self.assertIn("op.op === 'create'", js)
        self.assertIn("title: op.payload.title,", js)
        # events wiring
        self.assertIn("addEventListener('online'", js)
        self.assertIn("addEventListener('offline'", js)
        html = self._read("gui/assets/index.html")
        self.assertIn('id="notes-sync-btn"', html)
        self.assertIn('id="notes-offline-banner"', html)
        self.assertIn('"notes-offline-banner" hidden', html)
        css = self._read("gui/assets/style.css")
        self.assertIn(".notes-offline-banner {", css)
        self.assertIn(".notes-offline-banner[hidden]", css)

    def test_notes_offline_i18n_four_locales(self):
        js = self._read("gui/assets/app.js")
        for key in ("notesSyncBtn", "notesSynced",
                    "notesOfflineBanner", "notesOfflineStash",
                    "notesStillOffline", "notesUpToDate",
                    "notesLoadOffline2"):
            self.assertEqual(js.count(key + ":"), 4, key)

    def test_style_css_notes_blocks(self):
        css = self._read("gui/assets/style.css")
        for cls in (".note-row", ".notes-editor", ".ne-preview",
                    ".notes-pager", ".btn.danger"):
            self.assertIn(cls, css)
        self.assertIn("@media (max-width: 480px)", css)
        # v0.9.99.3 — collapsed state css
        self.assertIn(".note-row.collapsed .note-snippet", css)
        self.assertIn(".note-row-head::after", css)


if __name__ == "__main__":
    unittest.main()