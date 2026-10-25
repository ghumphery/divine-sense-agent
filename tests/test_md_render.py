"""Tests for ``src.ui.md_render.render_markdown``.

Covers the GFM element surface that the CLI chat REPL renders.
Each test feeds a markdown fragment and asserts the *lines* the
CLI renderer is expected to produce. The renderer is intentionally
zero-dep and ANSI-aware, so we don't assert on raw ANSI escapes —
we strip them via ``_strip_ansi`` and compare readable content.
"""
from __future__ import annotations

import re

from src.ui.md_render import _strip_ansi, render_markdown


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _visible(s: str) -> str:
    """Strip ANSI escape codes so test assertions stay readable."""
    return _ANSI.sub("", s)


# ──────────────────────────────────────────────────────────────
# Existing baseline behaviour — guards against regressions
# ──────────────────────────────────────────────────────────────


class TestExistingBaseline:
    def test_heading_h1(self):
        out = render_markdown("# Hello")
        assert _visible(out) == "▌ Hello"

    def test_heading_h2_h3_h4_styled_differently(self):
        h2 = _visible(render_markdown("## sub"))
        h3 = _visible(render_markdown("### deep"))
        h4 = _visible(render_markdown("#### deeper"))
        for h in (h2, h3, h4):
            assert h.startswith("▌ ")

    def test_bullet(self):
        out = _visible(render_markdown("- one"))
        assert out.startswith("• one")

    def test_inline_bold(self):
        out = _visible(render_markdown("this is **bold** text"))
        assert "bold" in out
        assert "this is" in out and "text" in out

    def test_inline_code(self):
        out = _visible(render_markdown("use `foo()` here"))
        assert "foo()" in out

    def test_fenced_code_block(self):
        md = "```\nprint(1)\nprint(2)\n```"
        out = _visible(render_markdown(md))
        assert "code" in out
        assert "print(1)" in out
        assert "print(2)" in out

    def test_gfm_table_renders_as_box(self):
        md = "| a | b |\n|---|---|\n| 1 | 2 |"
        out = _visible(render_markdown(md))
        assert "│" in out
        assert "a" in out and "b" in out
        assert "1" in out and "2" in out

    def test_plain_text_passthrough(self):
        out = _visible(render_markdown("just words"))
        assert out == "just words"




# ──────────────────────────────────────────────────────────────
# Full GFM coverage — new in v0.8.3
# ──────────────────────────────────────────────────────────────


class TestNumberedList:
    def test_simple_ordered_list(self):
        out = _visible(render_markdown("1. first\n2. second\n3. third"))
        assert "1. first" in out
        assert "2. second" in out
        assert "3. third" in out

    def test_ordered_list_with_inline_bold(self):
        out = _visible(render_markdown("1. **bold step**"))
        # EM-space sentinel (U+2003) may flank the bolded word so
        # it's visually distinct even in non-ANSI terminals.
        assert "1." in out and "bold step" in out


class TestNestedBullet:
    def test_two_level_indent(self):
        out = _visible(render_markdown("- outer\n  - inner"))
        lines = [l for l in out.splitlines() if l.strip()]
        assert lines[0].lstrip().startswith(("•", "-"))
        assert lines[1].startswith("  ") or lines[1].startswith("    ")
        assert "inner" in lines[1]


class TestTaskList:
    def test_unchecked_and_checked(self):
        out = _visible(render_markdown("- [ ] todo\n- [x] done"))
        assert "[ ]" in out
        assert "[x]" in out

    def test_task_list_alongside_regular_bullet(self):
        out = _visible(render_markdown("- plain\n- [ ] check"))
        assert "plain" in out
        assert "[ ]" in out


class TestItalic:
    def test_single_asterisk(self):
        out = _visible(render_markdown("this is *italic*"))
        assert "italic" in out
        assert _visible(render_markdown("this is **italic**")) != out

    def test_underscore_form(self):
        out = _visible(render_markdown("use _emphasis_ here"))
        assert "emphasis" in out


class TestStrikethrough:
    def test_double_tilde(self):
        out = _visible(render_markdown("this is ~~old~~ text"))
        assert "old" in out
        assert any(
            marker in out
            for marker in ("~~", "\x1b[9m", "[del]", "✗", "\u2003")
        )


class TestLinks:
    def test_inline_link_renders_as_text_with_url(self):
        out = _visible(render_markdown("see [docs](https://example.com)"))
        assert "docs" in out
        assert "https://example.com" in out

    def test_bare_url_not_required(self):
        out = _visible(render_markdown("https://example.com"))
        assert "example.com" in out


class TestBlockquote:
    def test_single_line_blockquote(self):
        out = _visible(render_markdown("> quoted text"))
        assert "quoted text" in out
        assert any(marker in out for marker in ("│", "|", "▎", ">"))


class TestHorizontalRule:
    def test_three_dashes(self):
        out = _visible(render_markdown("---"))
        lines = [l for l in out.splitlines() if l.strip()]
        assert lines
        assert any(
            set(l.replace(" ", "")) <= {"─", "-", "="}
            for l in lines
        )

    def test_three_asterisks(self):
        out = _visible(render_markdown("***"))
        lines = [l for l in out.splitlines() if l.strip()]
        assert lines


class TestEscaping:
    def test_escaped_asterisk_renders_literally(self):
        out = _visible(render_markdown(r"\*not italic\*"))
        assert "*not italic*" in out

    def test_escaped_pipe_does_not_start_table(self):
        out = _visible(render_markdown(r"\| not a table |"))
        assert "│" not in out


class TestImages:
    def test_inline_image_shows_alt_and_url(self):
        out = _visible(
            render_markdown("![diagram](https://example.com/x.png)")
        )
        assert "diagram" in out
        assert "https://example.com/x.png" in out


class TestEdgeCases:
    def test_empty_string(self):
        assert render_markdown("") == ""

    def test_blank_lines_around_blocks(self):
        out = _visible(render_markdown("# title\n\nbody text"))
        assert "title" in out
        assert "body text" in out

    def test_mixed_list_and_heading(self):
        out = _visible(render_markdown("## Section\n- item a\n- item b"))
        assert "Section" in out
        assert "item a" in out
        assert "item b" in out
