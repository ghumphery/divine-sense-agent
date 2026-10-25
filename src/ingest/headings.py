"""Heading-aware document segmentation (v0.9.113.0 — ROADMAP STAIR item).

Detects ATX markdown headings (``^#{1,6} title``) in loaded document text and
provides:

* ``heading_sections(text)`` — split the text into heading-delimited sections
  with an accumulated ``heading_path`` (``"H1 > H2"`` stack); the preamble
  before the first heading gets an empty path.
* ``detect_heading_path(text, last_path)`` — single-chunk reverse lookup used
  by the backfill tool: the LAST heading line inside the chunk wins; a chunk
  with no heading line inherits ``last_path`` (sequential carry-over), which
  is correct for sections split across chunk boundaries except for the
  leading fragment of a mid-chunk heading (documented best-effort).

Our loaders already emit ATX-compatible heading lines:
  - markdown files carry their native ``#`` headings;
  - PPTX loader writes ``# Slide N: title``;
  - DOCX loader (v0.9.113.0) prefixes Heading-1..6 paragraphs with ``#``*n.

Zero external dependencies; never raises for malformed heading lines.
"""
from __future__ import annotations

import re

# ATX heading: 1-6 leading #, a space, and non-empty text. Trained on the
# shapes our loaders emit; setext (= underline) styles are deliberately not
# supported (none of the loaders produce them).
HEADING_LINE_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*?)\s*#*\s*$", re.MULTILINE)


def _is_heading_line(line: str) -> bool:
    return bool(HEADING_LINE_RE.match(line))


def heading_sections(text: str) -> list[dict]:
    """Split ``text`` into heading-delimited sections.

    Returns a list of dicts in document order::

        [
            {"path": "",            "level": 0, "title": "",      "text": preamble},
            {"path": "Title",       "level": 1, "title": "Title", "text": "..."},
            {"path": "Title > Sub", "level": 2, "title": "Sub",   "text": "..."},
            ...
        ]

    The first entry (empty path) is the preamble before the first heading;
    it is omitted when the text starts with a heading. A text with NO
    heading lines returns the single full-text section with an empty path.
    """
    matches = list(HEADING_LINE_RE.finditer(text))
    if not matches:
        return [{"path": "", "level": 0, "title": "", "text": text}]

    sections: list[dict] = []
    stack: list[tuple[int, str]] = []  # (level, title) accumulation

    # Preamble (text before the first heading line).
    if matches[0].start() > 0:
        preamble = text[:matches[0].start()]
        if preamble.strip():
            sections.append({"path": "", "level": 0, "title": "",
                             "text": preamble})

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        nxt_start = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body_end = nxt_start

        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = " > ".join(t for _, t in stack)

        sections.append({
            "path": path,
            "level": level,
            "title": title,
            "text": text[m.start():body_end],
        })
    return sections


def heading_for_text(text: str, last_path: str = "") -> tuple[str, str]:
    """Best-effort heading lookup for a single chunk (backfill path).

    Returns ``(heading_path, heading_title)``: the LAST ATX heading line
    found anywhere in the chunk wins (the heading that opened the section
    the chunk mostly belongs to); when the chunk carries no heading line it
    inherits ``last_path`` (empty if unknown) with an empty title.
    """
    matches = list(HEADING_LINE_RE.finditer(text))
    if not matches:
        return last_path, ""
    m = matches[-1]
    return m.group(2).strip(), m.group(2).strip()


def has_headings(text: str) -> bool:
    """True when the text contains at least one ATX heading line."""
    return bool(HEADING_LINE_RE.search(text))