"""Lightweight markdown rendering (Windows cmd compatible).

Port of milvus-mcp-agent/src/ui/md_render.py. Zero external deps;
ANSI-aware for BBS-style chat output.

v0.2.7+: GFM tables (``| col | col |``) are detected and rendered as
standalone ANSI-boxed blocks (CJK width-aware via ``unicodedata``).
The BBS-style ``_bbs_post_md`` caller can therefore keep its narrow
``inner`` width for prose without breaking cell alignment.

v0.8.3+: full GFM coverage — numbered / nested / task lists, italic,
strikethrough, blockquote, horizontal rule, inline links, inline
images, and markdown escaping (``\\*``, ``\\|``, ``\\` ``). Zero-dep
regex-based; nothing imported beyond the stdlib.
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata

from mcp.types import TextContent  # noqa: F401  (kept for parity)


# ─── ANSI helpers ─────────────────────────────────────────────
class Style:
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    UNDERLINE = "\x1b[4m"
    ITALIC = "\x1b[3m"
    STRIKE = "\x1b[9m"
    DIM = "\x1b[2m"
    CYAN = "\x1b[36m"
    BLUE = "\x1b[34m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    RED = "\x1b[31m"
    GREY = "\x1b[90m"      # bright black (v0.2.7 had "\x90m" — bare ESC bug)


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    return True


_USE_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"{code}{text}{Style.RESET}"


def _strip_ansi(text: str) -> str:
    """Drop every CSI escape sequence (SGR + cursor moves).

    Public helper for tests and for callers that need to compute
    display width / re-wrap without ANSI noise.
    """
    return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)


# ─── Inline / block transforms ─────────────────────────────────
_INLINE_CODE = re.compile(r"`([^`]+)`")
# Bold: **text** (non-greedy so we don't span adjacent pairs).
_BOLD = re.compile(r"\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*")
# Italic: *text* or _text_, but NOT ** (already covered) and NOT
# inside a word (so ``foo*bar*baz`` doesn't match). Use look-arounds.
_ITALIC = re.compile(
    r"(?<![\w*])[*_](?=\S)([^*\n_]+?)(?<=\S)[*_](?![\w*])"
)
# Strikethrough: ~~text~~ (GFM).
_STRIKE = re.compile(r"~~(?=\S)([^~\n]+?)(?<=\S)~~")
# Inline link: [label](url)
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# Inline image: ![alt](url)
_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# Escape sequence: \X where X is one of * _ ` | ~ [ ] < > # + - . !
_ESCAPED = re.compile(r"\\([\\*_`|\~\[\]<>+#\-.!])")

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# Bullet (unordered): -, *, or + followed by space. Leading indent
# captured so nested bullets get extra indent in the output.
_BULLET = re.compile(r"^(?P<indent>[ \t]*)(?:[-*+])\s+(?P<rest>.*)$")
# Numbered (ordered) list: 1.  2.  ... — any digits + dot + space.
_ORDERED = re.compile(r"^(?P<indent>[ \t]*)\d+\.\s+(?P<rest>.*)$")
# Task list: - [ ] or - [x] / - [X]
_TASK = re.compile(
    r"^(?P<indent>[ \t]*)[-*+]\s+\[(?P<mark>[ xX])\]\s+(?P<rest>.*)$"
)
# Blockquote: line starting with optional spaces + '> '
_BLOCKQUOTE = re.compile(r"^(?P<indent>[ \t]*)>\s?(?P<rest>.*)$")
# Horizontal rule: --- or *** or ___ (3+ of -, *, _) on its own line.
_HR = re.compile(r"^[ \t]*([-*_])[ \t]*\1[ \t]*\1[-*_ \t]*$")

# GFM table separator row: | --- | :---: | ---: |
_GFM_TABLE_SEP = re.compile(
    r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$"
)
# Generic table row: any line with at least one pipe, contains content.
_GFM_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _wcswidth(s: str) -> int:
    """Display width: ASCII = 1, ambiguous = 1, fullwidth/CJK = 2.

    Stand-in for the optional ``wcwidth`` package (which we don't pull
    in to keep zero-deps). Slightly wrong for emoji vs CJK, but that's
    how Windows GDI/Terminal render them too.
    """
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        if eaw in ("F", "W"):        # Fullwidth / Wide
            w += 2
        # A (Ambiguous) → treat as 1 (matches most monospace fonts).
        # N (Neutral), H (Halfwidth), Na (Narrow) → 1.
        # None → 1.
        else:
            w += 1
    return w


def _bullet_prefix(indent: str) -> str:
    """Render nested bullets as `•` at top level, `◦` at 2nd, `▪` 3rd+."""
    depth = len(indent.replace("\t", "    ")) // 2
    return ("  " * depth) + ("•" if depth == 0 else "◦" if depth == 1 else "▪")


def _ordered_prefix(indent: str, n: int) -> str:
    depth = len(indent.replace("\t", "    ")) // 2
    return ("  " * depth) + f"{n}."


def _render_inline(text: str) -> str:
    """Apply inline markdown transforms to a single line.

    Order matters: escape first (so literal ``\\*`` doesn't get treated
    as italic later), then image > link > strikethrough > bold >
    italic > inline-code (code last so its content is preserved).

    Escape handling: backslash-escaped chars are stashed in the
    Unicode private-use area (U+E000–U+E001) so the italic / bold
    regexes don't re-match them. After every other transform runs
    we swap the stash back for literal characters.
    """
    stash: list[str] = []
    _PUA_START = "\uE000"
    _PUA_END = "\uE001"

    def _esc_sub(m: re.Match) -> str:
        idx = len(stash)
        stash.append(m.group(1))
        return f"{_PUA_START}{idx}{_PUA_END}"

    def _unstash(s: str) -> str:
        def repl(pm: re.Match) -> str:
            return stash[int(pm.group(1))]
        return re.sub(rf"{_PUA_START}(\d+){_PUA_END}", repl, s)

    # 1) backslash escapes → stash literal char, leave PUA marker.
    text = _ESCAPED.sub(_esc_sub, text)
    # 2) inline image: ![alt](url) → [image: alt](url)
    text = _IMAGE.sub(
        lambda m: _c(Style.BLUE, f"[image: {m.group(1)}]") +
                  f"({m.group(2)})",
        text,
    )
    # 3) inline link: [label](url) → label (url) — terminal-safe
    text = _LINK.sub(
        lambda m: _c(Style.CYAN + Style.UNDERLINE, m.group(1)) +
                  f" ({m.group(2)})",
        text,
    )
    # 4) strikethrough: ~~text~~ → struck-through text
    text = _STRIKE.sub(
        lambda m: f"\u2003{Style.STRIKE}{m.group(1)}{Style.RESET}\u2003",
        text,
    )
    # 5) bold: **text** — wrap with U+2003 (EM SPACE) sentinels
    # so it's visually distinct even without ANSI.
    text = _BOLD.sub(
        lambda m: f"\u2003{Style.BOLD}{m.group(1)}{Style.RESET}\u2003",
        text,
    )
    # 6) italic: *text* or _text_ — wrap with U+2002 (EN SPACE) sentinels
    # so the result is visually distinct from plain text even when
    # the terminal doesn't render SGR italic.
    text = _ITALIC.sub(
        lambda m: f"{Style.ITALIC}\u2002{m.group(1)}\u2002{Style.RESET}",
        text,
    )
    # 7) inline code: `text` — applied last so its body is preserved
    text = _INLINE_CODE.sub(
        lambda m: _c("\x1b[7m\x1b[36m", " " + m.group(1) + " "), text,
    )
    return _unstash(text)


def _parse_table_row(line: str) -> list[str] | None:
    """Split a GFM table row on ``|`` and strip cells.

    Returns None if the line isn't a real table row (must contain at
    least one pipe and have non-empty content after stripping).
    """
    if "|" not in line:
        return None
    s = line.strip()
    # Drop leading / trailing empty pipes
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells = [c.strip() for c in s.split("|")]
    return [c for c in cells]


def _render_table(rows: list[list[str]]) -> list[str]:
    """Render a GFM table as standalone ANSI-boxed rows (CJK-aware).

    ``rows`` is a list of cell-lists already stripped of the GFM
    separator: ``rows[0]`` is the header and ``rows[1:]`` is the body.
    """
    if len(rows) < 2:
        return []
    header = rows[0]
    body = rows[1:]

    n_cols = len(header)
    # Make every row exactly n_cols cells (pad with "" if LLM shorted)
    def pad_row(r: list[str]) -> list[str]:
        return list(r) + [""] * (n_cols - len(r))

    header = pad_row(header)
    body = [pad_row(r) for r in body]

    # Compute per-column max CJK width
    widths = [_wcswidth(header[j]) for j in range(n_cols)]
    for r in body:
        for j in range(n_cols):
            widths[j] = max(widths[j], _wcswidth(r[j]))

    def cell(text: str, j: int) -> str:
        # Inline-style emphasis (bold + code) inside cells, but no
        # block constructs — keep cell single-line.
        out = _render_inline(text)
        # We need to pad to visible width; ANSI escape sequences are
        # invisible, so re-compute via the *original* cell width.
        pad = widths[j] - _wcswidth(text)
        return out + (" " * pad) if pad > 0 else out

    def fmt_row(cells: list[str]) -> str:
        return "│ " + " │ ".join(cell(c, j) for j, c in enumerate(cells)) + " │"

    bar = "─"
    hbar_parts = [bar * (widths[j] + 2) for j in range(n_cols)]
    top = "┌─" + "─┬─".join(hbar_parts) + "─┐"
    sep = "├─" + "─┼─".join(hbar_parts) + "─┤"
    bot = "└─" + "─┴─".join(hbar_parts) + "─┘"

    out = [_c(Style.BOLD, top)]
    out.append(_c(Style.BOLD, fmt_row(header)))
    out.append(_c(Style.GREY, sep))
    for r in body:
        out.append(fmt_row(r))
    out.append(_c(Style.GREY, bot))
    return out


def render_markdown(text: str) -> str:
    """Render a markdown string for the terminal.

    v0.2.7+: GFM table blocks are extracted and rendered as full-width
    ANSI-boxed (CJK-aware) blocks. They are NOT split across caller
    lines so a downstream ``_bbs_post_md`` still sees one line per
    logical row — caller is responsible for *not* wrapping them inside
    a narrow BBS-style frame (they go outside the frame instead).

    v0.8.3+: full GFM coverage — numbered / nested / task lists,
    italic, strikethrough, blockquote, horizontal rule, inline links
    and images, and backslash escapes. Order: fenced code → table →
    heading → hr → blockquote → list (ordered / bullet / task) →
    blank / default. Inside lists, ordered numbering is tracked per
    indent level so ``1. 2. 3.`` and a nested ``a. b. c.`` both
    auto-increment correctly.
    """
    out: list[str] = []
    in_code = False
    code_lang = ""
    code_buf: list[str] = []
    lines = text.splitlines()
    i = 0
    # Per-indent ordered-list counter so nested ordered lists start
    # at 1 again instead of continuing the outer sequence.
    ordered_counter: dict[str, int] = {}

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # ── fenced code blocks (with optional language tag)
        if stripped.startswith("```"):
            if not in_code:
                in_code = True
                code_lang = stripped[3:].strip()
                code_buf = []
            else:
                in_code = False
                lang_tag = (
                    _c(Style.DIM, f" [{code_lang}]") if code_lang else ""
                )
                out.append(_c(Style.GREY, "  ┌─ code" + lang_tag))
                for cl in code_buf:
                    out.append(_c(Style.GREY, f"  │ {cl}"))
                out.append(_c(Style.GREY, "  └"))
                code_lang = ""
            i += 1
            continue
        if in_code:
            code_buf.append(raw)
            i += 1
            continue

        # ── blank line: paragraph separator
        if not stripped:
            out.append("")
            ordered_counter.clear()
            i += 1
            continue

        # ── horizontal rule: --- *** ___ (3+ of same char on its own)
        if _HR.match(stripped):
            out.append(_c(Style.GREY, "─" * min(len(stripped) + 4, 64)))
            i += 1
            continue

        # ── GFM table
        if (
            "|" in raw
            and i + 1 < len(lines)
            and _GFM_TABLE_SEP.match(lines[i + 1].strip())
        ):
            header_row = _parse_table_row(raw)
            if header_row is not None:
                rows: list[list[str]] = [header_row]
                i += 2
                while i < len(lines):
                    nxt = lines[i]
                    if not nxt.strip():
                        break
                    if _GFM_TABLE_SEP.match(nxt.strip()):
                        i += 1
                        continue
                    row = _parse_table_row(nxt)
                    if row is None:
                        break
                    rows.append(row)
                    i += 1
                out.extend(_render_table(rows))
                ordered_counter.clear()
                continue

        # ── heading (1-6 # marks)
        m = _HEADING.match(raw)
        if m:
            level = len(m.group(1))
            content = _render_inline(m.group(2))
            if level == 1:
                out.append(_c(
                    Style.BOLD + Style.UNDERLINE + Style.CYAN,
                    "▌ " + content,
                ))
            elif level == 2:
                out.append(_c(Style.BOLD + Style.CYAN, "▌ " + content))
            else:
                out.append(_c(Style.BOLD + Style.BLUE, "▌ " + content))
            i += 1
            ordered_counter.clear()
            continue

        # ── blockquote: '> text' (collect contiguous block)
        bm = _BLOCKQUOTE.match(raw)
        if bm:
            block: list[str] = [_render_inline(bm.group("rest"))]
            j = i + 1
            while j < len(lines):
                bn = _BLOCKQUOTE.match(lines[j])
                if bn:
                    block.append(_render_inline(bn.group("rest")))
                    j += 1
                elif not lines[j].strip():
                    break
                else:
                    # Continuation paragraph (no '>' prefix).
                    block.append(_render_inline(lines[j]))
                    j += 1
            out.append(_c(Style.GREEN, "│ " + block[0]))
            for cont in block[1:]:
                out.append(_c(Style.GREEN, "│ " + cont))
            i = j
            ordered_counter.clear()
            continue

        # ── task list: - [ ] / - [x]
        tm = _TASK.match(raw)
        if tm:
            indent = tm.group("indent")
            mark = tm.group("mark")
            rest = _render_inline(tm.group("rest"))
            box = (
                _c(Style.GREEN, "[x]") if mark.lower() == "x"
                else _c(Style.GREY, "[ ]")
            )
            out.append(
                _bullet_prefix(indent) + " " + box + " " + rest
            )
            i += 1
            continue

        # ── ordered list: 1. 2. 3.  (auto-increment per indent depth)
        om = _ORDERED.match(raw)
        if om:
            indent = om.group("indent")
            ordered_counter[indent] = ordered_counter.get(indent, 0) + 1
            n = ordered_counter[indent]
            out.append(
                _ordered_prefix(indent, n) + " "
                + _render_inline(om.group("rest"))
            )
            i += 1
            continue

        # ── unordered bullet (incl. nested)
        um = _BULLET.match(raw)
        if um:
            indent = um.group("indent")
            out.append(
                _bullet_prefix(indent) + " "
                + _render_inline(um.group("rest"))
            )
            ordered_counter.clear()
            i += 1
            continue

        # ── default: paragraph line (inline transforms only)
        out.append(_render_inline(raw))
        ordered_counter.clear()
        i += 1

    # Strip trailing blank lines so callers don't get spurious
    # whitespace when chaining multiple render calls.
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)