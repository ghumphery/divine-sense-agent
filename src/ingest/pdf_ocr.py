"""Scanned-PDF OCR for the ingest pipeline (v0.9.61.0).

User request: 「針對掃描式PDF檔的攝入使用openrouter 和 ollama的OCR支援
檔型 辨識內容並歸檔匯入」 — a SCANNED pdf (image pages, no text layer)
extracts (near-)zero text with pypdf, so it silently ingested nothing.
This module detects scanned PDFs, extracts each page's embedded scan
(pypdf page.images — the largest image IS the scan; no new dependency:
pypdf>=4 + Pillow are already required), transcribes it through the
ImageDescriber vision chain (VISION_PROVIDER_CHAIN: Ollama Cloud →
OpenRouter → SiliconFlow → OpenAI-compatible, OCR_PROMPT verbatim
transcription), and loader.py splices the text under
``--- Page N (OCR) ---`` markers so the normal chunk → embed → upsert
pipeline ARCHIVES it like any other text.

Env: QDRANT_INGEST_OCR (default on) / QDRANT_PDF_OCR_MAX_PAGES
(default 100). Without any vision provider the OCR step is SKIPPED
with a printed note — never a crash (graceful degradation).
"""
from __future__ import annotations

import os
import tempfile
from io import BytesIO
from pathlib import Path

from src.ingest.image_describer import ImageDescriber

# A page with fewer extractable chars than this counts as image-only.
_MIN_TEXT_CHARS_PER_PAGE = 20
# Downscale cap for the longest image side (keeps each payload far
# under the ImageDescriber 4 MB cap even for 600 DPI scans).
_MAX_IMAGE_SIDE = 1600


def ocr_enabled() -> bool:
    """``QDRANT_INGEST_OCR`` (default on)."""
    return ((os.environ.get("QDRANT_INGEST_OCR", "on") or "on")
            .strip().lower() not in ("off", "0", "false", "no"))


def _ocr_max_pages() -> int:
    try:
        return max(1, int(os.environ.get(
            "QDRANT_PDF_OCR_MAX_PAGES", "100") or 100))
    except (TypeError, ValueError):
        return 100


def is_scanned_pdf(path: str | Path) -> bool:
    """True when EVERY page of the PDF has (almost) no text layer."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        pages = reader.pages
        if not pages:
            return False
        for page in pages:
            try:
                if len((page.extract_text() or "").strip()) \
                        >= _MIN_TEXT_CHARS_PER_PAGE:
                    return False
            except Exception:
                continue  # unreadable page still counts as image-only
        return True
    except Exception:
        return False


def extract_page_images(
    path: str | Path,
    max_pages: int | None = None,
) -> list[Path]:
    """Extract ONE image per PDF page (the largest embedded image —
    the scan itself) into a temp dir as ``page-NNNN.png``, downscaled
    to ``_MAX_IMAGE_SIDE`` so each payload stays far below the
    ImageDescriber 4 MB cap. Page numbers live in the file names
    (``page.stem.split('-')[1]``)."""
    from PIL import Image
    from pypdf import PdfReader

    cap = max_pages or _ocr_max_pages()
    out_dir = Path(tempfile.mkdtemp(prefix="pdf-ocr-"))
    reader = PdfReader(str(path))
    out: list[Path] = []
    for i, page in enumerate(reader.pages, 1):
        if i > cap:
            break
        try:
            best: bytes | None = None
            best_area = -1
            for img in page.images:
                data = img.data
                try:
                    with Image.open(BytesIO(data)) as im:
                        area = im.size[0] * im.size[1]
                except Exception:
                    continue
                if area > best_area:
                    best_area = area
                    best = data
            if not best:
                continue
            with Image.open(BytesIO(best)) as im:
                im = im.convert("RGB")
                w, h = im.size
                scale = _MAX_IMAGE_SIDE / max(w, h)
                if scale < 1.0:
                    im = im.resize((max(1, int(w * scale)),
                                    max(1, int(h * scale))),
                                   Image.LANCZOS)
                target = out_dir / f"page-{i:04d}.png"
                im.save(target, format="PNG", optimize=True)
                out.append(target)
        except Exception:
            continue  # page-level failure → skip that page
    return out


def ocr_scanned_pdf(
    path: str | Path,
    describer: ImageDescriber | None = None,
    max_pages: int | None = None,
) -> list[tuple[int, str]]:
    """OCR a scanned PDF → ``[(page_no, text)]`` (1-based, text-bearing
    pages only). Pages whose transcription fails or comes back as a
    fallback marker are skipped (never crash the ingest)."""
    if describer is None:
        describer = ImageDescriber()
    # ImageDescriber.enabled is a property (bool); test fakes often
    # provide a method — accept both shapes.
    enabled = getattr(describer, "enabled")
    if callable(enabled):
        enabled = enabled()
    if not enabled:
        print("  [ocr] no vision provider configured "
              "(OLLAMA_API_KEY / OPENROUTER_API_KEY / ...) — skipped")
        return []
    images = extract_page_images(path, max_pages)
    results: list[tuple[int, str]] = []
    for img in images:
        try:
            page_no = int(img.stem.split("-")[1])
        except (IndexError, ValueError):
            continue
        try:
            text = (describer.describe(
                img, prompt=ImageDescriber.OCR_PROMPT,
                max_tokens=2000) or "").strip()
        except Exception as e:  # describer never raises, but be safe
            print(f"  [ocr] page {page_no} failed: {e!r}")
            continue
        # fallback markers start with "[Image:" — not content
        if not text or text.startswith("[Image:"):
            continue
        results.append((page_no, text))
    return results