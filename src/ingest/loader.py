"""File loader — multi-format text extraction.

Supports 16+ formats via stdlib + pypdf / python-docx / python-pptx /
ebooklib. Format detection is by file extension. Each loader returns
the extracted text as a plain str (UTF-8).

v0.2.3: also exposes a ``FileLoader`` class with ``extract_media()``
(DOCX/PPTX image extraction) and ``scan_directory()`` (filter by
supported extensions), mirroring milvus-mcp-agent so the new
``IngestPipeline`` can share its workflow. The module-level
``load_file()`` and per-format ``_load_*()`` functions are kept for
backward compatibility with the v0.1.x pipeline.
"""
from __future__ import annotations

import mimetypes
import os
import zipfile
from pathlib import Path
from typing import Iterable


def _read_text(path: Path) -> str:
    """Plain-text fallback (utf-8 → chardet auto-detect)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            import chardet
            raw = path.read_bytes()
            enc = (chardet.detect(raw) or {}).get("encoding") or "utf-8"
            return raw.decode(enc, errors="replace")
        except ImportError:
            return path.read_text(encoding="utf-8", errors="replace")


def _load_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages, 1):
        parts.append(f"\n--- Page {i} ---\n{page.extract_text() or ''}")
    text = "\n".join(parts)
    # v0.9.61.0 — scanned-PDF OCR: image pages with no text layer used
    # to silently ingest nothing. When every page is text-less, run the
    # vision OCR chain (Ollama/OpenRouter) and splice the recognized
    # content in with (OCR) page markers so the normal chunk → embed →
    # upsert pipeline archives it.
    from src.ingest import pdf_ocr
    if pdf_ocr.ocr_enabled() and pdf_ocr.is_scanned_pdf(path):
        try:
            pages_ocr = pdf_ocr.ocr_scanned_pdf(path)
        except Exception as e:
            print(f"  [loader] pdf OCR failed: {e!r}")
            pages_ocr = []
        if pages_ocr:
            ocr_map = dict(pages_ocr)
            parts = []
            for i, page in enumerate(reader.pages, 1):
                ocr_text = ocr_map.get(i, "")
                tag = "OCR" if ocr_text else "text"
                content = ocr_text or (page.extract_text() or "")
                parts.append(f"\n--- Page {i} ({tag}) ---\n{content}")
            text = "\n".join(parts)
        else:
            text += ("\n[loader] scanned PDF produced no OCR text "
                     "(no vision provider configured or all pages "
                     "unreadable)\n")
    return text


def _docx_heading_prefix(para) -> str:
    """ATX ``#``*n prefix for DOCX Heading styles (v0.9.113.0 STAIR).

    Gated by ``QDRANT_HEADING_METADATA`` (default OFF = "" for byte-identical
    legacy output). Accepts built-in English ("Heading 1".."Heading 6") and
    zh-Word style names ("標題 1".."標題 6"); an unknown style name yields ""
    (plain paragraph). Level derived from the trailing digit, clamped 1-6.
    """
    raw = ((os.environ.get("QDRANT_HEADING_METADATA", "") or "")
           .strip().lower())
    if raw not in ("1", "true", "yes", "on"):
        return ""
    try:
        name = (para.style.name or "") if para.style is not None else ""
    except Exception:
        return ""
    import re as _re
    m = _re.search(r"heading\s*([1-6])|標題\s*([1-6])", name, _re.I)
    if not m:
        return ""
    level = int(m.group(1) or m.group(2) or 1)
    return "#" * max(1, min(6, level)) + " "


def _load_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    parts: list[str] = []
    for para in doc.paragraphs:
        if not para.text.strip():
            continue
        parts.append(_docx_heading_prefix(para) + para.text)
    return "\n".join(parts)


def _load_pptx(path: Path) -> str:
    from pptx import Presentation
    prs = Presentation(str(path))
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"# Slide {i}")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    txt = para.text.strip()
                    if txt:
                        parts.append(f"- {txt}")
    return "\n".join(parts)


def _load_epub(path: Path) -> str:
    """Extract text from an EPUB file (ebooklib).

    Walks the book spine in document order, concatenating the stripped
    text of each document item. Skips non-document items (images, styles,
    nav, NCX) automatically — ebooklib's ITEM_DOCUMENT filter is the
    canonical way to keep only the reading content.
    """
    from ebooklib import epub, ITEM_DOCUMENT
    from bs4 import BeautifulSoup

    book = epub.read_epub(str(path))
    parts: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        # item.get_content() returns bytes; decode + parse HTML.
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _load_xlsx(path: Path) -> str:
    """XLSX (Open XML Excel workbook) → per-sheet pipe tables.

    v0.9.120.1 — chat Excel uploads were a DEAD END: the pipeline
    skip-listed ``.xlsx``, ``read_file`` treats it as binary and
    nothing parsed it, so "uploaded" spreadsheets were unusable.
    openpyxl (``read_only`` + ``data_only`` — formulas resolve to
    their cached values; no write, no style parsing) renders:

        ## Sheet: <name>
        <cell> | <cell> | ...

    Discipline: fully-empty rows are skipped, trailing empty cells
    trimmed, each cell stringified and capped at 200 chars, and each
    sheet's rendered text capped at 200_000 chars (very large sheets
    degrade instead of blowing the chunker). ``.xls`` / ``.xlsm``
    stay unsupported (BIFF + macro workbooks need the xlrd engine;
    out of scope here).
    """
    import openpyxl

    wb = openpyxl.load_workbook(
        path, read_only=True, data_only=True,
    )
    parts: list[str] = []
    for ws in wb.worksheets:
        rows: list[str] = []
        chars = 0
        for row in ws.iter_rows(values_only=True):
            cells = []
            for v in row:
                s = "" if v is None else str(v)
                if len(s) > 200:
                    s = s[:200] + "…"
                cells.append(s)
            while cells and not cells[-1]:
                cells.pop()
            if not any(cells):
                continue
            line = " | ".join(cells)
            rows.append(line)
            chars += len(line)
            if chars > 200_000:
                rows.append("[sheet truncated at 200000 chars]")
                break
        if rows:
            parts.append(f"\n## Sheet: {ws.title}\n" + "\n".join(rows))
    wb.close()
    return "\n".join(parts)


_LOADERS = {
    ".txt": _read_text, ".md": _read_text, ".py": _read_text,
    ".js": _read_text, ".ts": _read_text, ".json": _read_text,
    ".yaml": _read_text, ".yml": _read_text, ".csv": _read_text,
    ".html": _read_text, ".css": _read_text, ".xml": _read_text,
    ".pdf": _load_pdf,
    ".docx": _load_docx,
    ".pptx": _load_pptx,
    ".xlsx": _load_xlsx,
    ".epub": _load_epub,
}


def load_file(path: str | Path) -> str:
    """Detect format by extension and extract text. Returns "" on failure."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    # Skip Office / LibreOffice lock files (created when a doc is open).
    if p.name.startswith("~$"):
        print(f"  [loader] skip lock file: {p.name}")
        return ""
    loader = _LOADERS.get(p.suffix.lower())
    if loader is None:
        # Unknown extension → treat as plain text
        loader = _read_text
    try:
        return loader(p) or ""
    except Exception as e:
        print(f"  [loader] failed to read {p.name}: {e!r}")
        return ""


# ─── FileLoader class (new in v0.2.3, mirrors milvus-mcp-agent) ─────────
# The module-level load_file() above is a thin wrapper around the same
# per-extension loaders. The class form below emits `[Image: ...]` markers
# for DOCX/PPTX inline pictures (filled in by ImageDescriber later) and
# supports scan_directory() + extract_media().

class FileLoader:
    """Multi-format loader with image-aware DOCX/PPTX extraction.

    DOCX/PPTX output includes ``[Image: name | alt='...']`` placeholders
    at the position each inline picture appears in the document. The
    pipeline layer can then call ``IngestPipeline._fill_image_descriptions``
    to swap them for vision-generated bilingual descriptions.

    v0.2.4 adds structured DOCX/PPTX extraction (mirrors milvus-mcp-agent):
      - DOCX: paragraphs + inline image placeholders + table rows +
        header/footer
      - PPTX: per-slide ``# Slide N: <title>`` heading + bullets +
        ``## Table`` (markdown) for table shapes + ``> [image: ...]``
        markers for picture shapes
    """

    @staticmethod
    def load_file(file_path: str | Path) -> str:
        """Load a file and extract its text content based on extension.

        For DOCX/PPTX, returns the *structured* output (image markers
        + slide titles + tables). Use ``load_file_plain`` if you want
        the v0.1.x flat-text behaviour.
        """
        p = Path(file_path)
        suffix = p.suffix.lower()
        if suffix == ".docx":
            return FileLoader._load_docx_structured(p)
        if suffix == ".pptx":
            return FileLoader._load_pptx_structured(p)
        return load_file(file_path)

    @staticmethod
    def load_file_plain(file_path: str | Path) -> str:
        """Backward-compatible v0.1.x loader (flat text for DOCX/PPTX)."""
        return load_file(file_path)

    # ─── DOCX / PPTX structured extractors (v0.2.4) ────────────────
    @staticmethod
    def _load_docx_structured(path: Path) -> str:
        """DOCX → paragraphs + image placeholders + tables + header/footer.

        Output format:
          <para text>
          <para text>
          [Image: <name> | alt='<descr>']      (inline pictures)
          [Table Row] <cell1> | <cell2> | ...
          [Header] <text>
          [Footer] <text>
        """
        from docx import Document
        from docx.oxml.ns import qn

        doc = Document(path)
        text_parts: list[str] = []

        # 1. Paragraphs (with inline image placeholders)
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(_docx_heading_prefix(para) + para.text)
            for shape in para._p.iter(qn("w:drawing")):
                docPr = shape.find(".//" + qn("wp:docPr"))
                if docPr is not None:
                    name = docPr.get("name", "image")
                    descr = docPr.get("descr", "")
                    label = descr if descr else "no alt text"
                    text_parts.append(f"[Image: {name} | alt='{label}']")

        # 2. Tables (cells in all rows)
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(
                    cell.text.strip() for cell in row.cells if cell.text.strip()
                )
                if row_text:
                    text_parts.append(f"[Table Row] {row_text}")

        # 3. Headers / Footers
        for section in doc.sections:
            if section.header and section.header.paragraphs:
                for para in section.header.paragraphs:
                    if para.text.strip():
                        text_parts.append(f"[Header] {para.text}")
            if section.footer and section.footer.paragraphs:
                for para in section.footer.paragraphs:
                    if para.text.strip():
                        text_parts.append(f"[Footer] {para.text}")

        return "\n\n".join(text_parts)

    @staticmethod
    def _load_pptx_structured(path: Path) -> str:
        """PPTX → structured Markdown per slide.

        Output format per slide:
          # Slide N: <slide title if any>

          - <bullet text>
          - <bullet text>

          ## Table
          | h1 | h2 | ... |
          | -- | -- | --- |
          | v1 | v2 | ... |

          > [image: <name> | alt='<descr>']     (picture shapes)

        The structured form preserves slide titles, table structure,
        and image positions — much better RAG retrieval signal than
        flat text (especially for technical decks).
        """
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        prs = Presentation(path)
        A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
        PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"

        def _shape_picture_info(shape):
            """Return (name, alt_text) for a PICTURE shape, else (None, None)."""
            if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                return None, None
            cNvPr = shape._element.find(
                f".//{{{PIC_NS}}}nvPicPr/{{{A_NS}}}cNvPr"
            )
            name = cNvPr.get("name", "image") if cNvPr is not None else "image"
            descr = cNvPr.get("descr", "") if cNvPr is not None else ""
            return name, (descr or "no alt text")

        def _shape_title(shape):
            """Heuristic: first placeholder with type TITLE / CENTER_TITLE."""
            try:
                if shape.is_placeholder and shape.placeholder_format is not None:
                    t = shape.placeholder_format.type
                    if t in (13, 14, 15):  # TITLE / CENTER_TITLE
                        return shape.text_frame.text.strip()
            except Exception:
                pass
            return None

        def _shape_table_text(shape):
            """Render a table shape as a Markdown table. Returns '' if empty."""
            if not getattr(shape, "has_table", False):
                return ""
            tbl = shape.table
            rows = []
            for row in tbl.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                rows.append(cells)
            if not rows:
                return ""
            header = rows[0]
            sep = ["---"] * len(header)
            md_rows = [
                "| " + " | ".join(header) + " |",
                "| " + " | ".join(sep) + " |",
            ]
            for r in rows[1:]:
                # Pad short rows so the table stays rectangular
                cells = r + [""] * (len(header) - len(r))
                md_rows.append("| " + " | ".join(cells) + " |")
            return "\n".join(md_rows)

        def _shape_paragraphs(shape):
            """Return non-empty paragraphs from a text frame shape."""
            if not shape.has_text_frame:
                return []
            out = []
            for para in shape.text_frame.paragraphs:
                t = para.text.strip()
                if t:
                    out.append(t)
            return out

        out: list[str] = []

        for slide_idx, slide in enumerate(prs.slides, 1):
            # Find slide title (from placeholders)
            slide_title = None
            for shape in slide.shapes:
                if shape.has_text_frame:
                    cand = _shape_title(shape)
                    if cand:
                        slide_title = cand
                        break

            out.append(f"# Slide {slide_idx}: {slide_title}" if slide_title
                       else f"# Slide {slide_idx}")

            slide_md: list[str] = []

            for shape in slide.shapes:
                # 1. Tables
                if getattr(shape, "has_table", False):
                    tbl_md = _shape_table_text(shape)
                    if tbl_md:
                        slide_md.append("## Table\n" + tbl_md)
                    continue

                # 2. Pictures: emit a marker; the pipeline will replace
                #    it with a vision description later.
                name, alt = _shape_picture_info(shape)
                if name is not None:
                    slide_md.append(f"> [image: {name} | alt='{alt}']")
                    continue

                # 3. Title placeholders are already in the H1 above; skip
                if shape.is_placeholder and _shape_title(shape):
                    continue

                # 4. Other text frames: bullets
                paras = _shape_paragraphs(shape)
                if not paras:
                    continue
                for p in paras:
                    slide_md.append(f"- {p}")

            if slide_md:
                out.append("\n\n".join(slide_md))

        return "\n\n".join(out)

    @staticmethod
    def extract_media(doc_path: str | Path, out_dir: str | Path) -> list[Path]:
        """Extract embedded images from a DOCX or PPTX file.

        Both formats are ZIP containers; DOCX stores media under
        ``word/media/`` and PPTX under ``ppt/media/``.

        Returns the list of extracted image paths.
        """
        src = Path(doc_path)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() not in (".docx", ".pptx"):
            return []
        prefix = "word/media/" if src.suffix.lower() == ".docx" else "ppt/media/"
        images: list[Path] = []
        try:
            with zipfile.ZipFile(src) as z:
                for name in z.namelist():
                    if name.startswith(prefix) and not name.endswith("/"):
                        data = z.read(name)
                        target = out / Path(name).name
                        # Avoid collision by suffixing an index.
                        if target.exists():
                            stem, suf = target.stem, target.suffix
                            i = 1
                            while target.exists():
                                target = out / f"{stem}_{i}{suf}"
                                i += 1
                        target.write_bytes(data)
                        images.append(target)
        except zipfile.BadZipFile:
            pass
        return images

    @staticmethod
    def scan_directory(directory: str | Path | None = None) -> list[Path]:
        """Scan a directory for supported files and return their paths.

        Filters by ``config.ingest.supported_extensions`` — the canonical
        set of formats we know how to load. Returns sorted paths.
        """
        from src.config import config

        target_dir = Path(directory) if directory else config.ingest.watch_dir
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            return []
        supported = config.ingest.supported_extensions
        files: list[Path] = []
        for ext in supported:
            files.extend(target_dir.rglob(f"*{ext}"))
        return sorted(files)
