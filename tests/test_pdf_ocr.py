"""v0.9.61.0 — scanned-PDF OCR ingest (Ollama/OpenRouter vision chain).

Covers:
* ``is_scanned_pdf`` — image-only PDFs detected; text-layer PDFs and
  missing files rejected.
* ``ocr_scanned_pdf`` — vision transcription per page, fallback
  markers skipped, missing-provider skip.
* loader ``_load_pdf`` — (OCR) page markers splice into the document
  so the normal pipeline archives the recognized content.
* ``ImageDescriber.describe(prompt=...)`` — prompt threading +
  prompt-aware cache key (OCR vs description never collide).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingest import pdf_ocr  # noqa: E402
from src.ingest.image_describer import ImageDescriber  # noqa: E402


import pytest

def _blank_pdf(tmp_path, pages: int = 2) -> str:
    """A real PDF with `pages` EMPTY pages (no text layer) — used for
    the detection + loader-integration tests."""
    from pypdf import PdfWriter

    p = tmp_path / f"blank-{pages}.pdf"
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    with open(p, "wb") as f:
        w.write(f)
    return str(p)


def _tmp_png() -> str:
    from PIL import Image

    p = Path(tempfile.mkdtemp(prefix="t-")) / "x.png"
    Image.new("RGB", (10, 10), "white").save(p, format="PNG")
    return str(p)


class TestDetectionAndEnv:
    def test_blank_pdf_is_scanned(self, tmp_path):
        assert pdf_ocr.is_scanned_pdf(_blank_pdf(tmp_path)) is True

    def test_text_layer_pdf_is_not_scanned(self, monkeypatch):
        class _P:
            def extract_text(self):
                return "hello world with plenty of text here"

        class _R:
            pages = [_P()]

        import pypdf
        monkeypatch.setattr(pypdf, "PdfReader", lambda _p: _R())
        assert pdf_ocr.is_scanned_pdf("fake.pdf") is False

    def test_missing_file_not_scanned(self, tmp_path):
        assert pdf_ocr.is_scanned_pdf(tmp_path / "no.pdf") is False

    def test_ocr_enabled_default_and_off(self, monkeypatch):
        monkeypatch.delenv("QDRANT_INGEST_OCR", raising=False)
        assert pdf_ocr.ocr_enabled() is True
        monkeypatch.setenv("QDRANT_INGEST_OCR", "off")
        assert pdf_ocr.ocr_enabled() is False

    def test_max_pages_default_and_bad_value(self, monkeypatch):
        monkeypatch.delenv("QDRANT_PDF_OCR_MAX_PAGES", raising=False)
        assert pdf_ocr._ocr_max_pages() == 100
        monkeypatch.setenv("QDRANT_PDF_OCR_MAX_PAGES", "bogus")
        assert pdf_ocr._ocr_max_pages() == 100
        monkeypatch.setenv("QDRANT_PDF_OCR_MAX_PAGES", "5")
        assert pdf_ocr._ocr_max_pages() == 5


class TestOcrScannedPdf:
    def _fake_describer(self, texts):
        d = MagicMock(spec=ImageDescriber)
        d.enabled.return_value = True
        d.describe.side_effect = list(texts)
        return d

    def test_uses_ocr_prompt_and_page_numbers(self, tmp_path, monkeypatch):
        pdf = _blank_pdf(tmp_path, pages=2)
        imgs = [tmp_path / "page-0001.png", tmp_path / "page-0002.png"]
        for p in imgs:
            p.write_bytes(b"png")
        monkeypatch.setattr(pdf_ocr, "extract_page_images",
                            lambda path, max_pages=None: imgs)
        d = self._fake_describer(["第一頁文字", "第二頁文字"])
        res = pdf_ocr.ocr_scanned_pdf(pdf, describer=d)
        assert res == [(1, "第一頁文字"), (2, "第二頁文字")]
        kwargs = d.describe.call_args.kwargs
        assert kwargs["prompt"] == ImageDescriber.OCR_PROMPT
        assert kwargs["max_tokens"] == 2000

    def test_fallback_markers_and_empty_skipped(
        self, tmp_path, monkeypatch,
    ):
        pdf = _blank_pdf(tmp_path)
        imgs = [tmp_path / "page-0001.png",
                tmp_path / "page-0002.png",
                tmp_path / "page-0003.png"]
        for p in imgs:
            p.write_bytes(b"png")
        monkeypatch.setattr(pdf_ocr, "extract_page_images",
                            lambda path, max_pages=None: imgs)
        d = self._fake_describer(["[Image: page | vision error]",
                                  "good page text", ""])
        res = pdf_ocr.ocr_scanned_pdf(pdf, describer=d)
        # only the CONTENT-bearing page survives (marker + empty skip)
        assert res == [(2, "good page text")]

    def test_no_provider_returns_empty(self, tmp_path):
        pdf = _blank_pdf(tmp_path)
        d = MagicMock(spec=ImageDescriber)
        d.enabled.return_value = False
        assert pdf_ocr.ocr_scanned_pdf(pdf, describer=d) == []


class TestLoaderIntegration:
    def test_scanned_pdf_gets_ocr_markers(self, tmp_path, monkeypatch):
        import src.ingest.loader as loader_mod

        pdf = _blank_pdf(tmp_path, pages=2)
        monkeypatch.setattr(
            pdf_ocr, "ocr_scanned_pdf",
            lambda path, describer=None, max_pages=None:
                [(1, "OCR辨識的內容甲"), (2, "OCR辨識的內容乙")],
        )
        monkeypatch.setenv("QDRANT_INGEST_OCR", "on")
        text = loader_mod.load_file(pdf)
        assert "--- Page 1 (OCR) ---" in text
        assert "OCR辨識的內容甲" in text
        assert "OCR辨識的內容乙" in text

    def test_ocr_off_leaves_loader_untouched(self, tmp_path, monkeypatch):
        import src.ingest.loader as loader_mod

        pdf = _blank_pdf(tmp_path, pages=1)
        monkeypatch.setenv("QDRANT_INGEST_OCR", "off")
        called = []
        monkeypatch.setattr(
            pdf_ocr, "ocr_scanned_pdf",
            lambda *a, **k: called.append(1) or [],
        )
        text = loader_mod.load_file(pdf)
        assert called == []
        assert "(OCR)" not in text

    def test_no_ocr_result_appends_note(self, tmp_path, monkeypatch):
        import src.ingest.loader as loader_mod

        pdf = _blank_pdf(tmp_path, pages=1)
        monkeypatch.setenv("QDRANT_INGEST_OCR", "on")
        monkeypatch.setattr(pdf_ocr, "ocr_scanned_pdf",
                            lambda *a, **k: [])
        text = loader_mod.load_file(pdf)
        assert "produced no OCR text" in text


class TestDescriberPromptOverride:
    @pytest.fixture(autouse=True)
    def _clear_shared_cache(self):
        """v0.9.104.5's process-level `_SHARED_CACHE` survives the
        instance-dict ``d._cache.clear()`` — an earlier describe of the
        SAME png bytes (this suite reuses `_tmp_png()`) would answer
        the OCR pass from the shared cache and the API assert sees
        nothing. Start empty, restore empty (suite convention)."""
        from src.ingest.image_describer import _shared_cache_clear

        _shared_cache_clear()
        yield
        _shared_cache_clear()

    def _describer(self, monkeypatch, captured):
        from types import SimpleNamespace

        d = ImageDescriber.__new__(ImageDescriber)
        d._cache = {}
        d._cache_max = 8
        d._vision_candidates = lambda: [(
            "k", "https://x/v1", "m", "ollama")]

        def fake_create(model, messages, max_tokens):
            captured.update({"model": model, "max_tokens": max_tokens,
                             "text": messages[0]["content"][0]["text"]})
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="OCR-TEXT"))])

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = fake_create
        monkeypatch.setattr("openai.OpenAI", lambda **kw: fake_client)
        return d

    def test_ocr_prompt_reaches_the_api(self, monkeypatch):
        captured: dict = {}
        d = self._describer(monkeypatch, captured)
        out = d.describe(_tmp_png(), prompt=ImageDescriber.OCR_PROMPT,
                         max_tokens=2000)
        assert out == "OCR-TEXT"
        assert captured["text"] == ImageDescriber.OCR_PROMPT
        assert captured["max_tokens"] == 2000

    def test_default_prompt_unchanged(self, monkeypatch):
        captured: dict = {}
        d = self._describer(monkeypatch, captured)
        d.describe(_tmp_png())
        assert captured["text"] == ImageDescriber.PROMPT
        assert captured["max_tokens"] == 500

    def test_ocr_result_not_served_from_description_cache(
        self, monkeypatch,
    ):
        captured: dict = {}
        d = self._describer(monkeypatch, captured)
        img = _tmp_png()
        d.describe(img)                       # description pass
        d._cache.clear()                      # isolate the OCR pass
        out = d.describe(img, prompt=ImageDescriber.OCR_PROMPT,
                         max_tokens=2000)
        assert out == "OCR-TEXT"
        # the OCR call went through the API (2nd create), not the cache
        assert captured["max_tokens"] == 2000