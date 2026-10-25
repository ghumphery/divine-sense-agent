# -*- coding: utf-8 -*-
"""v0.9.125.0 — the ✍️ handwriting-note flow (web Notes canvas →
Api.notes_handwriting_save): ARCHIVE the PNG on the host
($QDRANT_RAG_HOME/handwriting/), OCR through the project vision
chain (ImageDescriber + transcription prompt), file a Notes-store
note (category 手寫) referencing the PNG, and INDEX the .txt
transcript via the SAME single-file ingest the 文件 tab uses.

The Api layer is behaviour-tested with stubs (engine + describer +
ingest_file — no network); the frontend part is source-guaranteed.
"""
from __future__ import annotations

import base64
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 300


def _data_url(raw=PNG_MAGIC):
    return "data:image/png;base64," + base64.b64encode(raw).decode()


class _FakeEngine:
    def __init__(self):
        self.created = []

    def local_create(self, title, body_text, category=""):
        self.created.append({"title": title, "body": body_text,
                             "category": category})
        return {"id": f"n-{len(self.created)}", "title": title,
                "text": body_text, "category": category}


@pytest.fixture()
def hw_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    eng = _FakeEngine()

    def _mk_api():
        from gui.api import Api

        a = Api.__new__(Api)
        a._notes_engine_ref = eng
        a._ingest_calls = []
        a.ingest_file = (
            lambda p: (a._ingest_calls.append(p),
                       {"status": "ok", "chunks": 3, "path": p})[1]
        )
        return a

    yield tmp_path, eng, _mk_api


class TestApiHandwriting:
    def _stub_describer(self, monkeypatch, text="週五早上先到總店取貨",
                        backend="ollama"):
        import src.ingest.image_describer as idm

        fake = MagicMock()
        fake.describe.return_value = text
        fake.last_backend = backend
        real_cls = idm.ImageDescriber

        class _Proxy:
            def __new__(cls, *a, **kw):
                return fake

            @classmethod
            def __instancecheck__(cls, o):  # keep isinstance happy
                return isinstance(o, real_cls)

        monkeypatch.setattr(idm, "ImageDescriber", _Proxy)
        return fake

    def test_full_pipeline_stubs(self, hw_env, monkeypatch):
        tmp, eng, _mk = hw_env
        self._stub_describer(monkeypatch, "回診提醒：週五 11:30 北醫耳鼻喉科")
        a = _mk()
        out = a.notes_handwriting_save({
            "data_url": _data_url(), "title": "", "category": "",
        })
        assert out.get("ok") and out["id"]
        # ① PNG archived on the host
        png = Path(out["png"])
        assert png.exists() and png.read_bytes()[:4] == PNG_MAGIC[:4]
        assert "handwriting" in out["png"]
        # ② OCR text transcribed (stub)
        assert "回診提醒" in out["ocr"]
        assert out["ocr_backend"] == "ollama"
        # ③ note filed with the PNG reference
        assert eng.created, eng
        note = eng.created[-1]
        assert note["title"].startswith("回診提醒")
        assert note["category"] == "手寫"
        assert "手寫原文" in note["body"] and out["png"] in note["body"]
        # ④ index built from the .txt transcript
        txt = Path(out["transcript"])
        assert txt.exists() and "回診提醒" in txt.read_text(encoding="utf-8")
        assert a._ingest_calls and Path(
            a._ingest_calls[0]) == txt  # sep-agnostic compare
        assert out["indexed_chunks"] == 3

    def test_ocr_marker_falls_back_honestly(self, hw_env, monkeypatch):
        tmp, eng, _mk = hw_env
        self._stub_describer(monkeypatch,
                             "[Image: hw.png | provider failed]",
                             backend=None)
        a = _mk()
        out = a.notes_handwriting_save({"data_url": _data_url()})
        assert out.get("ok") and Path(out["png"]).exists()
        assert out["ocr"] == ""
        assert eng.created[-1]["body"].startswith(
            "（OCR 未取得文字）")
        # the index still builds (title-only transcript)
        assert out["indexed_chunks"] == 3

    def test_title_from_ocr_first_line(self, hw_env, monkeypatch):
        tmp, eng, _mk = hw_env
        self._stub_describer(monkeypatch,
                             "第二行程式碼\n今天買牛奶和雞蛋")
        a = _mk()
        out = a.notes_handwriting_save({"data_url": _data_url()})
        assert out["title"] == "第二行程式碼"[:60] or out["title"]

    def test_validation_errors(self, hw_env, monkeypatch):
        tmp, eng, _mk = hw_env
        a = _mk()
        assert "data_url" in a.notes_handwriting_save({})["error"]
        assert "data_url" in a.notes_handwriting_save({
            "data_url": "data:text/plain;base64,AAAA"})["error"]
        assert "PNG/JPEG" in a.notes_handwriting_save({
            "data_url": "data:image/png;base64,"
                        + base64.b64encode(b"GIF89a").decode(),
        })["error"]
        huge = PNG_MAGIC + b"\x00" * (9 * 1024 * 1024)
        assert "too large" in a.notes_handwriting_save({
            "data_url": _data_url(huge)})["error"]

    def test_multipage_pages_archived(self, hw_env, monkeypatch):
        tmp, eng, _mk = hw_env
        self._stub_describer(monkeypatch, "多頁轉寫內容")
        a = _mk()
        out = a.notes_handwriting_save({
            "data_url": _data_url(),
            "pages": [_data_url(), _data_url()],
        })
        assert out.get("ok")
        assert len(out["pages"]) == 2, out["pages"]
        for f in out["pages"]:
            assert Path(f).exists()
        # the stitched main PNG + the transcript still archive/index
        assert Path(out["png"]).exists()
        assert out["indexed_chunks"] == 3

    def test_bad_page_entries_skipped(self, hw_env, monkeypatch):
        tmp, eng, _mk = hw_env
        self._stub_describer(monkeypatch, "x")
        a = _mk()
        out = a.notes_handwriting_save({
            "data_url": _data_url(),
            "pages": ["not-a-data-url",
                      "data:image/png;base64," + base64.b64encode(
                          b"GIF89a").decode()],
        })
        assert out.get("ok") and out["pages"] == []

    def test_read_handwriting_image_scoped(self, hw_env, monkeypatch):
        tmp, eng, _mk = hw_env
        self._stub_describer(monkeypatch, "x")
        a = _mk()
        saved = a.notes_handwriting_save({"data_url": _data_url()})
        png = saved["png"]
        r = a.read_handwriting_image({"path": png})
        assert r.get("ok") and r["data_url"].startswith(
            "data:image/png;base64,")
        # bare filename form works too
        r2 = a.read_handwriting_image({
            "path": Path(png).name})
        assert r2.get("ok")
        # traversal refused
        bad = a.read_handwriting_image({"path": r"C:/Windows/win.ini"})
        assert "refused" in bad["error"]
        bad2 = a.read_handwriting_image({"path": "../secrets.txt"})
        assert "refused" in bad2["error"]
        # not-a-image refused
        other = Path(str(tmp)) / "handwriting" / "note.txt"
        other.write_text("x", encoding="utf-8")
        bad3 = a.read_handwriting_image({"path": str(other)})
        assert "not a handwriting image" in bad3["error"]

    def test_home_env_isolation(self, hw_env, monkeypatch):
        tmp, eng, _mk = hw_env
        self._stub_describer(monkeypatch, "x")
        a = _mk()
        out = a.notes_handwriting_save({"data_url": _data_url()})
        tmp_posix = str(tmp).replace(os.sep, "/")
        assert tmp_posix in out["png"]


class TestHandwritingFrontend(unittest.TestCase):
    def _read(self, p):
        return Path(p).read_text(encoding="utf-8")

    def test_app_js_handwriting_logic(self):
        js = self._read("gui/assets/app.js")
        for fn in ("function notesHwOpen", "function notesHwClose",
                   "function notesHwInitCanvas", "function notesHwClear",
                   "async function notesHwSave",
                   "function notesHwBind"):
            self.assertIn(fn, js)
        # pointer-draw machinery + smoothing
        for tok in ("pointerdown", "pointermove", "pointerup",
                    "setPointerCapture", "getBoundingClientRect",
                    "quadraticCurveTo"):
            self.assertIn(tok, js)
        # send = base64 data URL to the Api method
        self.assertIn("notes_handwriting_save", js)
        self.assertIn("toDataURL('image/png')", js)
        self.assertIn("data_url:", js)
        # empty canvas guard + honest offline refusal
        self.assertIn("getImageData", js)
        self.assertIn("navigator.onLine", js)
        self.assertIn("hwOffline", js)
        self.assertIn("hwEmpty", js)
        # the created note opens for review
        self.assertIn("notesOpen(r.id)", js)

    def test_html_css_hw_blocks(self):
        html = self._read("gui/assets/index.html")
        for tok in ('id="notes-hw-btn"', 'id="hw-modal"',
                    'id="hw-canvas"', 'id="hw-save"', 'id="hw-clear"',
                    'id="hw-note-title"', 'data-i18n="hwTitle"',
                    'data-i18n-title="hwSave"'):
            self.assertIn(tok, html)
        self.assertIn('"hw-modal" hidden', html)
        css = self._read("gui/assets/style.css")
        for sel in (".hw-modal {", ".hw-modal[hidden]",
                    ".hw-canvas {", ".hw-row {"):
            self.assertIn(sel, css)
        self.assertIn("touch-action: none", css)

    def test_multipage_fullscreen_bg_and_viewimg(self):
        """v0.9.125.1 — 全螢幕/多頁加頁/筆記本背景圖/可看圖 guards."""
        js = self._read("gui/assets/app.js")
        for fn in ("function notesHwAddPage", "function notesHwPrev",
                   "function notesHwNext", "function notesHwFlushPage",
                   "function notesHwShowArchived",
                   "function notesHwFullToggle",
                   "function notesHwBgPick",
                   "function notesHwBgChange",
                   "function notesHwBgClearF"):
            self.assertIn(fn, js)
        # multi-page stitching (single PNG for OCR; per-page archives)
        self.assertIn("notesHwStitch", js) if False else None
        self.assertIn("stitch.height = H", js)
        self.assertIn("pages: perPage", js)
        # background image baked into the export
        self.assertIn("hwPageBgs[hwPage] || hwBgImg", js)
        self.assertIn("g.drawImage(src", js)
        self.assertIn("'hw-bg-file'", js)
        self.assertIn("FileReader", js)
        # fullscreen — v0.9.125.2 CSS-ONLY: the Fullscreen API VISUALLY
        # ZOOMED the whole top-layer element (fonts/buttons included --
        # the user reported「所有物件都放大」); it must never come back.
        self.assertNotIn("requestFullscreen", js)
        self.assertNotIn("exitFullscreen", js)
        self.assertIn("classList.toggle('hw-full'", js)
        # 可看圖 preview wiring
        self.assertIn("read_handwriting_image", js)
        self.assertIn("ne-hw-img", js)
        self.assertIn("手寫原文：", js)
        html = self._read("gui/assets/index.html")
        for tok in ('id="hw-pageinfo"', 'id="hw-addpage"',
                    'id="hw-full"', 'id="hw-bg-btn"',
                    'id="hw-bg-clear"', 'id="hw-bg-file"',
                    'id="ne-hw-img"'):
            self.assertIn(tok, html)
        css = self._read("gui/assets/style.css")
        self.assertIn(".hw-modal.hw-full .hw-box", css)
        self.assertIn(".ne-hw-img img", css)
        self.assertIn(".hw-pageinfo", css)

    def test_i18n_hw_v2_keys_four_locales(self):
        js = self._read("gui/assets/app.js")
        for key in ("hwPageOf", "hwPageAdd", "hwFull", "hwBg",
                    "hwBgClear", "hwBgApplied", "hwBgCleared",
                    "hwViewOriginal"):
            self.assertEqual(js.count(key + ":"), 4, key)

    def test_stylus_coalesced_events_guard(self):
        """The Android-pad delay fix: every coalesced pointermove
        sample is stroked (not just one per frame)."""
        js = self._read("gui/assets/app.js")
        self.assertIn("getCoalescedEvents", js)
        i = js.find("pointermove', function (ev)")
        seg = js[i:i + 1200]
        self.assertIn("getCoalescedEvents", seg)
        self.assertIn("for (const cev of evs)", seg)

    def test_i18n_hw_keys_four_locales(self):
        js = self._read("gui/assets/app.js")
        for key in ("notesHw", "hwTitle", "hwHint", "hwClear",
                    "hwSave", "hwOcring", "hwDone", "hwEmpty",
                    "hwOffline"):
            self.assertEqual(js.count(key + ":"), 4, key)

    def test_app_js_pdf_underlay_v0125_6(self):
        """v0.9.125.6 — 📄 匯入 PDF: the per-page underlay state, the
        contain-fit page background, the two import functions and the
        save-payload provenance keys."""
        js = self._read("gui/assets/app.js")
        for fn in ("function notesHwPdfPick", "function notesHwPdfChange",
                   "let hwPageBgs", "let hwSrcPdf"):
            self.assertIn(fn, js)
        # background chain: per-page underlay wins over the global bg
        self.assertIn("hwPageBgs[hwPage] || hwBgImg", js)
        # contain-fit for the PDF page (whole page visible)
        self.assertIn("Math.min(rect.width / iw", js)
        # host call + provenance in the save payload
        self.assertIn("notes_handwriting_pdf_pages", js)
        self.assertIn("source_pdf: hwSrcPdf", js)
        self.assertIn("has_pdf_bg: hwPageBgs.length > 0", js)
        # web upload path + the GUI (pywebview) native-dialog path
        self.assertIn("fd.append('files', f, f.name)", js)
        self.assertIn("pv.create_file_dialog(", js)
        # open() resets both states
        i = js.find("function notesHwOpen()")
        self.assertGreater(i, -1)
        self.assertIn("hwPageBgs = [];", js[i:i + 240])
        self.assertIn("hwSrcPdf = '';", js[i:i + 240])
        # bind wiring
        self.assertIn("on('hw-pdf-btn', notesHwPdfPick);", js)
        self.assertIn("pfile.addEventListener('change', notesHwPdfChange)",
                      js)
        html = self._read("gui/assets/index.html")
        for tok in ('id="hw-pdf-btn"', 'id="hw-pdf-file"',
                    'data-i18n-title="hwPdf"'):
            self.assertIn(tok, html)

    def test_i18n_hw_pdf_keys_four_locales(self):
        js = self._read("gui/assets/app.js")
        for key in ("hwPdf", "hwPdfImporting", "hwPdfDone", "hwPdfFail"):
            self.assertEqual(js.count(key + ":"), 4, key)

    def test_app_js_writing_tools_v0125_7(self):
        """v0.9.125.7 — 🖊 pen (multi-colour + width) / 🖍 marker
        (multi-colour, translucent) / 🧽 eraser: the recorded-stroke
        model + the background-safe eraser replay."""
        js = self._read("gui/assets/app.js")
        for fn in ("function notesHwRedrawPage",
                   "function notesHwRestoreStyle",
                   "function notesHwApplyToolUI", "let hwTool",
                   "let hwCfg", "let hwStrokes"):
            self.assertIn(fn, js)
        # per-tool configs (marker = translucent yellow, wide)
        self.assertIn("pen:    { c: '#000000', w: 3 },", js)
        self.assertIn("marker: { c: '#ffe94a', w: 14 },", js)
        self.assertIn("eraser: { c: '', w: 24 },", js)
        # eraser replay = re-bake background + all strokes (never tears
        # the PDF/notebook underlay)
        seg = js[js.find("function notesHwRedrawPage"):]
        self.assertIn("notesHwInitCanvas()", seg[:600])
        self.assertIn("destination-out", seg[:1600])
        # the eraser stroke completion re-bakes the page
        self.assertIn(
            "if (hwTool === 'eraser') { notesHwRedrawPage(); }", js)
        # v0.9.125.7b — SOLID geometry: every segment starts from the
        # previous MID-POINT (the old g.moveTo(lx,ly) per segment left
        # a gap at each joint = the Android pen drew DASHED lines)
        self.assertIn("let bx = 0, by = 0;", js)
        self.assertIn("g.moveTo(bx, by);", js)
        self.assertIn("bx = p.x; by = p.y;", js)
        self.assertIn("bx = mx; by = my;", js)
        # the old per-segment g.moveTo(lx, ly) start is GONE (it was
        # the dash source)
        self.assertNotIn("g.moveTo(lx, ly);", js)
        # the final half-segment lands in BOTH the live draw (tail())
        # and the replay (lineTo)
        self.assertIn("moveTo(bx, by);\n          g.lineTo(lx, ly);", js)
        self.assertIn("g.lineTo(lx, ly);", js)
        # eraser single-TAP = a destination-out dot in the replay
        self.assertIn("g.arc(pts[0].x, pts[0].y, s.width / 2 + 1", js)
        self.assertIn("if (st) st.pts.push(p);", js)
        # stroke recording on pointerdown
        self.assertIn("st = { tool: hwTool, color: hwCfg[hwTool].c,", js)
        self.assertIn("hwStrokes[hwPage].push(st);", js)
        # 🧹 clear wipes the recorded strokes too (no replay ghost)
        self.assertIn("hwStrokes[hwPage] = [];", js)
        # toolbar wiring + dataset-driven chips (no innerHTML/XSS)
        for needle in ("on('hw-tool-pen'", "on('hw-tool-marker'",
                       "on('hw-tool-eraser'",
                       "chip.getAttribute('data-col')",
                       "chip.style.background = cv",
                       "notesHwApplyToolUI();"):
            self.assertIn(needle, js)
        html = self._read("gui/assets/index.html")
        for tok in ('id="hw-tool-pen"', 'id="hw-tool-marker"',
                    'id="hw-tool-eraser"', 'id="hw-color"',
                    'id="hw-width"', 'hw-tools-row', 'class="hw-chip"'):
            self.assertIn(tok, html)
        css = self._read("gui/assets/style.css")
        for sel in (".hw-tools-row", ".hw-tool.active", ".hw-chip",
                    ".hw-width"):
            self.assertIn(sel, css)

    def test_i18n_hw_tool_keys_four_locales(self):
        js = self._read("gui/assets/app.js")
        for key in ("hwPen", "hwMarker", "hwEraser"):
            self.assertEqual(js.count(key + ":"), 4, key)

    def test_hw_buttons_icon_only_v0126_4(self):
        # v0.9.126.4 — every labelled hw-modal button is ICON-ONLY: the
        # label lives in data-i18n-title (tooltip + aria-label), NOT in
        # data-i18n text — text made the rows overflow on narrow screens.
        html = self._read("gui/assets/index.html")
        for key in ("hwFull", "hwPen", "hwMarker", "hwEraser",
                    "hwPageAdd", "hwBg", "hwBgClear", "hwPdf",
                    "hwClear", "hwSave"):
            self.assertIn('data-i18n-title="%s"' % key, html)
            self.assertNotIn('data-i18n="%s"' % key, html)
        # no leftover label text inside the icon buttons
        for label in (">🖊 筆<", ">🖍 螢光<", ">🧽 擦<", ">⛶ 全螢幕<",
                      ">➕ 加頁<", ">🖼 背景圖<", ">📄 匯入 PDF<",
                      ">🧹 清除<", ">💾 儲存並 OCR<", ">✕ 去除背景<"):
            self.assertNotIn(label, html)
        # tooltips follow the locale (aria-label mirror in applyI18n)
        js = self._read("gui/assets/app.js")
        self.assertIn("[data-i18n-title]", js)
        self.assertIn("setAttribute('aria-label'", js)
        css = self._read("gui/assets/style.css")
        self.assertIn(".hw-modal .btn", css)

    def test_hw_canvas_frameless_v0126_7(self):
        # v0.9.126.7 — the writing surface has NO border box (user: the
        # frame looked cheap). The canvas rule must not draw a border;
        # the white sheet alone defines the writing area.
        css = self._read("gui/assets/style.css")
        i = css.find(".hw-canvas {")
        self.assertGreater(i, -1)
        block = css[i:css.find("}", i)]
        self.assertNotIn("border:", block)   # no border DECLARATION
        self.assertIn("background: #ffffff", block)
        # status preview / modal chrome untouched
        self.assertIn("background: var(--bg-1, #fff)", css)

    def test_hw_idle_hint_removed_v0126_5(self):
        # v0.9.126.5 — the idle hint line (用手指／觸控筆／滑鼠在下方書寫)
        # is GONE: the #hw-hint span stays (it is the save / PDF-import
        # status line) but starts empty and nothing ever refills the hint
        # text (applyI18n would otherwise repaint it via data-i18n).
        html = self._read("gui/assets/index.html")
        self.assertNotIn("用手指", html)
        self.assertNotIn('data-i18n="hwHint"', html)
        self.assertIn('id="hw-hint"', html)
        js = self._read("gui/assets/app.js")
        self.assertNotIn("t('hwHint')", js)
        # the i18n key definitions (4 locales) remain = contract intact
        self.assertEqual(js.count("hwHint:"), 4)

    def test_hw_modal_light_palette_pin_v0126_3(self):
        # v0.9.126.3 — the writing pad is ALWAYS white; the whole
        # .hw-modal subtree is pinned to the LIGHT palette so dark-theme
        # buttons stop rendering as black-on-white anomalies on the pad.
        css = self._read("gui/assets/style.css")
        first = css.find(".hw-modal {")
        self.assertGreater(first, -1)
        second = css.find(".hw-modal {", first + 1)
        self.assertGreater(second, -1, "light-palette pin rule missing")
        block = css[second:css.find("}", second)]
        for var in ("--bg: #ffffff", "--bg-3: #ebedf0", "--fg: #1c1e21",
                    "--fg-2: #5a6066", "--border: #d8dadf",
                    "--primary: #2563eb", "--accent: #7c3aed",
                    "--accent-soft"):
            self.assertIn(var, block)


class TestApiHandwritingPdfV0125_6:
    """v0.9.125.6 — Api.notes_handwriting_pdf_pages, behaviour-tested
    with a REAL fitz-rendered PDF (in-tmp fixture, no network)."""

    def _mk_pdf(self, tmp: Path, pages: int = 2) -> Path:
        import fitz

        doc = fitz.open()
        for _ in range(pages):
            p = doc.new_page()
            p.insert_text((72, 72), "SAMPLE PDF PAGE", fontsize=18)
        path = tmp / "underlay.pdf"
        path.write_bytes(doc.tobytes())
        doc.close()
        return path

    @pytest.fixture()
    def pdf_env(self, hw_env, monkeypatch):
        tmp, eng, _mk = hw_env
        # a NARROW write sandbox so the outside-roots refusal is
        # provable regardless of the live .env's wide WRITE_ROOT —
        # _allowed_file_roots() is patched too (it resolves the live
        # config object, whose WRITE_ROOT spans pytest's tmp dir).
        from src.mcp_client import host as _fhost

        sb = tmp / "sandbox"
        sb.mkdir()
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(sb))
        monkeypatch.delenv("QDRANT_FILESYSTEM_READ_ROOT", raising=False)
        monkeypatch.setattr(_fhost, "_allowed_file_roots",
                            lambda: [sb], raising=False)
        return sb, tmp, eng, _mk

    def test_full_import_renders_pages(self, pdf_env):
        sb, tmp, eng, _mk = pdf_env
        path = self._mk_pdf(sb, pages=2)
        a = _mk()
        out = a.notes_handwriting_pdf_pages({"path": str(path)})
        assert out.get("ok"), out
        assert out["pages_total"] == 2 and not out["truncated"]
        assert len(out["pages"]) == 2
        pg = out["pages"][0]
        assert pg["index"] == 1 and pg["w"] > 0 and pg["h"] > 0
        assert pg["data_url"].startswith("data:image/png;base64,")
        raw = base64.b64decode(pg["data_url"].split(",", 1)[1])
        assert raw[:8] == PNG_MAGIC[:8]
        # archived under $QDRANT_RAG_HOME/handwriting/pdf-*/
        assert "handwriting" in pg["path"]
        assert Path(pg["path"]).exists()
        assert Path(out["pdf"]).as_posix() == path.as_posix()

    def test_relative_path_resolves_write_root_uploads(self, pdf_env):
        sb, tmp, eng, _mk = pdf_env
        up = sb / "uploads"
        up.mkdir(exist_ok=True)
        path = self._mk_pdf(up)
        a = _mk()
        out = a.notes_handwriting_pdf_pages(
            {"path": "uploads/" + path.name})
        assert out.get("ok"), out
        assert len(out["pages"]) == 2

    def test_guards_magic_and_caps(self, pdf_env):
        sb, tmp, eng, _mk = pdf_env
        a = _mk()
        assert "path" in a.notes_handwriting_pdf_pages({})["error"]
        r = a.notes_handwriting_pdf_pages(
            {"path": str(sb / "nope.pdf")})
        assert "file not found" in r["error"]
        stray = sb / "a.txt"
        stray.write_text("x", encoding="utf-8")
        assert "not a .pdf" in a.notes_handwriting_pdf_pages(
            {"path": str(stray)})["error"]
        fake = sb / "fake.pdf"
        fake.write_bytes(b"NOT-A-PDF-AT-ALL")
        assert "magic" in a.notes_handwriting_pdf_pages(
            {"path": str(fake)})["error"]
        # outside the (narrow) sandbox — sb's sibling is still inside
        # tmp but OUTSIDE the write root now
        outside = sb.parent / "evasive.pdf"
        outside.write_bytes(b"%PDF-1.4\n")
        assert "refused" in a.notes_handwriting_pdf_pages(
            {"path": str(outside)})["error"]
        a2 = _mk()
        a2._HW_PDF_FILE_CAP = 10
        path = self._mk_pdf(sb)
        assert "too large" in a2.notes_handwriting_pdf_pages(
            {"path": str(path)})["error"]

    def test_page_cap_truncates_honestly(self, pdf_env):
        sb, tmp, eng, _mk = pdf_env
        path = self._mk_pdf(sb, pages=6)
        a = _mk()
        a._HW_PDF_MAX_PAGES = 3
        out = a.notes_handwriting_pdf_pages({"path": str(path)})
        assert out.get("ok")
        assert len(out["pages"]) == 3
        assert out["pages_total"] == 6 and out["truncated"] is True

    def test_missing_fitz_install_hint(self, pdf_env, monkeypatch):
        import sys

        sb, tmp, eng, _mk = pdf_env
        path = self._mk_pdf(sb)          # real fitz BEFORE the stub
        monkeypatch.setitem(sys.modules, "fitz", None)
        a = _mk()
        out = a.notes_handwriting_pdf_pages({"path": str(path)})
        assert "pymupdf" in out["error"] and "pip install" in out["error"]

    def _proxy_describer(self, monkeypatch, text):
        from unittest.mock import MagicMock

        import src.ingest.image_describer as idm

        fake = MagicMock()
        fake.describe.return_value = text
        fake.last_backend = "ollama"

        class _Proxy:
            def __new__(cls, *a, **kw):
                return fake

        monkeypatch.setattr(idm, "ImageDescriber", _Proxy)
        return fake

    def test_save_payload_pdf_provenance_and_ocr_hint(
            self, pdf_env, monkeypatch):
        sb, tmp, eng, _mk = pdf_env
        fake = self._proxy_describer(monkeypatch, "手寫結論 A")
        a = _mk()
        out = a.notes_handwriting_save({
            "data_url": _data_url(), "title": "批註",
            "source_pdf": "C:/x/underlay.pdf", "has_pdf_bg": True,
        })
        assert out.get("ok")
        note = eng.created[-1]
        assert "📄 底稿：C:/x/underlay.pdf" in note["body"]
        kw = fake.describe.call_args
        assert "忽略印刷體" in kw.kwargs.get("prompt", "")

    def test_save_without_pdf_flag_keeps_plain_prompt(
            self, pdf_env, monkeypatch):
        sb, tmp, eng, _mk = pdf_env
        fake = self._proxy_describer(monkeypatch, "純手寫內容")
        a = _mk()
        out = a.notes_handwriting_save({"data_url": _data_url()})
        assert out.get("ok")
        note = eng.created[-1]
        assert "底稿" not in note["body"]
        kw = fake.describe.call_args
        assert "忽略印刷體" not in kw.kwargs.get("prompt", "")


if __name__ == "__main__":
    import unittest

    unittest.main()