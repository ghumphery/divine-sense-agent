"""Telegram MarkdownV2 converter — standard (GFM-style) Markdown in,
Telegram MarkdownV2 out.

Why this exists
---------------
The ``send_telegram_markdown`` MCP tool already sends with
``parse_mode="MarkdownV2"``, but the LLM writes NORMAL markdown
(``**bold**``, ``# heading``, ``- list``, bare ``.`` …). Telegram's
MarkdownV2 is NOT normal markdown:

* bold is ``*x*`` (not ``**x**``), italic is ``_x_``,
  strikethrough is ``~x~`` (not ``~~x~~``);
* the characters ``_ * [ ] ( ) ~ ` > # + - = | { } . !`` must be
  escaped with a preceding ``\\`` everywhere OUTSIDE entities
  (a bare ``.`` or ``-`` in an unescaped sentence 400s the whole
  sendMessage with "can't parse entities");
* inside ``pre``/``code`` entities only `` ` `` and ``\\`` need
  escaping; inside the ``(...)`` of an inline link only ``)`` and
  ``\\`` need escaping.

This module converts the markdown the LLM actually produces into
strict MarkdownV2 so the tool renders bold/italic/code/quotes/
tables instead of 400-ing or dumping raw asterisks.

Converter model (placeholder strategy):

1. Extract the constructs that have EXACT Telegram semantics
   (fenced code, inline code, tables, links) and render them to
   their final MarkdownV2 form under ``\\x00N\\x00`` placeholders.
2. Convert line-level constructs (headings → bold, bullets → ``• ``,
   task lists → ✅/☐, blockquotes → ``>`` markers, horizontal rules
   → em dash).
3. Convert inline emphasis (``***x***`` → ``*_x_*``, ``**x**`` →
   ``*x*``, ``__x__`` underline, ``~~x~~`` → ``~x~``, ``*x*``/``_x_``
   → ``_x_`` — word-boundary aware so ``snake_case`` survives).
4. Escape every remaining MarkdownV2 special in the raw text.
5. Restore the placeholders (insertion order makes nested fragments
   resolve correctly).
"""
from __future__ import annotations

import re

__all__ = ["markdown_to_telegram_v2", "escape_mdv2", "escape_mdv2_url",
           "escape_mdv2_code"]

# Characters Telegram requires to be escaped OUTSIDE entities.
_MDV2_SPECIALS = set("_*[]()~`>#+-=|{}.!\\")

# Compact bullet — safe (not in the specials set), renders nicely.
_BULLET = "•"


def escape_mdv2(text: str) -> str:
    """Escape every MarkdownV2 special character in ``text``."""
    out = []
    for ch in text:
        if ch in _MDV2_SPECIALS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def escape_mdv2_url(text: str) -> str:
    """Inside the ``(...)`` of an inline link only ``)`` and ``\\``
    must be escaped (per the Bot API docs)."""
    return text.replace("\\", "\\\\").replace(")", "\\)")


def escape_mdv2_code(text: str) -> str:
    """Inside ``pre``/``code`` entities only `` ` `` and ``\\`` must
    be escaped."""
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _safe_lang(lang: str) -> str:
    """MarkdownV2 pre language tokens are restricted to a small
    alphabet — keep the alnum subset, drop the fence language
    entirely when nothing survives."""
    return re.sub(r"[^A-Za-z0-9_+-]", "", lang or "")[:64]


# __PART_CONVERTER__


def markdown_to_telegram_v2(text: str) -> str:
    """Convert normal markdown to strict Telegram MarkdownV2."""
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "").replace("\u2028", "\n")

    fragments: list[str] = []

    def protect(fragment: str) -> str:
        # Placeholder keys survive general escaping (NUL is outside
        # the escapable range) and resolve in insertion order, so
        # nested fragments substitute correctly.
        key = f"\x00{len(fragments)}\x00"
        fragments.append(fragment)
        return key

    # ── 1) fenced code blocks (```lang\n body ```) ────────────────
    def _fence(m: re.Match) -> str:
        lang = _safe_lang(m.group(1) or "")
        body = escape_mdv2_code(m.group(2).rstrip("\n"))
        head = f"```{lang}\n" if lang else "```\n"
        return protect(f"{head}{body}\n```")

    text = re.sub(r"```([^\n`]*)\n?(.*?)```", _fence, text, flags=re.S)
    # Unclosed fence — treat everything after the opening as pre.
    m = re.search(r"```([^\n`]*)\n?(.*)$", text, flags=re.S)
    if m and "`" * 3 not in (m.group(2) or ""):
        lang = _safe_lang(m.group(1) or "")
        body = escape_mdv2_code(m.group(2).rstrip("\n"))
        head = f"```{lang}\n" if lang else "```\n"
        text = text[:m.start()] + protect(f"{head}{body}\n```")

    # ── 2) tables → pre blocks (readable monospace) ──────────────
    table_lines: list[str] = []
    converted_lines: list[str] = []

    def _flush_table() -> None:
        if not table_lines:
            return
        rows = [r.rstrip() for r in table_lines]
        body = escape_mdv2_code("\n".join(rows))
        converted_lines.append(protect(f"```\n{body}\n```"))
        table_lines.clear()

    for line in text.split("\n"):
        if line.lstrip().startswith("|"):
            table_lines.append(line)
        else:
            _flush_table()
            converted_lines.append(line)
    _flush_table()
    text = "\n".join(converted_lines)

    # ── 3) inline code ────────────────────────────────────────────
    text = re.sub(
        r"`([^`\n]+)`",
        lambda m: protect(f"`{escape_mdv2_code(m.group(1))}`"),
        text,
    )

    # ── 4) inline links [text](url) ───────────────────────────────
    text = re.sub(
        r"\[([^\]\n]+)\]\(([^()\s]*)\)",
        lambda m: protect(
            f"[{escape_mdv2(m.group(1))}]({escape_mdv2_url(m.group(2))})"),
        text,
    )

    # ── 5) line-level conversions ─────────────────────────────────
    out_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        # headings → bold
        mh = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if mh:
            content = mh.group(1).strip()
            out_lines.append(
                protect(f"*{escape_mdv2(content)}*") if content else "")
            continue
        # horizontal rules
        if re.fullmatch(r"\s*([-*_])\1{2,}\s*", line):
            out_lines.append(protect("—"))
            continue
        # task-list bullets → • ✅ / • ☐
        mt = re.match(r"(\s*)[-*+]\s+\[([ xX])\]\s*(.*)$", line)
        if mt:
            mark = "✅" if mt.group(2).lower() == "x" else "☐"
            prefix = protect(f"{mt.group(1)}• {mark}")
            out_lines.append(f"{prefix} {mt.group(3)}")
            continue
        # plain bullets → •
        mb = re.match(r"(\s*)[-*+]\s+(.*)$", line)
        if mb:
            prefix = protect(f"{mb.group(1)}• ")
            out_lines.append(f"{prefix}{mb.group(2)}")
            continue
        # blockquote → keep the > marker, escape the content later
        if stripped.startswith(">"):
            prefix = protect("> ")
            out_lines.append(f"{prefix}{stripped[1:].lstrip()}")
            continue
        out_lines.append(line)
    text = "\n".join(out_lines)

    # ── 6) inline emphasis (word-boundary aware) ──────────────────
    text = re.sub(
        r"\*\*\*([^*\n]+?)\*\*\*",
        lambda m: protect(f"*_{escape_mdv2(m.group(1))}_*"), text)
    text = re.sub(
        r"\*\*([^*\n]+?)\*\*",
        lambda m: protect(f"*{escape_mdv2(m.group(1))}*"), text)
    text = re.sub(
        r"(?<![\w\\])__([^_\n]+?)__(?![\w])",
        lambda m: protect(f"__{escape_mdv2(m.group(1))}__"), text)
    text = re.sub(
        r"~~([^~\n]+?)~~",
        lambda m: protect(f"~{escape_mdv2(m.group(1))}~"), text)
    text = re.sub(
        r"(?<![\w\\])\*([^*\n]+?)\*(?![\w])",
        lambda m: protect(f"_{escape_mdv2(m.group(1))}_"), text)
    text = re.sub(
        r"(?<![\w\\])_([^_\n]+?)_(?![\w])",
        lambda m: protect(f"_{escape_mdv2(m.group(1))}_"), text)

    # ── 7) escape everything that is left ─────────────────────────
    text = escape_mdv2(text)

    # ── 8) restore placeholders (insertion order) ─────────────────
    # Reversed order: outermost fragments (registered LAST) resolve
    # FIRST, so a nested placeholder key inside their content is then
    # expanded by the earlier entries (e.g. bold containing a link).
    for i in reversed(range(len(fragments))):
        text = text.replace(f"\x00{i}\x00", fragments[i])
    return text