"""In-process tool host (v0.8.0+).

Exposes the existing in-process Python functions (Qdrant search /
stats / web search / etc) as ``McpTool``-shaped adapters, so the
Agent's tool-calling loop doesn't have to special-case "local" tools
vs "remote MCP tools".

Three concrete hosts ship in v0.8.x:

* ``QdrantLocalHost``  - wraps ``Retriever`` + ``QdrantStore``
  (search_documents / list_documents / get_document_stats /
  remove_documents). v0.9.0+ also adds ``add_document`` and
  ``update_document`` so the LLM can ingest new content (large files
  via path with no context pollution, small files via inline content
  string) and overwrite existing documents. v0.9.1+ adds
  ``describe_image`` so the LLM can hand an image file (or inline
  base64) to the local vision model and get back a bilingual
  description + the raw bytes for multimodal chat models to
  consume directly. v0.9.2+ adds ``resize_image`` so the LLM can
  downscale large images (Pillow-backed, EXIF-preserving) to fit
  the 4 MB vision cap before re-calling ``describe_image``.
* ``WebSearchLocalHost`` - wraps ``OllamaWebSearch``
  (web_search / web_fetch).
* ``MetaLocalHost`` (v0.8.2+) - exposes ``list_mcp_tools`` itself
  so the LLM can introspect the live tool surface mid-conversation
  instead of guessing. Without this the agent falls back to
  ``web_search("MCP 功能")`` and gives a generic answer.

The user can register arbitrary ``LocalTool`` objects for anything
else (calculator, file readers, date helpers) - ``McpManager`` is
the single source of truth for the agent's tool surface.

Sandbox safety (v0.9.0+)
------------------------
``add_document`` / ``update_document`` / ``describe_image`` /
``resize_image`` refuse to touch anything outside
``QDRANT_IMPORT_ALLOWED_ROOT`` (default ``WATCH_DIR``).
``Path.resolve()`` is computed against both sides so ``..``
traversal is blocked; this is the only line of defence between
an LLM that wants to describe a screenshot and one that wants
to ``Path("/etc/passwd")`` for an evasion attempt.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import inspect
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .base import McpCallResult, McpTool

logger = logging.getLogger(__name__)


# File extensions ``add_document`` will accept for inline-content
# writes. Mirrors ``src.config._IngestNamespace.supported_extensions``
# so the loader / chunker downstream can actually process the file
# once we write it to the sandbox root. Anything else gets a clean
# error rather than a confusing loader-side crash.
_ADD_DOC_ALLOWED_EXTS: frozenset[str] = frozenset({
    ".txt", ".md", ".pdf", ".docx", ".pptx", ".epub",
    ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".csv", ".html", ".css", ".xml", ".log",
})


# v0.9.3+: filesystem MCP server. Split roots so users can
# grant read-only access to a wide tree (e.g. ``./documents``)
# while keeping write/delete operations pinned to a narrower
# directory (e.g. ``./scratch``). Defaults both fall back to
# ``WATCH_DIR`` for symmetry with the existing import sandbox
# so the feature is no-op until the operator opts in.
_FS_TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".tsv", ".html", ".css", ".scss", ".xml", ".svg",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".log", ".sql", ".gitignore", ".env",
    ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java", ".kt",
    ".rb", ".php", ".pl", ".lua", ".r",
})

# Binary extensions that ``search_content`` and ``read_file``
# refuse to load as text — same policy as the watcher.
_FS_BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".pdf", ".docx", ".pptx", ".xlsx",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib",
    ".mp3", ".mp4", ".wav", ".flac", ".ogg", ".avi", ".mkv",
    ".pyc", ".pyo", ".class", ".wasm",
})

# Dangerous extensions that ``write_file`` / ``append_file``
# refuse outright regardless of sandbox — the LLM should not
# be in the business of dropping new binaries or scripts that
# the OS will execute on sight.
_FS_DANGEROUS_WRITE_EXTS: frozenset[str] = frozenset({
    ".exe", ".dll", ".so", ".dylib",
    ".bat", ".cmd", ".sh", ".ps1", ".vbs", ".js", ".wsf",
    ".scr", ".com", ".cpl",
})

# Default ``max_bytes`` for ``read_file`` when the caller does
# not supply one — 64 KB keeps the LLM context manageable but
# large enough for source files.
_FS_DEFAULT_READ_MAX_BYTES = 64 * 1024

# Hard ceiling for ``read_file`` (regardless of caller) —
# protects against a buggy LLM asking for 1 GB.
_FS_READ_HARD_MAX_BYTES = 4 * 1024 * 1024

# ``get_tree`` and ``search_files`` default cap.
_FS_DEFAULT_MAX_ENTRIES = 1000


# v0.9.4+: diff_files + patch_file limits. Diff hunks can grow
# quickly on big edits; cap output so we don't blow the chat
# context window with a multi-MB patch. Patch *application*
# is bounded by the file size hard cap (4 MB) below.
_FS_DEFAULT_DIFF_CONTEXT = 3
_FS_DIFF_MAX_OUTPUT_BYTES = 4 * 1024 * 1024  # 4 MB
_FS_PATCH_MAX_PATCH_BYTES = 4 * 1024 * 1024  # 4 MB


def _fs_make_unified_diff(
    a_text: str,
    b_text: str,
    a_path: str,
    b_path: str,
    context: int = _FS_DEFAULT_DIFF_CONTEXT,
) -> str:
    """Build a unified-diff string from two text blobs.

    Uses stdlib ``difflib.unified_diff`` with ``lineterm=""`` so
    the output round-trips through ``patch_file`` without a
    trailing-CR mismatch. Returns an empty string when the
    two inputs are identical.
    """
    import difflib
    a_lines = a_text.splitlines(keepends=False)
    b_lines = b_text.splitlines(keepends=False)
    diff_lines = list(difflib.unified_diff(
        a_lines, b_lines,
        fromfile=a_path, tofile=b_path,
        lineterm="",
        n=context,
    ))
    return "\n".join(diff_lines)


def _fs_split_patch_hunks(patch_text: str) -> list[str]:
    """Split a unified diff into one entry per hunk block.

    Each hunk starts with ``@@`` and runs through the line
    before the next ``@@`` header. The leading ``--- `` /
    ``+++ `` file headers are glued to the FIRST hunk (most
    patch consumers expect that). Lines before the first
    ``@@`` are silently dropped.
    """
    chunks: list[list[str]] = []
    current: list[str] | None = None
    for line in patch_text.splitlines():
        if line.startswith("@@"):
            current = [line]
            chunks.append(current)
            continue
        if current is None:
            # Pre-hunk preamble (file headers, blank lines).
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            chunks[0].insert(0, line)
            continue
        current.append(line)
    return ["\n".join(c) for c in chunks]


def _fs_parse_hunk_header(
    header: str,
) -> tuple[int, int, int, int]:
    """Parse ``@@ -a,b +c,d @@``.

    Returns ``(a_start, a_len, b_start, b_len)``. Missing
    counts default to 1. Raises ``ValueError`` on malformed
    input — caller wraps and returns ``{"error": ...}``.
    """
    import re
    m = re.match(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
        header,
    )
    if not m:
        raise ValueError(f"malformed hunk header: {header!r}")
    a_start = int(m.group(1))
    a_len = int(m.group(2)) if m.group(2) is not None else 1
    b_start = int(m.group(3))
    b_len = int(m.group(4)) if m.group(4) is not None else 1
    return a_start, a_len, b_start, b_len


def _fs_apply_patch(
    original: str,
    patch_text: str,
) -> str:
    """Apply a unified diff to ``original`` and return the new text.

    Supports the strict subset that ``difflib.unified_diff``
    produces:

    * ``@@ -a,b +c,d @@`` headers (counts optional)
    * Body lines starting with `` ``, ``-``, ``+``
    * ``\\ No newline at end of file`` markers (skipped)
    * 1-based line numbers; ``-0`` falls back to anchor=1

    Refuses (with ``ValueError``) on:

    * Unknown line tags
    * Hunk anchors that overlap a previous hunk
    * Hunk anchors past end of original file
    * Context lines that don't match the source
    * Hunk bodies whose length disagrees with the header

    The caller wraps ``ValueError`` into ``{"error": ...}`` so
    chat-loop safety holds.
    """
    original_lines = original.splitlines()
    out: list[str] = []
    cursor = 0
    hunks = _fs_split_patch_hunks(patch_text)
    if not hunks:
        raise ValueError("patch contains no hunks")
    for hunk in hunks:
        body = hunk.splitlines()
        header = body[0]
        a_start, a_len, _b_start, _b_len = _fs_parse_hunk_header(
            header,
        )
        anchor_idx = a_start - 1
        if anchor_idx < 0:
            anchor_idx = 0
        if anchor_idx < cursor:
            raise ValueError(
                f"hunk anchor @ line {a_start} overlaps with "
                f"previous hunk applied up to line {cursor + 1}"
            )
        if anchor_idx > len(original_lines):
            raise ValueError(
                f"hunk anchor @ line {a_start} is past end of "
                f"file (length {len(original_lines)})"
            )
        out.extend(original_lines[cursor:anchor_idx])
        old_lines: list[str] = []
        new_lines: list[str] = []
        for line in body[1:]:
            tag = line[:1]
            content = line[1:]
            if tag == " ":
                old_lines.append(content)
                new_lines.append(content)
            elif tag == "-":
                old_lines.append(content)
            elif tag == "+":
                new_lines.append(content)
            elif tag == "\\":
                continue
            else:
                raise ValueError(
                    f"unknown hunk line tag {tag!r}"
                )
        # Tolerate the ``unified_diff`` quirk where a single
        # line can be emitted with ``-1,1`` (count 1) but
        # ``+0,0`` for pure insertion — we only refuse if the
        # counts are wildly off (likely a stale patch).
        if abs(len(old_lines) - a_len) > 1:
            raise ValueError(
                f"hunk header says {a_len} old line(s) "
                f"but body has {len(old_lines)}"
            )
        actual = original_lines[
            anchor_idx: anchor_idx + len(old_lines)
        ]
        if actual != old_lines:
            for i, (exp, got) in enumerate(
                zip(old_lines, actual),
            ):
                if exp != got:
                    raise ValueError(
                        f"context mismatch at line "
                        f"{anchor_idx + i + 1}: expected "
                        f"{exp!r}, found {got!r}"
                    )
            raise ValueError(
                "context mismatch: hunk body has more lines "
                "than anchor region in original"
            )
        out.extend(new_lines)
        cursor = anchor_idx + len(old_lines)
    out.extend(original_lines[cursor:])
    result = "\n".join(out)
    if original.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def _resolve_filesystem_root(
    env_var: str, fallback: str = "./documents",
) -> Path:
    """Resolve one of the filesystem MCP roots.

    v0.9.3+: same resolution shape as ``_resolve_import_root``
    but parameterised so the read and write roots can be set
    independently. Falls back to ``WATCH_DIR`` so the feature
    is non-breaking when the operator hasn't configured it.

    Resolution order:

    1. ``env_var`` (``QDRANT_FILESYSTEM_READ_ROOT`` or
       ``QDRANT_FILESYSTEM_WRITE_ROOT``).
    2. ``WATCH_DIR`` env var (the canonical ingest watch dir).
    3. ``fallback`` (default ``./documents``).
    """
    raw = os.environ.get(env_var)
    if not raw:
        raw = os.environ.get("WATCH_DIR") or fallback
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # Anchor relative paths at the workspace root so the
        # sandbox root is stable across CWD changes (CLI /
        # stdio MCP server / GUI may launch from different
        # cwd's).
        p = (Path(__file__).resolve().parents[2] / raw).resolve()
    else:
        p = p.resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# MIME types ``describe_image`` accepts (v0.9.1+). Mirrors
# ``ImageDescriber._guess_mime`` so the sandbox writeup can pick
# a sensible ``.png`` / ``.jpg`` suffix for the tmp file the
# vision API actually reads. SVG is listed because OpenAI's
# vision stack accepts it; BMP kept for parity with the
# image_describer's table.
_DESCRIBE_IMAGE_MIMES: frozenset[str] = frozenset({
    "image/png", "image/jpeg", "image/gif",
    "image/webp", "image/bmp", "image/svg+xml",
})

# Magic-byte signatures for ``describe_image(mode="base64")``
# validation. We sniff the first few bytes after decoding so a
# caller can't smuggle ``data:image/png;base64,<html>`` past us.
# Mapping is mime -> first 8 bytes. SVG deliberately omitted
# (it's XML text, no magic — we accept it on mime declaration
# alone and reject malformed XML downstream via the parser).
_IMAGE_MAGIC: dict[str, bytes] = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/gif": b"GIF87a",  # also matches GIF89a via prefix
    "image/webp": b"RIFF",
    "image/bmp": b"BM",
}

# Max image size we'll accept (matches ImageDescriber.MAX_IMAGE_BYTES
# = 4 MB). Larger payloads should the LLM downsample or chunk; we
# refuse early to keep the base64 round-trip in MCP manageable.
_DESCRIBE_IMAGE_MAX_BYTES = 4 * 1024 * 1024

# v0.9.2+: defaults for ``resize_image`` — JPEG / quality 85
# gives the best size/quality tradeoff for LLM vision APIs and
# avoids the silent re-compression that would happen if we kept
# PNG (which is lossless and would balloon the file for
# photo-like content).
_RESIZE_DEFAULT_FORMAT = "jpeg"
_RESIZE_DEFAULT_QUALITY = 85

# Pillow / Pillow's ``Image.Resampling`` is the modern enum used
# in Pillow 10+. We import lazily in the handler — this module
# is imported during ``host.list_tools()`` calls and we don't
# want a hard Pillow dependency at import time just for two
# constants.


class LocalTool:
    """Wraps a Python callable as an ``McpTool``-shaped function.

    The wrapped function MUST return a JSON-serialisable object
    (typically ``dict`` or ``str``). The host serialises that to a
    string before shoving it into the OpenAI ``tool`` message.
    """

    def __init__(
        self,
        name: str,
        description: str,
        handler: Callable[..., Any],
        input_schema: dict[str, Any] | None = None,
        server_name: str = "local",
    ):
        self.name = name
        self.description = description
        self._handler = handler
        self.input_schema = input_schema or _schema_from_signature(handler)
        self.server_name = server_name

    def to_mcp_tool(self) -> McpTool:
        return McpTool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            server_name=self.server_name,
        )

    def _call_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Decide whether to feed ``kwargs`` as kwargs or wrap as
        a single positional ``args`` dict.

        All current LocalHost handlers take a single positional
        ``args: dict`` (``_add(self, args)``, ``_delete(self, args)``,
        ``_search(self, query, top_k=5)`` etc. — the ``_search``
        family has its own explicit kwargs, so they pass through
        unchanged). When ``self`` is the only non-self parameter
        AND it's annotated as ``dict``, treat the call as
        ``handler(self, args=<the full dict>)``.

        This makes the tool callable from LLM providers that
        wrap their arguments in a single object (``{"args": {...}}``)
        — which is exactly the wrapper-key shape ``_coerce_arguments``
        unwraps to.
        """
        try:
            sig = inspect.signature(self._handler)
        except (TypeError, ValueError):
            return kwargs
        params = [
            p for p in sig.parameters.values()
            if p.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.POSITIONAL_ONLY,
            )
        ]
        # Skip ``self`` for bound methods.
        if params and params[0].name == "self":
            params = params[1:]
        if len(params) == 1 and params[0].name in ("args", "arguments"):
            return {"args": kwargs}
        return kwargs

    def __call__(self, arguments: dict[str, Any]) -> McpCallResult:
        try:
            # v0.8.7: normalise the shape LLM tool-calls sometimes
            # arrive in. Real-world failures we want to absorb:
            #   * bare JSON string (some providers double-encode)
            #   * {"args": "<json>"} / {"kwargs": {...}} wrapper dict
            #   * [single_dict] one-element list wrapper
            #   * None (callers that pass no arguments)
            # Without this every handler's first ``args["foo"]``
            # access would raise a confusing ``AttributeError`` or
            # ``TypeError`` instead of returning a clean error to
            # the LLM loop so it can retry with the right shape.
            arguments = _coerce_arguments(arguments)
            # Coerce numeric strings into int / float so an LLM
            # that emits "5" for a typed-as-integer parameter does
            # not crash the handler. The schema said ``integer``,
            # but JSON itself does not distinguish string and
            # number - depending on model output we may get either.
            kwargs: dict[str, Any] = {}
            for k, v in arguments.items():
                if isinstance(v, str):
                    # Only try numeric coercion for short strings
                    # that LOOK like numbers - leave prose alone.
                    if v.lstrip("-").isdigit():
                        v = int(v)
                kwargs[k] = v
            # If the handler signature is ``(args: dict)`` (single
            # positional) rather than ``(foo, bar, ...)`` (kwargs-
            # spread), feed the whole normalised dict as the single
            # positional argument. This is how every host in this
            # module is currently written — keeping the convention
            # uniform avoids the "got an unexpected keyword
            # argument 'prompt'" failure mode.
            result = self._handler(**self._call_args(kwargs))
            if inspect.iscoroutine(result):
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        raise RuntimeError(
                            "async local tool cannot be called from a "
                            "running loop; use sync handlers"
                        )
                    result = loop.run_until_complete(result)
                except RuntimeError as e:
                    if "running loop" in str(e):
                        raise
                    result = asyncio.run(_await_once(result))
            text = _stringify(result)
            return McpCallResult(text=text, is_error=False)
        except Exception as e:
            logger.warning(
                "[local:%s] call failed: %r", self.name, e,
            )
            return McpCallResult(
                text=f"{type(e).__name__}: {e}",
                is_error=True,
            )


async def _await_once(coro):
    return await coro


def _resolve_import_root() -> Path:
    """Return the sandbox root for ``add_document`` / ``update_document``.

    Resolution order:

    1. ``QDRANT_IMPORT_ALLOWED_ROOT`` env var (absolute or relative).
    2. ``WATCH_DIR`` env var (the canonical ingest watch dir).
    3. ``./documents`` (matches the watcher fallback).

    The returned path is resolved but not required to exist — the
    caller may create it on first use (the ingest pipeline already
    does this for ``WATCH_DIR``).
    """
    raw = (
        os.environ.get("QDRANT_IMPORT_ALLOWED_ROOT")
        or os.environ.get("WATCH_DIR")
        or "./documents"
    )
    return Path(raw).expanduser().resolve()


def _allowed_file_roots() -> list[Path]:
    """v0.9.16.1 — sandbox roots a file-reading tool may touch:
    the import sandbox plus the filesystem READ/WRITE sandboxes.

    Web-mode uploads land in the filesystem WRITE sandbox under
    ``uploads/`` (v0.9.16.0). Without this, chat-driven OCR /
    resize / add_document on an uploaded file bounced with
    "outside the import sandbox root" whenever
    ``QDRANT_IMPORT_ALLOWED_ROOT`` was scoped narrower than the
    write sandbox. Returns a deduped, resolved list; never raises.
    """
    roots = [_resolve_import_root()]
    try:
        roots.append(
            _resolve_filesystem_root("QDRANT_FILESYSTEM_WRITE_ROOT"))
        roots.append(
            _resolve_filesystem_root("QDRANT_FILESYSTEM_READ_ROOT"))
    except Exception:
        pass
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = r.as_posix()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _is_within(child: Path, parent: Path) -> bool:
    """True iff ``child`` is inside ``parent`` (after resolution).

    Uses ``Path.is_relative_to`` when available (Py 3.9+) and
    falls back to a string-prefix check on the resolved posix
    path so symlink traversal can't bypass the guard. Both
    sides are resolved so ``..`` segments collapse before the
    comparison.
    """
    try:
        c = child.resolve()
        p = parent.resolve()
    except OSError:
        return False
    if hasattr(c, "is_relative_to"):
        try:
            return c.is_relative_to(p)
        except ValueError:
            return False
    try:
        cp = c.as_posix() + "/"
        pp = p.as_posix() + "/"
        return cp.startswith(pp)
    except Exception:
        return False


def _write_content_to_sandbox(
    allowed_root: Path,
    filename: str,
    content: str,
    overwrite: bool,
) -> Path:
    """Write ``content`` to ``allowed_root/<filename>`` atomically.

    Validation:

    * ``filename`` must be a non-empty basename — no path
      separators, no ``..`` components, no absolute paths.
    * Extension must be in ``_ADD_DOC_ALLOWED_EXTS`` (mirrors the
      pipeline's supported set so the downstream loader won't
      silently drop the file).
    * If the file exists and ``overwrite`` is False, raises
      ``FileExistsError`` so the handler can format a clean
      message for the LLM.

    Atomic write: writes to a sibling temp file then ``os.replace``
    so an interrupted write never leaves a half-written file the
    pipeline would happily ingest.

    Returns the resolved Path the caller should pass to the
    ingest pipeline.
    """
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("'filename' must be a non-empty string.")
    name = filename.strip()
    if (
        "/" in name
        or "\\" in name
        or name in (".", "..")
        or os.path.basename(name) != name
    ):
        raise ValueError(
            "'filename' must be a leaf name (no path separators, "
            "no '..'). The sandbox root controls the directory."
        )
    ext = os.path.splitext(name)[1].lower()
    if ext not in _ADD_DOC_ALLOWED_EXTS:
        raise ValueError(
            f"unsupported extension {ext!r}; allowed: "
            f"{sorted(_ADD_DOC_ALLOWED_EXTS)}"
        )

    allowed_root.mkdir(parents=True, exist_ok=True)
    target = (allowed_root / name).resolve()
    if not _is_within(target, allowed_root):
        # Belt-and-suspenders: should be impossible given the
        # basename-only check above, but defence in depth.
        raise ValueError(
            f"resolved path {target.as_posix()} escapes the "
            f"sandbox root {allowed_root.as_posix()!r}."
        )
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"file {target.name!r} already exists in sandbox root; "
            "pass overwrite=true to replace."
        )

    # Atomic write: temp file in the same directory so
    # ``os.replace`` is a same-filesystem rename (not a copy).
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{name}.", suffix=".tmp", dir=str(allowed_root),
    )
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
            os.replace(tmp_path, target)
        except Exception:
            # Best-effort cleanup: close fd first (Windows
            # refuses to unlink a file with an open handle),
            # then remove the half-written temp. ``os.replace``
            # failure leaves the temp in place too — same fix.
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        raise
    return target


# ── v0.9.3+: filesystem MCP helpers ────────────────────────


def _fs_resolve_path(
    raw: Any,
    root: Path,
    *,
    allow_relative: bool = False,
    must_exist: bool = False,
    must_be_file: bool = False,
    must_be_dir: bool = False,
) -> Path:
    """Resolve ``raw`` (a string or Path) against ``root``,
    enforce sandbox containment, and validate the entry shape.

    ``allow_relative=False`` (the default) is the write-side
    policy: the LLM must supply an absolute path or a path that
    resolves to inside the sandbox root. ``allow_relative=True``
    is the read-side policy: bare relative paths are joined to
    ``root`` so the LLM can pass ``"foo/bar.md"`` and get
    ``<root>/foo/bar.md``.

    Returns the resolved Path on success. Raises ``ValueError``
    with a one-line, sandbox-aware message on failure — the
    handler turns that into the MCP ``{"error": ...}`` envelope.
    """
    if raw is None:
        raise ValueError("path is required.")
    if not isinstance(raw, (str, Path)):
        raise ValueError(
            f"path must be a string or Path, got {type(raw).__name__}."
        )
    p = Path(raw).expanduser()
    if not p.is_absolute():
        if not allow_relative:
            raise ValueError(
                f"path {p.as_posix()!r} is not absolute; supply an "
                f"absolute path under {root.as_posix()!r}."
            )
        p = (root / p).resolve()
    else:
        p = p.resolve()
    if not _is_within(p, root):
        raise ValueError(
            f"path {p.as_posix()!r} is outside the filesystem "
            f"sandbox root {root.as_posix()!r}."
        )
    if must_exist and not p.exists():
        raise FileNotFoundError(f"path not found: {p.as_posix()}")
    if must_be_file:
        # v0.9.63.1: a MISSING path must say so — the old message
        # ("is not a regular file") conflated "does not exist" with
        # "exists but special", which sent the LLM down workaround
        # paths instead of correcting the path (live log 2026-09-16
        # 10:08: a guessed vault path was reported as "not a regular
        # file" although nothing was there).
        if not p.exists():
            raise ValueError(f"path not found: {p.as_posix()}")
        if not p.is_file():
            raise ValueError(
                f"path {p.as_posix()!r} is not a regular file."
            )
    if must_be_dir and (not p.exists() or not p.is_dir()):
        raise ValueError(
            f"path {p.as_posix()!r} is not a directory."
        )
    return p


def _fs_safe_basename(name: str, label: str = "filename") -> str:
    """Strip path separators / dot-dot out of an LLM-supplied
    leaf name. Returns the leaf basename. Raises ``ValueError``
    if nothing valid remains."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"'{label}' must be a non-empty string.")
    cleaned = os.path.basename(name.strip())
    if (
        not cleaned
        or cleaned in (".", "..")
        or os.path.basename(cleaned) != cleaned
    ):
        raise ValueError(
            f"'{label}' must be a leaf basename, no path "
            f"separators, no '..'."
        )
    return cleaned


def _fs_format_entry(
    p: Path, *, base: Path,
) -> dict:
    """Format one directory entry for ``list_dir`` / ``get_tree``.

    Returns ``{name, path, relative_path, type, size_bytes,
    mtime}`` where ``type`` is ``"file"`` / ``"directory"`` /
    ``"other"``. ``path`` is POSIX; ``size_bytes`` is the stat
    size for files, else 0.
    """
    try:
        st = p.stat()
    except OSError:
        return {
            "name": p.name,
            "path": p.as_posix(),
            "relative_path": p.name,
            "type": "other",
            "size_bytes": 0,
            "mtime": None,
        }
    if p.is_dir():
        kind = "directory"
    elif p.is_file():
        kind = "file"
    else:
        kind = "other"
    try:
        rel = p.relative_to(base).as_posix()
    except ValueError:
        rel = p.name
    return {
        "name": p.name,
        "path": p.as_posix(),
        "relative_path": rel,
        "type": kind,
        "size_bytes": st.st_size if kind == "file" else 0,
        "mtime": st.st_mtime,
    }


# v0.9.107.0 — directory names the filesystem walker NEVER descends
# into (cache storms / dependency trees / VCS internals). The dir
# entry itself still appears in listings; only the DESCENT is
# skipped. (The 2026-10-04 00:06 search_files hang walked straight
# into .cache/huggingface under the user-profile read root.)
_FS_SKIP_DIRS = frozenset({
    ".cache", ".git", ".hg", ".svn", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".venv", "venv", "env", "node_modules",
    "__pycache__", "site-packages", "appdata",
    "$recycle.bin", "system volume information",
})


def _fs_walk(
    base: Path,
    *,
    max_entries: int,
    max_depth: int,
    include_files: bool = True,
    include_dirs: bool = True,
) -> tuple[list[Path], bool]:
    """Walk ``base`` depth-first and collect entries.

    Returns ``(paths, truncated)``. ``truncated=True`` means the
    walker hit ``max_entries`` and stopped — the LLM should
    either refine its pattern or raise the limit.

    Symlinks are NOT followed: a malicious LLM could drop a
    symlink at ``./documents/escape -> /etc`` and walk into it.
    We check each child for the symlink escape before
    descending.
    """
    collected: list[Path] = []
    truncated = False

    def _walk_one(cur: Path, depth: int) -> bool:
        """Return True if the walk hit the cap."""
        nonlocal truncated
        try:
            entries = list(os.scandir(cur))
        except OSError:
            return False
        # Stable order: directories first, then alpha sort.
        entries.sort(
            key=lambda e: (
                not e.is_dir(follow_symlinks=False),
                e.name.lower(),
            )
        )
        for entry in entries:
            if entry.name in (".", ".."):
                continue
            child = cur / entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir and entry.name.lower() in _FS_SKIP_DIRS:
                # v0.9.107.0 — list the dir but never descend
                # (see _FS_SKIP_DIRS).
                if include_dirs:
                    collected.append(child)
                    if len(collected) >= max_entries:
                        truncated = True
                        return True
                continue
            # Refuse to descend through a symlink that escapes
            # the sandbox root.
            if child.is_symlink():
                try:
                    target = child.resolve()
                except OSError:
                    continue
                if base not in target.parents and target != base:
                    continue
            if is_dir:
                if include_dirs:
                    collected.append(child)
                    if len(collected) >= max_entries:
                        truncated = True
                        return True
                if depth < max_depth:
                    if _walk_one(child, depth + 1):
                        return True
            elif is_file:
                if include_files:
                    collected.append(child)
                    if len(collected) >= max_entries:
                        truncated = True
                        return True
        return False

    # Always include ``base`` itself if it's a directory and we
    # asked for dirs. Skip the descent entirely when
    # ``max_depth == 0`` — only the base is in scope.
    if base.is_dir() and include_dirs:
        collected.append(base)
        if len(collected) >= max_entries:
            return collected, True
    if max_depth > 0:
        _walk_one(base, 1)
    return collected, truncated


def _ext_for_mime(mime: str) -> str:
    """Return a canonical ``.png``/``.jpg``/... suffix for ``mime``.

    Mirrors ``ImageDescriber._guess_mime`` mapping (reverse
    direction). SVG / unknown mimes fall through to ``.bin`` so
    the tmp file always has *some* suffix — the vision API
    itself keys off MIME, not extension, so this is purely for
    the file system.
    """
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/svg+xml": ".svg",
    }.get(mime, ".bin")


def _parse_data_url(data_b64: str) -> tuple[str, bytes]:
    """Parse an inline data URL or bare base64 string.

    Accepts:

    * ``data:image/png;base64,<bytes>``
    * ``data:image/jpeg;base64,<bytes>``
    * ``<bare base64>`` (mime defaults to ``image/png`` — the
      common case for image clipboards)

    Returns ``(mime, decoded_bytes)``. Raises ``ValueError`` for
    unparseable input; the caller formats the message for the
    LLM.

    Magic-byte sniffing (``_IMAGE_MAGIC``) verifies the declared
    mime matches the actual bytes — a caller can't sneak
    ``data:image/png;base64,<html>`` through us.
    """
    if not isinstance(data_b64, str) or not data_b64.strip():
        raise ValueError("'data_base64' must be a non-empty string.")
    s = data_b64.strip()
    mime = "image/png"  # default for bare base64

    if s.lower().startswith("data:"):
        # ``data:<mime>;base64,<payload>``
        try:
            header, _, payload = s.partition(",")
            if ";base64" not in header.lower():
                raise ValueError(
                    "only base64 data URLs are supported; got "
                    f"header {header!r}."
                )
            # ``data:image/png;base64`` -> ``image/png``
            mime_token = header.split(":", 1)[1].split(";", 1)[0].strip().lower()
            if mime_token:
                mime = mime_token
        except (ValueError, IndexError):
            raise
        s = payload

    try:
        # ``validate=False`` accepts padding-less input — the
        # LLM (or a clipboard drop) may chop trailing ``=``;
        # base64 is robust to that. ``validate=True`` would
        # reject and surface a confusing error to the LLM.
        # The magic-byte sniff below catches the real "this
        # is not an image" case.
        raw = base64.b64decode(s, validate=False)
    except Exception as e:
        raise ValueError(f"base64 decode failed: {e}") from e

    if mime not in _DESCRIBE_IMAGE_MIMES:
        raise ValueError(
            f"unsupported mime {mime!r}; allowed: "
            f"{sorted(_DESCRIBE_IMAGE_MIMES)}"
        )
    if len(raw) == 0:
        raise ValueError("decoded image is empty (0 bytes).")
    if len(raw) > _DESCRIBE_IMAGE_MAX_BYTES:
        raise ValueError(
            f"image too large: {len(raw)} bytes > "
            f"{_DESCRIBE_IMAGE_MAX_BYTES} byte cap."
        )
    # Magic-byte sniff for raster formats. SVG is XML text so
    # the magic check is skipped (the parser handles malformed
    # SVG downstream).
    magic = _IMAGE_MAGIC.get(mime)
    if magic is not None and not raw.startswith(magic):
        raise ValueError(
            f"declared mime {mime!r} doesn't match magic bytes "
            f"(got {raw[:8]!r})."
        )
    return mime, raw


def _write_base64_to_sandbox_tmp(
    allowed_root: Path,
    data_bytes: bytes,
    mime: str,
) -> Path:
    """Write ``data_bytes`` to a unique tmp file inside the sandbox.

    Used by ``describe_image(mode="base64")`` — the LLM hands
    us inline bytes, we drop them on disk inside the import
    sandbox so the vision API has something to read, and the
    caller cleans up after the description returns.

    Atomic write: ``mkstemp`` in the sandbox dir + ``os.replace``
    onto a stable name. Windows fd close + unlink on the
    failure path (same pattern as
    ``_write_content_to_sandbox``).

    Returns the resolved Path to the tmp file.
    """
    allowed_root.mkdir(parents=True, exist_ok=True)
    suffix = _ext_for_mime(mime)
    # Stable per-call name: ``.describe_<sha1_prefix>_<suffix>``
    # keeps two simultaneous base64 descriptions from colliding
    # and makes leftover cleanup a simple glob if the process
    # dies mid-flight.
    sha_prefix = hashlib.sha1(data_bytes).hexdigest()[:12]
    target_name = f".describe_{sha_prefix}{suffix}"
    target = (allowed_root / target_name).resolve()
    if not _is_within(target, allowed_root):
        raise ValueError(
            f"resolved path {target.as_posix()} escapes sandbox "
            f"root {allowed_root.as_posix()!r}."
        )

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".describe_{sha_prefix}.",
        suffix=f"{suffix}.tmp",
        dir=str(allowed_root),
    )
    try:
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data_bytes)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        raise
    return target


def _stringify(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (int, float, bool)):
        return str(result)
    if isinstance(result, list):
        return "\n".join(_stringify(x) for x in result)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)
    return str(result)


# Sentinel wrapper keys some LLM providers emit instead of
# spreading the call arguments at the top level. We unwrap
# them rather than treating them as opaque values.
_ARGUMENT_WRAPPER_KEYS = frozenset(
    {"args", "arguments", "kwargs", "params", "parameters", "input"}
)


def _coerce_arguments(arguments: Any) -> dict[str, Any]:
    """Normalise a tool-call argument payload into a ``dict``.

    Real-world LLM tool calls arrive in shapes other than a flat
    ``{key: value}`` dict:

    * ``None`` / empty → ``{}``
    * bare JSON string → parsed
    * single-element list / tuple → first element
    * ``{"args": <...>}`` / ``{"kwargs": {...}}`` / etc. →
      recursively normalise the inner value (handles both
      ``{"args": "{...}"}`` and ``{"args": {...}}``)
    * a flat dict with no recognisable wrapper key → returned
      as-is.

    Any unrecognised shape falls back to ``{}`` rather than
    raising — the caller can still return a clean error to the
    LLM loop instead of crashing on the first ``args["x"]``
    access. Without this, every handler's first line of business
    was to defend itself against ``AttributeError: 'str' object
    has no attribute 'get'`` when the model wrapped its call.
    """
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        # Single-wrapper unwrap. Only strip if the wrapper is the
        # SOLE key — otherwise it's a real dict whose values might
        # happen to be a dict (e.g. ``{"cron": "..."}``).
        if len(arguments) == 1:
            only_key = next(iter(arguments))
            if only_key in _ARGUMENT_WRAPPER_KEYS:
                return _coerce_arguments(arguments[only_key])
        return arguments
    if isinstance(arguments, (list, tuple)):
        if len(arguments) == 1:
            return _coerce_arguments(arguments[0])
        # Multi-element list: not a valid arguments shape, but be
        # graceful — promote to ``{positional: value}`` so the
        # handler at least sees *something* useful.
        return {str(i): v for i, v in enumerate(arguments)}
    if isinstance(arguments, str):
        s = arguments.strip()
        if not s:
            return {}
        if s.startswith(("{", "[")):
            try:
                parsed = json.loads(s)
                return _coerce_arguments(parsed)
            except Exception:
                # Not actually JSON — leave as a single ``text``
                # key so handlers that want prose can still see it.
                return {"text": s}
        return {"text": s}
    # Last-resort: any other object — wrap its ``__dict__`` if
    # there is one, otherwise empty. This keeps the call shape
    # uniform.
    d = getattr(arguments, "__dict__", None)
    if isinstance(d, dict):
        return _coerce_arguments(d)
    return {}


def _schema_from_signature(fn: Callable) -> dict[str, Any]:
    try:
        sig = inspect.signature(fn)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            properties[pname] = _py_type_to_json(param.annotation)
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        out: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            out["required"] = required
        return out
    except Exception:
        return {"type": "object", "properties": {}}


_TYPE_MAP = {
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    str: {"type": "string"},
    list: {"type": "array"},
    dict: {"type": "object"},
}


def _py_type_to_json(annotation: Any) -> dict[str, Any]:
    """Map a Python type annotation to a JSON Schema fragment.

    v0.8.8+: also accepts string annotations (the common case
    with ``from __future__ import annotations``, where
    ``inspect.signature`` returns ``"dict"`` not ``dict``).
    Without this, every LocalTool whose handler takes a single
    ``args: dict`` arg would advertise ``"args": {"type": "string"}``
    to the LLM and the model would emit wrong-shape calls."""
    if annotation in _TYPE_MAP:
        return _TYPE_MAP[annotation]
    if isinstance(annotation, str):
        # Common forward-ref names that appear in this codebase.
        str_map = {
            "dict": "object", "list": "array", "int": "integer",
            "float": "number", "bool": "boolean", "str": "string",
        }
        inner = str_map.get(annotation.strip())
        if inner is not None:
            return {"type": inner}
    return {"type": "string"}


# ─── Concrete hosts ──────────────────────────────────────────


def _as_str_list(value, name: str) -> list[str] | None:
    """Coerce an LLM-supplied list parameter into ``list[str]``
    (v0.9.110.1 — the remove_documents character-iteration incident:
    the LLM sent ``filenames`` as ONE string or as a JSON-encoded
    array string and the handler iterated its CHARACTERS,
    `missing: ["[", "\"", "2", "0", ...]`).

    Accepts:
    * ``None`` -> ``[]``;
    * list/tuple/set of strings (whitespace/quote-stripped, empties
      dropped);
    * a bare string -> JSON-array first (``'["a.md","b.md"]'``),
      then comma-split (``'a.md, b.md'``), else ONE item.

    Returns ``None`` only for un-coercible shapes (caller surfaces a
    clean usage error).
    """
    import json as _json

    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for v in value:
            if isinstance(v, str):
                s = v.strip().strip('"').strip("'").strip()
                if s:
                    out.append(s)
        return out
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s[0] == "[":
            try:
                parsed = _json.loads(s)
                if isinstance(parsed, list):
                    return _as_str_list(parsed, name)
            except Exception:
                pass
        if "," in s:
            return _as_str_list(s.split(","), name)
        one = s.strip('"').strip("'").strip()
        return [one] if one else []
    return None


class QdrantLocalHost:
    """Local Qdrant tools - uses Retriever / QdrantStore directly.

    Run in-process; no MCP subprocess, no HTTP. We do not spawn
    ``src.mcp_server.server`` as a child stdio server because the
    agent can already call these via direct function call.
    """

    def __init__(self):
        # Lazy: defer import / connect to first tool call.
        from src.vector_store.qdrant_client import QdrantStore
        from src.vector_store.retriever import Retriever
        self._store = QdrantStore()
        self._retriever = Retriever()

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="search_documents",
                description=(
                    "Semantic search over locally ingested documents. "
                    "Returns top-k chunks ranked by cosine similarity. "
                    "ALWAYS call this first before answering any "
                    "question that may be answered from the local "
                    "knowledge base."
                ),
                server_name="qdrant-local",
                handler=self._search,
            ),
            LocalTool(
                name="list_documents",
                description=(
                    "List all files currently indexed in the Qdrant "
                    "collection. Use when the user asks 'what files "
                    "do I have?' or before picking a filename_filter."
                ),
                server_name="qdrant-local",
                handler=self._list_documents,
            ),
            LocalTool(
                name="get_document_stats",
                description=(
                    "Return collection-level Qdrant statistics: "
                    "point count, vector dim, distance metric. Use "
                    "when the user asks 'how many chunks?' or "
                    "'what embedding model?'."
                ),
                server_name="qdrant-local",
                handler=self._stats,
            ),
            LocalTool(
                name="remove_documents",
                description=(
                    "Remove files (by basename) from the Qdrant "
                    "collection. Returns {removed: {name: chunks}, "
                    "missing: [name, ...]}. DESTRUCTIVE - confirm "
                    "with the user before calling. 'filenames' "
                    "accepts a list, a single basename string, or a "
                    "JSON/comma array string (v0.9.110.1)."
                ),
                server_name="qdrant-local",
                handler=self._remove,
            ),
            # ── v0.9.0+: LLM-driven document ingest ────────────────
            LocalTool(
                name="add_document",
                description=(
                    "Ingest a new file into the Qdrant collection. "
                    "Two modes: 'path' (large files; pass "
                    "file_path, content never enters context) and "
                    "'content' (small files; pass filename + "
                    "content). Both modes are sandboxed: paths "
                    "must live under QDRANT_IMPORT_ALLOWED_ROOT "
                    "(default WATCH_DIR). "
                    "v0.9.133.5: OVERSIZED files (PDF etc. over "
                    "QDRANT_ADD_DOCUMENT_INLINE_MAX_MB, default 8 MB) "
                    "are auto-promoted to a BACKGROUND ingest task — "
                    "the tool returns immediately with "
                    "{ok, background: true, task_id, filename, size_mb} "
                    "and the ingestion runs detached (registry row "
                    "source='ingest'; the user is notified on "
                    "completion; a later add_document of the SAME file "
                    "resumes idempotently via content-hash dedup). "
                    "On the background response, TELL the user the "
                    "ingest is running with its task_id — do NOT poll "
                    "in a loop (get_task_result(task_id) once if "
                    "asked). Returns {ok, mode, filename, source, "
                    "chunks} or {error} for inline paths."
                ),
                server_name="qdrant-local",
                handler=self._add_document,
                input_schema={
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["path", "content"],
                            "description": (
                                "'path' reads from disk; "
                                "'content' writes an inline string."
                            ),
                        },
                        "file_path": {
                            "type": "string",
                            "description": (
                                "Required when mode='path'. "
                                "Path inside the import sandbox."
                            ),
                        },
                        "filename": {
                            "type": "string",
                            "description": (
                                "Required when mode='content'. "
                                "Leaf basename only."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "Required when mode='content'. "
                                "Full file body."
                            ),
                        },
                        "overwrite": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When mode='content' and the "
                                "target exists, replace it."
                            ),
                        },
                    },
                    "required": ["mode"],
                },
            ),
            LocalTool(
                name="update_document",
                description=(
                    "Replace an already-indexed document with new "
                    "content. Deletes old chunks first, then "
                    "re-ingests. Filename MUST already appear in "
                    "list_documents. DESTRUCTIVE — confirm with "
                    "the user before calling."
                ),
                server_name="qdrant-local",
                handler=self._update_document,
                input_schema={
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": (
                                "Basename of an already-indexed "
                                "document (see list_documents)."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "Full new content for the file."
                            ),
                        },
                    },
                    "required": ["filename", "content"],
                },
            ),
            # ── v0.9.1+: image description (LLM-driven vision) ──
            LocalTool(
                name="describe_image",
                description=(
                    "Describe an image using the local Ollama "
                    "vision model (OLLAMA_VISION_MODEL, default "
                    "gemma4:31b-cloud); when Ollama Cloud is "
                    "unavailable the SAME call automatically falls "
                    "back along the vision provider chain — by "
                    "default OpenRouter (OPENROUTER_VISION_MODEL, "
                    "default google/gemini-2.5-flash), then "
                    "SiliconFlow, then any generic "
                    "OPENAI_API_KEY endpoint; the whole chain "
                    "order is adjustable via VISION_PROVIDER_CHAIN "
                    "(v0.9.18.0). The result "
                    "carries ``vision_backend`` saying which one "
                    "answered. Two modes:\n"
                    "  * mode='path': pass file_path=<abs or "
                    "rel path inside the import sandbox>; the "
                    "tool reads the image from disk (no context "
                    "pollution for the bytes).\n"
                    "  * mode='base64': pass data_base64=<full "
                    "data URL or bare base64 string>; the tool "
                    "decodes + sniffs the mime + drops the bytes "
                    "in the sandbox tmp for the vision API to "
                    "read.\n"
                    "Returns {ok, mode, description, vision_backend, "
                    "mime, size_bytes, sha1, filename, from_cache, "
                    "note}. v0.9.104.5: the raw bytes are NOT "
                    "returned (raw_image_base64 removed) — the "
                    "text-only chat tool loop never consumed them, "
                    "so the blob only wasted ~58k tokens per call; "
                    "rely on 'description' (and the file path for "
                    "re-reads / read_file). Repeated identical "
                    "images hit a process-wide SHA-1 cache — "
                    "from_cache: true skips the vision API entirely, "
                    "so DON'T re-describe the same file with the "
                    "same prompt. "
                    "Sandboxed under QDRANT_IMPORT_ALLOWED_ROOT; "
                    "mimes accepted: png / jpeg / gif / webp / "
                    "bmp / svg+xml."
                ),
                server_name="qdrant-local",
                handler=self._describe_image,
                input_schema={
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["path", "base64"],
                            "description": (
                                "'path' reads from disk; "
                                "'base64' decodes inline bytes."
                            ),
                        },
                        "file_path": {
                            "type": "string",
                            "description": (
                                "Required when mode='path'. "
                                "Path inside the import sandbox."
                            ),
                        },
                        "data_base64": {
                            "type": "string",
                            "description": (
                                "Required when mode='base64'. "
                                "Either ``data:<mime>;base64,"
                                "<payload>`` or a bare base64 "
                                "string (defaults to image/png)."
                            ),
                        },
                        "filename_hint": {
                            "type": "string",
                            "description": (
                                "Optional basename for the LLM's "
                                "own reference. mode='base64' "
                                "uses it for the tmp filename; "
                                "mode='path' just echoes the "
                                "real path's basename."
                            ),
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Optional custom prompt to send to "
                                "the vision model instead of the "
                                "default bilingual prompt."
                            ),
                        },
                    },
                    "required": ["mode"],
                },
            ),
            # ── v0.9.2+: image resize (downscale for vision cap) ──
            LocalTool(
                name="resize_image",
                description=(
                    "Resize an image inside the import sandbox "
                    "so it fits the local vision model's size cap "
                    "(_DESCRIBE_IMAGE_MAX_BYTES = 4 MB). Use this "
                    "after describe_image returns ``too large`` — "
                    "downscale once, then re-call describe_image "
                    "on the resized path. Pillow-backed; preserves "
                    "EXIF metadata (incl. Orientation tag) when "
                    "the source format carries it. Sandbox: "
                    "file_path and out_filename must live under "
                    "QDRANT_IMPORT_ALLOWED_ROOT. Returns "
                    "{ok, input_path, output_path, "
                    "original_dimensions, output_dimensions, "
                    "original_size_bytes, output_size_bytes, "
                    "format, quality, within_cap, "
                    "raw_image_base64} so multimodal chat models "
                    "can consume the downscaled bytes directly. "
                    "NOTE: the chat tool loop strips base64 blobs "
                    "from tool results (v0.9.16.2) — use "
                    "'output_path' + describe_image instead."
                ),
                server_name="qdrant-local",
                handler=self._resize_image,
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": (
                                "Path inside the import sandbox."
                            ),
                        },
                        "max_width": {
                            "type": "integer",
                            "description": (
                                "Required. Maximum output width "
                                "(pixels). Image is downscaled to "
                                "fit while preserving aspect ratio."
                            ),
                        },
                        "max_height": {
                            "type": "integer",
                            "description": (
                                "Optional. Maximum output height. "
                                "Defaults to max_width (square cap)."
                            ),
                        },
                        "out_filename": {
                            "type": "string",
                            "description": (
                                "Optional basename for the output "
                                "file. Defaults to "
                                "``<stem>_resized.<ext>``. Must "
                                "be a leaf name (no path "
                                "separators); lands inside the "
                                "sandbox root."
                            ),
                        },
                        "format": {
                            "type": "string",
                            "enum": ["jpeg", "png", "webp"],
                            "description": (
                                "Output format. Defaults to "
                                "``jpeg`` (best size/quality for "
                                "vision APIs)."
                            ),
                        },
                        "quality": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "description": (
                                "JPEG/WebP encoder quality "
                                "(ignored).  Defaults to 85."
                            ),
                        },
                    },
                    "required": ["file_path", "max_width"],
                },
            ),
            # ── v0.9.114.0 — cross-modal media search (EmbeddingGemma 2) ──
            LocalTool(
                name="search_media",
                description=(
                    "Cross-modal semantic search over MEDIA (images "
                    "embedded from document media and standalone media "
                    "files) using EmbeddingGemma 2's shared text+image+"
                    "audio 768-dim space: the TEXT query is embedded "
                    "with the ggema text tower and matched against "
                    "media vectors. Honest about the channel state — "
                    "the media semantics also live in the TEXT pool "
                    "via the stored vision descriptions "
                    "(search_documents)."
                ),
                server_name="qdrant-local",
                handler=self._search_media,
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": ("Text query describing the "
                                            "media content to find."),
                        },
                        "top_k": {"type": "integer", "default": 5,
                                  "maximum": 20},
                        "filename_filter": {
                            "type": "string",
                            "description": ("Optional host-document "
                                            "filter (payload "
                                            "filename ==)."),
                        },
                    },
                    "required": ["query"],
                },
            ),
        ]

    def _search(
        self, query: str, top_k: int = 5,
        filename_filter: str | None = None,
    ) -> dict:
        results = self._retriever.search(
            query=query, top_k=top_k,
            filename_filter=filename_filter,
        )
        return {
            "query": query,
            "top_k": top_k,
            "filename_filter": filename_filter,
            "count": len(results),
            "results": [
                {
                    "score": round(r.get("score", 0.0), 4),
                    "filename": r.get("filename", "unknown"),
                    "source": r.get("source", ""),
                    "text": (r.get("text") or "")[:800],
                }
                for r in results
            ],
        }

    # ── v0.9.114.0 — cross-modal media search (EmbeddingGemma 2) ──
    def _search_media(self, query: str, top_k: int = 5,
                      filename_filter: str | None = None) -> dict:
        """The TEXT query is embedded with the ggema text tower and
        matched against media vectors in the side collection; honest
        about the channel state (fail-closed — see media_embed.py)."""
        from src.ingest import media_embed as me
        from src.vector_store.qdrant_client import QdrantStore

        if not me.media_embedding_enabled():
            return {
                "error": ("media side-channel disabled "
                          "(QDRANT_MEDIA_EMBEDDING=off). Media "
                          "semantics are in the TEXT pool via the "
                          "vision descriptions — use search_documents."),
            }
        ok, note = me.media_path_available()
        if not ok:
            return {
                "error": (f"media embedding channel unavailable: {note} "
                          "— media semantics still live in the TEXT "
                          "pool via the stored vision descriptions "
                          "(search_documents)."),
            }
        try:
            vec = me._embed_texts_server([query])[0]
        except Exception as e:
            return {"error": f"ggema text embed failed: {e!r:.140}"}
        side = QdrantStore(collection=me.media_collection())
        side.collection = me.media_collection()
        try:
            hits = side.search(query_vector=me._normalize(vec, len(vec)),
                               top_k=max(1, min(int(top_k), 20)),
                               filename_filter=filename_filter or None)
        except Exception as e:
            return {"error": f"media search failed: {e!r:.140}"}
        return {
            "query": query,
            "collection": me.media_collection(),
            "backend": "embeddinggemma2-text-tower (cross-modal)",
            "count": len(hits),
            "results": [
                {
                    "score": round(h.get("score", 0.0), 4),
                    "filename": h.get("filename", ""),
                    "media_name": ((h.get("payload") or {})
                                   .get("media_name", "")),
                    "media_kind": ((h.get("payload") or {})
                                   .get("media_kind", "")),
                    "text": (h.get("text") or "")[:600],
                }
                for h in hits
            ],
        }

    def _list_documents(self) -> dict:
        docs = self._store.list_documents()
        return {"count": len(docs), "documents": docs}

    def _stats(self) -> dict:
        return self._store.get_stats()

    def _remove(self, filenames: list[str]) -> dict:
        names = _as_str_list(filenames, "filenames")
        if names is None:
            return {
                "error": (
                    "'filenames' must be a list of basenames — a "
                    "single basename string or a JSON array string "
                    "is also accepted; e.g. ['a.md','b.md'] or "
                    "'[\"a.md\", \"b.md\"]'."
                ),
            }
        existing = set(self._store.list_documents())
        removed: dict[str, int] = {}
        missing: list[str] = []
        for fn in names:
            if not isinstance(fn, str) or not fn.strip():
                continue
            if fn not in existing:
                missing.append(fn)
                continue
            n = self._store.delete_by_filename(fn)
            removed[fn] = max(0, n) if n >= 0 else -1
        return {"removed": removed, "missing": missing}

    # ── v0.9.0+ ingest helpers ──────────────────────────────────
    def _ingest_via_pipeline(
        self, p: Path, *, stop_check: Callable[[], bool] | None = None,
    ) -> int:
        """Run ``IngestPipeline.ingest_file``; return chunks added.

        Wraps the pipeline so its ``print(...)`` chatter goes
        through ``logging`` instead of polluting the GUI's
        stdout — the MCP tool return value is meant to be the
        only thing the LLM sees. Returns 0 on graceful skip
        (unsupported ext / oversize / empty) so the caller can
        report it cleanly.
        """
        from src.ingest.pipeline import IngestPipeline
        pipeline = IngestPipeline()
        n = pipeline.ingest_file(str(p))
        logger.info(
            "[qdrant-local] ingest ok path=%s chunks=%d",
            p.as_posix(), n,
        )
        return n

    def _inline_max_mb(self) -> float:
        """v0.9.133.5 — the add_document inline/background threshold in
        MB. Files bigger than this are ingested by a DETACHED thread
        registered in the background-task registry instead of the
        synchronous tool call (the 600 s tool watchdog would otherwise
        abort an 800-page PDF mid-run while the daemon thread kept
        writing silently). ``0`` = never go background (legacy exact
        behaviour); a bad value falls back to the default."""
        raw = os.environ.get("QDRANT_ADD_DOCUMENT_INLINE_MAX_MB", "")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = 8.0
        return max(0.0, v)

    def _maybe_background_ingest(self, p: Path) -> dict | None:
        """v0.9.133.5 — promote an oversized ingest into a DETACHED
        background thread registered in the process-wide task registry.

        Returns ``None`` when the file is within the inline threshold
        (the caller proceeds synchronously); otherwise registers
        ``begin_task(source="ingest", ...)`` and spawns the daemon
        thread, which:
          * runs the SAME ``_ingest_via_pipeline`` (dedup upsert —
            reruns overwrite the same content-hash ids),
          * honors the registry stop flag BETWEEN embed batches
            (``ingest_file(stop_check=...)`` — the ⏳ panel / `stop_task`
            can halt a huge PDF cleanly, keeping the written prefix),
          * stamps ``end_task(done/failed/stopped)`` — the terminal
            notify hook (gui/api.py wires it to Telegram + the task
            panel) tells the user what happened.

        Never raises; a registry/thread failure degrades to ``None``
        (inline ingest) rather than killing the tool call.
        """
        try:
            max_mb = self._inline_max_mb()
            if max_mb <= 0:
                return None
            size_mb = p.stat().st_size / (1024.0 * 1024.0)
            if size_mb <= max_mb:
                return None
            from src.agent import task_runner

            title = (f"匯入 {p.name}（{size_mb:.1f} MB 背景任務）")
            tid = task_runner.begin_task("ingest", title)
            task_runner.attach_sid(tid, f"task-{tid}")
        except Exception as e:  # noqa: BLE001 — degrade to inline
            logger.warning(
                "[qdrant-local] background-ingest promote failed, "
                "falling back to inline: %r", e,
            )
            return None

        def _work() -> None:
            try:
                n = self._ingest_via_pipeline(
                    p,
                    stop_check=lambda: task_runner.stop_requested(tid),
                )
                was_stopped = task_runner.stop_requested(tid)
                task_runner.end_task(
                    tid,
                    task_runner.STOPPED if was_stopped else
                    task_runner.DONE,
                    answer_chars=int(n),
                )
            except FileNotFoundError as e:
                task_runner.end_task(tid, task_runner.FAILED,
                                     error=f"file disappeared: {e}")
            except Exception as e:  # noqa: BLE001 — bookkeeping honesty
                logger.warning(
                    "[qdrant-local] background ingest failed for %s: %r",
                    p.name, e,
                )
                task_runner.end_task(
                    tid, task_runner.FAILED,
                    error=f"{type(e).__name__}: {e}"[:300],
                )

        th = threading.Thread(
            target=_work, daemon=True,
            name=f"ingest-bg-{tid}",
        )
        th.start()
        logger.info(
            "[qdrant-local] oversized ingest promoted to background: "
            "%s (%.1f MB) task=%s",
            p.name, size_mb, tid,
        )
        return {
            "ok": True,
            "background": True,
            "task_id": tid,
            "filename": p.name,
            "source": p.as_posix(),
            "size_mb": round(size_mb, 1),
            "message": (
                f"「{p.name}」({size_mb:.1f} MB) 太大，已自動轉為背景匯入"
                f"任務 task_id={tid}。完成（或失敗）會自動通知使用者；"
                "重複 add_document 同一檔案會以 content-hash 冪等續傳。"
            ),
        }

    def _add_document(self, args: dict) -> dict:
        """v0.9.0+: handle ``add_document`` tool call.

        ``args`` shape (after LocalTool._call_args normalisation):

        * ``mode``              — ``"path"`` or ``"content"``
        * ``file_path``         — only for ``mode="path"``
        * ``filename`` + ``content`` — only for ``mode="content"``
        * ``overwrite``         — only for ``mode="content"``

        Sandbox: the resolved path MUST be inside the import
        root (``QDRANT_IMPORT_ALLOWED_ROOT`` → ``WATCH_DIR`` →
        ``./documents``). Out-of-root paths return ``{"error":
        ...}`` and never touch disk.
        """
        if not isinstance(args, dict):
            return {"error": "'add_document' expects a dict payload."}
        mode = args.get("mode")
        if mode not in ("path", "content"):
            return {
                "error": (
                    "'mode' must be 'path' or 'content'. Use "
                    "'path' for large files (no context "
                    "pollution); 'content' for small files."
                ),
            }

        # v0.9.16.1: import sandbox + filesystem read/write sandboxes
        # (web-mode uploads live under the write sandbox's uploads/).
        allowed_roots = _allowed_file_roots()
        allowed_root = allowed_roots[0]
        try:
            if mode == "path":
                file_path_raw = args.get("file_path")
                if not isinstance(file_path_raw, str) or not file_path_raw.strip():
                    return {
                        "error": "mode='path' requires 'file_path'.",
                    }
                src = Path(file_path_raw).expanduser().resolve()
                if not any(_is_within(src, r) for r in allowed_roots):
                    return {
                        "error": (
                            f"file_path {src.as_posix()!r} is "
                            f"outside the allowed sandbox roots "
                            f"{[r.as_posix() for r in allowed_roots]!r}. "
                            f"Set QDRANT_IMPORT_ALLOWED_ROOT / "
                            f"QDRANT_FILESYSTEM_WRITE_ROOT to a "
                            f"wider directory if this is "
                            f"intentional."
                        ),
                    }
                if not src.exists():
                    return {
                        "error": f"file not found: {src.as_posix()}",
                    }
                if not src.is_file():
                    return {
                        "error": (
                            f"not a regular file: {src.as_posix()}"
                        ),
                    }
                target = src
            else:
                # mode == 'content'
                filename = args.get("filename")
                content = args.get("content")
                overwrite = bool(args.get("overwrite", False))
                if not isinstance(filename, str) or not filename.strip():
                    return {
                        "error": "mode='content' requires 'filename'.",
                    }
                if not isinstance(content, str):
                    return {
                        "error": (
                            "mode='content' requires 'content' "
                            "(string)."
                        ),
                    }
                try:
                    target = _write_content_to_sandbox(
                        allowed_root=allowed_root,
                        filename=filename,
                        content=content,
                        overwrite=overwrite,
                    )
                except FileExistsError as e:
                    return {"error": str(e)}
                except ValueError as e:
                    return {"error": str(e)}
        except Exception as e:
            logger.warning(
                "[qdrant-local] add_document preflight failed: %r",
                e,
            )
            return {"error": f"{type(e).__name__}: {e}"}

        # v0.9.133.5 — oversized files: promote to a background
        # ingest task (the 600 s tool watchdog would otherwise abort
        # an 800-page PDF while the work kept running silently).
        bg = self._maybe_background_ingest(target)
        if bg is not None:
            return bg

        # Preflight passed — run the pipeline.
        try:
            chunks = self._ingest_via_pipeline(target)
        except FileNotFoundError as e:
            return {"error": f"file disappeared mid-ingest: {e}"}
        except Exception as e:
            logger.warning(
                "[qdrant-local] add_document ingest failed: %r", e,
            )
            return {"error": f"ingest failed: {type(e).__name__}: {e}"}

        return {
            "ok": True,
            "mode": mode,
            "filename": target.name,
            "source": target.as_posix(),
            "chunks": chunks,
            "sandbox_root": allowed_root.as_posix(),
        }

    def _update_document(self, args: dict) -> dict:
        """v0.9.0+: handle ``update_document`` tool call.

        Behaviour:

        1. ``filename`` must already be in the Qdrant collection
           (checked via ``list_documents``) — a typo doesn't
           accidentally wipe an unrelated file.
        2. Delete the old chunks (``delete_by_filename``).
        3. Write ``content`` to ``sandbox_root/<filename>`` with
           ``overwrite=True``.
        4. Re-ingest through the normal pipeline.

        Returns ``{ok, filename, source, chunks_deleted,
        chunks_added}``.
        """
        if not isinstance(args, dict):
            return {"error": "'update_document' expects a dict payload."}
        filename = args.get("filename")
        content = args.get("content")
        if not isinstance(filename, str) or not filename.strip():
            return {
                "error": (
                    "'filename' is required (basename of an "
                    "already-indexed document)."
                ),
            }
        if not isinstance(content, str):
            return {"error": "'content' must be a string."}
        name = os.path.basename(filename.strip())
        if name != filename.strip() or "/" in name or "\\" in name:
            return {
                "error": (
                    "'filename' must be a leaf basename, no path "
                    "separators."
                ),
            }

        existing = set(self._store.list_documents())
        if name not in existing:
            return {
                "error": (
                    f"file {name!r} is not in the Qdrant "
                    f"collection. Use add_document for new files, "
                    f"or list_documents to see what exists."
                ),
                "missing": True,
            }

        allowed_root = _resolve_import_root()
        try:
            deleted = self._store.delete_by_filename(name)
            target = _write_content_to_sandbox(
                allowed_root=allowed_root,
                filename=name,
                content=content,
                overwrite=True,
            )
        except Exception as e:
            logger.warning(
                "[qdrant-local] update_document preflight "
                "failed: %r", e,
            )
            return {"error": f"{type(e).__name__}: {e}"}

        try:
            added = self._ingest_via_pipeline(target)
        except Exception as e:
            return {
                "error": (
                    f"delete ok ({deleted} chunks) but re-"
                    f"ingest failed: {type(e).__name__}: {e}. "
                    f"The document is now gone from Qdrant; "
                    f"rerun add_document to recover."
                ),
            }

        return {
            "ok": True,
            "filename": name,
            "source": target.as_posix(),
            "chunks_deleted": max(0, deleted) if deleted >= 0 else -1,
            "chunks_added": added,
            "sandbox_root": allowed_root.as_posix(),
        }

    # ── v0.9.1+ describe_image ──────────────────────────────────
    def _describe_image(self, args: dict) -> dict:
        """v0.9.1+: handle ``describe_image`` tool call.

        ``args`` shape:

        * ``mode``         — ``"path"`` or ``"base64"``
        * ``file_path``    — only for ``mode="path"`` (sandboxed)
        * ``data_base64``  — only for ``mode="base64"``
        * ``filename_hint`` — optional basename for LLM ref
        * ``prompt``       — optional vision prompt override

        Returns ``{ok, mode, description, vision_backend, mime,
        size_bytes, sha1, filename, from_cache, note}`` or
        ``{error}``. v0.9.104.5 — ``raw_image_base64`` is NO LONGER
        returned: the text-only chat tool loop never consumed it and
        it was re-stripped from every follow-up prompt at ~58k tokens
        of waste per describe (the description is the payload; the
        bytes stay on disk in the sandbox).
        """
        if not isinstance(args, dict):
            return {"error": "'describe_image' expects a dict payload."}
        mode = args.get("mode")
        if mode not in ("path", "base64"):
            # v0.9.16.1 — user log: the LLM called
            # describe_image(file_path, prompt) WITHOUT the enum'd
            # 'mode' param and got bounced. Infer the mode from what
            # actually came in instead of rejecting the call.
            fp = args.get("file_path")
            db = args.get("data_base64")
            if isinstance(fp, str) and fp.strip():
                mode = "path"
            elif isinstance(db, str) and db.strip():
                mode = "base64"
            else:
                return {
                    "error": (
                        "'mode' must be 'path' or 'base64'. Use "
                        "'path' for sandbox-resident files; "
                        "'base64' for inline GUI drops / clipboard."
                    ),
                }

        # v0.9.16.1 — import sandbox + filesystem read/write sandboxes
        # (web-mode uploads live under the write sandbox's uploads/).
        allowed_roots = _allowed_file_roots()
        allowed_root = allowed_roots[0]
        image_path: Path | None = None
        raw_bytes: bytes | None = None
        mime: str = "image/octet-stream"
        sha1_hex: str | None = None
        filename_for_llm: str | None = None
        # mode="path" never creates a tmp file; mode="base64"
        # owns the staging file we wrote and must clean up.
        own_tmp: Path | None = None

        try:
            if mode == "path":
                file_path_raw = args.get("file_path")
                if not isinstance(file_path_raw, str) or not file_path_raw.strip():
                    return {
                        "error": "mode='path' requires 'file_path'.",
                    }
                src = Path(file_path_raw).expanduser().resolve()
                if not any(_is_within(src, r) for r in allowed_roots):
                    return {
                        "error": (
                            f"file_path {src.as_posix()!r} is "
                            f"outside the allowed sandbox roots "
                            f"{[r.as_posix() for r in allowed_roots]!r}. "
                            f"Set QDRANT_IMPORT_ALLOWED_ROOT / "
                            f"QDRANT_FILESYSTEM_WRITE_ROOT to a "
                            f"wider directory if this is "
                            f"intentional."
                        ),
                    }
                if not src.exists():
                    return {
                        "error": f"file not found: {src.as_posix()}",
                    }
                if not src.is_file():
                    return {
                        "error": (
                            f"not a regular file: {src.as_posix()}"
                        ),
                    }
                try:
                    size = src.stat().st_size
                except OSError as e:
                    return {"error": f"stat failed: {e}"}
                if size > _DESCRIBE_IMAGE_MAX_BYTES:
                    return {
                        "error": (
                            f"image too large: {size} bytes > "
                            f"{_DESCRIBE_IMAGE_MAX_BYTES} byte cap."
                        ),
                    }
                try:
                    raw_bytes = src.read_bytes()
                except OSError as e:
                    return {"error": f"read failed: {e}"}
                ext = src.suffix.lower()
                mime = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                    ".bmp": "image/bmp",
                    ".svg": "image/svg+xml",
                }.get(ext, "")
                if mime not in _DESCRIBE_IMAGE_MIMES:
                    return {
                        "error": (
                            f"unsupported image extension "
                            f"{ext!r}; allowed: png, jpg, jpeg, "
                            f"gif, webp, bmp, svg."
                        ),
                    }
                sha1_hex = hashlib.sha1(raw_bytes).hexdigest()
                filename_for_llm = (
                    args.get("filename_hint")
                    if isinstance(args.get("filename_hint"), str)
                    and args.get("filename_hint").strip()
                    else src.name
                )
                # Original stays on disk inside the sandbox;
                # ImageDescriber reads it directly — no copy.
                image_path = src

            # ── base64 mode branch ────────────────────────
            else:
                data_b64 = args.get("data_base64")
                try:
                    mime, raw_bytes = _parse_data_url(data_b64 or "")
                except ValueError as e:
                    return {"error": str(e)}
                sha1_hex = hashlib.sha1(raw_bytes).hexdigest()
                hint = args.get("filename_hint")
                if isinstance(hint, str) and hint.strip():
                    clean = os.path.basename(hint.strip())
                    if clean and clean not in (".", ".."):
                        filename_for_llm = clean
                if not filename_for_llm:
                    ext = _ext_for_mime(mime)
                    filename_for_llm = f"inline_{sha1_hex[:12]}{ext}"
                try:
                    own_tmp = _write_base64_to_sandbox_tmp(
                        allowed_root=allowed_root,
                        data_bytes=raw_bytes,
                        mime=mime,
                    )
                except (OSError, ValueError) as e:
                    return {
                        "error": (
                            f"failed to stage inline image in "
                            f"sandbox: {e}"
                        ),
                    }
                image_path = own_tmp

            # ── Common: call the vision API ──────────────
            # Late-bound import: importing at module level
            # would force the openai SDK + Ollama env checks
            # on every ``host.list_tools()`` call. Doing it
            # here also lets ``patch("src.ingest.image_describer.
            # ImageDescriber")`` in unit tests work without
            # touching host.py's reference.
            from src.ingest.image_describer import ImageDescriber
            describer = ImageDescriber()
            cache_before = dict(describer._cache)
            custom_prompt = args.get("prompt")
            try:
                if isinstance(custom_prompt, str) and custom_prompt.strip():
                    original_prompt = describer.PROMPT
                    describer.PROMPT = custom_prompt
                    try:
                        description = describer.describe(image_path)
                    finally:
                        describer.PROMPT = original_prompt
                else:
                    description = describer.describe(image_path)
            except Exception as e:
                logger.warning(
                    "[qdrant-local] describe_image vision call "
                    "failed: %r", e,
                )
                return {
                    "error": f"vision call failed: "
                             f"{type(e).__name__}: {e}",
                }
            # Cache-hit detection — v0.9.104.5: prefer the describer's
            # own ``last_cache_hit`` (covers instance AND shared-cache
            # short-circuits; strict ``is True`` so MagicMock fakes in
            # tests — auto-attr MagicMock — can't fake it). The legacy
            # before/after instance-dict diff stays as the fallback.
            from_cache = (
                getattr(describer, "last_cache_hit", None) is True
            ) or (
                sha1_hex in cache_before
                and sha1_hex in describer._cache
                and cache_before.get(sha1_hex) == description
            )

            # v0.9.104.5 — ``raw_image_base64`` is NO LONGER returned.
            # The chat tool loop is TEXT-ONLY: the blob (231,424 chars
            # for a 173 KB screenshot) was always stripped back out by
            # _strip_base64_from_tool_text, so it cost ~58k tokens on
            # EVERY follow-up create for nothing (live log 2026-10-02:
            # 3 describes in one turn → ~175k wasted tokens). The
            # description is the payload; the bytes stay on disk.
            # v0.9.16.4 — which vision provider answered
            # ("ollama-cloud" | "openrouter" | "openai-compat");
            # only plain strings pass (mocked describers in tests /
            # exotic backends must not break JSON serialisation).
            _vb = getattr(describer, "last_backend", None)
            return {
                "ok": True,
                "mode": mode,
                "description": description,
                "vision_backend": _vb if isinstance(_vb, str) else None,
                "mime": mime,
                "size_bytes": len(raw_bytes),
                "sha1": sha1_hex,
                "filename": filename_for_llm,
                "from_cache": from_cache,
                "sandbox_root": allowed_root.as_posix(),
                "note": (
                    "raw_image_base64 omitted (v0.9.104.5) — the "
                    "text-only tool loop never consumes it; use "
                    "read_file on the path if the raw bytes are truly "
                    "needed."
                ),
            }
        finally:
            # Cleanup our tmp file regardless of outcome
            # (mode="base64" only — mode="path" never sets
            # own_tmp).
            if own_tmp is not None:
                try:
                    os.unlink(own_tmp)
                except OSError:
                    pass

    # ── v0.9.2+ resize_image ──────────────────────────────────
    def _resize_image(self, args: dict) -> dict:
        """v0.9.2+: handle ``resize_image`` tool call.

        Args (after LocalTool._call_args normalisation):

        * ``file_path``    — path inside import sandbox (required)
        * ``max_width``    — required
        * ``max_height``   — optional; defaults to ``max_width``
        * ``out_filename`` — optional; default
          ``<stem>_resized.<ext>``
        * ``format``       — ``jpeg`` / ``png`` / ``webp``;
          default ``jpeg``
        * ``quality``      — 1-100; default 85; ignored for PNG

        Pillow reads the source, downsizes preserving aspect
        ratio so the longer side fits within ``max_width`` /
        ``max_height``, and writes the result back into the
        sandbox at ``out_filename``. EXIF metadata (incl.
        Orientation) is preserved when the source has it.

        Returns ``{ok, input_path, output_path,
        original_dimensions, output_dimensions,
        original_size_bytes, output_size_bytes, format,
        quality, within_cap, raw_image_base64}`` or
        ``{error}``. ``within_cap`` tells the LLM whether the
        downsized file already fits the 4 MB vision cap.
        """
        if not isinstance(args, dict):
            return {"error": "'resize_image' expects a dict payload."}

        # ── Validate inputs ──────────────────────────────
        file_path_raw = args.get("file_path")
        if not isinstance(file_path_raw, str) or not file_path_raw.strip():
            return {"error": "'file_path' is required."}
        max_width = args.get("max_width")
        if not isinstance(max_width, int) or max_width <= 0:
            return {"error": "'max_width' must be a positive integer."}
        max_height = args.get("max_height")
        if max_height is None:
            max_height = max_width
        elif not isinstance(max_height, int) or max_height <= 0:
            return {
                "error": "'max_height' must be a positive integer "
                         "when provided.",
            }
        out_filename = args.get("out_filename")
        if out_filename is not None and (
            not isinstance(out_filename, str) or not out_filename.strip()
        ):
            return {"error": "'out_filename' must be a non-empty string."}
        fmt = args.get("format", _RESIZE_DEFAULT_FORMAT)
        if fmt not in ("jpeg", "png", "webp"):
            return {
                "error": f"'format' must be one of jpeg/png/webp "
                         f"(got {fmt!r}).",
            }
        quality = args.get("quality", _RESIZE_DEFAULT_QUALITY)
        if not isinstance(quality, int) or quality < 1 or quality > 100:
            return {
                "error": "'quality' must be an integer between "
                         "1 and 100 (got "
                         f"{quality!r}).",
            }

        # ── Sandbox containment for input ─────────────────
        # v0.9.16.1: import sandbox + filesystem read/write sandboxes
        # (web-mode uploads live under the write sandbox's uploads/).
        allowed_roots = _allowed_file_roots()
        allowed_root = allowed_roots[0]
        src = Path(file_path_raw).expanduser().resolve()
        if not any(_is_within(src, r) for r in allowed_roots):
            return {
                "error": (
                    f"file_path {src.as_posix()!r} is outside the "
                    f"allowed sandbox roots "
                    f"{[r.as_posix() for r in allowed_roots]!r}. Set "
                    f"QDRANT_IMPORT_ALLOWED_ROOT / "
                    f"QDRANT_FILESYSTEM_WRITE_ROOT to a wider "
                    f"directory if this is intentional."
                ),
            }
        if not src.exists():
            return {"error": f"file not found: {src.as_posix()}"}
        if not src.is_file():
            return {
                "error": f"not a regular file: {src.as_posix()}",
            }

        # ── Output filename derivation ───────────────────
        src_stem = src.stem
        if out_filename is None:
            out_filename = f"{src_stem}_resized.{fmt}"
        clean_out = os.path.basename(out_filename.strip())
        if (
            not clean_out
            or clean_out in (".", "..")
            or os.path.basename(clean_out) != clean_out
        ):
            return {
                "error": (
                    "'out_filename' must be a leaf basename, no "
                    "path separators."
                ),
            }
        out_ext = os.path.splitext(clean_out)[1].lstrip(".").lower()
        if out_ext not in ("jpeg", "jpg", "png", "webp"):
            out_ext = fmt
            clean_out = f"{os.path.splitext(clean_out)[0]}.{out_ext}"

        # v0.9.16.1: output lands NEXT TO the source (uploads/ for
        # web-uploaded images) instead of the import root, so the
        # resized file sits beside the original the LLM referenced.
        out_base = src.parent
        target = (out_base / clean_out).resolve()
        if not _is_within(target, out_base):
            return {
                "error": (
                    f"resolved output path {target.as_posix()} "
                    f"escapes sandbox root "
                    f"{out_base.as_posix()!r}."
                ),
            }
        if target.exists() and target.resolve() != src.resolve():
            return {
                "error": (
                    f"output file {clean_out!r} already exists in "
                    f"sandbox; pick a different out_filename."
                ),
            }

        # ── Pillow work ───────────────────────────────────
        try:
            from PIL import Image, ImageOps
        except ImportError as e:
            return {
                "error": (
                    f"Pillow is not installed: {e}. Run "
                    f"'pip install Pillow>=10.0' to enable "
                    f"resize_image."
                ),
            }

        original_bytes_size = src.stat().st_size
        try:
            with Image.open(src) as raw_img:
                img = ImageOps.exif_transpose(raw_img)
                src_exif = raw_img.info.get("exif", None)
                orig_w, orig_h = img.size
                # Per-axis cap: only scale axes that exceed the
                # user's limits; the other axis is allowed to
                # track the aspect ratio. Using ``max`` here
                # would treat a 9999x500 cap on a 4000x1000
                # image as no-op (long-side 4000 < 9999), which
                # is the opposite of what the user asked for.
                need_w = orig_w > max_width
                need_h = orig_h > max_height
                if not (need_w or need_h):
                    new_w, new_h = orig_w, orig_h
                    resized = img
                else:
                    # Aspect-preserving scale so neither axis
                    # exceeds its cap.
                    scale_w = max_width / orig_w
                    scale_h = max_height / orig_h
                    scale = min(scale_w, scale_h)
                    new_w = max(1, int(round(orig_w * scale)))
                    new_h = max(1, int(round(orig_h * scale)))
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:  # pragma: no cover
                        resample = Image.LANCZOS
                    resized = img.resize(
                        (new_w, new_h), resample=resample,
                    )
                # Atomic write: tmp file inside the sandbox
                # then ``os.replace`` — same pattern as
                # ``_write_content_to_sandbox``.
                fd, tmp_path = tempfile.mkstemp(
                    prefix=f".resize_{src_stem}.",
                    suffix=f".{out_ext}.tmp",
                    dir=str(allowed_root),
                )
                try:
                    save_kwargs: dict[str, Any] = {}
                    pil_fmt = (
                        "JPEG" if out_ext in ("jpg", "jpeg")
                        else out_ext.upper()
                    )
                    if pil_fmt == "JPEG":
                        save_kwargs["quality"] = quality
                        save_kwargs["optimize"] = True
                        # JPEG can't carry alpha — flatten
                        # onto a white background.
                        if resized.mode in ("RGBA", "LA", "P"):
                            background = Image.new(
                                "RGB", resized.size, (255, 255, 255),
                            )
                            try:
                                mask = (
                                    resized.convert("RGBA").split()[-1]
                                )
                            except Exception:
                                mask = None
                            background.paste(
                                resized.convert("RGB"), mask=mask,
                            )
                            resized_to_save = background
                        else:
                            resized_to_save = (
                                resized.convert("RGB")
                                if resized.mode != "RGB"
                                else resized
                            )
                    elif pil_fmt == "PNG":
                        # Preserve alpha for PNG.
                        resized_to_save = resized
                    else:  # WEBP
                        save_kwargs["quality"] = quality
                        resized_to_save = resized
                    # Re-attach the original EXIF segment when
                    # present; PIL strips it on save by default.
                    if src_exif and pil_fmt in ("JPEG", "WEBP"):
                        save_kwargs["exif"] = src_exif

                    with os.fdopen(fd, "wb") as fh:
                        resized_to_save.save(
                            fh, pil_fmt, **save_kwargs,
                        )
                    os.replace(tmp_path, target)
                except Exception:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
        except FileNotFoundError as e:
            return {"error": f"file disappeared mid-resize: {e}"}
        except Exception as e:
            logger.warning(
                "[qdrant-local] resize_image failed: %r", e,
            )
            return {
                "error": f"resize failed: "
                         f"{type(e).__name__}: {e}",
            }

        # ── Build response ───────────────────────────────
        output_bytes_size = target.stat().st_size
        with open(target, "rb") as fh:
            output_bytes = fh.read()
        raw_b64 = base64.b64encode(output_bytes).decode("ascii")
        return {
            "ok": True,
            "mode": "path",
            "input_path": src.as_posix(),
            "output_path": target.as_posix(),
            "original_dimensions": [orig_w, orig_h],
            "output_dimensions": [new_w, new_h],
            "original_size_bytes": original_bytes_size,
            "output_size_bytes": output_bytes_size,
            "format": out_ext,
            "quality": quality,
            "within_cap": (
                output_bytes_size <= _DESCRIBE_IMAGE_MAX_BYTES
            ),
            "sandbox_root": allowed_root.as_posix(),
            "raw_image_base64": raw_b64,
        }


class FilesystemLocalHost:
    """v0.9.3+ filesystem MCP tools (read / write / search),
    expanded in v0.9.4+ with copy / diff / patch.

    Exposes 15 ``LocalTool`` entries under server name
    ``filesystem-local``: 8 read-only (pinned to
    ``QDRANT_FILESYSTEM_READ_ROOT`` — 7 from v0.9.3 plus
    ``diff_files`` from v0.9.4) and 7 write / copy / patch
    (pinned to the narrower ``QDRANT_FILESYSTEM_WRITE_ROOT`` —
    5 from v0.9.3 plus ``copy_file`` / ``patch_file`` from
    v0.9.4). Roots default to ``WATCH_DIR`` so the feature is
    no-op until the operator opts in. Symlinks are refused at
    walk boundaries; write operations reject a closed list of
    dangerous executable extensions regardless of sandbox.
    Every handler returns a dict (never raises).
    """

    SERVER_NAME = "filesystem-local"

    def __init__(self) -> None:
        # Resolve once and cache so we don't hit the disk on
        # every tool call. The resolve also mkdir's the root.
        self._read_root: Path = _resolve_filesystem_root(
            "QDRANT_FILESYSTEM_READ_ROOT",
        )
        self._write_root: Path = _resolve_filesystem_root(
            "QDRANT_FILESYSTEM_WRITE_ROOT",
        )

    @property
    def read_root(self) -> Path:
        return self._read_root

    @property
    def write_root(self) -> Path:
        return self._write_root

    def _err(self, msg: str) -> dict:
        return {"error": msg}

    def list_tools(self) -> list[LocalTool]:
        return [
            # ── READ CLASS ───────────────────────────────
            LocalTool(
                name="list_dir",
                description=(
                    "List entries in a directory inside the "
                    "filesystem read sandbox. Returns "
                    "{entries: [...], truncated: bool}. "
                    "Each entry is {name, path, "
                    "relative_path, type, size_bytes, mtime}."
                ),
                server_name=self.SERVER_NAME,
                handler=self._list_dir,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory inside the sandbox. "
                                "Defaults to the sandbox root."
                            ),
                        },
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Optional fnmatch-style name "
                                "filter (e.g. ``*.md``)."
                            ),
                        },
                        "include_dirs": {
                            "type": "boolean",
                            "default": True,
                        },
                        "include_files": {
                            "type": "boolean",
                            "default": True,
                        },
                    },
                },
            ),
            LocalTool(
                name="stat_file",
                description=(
                    "Stat a single path. Returns "
                    "{exists, type, size_bytes, mtime, mode, "
                    "is_symlink, path, relative_path}."
                ),
                server_name=self.SERVER_NAME,
                handler=self._stat_file,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            ),
            LocalTool(
                name="read_file",
                description=(
                    "Read a text file inside the read sandbox. "
                    "Returns {content, encoding, size_bytes, "
                    "truncated, offset_bytes, next_offset} or "
                    "{error}. Use ``offset_bytes`` / "
                    "``max_bytes`` to page through large "
                    "files. Binary extensions are rejected."
                ),
                server_name=self.SERVER_NAME,
                handler=self._read_file,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset_bytes": {
                            "type": "integer",
                            "minimum": 0,
                            "description": (
                                "Byte offset to start reading "
                                "from. Default 0."
                            ),
                        },
                        "max_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "Max bytes to return. Default "
                                f"{_FS_DEFAULT_READ_MAX_BYTES}; "
                                f"hard cap "
                                f"{_FS_READ_HARD_MAX_BYTES}."
                            ),
                        },
                        "encoding": {
                            "type": "string",
                            "description": (
                                "Text encoding. Default utf-8."
                            ),
                        },
                    },
                    "required": ["path"],
                },
            ),
            LocalTool(
                name="read_multiple",
                description=(
                    "Read several files in one round-trip. "
                    "Returns {files: [{path, content, "
                    "size_bytes, error?}], total_bytes}. Each "
                    "file is capped by ``max_bytes_each`` "
                    "(default 16 KB). Binary extensions and "
                    "out-of-sandbox paths produce per-file "
                    "errors without aborting the batch."
                ),
                server_name=self.SERVER_NAME,
                handler=self._read_multiple,
                input_schema={
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List of paths inside the read "
                                "sandbox."
                            ),
                        },
                        "max_bytes_each": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 16 * 1024,
                            "description": (
                                "Per-file byte cap. Default "
                                "16 KB."
                            ),
                        },
                    },
                    "required": ["paths"],
                },
            ),
            LocalTool(
                name="search_files",
                description=(
                    "Glob-match file names inside the read "
                    "sandbox. ``pattern`` is fnmatch-style "
                    "(e.g. ``*.md``). Returns "
                    "{matches: [{path, relative_path, "
                    "size_bytes}], truncated: bool, "
                    "match_count}."
                ),
                server_name=self.SERVER_NAME,
                handler=self._search_files,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Search root. Defaults to the "
                                "sandbox root."
                            ),
                        },
                        "pattern": {
                            "type": "string",
                            "description": (
                                "fnmatch glob. Required."
                            ),
                        },
                        "recursive": {
                            "type": "boolean",
                            "default": True,
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "default": _FS_DEFAULT_MAX_ENTRIES,
                        },
                    },
                    "required": ["pattern"],
                },
            ),
            LocalTool(
                name="get_tree",
                description=(
                    "Recursive tree dump of a directory. "
                    "Returns {entries: [{name, path, "
                    "relative_path, type, size_bytes}], "
                    "truncated: bool, total_count, max_depth}."
                ),
                server_name=self.SERVER_NAME,
                handler=self._get_tree,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory. Defaults to "
                                "sandbox root."
                            ),
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 8,
                            "description": (
                                "Max recursion depth. Default 8."
                            ),
                        },
                        "max_entries": {
                            "type": "integer",
                            "minimum": 1,
                            "default": _FS_DEFAULT_MAX_ENTRIES,
                        },
                        "include_dirs": {
                            "type": "boolean",
                            "default": True,
                        },
                        "include_files": {
                            "type": "boolean",
                            "default": True,
                        },
                    },
                },
            ),
            LocalTool(
                name="search_content",
                description=(
                    "Grep text files for ``query`` (plain or "
                    "regex if ``is_regex=True``). Returns "
                    "{matches: [{path, line_number, line}], "
                    "truncated: bool, files_scanned, "
                    "match_count}. Skips binary files by "
                    "extension."
                ),
                server_name=self.SERVER_NAME,
                handler=self._search_content,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Search root. Defaults to "
                                "sandbox root."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Search string (plain or "
                                "regex if ``is_regex=True``)."
                            ),
                        },
                        "is_regex": {
                            "type": "boolean",
                            "default": False,
                        },
                        "case_sensitive": {
                            "type": "boolean",
                            "default": False,
                        },
                        "file_pattern": {
                            "type": "string",
                            "description": (
                                "fnmatch filter for file "
                                "names (e.g. ``*.py``)."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 100,
                        },
                        "context_chars": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 60,
                            "description": (
                                "Characters of context "
                                "around each match in the "
                                "``line`` field."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
            # ── WRITE CLASS ──────────────────────────────
            LocalTool(
                name="write_file",
                description=(
                    "Create or overwrite a UTF-8 text file "
                    "inside the filesystem write sandbox. "
                    "Returns {ok, path, size_bytes} or "
                    "{error}. Refuses dangerous executable "
                    "extensions (.exe/.bat/.sh/.ps1 etc.). "
                    "Atomic write: tmp + rename."
                ),
                server_name=self.SERVER_NAME,
                handler=self._write_file,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Destination path inside the "
                                "write sandbox."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "Full file body.",
                        },
                        "encoding": {"type": "string"},
                        "overwrite": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "If True, replace an "
                                "existing file at the same "
                                "path. Default refuses."
                            ),
                        },
                    },
                    "required": ["path", "content"],
                },
            ),
            LocalTool(
                name="append_file",
                description=(
                    "Append text to a UTF-8 file inside the "
                    "write sandbox. Creates the file if it "
                    "doesn't exist. Returns {ok, path, "
                    "appended_bytes, size_bytes} or {error}."
                ),
                server_name=self.SERVER_NAME,
                handler=self._append_file,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "encoding": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            LocalTool(
                name="delete_file",
                description=(
                    "Delete a single file inside the write "
                    "sandbox. Returns {ok, path} or "
                    "{error}. DESTRUCTIVE — cannot delete "
                    "directories; non-empty dirs fail."
                ),
                server_name=self.SERVER_NAME,
                handler=self._delete_file,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            ),
            LocalTool(
                name="move_file",
                description=(
                    "Rename or move a file inside the "
                    "write sandbox. Returns {ok, src, dst} "
                    "or {error}. Both paths must live in "
                    "the write sandbox."
                ),
                server_name=self.SERVER_NAME,
                handler=self._move_file,
                input_schema={
                    "type": "object",
                    "properties": {
                        "src": {"type": "string"},
                        "dst": {"type": "string"},
                        "overwrite": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Allow overwriting an "
                                "existing destination."
                            ),
                        },
                    },
                    "required": ["src", "dst"],
                },
            ),
            LocalTool(
                name="mkdir",
                description=(
                    "Create a directory (and parents if "
                    "needed) inside the write sandbox. "
                    "Returns {ok, path, created} or "
                    "{error}."
                ),
                server_name=self.SERVER_NAME,
                handler=self._mkdir,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "exist_ok": {
                            "type": "boolean",
                            "default": True,
                        },
                    },
                    "required": ["path"],
                },
            ),
            LocalTool(
                name="copy_file",
                description=(
                    "Copy a file (or overwrite in place) "
                    "inside the write sandbox. The source "
                    "is preserved — use move_file when you "
                    "want to remove it. Both paths must "
                    "live in the write sandbox; symlinks "
                    "are followed. Returns {ok, src, dst, "
                    "bytes_copied, overwrite} or {error}. "
                    "Dangerous extensions "
                    "(.exe/.bat/.sh/.ps1/.js/.vbs/.scr/...) "
                    "are refused regardless of sandbox."
                ),
                server_name=self.SERVER_NAME,
                handler=self._copy_file,
                input_schema={
                    "type": "object",
                    "properties": {
                        "src": {
                            "type": "string",
                            "description": (
                                "Source path. Must live in the "
                                "write sandbox."
                            ),
                        },
                        "dst": {
                            "type": "string",
                            "description": (
                                "Destination path. Must live "
                                "in the write sandbox. Same "
                                "directory tree as src."
                            ),
                        },
                        "overwrite": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Allow overwriting an "
                                "existing destination file."
                            ),
                        },
                        "follow_symlinks": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "When False, refuse if src or "
                                "any parent is a symlink that "
                                "points outside the sandbox."
                            ),
                        },
                    },
                    "required": ["src", "dst"],
                },
            ),
            LocalTool(
                name="diff_files",
                description=(
                    "Return a unified-diff between two text files "
                    "inside the read sandbox. Both files must be "
                    "text (extension whitelist). Returns "
                    "{ok, diff, identical, hunks, a_path, b_path, "
                    "truncated, read_root} or {error}. Use "
                    "context=N to widen/narrow the diff context "
                    "(default 3). Output is capped at "
                    "~4 MB; larger diffs set truncated=true."
                ),
                server_name=self.SERVER_NAME,
                handler=self._diff_files,
                input_schema={
                    "type": "object",
                    "properties": {
                        "a": {
                            "type": "string",
                            "description": (
                                "First file path. Must live in "
                                "the read sandbox."
                            ),
                        },
                        "b": {
                            "type": "string",
                            "description": (
                                "Second file path. Must live in "
                                "the read sandbox."
                            ),
                        },
                        "context": {
                            "type": "integer",
                            "default": _FS_DEFAULT_DIFF_CONTEXT,
                            "minimum": 0,
                            "maximum": 100,
                            "description": (
                                "Lines of context around each "
                                "hunk. 0 = no context (just "
                                "the +/- lines)."
                            ),
                        },
                        "max_output_bytes": {
                            "type": "integer",
                            "default": _FS_DIFF_MAX_OUTPUT_BYTES,
                            "description": (
                                "Hard cap on the diff string "
                                "size. Larger diffs return "
                                "truncated=true with the "
                                "partial output."
                            ),
                        },
                    },
                    "required": ["a", "b"],
                },
            ),
            LocalTool(
                name="patch_file",
                description=(
                    "Apply a unified-diff patch to a text file "
                    "inside the write sandbox. The patch must "
                    "be in the strict ``difflib.unified_diff`` "
                    "format (context / '-' / '+' lines, "
                    "``@@ -a,b +c,d @@`` headers). Returns "
                    "{ok, path, hunks_applied, bytes_written, "
                    "dry_run} or {error}. Pass dry_run=true to "
                    "preview without writing. Dangerous "
                    "extensions refused."
                ),
                server_name=self.SERVER_NAME,
                handler=self._patch_file,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Target file path. Must live in "
                                "the write sandbox. The patch "
                                "is applied IN PLACE — the "
                                "file is overwritten with the "
                                "patched content."
                            ),
                        },
                        "patch": {
                            "type": "string",
                            "description": (
                                "Unified-diff body. Must contain "
                                "at least one ``@@`` hunk "
                                "header."
                            ),
                        },
                        "dry_run": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When true, validate the patch "
                                "and return the would-be "
                                "result preview without "
                                "writing to disk."
                            ),
                        },
                    },
                    "required": ["path", "patch"],
                },
            ),
        ]

    # ── handler implementations (v0.9.3+) ────────────────
    # Every handler returns a dict and never raises — chat-
    # loop safety is non-negotiable.

    def _list_dir(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        pattern = args.get("pattern")
        include_dirs = bool(args.get("include_dirs", True))
        include_files = bool(args.get("include_files", True))
        try:
            target = _fs_resolve_path(
                rel if rel else self._read_root,
                self._read_root,
                allow_relative=True, must_be_dir=True,
            )
        except (ValueError, FileNotFoundError) as e:
            return self._err(str(e))
        try:
            entries = sorted(
                os.scandir(target), key=lambda e: e.name.lower(),
            )
        except OSError as e:
            return self._err(
                f"cannot list {target.as_posix()!r}: {e}"
            )
        out: list[dict] = []
        import fnmatch as _fn
        for entry in entries:
            if entry.name in (".", ".."):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
                is_link = entry.is_symlink()
            except OSError:
                continue
            if is_link:
                try:
                    t = (target / entry.name).resolve()
                except OSError:
                    continue
                if (
                    self._read_root not in t.parents
                    and t != self._read_root
                ):
                    continue
            if is_dir and not include_dirs:
                continue
            if is_file and not include_files:
                continue
            if pattern and not _fn.fnmatch(entry.name, pattern):
                continue
            out.append(
                _fs_format_entry(
                    target / entry.name, base=self._read_root,
                )
            )
        return {
            "path": target.as_posix(),
            "relative_path": (
                target.relative_to(self._read_root).as_posix()
                if target != self._read_root else "."
            ),
            "entries": out,
            "truncated": False,
            "count": len(out),
            "read_root": self._read_root.as_posix(),
        }

    def _stat_file(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        if not rel:
            return self._err("'path' is required.")
        try:
            p = _fs_resolve_path(
                rel, self._read_root, allow_relative=True,
            )
        except ValueError as e:
            return self._err(str(e))
        try:
            st = p.stat()
        except FileNotFoundError:
            try:
                rel_str = p.relative_to(self._read_root).as_posix()
            except ValueError:
                rel_str = p.name
            return {
                "exists": False,
                "path": p.as_posix(),
                "relative_path": rel_str,
                "read_root": self._read_root.as_posix(),
            }
        except OSError as e:
            return self._err(f"stat failed: {e}")
        try:
            rel_str = p.relative_to(self._read_root).as_posix()
        except ValueError:
            rel_str = p.name
        if p.is_dir():
            kind = "directory"
        elif p.is_file():
            kind = "file"
        else:
            kind = "other"
        return {
            "exists": True,
            "type": kind,
            "size_bytes": st.st_size,
            "mtime": st.st_mtime,
            "mode": oct(st.st_mode & 0o777),
            "is_symlink": p.is_symlink(),
            "path": p.as_posix(),
            "relative_path": rel_str,
            "read_root": self._read_root.as_posix(),
        }

    def _read_file(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        if not rel:
            return self._err("'path' is required.")
        try:
            p = _fs_resolve_path(
                rel, self._read_root,
                allow_relative=True, must_be_file=True,
            )
        except (ValueError, FileNotFoundError) as e:
            return self._err(str(e))
        ext = p.suffix.lower()
        if ext in _FS_BINARY_EXTENSIONS:
            return self._err(
                f"refusing to read binary file {p.as_posix()!r} "
                f"(extension {ext!r}). Use search_content for "
                f"grep-style access."
            )
        offset = int(args.get("offset_bytes", 0) or 0)
        if offset < 0:
            return self._err("'offset_bytes' must be >= 0.")
        max_bytes = int(
            args.get(
                "max_bytes", _FS_DEFAULT_READ_MAX_BYTES,
            ) or _FS_DEFAULT_READ_MAX_BYTES,
        )
        if max_bytes <= 0:
            return self._err("'max_bytes' must be > 0.")
        if max_bytes > _FS_READ_HARD_MAX_BYTES:
            return self._err(
                f"'max_bytes' capped at "
                f"{_FS_READ_HARD_MAX_BYTES}; requested {max_bytes}."
            )
        encoding = args.get("encoding") or "utf-8"
        try:
            size = p.stat().st_size
        except OSError as e:
            return self._err(f"stat failed: {e}")
        try:
            with p.open("rb") as fh:
                if offset:
                    fh.seek(offset)
                blob = fh.read(max_bytes)
        except OSError as e:
            return self._err(f"read failed: {e}")
        truncated = (
            len(blob) < (size - offset) or len(blob) == max_bytes
        )
        text = blob.decode(encoding, errors="replace")
        try:
            rel_str = p.relative_to(self._read_root).as_posix()
        except ValueError:
            rel_str = p.name
        return {
            "content": text,
            "encoding": encoding,
            "size_bytes": size,
            "returned_bytes": len(blob),
            "truncated": truncated,
            "offset_bytes": offset,
            "next_offset": (
                offset + len(blob) if truncated else None
            ),
            "path": p.as_posix(),
            "relative_path": rel_str,
            "read_root": self._read_root.as_posix(),
        }

    def _read_multiple(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        paths = args.get("paths")
        if not isinstance(paths, list):
            return self._err("'paths' must be a list of strings.")
        max_each = int(
            args.get("max_bytes_each", 16 * 1024) or 16 * 1024,
        )
        if max_each <= 0:
            return self._err("'max_bytes_each' must be > 0.")
        if max_each > _FS_READ_HARD_MAX_BYTES:
            max_each = _FS_READ_HARD_MAX_BYTES
        files: list[dict] = []
        total = 0
        for rel in paths:
            entry: dict
            if not isinstance(rel, str):
                entry = {
                    "path": str(rel),
                    "error": "path must be a string",
                }
                files.append(entry)
                continue
            try:
                p = _fs_resolve_path(
                    rel, self._read_root,
                    allow_relative=True, must_be_file=True,
                )
            except (ValueError, FileNotFoundError) as e:
                entry = {"path": rel, "error": str(e)}
                files.append(entry)
                continue
            ext = p.suffix.lower()
            if ext in _FS_BINARY_EXTENSIONS:
                entry = {
                    "path": p.as_posix(),
                    "error": f"binary extension {ext!r} refused",
                }
                files.append(entry)
                continue
            try:
                blob = p.read_bytes()[:max_each]
            except OSError as e:
                entry = {"path": p.as_posix(), "error": str(e)}
                files.append(entry)
                continue
            text = blob.decode("utf-8", errors="replace")
            try:
                rel_str = p.relative_to(self._read_root).as_posix()
            except ValueError:
                rel_str = p.name
            entry = {
                "path": p.as_posix(),
                "relative_path": rel_str,
                "content": text,
                "size_bytes": p.stat().st_size,
                "returned_bytes": len(blob),
            }
            total += len(blob)
            files.append(entry)
        return {
            "files": files,
            "total_bytes": total,
            "count": len(files),
            "read_root": self._read_root.as_posix(),
        }

    def _search_files(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        pattern = args.get("pattern")
        if not pattern:
            return self._err("'pattern' is required.")
        recursive = bool(args.get("recursive", True))
        max_results = int(
            args.get("max_results", _FS_DEFAULT_MAX_ENTRIES)
            or _FS_DEFAULT_MAX_ENTRIES,
        )
        try:
            target = _fs_resolve_path(
                rel if rel else self._read_root,
                self._read_root,
                allow_relative=True, must_be_dir=True,
            )
        except (ValueError, FileNotFoundError) as e:
            return self._err(str(e))
        paths, truncated = _fs_walk(
            target,
            max_entries=max_results,
            max_depth=99 if recursive else 1,
            include_dirs=False,
            include_files=True,
        )
        import fnmatch as _fn
        out: list[dict] = []
        for p in paths:
            if not _fn.fnmatch(p.name, pattern):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            try:
                rel_str = p.relative_to(self._read_root).as_posix()
            except ValueError:
                rel_str = p.name
            out.append({
                "path": p.as_posix(),
                "relative_path": rel_str,
                "size_bytes": size,
            })
            if len(out) >= max_results:
                break
        return {
            "matches": out,
            "match_count": len(out),
            "truncated": truncated or len(out) >= max_results,
            "pattern": pattern,
            "search_root": target.as_posix(),
            "read_root": self._read_root.as_posix(),
        }

    def _get_tree(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        max_depth = int(args.get("max_depth", 8) or 8)
        max_entries = int(
            args.get("max_entries", _FS_DEFAULT_MAX_ENTRIES)
            or _FS_DEFAULT_MAX_ENTRIES,
        )
        include_dirs = bool(args.get("include_dirs", True))
        include_files = bool(args.get("include_files", True))
        if max_depth < 1:
            return self._err("'max_depth' must be >= 1.")
        if max_entries < 1:
            return self._err("'max_entries' must be >= 1.")
        try:
            target = _fs_resolve_path(
                rel if rel else self._read_root,
                self._read_root,
                allow_relative=True, must_be_dir=True,
            )
        except (ValueError, FileNotFoundError) as e:
            return self._err(str(e))
        paths, truncated = _fs_walk(
            target,
            max_entries=max_entries,
            max_depth=max_depth,
            include_dirs=include_dirs,
            include_files=include_files,
        )
        entries = [
            _fs_format_entry(p, base=self._read_root)
            for p in paths
        ]
        return {
            "entries": entries,
            "total_count": len(entries),
            "truncated": truncated,
            "max_depth": max_depth,
            "search_root": target.as_posix(),
            "read_root": self._read_root.as_posix(),
        }

    def _search_content(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        query = args.get("query")
        if not query:
            return self._err("'query' is required.")
        is_regex = bool(args.get("is_regex", False))
        case_sensitive = bool(args.get("case_sensitive", False))
        file_pattern = args.get("file_pattern")
        max_results = int(args.get("max_results", 100) or 100)
        context_chars = int(args.get("context_chars", 60) or 60)
        if max_results < 1:
            return self._err("'max_results' must be >= 1.")
        try:
            target = _fs_resolve_path(
                rel if rel else self._read_root,
                self._read_root,
                allow_relative=True, must_be_dir=True,
            )
        except (ValueError, FileNotFoundError) as e:
            return self._err(str(e))
        if is_regex:
            try:
                rx = re.compile(
                    query,
                    0 if case_sensitive else re.IGNORECASE,
                )
            except re.error as e:
                return self._err(f"bad regex: {e}")
            needle_re = rx
            needle_plain = None
        else:
            needle_re = None
            needle_plain = (
                query if case_sensitive else query.lower()
            )
        matches: list[dict] = []
        files_scanned = 0
        truncated = False
        import fnmatch as _fn
        paths, _ = _fs_walk(
            target,
            max_entries=_FS_DEFAULT_MAX_ENTRIES,
            max_depth=99,
            include_dirs=False,
            include_files=True,
        )
        for p in paths:
            if file_pattern and not _fn.fnmatch(p.name, file_pattern):
                continue
            ext = p.suffix.lower()
            if ext in _FS_BINARY_EXTENSIONS:
                continue
            try:
                blob = p.read_bytes()
            except OSError:
                continue
            files_scanned += 1
            try:
                text = blob.decode("utf-8", errors="replace")
            except Exception:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                hit = False
                if needle_re is not None:
                    hit = bool(needle_re.search(line))
                else:
                    hay = line if case_sensitive else line.lower()
                    hit = needle_plain in hay
                if not hit:
                    continue
                if context_chars and len(line) > context_chars * 2:
                    if needle_re is not None:
                        m = needle_re.search(line)
                        start = max(0, m.start() - context_chars)
                        end = min(len(line), m.end() + context_chars)
                    else:
                        idx = (
                            line.lower().find(needle_plain)
                            if not case_sensitive
                            else line.find(needle_plain)
                        )
                        start = max(0, idx - context_chars)
                        end = min(
                            len(line),
                            idx + len(query) + context_chars,
                        )
                    snippet = line[start:end]
                else:
                    snippet = line
                try:
                    rel_str = p.relative_to(self._read_root).as_posix()
                except ValueError:
                    rel_str = p.name
                matches.append({
                    "path": p.as_posix(),
                    "relative_path": rel_str,
                    "line_number": lineno,
                    "line": snippet,
                })
                if len(matches) >= max_results:
                    truncated = True
                    break
            if truncated:
                break
        return {
            "matches": matches,
            "match_count": len(matches),
            "truncated": truncated,
            "files_scanned": files_scanned,
            "query": query,
            "is_regex": is_regex,
            "search_root": target.as_posix(),
            "read_root": self._read_root.as_posix(),
        }

    # ── WRITE handlers ────────────────────────────────

    def _write_file(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        content = args.get("content")
        if not rel:
            return self._err("'path' is required.")
        if content is None:
            return self._err("'content' is required.")
        if not isinstance(content, str):
            return self._err("'content' must be a string.")
        encoding = args.get("encoding") or "utf-8"
        overwrite = bool(args.get("overwrite", False))
        ext = os.path.splitext(str(rel).strip())[1].lower()
        if ext in _FS_DANGEROUS_WRITE_EXTS:
            return self._err(
                f"refusing to write dangerous extension {ext!r}."
            )
        try:
            p = _fs_resolve_path(
                rel, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return self._err(str(e))
        parent = p.parent
        if not parent.exists():
            return self._err(
                f"parent directory does not exist: "
                f"{parent.as_posix()!r}. Call mkdir first."
            )
        if p.exists() and p.is_dir():
            return self._err(
                f"path {p.as_posix()!r} is a directory."
            )
        if p.exists() and not overwrite:
            return self._err(
                f"file {p.as_posix()!r} already exists; pass "
                f"overwrite=true to replace."
            )
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=f".{p.name}.",
                suffix=".tmp",
                dir=str(parent),
            )
        except OSError as e:
            return self._err(f"cannot create temp file: {e}")
        try:
            try:
                with os.fdopen(
                    fd, "w", encoding=encoding, newline="",
                ) as fh:
                    fh.write(content)
                os.replace(tmp_path, p)
            except Exception as e:
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return self._err(f"write failed: {e}")
        except Exception as e:
            return self._err(f"write failed: {e}")
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        try:
            rel_str = p.relative_to(self._write_root).as_posix()
        except ValueError:
            rel_str = p.name
        return {
            "ok": True,
            "path": p.as_posix(),
            "relative_path": rel_str,
            "size_bytes": size,
            "encoding": encoding,
            "overwrite": overwrite,
            "write_root": self._write_root.as_posix(),
        }

    def _append_file(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        content = args.get("content")
        if not rel:
            return self._err("'path' is required.")
        if content is None:
            return self._err("'content' is required.")
        if not isinstance(content, str):
            return self._err("'content' must be a string.")
        encoding = args.get("encoding") or "utf-8"
        ext = os.path.splitext(str(rel).strip())[1].lower()
        if ext in _FS_DANGEROUS_WRITE_EXTS:
            return self._err(
                f"refusing to append dangerous extension {ext!r}."
            )
        try:
            p = _fs_resolve_path(
                rel, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return self._err(str(e))
        if p.exists() and p.is_dir():
            return self._err(
                f"path {p.as_posix()!r} is a directory."
            )
        if not p.parent.exists():
            return self._err(
                f"parent directory does not exist: "
                f"{p.parent.as_posix()!r}. Call mkdir first."
            )
        encoded = content.encode(encoding)
        appended_bytes = len(encoded)
        try:
            with open(p, "ab") as fh:
                fh.write(encoded)
        except OSError as e:
            return self._err(f"append failed: {e}")
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        try:
            rel_str = p.relative_to(self._write_root).as_posix()
        except ValueError:
            rel_str = p.name
        return {
            "ok": True,
            "path": p.as_posix(),
            "relative_path": rel_str,
            "appended_bytes": appended_bytes,
            "size_bytes": size,
            "encoding": encoding,
            "write_root": self._write_root.as_posix(),
        }

    def _delete_file(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        if not rel:
            return self._err("'path' is required.")
        try:
            p = _fs_resolve_path(
                rel, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return self._err(str(e))
        if not p.exists():
            return self._err(
                f"file not found: {p.as_posix()!r}"
            )
        if p.is_dir():
            return self._err(
                f"path {p.as_posix()!r} is a directory; "
                f"delete_file refuses to remove directories."
            )
        try:
            p.unlink()
        except OSError as e:
            return self._err(f"delete failed: {e}")
        try:
            rel_str = p.relative_to(self._write_root).as_posix()
        except ValueError:
            rel_str = p.name
        return {
            "ok": True,
            "path": p.as_posix(),
            "relative_path": rel_str,
            "write_root": self._write_root.as_posix(),
        }

    def _move_file(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        src = args.get("src")
        dst = args.get("dst")
        overwrite = bool(args.get("overwrite", False))
        if not src:
            return self._err("'src' is required.")
        if not dst:
            return self._err("'dst' is required.")
        try:
            src_p = _fs_resolve_path(
                src, self._write_root, allow_relative=True,
            )
            dst_p = _fs_resolve_path(
                dst, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return self._err(str(e))
        if not src_p.exists():
            return self._err(
                f"source not found: {src_p.as_posix()!r}"
            )
        if src_p.is_dir():
            return self._err(
                "move_file refuses to move directories."
            )
        if dst_p.exists() and not overwrite:
            return self._err(
                f"destination exists: {dst_p.as_posix()!r}; "
                f"pass overwrite=true to replace."
            )
        if not dst_p.parent.exists():
            return self._err(
                f"destination parent missing: "
                f"{dst_p.parent.as_posix()!r}; call mkdir first."
            )
        try:
            os.replace(src_p, dst_p)
        except OSError as e:
            return self._err(f"move failed: {e}")
        try:
            rel_str = dst_p.relative_to(self._write_root).as_posix()
        except ValueError:
            rel_str = dst_p.name
        return {
            "ok": True,
            "src": src_p.as_posix(),
            "dst": dst_p.as_posix(),
            "relative_path": rel_str,
            "overwrite": overwrite,
            "write_root": self._write_root.as_posix(),
        }

    def _mkdir(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        rel = args.get("path")
        exist_ok = bool(args.get("exist_ok", True))
        if not rel:
            return self._err("'path' is required.")
        try:
            p = _fs_resolve_path(
                rel, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return self._err(str(e))
        if p.exists() and not p.is_dir():
            return self._err(
                f"path {p.as_posix()!r} exists but is not "
                f"a directory."
            )
        if p.exists() and not exist_ok:
            return self._err(
                f"directory already exists: {p.as_posix()!r}"
            )
        created = not p.exists()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return self._err(f"mkdir failed: {e}")
        try:
            rel_str = p.relative_to(self._write_root).as_posix()
        except ValueError:
            rel_str = p.name
        return {
            "ok": True,
            "path": p.as_posix(),
            "relative_path": rel_str,
            "created": created,
            "write_root": self._write_root.as_posix(),
        }

    def _copy_file(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        src = args.get("src")
        dst = args.get("dst")
        overwrite = bool(args.get("overwrite", False))
        follow_symlinks = bool(args.get("follow_symlinks", True))
        if not src:
            return self._err("'src' is required.")
        if not dst:
            return self._err("'dst' is required.")
        try:
            src_p = _fs_resolve_path(
                src, self._write_root, allow_relative=True,
            )
            dst_p = _fs_resolve_path(
                dst, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return self._err(str(e))
        if not src_p.exists():
            return self._err(
                f"source not found: {src_p.as_posix()!r}"
            )
        if src_p.is_dir():
            return self._err(
                "copy_file refuses to copy directories."
            )
        if dst_p.is_dir():
            return self._err(
                f"destination {dst_p.as_posix()!r} is a "
                f"directory; pass a file path."
            )
        src_ext = src_p.suffix.lower()
        dst_ext = dst_p.suffix.lower()
        if src_ext in _FS_DANGEROUS_WRITE_EXTS:
            return self._err(
                f"refusing to copy a dangerous source file: "
                f"{src_p.as_posix()!r} (extension {src_ext!r})."
            )
        if dst_ext in _FS_DANGEROUS_WRITE_EXTS:
            return self._err(
                f"refusing to write a file with dangerous "
                f"extension: {dst_p.as_posix()!r} "
                f"({dst_ext!r})."
            )
        # Same-file check must come BEFORE the destination-exists
        # check, otherwise ``copy(src, src)`` reads as "dest
        # exists, refuse" instead of the more actionable
        # "same file" message.
        if src_p.resolve() == dst_p.resolve():
            return self._err(
                "source and destination resolve to the same "
                "file; use write_file instead."
            )
        if dst_p.exists() and not overwrite:
            return self._err(
                f"destination exists: {dst_p.as_posix()!r}; "
                f"pass overwrite=true to replace."
            )
        if not dst_p.parent.exists():
            return self._err(
                f"destination parent missing: "
                f"{dst_p.parent.as_posix()!r}; call mkdir first."
            )
        if not follow_symlinks:
            cur = src_p
            while cur != self._write_root and cur != cur.parent:
                if cur.is_symlink():
                    target = cur.resolve()
                    try:
                        target.relative_to(self._write_root)
                    except ValueError:
                        return self._err(
                            f"refusing to follow symlink "
                            f"outside sandbox: "
                            f"{cur.as_posix()!r} -> "
                            f"{target.as_posix()!r}."
                        )
                if cur.parent == cur:
                    break
                cur = cur.parent
        # Atomic copy via mkstemp + shutil + os.replace. Falls
        # back to non-atomic shutil.copyfile on temp failure so
        # we never leave a half-written copy at a surprising
        # path inside the sandbox.
        import shutil
        src_bytes = src_p.stat().st_size
        parent = dst_p.parent
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=".copy.", suffix=dst_p.suffix,
            dir=str(parent),
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "wb") as tmp_f:
                shutil.copyfileobj(
                    src_p.open("rb"), tmp_f, length=1024 * 1024,
                )
                tmp_f.flush()
                try:
                    os.fsync(tmp_f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, dst_p)
        except OSError as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return self._err(f"copy failed: {e}")
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        try:
            src_rel = src_p.relative_to(self._write_root).as_posix()
        except ValueError:
            src_rel = src_p.name
        try:
            dst_rel = dst_p.relative_to(self._write_root).as_posix()
        except ValueError:
            dst_rel = dst_p.name
        return {
            "ok": True,
            "src": src_p.as_posix(),
            "dst": dst_p.as_posix(),
            "relative_src": src_rel,
            "relative_dst": dst_rel,
            "bytes_copied": src_bytes,
            "overwrite": overwrite,
            "follow_symlinks": follow_symlinks,
            "write_root": self._write_root.as_posix(),
        }

    def _diff_files(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        a = args.get("a")
        b = args.get("b")
        context = args.get("context", _FS_DEFAULT_DIFF_CONTEXT)
        max_output_bytes = args.get(
            "max_output_bytes", _FS_DIFF_MAX_OUTPUT_BYTES,
        )
        if not a:
            return self._err("'a' is required.")
        if not b:
            return self._err("'b' is required.")
        try:
            context = int(context)
        except (TypeError, ValueError):
            return self._err("'context' must be an integer.")
        if context < 0 or context > 100:
            return self._err(
                "'context' must be between 0 and 100."
            )
        try:
            max_output_bytes = int(max_output_bytes)
        except (TypeError, ValueError):
            return self._err(
                "'max_output_bytes' must be an integer."
            )
        if max_output_bytes <= 0:
            return self._err(
                "'max_output_bytes' must be positive."
            )
        try:
            a_p = _fs_resolve_path(
                a, self._read_root, allow_relative=True,
            )
            b_p = _fs_resolve_path(
                b, self._read_root, allow_relative=True,
            )
        except ValueError as e:
            return self._err(str(e))
        for label, p in (("a", a_p), ("b", b_p)):
            if not p.exists():
                return self._err(
                    f"{label} not found: {p.as_posix()!r}"
                )
            if not p.is_file():
                return self._err(
                    f"{label} is not a regular file: "
                    f"{p.as_posix()!r}"
                )
            ext = p.suffix.lower()
            if ext not in _FS_TEXT_EXTENSIONS:
                return self._err(
                    f"{label} has non-text extension {ext!r}; "
                    f"diff_files refuses binaries."
                )
        a_size = a_p.stat().st_size
        b_size = b_p.stat().st_size
        if a_size > _FS_READ_HARD_MAX_BYTES:
            return self._err(
                f"a is {a_size} bytes, exceeds the "
                f"{_FS_READ_HARD_MAX_BYTES} byte hard cap."
            )
        if b_size > _FS_READ_HARD_MAX_BYTES:
            return self._err(
                f"b is {b_size} bytes, exceeds the "
                f"{_FS_READ_HARD_MAX_BYTES} byte hard cap."
            )
        try:
            a_text = a_p.read_text(encoding="utf-8")
            b_text = b_p.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            return self._err(
                f"file is not valid UTF-8: {e}"
            )
        diff = _fs_make_unified_diff(
            a_text, b_text,
            a_path=a_p.as_posix(),
            b_path=b_p.as_posix(),
            context=context,
        )
        identical = diff == ""
        truncated = False
        if len(diff.encode("utf-8")) > max_output_bytes:
            encoded = diff.encode("utf-8")[:max_output_bytes]
            cut = encoded.rfind(b"\n")
            if cut > 0:
                encoded = encoded[:cut]
            diff = encoded.decode("utf-8", errors="replace")
            truncated = True
        hunk_count = sum(
            1 for ln in diff.splitlines()
            if ln.startswith("@@")
        )
        try:
            a_rel = a_p.relative_to(self._read_root).as_posix()
        except ValueError:
            a_rel = a_p.name
        try:
            b_rel = b_p.relative_to(self._read_root).as_posix()
        except ValueError:
            b_rel = b_p.name
        return {
            "ok": True,
            "identical": identical,
            "hunks": hunk_count,
            "diff": diff,
            "truncated": truncated,
            "diff_bytes": len(diff.encode("utf-8")),
            "context": context,
            "a_path": a_p.as_posix(),
            "b_path": b_p.as_posix(),
            "relative_a": a_rel,
            "relative_b": b_rel,
            "read_root": self._read_root.as_posix(),
        }

    def _patch_file(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return self._err("'args' must be a JSON object.")
        path = args.get("path")
        patch = args.get("patch")
        dry_run = bool(args.get("dry_run", False))
        if not path:
            return self._err("'path' is required.")
        if not isinstance(patch, str):
            return self._err(
                "'patch' must be a string (unified-diff body)."
            )
        if not patch:
            return self._err("'patch' must be non-empty.")
        if len(patch.encode("utf-8")) > _FS_PATCH_MAX_PATCH_BYTES:
            return self._err(
                f"patch body is "
                f"{len(patch.encode('utf-8'))} bytes, "
                f"exceeds the {_FS_PATCH_MAX_PATCH_BYTES} "
                f"byte cap."
            )
        try:
            target = _fs_resolve_path(
                path, self._write_root, allow_relative=True,
            )
        except ValueError as e:
            return self._err(str(e))
        ext = target.suffix.lower()
        if ext in _FS_DANGEROUS_WRITE_EXTS:
            return self._err(
                f"refusing to patch a file with dangerous "
                f"extension: {target.as_posix()!r} "
                f"({ext!r})."
            )
        if not target.exists():
            return self._err(
                f"target not found: {target.as_posix()!r}; "
                f"patch_file applies patches in place — use "
                f"write_file to create a new file first."
            )
        if not target.is_file():
            return self._err(
                f"target is not a regular file: "
                f"{target.as_posix()!r}"
            )
        if ext not in _FS_TEXT_EXTENSIONS:
            return self._err(
                f"target has non-text extension {ext!r}; "
                f"patch_file refuses binaries."
            )
        original = target.read_text(encoding="utf-8")
        try:
            patched = _fs_apply_patch(original, patch)
        except ValueError as e:
            return self._err(f"patch failed: {e}")
        new_bytes = patched.encode("utf-8")
        hunk_count = sum(
            1 for ln in patch.splitlines()
            if ln.startswith("@@")
        )
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "hunks_applied": hunk_count,
                "bytes_would_write": len(new_bytes),
                "original_bytes": len(original.encode("utf-8")),
                "preview": patched[:4096],
                "path": target.as_posix(),
            }
        # Atomic write via mkstemp + os.replace (mirrors
        # write_file / append_file so a failed patch never
        # leaves the file half-overwritten).
        try:
            parent = target.parent
            fd, tmp_path_str = tempfile.mkstemp(
                prefix=".patch.", suffix=target.suffix,
                dir=str(parent),
            )
            tmp_path = Path(tmp_path_str)
            try:
                with os.fdopen(fd, "wb") as tmp_f:
                    tmp_f.write(new_bytes)
                    tmp_f.flush()
                    try:
                        os.fsync(tmp_f.fileno())
                    except OSError:
                        pass
                os.replace(tmp_path, target)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            return self._err(f"write failed: {e}")
        try:
            rel_str = target.relative_to(self._write_root).as_posix()
        except ValueError:
            rel_str = target.name
        return {
            "ok": True,
            "dry_run": False,
            "hunks_applied": hunk_count,
            "bytes_written": len(new_bytes),
            "original_bytes": len(original.encode("utf-8")),
            "path": target.as_posix(),
            "relative_path": rel_str,
            "write_root": self._write_root.as_posix(),
        }


class WebSearchLocalHost:
    """Local Ollama Web Search / Fetch tools."""

    def __init__(self):
        from src.tools.web_search import build_web_search
        self._web = build_web_search()

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="web_search",
                description=(
                    "Search the public web via Ollama's hosted search "
                    "API. Returns up to ``max_results`` snippets with "
                    "title / url / summary. Call this ONLY for "
                    "questions that cannot be answered from the "
                    "local document corpus (e.g. recent news, "
                    "external APIs)."
                ),
                server_name="web-local",
                handler=self._search,
            ),
            LocalTool(
                name="web_fetch",
                description=(
                    "Fetch the readable text of a URL and return it "
                    "as Markdown. Use this to follow up on a "
                    "``web_search`` result before quoting it in the "
                    "final answer."
                ),
                server_name="web-local",
                handler=self._fetch,
            ),
            LocalTool(
                name="web_fetch_batch",
                description=(
                    "Fetch MULTIPLE URLs in ONE call (parallel, "
                    "4 workers). Use this instead of several "
                    "sequential ``web_fetch`` calls — e.g. one page "
                    "per stock symbol or several search-result "
                    "candidates. Returns per-URL results with "
                    "per-URL failure containment."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "URLs to fetch (max 8)."
                            ),
                        },
                    },
                    "required": ["urls"],
                },
                server_name="web-local",
                handler=self._fetch_batch,
            ),
        ]

    def _search(self, query: str, max_results: int = 5) -> dict:
        hits = self._web.search(query=query, max_results=max_results)
        return {
            "query": query,
            "count": len(hits),
            "results": [h.to_prompt() for h in hits],
        }

    def _fetch(self, url: str) -> dict:
        page = self._web.fetch(url=url)
        if page is None:
            return {"url": url, "content": ""}
        # v0.9.84.3 — FetchedPage carries .content (v0.2.8 contract);
        # the .text read was an AttributeError waiting for the first
        # successful fallback page. Defensive getattr for safety.
        text = re.sub(
            r"\n{3,}", "\n\n",
            str(getattr(page, "content", "") or ""),
        ).strip()
        return {"url": url, "title": getattr(page, "title", "") or "",
                "content": text[:6000]}

    # v0.9.85.0 — batched fetch: ONE loop iteration covers up to N
    # URLs (parallel, 4 workers) instead of N LLM tool-loop rounds.
    def _fetch_batch(self, args: dict) -> dict:
        args = args or {}
        urls = args.get("urls")
        # v0.9.110.1 — NO pre-wrap; the bare/JSON/comma string forms
        # are handled by _as_str_list (a pre-wrapped JSON string used
        # to become ONE bogus item).
        raw = (os.environ.get("QDRANT_WEB_FETCH_BATCH_MAX", "") or "").strip()
        try:
            cap = max(1, min(20, int(raw))) if raw else 8
        except ValueError:
            cap = 8
        urls = _as_str_list(urls, "urls")
        if urls is None:
            return {"error": "urls must be a list of URLs (a single URL string or a JSON/comma form also accepted)"}
        urls = [str(u) for u in urls if str(u).strip()][:cap]
        if not urls:
            return {"error": "urls must be a non-empty list of URLs"}
        from concurrent.futures import ThreadPoolExecutor

        def _one(u: str) -> dict:
            try:
                return self._fetch(u)
            except Exception as e:  # per-URL containment
                return {"url": u, "error": str(e)[:200]}

        with ThreadPoolExecutor(max_workers=min(4, len(urls))) as pool:
            results = list(pool.map(_one, urls))
        ok = sum(1 for r in results if r.get("content"))
        return {
            "count": len(results),
            "ok_count": ok,
            "failed_count": len(results) - ok,
            "results": results,
        }


def _task_max_parallel() -> int:
    """``QDRANT_TASK_MAX_PARALLEL`` — max CONCURRENT background tasks
    (source=``task``) accepted by ``run_task_once``.

    The 2026-10-04 05:41 incident: ONE turn spawned SIX parallel tasks
    (62 s / 227 dispatches) — provider 429s + token x6 + offload-chain
    storms. The default cap (4) rejects the surplus AT THE SPAWN SITE
    with a batching hint, instead of letting ``turn_gate`` fail queued
    turns later. 0 = unlimited (legacy); invalid/negative -> default.
    """
    raw = (os.environ.get("QDRANT_TASK_MAX_PARALLEL", "") or "").strip()
    if not raw:
        return _TASK_MAX_PARALLEL_DEFAULT
    try:
        v = int(raw)
    except ValueError:
        return _TASK_MAX_PARALLEL_DEFAULT
    if v < 0:
        return _TASK_MAX_PARALLEL_DEFAULT
    return v


_TASK_MAX_PARALLEL_DEFAULT = 4


class TaskLocalHost:
    """Background-task registry tools (v0.9.45.0 Phase 2 — parallel
    multi-task core).

    Three tools over ``src.agent.task_runner`` (the process-wide
    registry every background LLM turn — manual task / scheduler fire /
    webhook fire / telegram turn — reports to):

      * ``run_task_once(title, prompt, use_web_search?)`` — submit ONE
        LLM turn to a background daemon thread; returns ``task_id`` +
        ``sid`` IMMEDIATELY. The turn runs through a FRESH Agent
        (sharing this process's ``McpManager``) and persists its
        conversation into a dedicated session ``task-<task_id>``
        (readable in the Sessions tab / ``get_chat_session``).
      * ``list_tasks(status?, limit?)`` — running + recent background
        turns across ALL sources with live counts.
      * ``get_task_result(task_id)`` — status + session id + the
        session's last assistant message (capped preview).

    Lets the LLM answer 「背景執行」 / 「在背景跑」 / 「不要等我」 /
    'run this in the background' without blocking the conversation.
    Concurrency limiting is NOT done here — background turns cross
    ``turn_gate.turn_slot()`` like every other turn (a
    ``QDRANT_MAX_CONCURRENT_TURNS`` cap queues them).
    """

    SERVER_NAME = "task-local"

    _DEFAULT_LIST_LIMIT = 20
    _MAX_LIST_LIMIT = 200
    _PREVIEW_CHARS = 800

    def __init__(self, mcp_manager=None):
        # The manager this host was registered with — background
        # agents share the SAME tool fleet instead of rebuilding a
        # ~15 s McpManager per task.
        self._mcp_manager = mcp_manager

    # Module-level import inside the method so tests can monkeypatch
    # this single hook instead of the Agent constructor.
    def _fresh_agent(self, llm: str | None = None):
        from src.agent.agent import Agent

        # v0.9.110.0 — llm pin threads through ONLY when given; the
        # no-llm call stays the exact legacy single-arg constructor
        # (tests monkeypatch this hook and assert its shape).
        if llm:
            return Agent(mcp_manager=self._mcp_manager, task_llm=str(llm))
        return Agent(mcp_manager=self._mcp_manager)

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="run_task_once",
                description=(
                    "Run ONE LLM turn in the BACKGROUND and return "
                    "immediately (task_id + session id). Use when the "
                    "user says 「背景執行」 「在背景跑」 「不要等我」 "
                    "「先去做 X，好了跟我說」 / 'run it in the "
                    "background' / 'don't wait for me' — and the task "
                    "is long (deep research, multi-step file work, "
                    "desktop automation). Check progress later with "
                    "list_tasks / get_task_result(task_id). The full "
                    "conversation lands in session task-<task_id> "
                    "(Sessions tab)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": (
                                "Short human-readable task name "
                                "(shown in the task list / Sessions "
                                "tab). Defaults to the first 40 chars "
                                "of prompt."
                            ),
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Required. The FULL task prompt for "
                                "the background turn — it runs with "
                                "the complete tool surface, so spell "
                                "out every step; the background agent "
                                "cannot ask you questions."
                            ),
                        },
                        "use_web_search": {
                            "type": "boolean",
                            "description": (
                                "Also fetch fresh web context for "
                                "this turn. Default false."
                            ),
                        },
                        "llm": {
                            "type": "string",
                            "description": (
                                "Optional. Pin THIS task's chat "
                                "model: 'provider:model' where "
                                "provider is openrouter | ollama "
                                "(e.g. 'openrouter:z-ai/glm-4.6', "
                                "'ollama:gpt-oss:120b-cloud'). "
                                "Validated against the LIVE "
                                "provider catalog at submission; "
                                "the pin bypasses the cross-"
                                "provider failover walk (fail-"
                                "closed). Omit = the default "
                                "provider chain. Discover ids "
                                "with list_task_models."
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
                server_name=self.SERVER_NAME,
                handler=self._run_once,
            ),
            LocalTool(
                name="list_tasks",
                description=(
                    "List background LLM tasks (newest first) with "
                    "status / source / session id. Use to answer "
                    "「現在有哪些在跑」 / 「背景任務進度」 / 'what is "
                    "running in the background'. status: running | "
                    "done | failed (omit = all)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["running", "done", "failed"],
                            "description": (
                                "Optional status filter. Omit = all."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "Max records returned (1-200, "
                                "default 20)."
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list,
            ),
            LocalTool(
                name="get_task_result",
                description=(
                    "Read ONE background task's outcome: status, "
                    "session id, and the session's last assistant "
                    "message (capped preview). Use after "
                    "run_task_once(task_id) or to inspect a scheduler / "
                    "webhook fire's progress. The FULL conversation is "
                    "in session <sid> — get_chat_session(sid) reads it."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": (
                                "Required. The task id returned by "
                                "run_task_once, or a record's task_id "
                                "from list_tasks."
                            ),
                        },
                        "preview_chars": {
                            "type": "integer",
                            "description": (
                                "Answer preview length cap "
                                "(100-4000, default 800)."
                            ),
                        },
                    },
                    "required": ["task_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get,
            ),
            LocalTool(
                name="list_task_models",
                description=(
                    "Discover which chat models a background task or "
                    "skill stage can pin via run_task_once(llm=...) / "
                    "compile_plan stage llm — catalog snapshot from "
                    "the LIVE openrouter + ollama endpoints "
                    "(READ-ONLY, TTL-cached). provider: openrouter | "
                    "ollama (omit = both); contains: case-insensitive "
                    "substring filter (e.g. 'glm', 'qwen', 'gpt-oss')."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "enum": ["openrouter", "ollama"],
                            "description": (
                                "Optional single-provider filter. "
                                "Omit = both catalogs."
                            ),
                        },
                        "contains": {
                            "type": "string",
                            "description": (
                                "Optional case-insensitive substring "
                                "filter over model ids."
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._models,
            ),
            LocalTool(
                name="stop_task",
                description=(
                    "FORCE-STOP a background task (v0.9.120.0+): sets "
                    "the registry stop flag — the turn ends COOPERATIVELY "
                    "at its next tool-loop boundary (worst case ≈ one "
                    "in-flight tool call / LLM round; timeout-guard "
                    "zombies honour it too). Works on runs from ANY "
                    "source (telegram / task / plan / scheduler). "
                    "list_tasks rows show stop_requested; the ending "
                    "registry status = 'stopped'."),
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": (
                                "Required. The task id from list_tasks "
                                "— e.g. a telegram turn you want to "
                                "abort mid-execution."
                            ),
                        },
                    },
                    "required": ["task_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._stop,
            ),
        ]

    # ── handlers ──────────────────────────────────────────────

    def _stop(self, args: dict) -> dict:
        """v0.9.120.0 — force-stop one task via its registry flag."""
        try:
            from src.agent import task_runner

            tid = str((args or {}).get("task_id") or "").strip()
            if not tid:
                return {"error": (
                    "task_id is required (from list_tasks — e.g. the "
                    "telegram turn you want to abort).")}
            if task_runner.stop_requested(tid):
                return {"ok": True, "task_id": tid,
                        "note": ("stop flag was already set — the turn "
                                 "ends at its next tool-loop boundary.")}
            if not task_runner.request_stop(tid):
                return {"error": (
                    f"task {tid!r} not found in this process's registry "
                    "(restarts clear it; check list_tasks).")}
            return {
                "ok": True, "task_id": tid,
                "note": (
                    "stop flag set — the turn ends cooperatively at "
                    "its next tool-loop iteration (worst case ≈ one "
                    "in-flight tool call / LLM round). Completed tool "
                    "work is kept; registry status becomes 'stopped'."
                ),
            }
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    def _run_once(self, args: dict) -> dict:
        """Submit ONE background LLM turn; returns task_id + sid."""
        from src.agent import task_runner

        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return {
                "error": (
                    "prompt is required — describe the FULL task the "
                    "background agent should execute."
                ),
            }
        title = str(args.get("title") or "").strip() or prompt[:40]
        use_web = bool(args.get("use_web_search") or False)
        # v0.9.110.0 — task-level model pin (+ fail-fast catalog
        # validation; unknown model = NO task started).
        llm = str(args.get("llm") or "").strip()
        llm_warning = None
        if llm:
            from src.agent.task_llm import validate_task_llm

            verdict = validate_task_llm(llm)
            if not verdict.get("ok"):
                return {
                    "error": (
                        "task llm pin rejected: "
                        + str(verdict.get("error"))
                    ),
                    "suggestions": verdict.get("suggestions") or [],
                    "hint": (
                        "Call list_task_models to discover valid "
                        "provider:model ids; omit 'llm' to run the "
                        "default provider chain."
                    ),
                }
            # alias normalisation: use the catalog-canonical id
            llm = f"{verdict['provider']}:{verdict['model']}"
            llm_warning = verdict.get("warning")

        # v0.9.108.0 — spawn-site concurrency cap (see _task_max_parallel).
        cap = _task_max_parallel()
        if cap > 0:
            try:
                running = [
                    r for r in (
                        task_runner.list_tasks(
                            status="running", limit=200,
                        ).get("tasks") or []
                    ) if r.get("source") == "task"
                ]
            except Exception:
                running = []  # bookkeeping hiccup -> fail-open, spawn goes
            if len(running) >= cap:
                return {
                    "error": (
                        f"背景任務併發上限（QDRANT_TASK_MAX_PARALLEL="
                        f"{cap}）已達 — 目前 {len(running)} 個 task 在跑："
                        + "、".join(str(r.get("title"))[:40]
                                    for r in running[:cap])
                        + f"。請先用 list_tasks / get_task_result 回收，"
                        f"下一輪再投放其餘單元（每輪 ≤ {cap} 個分批）。"
                    ),
                    "running_count": len(running),
                    "cap": cap,
                }

        task_id = task_runner.begin_task(
            "task", title, llm=(llm or None),
        )
        sid = f"task-{task_id}"
        # v0.9.133.18 — register the sid immediately: the write-back
        # poller (bgWatchTick → get_background_task) reads the answer
        # preview from THIS registry field; leaving it None made every
        # manual background submission land as 「(無回覆 — 模型未產生
        # 內容)」 even though the reply sat in the task session.
        task_runner.attach_sid(task_id, sid)
        # v0.9.132.3 — optional origin chat-session binding (the web/GUI
        # chat passes its own session id so the client can write the
        # finished result back into that conversation).
        origin_sid = str(args.get("origin_sid") or "").strip() or None
        if origin_sid:
            task_runner.set_origin(task_id, origin_sid)

        def _work() -> None:
            # Runs in its own daemon thread. All failures are stamped
            # into the registry AND the session file — nothing
            # propagates into the caller's thread.
            try:
                agent = self._fresh_agent(llm=(llm or None))
            except Exception as e:
                task_runner.end_task(
                    task_id, task_runner.FAILED, error=repr(e)[:500],
                )
                return
            # v0.9.120.0 — wire the registry stop flag into the tool
            # loop (cooperative force-stop via ``stop_task``).
            try:
                _setter = getattr(agent, "set_stop_check", None)
                if callable(_setter):
                    _setter(
                        lambda: task_runner.stop_requested(task_id))
                else:
                    setattr(agent, "_stop_check",
                            lambda: task_runner.stop_requested(task_id))
            except Exception:
                pass
            try:
                from src.agent.session import Session, SessionManager

                sess = Session(sid=sid, title=f"[task] {title}")
                sess.messages.append({
                    "role": "user", "content": prompt,
                    "ts": task_runner.now_iso(),
                })
                try:
                    result = agent.chat_turn(
                        question=prompt, history=[],
                        use_web_search=use_web, use_tools=True,
                    )
                except Exception as chat_exc:
                    sess.messages.append({
                        "role": "assistant",
                        "content": f"[error] {chat_exc!r}",
                        "ts": task_runner.now_iso(),
                        "sources": [], "error": True,
                        "task_id": task_id,
                    })
                    sess.updated_at = task_runner.now_iso()
                    try:
                        SessionManager().save(sess)
                    except Exception:
                        pass
                    task_runner.end_task(
                        task_id, task_runner.FAILED,
                        error=repr(chat_exc)[:500],
                    )
                    return
                # Tuple/dict dual contract (v0.9.23.0 lesson).
                if isinstance(result, dict):
                    answer = result.get("answer")
                elif isinstance(result, tuple) and len(result) >= 1:
                    answer = result[0]
                else:
                    answer = None
                answer_text = str(answer or "").strip() or (
                    "(無回覆 — 模型未產生內容)"
                )
                sess.messages.append({
                    "role": "assistant", "content": answer_text,
                    "ts": task_runner.now_iso(), "sources": [],
                    "task_id": task_id,
                })
                sess.updated_at = task_runner.now_iso()
                try:
                    SessionManager().save(sess)
                except Exception as e:
                    logger.warning(
                        "[task-local] session save(%s) failed: %r",
                        sid, e,
                    )
                # v0.9.120.0 — a stop request ends the task as STOPPED
                # (still stamps the session; skips the memory sink).
                if task_runner.stop_requested(task_id):
                    task_runner.end_task(
                        task_id, task_runner.STOPPED,
                        answer_chars=len(answer_text),
                    )
                    return
                task_runner.end_task(
                    task_id, task_runner.DONE,
                    answer_chars=len(answer_text),
                )
                # v0.9.111.0 — conclusion sink (kind=task-result,
                # store-NOT-recall; QDRANT_TASK_MEMORY_SINK=off opts
                # out). Fire-and-forget — never blocks the worker.
                try:
                    from src.agent.memory_store import (
                        store_task_result,
                        task_sink_enabled,
                    )

                    if task_sink_enabled():
                        threading.Thread(
                            target=store_task_result,
                            kwargs={
                                "title": title,
                                "prompt": prompt,
                                "answer": answer_text,
                                "source": f"task:{task_id}",
                                "session_id": sid,
                            },
                            daemon=True, name=f"task-sink-{task_id}",
                        ).start()
                except Exception:
                    pass
            except Exception as e:  # pragma: no cover — defence
                task_runner.end_task(
                    task_id, task_runner.FAILED, error=repr(e)[:500],
                )

        threading.Thread(
            target=_work, daemon=True, name=f"task-{task_id}",
        ).start()
        out = {
            "ok": True,
            "task_id": task_id,
            "sid": sid,
            "title": title,
            "status": "running",
            "llm": (llm or None),
            "note": (
                "Submitted in the background. Check progress with "
                "list_tasks / get_task_result(task_id); the full "
                "conversation lands in session " + sid + " (Sessions "
                "tab). Never claim the result is ready now."
            ),
        }
        if llm_warning:
            out["warning"] = llm_warning
        return out

    def _models(self, args: dict) -> dict:
        """Catalog snapshot for pinning task/stage models (v0.9.110.0)."""
        from src.agent.task_llm import list_task_models

        return list_task_models(
            provider=(str(args.get("provider") or "").strip() or None),
            contains=(str(args.get("contains") or "").strip() or None),
        )

    def _list(self, args: dict) -> dict:
        from src.agent import task_runner

        status = str(args.get("status") or "").strip() or None
        if status and status not in (
            task_runner.RUNNING, task_runner.DONE, task_runner.FAILED,
        ):
            return {
                "error": (
                    f"unknown status {status!r} — use running | done "
                    f"| failed (or omit for all)."
                ),
            }
        out = task_runner.list_tasks(
            status=status, limit=args.get("limit"),
        )
        out["note"] = (
            "registry is in-memory — restart clears it; the session "
            "files persist (Sessions tab / get_chat_session)."
        )
        return out

    def _get(self, args: dict) -> dict:
        from src.agent import task_runner

        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return {"error": "task_id is required"}
        rec = task_runner.get_task(task_id)
        if rec is None:
            return {
                "error": (
                    f"unknown task_id {task_id!r} — the registry is "
                    f"in-memory; the session may still exist in the "
                    f"Sessions tab after a restart."
                ),
            }
        try:
            preview = min(
                max(
                    int(args.get("preview_chars")
                        or self._PREVIEW_CHARS), 100,
                ),
                4000,
            )
        except (TypeError, ValueError):
            preview = self._PREVIEW_CHARS
        # Read the session's last assistant message as the result
        # preview (running tasks get whatever is persisted so far).
        preview_text = None
        if rec.get("sid"):
            try:
                from src.agent.session import SessionManager

                sess = SessionManager().load(rec["sid"])
                if sess is not None:
                    for msg in reversed(sess.messages):
                        if msg.get("role") == "assistant":
                            preview_text = str(msg.get("content") or "")
                            break
            except Exception as e:  # pragma: no cover
                logger.debug("[task-local] preview read failed: %r", e)
        if preview_text is not None:
            rec["answer_preview"] = preview_text[:preview]
            rec["preview_truncated"] = len(preview_text) > preview
        return rec


class MetaLocalHost:
    """Tools that introspect the McpManager itself (v0.8.2+).

    Provides ``list_mcp_tools`` so the LLM can ask "本地有哪些
    MCP 功能可以用?" and get an authoritative answer based on
    the actual live ``McpManager`` catalog — not a guess and
    not a web search hit.

    The handler receives the McpManager instance via a small
    setter (``attach_manager``), called by ``McpManager.from_config``
    right after the local hosts are registered. We can't pass the
    manager into the constructor because ``from_config`` builds
    the manager first, then seeds the local hosts — a chicken-
    and-egg problem the setter resolves cleanly.
    """

    def __init__(self):
        self._manager: Any = None

    def attach_manager(self, manager: Any) -> None:
        """Called by ``McpManager.from_config`` after registration."""
        self._manager = manager

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="list_mcp_tools",
                description=(
                    "List every MCP tool currently registered in the "
                    "agent's tool catalog. Includes local hosts "
                    "(Qdrant / Web Search) and every connected "
                    "remote MCP server in config/mcp.json. Use this "
                    "when the user asks 'what tools do I have', "
                    "'本地有哪些 MCP 功能', or before promising that "
                    "a specific capability exists. Returns name + "
                    "description + server per tool."
                ),
                server_name="meta-local",
                handler=self._list,
            ),
        ]

    def _list(self) -> dict:
        if self._manager is None:
            return {
                "tool_count": 0,
                "tools": [],
                "servers": [],
                "failed": {},
                "note": (
                    "McpManager not attached yet — call "
                    "McpManager.from_config() to seed."
                ),
            }
        # v0.9.13.2+: the catalog must agree with the DECLARED
        # per-request surface (MCP_ACTIVE_SERVERS gate). Listing
        # tools the LLM cannot call made it fake the call in
        # plain text instead of emitting a real tool_call.
        tools = self._manager.active_tools()
        hidden = list(
            getattr(self._manager, "last_hidden_servers", None)
            or []
        )
        # Group by server_name for the prompt.
        grouped: dict[str, list] = {}
        for t in tools:
            grouped.setdefault(t.server_name, []).append({
                "name": t.name,
                "description": t.description,
            })
        out = {
            "tool_count": len(tools),
            "server_count": len(grouped),
            "servers": sorted(grouped.keys()),
            "tools": [
                {
                    "name": t.name,
                    "server": t.server_name,
                    "description": t.description,
                }
                for t in tools
            ],
            "tools_by_server": grouped,
            "failed": dict(self._manager.failed or {}),
        }
        if hidden:
            out["hidden_servers"] = hidden
            out["hidden_note"] = (
                "These servers are registered but NOT exposed in "
                "this chat (MCP_ACTIVE_SERVERS) — their tools "
                "CANNOT be called. Do NOT fake their calls in "
                "text. Tell the user to add the server to "
                "MCP_ACTIVE_SERVERS, then retry."
            )
        return out


class SchedulerLocalHost:
    """In-process tools for managing the local scheduler (v0.8.6+).

    Mirrors the public surface of ``src.mcp_server.scheduler_server``
    but runs in-process — no stdio subprocess, no JSON-RPC overhead.
    The LLM sees the same 8 tool names; the LocalHost dispatches
    them straight to ``ScheduleManager``.
    """
    def __init__(self):
        self._mgr: Any = None
        # v0.8.6.3: optional callback fired after a successful
        # ``_add`` so the underlying daemon can be spun up
        # automatically. Without this the user has to manually
        # click "Start daemon" in the GUI / run
        # ``divine-sense-agent.bat schedules daemon`` before the very
        # first ``add_scheduled_task`` will actually fire — the
        # common trap reported in 2026-08-31.
        self._auto_start: Callable[[], None] | None = None

    def attach_manager(self, mgr_or_root: Any) -> None:
        """Called by ``McpManager.from_config`` after registration.

        Accepts either a ``ScheduleManager`` instance, a path
        string, or a ``McpManager`` (the top-level manager wired
        in v0.8.6.3) — in the last case we still build a fresh
        ``ScheduleManager`` for storage but copy the
        ``auto_start_callback`` so ``_add`` can spin up the
        daemon thread without waiting for an explicit
        ``Agent.start_scheduler()`` call.
        """
        try:
            from src.agent.scheduler import ScheduleManager
            if isinstance(mgr_or_root, ScheduleManager):
                self._mgr = mgr_or_root
            elif isinstance(mgr_or_root, (str, bytes, os.PathLike)):
                self._mgr = ScheduleManager(root=mgr_or_root)
            else:
                self._mgr = ScheduleManager()
                # v0.8.6.3: if the McpManager has wired an
                # auto-start callback (Agent does this lazily
                # on first ``mcp_manager`` access), capture it
                # here too. ``Agent.mcp_manager`` may also push
                # it later via ``attach_auto_start`` — both
                # paths converge on ``self._auto_start``.
                cb = getattr(mgr_or_root, "auto_start_callback", None)
                if callable(cb):
                    self._auto_start = cb
        except Exception:
            self._mgr = None

    def attach_auto_start(self, callback: Callable[[], None]) -> None:
        """Register a callable to invoke after a successful ``_add``.

        The callback should be idempotent — it gets called on every
        add, but only the first time actually does the heavy
        ``Scheduler.start()`` work (subsequent calls are no-ops
        once the daemon thread is alive).

        Wired by ``Agent`` via ``McpManager.from_config()`` so the
        in-process LocalHost and the GUI / CLI Agent always agree
        on the daemon lifecycle.
        """
        self._auto_start = callback

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="add_scheduled_task",
                description=(
                    "Register a new scheduled task. Three types "
                    "are supported: 'cron' (5-field standard cron "
                    "expression; day-of-week 0=Sun..6=Sat), "
                    "'interval_seconds' (positive int >= 5; the "
                    "scheduler ticks once per second so sub-5s "
                    "intervals are rejected), and 'once_at' "
                    "(ISO-8601 timestamp — fires once then "
                    "auto-disables). Returns the new schedule's "
                    "id + next_fire_at. Use this whenever the "
                    "user says 'schedule X', 'every day at 9am', "
                    "'remind me tomorrow at 3pm', 'set up a "
                    "recurring check', or otherwise asks for "
                    "automated, time-triggered activity."
                ),
                server_name="scheduler-local",
                handler=self._add,
            ),
            LocalTool(
                name="delete_scheduled_task",
                description=(
                    "Remove a task and its fire history. Accepts "
                    "either the 12-char id or the human-readable "
                    "name. Use when the user says 'delete X', "
                    "'remove X', 'get rid of the X schedule'."
                ),
                server_name="scheduler-local",
                handler=self._delete,
            ),
            LocalTool(
                name="list_scheduled_tasks",
                description=(
                    "List every task the scheduler knows about. "
                    "Returns a flat array of objects with id, "
                    "name, schedule_type, enabled, fire_count, "
                    "last_fire_at, next_fire_at, and a one-word "
                    "status. Use this when the user asks 'what "
                    "do I have scheduled', 'show my tasks', or "
                    "before doing a delete / pause."
                ),
                server_name="scheduler-local",
                handler=self._list,
            ),
            LocalTool(
                name="get_scheduled_task",
                description=(
                    "Full detail for one task, including "
                    "``last_error`` and ``last_error_at`` from "
                    "the most recent fire. Use when the user "
                    "asks 'what does X do', 'show me the X "
                    "schedule', or to diagnose a specific task."
                ),
                server_name="scheduler-local",
                handler=self._get,
            ),
            LocalTool(
                name="enable_scheduled_task",
                description=(
                    "Resume a paused task. Recomputes "
                    "``next_fire_at`` so the daemon picks it "
                    "up on its next tick. Use when the user "
                    "says 'resume X', 'turn X back on'."
                ),
                server_name="scheduler-local",
                handler=self._enable,
            ),
            LocalTool(
                name="disable_scheduled_task",
                description=(
                    "Pause a task without deleting it. The "
                    "schedule's configuration is preserved; "
                    "the daemon will skip it until enabled "
                    "again. Use when the user says 'pause X', "
                    "'stop X for now', 'suspend X'."
                ),
                server_name="scheduler-local",
                handler=self._disable,
            ),
            LocalTool(
                name="run_scheduled_task_now",
                description=(
                    "Fire a task synchronously, bypassing "
                    "the daemon. The agent runs the prompt "
                    "with the schedule's tool / web-search "
                    "settings; a new fire record is appended "
                    "to history.jsonl and the dedicated "
                    "session. Use when the user says 'run X "
                    "now', 'trigger X', 'execute X right away'."
                ),
                server_name="scheduler-local",
                handler=self._run_now,
            ),
            LocalTool(
                name="get_scheduled_task_errors",
                description=(
                    "Query the past failures of a task from "
                    "its history.jsonl. Returns one record "
                    "per failed fire, including the error "
                    "string, the prompt that was attempted, "
                    "and the session id (so the user can "
                    "open the session for full context). Use "
                    "when the user asks 'why did X fail', "
                    "'show me the errors', 'is X working'."
                ),
                server_name="scheduler-local",
                handler=self._errors,
            ),
            # v0.8.7+: lifecycle snapshot. Returns the full
            # ``ScheduleManager.get_progress`` payload — fire
            # count, max_fires cap + remaining, expires_at + seconds
            # until expiry, current status (active/expired/
            # exhausted/disabled/failed), last 5 runs. Lets the
            # LLM answer "還能跑幾次" / "這個排程過期了嗎" / "上次
            # 什麼時候跑的" in one roundtrip.
            LocalTool(
                name="get_scheduled_task_status",
                description=(
                    "Get the full lifecycle snapshot of a "
                    "schedule: status (active/expired/exhausted/"
                    "disabled/failed), fire_count / max_fires / "
                    "fires_remaining, last_fire_at / next_fire_at / "
                    "seconds_until_next_fire, expires_at / "
                    "seconds_until_expiry, last_error, and the "
                    "last 5 runs (ts / prompt / answer preview / "
                    "sources / error flag). Use when the user "
                    "asks '這個排程跑幾次了', '還會跑幾次', '這個"
                    "排程過期了嗎', '上次排程什麼時候跑的', or "
                    "when you need to decide whether to keep a "
                    "schedule running."
                ),
                server_name="scheduler-local",
                handler=self._status,
            ),
            LocalTool(
                name="list_scheduled_task_runs",
                description=(
                    "List the most recent fire records of a "
                    "schedule from its history.jsonl (newest "
                    "last). Each row carries the timestamp, "
                    "prompt that was sent, truncated answer "
                    "(500-char preview + ellipsis on longer), "
                    "sources, error flag if the fire failed, and "
                    "the v0.9.106.0 `verified` three-state "
                    "verification marker (true = verified/honest "
                    "failure, false = UNVERIFIED success claim, "
                    "absent = verification not required). Use "
                    "when the user asks '這個排程上次跑得怎"
                    "樣', 'show me the last 10 runs', 'history', "
                    "or when auditing whether fires really "
                    "verified their output."
                ),
                server_name="scheduler-local",
                handler=self._runs,
            ),
            # v0.9.106.0 — P1 機械式檢查（獨立評估器）: a ZERO-LLM
            # verifier for produced artifacts. The schedule-fire
            # verification directive names this tool explicitly, so
            # the LLM's "step N+1: verify" node is a real machine
            # check (exists / min_bytes / JSON fields), not
            # self-assessment.
            LocalTool(
                name="verify_artifact",
                description=(
                    "Mechanical artifact verification (ZERO LLM, "
                    "zero hallucination). Checks a produced file "
                    "inside the filesystem sandbox: existence, "
                    "minimum size, JSON parse, and per-field "
                    "requirements (dotted path like "
                    "'stage1.status', types: exists / nonempty / "
                    "json_fields / json_number with min-max / "
                    "json_array with min_length / json_string "
                    "with regex pattern / file_exists for a path "
                    "recorded inside the JSON). Unknown check "
                    "types FAIL closed. Returns {ok, size, "
                    "passed, errors}. MANDATORY in the schedule "
                    "verification flow: call it on every file "
                    "you produced BEFORE claiming success; on a "
                    "failure, report 驗證失敗 + 原因 and never "
                    "conclude the task succeeded."
                ),
                server_name="scheduler-local",
                handler=self._verify_artifact,
            ),
        ]

    def _ensure_mgr(self) -> Any:
        if self._mgr is None:
            from src.agent.scheduler import ScheduleManager
            self._mgr = ScheduleManager()
        return self._mgr

    def _add(self, args: dict) -> dict:
        from src.mcp_server.scheduler_server import _validate_add_args
        err = _validate_add_args(args)
        if err:
            return {"error": err}
        from src.agent.scheduler import Schedule
        kwargs: dict[str, Any] = {
            "prompt": args["prompt"].strip(),
            "schedule_type": args.get(
                "schedule_type", "interval_seconds",
            ),
        }
        name = args.get("name")
        if isinstance(name, str) and name.strip():
            kwargs["name"] = name.strip()
        for k in ("cron", "once_at"):
            if k in args:
                kwargs[k] = args[k]
        # Default interval to 60 s when only prompt is provided —
        # see the matching block in scheduler_server._do_add for
        # why this matches the LLM's mental model.
        if "interval_seconds" in args:
            kwargs["interval_seconds"] = args["interval_seconds"]
        elif kwargs["schedule_type"] == "interval_seconds":
            kwargs["interval_seconds"] = 60
        for k in ("use_tools", "use_web_search"):
            if k in args:
                kwargs[k] = bool(args[k])
        if "web_max_results" in args:
            kwargs["web_max_results"] = int(args["web_max_results"])
        # v0.8.7+: lifecycle caps. ``None`` means "no cap" (same
        # default as the dataclass). Validation: positive integer
        # or None; bad input raises so the LLM gets a clean error
        # rather than a silent ignore.
        if "max_fires" in args and args["max_fires"] is not None:
            try:
                mf = int(args["max_fires"])
            except (TypeError, ValueError):
                return {"error": "'max_fires' must be a positive integer or null."}
            if mf <= 0:
                return {"error": "'max_fires' must be a positive integer or null."}
            kwargs["max_fires"] = mf
        if "expires_at" in args and args["expires_at"] is not None:
            kwargs["expires_at"] = args["expires_at"]
        # v0.9.106.0 (P0 驗證前置化): optional per-schedule opt-out
        # of the fire-time verification directive + machine guard.
        # Default True (Schedule dataclass default) — only an
        # explicit False/true string is coerced.
        if "verify_required" in args and args["verify_required"] is not None:
            kwargs["verify_required"] = bool(args["verify_required"])
        # v0.9.111.0 — optional fire-time model pin (catalog-validated,
        # fail-closed ADD: a bad pin becomes a clean tool error, never a
        # broken schedule). ""/None = the default provider chain.
        if "llm" in args and args["llm"]:
            from src.agent.task_llm import coerce_llm_pin

            pin_val, pin_err = coerce_llm_pin(args["llm"])
            if pin_err:
                return {"error": str(pin_err)}
            kwargs["llm"] = pin_val
        mgr = self._ensure_mgr()
        try:
            s = mgr.save(Schedule(**kwargs))
        except ValueError as e:
            return {"error": f"invalid schedule: {e}"}
        # v0.8.6.3: kick off the daemon so the LLM-built schedule
        # actually fires. Without this hook the daemon thread is
        # only ever started by an explicit ``start_scheduler()``
        # call (GUI button / CLI ``schedules daemon``) — but the
        # whole point of the LLM being able to add_scheduled_task
        # is that the user expects the schedule to run as soon as
        # they confirm it. Best-effort: any failure inside the
        # auto-start callback is swallowed so a misconfigured
        # callback doesn't break the tool's success response.
        if self._auto_start is not None:
            try:
                self._auto_start()
            except Exception as e:
                logger.debug(
                    "[scheduler-local] auto_start callback "
                    "raised: %r",
                    e,
                )
        return {
            "ok": True, "id": s.id, "name": s.name,
            "schedule_type": s.schedule_type,
            "next_fire_at": s.next_fire_at,
            "enabled": s.enabled,
            "status": (
                "armed" if s.enabled and s.next_fire_at else "idle"
            ),
        }

    def _delete(self, args: dict) -> dict:
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return {"error": "'task_id' must be a non-empty string."}
        mgr = self._ensure_mgr()
        sid = _resolve_id_or_name(mgr, ident)
        if sid is None:
            return {
                "error": (
                    f"no schedule found for {ident!r}. Use "
                    "list_scheduled_tasks to see available ids."
                ),
            }
        return {"ok": True, "deleted": mgr.delete(sid), "id": sid}

    def _list(self, args: dict) -> dict:
        from src.mcp_server.scheduler_server import _schedule_to_dict
        enabled_only = bool(args.get("enabled_only", False))
        tasks = []
        mgr = self._ensure_mgr()
        for s in mgr.list():
            if enabled_only and not s.enabled:
                continue
            d = _schedule_to_dict(s)
            tasks.append({
                "id": d["id"],
                "name": d["name"],
                "schedule_type": d["schedule_type"],
                "enabled": d["enabled"],
                "fire_count": d["fire_count"],
                "last_fire_at": d["last_fire_at"],
                "next_fire_at": d["next_fire_at"],
                "status": d["status"],
                "last_error": d["last_error"],
            })
        return {"count": len(tasks), "tasks": tasks}

    def _get(self, args: dict) -> dict:
        from src.mcp_server.scheduler_server import (
            _resolve_id_or_name, _schedule_to_dict,
        )
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return {"error": "'task_id' must be a non-empty string."}
        mgr = self._ensure_mgr()
        sid = _resolve_id_or_name(mgr, ident)
        if sid is None:
            return {"error": f"no schedule found for {ident!r}."}
        s = mgr.get(sid)
        if s is None:
            return {"error": f"schedule {sid!r} disappeared mid-call."}
        return {"ok": True, "task": _schedule_to_dict(s)}

    def _enable(self, args: dict) -> dict:
        return self._set_enabled(args, True)

    def _disable(self, args: dict) -> dict:
        return self._set_enabled(args, False)

    def _set_enabled(self, args: dict, enabled: bool) -> dict:
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return {"error": "'task_id' must be a non-empty string."}
        mgr = self._ensure_mgr()
        sid = _resolve_id_or_name(mgr, ident)
        if sid is None:
            return {"error": f"no schedule found for {ident!r}."}
        s = mgr.set_enabled(sid, enabled)
        if s is None:
            return {"error": f"schedule {sid!r} disappeared mid-call."}
        return {
            "ok": True, "id": sid,
            "enabled": s.enabled, "next_fire_at": s.next_fire_at,
        }

    def _run_now(self, args: dict) -> dict:
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return {"error": "'task_id' must be a non-empty string."}
        mgr = self._ensure_mgr()
        sid = _resolve_id_or_name(mgr, ident)
        if sid is None:
            return {"error": f"no schedule found for {ident!r}."}
        s = mgr.get(sid)
        if s is None:
            return {"error": f"schedule {sid!r} disappeared mid-call."}
        from src.agent.agent import Agent
        from src.agent.scheduler import make_agent_on_fire
        agent = Agent()
        # v0.9.111.0 — the fire factory honours the schedule's llm pin
        # (validate/degrade lives INSIDE make_agent_on_fire._fire).
        on_fire = make_agent_on_fire(
            agent_factory=lambda llm=None: (
                agent if not llm else Agent(task_llm=llm)
            ),
        )
        try:
            on_fire(s)
        except Exception as e:
            return {
                "error": (
                    f"fired {sid} but the prompt raised: "
                    f"{type(e).__name__}: {e}. Use "
                    "get_scheduled_task_errors to inspect the "
                    "history."
                ),
            }
        return {
            "ok": True, "id": sid,
            "session_id": s.last_session_id,
            "fire_count": s.fire_count,
            "last_fire_at": s.last_fire_at,
            "next_fire_at": s.next_fire_at,
        }

    def _errors(self, args: dict) -> dict:
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return {"error": "'task_id' must be a non-empty string."}
        mgr = self._ensure_mgr()
        sid = _resolve_id_or_name(mgr, ident)
        if sid is None:
            return {"error": f"no schedule found for {ident!r}."}
        try:
            limit = int(args.get("limit", 20))
        except (TypeError, ValueError):
            return {"error": "'limit' must be an integer."}
        if limit <= 0 or limit > 100:
            return {"error": "'limit' must be between 1 and 100."}
        since = args.get("since")
        if since is not None and not isinstance(since, str):
            return {"error": "'since' must be an ISO-8601 string."}
        history = mgr.get_history(sid, limit=max(limit * 4, 50))
        errors: list[dict[str, Any]] = []
        for entry in history:
            if not entry.get("error"):
                continue
            if since:
                ts = entry.get("ts") or ""
                if isinstance(ts, str) and ts < since:
                    continue
            errors.append({
                "ts": entry.get("ts"),
                "error": entry.get("error"),
                "prompt": entry.get("prompt"),
                "session_id": entry.get("session_id"),
            })
            if len(errors) >= limit:
                break
        s = mgr.get(sid)
        head: dict[str, Any] | None = None
        if s and s.last_error:
            head = {
                "ts": s.last_error_at,
                "error": s.last_error,
                "prompt": s.prompt,
                "session_id": s.last_session_id,
                "source": "schedule.last_error",
            }
        return {
            "ok": True, "id": sid, "current": head,
            "history_count": len(errors), "errors": errors,
        }


    def _status(self, args: dict) -> dict:
        """v0.8.7+: lifecycle snapshot of a single schedule.

        Returns the full ``ScheduleManager.get_progress`` payload:
        status (active/expired/exhausted/disabled/failed),
        fire_count / max_fires / fires_remaining, last_fire_at /
        next_fire_at / seconds_until_next_fire, expires_at /
        seconds_until_expiry, last_error, and the last 5 runs.
        Accepts either an id or a name via ``task_id``.
        """
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return {"error": "'task_id' must be a non-empty string."}
        mgr = self._ensure_mgr()
        sid = _resolve_id_or_name(mgr, ident)
        if sid is None:
            return {"error": f"no schedule found for {ident!r}."}
        prog = mgr.get_progress(sid)
        if prog is None:
            return {"error": f"schedule {sid!r} disappeared mid-call."}
        return {"ok": True, "id": sid, "progress": prog}

    def _runs(self, args: dict) -> dict:
        """v0.8.7+: list the most recent N fire records of a schedule.

        Each row is the raw history.jsonl line (answer truncated
        to 500 chars). Backs questions like "這個排程上次跑得怎樣"
        / "show me the last 10 runs" without pulling in the full
        session content.
        """
        from src.mcp_server.scheduler_server import _resolve_id_or_name
        ident = args.get("task_id")
        if not isinstance(ident, str) or not ident.strip():
            return {"error": "'task_id' must be a non-empty string."}
        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            return {"error": "'limit' must be an integer."}
        if limit <= 0 or limit > 100:
            return {"error": "'limit' must be between 1 and 100."}
        mgr = self._ensure_mgr()
        sid = _resolve_id_or_name(mgr, ident)
        if sid is None:
            return {"error": f"no schedule found for {ident!r}."}
        runs = mgr.list_runs(sid, limit=limit)
        if runs is None:
            return {"error": f"schedule {sid!r} disappeared mid-call."}
        return {"ok": True, "id": sid, "count": len(runs), "runs": runs}

    # ── v0.9.106.0 — P1 機械式檢查工具 ────────────────────────────

    def _verify_artifact(self, args: dict) -> dict:
        """Mechanical (zero-LLM) artifact verification — the P1
        independent evaluator. Delegates to
        ``src.agent.scheduler.verify_artifact`` (shared with the
        schedule fire verification node)."""
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"error": "'path' must be a non-empty string."}
        checks = args.get("checks")
        if checks is not None and not isinstance(checks, list):
            return {
                "error": (
                    "'checks' must be a list of check objects "
                    '(e.g. [{"type": "min_bytes", "value": 100}, '
                    '{"field": "stage1.status", "type": "exists"}]).'
                )
            }
        from src.agent.scheduler import verify_artifact
        try:
            return verify_artifact(path, checks)
        except Exception as e:  # never raise from a tool handler
            return {"error": f"verify_artifact failed: {e!r}"}



class TodoLocalHost:
    """In-process tools for the multi-level todo list (v0.8.8+).

    Mirrors the public surface of ``src.mcp_server.todo_server``
    but runs in-process - no stdio subprocess, no JSON-RPC
    overhead. The LLM sees the same 10 tool names; the LocalHost
    dispatches them straight to TodoManager.
    """
    SERVER_NAME = "todo-local"

    def __init__(self) -> None:
        self._mgr: Any = None

    def attach_manager(self, mgr_or_root: Any) -> None:
        try:
            from src.core.todo_manager import TodoManager
            if isinstance(mgr_or_root, TodoManager):
                self._mgr = mgr_or_root
            elif isinstance(mgr_or_root, (str, bytes, os.PathLike)):
                self._mgr = TodoManager(root=mgr_or_root)
            else:
                self._mgr = TodoManager()
        except Exception:
            self._mgr = None

    def _ensure_mgr(self) -> Any:
        if self._mgr is None:
            from src.core.todo_manager import TodoManager
            self._mgr = TodoManager()
        return self._mgr

    # ---- tool registry ----

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="create_task",
                description=(
                    "Create a new todo task. Pass title (required). "
                    "parent_id (optional) attaches it as a sub-task "
                    "under another task - OMIT for root tasks. "
                    "description is a longer spec the agent reads "
                    "later. priority is one of high/medium/low "
                    "(default medium). depends_on is a list of "
                    "task_ids that must complete first. Returns "
                    "the new task's id, parent_id, status "
                    "(pending), and order. Use this for any "
                    "multi-step user request that needs planning."
                ),
                server_name=self.SERVER_NAME,
                handler=self._create,
            ),
            LocalTool(
                name="add_subtask",
                description=(
                    "Shortcut for create_task(parent_id=<existing>) "
                    "when the LLM is decomposing an existing task "
                    "into smaller pieces mid-flight. Returns the "
                    "new subtask's id."
                ),
                server_name=self.SERVER_NAME,
                handler=self._add_subtask,
            ),
            LocalTool(
                name="update_task",
                description=(
                    "Mutate fields on an existing task - title, "
                    "description, priority (high/medium/low), "
                    "notes (free-form markdown), parent_id (move "
                    "to a new parent), or depends_on (replace the "
                    "list). Omit any field to leave it unchanged."
                ),
                server_name=self.SERVER_NAME,
                handler=self._update,
            ),
            LocalTool(
                name="delete_task",
                description=(
                    "Remove a task and (by default) every "
                    "descendant recursively. Pass cascade=false "
                    "to keep children as orphans. Use when the "
                    "user says 'remove X', 'delete the planning "
                    "task', 'wipe that branch'."
                ),
                server_name=self.SERVER_NAME,
                handler=self._delete,
            ),
            LocalTool(
                name="list_tasks",
                description=(
                    "List tasks. parent_id=None returns root "
                    "tasks. Pass a task_id to get direct "
                    "children. include_descendants=true returns "
                    "the entire subtree. status filters by one "
                    "of pending/in_progress/completed/skipped/"
                    "failed/blocked."
                ),
                server_name=self.SERVER_NAME,
                handler=self._list,
            ),
            LocalTool(
                name="get_task",
                description=(
                    "Return the full TodoTask payload for a "
                    "single id - all fields including notes, "
                    "result, error, completed_at."
                ),
                server_name=self.SERVER_NAME,
                handler=self._get,
            ),
            LocalTool(
                name="start_task",
                description=(
                    "Mark a task in_progress. Auto-flips it "
                    "from pending (or from blocked if its deps "
                    "just resolved). Use when beginning work."
                ),
                server_name=self.SERVER_NAME,
                handler=self._start,
            ),
            LocalTool(
                name="complete_task",
                description=(
                    "Mark a task completed. Pass a result "
                    "string (free-form summary). If ALL "
                    "siblings become terminal, the parent "
                    "auto-completes too."
                ),
                server_name=self.SERVER_NAME,
                handler=self._complete,
            ),

            LocalTool(
                name="skip_task",
                description=(
                    "Mark a task skipped. Use when the user "
                    "says 'skip X', 'we don't need that "
                    "anymore', 'X is no longer relevant'. "
                    "Reason is appended to notes."
                ),
                server_name=self.SERVER_NAME,
                handler=self._skip,
            ),
            LocalTool(
                name="fail_task",
                description=(
                    "Mark a task failed (terminal state) with "
                    "an error string explaining what went "
                    "wrong. Use when a step truly failed."
                ),
                server_name=self.SERVER_NAME,
                handler=self._fail,
            ),
            LocalTool(
                name="add_task_dependency",
                description=(
                    "Append a single task_id to a task's "
                    "depends_on list. Idempotent."
                ),
                server_name=self.SERVER_NAME,
                handler=self._add_dependency,
            ),
            LocalTool(
                name="reorder_task",
                description=(
                    "Move a task between two siblings. Pass "
                    "task_id + before_id OR after_id. If both "
                    "omitted, the task moves to the bottom of "
                    "its parent."
                ),
                server_name=self.SERVER_NAME,
                handler=self._reorder,
            ),
            LocalTool(
                name="get_todo_progress",
                description=(
                    "Compute aggregate progress. task_id=None "
                    "covers the whole store; pass a task_id "
                    "to scope to one subtree. Returns total "
                    "nodes, by_status counts, completion_pct, "
                    "blocked ids, ready ids, in_progress "
                    "ids, and next_recommended (lowest-order "
                    "ready task)."
                ),
                server_name=self.SERVER_NAME,
                handler=self._progress,
            ),
            LocalTool(
                name="list_task_journal",
                description=(
                    "Read the last N lines of a task's "
                    "append-only audit log (status "
                    "transitions, field updates, dep "
                    "changes). Default limit=20, max 200."
                ),
                server_name=self.SERVER_NAME,
                handler=self._journal,
            ),
            LocalTool(
                name="render_task_tree",
                description=(
                    "Render a subtree as a flat list of "
                    "TodoTask dicts in DFS pre-order, each "
                    "with a 'depth' field (root=0). "
                    "task_id=None renders every root."
                ),
                server_name=self.SERVER_NAME,
                handler=self._tree,
            ),
            LocalTool(
                name="execute_plan",
                description=(
                    "Kick a plan forward. Unblocks any task "
                    "whose dependencies just resolved, then "
                    "auto-completes any parent whose "
                    "children are all terminal. Returns the "
                    "new progress summary."
                ),
                server_name=self.SERVER_NAME,
                handler=self._execute_plan,
            ),
        ]



    # ---- handlers ----

    def _create(self, args: dict) -> dict:
        title = args.get("title")
        if not isinstance(title, str) or not title.strip():
            return {"error": "'title' must be a non-empty string."}
        kwargs: dict[str, Any] = {"title": title.strip()}
        for k in ("parent_id", "description"):
            v = args.get(k)
            if isinstance(v, str) and v.strip():
                kwargs[k] = v.strip()
        if "priority" in args:
            kwargs["priority"] = args["priority"]
        if "depends_on" in args and args["depends_on"] is not None:
            if not isinstance(args["depends_on"], list):
                return {"error": "'depends_on' must be a list of task_ids."}
            kwargs["depends_on"] = args["depends_on"]
        if "order" in args and args["order"] is not None:
            try:
                kwargs["order"] = float(args["order"])
            except (TypeError, ValueError):
                return {"error": "'order' must be a number."}
        try:
            t = self._ensure_mgr().create(**kwargs)
        except ValueError as e:
            return {"error": str(e)}
        return {
            "ok": True, "task_id": t.task_id,
            "parent_id": t.parent_id, "status": t.status,
            "order": t.order, "title": t.title,
            "priority": t.priority,
        }

    def _add_subtask(self, args: dict) -> dict:
        parent_id = args.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id.strip():
            return {"error": "'parent_id' must be a non-empty string."}
        title = args.get("title")
        if not isinstance(title, str) or not title.strip():
            return {"error": "'title' must be a non-empty string."}
        kwargs: dict[str, Any] = {"parent_id": parent_id.strip(),
                                  "title": title.strip()}
        if isinstance(args.get("description"), str):
            kwargs["description"] = args["description"]
        if "priority" in args:
            kwargs["priority"] = args["priority"]
        try:
            t = self._ensure_mgr().add_subtask(**kwargs)
        except ValueError as e:
            return {"error": str(e)}
        if t is None:
            return {"error": "subtask creation returned None."}
        return {
            "ok": True, "task_id": t.task_id,
            "parent_id": t.parent_id, "status": t.status,
            "order": t.order, "title": t.title,
        }

    def _update(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        kwargs: dict[str, Any] = {"task_id": tid.strip()}
        for k in ("title", "description", "notes", "parent_id"):
            if k in args and args[k] is not None:
                kwargs[k] = args[k]
        if "priority" in args and args["priority"] is not None:
            kwargs["priority"] = args["priority"]
        if "order" in args and args["order"] is not None:
            try:
                kwargs["order"] = float(args["order"])
            except (TypeError, ValueError):
                return {"error": "'order' must be a number."}
        if "depends_on" in args and args["depends_on"] is not None:
            if not isinstance(args["depends_on"], list):
                return {"error": "'depends_on' must be a list."}
            kwargs["depends_on"] = args["depends_on"]
        try:
            t = self._ensure_mgr().update(**kwargs)
        except ValueError as e:
            return {"error": str(e)}
        if t is None:
            return {"error": f"task {tid!r} not found."}
        return {"ok": True, "task": t.to_dict()}

    def _delete(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        cascade = bool(args.get("cascade", True))
        ok = self._ensure_mgr().delete(tid.strip(), cascade=cascade)
        return {"ok": ok, "task_id": tid.strip(), "cascade": cascade}

    def _list(self, args: dict) -> dict:
        kwargs: dict[str, Any] = {}
        if "parent_id" in args and args["parent_id"] is not None:
            kwargs["parent_id"] = args["parent_id"]
        if "status" in args and args["status"] is not None:
            kwargs["status"] = args["status"]
        if "include_descendants" in args:
            kwargs["include_descendants"] = bool(args["include_descendants"])
        items = self._ensure_mgr().list_tasks(**kwargs)
        return {"ok": True, "count": len(items), "tasks": items}

    def _get(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        t = self._ensure_mgr().get(tid.strip())
        if t is None:
            return {"error": f"task {tid!r} not found."}
        return {"ok": True, "task": t.to_dict()}



    def _start(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        t = self._ensure_mgr().start(tid.strip())
        if t is None:
            return {"error": f"task {tid!r} not found."}
        return {"ok": True, "task": t.to_dict()}

    def _complete(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        result = args.get("result", "") or ""
        t = self._ensure_mgr().complete(tid.strip(), result=result)
        if t is None:
            return {"error": f"task {tid!r} not found."}
        return {"ok": True, "task": t.to_dict()}

    def _skip(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        reason = args.get("reason", "") or ""
        t = self._ensure_mgr().skip(tid.strip(), reason=reason)
        if t is None:
            return {"error": f"task {tid!r} not found."}
        return {"ok": True, "task": t.to_dict()}

    def _fail(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        error = args.get("error", "") or ""
        t = self._ensure_mgr().fail(tid.strip(), error=error)
        if t is None:
            return {"error": f"task {tid!r} not found."}
        return {"ok": True, "task": t.to_dict()}

    def _add_dependency(self, args: dict) -> dict:
        tid = args.get("task_id")
        dep = args.get("depends_on")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        if not isinstance(dep, str) or not dep.strip():
            return {"error": "'depends_on' must be a non-empty task_id string."}
        try:
            t = self._ensure_mgr().add_dependency(tid.strip(), dep.strip())
        except ValueError as e:
            return {"error": str(e)}
        if t is None:
            return {"error": f"task {tid!r} not found."}
        return {"ok": True, "task": t.to_dict()}

    def _reorder(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        kwargs: dict[str, Any] = {}
        for k in ("before_id", "after_id"):
            if k in args and args[k] is not None:
                if not isinstance(args[k], str) or not args[k].strip():
                    return {"error": f"'{k}' must be a non-empty task_id string."}
                kwargs[k] = args[k].strip()
        try:
            t = self._ensure_mgr().reorder(tid.strip(), **kwargs)
        except ValueError as e:
            return {"error": str(e)}
        if t is None:
            return {"error": f"task {tid!r} not found."}
        return {"ok": True, "task": t.to_dict()}

    def _progress(self, args: dict) -> dict:
        tid = args.get("task_id")
        if tid is not None and not isinstance(tid, str):
            return {"error": "'task_id' must be a string or null."}
        summary = self._ensure_mgr().get_progress_summary(task_id=tid)
        return {"ok": True, "progress": summary}

    def _journal(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        try:
            limit = int(args.get("limit", 20))
        except (TypeError, ValueError):
            return {"error": "'limit' must be an integer."}
        if limit <= 0 or limit > 200:
            return {"error": "'limit' must be between 1 and 200."}
        entries = self._ensure_mgr().list_journal(tid.strip(), limit=limit)
        return {
            "ok": True, "task_id": tid.strip(),
            "count": len(entries), "entries": entries,
        }

    def _tree(self, args: dict) -> dict:
        tid = args.get("task_id")
        if tid is not None and not isinstance(tid, str):
            return {"error": "'task_id' must be a string or null."}
        items = self._ensure_mgr().render_tree(root_id=tid)
        return {"ok": True, "count": len(items), "tree": items}

    def _execute_plan(self, args: dict) -> dict:
        tid = args.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            return {"error": "'task_id' must be a non-empty string."}
        summary = self._ensure_mgr().execute_plan(tid.strip())
        return {"ok": True, "task_id": tid.strip(), "progress": summary}


# ── v0.9.6.0+: open-with-associated-application ─────────────


# Extensions always routed to the in-GUI Drawio Viewer window
# (when a diagrams opener callback is wired by ``gui/api.py``)
# instead of the OS file association. A bare ``.xml`` is *sniffed*
# for drawio shapes (``<mxfile`` / ``<mxGraphModel``) and routed
# when it matches.
_OPENWITH_DIAGRAM_EXTS: frozenset[str] = frozenset({".drawio", ".dio"})

# Content cap for ``open_content`` — 4 MB, mirrors the filesystem
# read cap and the diagrams viewer's XML cap.
_OPENWITH_MAX_CONTENT_BYTES = 4 * 1024 * 1024


class OpenWithLocalHost:
    """Windows-Explorer-style 'open with the associated application'
    for the artifacts a chat LLM produces (v0.9.6.0+).

    Three tools:

    * ``open_file(path)`` — open a file that already lives inside
      the filesystem write sandbox (``QDRANT_FILESYSTEM_WRITE_ROOT``,
      the same root ``FilesystemLocalHost`` writes to). The LLM
      typically calls ``write_file`` first, then ``open_file``.
    * ``open_content(name?, content)`` — save content the LLM just
      generated (drawio XML, markdown, csv, plain text, ...) into
      the sandbox and open it in one step. Content that sniffs as
      drawio (``<mxfile`` / ``<mxGraphModel`` / ``<mxCell``) is
      force-routed to a ``.drawio`` file and, when the GUI is
      running, popped open in the in-GUI Drawio Viewer window.
    * ``list_openable_types()`` — introspection: what can be
      opened, how, and what is refused.

    Routing (``_route_open``):

    1. Dangerous executables (``_FS_DANGEROUS_WRITE_EXTS`` —
       ``.exe`` / ``.bat`` / ``.sh`` / ...) are refused outright.
       This tool *opens* files with *other* applications; it must
       never become an LLM-driven launcher.
    2. ``.drawio`` / ``.dio`` (or a drawio-shaped ``.xml``) is
       handed to the ``diagrams opener`` callback when one is
       wired — ``gui/api.py`` pushes
       ``DiagramsWindowManager.open_with_path`` here so GUI users
       get the in-app viewer window. If the callback fails, we
       fall back to the OS association.
    3. Everything else goes through the OS file association —
       ``os.startfile`` on Windows, ``open`` on macOS, ``xdg-open``
       on Linux — exactly the Explorer double-click behaviour.

    GUI wiring mirrors the scheduler auto-start pattern:
    ``gui/api.py`` calls ``McpManager.set_diagrams_opener`` which
    pushes ``attach_diagrams_opener`` down here. Without the
    callback (CLI / stdio server), step 2 falls through to step 3
    gracefully.
    """

    # Drawio-shape sniffing (first 8 KB only — enough for every
    # real mxfile/mxGraphModel header we have seen in the wild,
    # cheap even for 4 MB payloads).
    _DRAWIO_SHAPE_RE = re.compile(
        r"<\s*(?:mxfile|mxGraphModel|mxCell)\b", re.IGNORECASE,
    )

    def __init__(self) -> None:
        self._write_root: Path | None = None
        self._diagrams_opener: Callable[[str], dict] | None = None

    # ── wiring ────────────────────────────────────────────────
    def attach_write_root(self, root: Any) -> None:
        """Pin the sandbox root explicitly (tests / stdio server).

        Accepts a ``Path`` or a string; anything else is ignored
        and the lazy env-based resolution stays in charge."""
        try:
            p = Path(root).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            self._write_root = p
        except Exception:
            self._write_root = None

    def attach_diagrams_opener(
        self, callback: Callable[[str], dict] | None,
    ) -> None:
        """Register the GUI-side drawio viewer opener.

        ``callback(path)`` must return a dict (``{ok: True, ...}``
        or ``{error: ...}``). Wired by ``gui/api.py`` via
        ``McpManager.set_diagrams_opener`` so ``open_file`` /
        ``open_content`` can pop the in-app Drawio Viewer window
        for ``.drawio`` artifacts instead of shelling out to the
        OS association.
        """
        self._diagrams_opener = callback

    # ── tool surface ──────────────────────────────────────────
    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="open_file",
                description=(
                    "Open a file from the filesystem write sandbox "
                    "with its associated application — Windows "
                    "Explorer double-click semantics. '.drawio' / "
                    "'.dio' files (and drawio-shaped '.xml') are "
                    "routed to the in-GUI Drawio Viewer window when "
                    "one is available, otherwise the OS file "
                    "association handles them. Executables (.exe / "
                    ".bat / ...) are refused. Use this AFTER "
                    "write_file when the user says 'open it', "
                    "'幫我開啟', 'show me the file'."
                ),
                server_name="openwith-local",
                handler=self._open_file,
            ),
            LocalTool(
                name="open_content",
                description=(
                    "Save content you just generated (drawio XML, "
                    "markdown, csv, plain text) into the filesystem "
                    "write sandbox AND open it with the associated "
                    "application in one step. Content that sniffs as "
                    "drawio (<mxfile>/<mxGraphModel>) is force-routed "
                    "to a .drawio file + the Drawio Viewer window. "
                    "Provide 'name' (e.g. 'circle.drawio', "
                    "'notes.md'); without one a timestamped default "
                    "is used."
                ),
                server_name="openwith-local",
                handler=self._open_content,
            ),
            LocalTool(
                name="list_openable_types",
                description=(
                    "Introspection for the open-with feature: which "
                    "extensions are routed to the Drawio Viewer, "
                    "which open via the OS file association, which "
                    "are refused, and where the sandbox root is."
                ),
                server_name="openwith-local",
                handler=self._list_openable,
            ),
        ]

    # ── internals ─────────────────────────────────────────────
    def _ensure_write_root(self) -> Path:
        if self._write_root is None:
            self._write_root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT",
            )
        return self._write_root

    def _sniff_drawio_file(self, target: Path) -> bool:
        """True iff the first 8 KB of ``target`` look like drawio
        XML (``<mxfile`` / ``<mxGraphModel`` / ``<mxCell``)."""
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
        return bool(self._DRAWIO_SHAPE_RE.search(head))

    @staticmethod
    def _open_via_os(target: Path) -> str:
        """Open ``target`` with the OS file association. Returns a
        short handler label for the tool result. Raises ``OSError``
        (Windows) / ``Exception`` (no xdg-open) on failure."""
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
            return "os.startfile (Windows file association)"
        import subprocess as _sp
        import sys as _sys
        if _sys.platform == "darwin":
            _sp.Popen(["open", str(target)])
            return "open (macOS file association)"
        _sp.Popen(["xdg-open", str(target)])
        return "xdg-open (Linux file association)"

    def _route_open(self, target: Path) -> dict:
        """Route one resolved, existing file to the right opener.

        Order: dangerous-ext refusal → drawio viewer callback
        (with OS-association fallback) → OS file association.
        Never raises — returns ``{error: ...}`` on failure so the
        MCP envelope stays clean for the LLM.
        """
        ext = target.suffix.lower()
        if ext in _FS_DANGEROUS_WRITE_EXTS:
            return {
                "error": (
                    f"refusing to open {ext!r} — executables are "
                    "never opened by this tool."
                ),
            }
        is_drawio = ext in _OPENWITH_DIAGRAM_EXTS
        if not is_drawio and ext == ".xml":
            try:
                is_drawio = self._sniff_drawio_file(target)
            except Exception:
                is_drawio = False
        if is_drawio and self._diagrams_opener is not None:
            try:
                r = self._diagrams_opener(target.as_posix())
            except Exception as e:
                r = {"error": f"drawio viewer callback raised: {e!r}"}
            if isinstance(r, dict) and not r.get("error"):
                return {
                    "ok": True,
                    "path": target.as_posix(),
                    "opened_with": "drawio-viewer-window",
                }
            fallback_reason = (
                str(r.get("error"))[:160]
                if isinstance(r, dict) else "viewer callback"
            )
            try:
                opened = self._open_via_os(target)
            except Exception as e:
                return {
                    "error": (
                        "drawio viewer failed "
                        f"({fallback_reason}) and the OS file "
                        f"association also failed: {e!r}"
                    ),
                }
            return {
                "ok": True,
                "path": target.as_posix(),
                "opened_with": opened,
                "note": (
                    "in-GUI Drawio Viewer was unavailable; opened "
                    "with the OS association instead "
                    f"({fallback_reason})"
                ),
            }
        try:
            opened = self._open_via_os(target)
        except Exception as e:
            return {
                "error": (
                    f"cannot open {target.name!r} via the OS file "
                    f"association (no handler installed?): {e!r}"
                ),
            }
        return {
            "ok": True,
            "path": target.as_posix(),
            "opened_with": opened,
        }

    # ── handlers (LocalTool convention: (self, args: dict)) ───
    def _open_file(self, args: dict) -> dict:
        raw = args.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return {"error": "'path' is required."}
        try:
            root = self._ensure_write_root()
            target = _fs_resolve_path(
                raw.strip(), root,
                allow_relative=True, must_exist=True, must_be_file=True,
            )
            return self._route_open(target)
        except Exception as e:
            return {"error": f"open_file failed: {e!r}"}

    def _open_content(self, args: dict) -> dict:
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            return {"error": "'content' must be a non-empty string."}
        if (
            len(content.encode("utf-8", errors="replace"))
            > _OPENWITH_MAX_CONTENT_BYTES
        ):
            return {
                "error": (
                    f"content exceeds the {_OPENWITH_MAX_CONTENT_BYTES} "
                    "byte cap; split it or write it in chunks via "
                    "write_file first."
                ),
            }
        try:
            from datetime import datetime as _dt
            stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
            name = args.get("name")
            if isinstance(name, str) and name.strip():
                leaf = _fs_safe_basename(name.strip(), "name")
            else:
                leaf = ""
            is_drawio = bool(
                self._DRAWIO_SHAPE_RE.search(content[:8192])
            )
            if not leaf:
                leaf = (
                    f"diagram-{stamp}.drawio" if is_drawio
                    else f"artifact-{stamp}.txt"
                )
            base, ext = os.path.splitext(leaf)
            if not ext:
                ext = ".drawio" if is_drawio else ".txt"
                leaf = base + ext
            if ext in _FS_DANGEROUS_WRITE_EXTS:
                return {
                    "error": (
                        "refusing to write executable target "
                        f"{leaf!r} — executables are never written "
                        "or opened by this tool."
                    ),
                }
            if ext == ".xml" and is_drawio:
                # Drawio XML in a .xml file — route it to the
                # dedicated viewer extension so the GUI callback
                # picks it up without sniffing ambiguity.
                leaf = base + ".drawio"
            root = self._ensure_write_root()
            root.mkdir(parents=True, exist_ok=True)
            target = (root / leaf).resolve()
            if not _is_within(target, root):
                # Belt-and-suspenders: leaf is a basename already,
                # but defence in depth (same as the fs host).
                return {
                    "error": (
                        f"resolved path {target.as_posix()!r} is "
                        "outside the filesystem sandbox root."
                    ),
                }
            # Atomic write — same shape as the diagrams viewer's
            # save_to_sandbox (tempfile.mkstemp + os.replace).
            fd, tmp = tempfile.mkstemp(
                dir=str(root), prefix=".openwith-", suffix=".tmp",
            )
            try:
                with os.fdopen(
                    fd, "w", encoding="utf-8", newline="",
                ) as fh:
                    fh.write(content)
                os.replace(tmp, target)
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            out = self._route_open(target)
            if isinstance(out, dict) and out.get("ok"):
                out["written"] = True
                out["bytes"] = len(
                    content.encode("utf-8", errors="replace"),
                )
            return out
        except Exception as e:
            return {"error": f"open_content failed: {e!r}"}

    def _list_openable(self, args: dict) -> dict:
        try:
            root = self._ensure_write_root()
        except Exception as e:
            return {"error": f"cannot resolve sandbox root: {e!r}"}
        return {
            "ok": True,
            "write_root": root.as_posix(),
            "drawio_viewer_exts": sorted(_OPENWITH_DIAGRAM_EXTS),
            "drawio_sniffed": [
                ".xml containing <mxfile> / <mxGraphModel> / <mxCell>",
            ],
            "os_associated": (
                "everything else with a normal extension (.md .txt "
                ".csv .html .pdf .docx .png ...) opens via the OS "
                "file association — Windows Explorer double-click "
                "semantics (os.startfile / open / xdg-open)"
            ),
            "refused_exts": sorted(_FS_DANGEROUS_WRITE_EXTS),
            "max_content_bytes": _OPENWITH_MAX_CONTENT_BYTES,
            "drawio_viewer_wired": self._diagrams_opener is not None,
        }


# v0.9.7+: log anomaly analysis host. Parses access / syslog files,
# embeds one point per log line into the dedicated
# ``QDRANT_LOG_COLLECTION`` collection, and runs the rule engine +
# semantic outlier detection from ``src.log_analyzer``. Read sandbox
# = ``QDRANT_LOG_ALLOWED_ROOT`` (fallback chain: filesystem read
# root → WATCH_DIR → ./documents) so the LLM can't be steered into
# reading arbitrary machine files.
_LOG_DEFAULT_MAX_LINES = 20000
_LOG_INGEST_BATCH = 128


class LogAnalysisLocalHost:
    """Qdrant-backed access-log / syslog anomaly analysis (v0.9.7+).

    Tool surface (all handlers take a single positional ``args`` dict,
    the v0.8.6.1 convention): ``ingest_log`` / ``analyze_anomalies`` /
    ``search_logs`` / ``get_log_stats`` / ``list_log_sources`` /
    ``remove_logs``.
    """

    def __init__(self):
        # Lazy: defer Qdrant connect + Embedder construction to the
        # first tool call so host registration never pays a network
        # round-trip (mirrors QdrantLocalHost).
        self._store = None
        self._embedder = None

    # ── tool surface ──────────────────────────────────────────
    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="ingest_log",
                description=(
                    "Parse and index a web-server access log "
                    "(Apache/Nginx combined) or system log (syslog "
                    "RFC3164/RFC5424) into the log analysis "
                    "collection — one vector per line. Paths are "
                    "sandboxed to QDRANT_LOG_ALLOWED_ROOT. "
                    "Compressed archives are accepted directly: "
                    ".zip / .7z / .tar / .tar.gz / .tgz / .tar.bz2 / "
                    ".gz are extracted to a temp dir and every "
                    "parsable member is ingested as its own source "
                    "'<archive>!<member>' (Zip-Slip guard, "
                    "dangerous-executable skipping and zip-bomb "
                    "budgets enforced). Returns {ok, log_type, "
                    "ingested, skipped, source} for plain files, or "
                    "{ok, source_archive, files: [...], "
                    "total_ingested} for archives, or {error}."
                ),
                server_name="log-analyzer-local",
                handler=self._ingest_log,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Log file or archive (.zip / .7z / "
                                ".tar / .tar.gz / .tgz / .tar.bz2 / "
                                ".gz) path inside the log sandbox."
                            ),
                        },
                        "log_type": {
                            "type": "string",
                            "enum": ["auto", "access", "syslog"],
                            "description": (
                                "Force a parser, or 'auto' to "
                                "majority-detect per file over its "
                                "first 50 lines. Default auto."
                            ),
                        },
                        "members": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Archives only: optional member-name "
                                "allowlist (extract/ingest only these "
                                "members)."
                            ),
                        },
                        "max_lines": {
                            "type": "integer",
                            "description": (
                                "Safety cap on ingested lines PER "
                                "FILE (default 20000)."
                            ),
                        },
                        "batch_size": {
                            "type": "integer",
                            "description": (
                                "Embedding batch size (default 128)."
                            ),
                        },
                    },
                    "required": ["path"],
                },
            ),
            LocalTool(
                name="analyze_anomalies",
                description=(
                    "Run anomaly analysis over ingested log lines: "
                    "rule-based statistics (HTTP error rate, 404 "
                    "scanning, high-frequency IPs, suspicious paths "
                    "and user-agents, 401/403 bursts, SSH brute "
                    "force, OOM/kernel events, log floods) PLUS "
                    "semantic outliers (lines whose embedding is "
                    "isolated from every other line). Returns "
                    "structured findings with severity + evidence — "
                    "interpret them for the user, never dump raw "
                    "JSON."
                ),
                server_name="log-analyzer-local",
                handler=self._analyze,
                input_schema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": (
                                "Restrict to one ingested file "
                                "(source path from list_log_sources)."
                            ),
                        },
                        "log_type": {
                            "type": "string",
                            "enum": ["access", "syslog"],
                            "description": (
                                "Restrict to one log type. Default: "
                                "analyse whatever is stored."
                            ),
                        },
                        "top_n": {
                            "type": "integer",
                            "description": (
                                "Max evidence rows / outliers per "
                                "finding (default 10)."
                            ),
                        },
                    },
                },
            ),
            LocalTool(
                name="search_logs",
                description=(
                    "Semantic search over ingested log lines. Use "
                    "for questions like '找出所有 SQL injection 嘗試' "
                    "or 'who tried to log in as root'. Optional "
                    "filters: source / log_type / level (error, "
                    "warning...) / min_status (e.g. 400)."
                ),
                server_name="log-analyzer-local",
                handler=self._search,
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "source": {"type": "string"},
                        "log_type": {
                            "type": "string",
                            "enum": ["access", "syslog"],
                        },
                        "level": {"type": "string"},
                        "min_status": {"type": "integer"},
                        "top_k": {"type": "integer"},
                        "min_score": {"type": "number"},
                    },
                    "required": ["query"],
                },
            ),
            LocalTool(
                name="get_log_stats",
                description=(
                    "Aggregate statistics over ingested logs: point "
                    "counts, log_type / level / status-code "
                    "distributions, top source IPs and paths, time "
                    "range. Pass source to scope to one file."
                ),
                server_name="log-analyzer-local",
                handler=self._stats,
                input_schema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                    },
                },
            ),
            LocalTool(
                name="list_log_sources",
                description=(
                    "List the log files already ingested into the "
                    "log collection with their point counts. Call "
                    "this before ingest_log to check whether the "
                    "file is already indexed."
                ),
                server_name="log-analyzer-local",
                handler=self._list_sources,
                input_schema={"type": "object", "properties": {}},
            ),
            LocalTool(
                name="remove_logs",
                description=(
                    "Remove ALL points ingested from one log file "
                    "(by source path). DESTRUCTIVE — confirm with "
                    "the user before calling. Returns {removed} or "
                    "{error}."
                ),
                server_name="log-analyzer-local",
                handler=self._remove,
                input_schema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": (
                                "Source path (from list_log_sources)."
                            ),
                        },
                    },
                    "required": ["source"],
                },
            ),
        ]

    # ── lazy wiring ───────────────────────────────────────────
    def _ensure(self) -> tuple[Any, Any]:
        """Lazy-build LogStore + Embedder on first tool call."""
        if self._store is None:
            from src.log_analyzer.store import LogStore

            self._store = LogStore()
        if self._embedder is None:
            from src.ingest.embedder import Embedder

            self._embedder = Embedder()
        return self._store, self._embedder

    def _resolve_log_root(self) -> Path:
        """Log read sandbox root.

        Priority: ``QDRANT_LOG_ALLOWED_ROOT`` →
        ``QDRANT_FILESYSTEM_READ_ROOT`` → ``WATCH_DIR`` →
        ``./documents``. Delegates to ``_resolve_filesystem_root``
        for the anchoring / mkdir behaviour so all three roots stay
        consistent across CLI / stdio / GUI launch cwds.
        """
        for env in ("QDRANT_LOG_ALLOWED_ROOT", "QDRANT_FILESYSTEM_READ_ROOT"):
            if os.environ.get(env):
                return _resolve_filesystem_root(env)
        return _resolve_filesystem_root("WATCH_DIR")

    def _resolve_log_path(self, raw_path: str) -> Path:
        """Resolve + sandbox-check a log file path from the LLM.

        Relative paths anchor at the log root; absolute paths must
        live inside it (``..`` / symlink traversal rejected via
        ``_is_within``).
        """
        root = self._resolve_log_root()
        p = Path(str(raw_path)).expanduser()
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if not _is_within(p, root):
            raise ValueError(
                f"path {p.as_posix()} is outside the log sandbox "
                f"{root.as_posix()} (set QDRANT_LOG_ALLOWED_ROOT to "
                "widen it)"
            )
        if not p.is_file():
            raise FileNotFoundError(f"no such log file: {p.as_posix()}")
        return p

    # ── ingest ────────────────────────────────────────────────
    def _embed_and_upsert(
        self,
        store: Any,
        embedder: Any,
        entries: list[dict],
        source: str,
        batch_size: int,
    ) -> int:
        """Batched embed + upsert shared by plain-file and archive
        ingestion paths. Returns the number of points written."""
        vectors: list[list[float]] = []
        for start in range(0, len(entries), batch_size):
            chunk = entries[start:start + batch_size]
            vectors.extend(
                embedder.embed_batch([e["text"] for e in chunk]),
            )
        return store.upsert_entries(
            entries, source=source, vectors=vectors, batch_size=256,
        )

    def _ingest_log(self, args: dict) -> dict:
        args = args or {}
        path_raw = args.get("path")
        if not path_raw:
            return {"error": "'path' is required."}
        log_type = args.get("log_type") or "auto"
        try:
            max_lines = int(args.get("max_lines") or _LOG_DEFAULT_MAX_LINES)
        except (TypeError, ValueError):
            max_lines = _LOG_DEFAULT_MAX_LINES
        try:
            batch_size = int(args.get("batch_size") or _LOG_INGEST_BATCH)
        except (TypeError, ValueError):
            batch_size = _LOG_INGEST_BATCH
        batch_size = max(1, min(batch_size, 512))
        members = args.get("members")
        if members is not None and not isinstance(members, list):
            members = None

        try:
            path = self._resolve_log_path(path_raw)
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}

        # v0.9.8.1: archives (.zip / .7z / .tar / .tar.gz / .tgz /
        # .tar.bz2 / .gz) are extracted to a throwaway temp dir and
        # every parsable member becomes its own source
        # ("<archive>!<member>"); plain text files take the
        # original single-file path.
        from src.archive_tools import sniff_archive_format

        fmt = sniff_archive_format(path)
        if fmt:
            return self._ingest_log_archive(
                path, log_type, max_lines, batch_size, fmt, members,
            )
        return self._ingest_log_file(
            path, log_type, max_lines, batch_size,
        )

    def _ingest_log_file(
        self,
        path: Path,
        log_type: str,
        max_lines: int,
        batch_size: int,
        source_override: str | None = None,
    ) -> dict:
        """Ingest one plain-text log file (original single-file path)."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"error": f"cannot read log file: {e!r}"}

        lines = text.splitlines()
        if not lines:
            return {"error": "log file is empty."}

        from src.log_analyzer.parser import parse_log_lines

        parsed = parse_log_lines(
            lines, log_type=log_type, max_lines=max_lines,
        )
        if parsed["log_type"] == "unknown":
            return {
                "error": (
                    "could not detect log format (neither combined "
                    "access log nor syslog). Re-call with "
                    "log_type='access' or 'syslog' to force a parser."
                ),
                "skipped": parsed["skipped"],
            }
        if not parsed["entries"]:
            return {
                "error": "no parsable log lines found.",
                "log_type": parsed["log_type"],
                "skipped": parsed["skipped"],
            }

        store, embedder = self._ensure()
        source = source_override or path.resolve().as_posix()

        try:
            n = self._embed_and_upsert(
                store, embedder, parsed["entries"], source, batch_size,
            )
        except Exception as e:
            logger.warning("[log-analyzer] upsert failed: %r", e)
            return {"error": f"Qdrant upsert failed: {e!r}"}

        out = {
            "ok": True,
            "source": source,
            "log_type": parsed["log_type"],
            "ingested": n,
            "skipped": parsed["skipped"],
            "truncated": parsed["truncated"],
            "collection": store.collection,
        }
        if parsed["truncated"]:
            out["note"] = (
                f"file had more than max_lines={max_lines} lines; "
                "raise max_lines to ingest the rest."
            )
        return out

    def _ingest_log_archive(
        self,
        arch: Path,
        log_type: str,
        max_lines: int,
        batch_size: int,
        fmt: str,
        members: list[str] | None,
    ) -> dict:
        """Ingest every parsable log member of a compressed archive.

        Extraction happens into a throwaway temp dir (never the
        user-visible write sandbox); ``extract_archive`` provides the
        Zip-Slip guard, dangerous-extension skipping and zip-bomb
        budgets. Each member is parsed independently — files whose
        lines match neither access nor syslog format are skipped
        with a reason instead of failing the whole archive — and
        each gets its own Qdrant source id ``<archive>!<member>`` so
        per-member filters still work in analyze / search.
        """
        import tempfile

        from src.archive_tools import extract_archive
        from src.log_analyzer.parser import parse_log_lines

        store, embedder = self._ensure()
        archive_posix = arch.resolve().as_posix()
        max_bytes, max_files = _archive_budget()

        files_parsed: list[dict] = []
        files_skipped: list[dict] = []
        total_ingested = 0
        total_skipped = 0
        type_counts: dict[str, int] = {}

        try:
            with tempfile.TemporaryDirectory(
                prefix="qdrant-logarch-",
            ) as td:
                dest = Path(td)
                try:
                    extract_archive(
                        arch,
                        dest,
                        fmt=fmt,
                        max_bytes=max_bytes,
                        max_files=max_files,
                        skip_dangerous=True,
                        overwrite=True,
                        members=members,
                    )
                except Exception as e:
                    return {"error": f"archive extraction failed: {e!r}"}

                member_files = sorted(
                    p for p in dest.rglob("*") if p.is_file()
                )
                for member_file in member_files:
                    rel = member_file.relative_to(dest).as_posix()
                    member_source = f"{archive_posix}!{rel}"
                    try:
                        text = member_file.read_text(
                            encoding="utf-8", errors="replace",
                        )
                    except OSError as e:
                        files_skipped.append({
                            "member": rel,
                            "reason": f"read failed: {e!r}",
                        })
                        continue
                    lines = text.splitlines()
                    if not lines:
                        files_skipped.append({
                            "member": rel, "reason": "empty file",
                        })
                        continue

                    parsed = parse_log_lines(
                        lines, log_type=log_type, max_lines=max_lines,
                    )
                    if (
                        parsed["log_type"] == "unknown"
                        or not parsed["entries"]
                    ):
                        files_skipped.append({
                            "member": rel,
                            "reason": "no parsable log lines",
                        })
                        total_skipped += parsed["skipped"]
                        continue

                    try:
                        n = self._embed_and_upsert(
                            store,
                            embedder,
                            parsed["entries"],
                            member_source,
                            batch_size,
                        )
                    except Exception as e:
                        logger.warning(
                            "[log-analyzer] upsert failed: %r", e,
                        )
                        return {"error": f"Qdrant upsert failed: {e!r}"}

                    total_ingested += n
                    total_skipped += parsed["skipped"]
                    lt = parsed["log_type"]
                    type_counts[lt] = type_counts.get(lt, 0) + 1
                    files_parsed.append({
                        "member": rel,
                        "log_type": lt,
                        "ingested": n,
                        "skipped": parsed["skipped"],
                        "truncated": parsed["truncated"],
                        "source_id": member_source,
                    })
        except OSError as e:
            return {"error": f"temp extraction dir failed: {e!r}"}

        if not files_parsed and not files_skipped:
            return {
                "error": (
                    "archive contained no file members (or everything "
                    "was filtered by the members allowlist)."
                ),
            }
        if not files_parsed:
            return {
                "error": (
                    "no archive member could be parsed as an access "
                    "log or syslog. Re-call with log_type='access' / "
                    "'syslog' to force a parser, or check members."
                ),
                "files_skipped": files_skipped,
            }

        out = {
            "ok": True,
            "source_archive": archive_posix,
            "archive_format": fmt,
            "log_types": type_counts,
            "files": files_parsed,
            "total_ingested": total_ingested,
            "total_skipped": total_skipped,
            "collection": store.collection,
        }
        if files_skipped:
            out["files_skipped"] = files_skipped
        return out

    # ── analysis ──────────────────────────────────────────────
    def _analyze(self, args: dict) -> dict:
        # Import the rule engine FIRST — this makes the tunables
        # function-locals available before the env reads below, and
        # keeps numpy paid only when analysis actually runs.
        from src.log_analyzer.anomalies import (
            DEFAULT_MAX_POINTS,
            DEFAULT_OUTLIER_THRESHOLD,
            analyze_access_entries,
            analyze_syslog_entries,
            semantic_outliers,
        )

        args = args or {}
        source = args.get("source") or None
        log_type = args.get("log_type") or None
        try:
            top_n = max(1, min(int(args.get("top_n") or 10), 50))
        except (TypeError, ValueError):
            top_n = 10
        try:
            max_points = int(
                os.environ.get("LOG_ANOMALY_MAX_POINTS", DEFAULT_MAX_POINTS),
            )
        except ValueError:
            max_points = DEFAULT_MAX_POINTS
        try:
            threshold = float(
                os.environ.get(
                    "LOG_ANOMALY_OUTLIER_THRESHOLD",
                    str(DEFAULT_OUTLIER_THRESHOLD),
                ),
            )
        except ValueError:
            threshold = DEFAULT_OUTLIER_THRESHOLD

        store, _ = self._ensure()

        try:
            rows = store.scroll_points(
                source=source,
                log_type=log_type,
                limit=max_points,
                with_vectors=True,
            )
        except Exception as e:
            logger.warning("[log-analyzer] scroll failed: %r", e)
            return {"error": f"Qdrant scroll failed: {e!r}"}

        if not rows:
            return {
                "error": (
                    "no log points found for this scope. Call "
                    "list_log_sources / ingest_log first."
                ),
            }

        by_type: dict[str, list[dict]] = {}
        vectors: list[list[float]] = []
        for row in rows:
            vec = row.pop("_vector", None)
            if vec is not None:
                vectors.append(vec)
            by_type.setdefault(row.get("log_type") or "unknown", []).append(row)

        findings: list[dict] = []
        if "access" in by_type:
            findings.extend(
                analyze_access_entries(by_type["access"], top_n=top_n),
            )
        if "syslog" in by_type:
            findings.extend(
                analyze_syslog_entries(by_type["syslog"], top_n=top_n),
            )

        outliers: list[dict] = []
        if len(vectors) >= 3:
            outliers = semantic_outliers(
                vectors, rows, threshold=threshold, top_n=top_n,
            )

        total = len(rows)
        return {
            "ok": True,
            "scanned": total,
            "truncated_to_max_points": total >= max_points,
            "log_types": {
                t: len(es) for t, es in sorted(by_type.items())
            },
            "findings": findings,
            "semantic_outliers": outliers,
            "outlier_threshold": threshold,
        }

    # ── search / stats ────────────────────────────────────────
    def _search(self, args: dict) -> dict:
        args = args or {}
        query = args.get("query")
        if not query:
            return {"error": "'query' is required."}
        try:
            top_k = max(1, min(int(args.get("top_k") or 10), 50))
        except (TypeError, ValueError):
            top_k = 10
        try:
            min_score = float(args.get("min_score") or 0.2)
        except (TypeError, ValueError):
            min_score = 0.2
        min_status = None
        if args.get("min_status") is not None:
            try:
                min_status = int(args["min_status"])
            except (TypeError, ValueError):
                min_status = None

        store, embedder = self._ensure()
        try:
            qvec = embedder.embed(query)
        except Exception as e:
            return {"error": f"embedding failed: {e!r}"}
        try:
            hits = store.search(
                qvec,
                top_k=top_k,
                source=args.get("source") or None,
                log_type=args.get("log_type") or None,
                level=args.get("level") or None,
                min_status=min_status,
                score_threshold=min_score,
            )
        except Exception as e:
            logger.warning("[log-analyzer] search failed: %r", e)
            return {"error": f"Qdrant search failed: {e!r}"}

        return {
            "ok": True,
            "query": query,
            "count": len(hits),
            "hits": [
                {
                    "score": round(h.get("score", 0.0), 4),
                    "raw": (h.get("raw") or h.get("text") or "")[:300],
                    "src_ip": h.get("src_ip", ""),
                    "ts": h.get("ts", ""),
                    "level": h.get("level", ""),
                    "status": h.get("status", 0),
                    "path": h.get("path", ""),
                    "user_agent": h.get("user_agent", ""),
                    "source": h.get("source", ""),
                    "line_no": h.get("line_no", 0),
                }
                for h in hits
            ],
        }

    def _stats(self, args: dict) -> dict:
        args = args or {}
        source = args.get("source") or None
        store, _ = self._ensure()
        try:
            rows = store.scroll_points(source=source, limit=50000)
        except Exception as e:
            logger.warning("[log-analyzer] stats scroll failed: %r", e)
            return {"error": f"Qdrant scroll failed: {e!r}"}

        from collections import Counter

        total = len(rows)
        type_counts: Counter = Counter()
        level_counts: Counter = Counter()
        status_counts: Counter = Counter()
        ip_counts: Counter = Counter()
        path_counts: Counter = Counter()
        sources: set[str] = set()
        min_ts: float | None = None
        max_ts: float | None = None
        for row in rows:
            type_counts[row.get("log_type") or "unknown"] += 1
            if row.get("level"):
                level_counts[row["level"]] += 1
            status = int(row.get("status") or 0)
            if status:
                status_counts[status] += 1
            if row.get("src_ip"):
                ip_counts[row["src_ip"]] += 1
            if row.get("path"):
                path_counts[row["path"]] += 1
            if row.get("source"):
                sources.add(row["source"])
            ts_epoch = row.get("ts_epoch") or 0.0
            if ts_epoch:
                min_ts = ts_epoch if min_ts is None else min(min_ts, ts_epoch)
                max_ts = ts_epoch if max_ts is None else max(max_ts, ts_epoch)

        return {
            "ok": True,
            "total_points": total,
            "source_filter": source,
            "sources": sorted(sources),
            "by_log_type": dict(type_counts.most_common()),
            "by_level": dict(level_counts.most_common()),
            "by_status": {
                str(k): v for k, v in status_counts.most_common(15)
            },
            "top_ips": [
                {"src_ip": ip, "count": c}
                for ip, c in ip_counts.most_common(10)
            ],
            "top_paths": [
                {"path": p, "count": c}
                for p, c in path_counts.most_common(10)
            ],
            "time_range": (
                None if min_ts is None
                else {"from": min_ts, "to": max_ts}
            ),
            "collection": store.collection,
        }

    def _list_sources(self, args: dict) -> dict:
        store, _ = self._ensure()
        try:
            sources = store.distinct_sources()
        except Exception as e:
            logger.warning("[log-analyzer] list sources failed: %r", e)
            return {"error": f"Qdrant query failed: {e!r}"}
        if not sources:
            return {
                "ok": True,
                "sources": [],
                "note": "no logs ingested yet — call ingest_log first.",
            }
        return {"ok": True, "sources": sources, "count": len(sources)}

    def _remove(self, args: dict) -> dict:
        args = args or {}
        source = args.get("source")
        if not source:
            return {"error": "'source' is required (see list_log_sources)."}
        store, _ = self._ensure()
        try:
            removed = store.delete_by_source(str(source))
        except Exception as e:
            logger.warning("[log-analyzer] remove failed: %r", e)
            return {"error": f"Qdrant delete failed: {e!r}"}
        return {
            "ok": True,
            "source": source,
            "removed": removed,
        }


# v0.9.8+: archive (zip / 7z / tar / gzip) host. Create / list /
# extract archives inside the filesystem sandboxes — read inputs
# from the read root, write outputs into the write root (same split
# roots FilesystemLocalHost uses). All the format work lives in
# ``src.archive_tools``; this class is the MCP surface + sandbox +
# budget enforcement. 7z needs the optional py7zr package (a project
# dependency since v0.9.8.0); without it the 7z tools degrade to a
# clean "pip install py7zr" error instead of a traceback.
_DEFAULT_ARCHIVE_MAX_BYTES = 512 * 1024 * 1024   # 512 MB
_DEFAULT_ARCHIVE_MAX_FILES = 10000


def _archive_budget() -> tuple[int, int]:
    """(max_bytes, max_files) from env with sane defaults."""
    try:
        max_bytes = int(
            os.environ.get(
                "QDRANT_ARCHIVE_MAX_BYTES",
                str(_DEFAULT_ARCHIVE_MAX_BYTES),
            ),
        )
    except ValueError:
        max_bytes = _DEFAULT_ARCHIVE_MAX_BYTES
    try:
        max_files = int(
            os.environ.get(
                "QDRANT_ARCHIVE_MAX_FILES",
                str(_DEFAULT_ARCHIVE_MAX_FILES),
            ),
        )
    except ValueError:
        max_files = _DEFAULT_ARCHIVE_MAX_FILES
    return max_bytes, max_files


class ArchiveLocalHost:
    """Filesystem archive tools over the MCP tool surface (v0.9.8+).

    All handlers take a single positional ``args: dict``. Every path
    argument is resolved against the filesystem read root (inputs) or
    write root (outputs) — ``..`` traversal and absolute paths outside
    the sandbox are refused, the same boundary ``FilesystemLocalHost``
    enforces for its read/write tools.
    """

    def __init__(self) -> None:
        # Lazy env-based roots; attach_* pins them for tests / stdio.
        self._read_root: Path | None = None
        self._write_root: Path | None = None

    # ── wiring ────────────────────────────────────────────────
    def attach_read_root(self, root: Any) -> None:
        try:
            p = Path(root).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            self._read_root = p
        except Exception:
            self._read_root = None

    def attach_write_root(self, root: Any) -> None:
        try:
            p = Path(root).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            self._write_root = p
        except Exception:
            self._write_root = None

    def _ensure_read_root(self) -> Path:
        if self._read_root is None:
            self._read_root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_READ_ROOT",
            )
        return self._read_root

    def _ensure_write_root(self) -> Path:
        if self._write_root is None:
            self._write_root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT",
            )
        return self._write_root

    def _resolve_read(self, raw: str) -> Path:
        """Resolve an input path inside the read OR write sandbox.

        Archives are usually written by ``archive_create`` into the
        write sandbox, so inputs are accepted from either root:
        relative names are tried against the read root first, then
        the write root; absolute paths must live inside one of them.
        Everything else is refused.
        """
        read_root = self._ensure_read_root()
        write_root = self._ensure_write_root()
        raw_path = Path(str(raw)).expanduser()
        if raw_path.is_absolute():
            p = raw_path.resolve()
            if not (
                _is_within(p, read_root) or _is_within(p, write_root)
            ):
                raise ValueError(
                    f"path {p.as_posix()} is outside both filesystem "
                    f"sandboxes (read: {read_root.as_posix()}, write: "
                    f"{write_root.as_posix()})"
                )
            if not p.exists():
                raise FileNotFoundError(f"no such file: {p.as_posix()}")
            return p
        for root in (read_root, write_root):
            candidate = (root / raw_path).resolve()
            if _is_within(candidate, root) and candidate.exists():
                return candidate
        # Nothing found — raise against the read-root candidate so
        # the message names the primary sandbox.
        probe = (read_root / raw_path).resolve()
        raise FileNotFoundError(f"no such file: {probe.as_posix()}")

    def _resolve_write(self, raw: str) -> Path:
        """Resolve an output path inside the write sandbox."""
        root = self._ensure_write_root()
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if not _is_within(p, root):
            raise ValueError(
                f"path {p.as_posix()} is outside the filesystem write "
                f"sandbox {root.as_posix()}"
            )
        return p

    # ── tool surface ──────────────────────────────────────────
    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="archive_create",
                description=(
                    "Compress files (and/or directories, walked "
                    "recursively) into an archive — zip, 7z, tar, "
                    "tar.gz, tar.bz2, or gz (gz = ONE file only). "
                    "Both the inputs and the output live in the "
                    "filesystem sandboxes; dangerous executables "
                    "are skipped from the bundle. Use when the "
                    "user says '壓縮', '打包', 'zip 起來', "
                    "'compress', 'archive these files'."
                ),
                server_name="archive-local",
                handler=self._create,
                input_schema={
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Files and/or directories to "
                                "compress, inside the read sandbox."
                            ),
                        },
                        "out_path": {
                            "type": "string",
                            "description": (
                                "Output archive path in the write "
                                "sandbox; format auto-detected from "
                                "the extension. Default: "
                                "archive-<timestamp>.<fmt>."
                            ),
                        },
                        "format": {
                            "type": "string",
                            "enum": [
                                "auto", "zip", "7z", "tar",
                                "tar.gz", "tar.bz2", "gz",
                            ],
                            "description": (
                                "Force a format, or 'auto' to sniff "
                                "it from out_path (default zip)."
                            ),
                        },
                    },
                    "required": ["files"],
                },
            ),
            LocalTool(
                name="archive_extract",
                description=(
                    "Extract a .zip / .7z / .tar / .tar.gz / .tgz / "
                    ".tar.bz2 / .gz archive into a directory inside "
                    "the write sandbox. Safety: Zip-Slip traversal "
                    "members are blocked, dangerous executable "
                    "members (.exe/.bat/.sh/...) are skipped, and "
                    "the total uncompressed size / member count are "
                    "capped (zip-bomb bounds). Returns {ok, "
                    "target_dir, extracted, skipped, count} or "
                    "{error}."
                ),
                server_name="archive-local",
                handler=self._extract,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Archive path in the read sandbox."
                            ),
                        },
                        "to_dir": {
                            "type": "string",
                            "description": (
                                "Destination directory in the write "
                                "sandbox. Default: a folder named "
                                "after the archive."
                            ),
                        },
                        "members": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional member-name allowlist."
                            ),
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": (
                                "Overwrite existing files (default "
                                "false)."
                            ),
                        },
                    },
                    "required": ["path"],
                },
            ),
            LocalTool(
                name="archive_list",
                description=(
                    "List the members of an archive without "
                    "extracting: name, uncompressed / compressed "
                    "size, is_dir, modified time."
                ),
                server_name="archive-local",
                handler=self._list,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            ),
            LocalTool(
                name="list_archive_formats",
                description=(
                    "Introspection: which archive formats can be "
                    "created / extracted, whether 7z is available "
                    "(py7zr), the size and file-count budgets, and "
                    "the sandbox roots in effect."
                ),
                server_name="archive-local",
                handler=self._formats,
                input_schema={"type": "object", "properties": {}},
            ),
        ]

    # ── internals ─────────────────────────────────────────────
    def _collect_files(
        self, raw_files: list[str],
    ) -> tuple[list[tuple[Path, str]], list[dict]]:
        """Resolve every input to ``(abs_path, arcname)``.

        Directories are walked recursively; arcnames keep the layout
        relative to the read sandbox root so archives unpack the same
        tree. Dangerous executables are skipped with a reason row.
        """
        from src.archive_tools import _DANGEROUS_EXTS

        root = self._ensure_read_root()
        files: list[tuple[Path, str]] = []
        skipped: list[dict] = []
        for raw in raw_files:
            p = self._resolve_read(raw)
            if p.is_dir():
                for child in sorted(p.rglob("*")):
                    if not child.is_file():
                        continue
                    arc = child.relative_to(root).as_posix()
                    if child.suffix.lower() in _DANGEROUS_EXTS:
                        skipped.append({
                            "path": arc,
                            "reason": "dangerous executable",
                        })
                        continue
                    files.append((child, arc))
            elif p.is_file():
                arc = p.name
                if p.suffix.lower() in _DANGEROUS_EXTS:
                    skipped.append({
                        "path": arc, "reason": "dangerous executable",
                    })
                    continue
                files.append((p, arc))
            else:
                skipped.append({
                    "path": str(raw), "reason": "not a file or directory",
                })
        return files, skipped

    # ── handlers ──────────────────────────────────────────────
    def _create(self, args: dict) -> dict:
        args = args or {}
        raw_files = args.get("files")
        if isinstance(raw_files, str):
            # LLM sent a bare string (or comma list) — tolerate it.
            raw_files = [
                s.strip() for s in raw_files.split(",") if s.strip()
            ]
        if not raw_files or not isinstance(raw_files, list):
            return {"error": "'files' (array of paths) is required."}

        from src.archive_tools import (
            CREATE_FORMATS,
            ArchiveError,
            create_archive,
            sniff_archive_format,
        )

        fmt = str(args.get("format") or "auto").strip().lower()
        out_raw = args.get("out_path")
        max_bytes, max_files = _archive_budget()

        try:
            collected, skipped = self._collect_files(raw_files)
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}
        if not collected:
            return {"error": "no compressible files.", "skipped": skipped}

        total_in = 0
        for f, _ in collected:
            try:
                total_in += f.stat().st_size
            except OSError:
                pass
        if total_in > max_bytes:
            return {
                "error": (
                    f"inputs total {total_in} bytes — over the "
                    f"max_bytes budget {max_bytes} (raise "
                    "QDRANT_ARCHIVE_MAX_BYTES to allow more)."
                ),
            }
        if len(collected) > max_files:
            return {
                "error": (
                    f"{len(collected)} files — over the max_files "
                    f"budget {max_files}."
                ),
            }

        if fmt == "auto":
            sniffed = sniff_archive_format(out_raw) if out_raw else None
            fmt = sniffed or "zip"
        if fmt not in CREATE_FORMATS:
            return {
                "error": (
                    f"unsupported format {fmt!r}; supported: "
                    f"{list(CREATE_FORMATS)}"
                ),
            }

        try:
            write_root = self._ensure_write_root()
            if out_raw:
                out = self._resolve_write(out_raw)
            else:
                suffix = {
                    "tar.gz": ".tar.gz",
                    "tar.bz2": ".tar.bz2",
                }.get(fmt, f".{fmt}")
                out = self._resolve_write(
                    f"archive-{int(time.time())}{suffix}",
                )
        except (ValueError, OSError) as e:
            return {"error": str(e)}

        try:
            summary = create_archive(collected, out, fmt)
        except ArchiveError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"archive creation failed: {e!r}"}

        out = {"ok": True, **summary}
        if skipped:
            out["skipped_inputs"] = skipped
        return out

    def _extract(self, args: dict) -> dict:
        args = args or {}
        path_raw = args.get("path")
        if not path_raw:
            return {"error": "'path' is required."}
        members = args.get("members")
        if members is not None and not isinstance(members, list):
            members = None
        overwrite = bool(args.get("overwrite") or False)
        max_bytes, max_files = _archive_budget()

        from src.archive_tools import extract_archive

        try:
            arch = self._resolve_read(path_raw)
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}

        try:
            to_dir_raw = args.get("to_dir")
            if to_dir_raw:
                dest = self._resolve_write(to_dir_raw)
            else:
                stem = arch.stem
                if stem.endswith(".tar"):
                    stem = stem[:-4]
                dest = self._resolve_write(f"{stem}_extracted")
        except (ValueError, OSError) as e:
            return {"error": str(e)}

        try:
            result = extract_archive(
                arch,
                dest,
                max_bytes=max_bytes,
                max_files=max_files,
                skip_dangerous=True,
                overwrite=overwrite,
                members=members,
            )
        except Exception as e:
            return {"error": f"extraction failed: {e!r}"}

        return {"ok": True, **result}

    def _list(self, args: dict) -> dict:
        args = args or {}
        path_raw = args.get("path")
        if not path_raw:
            return {"error": "'path' is required."}

        from src.archive_tools import (
            ArchiveError,
            list_members,
            sniff_archive_format,
        )

        try:
            arch = self._resolve_read(path_raw)
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}

        fmt = sniff_archive_format(arch)
        if not fmt:
            return {
                "error": (
                    "not a recognised archive extension — supported: "
                    ".zip .7z .tar .tar.gz .tgz .tar.bz2 .gz"
                ),
            }
        try:
            members = list_members(arch, fmt)
        except ArchiveError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"cannot read archive: {e!r}"}

        total = sum(m.get("size") or 0 for m in members)
        return {
            "ok": True,
            "path": arch.as_posix(),
            "format": fmt,
            "count": len(members),
            "total_bytes": total,
            "members": members[:500],  # context guard for the LLM
        }

    def _formats(self, args: dict) -> dict:
        from src.archive_tools import (
            CREATE_FORMATS,
            DEFAULT_MAX_BYTES,
            DEFAULT_MAX_FILES,
            sevenzip_available,
        )

        max_bytes, max_files = _archive_budget()
        return {
            "ok": True,
            "create_formats": list(CREATE_FORMATS),
            "extract_formats": list(CREATE_FORMATS),
            "sevenzip_available": sevenzip_available(),
            "sevenzip_note": (
                "7z needs the optional 'py7zr' package "
                "(pip install py7zr)" if not sevenzip_available()
                else "py7zr installed"
            ),
            "max_bytes": max_bytes,
            "max_files": max_files,
            "read_root": self._ensure_read_root().as_posix(),
            "write_root": self._ensure_write_root().as_posix(),
            "safety": (
                "zip-slip traversal blocked; dangerous executable "
                "members skipped; total-uncompressed-size and "
                "member-count caps enforced (zip-bomb bounds)"
            ),
        }


# v0.9.9+: local core-tool executors (bash / awk / grep / python /
# platform shell). Gives the LLM local compute for format
# conversion and bulk text work instead of burning context — cwd
# pinned to the filesystem WRITE sandbox, per-call timeouts, output
# caps, and a machine-level disaster blocklist. Discovery: bash/awk
# from Git-for-Windows or the OS; grep is pure Python (always
# available); python is sys.executable.
_DEFAULT_EXEC_TIMEOUT = 30
_MAX_EXEC_TIMEOUT = 120
_DEFAULT_EXEC_MAX_OUTPUT = 64 * 1024

# Machine-level destruction patterns (narrow by design — see
# exec_tools). QDRANT_EXEC_BLOCKLIST=off disables the check for
# operators who really know what they're doing.
_EXEC_BLOCKLIST_OFF = os.environ.get(
    "QDRANT_EXEC_BLOCKLIST", "",
).strip().lower() in ("off", "0", "false", "no")


def _exec_budget() -> tuple[int, int]:
    """(timeout_s, max_output_chars) from env with sane caps."""
    try:
        timeout = int(os.environ.get(
            "QDRANT_EXEC_TIMEOUT", str(_DEFAULT_EXEC_TIMEOUT),
        ))
    except ValueError:
        timeout = _DEFAULT_EXEC_TIMEOUT
    timeout = max(1, min(timeout, _MAX_EXEC_TIMEOUT))
    try:
        max_output = int(os.environ.get(
            "QDRANT_EXEC_MAX_OUTPUT", str(_DEFAULT_EXEC_MAX_OUTPUT),
        ))
    except ValueError:
        max_output = _DEFAULT_EXEC_MAX_OUTPUT
    return timeout, max(1024, max_output)


class ZipFsLocalHost:
    """Treat ZIP / TAR uploads as a filesystem (v0.9.133.13).

    The user ships ONE archive (``uploads/`` in the write sandbox) and
    the LLM lists / reads / writes / renames / deletes members IN
    PLACE — cp-on-write atomic rebuilds, no extraction required. The
    modified single file is what the user downloads again.

    Formats: zip / tar / tar.gz / tar.bz2 (read-write); 7z read-only
    (py7zr optional). Every archive path is anchored to the
    filesystem sandboxes exactly like ``ArchiveLocalHost``; mutating
    calls additionally require the archive to live inside the WRITE
    root. Deletes/renames are confirm-gated; all budgets mirror the
    shared archive discipline (member count / uncompressed bytes)."""

    def __init__(self) -> None:
        self._read_root: Any = None
        self._write_root: Any = None

    # ── wiring (same shape as ArchiveLocalHost) ───────────────
    def attach_read_root(self, root: Any) -> None:
        try:
            p = Path(root).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            self._read_root = p
        except Exception:
            self._read_root = None

    def attach_write_root(self, root: Any) -> None:
        try:
            p = Path(root).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            self._write_root = p
        except Exception:
            self._write_root = None

    def _ensure_read_root(self) -> Path:
        if self._read_root is None:
            self._read_root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_READ_ROOT",
            )
        return self._read_root

    def _ensure_write_root(self) -> Path:
        if self._write_root is None:
            self._write_root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT",
            )
        return self._write_root

    def _resolve_read(self, raw: str) -> Path:
        """Resolve an archive path against the read OR write root."""
        read_root = self._ensure_read_root()
        write_root = self._ensure_write_root()
        raw_path = Path(str(raw)).expanduser()
        if raw_path.is_absolute():
            p = raw_path.resolve()
            if not (_is_within(p, read_root) or _is_within(p, write_root)):
                raise ValueError(
                    f"path {p.as_posix()} is outside both filesystem "
                    f"sandboxes (read: {read_root.as_posix()}, write: "
                    f"{write_root.as_posix()})"
                )
            if not p.exists():
                raise FileNotFoundError(f"no such file: {p.as_posix()}")
            return p
        for root in (read_root, write_root):
            candidate = (root / raw_path).resolve()
            if _is_within(candidate, root) and candidate.exists():
                return candidate
        probe = (read_root / raw_path).resolve()
        raise FileNotFoundError(f"no such file: {probe.as_posix()}")

    def _resolve_mutable(self, raw: str) -> Path:
        """Resolve AND require the archive to live in the WRITE root
        (mutations rewrite the single file in place)."""
        p = self._resolve_read(raw)
        write_root = self._ensure_write_root()
        if not _is_within(p, write_root):
            raise ValueError(
                f"archive {p.as_posix()} is outside the write sandbox "
                f"({write_root.as_posix()}) — copying it into the "
                "write sandbox first makes it editable."
            )
        return p

    def _err(self, e: Exception) -> dict[str, Any]:
        from src.zipfs_tools import ZipFsError

        if isinstance(e, ZipFsError):
            return {"error": str(e)}
        return {"error": repr(e)[:220]}

    def _decode(self, content: str, encoding: str) -> bytes:
        if encoding == "base64":
            import base64

            try:
                return base64.b64decode(content)
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"invalid base64 content: {e!r}") from e
        return str(content).encode("utf-8")

    @staticmethod
    def _expand_prefix(path: Path, names: list[str]) -> list[str]:
        """Expand trailing-slash names into full member sets (dir
        delete/rename). Returns the flat, normalised name list."""
        from src.zipfs_tools import _members, _sniff

        out: list[str] = []
        for nm in names:
            if nm.endswith("/"):
                prefix = nm
                members = _members(path, _sniff(path))
                for m in members:
                    mn = str(m.get("name") or "")
                    if mn.startswith(prefix) or mn == prefix[:-1]:
                        out.append(mn)
            else:
                out.append(nm)
        return out

    # ── tool surface ──────────────────────────────────────────
    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="zipfs_list",
                description=(
                    "List a ZIP/TAR/7z archive as a FOLDER TREE without "
                    "extracting it (depth-limited; subdir-rooted). Use "
                    "when the user uploads a .zip/.tar to work on its "
                    "contents."
                ),
                server_name="zipfs-local",
                handler=self._list,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string",
                                 "description": "archive path in the "
                                 "sandbox (e.g. uploads/bundle.zip)"},
                        "subdir": {"type": "string"},
                        "depth": {"type": "integer",
                                  "minimum": 1, "maximum": 8},
                    },
                    "required": ["path"],
                },
            ),
            LocalTool(
                name="zipfs_read",
                description=(
                    "Read ONE file inside a ZIP/TAR/7z archive WITHOUT "
                    "extracting the whole archive. Text comes back "
                    "decoded; binary members come back base64."
                ),
                server_name="zipfs-local",
                handler=self._read,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "member": {"type": "string",
                                   "description": "member path inside "
                                   "the archive, e.g. docs/a.txt"},
                        "max_bytes": {"type": "integer",
                                      "minimum": 200,
                                      "maximum": 5000000},
                    },
                    "required": ["path", "member"],
                },
            ),
            LocalTool(
                name="zipfs_write",
                description=(
                    "Create or overwrite ONE file inside a ZIP/TAR "
                    "archive (atomic in-place rebuild — no extraction). "
                    "encoding='text' (default, utf-8) or 'base64' for "
                    "binary payloads; the archive must live in the "
                    "write sandbox. Dangerous extensions (.exe/.bat/..) "
                    "are refused."
                ),
                server_name="zipfs-local",
                handler=self._write,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "member": {"type": "string"},
                        "content": {"type": "string"},
                        "encoding": {"type": "string",
                                     "enum": ["text", "base64"]},
                    },
                    "required": ["path", "member", "content"],
                },
            ),
            LocalTool(
                name="zipfs_delete",
                description=(
                    "Delete members from a ZIP/TAR archive (atomic "
                    "rebuild). A name ending with '/' deletes the WHOLE "
                    "directory subtree. confirm=true required."
                ),
                server_name="zipfs-local",
                handler=self._delete,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "members": {"type": "array",
                                    "items": {"type": "string"},
                                    "description": "member names; end "
                                    "with '/' for a subtree"},
                        "confirm": {"type": "boolean"},
                    },
                    "required": ["path", "members", "confirm"],
                },
            ),
            LocalTool(
                name="zipfs_rename",
                description=(
                    "Rename/move a member (or a whole directory subtree "
                    "when the source ends with '/') inside a ZIP/TAR "
                    "archive. confirm=true required."
                ),
                server_name="zipfs-local",
                handler=self._rename,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "src": {"type": "string"},
                        "dst": {"type": "string"},
                        "confirm": {"type": "boolean"},
                    },
                    "required": ["path", "src", "dst", "confirm"],
                },
            ),
            LocalTool(
                name="zipfs_extract_one",
                description=(
                    "Pull ONE member out of the archive into the write "
                    "sandbox (default: uploads/<basename>) so it can be "
                    "displayed, OCR'd, or further processed by the "
                    "filesystem tools. Dangerous executables refused."
                ),
                server_name="zipfs-local",
                handler=self._extract_one,
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "member": {"type": "string"},
                        "dest_path": {"type": "string"},
                        "allow_dangerous": {"type": "boolean"},
                    },
                    "required": ["path", "member"],
                },
            ),
            LocalTool(
                name="zipfs_info",
                description=(
                    "Archive metadata: format, member/dir/file counts, "
                    "sizes, writability, budgets — one call before "
                    "deciding how to operate on it."
                ),
                server_name="zipfs-local",
                handler=self._info,
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            LocalTool(
                name="zipfs_status",
                description=(
                    "zipfs capability introspection: readable/writable "
                    "formats, budgets, sandbox roots, py7zr availability."
                ),
                server_name="zipfs-local",
                handler=self._status,
                input_schema={"type": "object", "properties": {}},
            ),
        ]

    # ── handlers ──────────────────────────────────────────────
    def _list(self, args: dict) -> dict[str, Any]:
        args = args or {}
        if not args.get("path"):
            return {"error": "'path' is required."}
        try:
            arch = self._resolve_read(str(args["path"]))
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}
        from src.zipfs_tools import list_tree

        try:
            return list_tree(
                arch,
                subdir=args.get("subdir") or None,
                depth=int(args.get("depth") or 2),
            )
        except Exception as e:
            return self._err(e)

    def _read(self, args: dict) -> dict[str, Any]:
        args = args or {}
        if not (args.get("path") and args.get("member")):
            return {"error": "'path' and 'member' are required."}
        try:
            arch = self._resolve_read(str(args["path"]))
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}
        from src.zipfs_tools import read_member

        try:
            return read_member(
                arch, str(args["member"]),
                max_bytes=int(args.get("max_bytes") or 1000000),
            )
        except Exception as e:
            return self._err(e)

    def _write(self, args: dict) -> dict[str, Any]:
        args = args or {}
        if args.get("member") is None:
            return {"error": "'member' is required."}
        if args.get("content") is None or not args.get("path"):
            return {"error":
                    "'path' and 'content' are required with 'member'."}
        try:
            arch = self._resolve_mutable(str(args["path"]))
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}
        from src.zipfs_tools import _sniff, write_member

        try:
            data = self._decode(
                str(args.get("content")),
                str(args.get("encoding") or "text"),
            )
            return write_member(
                arch, str(args["member"]), data, fmt=_sniff(arch),
                max_bytes=_archive_budget()[0],
                max_files=_archive_budget()[1],
            )
        except Exception as e:
            return self._err(e)

    def _delete(self, args: dict) -> dict[str, Any]:
        args = args or {}
        if not (args.get("path") and args.get("members")):
            return {"error": "'path' and 'members' are required."}
        if not args.get("confirm"):
            return {
                "error": "confirm=true is required to DELETE archive "
                         "members (undoable only via a fresh upload).",
            }
        try:
            arch = self._resolve_mutable(str(args["path"]))
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}
        from src.zipfs_tools import _sniff, delete_members

        try:
            names = self._expand_prefix(
                arch, [str(m) for m in args["members"]],
            )
            return delete_members(
                arch, names, fmt=_sniff(arch),
                max_bytes=_archive_budget()[0],
                max_files=_archive_budget()[1],
            )
        except Exception as e:
            return self._err(e)

    def _rename(self, args: dict) -> dict[str, Any]:
        args = args or {}
        if not all(args.get(k) for k in ("path", "src", "dst")):
            return {"error": "'path', 'src' and 'dst' are required."}
        if not args.get("confirm"):
            return {
                "error": "confirm=true is required to RENAME archive "
                         "members.",
            }
        try:
            arch = self._resolve_mutable(str(args["path"]))
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}
        from src.zipfs_tools import _sniff, rename_members

        try:
            src_orig = str(args["src"])
            dst_raw = str(args["dst"]).rstrip("/")
            has_slash = src_orig.endswith("/")
            src_raw = src_orig.rstrip("/")
            rename_map: dict[str, str] = {}
            for src in self._expand_prefix(arch, [src_orig]):
                base = src.rstrip("/")
                if base == src_raw:
                    dst = (dst_raw + "/") if has_slash else dst_raw
                else:
                    rest = base[len(src_raw):]
                    dst = dst_raw + rest + ("/" if has_slash else "")
                rename_map[src] = dst
            return rename_members(
                arch, rename_map, fmt=_sniff(arch),
                max_bytes=_archive_budget()[0],
                max_files=_archive_budget()[1],
            )
        except Exception as e:
            return self._err(e)

    def _extract_one(self, args: dict) -> dict[str, Any]:
        args = args or {}
        if not (args.get("path") and args.get("member")):
            return {"error": "'path' and 'member' are required."}
        try:
            arch = self._resolve_read(str(args["path"]))
            write_root = self._ensure_write_root()
            dest_raw = str(args.get("dest_path") or "").strip()
            if dest_raw:
                dest = Path(dest_raw)
                if not dest.is_absolute():
                    dest = write_root / dest
            else:
                base = Path(
                    str(args["member"]).rstrip("/"),
                ).name or "member.bin"
                dest = write_root / "uploads" / base
            dest = Path(dest).resolve()
            if not _is_within(dest, write_root):
                return {"error":
                        f"dest {dest.as_posix()} is outside the write "
                        f"sandbox {write_root.as_posix()}"}
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}
        from src.zipfs_tools import extract_member

        try:
            return extract_member(
                arch, str(args["member"]), dest,
                allow_dangerous=bool(args.get("allow_dangerous")),
            )
        except Exception as e:
            return self._err(e)

    def _info(self, args: dict) -> dict[str, Any]:
        args = args or {}
        if not args.get("path"):
            return {"error": "'path' is required."}
        try:
            arch = self._resolve_read(str(args["path"]))
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}
        from src.zipfs_tools import archive_info

        try:
            out = archive_info(arch)
            out["in_write_root"] = _is_within(
                arch, self._ensure_write_root(),
            )
            return out
        except Exception as e:
            return self._err(e)

    def _status(self, _args: dict) -> dict[str, Any]:
        from src.archive_tools import DEFAULT_MAX_BYTES, DEFAULT_MAX_FILES
        from src.zipfs_tools import RO_FORMATS, _sevenzip_available

        return {
            "ok": True, "server": "zipfs-local",
            "read_write": ["zip", "tar", "tar.gz", "tar.bz2"],
            "read_only": list(RO_FORMATS),
            "py7zr": _sevenzip_available(),
            "budget": {
                "max_members": DEFAULT_MAX_FILES,
                "max_uncompressed_bytes": DEFAULT_MAX_BYTES,
            },
            "read_root": self._ensure_read_root().as_posix(),
            "write_root": self._ensure_write_root().as_posix(),
            "note": "mutations require the archive INSIDE the write "
                    "root; deletes/renames need confirm=true",
        }


class ExecLocalHost:
    """Sandboxed local compute for the LLM (v0.9.9+).

    Tools: ``run_shell`` (platform native) / ``run_bash`` (GNU
    toolchain) / ``run_grep`` (pure Python) / ``run_awk`` (system
    awk) / ``run_python`` (sys.executable). Every subprocess runs
    with cwd = the filesystem write sandbox; the child env carries
    ``QDRANT_READ_ROOT`` / ``QDRANT_WRITE_ROOT``.

    v0.9.105.0: six ``os_*`` REAL-input tools (SendInput keyboard/
    mouse for trusted-only fields the bridge cannot fill — see
    ``src/os_input_tools.py``; fail-closed: QDRANT_OS_INPUT_ALLOW=on
    + per-call confirm=true + title_regex anchor + cursor restore +
    audit jsonl).
    """

    def __init__(self) -> None:
        self._write_root: Path | None = None
        self._read_root: Path | None = None

    # ── wiring ────────────────────────────────────────────────
    def attach_write_root(self, root: Any) -> None:
        try:
            p = Path(root).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            self._write_root = p
        except Exception:
            self._write_root = None

    def attach_read_root(self, root: Any) -> None:
        try:
            p = Path(root).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            self._read_root = p
        except Exception:
            self._read_root = None

    def _ensure_write_root(self) -> Path:
        if self._write_root is None:
            self._write_root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT",
            )
        return self._write_root

    def _ensure_read_root(self) -> Path:
        if self._read_root is None:
            self._read_root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_READ_ROOT",
            )
        return self._read_root

    def _resolve_input(self, raw: str) -> Path:
        """Resolve an input path against read ∪ write roots
        (relative names tried against the read root first, then the
        write root) — same rule as the archive host's inputs."""
        read_root = self._ensure_read_root()
        write_root = self._ensure_write_root()
        raw_path = Path(str(raw)).expanduser()
        if raw_path.is_absolute():
            p = raw_path.resolve()
            if not (
                _is_within(p, read_root) or _is_within(p, write_root)
            ):
                raise ValueError(
                    f"path {p.as_posix()} is outside both filesystem "
                    f"sandboxes"
                )
            return p
        for root in (read_root, write_root):
            candidate = (root / raw_path).resolve()
            if _is_within(candidate, root) and candidate.exists():
                return candidate
        probe = (read_root / raw_path).resolve()
        raise FileNotFoundError(f"no such file: {probe.as_posix()}")

    @staticmethod
    def _wrap(result: dict, tool: str) -> dict:
        result.setdefault("ok", True)
        result["tool"] = tool
        return result

    def _check_command(self, text: str) -> dict | None:
        """Blocklist gate shared by shell/bash. Returns an error
        dict when the command is refused, else None."""
        if _EXEC_BLOCKLIST_OFF:
            return None
        from src.exec_tools import is_dangerous

        hit = is_dangerous(text)
        if hit:
            return {
                "error": (
                    f"refused: machine-level destructive command "
                    f"matched blocklist ({hit!r}). Sandbox file "
                    "operations are fine — this pattern targets "
                    "the OS/disk itself."
                ),
            }
        return None

    # ── tool surface ──────────────────────────────────────────
    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="run_shell",
                description=(
                    "Run a platform-native shell command (Windows: "
                    "cmd /c; POSIX: /bin/sh -c) with the filesystem "
                    "write sandbox as the working directory. Use for "
                    "OS-native batch work. Machine-level destructive "
                    "commands (rm -rf /, format, shutdown, "
                    "del /f /s...) are refused. Returns {stdout, "
                    "stderr, exit_code, duration_ms} — output is "
                    "capped (64 KB)."
                ),
                server_name="exec-local",
                handler=self._run_shell,
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {
                            "type": "integer",
                            "description": (
                                "Seconds (default 30, hard cap 120)."
                            ),
                        },
                    },
                    "required": ["command"],
                },
            ),
            LocalTool(
                name="run_bash",
                description=(
                    "Run a GNU bash script (bash -c) with the write "
                    "sandbox as cwd. Git-for-Windows bundles "
                    "awk/grep/sed, so full Unix pipelines work: "
                    "\"grep ERROR app.log | awk -F, '{print $2}' | "
                    "sort | uniq -c\". Use for format conversion and "
                    "bulk text surgery instead of reading whole "
                    "files into your context."
                ),
                server_name="exec-local",
                handler=self._run_bash,
                input_schema={
                    "type": "object",
                    "properties": {
                        "script": {"type": "string"},
                        "timeout": {
                            "type": "integer",
                            "description": (
                                "Seconds (default 30, hard cap 120)."
                            ),
                        },
                    },
                    "required": ["script"],
                },
            ),
            LocalTool(
                name="run_grep",
                description=(
                    "grep over files in the filesystem sandboxes — "
                    "pure Python implementation, always available "
                    "(regex or fixed-string, ignore_case, invert, "
                    "line_numbers, count mode, filenames_only "
                    "mode, context lines). Directories are walked "
                    "recursively; binaries and >4 MB files are "
                    "skipped. Returns {matches|counts|files, "
                    "total_matches, truncated}."
                ),
                server_name="exec-local",
                handler=self._run_grep,
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Sandbox-relative files/directories "
                                "(default: the whole read sandbox)."
                            ),
                        },
                        "fixed": {
                            "type": "boolean",
                            "description": "Literal match (grep -F).",
                        },
                        "ignore_case": {
                            "type": "boolean", "description": "-i",
                        },
                        "invert": {
                            "type": "boolean", "description": "-v",
                        },
                        "count": {
                            "type": "boolean", "description": "-c",
                        },
                        "filenames_only": {
                            "type": "boolean", "description": "-l",
                        },
                        "line_numbers": {
                            "type": "boolean", "description": "-n",
                        },
                        "after_context": {
                            "type": "integer", "description": "-A",
                        },
                        "before_context": {
                            "type": "integer", "description": "-B",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Output line cap (200).",
                        },
                    },
                    "required": ["pattern"],
                },
            ),
            LocalTool(
                name="run_awk",
                description=(
                    "Run the system awk over ONE file in the "
                    "sandboxes (Git-for-Windows / WSL / Linux / "
                    "macOS all ship awk). Use for column surgery: "
                    "awk -F, '{sum += $3} END {print sum}' data.csv. "
                    "If awk is unavailable the error suggests "
                    "run_bash / run_python instead."
                ),
                server_name="exec-local",
                handler=self._run_awk,
                input_schema={
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "description": (
                                "awk program, e.g. "
                                "\"{count++} END {print count}\"."
                            ),
                        },
                        "file": {
                            "type": "string",
                            "description": (
                                "Input file, sandbox-relative."
                            ),
                        },
                        "field_separator": {
                            "type": "string",
                            "description": "-F value (e.g. \",\").",
                        },
                        "timeout": {"type": "integer"},
                    },
                    "required": ["script", "file"],
                },
            ),
            LocalTool(
                name="run_python",
                description=(
                    "Run Python code (sys.executable -c <code>) with "
                    "the write sandbox as cwd — the most flexible "
                    "converter: json/csv transforms, regex "
                    "extraction, computed reports. Write outputs "
                    "into the sandbox (relative paths); stdout is "
                    "capped. Use this INSTEAD of dumping large "
                    "files through your context."
                ),
                server_name="exec-local",
                handler=self._run_python,
                input_schema={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "timeout": {
                            "type": "integer",
                            "description": (
                                "Seconds (default 30, hard cap 120)."
                            ),
                        },
                    },
                    "required": ["code"],
                },
            ),
            LocalTool(
                name="os_input_status",
                description=(
                    "OS real-input capability report (read-only): gate "
                    "status (QDRANT_OS_INPUT_ALLOW), cursor position, "
                    "foreground window title, screen size, pynput "
                    "availability, and the recommended flow. No "
                    "takeover — start here."
                ),
                server_name="exec-local",
                handler=self._os_status,
                input_schema={"type": "object", "properties": {},
                              "required": []},
            ),
            LocalTool(
                name="os_windows_list",
                description=(
                    "List visible top-level windows (title/hwnd/pid) "
                    "filtered by an optional regex on the title — "
                    "read-only, no takeover. Use to nail the exact "
                    "title_regex."
                ),
                server_name="exec-local",
                handler=self._os_windows,
                input_schema={"type": "object",
                              "properties": {
                                  "regex": {"type": "string"},
                                  "limit": {"type": "integer"},
                              },
                              "required": []},
            ),
            LocalTool(
                name="os_focus_window",
                description=(
                    "Bring a window to the FOREGROUND by title_regex "
                    "(Alt-trick to bypass the foreground lock). "
                    "DELEGATES REAL FOCUS STEAL — needs "
                    "QDRANT_OS_INPUT_ALLOW=on + confirm=true."
                ),
                server_name="exec-local",
                handler=self._os_focus,
                input_schema={"type": "object",
                              "properties": {
                                  "title_regex": {"type": "string"},
                                  "confirm": {"type": "boolean"},
                              },
                              "required": ["title_regex", "confirm"]},
            ),
            LocalTool(
                name="os_click_point",
                description=(
                    "REAL OS mouse click at PHYSICAL screen (x,y) — "
                    "pair with the firefox bridge tool "
                    "browser_element_point (physX/physY). First it "
                    "brings the window matching title_regex to "
                    "foreground and verifies the foreground title; "
                    "restores the cursor afterwards. preview=true = "
                    "passive screen crop around (x,y) saved into "
                    "sandbox os_input/ for describe_image review "
                    "(no cursor move, no focus steal; gate still "
                    "applies). REAL click requires confirm=true."
                ),
                server_name="exec-local",
                handler=self._os_click,
                input_schema={"type": "object",
                              "properties": {
                                  "x": {"type": "integer"},
                                  "y": {"type": "integer"},
                                  "title_regex": {"type": "string"},
                                  "confirm": {"type": "boolean"},
                                  "preview": {"type": "boolean"},
                                  "half": {"type": "integer"},
                                  "restore_cursor": {"type": "boolean"},
                              },
                              "required": ["x", "y"]},
            ),
            LocalTool(
                name="os_type_text",
                description=(
                    "Type REAL text (SendInput unicode, per-char pacing) "
                    "into the CURRENTLY focused element — click the "
                    "target first (os_click_point or the user's real "
                    "click). title_regex must still match the "
                    "foreground window; confirm=true. Reserved for "
                    "trusted-only fields the bridge cannot fill "
                    "(Teams/Outlook rich compose, M365 login tile) — "
                    "normal <input> fields stay with the bridge type."
                ),
                server_name="exec-local",
                handler=self._os_type,
                input_schema={"type": "object",
                              "properties": {
                                  "text": {"type": "string"},
                                  "title_regex": {"type": "string"},
                                  "confirm": {"type": "boolean"},
                              },
                              "required": ["text", "title_regex", "confirm"]},
            ),
            LocalTool(
                name="os_key_press",
                description=(
                    "Press ONE whitelisted key (Enter/Escape/Tab/Space/"
                    "Backspace/Delete/Home/End/PageUp/PageDown/Arrows) "
                    "as REAL input into the focused element; "
                    "title_regex must match the foreground window; "
                    "confirm=true. Submit-type combos (Control+Enter, "
                    "Meta+Enter…) are refused by design."
                ),
                server_name="exec-local",
                handler=self._os_key,
                input_schema={"type": "object",
                              "properties": {
                                  "key": {"type": "string"},
                                  "title_regex": {"type": "string"},
                                  "confirm": {"type": "boolean"},
                              },
                              "required": ["key", "title_regex", "confirm"]},
            ),
        ] + _script_tools_for(self)

    # ── handlers ──────────────────────────────────────────────
    def _run_shell(self, args: dict) -> dict:
        args = args or {}
        command = args.get("command")
        if not command:
            return {"error": "'command' is required."}
        blocked = self._check_command(str(command))
        if blocked:
            return blocked
        timeout, max_output = _exec_budget()
        try:
            timeout = max(1, min(int(args.get("timeout") or timeout), 120))
        except (TypeError, ValueError):
            pass
        from src.exec_tools import run_shell

        result = run_shell(
            str(command),
            self._ensure_write_root().as_posix(),
            timeout=timeout,
            max_output=max_output,
            read_root=self._ensure_read_root().as_posix(),
        )
        self._auto_note(result, str(command), 'cmd') 
        return self._wrap(result, "shell")

    def _run_bash(self, args: dict) -> dict:
        args = args or {}
        script = args.get("script")
        if not script:
            return {"error": "'script' is required."}
        blocked = self._check_command(str(script))
        if blocked:
            return blocked
        timeout, max_output = _exec_budget()
        try:
            timeout = max(1, min(int(args.get("timeout") or timeout), 120))
        except (TypeError, ValueError):
            pass
        from src.exec_tools import run_bash

        result = run_bash(
            str(script),
            self._ensure_write_root().as_posix(),
            timeout=timeout,
            max_output=max_output,
            read_root=self._ensure_read_root().as_posix(),
        )
        self._auto_note(result, str(script), 'sh') 
        return self._wrap(result, "bash")

    def _run_grep(self, args: dict) -> dict:
        args = args or {}
        pattern = args.get("pattern")
        if pattern is None or pattern == "":
            return {"error": "'pattern' is required."}
        paths = args.get("paths")
        if paths is not None and not isinstance(paths, list):
            paths = None
        timeout_unused, max_output = _exec_budget()

        from src.exec_tools import grep_files

        try:
            result = grep_files(
                str(pattern),
                self._ensure_read_root(),
                paths=paths,
                fixed=bool(args.get("fixed")),
                ignore_case=bool(args.get("ignore_case")),
                invert=bool(args.get("invert")),
                count=bool(args.get("count")),
                filenames_only=bool(args.get("filenames_only")),
                line_numbers=args.get("line_numbers") is not False,
                after_context=int(args.get("after_context") or 0),
                before_context=int(args.get("before_context") or 0),
                max_results=max(1, min(int(args.get("max_results") or 200), 1000)),
            )
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"grep failed: {e!r}"}
        return self._wrap(result, "grep")

    def _run_awk(self, args: dict) -> dict:
        args = args or {}
        script = args.get("script")
        file_raw = args.get("file")
        if not script or not file_raw:
            return {"error": "'script' and 'file' are required."}
        timeout, max_output = _exec_budget()
        try:
            timeout = max(1, min(int(args.get("timeout") or timeout), 120))
        except (TypeError, ValueError):
            pass

        from src.exec_tools import run_awk

        try:
            f = self._resolve_input(file_raw)
        except (ValueError, FileNotFoundError, OSError) as e:
            return {"error": str(e)}

        result = run_awk(
            str(script),
            f.as_posix(),
            self._ensure_write_root().as_posix(),
            field_separator=args.get("field_separator") or None,
            timeout=timeout,
            max_output=max_output,
        )
        return self._wrap(result, "awk")

    def _run_python(self, args: dict) -> dict:
        args = args or {}
        code = args.get("code")
        if not code:
            return {"error": "'code' is required."}
        timeout, max_output = _exec_budget()
        try:
            timeout = max(1, min(int(args.get("timeout") or timeout), 120))
        except (TypeError, ValueError):
            pass

        from src.exec_tools import exec_python

        result = exec_python(
            str(code),
            self._ensure_write_root().as_posix(),
            timeout=timeout,
            max_output=max_output,
            read_root=self._ensure_read_root().as_posix(),
        )
        self._auto_note(result, str(code), 'py')
        return self._wrap(result, "python")

    def _auto_note(self, result: dict, code: str, lang: str) -> None:
        # Auto-save a SUCCESSFUL run's code into the script library  
        # (v0.9.102.5+) and stamp the result with the script name.  
        if result.get('exit_code') != 0:  
            return  
        try:  
            from src.exec_tools import auto_save_script  
            meta = auto_save_script(code, lang, scripts_root=self._ensure_write_root())  
        except Exception:  
            return  
        if meta:  
            result['auto_saved'] = {  
                'name': str(meta.get('name')),  
                'note': ('code saved to the script library - replay with script_run(name, args?) instead of re-generating' + (' (already in the library)' if meta.get('dedup') else '')),  
            }  
    def _script_save(self, args: dict) -> dict:
        args = args or {}  
        code = args.get('code')  
        if not code:  
            return {'error': 'code is required.'}  
        from src.exec_tools import save_script  
        return save_script(str(code), str(args.get('lang') or 'auto'), name=str(args.get('name') or ''), description=str(args.get('description') or ''), conda_env=str(args.get('conda_env') or ''), docker_image=str(args.get('docker_image') or ''), network=str(args.get('network') or ''), scripts_root=self._ensure_write_root())  
    def _script_list(self, args: dict) -> dict:
        from src.exec_tools import list_scripts  
        return list_scripts(scripts_root=self._ensure_write_root())  
    def _script_run(self, args: dict) -> dict:
        args = args or {}  
        name = args.get('name')  
        if not name:  
            return {'error': 'name is required.'}  
        timeout, max_output = _exec_budget()  
        try:  
            timeout = max(1, min(int(args.get('timeout') or timeout), 120))  
        except (TypeError, ValueError):  
            pass  
        from src.exec_tools import run_script  
        result = run_script(str(name), str(args.get('args') or ''), cwd=self._ensure_write_root().as_posix(), timeout=timeout, max_output=max_output, read_root=self._ensure_read_root().as_posix(), scripts_root=self._ensure_write_root())  
        return self._wrap(result, 'script')

    # ─── os-input tools (v0.9.105.0 — REAL SendInput; fail-closed) ─
    def _os_status(self, args: dict) -> dict:
        from src.os_input_tools import os_input_status
        return os_input_status()

    def _os_windows(self, args: dict) -> dict:
        from src.os_input_tools import windows_list
        a = args or {}
        try:
            limit = int(a.get('limit') or 0)
        except (TypeError, ValueError):
            limit = 0
        return windows_list(str(a.get('regex') or ''), limit)

    def _os_focus(self, args: dict) -> dict:
        from src.os_input_tools import focus_window
        a = args or {}
        return focus_window(str(a.get('title_regex') or ''),
                            confirm=bool(a.get('confirm') or False))

    def _os_click(self, args: dict) -> dict:
        from src.os_input_tools import click_point, preview_crop
        a = args or {}
        if bool(a.get('preview') or False):
            return preview_crop(a.get('x', 0), a.get('y', 0),
                                half=a.get('half', 60))
        return click_point(
            a.get('x', 0), a.get('y', 0),
            title_regex=str(a.get('title_regex') or ''),
            confirm=bool(a.get('confirm') or False),
            restore_cursor=bool(a.get('restore_cursor', True)))

    def _os_type(self, args: dict) -> dict:
        from src.os_input_tools import type_text
        a = args or {}
        return type_text(str(a.get('text') or ''),
                         title_regex=str(a.get('title_regex') or ''),
                         confirm=bool(a.get('confirm') or False))

    def _os_key(self, args: dict) -> dict:
        from src.os_input_tools import press_key
        a = args or {}
        return press_key(str(a.get('key') or ''),
                         title_regex=str(a.get('title_regex') or ''),
                         confirm=bool(a.get('confirm') or False))

_SCRIPT_TOOL_DEFS = [  
    ('script_save', 'Save a reusable script (python / powershell / bash / cmd) into the exec script library - the write-sandbox scripts/ dir; sha1 dedup; optional conda_env or docker_image isolation backend; machine-destructive code is refused.', {'name': {'type': 'string', 'description': 'short kebab-case name; suffix auto-appended'}, 'code': {'type': 'string', 'description': 'the script content'}, 'lang': {'type': 'string', 'description': 'py / ps1 / sh / cmd (default: auto-sniff)'}, 'description': {'type': 'string', 'description': 'one-line what-it-does note'}, 'conda_env': {'type': 'string', 'description': 'optional conda env name (py only)'}, 'docker_image': {'type': 'string', 'description': 'optional docker image repo/tag; the container mounts the write sandbox only and defaults to no network'}, 'network': {'type': 'string', 'description': 'none or bridge (docker scripts only)'}}, ['code'], '_script_save'),  
    ('script_list', 'List the exec script library (name / lang / chars / description / runs / backend). Read-only.', {}, [], '_script_list'),  
    ('script_run', 'Replay a SAVED library script as a file: direct / conda run / docker run per its saved backend, sha1 tamper-checked first.', {'name': {'type': 'string', 'description': 'library script name (with or without suffix)'}, 'args': {'type': 'string', 'description': 'optional argv string for the script'}, 'timeout': {'type': 'integer', 'description': 'seconds (default 30, hard cap 120)'}}, ['name'], '_script_run'),  
]  
def _script_tools_for(self):  
    outs = []  
    for n, d, props, req, hname in _SCRIPT_TOOL_DEFS:  
        outs.append(LocalTool(name=n, description=d, input_schema={'type': 'object', 'properties': props, 'required': req}, server_name='exec-local', handler=getattr(self, hname)))  
    return outs 

def _fmt_utc_offset(td) -> str:
    """``timedelta`` → ``'+08:00'`` / ``'-05:30'`` / ``'+00:00'``
    (ISO 8601 offset style; sub-minute offsets append seconds)."""
    if td is None:
        return "+00:00"
    total = int(td.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    out = f"{sign}{hours:02d}:{minutes:02d}"
    if seconds:
        out += f":{seconds:02d}"
    return out


class DateTimeLocalHost:
    """Current date / time query (v0.9.11.0+).

    One zero-argument tool — ``get_current_time`` — returns the
    LOCAL time (ISO 8601 with UTC offset + timezone name) and UTC+0
    time side by side, plus Unix epoch seconds and weekday numbers,
    so the LLM never has to guess the clock or do timezone
    arithmetic from memory. Registered in-process by
    ``McpManager.from_config``; stdio server
    ``src/mcp_server/time_server.py`` for external clients.

    No sandbox / security concerns: the tool is read-only, takes no
    arguments, and touches nothing but the local clock.
    """

    SERVER_NAME = "datetime-local"

    # Locale-independent weekday names (strftime("%A") depends on
    # the C locale; this tuple does not).
    _WEEKDAY_EN = (
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday",
    )

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="get_current_time",
                description=(
                    "Current date and time — LOCAL time (with UTC "
                    "offset and timezone name) and UTC+0 time, plus "
                    "Unix epoch seconds and weekday. Call this "
                    "whenever the user asks '現在幾點' / '今天幾號' / "
                    "'what time is it' / 'today's date' / anything "
                    "that needs the current clock — never guess "
                    "from memory."
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._now,
            ),
        ]

    # ── handlers (LocalTool convention: (self, args: dict)) ────
    def _now(self, args: dict) -> dict:
        local = datetime.now().astimezone()
        utc = datetime.now(timezone.utc)
        return {
            "local": self._render(local),
            "utc": self._render(utc),
            "epoch_seconds": round(utc.timestamp(), 3),
            "note": (
                "'local' is the machine timezone "
                f"(UTC{_fmt_utc_offset(local.utcoffset())}); "
                "'utc' is UTC+0. Derived dates (tomorrow, next "
                "Monday, ...) can be computed from these values."
            ),
        }

    def _render(self, d: datetime) -> dict:
        return {
            "iso": d.isoformat(timespec="seconds"),
            "date": d.strftime("%Y-%m-%d"),
            "time": d.strftime("%H:%M:%S"),
            "year": d.year,
            "month": d.month,
            "day": d.day,
            "hour": d.hour,
            "minute": d.minute,
            "second": d.second,
            "weekday_en": self._WEEKDAY_EN[d.weekday()],
            "isoweekday": d.isoweekday(),
            "timezone_name": d.tzname() or "",
            "utc_offset": _fmt_utc_offset(d.utcoffset()),
        }


class ChatHistoryLocalHost:
    """Chat-history query core tools (v0.9.21.0+).

    Three READ-ONLY tools over the persisted chat sessions
    (``$QDRANT_RAG_HOME/sessions/<sid>.json`` via
    ``src.agent.session.SessionManager``):

      * ``list_chat_sessions``   — enumerate sessions, newest first
        (keyword title filter, scheduler-fire sessions hidden by
        default).
      * ``search_chat_history``  — case-insensitive keyword search
        across message content (optional ``sid`` scope + ``role``
        filter), returning ±context snippets.
      * ``get_chat_session``     — read one session's messages
        (most recent ``last_n``, each content truncated).

    Lets the LLM answer 「上次聊過什麼」 / 「我之前有問過 X 嗎」 /
    「幫我找昨天那個討論」 without the user scrolling the UI.

    Caps are built-in (no env vars) so a runaway history dump can
    never blow the context window: search returns at most 50 hits
    (soft default 10) with 280-char snippets (2000 hard cap) under
    an 8000-char total budget; get_chat_session returns at most 100
    messages with each content capped at 1200 chars (4000 hard cap).

    Scheduler-fired sessions (``sid`` starts with ``sched-``) are
    excluded from list/search by default — they are machine-written
    fire records, not user conversations; ``include_scheduled`` opts
    back in. They remain readable by id via ``get_chat_session``.
    """

    SERVER_NAME = "chat-history-local"
    _SCHED_PREFIX = "sched-"

    _DEFAULT_LIST_LIMIT = 20
    _MAX_LIST_LIMIT = 100
    _DEFAULT_SEARCH_LIMIT = 10
    _MAX_SEARCH_LIMIT = 50
    _DEFAULT_SNIPPET_CHARS = 280
    _MAX_SNIPPET_CHARS = 2000
    _TOTAL_SNIPPET_BUDGET = 8000
    _DEFAULT_LAST_N = 20
    _MAX_LAST_N = 100
    _DEFAULT_MSG_CHARS = 1200
    _MAX_MSG_CHARS = 4000

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="list_chat_sessions",
                description=(
                    "List saved chat sessions (newest first) with id / "
                    "title / message counts. Call this when the user "
                    "asks 「上次聊過什麼」 / 「歷史對話」 / 「之前的對話」 / "
                    "'what did we talk about' / 'list my chats' — then "
                    "use search_chat_history or get_chat_session to "
                    "drill in. Scheduler-fired sessions (sched-*) are "
                    "hidden unless include_scheduled=true."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": (
                                "Optional case-insensitive substring "
                                "filter on the session title."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "Max sessions to return (1-100, "
                                "default 20)."
                            ),
                        },
                        "include_scheduled": {
                            "type": "boolean",
                            "description": (
                                "Also include scheduler-fired sessions "
                                "(id starts with 'sched-'). Default false."
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list,
            ),
            LocalTool(
                name="search_chat_history",
                description=(
                    "Keyword search across past chat messages "
                    "(case-insensitive). Returns matching snippets "
                    "with session id / title / role / timestamp. Use "
                    "when the user asks 「我之前有問過 X 嗎」 / 「找一下"
                    "之前聊過的 Y」 / 'did we discuss X' / 'search my "
                    "chat history'. Then get_chat_session(sid) to "
                    "read the full conversation around a hit."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": (
                                "Required search keyword (case-"
                                "insensitive substring)."
                            ),
                        },
                        "sid": {
                            "type": "string",
                            "description": (
                                "Optional session id — search only "
                                "that session instead of all."
                            ),
                        },
                        "role": {
                            "type": "string",
                            "enum": ["any", "user", "assistant"],
                            "description": (
                                "Restrict matches to one role. "
                                "Default 'any'."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "Max matches returned (1-50, "
                                "default 10)."
                            ),
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": (
                                "Snippet length cap per match "
                                "(100-2000, default 280)."
                            ),
                        },
                        "include_scheduled": {
                            "type": "boolean",
                            "description": (
                                "Also search scheduler-fired sessions. "
                                "Default false."
                            ),
                        },
                    },
                    "required": ["keyword"],
                },
                server_name=self.SERVER_NAME,
                handler=self._search,
            ),
            LocalTool(
                name="get_chat_session",
                description=(
                    "Read one saved chat session's messages (most "
                    "recent ``last_n``, each content truncated). Use "
                    "after list_chat_sessions / search_chat_history "
                    "to re-read the full conversation the user refers "
                    "to. Session ids come from those tools; ids "
                    "starting with 'sched-' are scheduler fire records."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "sid": {
                            "type": "string",
                            "description": "Session id (required).",
                        },
                        "last_n": {
                            "type": "integer",
                            "description": (
                                "Return the most recent N messages "
                                "(1-100, default 20)."
                            ),
                        },
                        "max_msg_chars": {
                            "type": "integer",
                            "description": (
                                "Per-message content cap "
                                "(100-4000, default 1200)."
                            ),
                        },
                    },
                    "required": ["sid"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get,
            ),
        ]

    # ── handlers (LocalTool convention: (self, args: dict)) ────

    def _sessions(self):
        from src.agent.session import SessionManager

        return SessionManager()

    @staticmethod
    def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    @staticmethod
    def _snippet(content: str, keyword: str, max_chars: int) -> str:
        """±context window around the FIRST keyword occurrence."""
        lower = content.lower()
        pos = lower.find(keyword.lower())
        if pos == -1:
            frag = content[:max_chars]
            return frag + ("…" if len(content) > max_chars else "")
        half = max(0, (max_chars - len(keyword)) // 2)
        start = max(0, pos - half)
        end = pos + len(keyword) + half
        frag = content[start:end]
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(content) else ""
        return prefix + frag + suffix

    def _visible(self, sid: str, include_scheduled: bool) -> bool:
        return include_scheduled or not sid.startswith(self._SCHED_PREFIX)

    def _list(self, args: dict) -> dict:
        args = args or {}
        sm = self._sessions()
        keyword = str(args.get("keyword") or "").strip()
        limit = self._clamp_int(
            args.get("limit"), self._DEFAULT_LIST_LIMIT,
            1, self._MAX_LIST_LIMIT,
        )
        include_scheduled = bool(args.get("include_scheduled") or False)
        rows = []
        for s in sm.list():
            sid = str(s.get("id") or "")
            if not self._visible(sid, include_scheduled):
                continue
            title = str(s.get("title") or "")
            if keyword and keyword.lower() not in title.lower():
                continue
            rows.append({
                "id": sid,
                "title": title,
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
                "n_messages": s.get("n_messages"),
                "kind": (
                    "schedule-fire"
                    if sid.startswith(self._SCHED_PREFIX) else "chat"
                ),
            })
        total = len(rows)
        truncated = total > limit
        return {
            "keyword": keyword or None,
            "total_sessions": total,
            "returned": len(rows[:limit]),
            "truncated": truncated,
            "sessions": rows[:limit],
            "note": (
                "newest first. Use get_chat_session(sid) to read one; "
                "search_chat_history(keyword) to find matches."
                + (
                    " (truncated — raise 'limit' for more)"
                    if truncated else ""
                )
            ),
        }

    def _search(self, args: dict) -> dict:
        args = args or {}
        keyword = str(args.get("keyword") or "").strip()
        if not keyword:
            return {"error": "keyword is required for search_chat_history"}
        sid_scope = str(args.get("sid") or "").strip() or None
        role = str(args.get("role") or "any").strip().lower()
        if role not in ("any", "user", "assistant"):
            role = "any"
        limit = self._clamp_int(
            args.get("limit"), self._DEFAULT_SEARCH_LIMIT,
            1, self._MAX_SEARCH_LIMIT,
        )
        max_chars = self._clamp_int(
            args.get("max_chars"), self._DEFAULT_SNIPPET_CHARS,
            100, self._MAX_SNIPPET_CHARS,
        )
        include_scheduled = bool(args.get("include_scheduled") or False)

        sm = self._sessions()
        if sid_scope:
            sessions = [s for s in sm.list() if s.get("id") == sid_scope]
            if not sessions:
                return {"error": f"session {sid_scope!r} not found"}
        else:
            sessions = sm.list()

        matches: list[dict[str, Any]] = []
        budget_left = self._TOTAL_SNIPPET_BUDGET
        total_hits = 0
        truncated = False
        for meta in sessions:
            meta_id = str(meta.get("id") or "")
            if not sid_scope and not self._visible(
                meta_id, include_scheduled,
            ):
                continue
            sess = sm.load(meta_id)
            if sess is None:
                continue
            for i, m in enumerate(sess.messages or []):
                mrole = str(m.get("role") or "")
                if role != "any" and mrole != role:
                    continue
                content = str(m.get("content") or "")
                if keyword.lower() not in content.lower():
                    continue
                total_hits += 1
                if len(matches) >= limit or budget_left <= 0:
                    truncated = True
                    continue
                snippet = self._snippet(content, keyword, max_chars)
                budget_left -= min(len(snippet), max_chars)
                matches.append({
                    "session_id": meta_id,
                    "session_title": meta.get("title") or "",
                    "msg_index": i,
                    "role": mrole,
                    "ts": m.get("ts"),
                    "snippet": snippet,
                })
        return {
            "keyword": keyword,
            "scope": sid_scope or "all-sessions",
            "total_hits": total_hits,
            "returned": len(matches),
            "truncated": truncated,
            "matches": matches,
            "note": (
                "Use get_chat_session(sid) to read the full "
                "conversation around a hit."
                + (" (truncated — raise 'limit' for more)" if truncated
                   else "")
            ),
        }

    def _get(self, args: dict) -> dict:
        args = args or {}
        sid = str(args.get("sid") or "").strip()
        if not sid:
            return {"error": "sid is required for get_chat_session"}
        last_n = self._clamp_int(
            args.get("last_n"), self._DEFAULT_LAST_N,
            1, self._MAX_LAST_N,
        )
        max_chars = self._clamp_int(
            args.get("max_msg_chars"), self._DEFAULT_MSG_CHARS,
            100, self._MAX_MSG_CHARS,
        )
        sess = self._sessions().load(sid)
        if sess is None:
            return {"error": f"session {sid!r} not found"}
        msgs = list(sess.messages or [])
        picked = msgs[-last_n:] if last_n < len(msgs) else msgs
        out = []
        for m in picked:
            content = str(m.get("content") or "")
            if len(content) > max_chars:
                content = content[:max_chars] + f"…[truncated {max_chars}]"
            row = {"role": m.get("role"), "ts": m.get("ts"),
                   "content": content}
            sources = m.get("sources")
            if sources:
                row["sources"] = sources
            out.append(row)
        return {
            "id": sess.id,
            "title": sess.title,
            "created_at": sess.created_at,
            "updated_at": sess.updated_at,
            "n_messages": len(msgs),
            "returned": len(out),
            "messages": out,
            "note": (
                f"showing the most recent {len(out)} of {len(msgs)} "
                "messages (raise 'last_n' or narrow with "
                "search_chat_history for older parts)"
                if len(msgs) > len(out) else
                f"all {len(msgs)} messages shown"
            ),
        }


class MemoryLocalHost:
    """Qdrant long-term-memory tools (v0.9.59.0+).

    ``remember_info`` / ``search_memory`` / ``list_recent_memories`` /
    ``forget_memory`` / ``purge_memories`` / ``suggest_memory_cleanup``
    (v0.9.127.0 DRY-RUN maintenance report) / ``summarize_sessions``
    (v0.9.133.3 T+1 session-summary compactor) over the DEDICATED
    ``QDRANT_MEMORY_COLLECTION`` (default ``memory_docs``). Every
    point carries ``stored_at`` (ISO) + ``stored_at_epoch`` — the LLM
    must CITE the acquisition time when it uses a memory, and
    lifecycle management keys on it. Session Q/A pairs and
    whitelisted tool results are ingested automatically
    (SessionManager.save hook + Agent._run_tool_call);
    ``remember_info`` is the LLM's explicit 「記住 X」 channel;
    ``summarize_sessions`` is the per-session batch precipitation
    (kind=session-summary — AUTO-RECALLABLE, only task-result stays
    store-NOT-recall). Registered in-process by
    ``McpManager.from_config``; stdio server
    ``src/mcp_server/memory_server.py`` for external clients.
    """

    SERVER_NAME = "memory-local"

    def __init__(self, store: Any | None = None):
        self._store = store

    @property
    def store(self) -> Any:
        if self._store is None:
            from src.agent.memory_store import MemoryStore

            self._store = MemoryStore()
        return self._store

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="remember_info",
                description=(
                    "Store a piece of information into the LLM's "
                    "long-term memory (Qdrant). Use when the user "
                    "asks to remember something (「記住」/ 'remember "
                    "that') or when a fact is worth persisting. The "
                    "memory is timestamped (取得的時間) automatically "
                    "for lifecycle management and citation."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string",
                                 "description": "Information to "
                                 "remember (≤4000 chars)."},
                        "kind": {"type": "string",
                                 "description": "Memory kind: fact / "
                                 "preference / note. Default fact."},
                        "source": {"type": "string",
                                   "description": "Optional provenance "
                                   "(who/where it came from)."},
                    },
                    "required": ["text"],
                },
                server_name=self.SERVER_NAME,
                handler=self._remember_info,
            ),
            LocalTool(
                name="search_memory",
                description=(
                    "Semantic search over the long-term memory "
                    "(previous sessions, stored lookups, remembered "
                    "facts). Use BEFORE answering questions like "
                    "「之前查過什麼」「上次說過什麼」. Every hit "
                    "carries its 取得的時間 — cite it."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer",
                                  "description": "Default 5."},
                        "kind": {"type": "string",
                                 "description": "Optional filter: "
                                 "chat / query / fact / task-result "
                                 "(背景任務/plan 結論，v0.9.111)。"},
                    },
                    "required": ["query"],
                },
                server_name=self.SERVER_NAME,
                handler=self._search_memory,
            ),
            LocalTool(
                name="list_recent_memories",
                description=(
                    "List the most recent memories (newest first), "
                    "each with id / kind / source / 取得的時間."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer",
                                  "description": "Default 10."},
                        "kind": {"type": "string"},
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list_recent,
            ),
            LocalTool(
                name="forget_memory",
                description=(
                    "Delete ONE memory by its id (from search_memory / "
                    "list_recent_memories). Use for a user-requested "
                    "「忘掉這條」."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                    },
                    "required": ["id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._forget,
            ),
            LocalTool(
                name="purge_memories",
                description=(
                    "Lifecycle management: delete memories OLDER than "
                    "``older_than_s`` seconds (and/or of a kind). "
                    "Refuses an unfiltered wipe. ONLY call on an "
                    "explicit user request."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "older_than_s": {"type": "number",
                                         "description": "Age threshold "
                                         "in seconds (e.g. 2592000 = "
                                         "30 days)."},
                        "kind": {"type": "string"},
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._purge,
            ),
            LocalTool(
                name="suggest_memory_cleanup",
                description=(
                    "長期記憶維護報告（v0.9.127.0，DRY-RUN — 不做任何刪"
                    "除）：列出零召回命中且超齡的記憶（最舊在前）與清理量"
                    "；實際清理仍走 purge_memories / forget_memory（confirm"
                    " 閘）。維護食譜：排程定期打本報告 → 報給使用者 → 等核准"
                    "才清。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "older_than_s": {
                            "type": "number",
                            "description": "零命中且儲存超過 N 秒才列入"
                                           "（預設 2592000 = 30 天）",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "最多列幾筆（預設 50）",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._suggest_cleanup,
            ),
            LocalTool(
                name="summarize_sessions",
                description=(
                    "T+1 批次把近期 chat session 沉澱成長期記憶（"
                    "v0.9.133.3，kind=session-summary）：每個近一日修改"
                    "的 session 檔 LLM 壓成 1-3 行、帶 session_id 反向連結"
                    "寫入；冪等（已寫過的 sid 自動跳過）、寫入後逐條回讀"
                    "驗證。自動召回（LONG-TERM MEMORY）會包含 "
                    "session-summary —— 只有 task-result 維持 "
                    "store-NOT-recall。要先看會寫什麼 → dry_run=true。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "number",
                            "description": "回溯視窗（天，預設 1；回補可用 7）",
                        },
                        "llm": {
                            "type": "string",
                            "description": "模型 pin provider:model"
                                           "（預設 QDRANT_SESSION_SUMMARY_LLM"
                                           " 或 ollama:qwen2.5:7b）",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "只回報會寫什麼、不寫入",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._summarize_sessions,
            ),
        ]

    # ── handlers (LocalTool convention: (self, args: dict)) ────
    def _remember_info(self, args: dict) -> dict:
        from src.agent.memory_store import memory_enabled

        if not memory_enabled():
            return {"error": (
                "memory is disabled (QDRANT_MEMORY_ENABLED=off)"
            )}
        text = str(args.get("text") or "").strip()
        if not text:
            return {"error": "'text' is required"}
        out = self.store.remember(
            text,
            kind=str(args.get("kind") or "fact"),
            source=str(args.get("source") or ""),
        )
        return {"ok": True, **out}

    def _search_memory(self, args: dict) -> dict:
        from src.agent.memory_store import memory_enabled

        if not memory_enabled():
            return {"error": (
                "memory is disabled (QDRANT_MEMORY_ENABLED=off)"
            )}
        hits = self.store.search(
            str(args.get("query") or "").strip(),
            top_k=int(args.get("top_k") or 5),
            kind=str(args.get("kind")) if args.get("kind") else None,
        )
        return {
            "count": len(hits),
            "memories": [
                {
                    "id": h.get("id"),
                    "kind": h.get("kind"),
                    "source": h.get("source"),
                    "stored_at": h.get("stored_at"),
                    "hit_count": int(h.get("hit_count") or 0),
                    "last_hit_at": h.get("last_hit_at"),
                    "score": h.get("score"),
                    "content": str(h.get("content", ""))[:800],
                }
                for h in hits
            ],
            "note": "each memory carries 取得的時間 (stored_at) — "
                    "cite it when you use the information.",
        }

    def _list_recent(self, args: dict) -> dict:
        rows = self.store.recent(
            limit=int(args.get("limit") or 10),
            kind=str(args.get("kind")) if args.get("kind") else None,
        )
        out = {
            "count": len(rows),
            "memories": [
                {
                    "id": r.get("id"),
                    "kind": r.get("kind"),
                    "source": r.get("source"),
                    "stored_at": r.get("stored_at"),
                    "hit_count": int(r.get("hit_count") or 0),
                    "last_hit_at": r.get("last_hit_at"),
                    "content": str(r.get("content", ""))[:400],
                }
                for r in rows
            ],
        }
        # v0.9.111.0 — lifecycle usage snapshot (zero-hit cleanup
        # candidates). Best-effort: a store hiccup keeps the list path.
        try:
            out["usage"] = self.store.usage_report()
        except Exception as e:  # noqa: BLE001
            logger.debug("[memory-local] usage_report failed: %r", e)
        return out

    def _forget(self, args: dict) -> dict:
        ok = self.store.forget(str(args.get("id") or ""))
        return {"ok": True} if ok else {
            "error": f"memory id {args.get('id')!r} not deleted "
                     "(invalid or unknown id)",
        }

    def _purge(self, args: dict) -> dict:
        older = args.get("older_than_s")
        kind = str(args.get("kind")) if args.get("kind") else None
        try:
            older = float(older) if older is not None else 0.0
        except (TypeError, ValueError):
            older = 0.0
        if not (older > 0 or kind):
            return {"error": (
                "refuse: give older_than_s and/or kind (no unfiltered "
                "wipe)"
            )}
        deleted = self.store.purge(
            older_than_s=older if older > 0 else None, kind=kind,
        )
        return {"ok": True, "purged": deleted}

    def _suggest_cleanup(self, args: dict) -> dict:
        """v0.9.127.0 — DRY-RUN maintenance report. NEVER deletes:
        the candidates feed purge_memories / forget_memory which keep
        their own confirm gates."""
        try:
            a = args if isinstance(args, dict) else {}
            older = float(a.get("older_than_s") or 2_592_000.0)
            rep = self.store.cleanup_candidates(
                older_than_s=older,
                limit=int(a.get("limit") or 50),
            )
        except Exception as e:  # noqa: BLE001
            rep = {"error": repr(e)}
        out = {"ok": rep.get("ok", False), "usage_note": (
            "suggest only — purge_memories / forget_memory execute, "
            "both confirm-gated")}
        out.update(rep)
        return out

    def _summarize_sessions(self, args: dict) -> dict:
        """v0.9.133.3 — T+1 batch session-summary compactor (the
        UniiChat-inspired per-session precipitation). Best-effort: per
        session failures are reported, never raised."""
        try:
            a = args if isinstance(args, dict) else {}
            from src.agent.session_compactor import (
                summarize_sessions,
                session_summary_sink_enabled,
            )

            if not session_summary_sink_enabled():
                return {"error": (
                    "session-summary sink is disabled "
                    "(QDRANT_SESSION_SUMMARY_SINK=off)")}
            try:
                days = float(a.get("days") or 1.0)
            except (TypeError, ValueError):
                days = 1.0
            llm = str(a.get("llm") or "") or None
            dry_run = bool(a.get("dry_run"))
            return summarize_sessions(
                days=days, llm=llm, dry_run=dry_run,
            )
        except Exception as e:  # noqa: BLE001 — tool errors don't raise
            return {"error": repr(e)[:500]}


class MegaStockLocalHost:
    """Stock query + order tools for 兆豐 MegaAPI (v0.9.24.0+).

    Mock-first: ``MEGA_STOCK_MODE=mock`` (the default) runs a fully
    simulated in-memory book — quotes/klines are deterministic per
    symbol, and place_order/cancel_order NEVER touch the real
    MegaAPI. live mode requires Windows + the 兆豐 megaspeedy
    component + credentials, and order placement is additionally
    gated by MEGA_STOCK_TRADING_ENABLED=on AND a per-call
    confirm=True (development/testing NEVER places real orders —
    see src/mega_stock_tools.py).
    """

    SERVER_NAME = "stock-local"

    def __init__(self):
        from src.mega_stock_tools import MegaStockEngine

        self.engine = MegaStockEngine()
        # v0.9.35.0+ — stock query snapshots auto-persist to Qdrant.
        self._store: Any = None
        self._embedder: Any = None

    def _stock_ensure(self) -> tuple[Any, Any] | None:
        """Lazy-build StockStore + Embedder on first save. Returns
        None when Qdrant is unreachable — the query tool still works
        (auto-save degrades gracefully, error only logged)."""
        if self._store is None:
            try:
                from src.stock_store import StockStore

                self._store = StockStore()
            except Exception as e:
                logger.warning(
                    "[stock-local] StockStore unavailable: %r", e,
                )
                return None
        if self._embedder is None:
            try:
                from src.ingest.embedder import Embedder

                self._embedder = Embedder()
            except Exception as e:
                logger.warning(
                    "[stock-local] Embedder unavailable: %r", e,
                )
                return None
        return self._store, self._embedder

    def _save_query(self, tool: str, args: dict, result: Any) -> dict:
        """Auto-persist a successful query result into stock_docs.

        Never raises — a Qdrant outage must not break the query tool.
        Returns ``{"ok": True, "id": ...}`` or ``{"ok": False}``.
        """
        try:
            if not isinstance(result, dict) or result.get("error"):
                return {"ok": False}
            pair = self._stock_ensure()
            if not pair:
                return {"ok": False}
            store, embedder = pair
            from src.stock_store import build_summary_text

            text = build_summary_text(tool, args, result)
            vector = embedder.embed(text)
            pid = store.save_query(
                tool=tool, args=args, result=result,
                text=text, vector=vector,
            )
            return {"ok": True, "id": pid, "collection": store.collection}
        except Exception as e:
            logger.warning("[stock-local] qdrant save failed: %r", e)
            return {"ok": False}

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="stock_get_quote",
                description=(
                    "即時報價快照（開高低收、漲跌、成交量）。"
                    "mock 模式為確定性模擬資料；live 模式需要兆豐 "
                    "megaspeedy 元件。例：'台積電現在多少錢' / "
                    "'2330 quote'。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "股票代號，如 2330 / 0056。",
                        },
                    },
                    "required": ["symbol"],
                },
                server_name=self.SERVER_NAME,
                handler=self._quote,
            ),
            LocalTool(
                name="stock_get_quotes",
                description=(
                    "MULTIPLE tickers in ONE call（多標的批次報價）。"
                    "symbols=[\"2330\",\"2344\",...] — 一次取得全部，"
                    "優先於連續多次 stock_get_quote。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "股票代號清單（最多 10 檔）。",
                        },
                    },
                    "required": ["symbols"],
                },
                server_name=self.SERVER_NAME,
                handler=self._quotes_batch,
            ),
            LocalTool(
                name="stock_get_kline",
                description=(
                    "開高低收成交量 / K線資料（day | week | month，"
                    "最多 200 根）。mock 模式為確定性模擬。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string",
                                   "description": "股票代號。"},
                        "interval": {
                            "type": "string",
                            "enum": ["day", "week", "month"],
                            "description": "K線週期，預設 day。",
                        },
                        "bars": {
                            "type": "integer",
                            "description": "根數（1-200，預設 20）。",
                        },
                    },
                    "required": ["symbol"],
                },
                server_name=self.SERVER_NAME,
                handler=self._kline,
            ),
            LocalTool(
                name="stock_get_account_summary",
                description=(
                    "帳戶摘要：庫存、權益、未平倉委託數。mock 模式為"
                    "模擬帳戶 MOCK-ACCOUNT。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._account,
            ),
            LocalTool(
                name="stock_list_orders",
                description=(
                    "委託 / 成交查詢：status=all|open|filled|cancelled。"
                    "mock 模式只列出本程序內的模擬委託。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["all", "open", "filled",
                                     "cancelled"],
                            "description": "預設 all。",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "筆數上限（1-100，預設 20）。",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._orders,
            ),
            LocalTool(
                name="stock_place_order",
                description=(
                    "下單（mock 模式 = 純模擬，絕不送真實委託）。"
                    "side=buy|sell；order_type=limit(需 price)|market；"
                    "confirm=True 為防呆確認。真實下單（live）需 "
                    "MEGA_STOCK_MODE=live + MEGA_STOCK_TRADING_ENABLED"
                    "=on + 完成兆豐 API 聯調，且開發/測試階段一律使用 "
                    "mock。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string",
                                   "description": "股票代號。"},
                        "side": {
                            "type": "string", "enum": ["buy", "sell"],
                            "description": "買(buy) / 賣(sell)。",
                        },
                        "qty": {
                            "type": "integer",
                            "description": "股數（正整數，台股整股）。",
                        },
                        "price": {
                            "type": "number",
                            "description": (
                                "限價單價格（order_type=limit 時必填）。"
                            ),
                        },
                        "order_type": {
                            "type": "string",
                            "enum": ["limit", "market"],
                            "description": "預設 limit。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "防呆確認，必須為 true。",
                        },
                    },
                    "required": ["symbol", "side", "qty", "confirm"],
                },
                server_name=self.SERVER_NAME,
                handler=self._place,
            ),
            LocalTool(
                name="stock_cancel_order",
                description="刪單（取消未成交的模擬委託）。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "委託單號。",
                        },
                    },
                    "required": ["order_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._cancel,
            ),
            LocalTool(
                name="stock_search_history",
                description=(
                    "語意搜尋過去自動存進 Qdrant 的股票查詢快照"
                    "（stock_docs collection — 每次報價/K線/帳戶/委託"
                    "查詢自動存檔）。例：'上次查的台積電收盤價' / "
                    "'2330 上次的 K 線'。可選 tool / symbol 過濾。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "語意搜尋字串。",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "筆數（1-50，預設 5）。",
                        },
                        "tool": {
                            "type": "string",
                            "description": (
                                "過濾查詢類型（stock_get_quote 等，可選）。"
                            ),
                        },
                        "symbol": {
                            "type": "string",
                            "description": "過濾股票代號（可選）。",
                        },
                    },
                    "required": ["query"],
                },
                server_name=self.SERVER_NAME,
                handler=self._search_history,
            ),
            LocalTool(
                name="stock_list_history",
                description=(
                    "最近存進 Qdrant 的股票查詢快照（新→舊）— 日期、"
                    "tool、symbol 與摘要。可選 tool / symbol 過濾。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "筆數（1-200，預設 20）。",
                        },
                        "tool": {"type": "string",
                                 "description": "過濾查詢工具（可選）。"},
                        "symbol": {"type": "string",
                                   "description": "過濾股票代號（可選）。"},
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list_history,
            ),
        ]

    # ── handlers (LocalTool convention: (self, args: dict)) ────

    def _quote(self, args: dict) -> dict:
        args = args or {}
        result = self.engine.get_quote(str(args.get("symbol") or ""))
        result["qdrant"] = self._save_query(
            "stock_get_quote", args, result,
        )
        return result

    # v0.9.85.0 — batched quotes: ONE tool call covers multiple
    # symbols (the 13:30 scheduler turn burned 5 serial tool calls on
    # the same shape). Serial on purpose — the live engine shares one
    # session/subscription; mock is trivially fast.
    def _quotes_batch(self, args: dict) -> dict:
        args = args or {}
        symbols = args.get("symbols") or []
        if isinstance(symbols, str):
            symbols = [symbols]
        raw = (os.environ.get("QDRANT_STOCK_BATCH_MAX", "") or "").strip()
        try:
            cap = max(1, min(20, int(raw))) if raw else 10
        except ValueError:
            cap = 10
        symbols = _as_str_list(symbols, "symbols")
        if symbols is None:
            return {"error": "symbols must be a list of tickers (a single ticker string or a JSON/comma form also accepted)"}
        symbols = [str(s).strip() for s in symbols if str(s).strip()][:cap]
        if not symbols:
            return {"error": "symbols must be a non-empty list of tickers"}
        quotes: list[dict] = []
        for sym in symbols:
            try:
                r = self.engine.get_quote(sym)
                r["qdrant"] = self._save_query(
                    "stock_get_quote", {"symbol": sym}, r,
                )
                quotes.append({"symbol": sym, **r})
            except Exception as e:  # per-symbol containment
                quotes.append({"symbol": sym, "error": repr(e)[:200]})
        return {
            "count": len(quotes),
            "ok_count": sum(1 for q in quotes if not q.get("error")
                            and not (isinstance(q.get("quote"), dict)
                                     and q["quote"].get("error"))),
            "quotes": quotes,
        }

    def _kline(self, args: dict) -> dict:
        args = args or {}
        result = self.engine.get_kline(
            str(args.get("symbol") or ""),
            interval=str(args.get("interval") or "day"),
            bars=args.get("bars") or 20,
        )
        result["qdrant"] = self._save_query(
            "stock_get_kline", args, result,
        )
        return result

    def _account(self, args: dict) -> dict:
        result = self.engine.get_account_summary()
        result["qdrant"] = self._save_query(
            "stock_get_account_summary", {}, result,
        )
        return result

    def _orders(self, args: dict) -> dict:
        args = args or {}
        result = self.engine.list_orders(
            status=str(args.get("status") or "all"),
            limit=args.get("limit") or 20,
        )
        result["qdrant"] = self._save_query(
            "stock_list_orders", args, result,
        )
        return result

    def _place(self, args: dict) -> dict:
        args = args or {}
        return self.engine.place_order(
            symbol=str(args.get("symbol") or ""),
            side=str(args.get("side") or ""),
            qty=args.get("qty") or 0,
            price=args.get("price"),
            order_type=str(args.get("order_type") or "limit"),
            confirm=bool(args.get("confirm") or False),
        )

    def _cancel(self, args: dict) -> dict:
        args = args or {}
        return self.engine.cancel_order(str(args.get("order_id") or ""))

    # ── v0.9.35.0+ — Qdrant snapshot history tools ──────────────

    def _search_history(self, args: dict) -> dict:
        args = args or {}
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "'query' is required"}
        top_k = max(1, min(int(args.get("top_k") or 5), 50))
        tool = str(args.get("tool") or "") or None
        symbol = str(args.get("symbol") or "") or None
        pair = self._stock_ensure()
        if not pair:
            return {"error": (
                "stock history store unavailable (Qdrant down or "
                "embedder not configured)"
            )}
        store, embedder = pair
        try:
            vector = embedder.embed(query)
            hits = store.search(
                vector, top_k=top_k, tool=tool, symbol=symbol,
            )
            return {
                "query": query, "tool": tool, "symbol": symbol,
                "count": len(hits), "hits": hits,
            }
        except Exception as e:
            return {"error": f"stock history search failed: {e!r}"}

    def _list_history(self, args: dict) -> dict:
        args = args or {}
        limit = max(1, min(int(args.get("limit") or 20), 200))
        tool = str(args.get("tool") or "") or None
        symbol = str(args.get("symbol") or "") or None
        pair = self._stock_ensure()
        if not pair:
            return {"error": (
                "stock history store unavailable (Qdrant down or "
                "embedder not configured)"
            )}
        store, _embedder = pair
        try:
            rows = store.recent(limit=limit, tool=tool, symbol=symbol)
            for r in rows:
                r.pop("data", None)  # keep the list light
            return {
                "tool": tool, "symbol": symbol,
                "count": len(rows), "recent": rows,
                "total": store.count_points(tool=tool, symbol=symbol),
            }
        except Exception as e:
            return {"error": f"stock history list failed: {e!r}"}


# ── v0.9.66.0 — data-driven tool specs for the 48 NEW tools across
# the 12 remaining HWS services (args map 1:1 to the HicloudEngine
# kwargs; the original 8 v0.9.64 tools stay hand-written in
# _base_tools()).

_HICLOUD_S = {"type": "string"}


def _hicloud_sa(desc=""):
    d = {"type": "array", "items": {"type": "string"}}
    if desc:
        d["description"] = desc
    return d


def _hicloud_ia(desc=""):
    d = {"type": "array", "items": {"type": "integer"}}
    if desc:
        d["description"] = desc
    return d


def _hicloud_b(desc=""):
    d = {"type": "boolean"}
    if desc:
        d["description"] = desc
    return d


def _hicloud_schema(req=None, **props):
    schema = {"type": "object", "properties": props}
    if req:
        schema["required"] = list(req)
    return schema


_HICLOUD_TOOL_SPECS: list[dict] = [
    # ── VM completions ──
    {
        "name": "hicloud_modify_instance_attribute",
        "desc": ("原機升降級 / 供裝監控（modifyInstanceAttribute）。帶 "
                 "instance_type 時 monitoring_enabled 被忽略。會影響計費 — "
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(
            ["instance_id", "confirm"],
            instance_id=_HICLOUD_S, instance_type=_HICLOUD_S,
            monitoring_enabled=_hicloud_b(), confirm=_hicloud_b()),
        "method": "modify_instance_attribute",
    },
    {
        "name": "hicloud_restore_instance",
        "desc": "由快照復原雲伺服器（restoreInstance）。需要 confirm=true。",
        "schema": _hicloud_schema(["snapshot_id", "confirm"],
                                  snapshot_id=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "restore_instance",
    },
    # ── CloudWatch ──
    {
        "name": "hicloud_get_instance_stats",
        "desc": ("VM 監控數據（getInstancesStats — CPU/DISK/Network 近"
                 "即時）；mock 為模擬數據。"),
        "schema": _hicloud_schema(["instance_ids"],
                                  instance_ids=_hicloud_sa(
                                      "instanceId 清單")),
        "method": "get_instance_stats",
    },
    # ── Log ──
    {
        "name": "hicloud_log_list",
        "desc": ("查詢 hicloud 平台操作 Log 紀錄（listLog；startDate 為 "
                 "ISO 8601 UTC，如 2024-05-01T00:00:00Z）。"),
        "schema": _hicloud_schema(["start_date"], start_date=_HICLOUD_S),
        "method": "log_list",
    },
    # ── EBS ──
    {
        "name": "hicloud_volume_create",
        "desc": ("申請儲存空間（createVolume — 產生計費！radix 1-20，"
                 "每單位 100GB；同一 VM 最多 5 顆/2TB）。"
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(
            ["instance_id", "radix", "confirm"],
            instance_id=_HICLOUD_S,
            radix={"type": "integer",
                   "description": "1-20（每單位 100GB）"},
            volume_name=_HICLOUD_S, confirm=_hicloud_b()),
        "method": "volume_create",
    },
    {
        "name": "hicloud_volume_attach",
        "desc": "掛載儲存空間到雲伺服器（attachVolume）。需要 confirm=true。",
        "schema": _hicloud_schema(
            ["volume_id", "instance_id", "confirm"],
            volume_id=_HICLOUD_S, instance_id=_HICLOUD_S,
            confirm=_hicloud_b()),
        "method": "volume_attach",
    },
    {
        "name": "hicloud_volume_detach",
        "desc": "卸載儲存空間（detachVolume）。需要 confirm=true。",
        "schema": _hicloud_schema(["volume_id", "confirm"],
                                  volume_id=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "volume_detach",
    },
    {
        "name": "hicloud_volume_delete",
        "desc": "刪除儲存空間（deleteVolume，多值）。需要 confirm=true。",
        "schema": _hicloud_schema(["volume_ids", "confirm"],
                                  volume_ids=_hicloud_sa("volumeId 清單"),
                                  confirm=_hicloud_b()),
        "method": "volume_delete",
    },
    {
        "name": "hicloud_volume_list",
        "desc": ("列出儲存空間（describeVolumes；可 volume_ids / "
                 "order_uuids / vpc_bsid 過濾，不帶 = 全部 CaaS）。"),
        "schema": _hicloud_schema(None, volume_ids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa(),
                                  vpc_bsid=_HICLOUD_S),
        "method": "volume_list",
    },
    # ── Image completions ──
    {
        "name": "hicloud_image_create",
        "desc": "由線上雲伺服器建立範本（createImage）。需要 confirm=true。",
        "schema": _hicloud_schema(["instance_id", "confirm"],
                                  instance_id=_HICLOUD_S,
                                  name=_HICLOUD_S,
                                  description=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "image_create",
    },
    {
        "name": "hicloud_image_delete",
        "desc": "刪除範本（deleteImage，多值）。需要 confirm=true。",
        "schema": _hicloud_schema(["image_ids", "confirm"],
                                  image_ids=_hicloud_sa("imageId 清單"),
                                  confirm=_hicloud_b()),
        "method": "image_delete",
    },
    {
        "name": "hicloud_image_modify",
        "desc": "異動範本名稱/描述（modifyImageAttribute）。",
        "schema": _hicloud_schema(["image_id", "confirm"],
                                  image_id=_HICLOUD_S, name=_HICLOUD_S,
                                  description=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "image_modify",
    },
    # ── SLB ──
    {
        "name": "hicloud_slb_create",
        "desc": ("建立負載平衡（createLoadBalancerPolicy — 產生計費！）。"
                 "CaaS 形狀：instance_ids + ports；CVPC 形狀：vpc_bsid + "
                 "load_balancer_type(0簡易/1進階規則/2進階設備) + "
                 "ports_info/rips_info。需要 confirm=true。"),
        "schema": _hicloud_schema(
            ["confirm"], instance_ids=_hicloud_sa(), ports=_hicloud_ia(),
            name=_HICLOUD_S, region_id=_HICLOUD_S, vpc_bsid=_HICLOUD_S,
            load_balancer_type=_HICLOUD_S, ports_info=_hicloud_sa(),
            rips_info=_hicloud_sa(), real_server_weights=_hicloud_sa(),
            user_assign_ip=_HICLOUD_S, route_direction=_HICLOUD_S,
            ip_subnet_bsid=_HICLOUD_S, confirm=_hicloud_b()),
        "method": "slb_create",
    },
    {
        "name": "hicloud_slb_delete",
        "desc": "刪除負載平衡（deleteLoadBalancerPolicy，多值）。"
                "需要 confirm=true。",
        "schema": _hicloud_schema(["load_balancer_ids", "confirm"],
                                  load_balancer_ids=_hicloud_sa(
                                      "loadBalancerId 清單"),
                                  confirm=_hicloud_b()),
        "method": "slb_delete",
    },
    {
        "name": "hicloud_slb_list",
        "desc": ("列出負載平衡（describeLoadBalancerPolicies；可 "
                 "load_balancer_ids / order_uuids / vpc_bsid / "
                 "load_balancer_type / business_tag 過濾）。"),
        "schema": _hicloud_schema(None, load_balancer_ids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa(),
                                  vpc_bsid=_HICLOUD_S,
                                  load_balancer_type=_HICLOUD_S,
                                  business_tag=_HICLOUD_S),
        "method": "slb_list",
    },
    {
        "name": "hicloud_slb_vip_health",
        "desc": "查詢 CVPC 負載平衡 VIP 健康度（querySlbVIPHealthStatus）。",
        "schema": _hicloud_schema(["load_balancer_ids"],
                                  load_balancer_ids=_hicloud_sa(
                                      "loadBalancerId 清單")),
        "method": "slb_vip_health",
    },
    {
        "name": "hicloud_slb_replace",
        "desc": ("異動負載平衡設定（replaceLoadBalancerPolicy）。CaaS 帶 "
                 "instance_ids+ports；CVPC 帶 ports_info+rips_info。"
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(
            ["load_balancer_id", "confirm"], load_balancer_id=_HICLOUD_S,
            instance_ids=_hicloud_sa(), ports=_hicloud_ia(),
            ports_info=_hicloud_sa(), rips_info=_hicloud_sa(),
            confirm=_hicloud_b()),
        "method": "slb_replace",
    },
    # ── Subnets ──
    {
        "name": "hicloud_subnet_create",
        "desc": ("建立子網段（createSubnet — 產生計費！CVPC 帶 vpc_bsid；"
                 "CaaS 帶 intranet_gateway_bsid；ip_version 預設 IPv4）。"
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(
            ["subnet", "confirm"], subnet=_HICLOUD_S, vpc_bsid=_HICLOUD_S,
            intranet_gateway_bsid=_HICLOUD_S, subnet_name=_HICLOUD_S,
            ip_version=_HICLOUD_S, confirm=_hicloud_b()),
        "method": "subnet_create",
    },
    {
        "name": "hicloud_subnet_delete",
        "desc": "刪除子網段（deleteSubnet，多值）。需要 confirm=true。",
        "schema": _hicloud_schema(["subnet_bsids", "confirm"],
                                  subnet_bsids=_hicloud_sa(
                                      "subnetBusinessServiceId 清單"),
                                  confirm=_hicloud_b()),
        "method": "subnet_delete",
    },
    {
        "name": "hicloud_subnet_list",
        "desc": ("列出子網段（describeSubnets；可 subnet_bsids / "
                 "order_uuids / business_tag / vpc_bsid 過濾）。"),
        "schema": _hicloud_schema(None, subnet_bsids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa(),
                                  business_tag=_HICLOUD_S,
                                  vpc_bsid=_HICLOUD_S),
        "method": "subnet_list",
    },
    {
        "name": "hicloud_subnet_modify",
        "desc": "異動子網段名稱/描述（modifySubnetAttribute）。",
        "schema": _hicloud_schema(["subnet_bsid", "confirm"],
                                  subnet_bsid=_HICLOUD_S, name=_HICLOUD_S,
                                  description=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "subnet_modify",
    },
    # ── Network ACL（防火牆） ──
    {
        "name": "hicloud_acl_create",
        "desc": ("建立防火牆（createNetworkAcl — 產生計費！）。CaaS 帶 "
                 "instance_id + 舊式 policy 字串（例："
                 "'1;;1;;1.0.0.0/8;;accept;;any'）；CVPC 帶 vpc_bsid + "
                 "新式 policy 字串（subnet$/fqdn$/iprange$…）。"
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(["policy_strings", "confirm"],
                                  policy_strings=_hicloud_sa(
                                      "防火牆組態字串清單（多值）"),
                                  instance_id=_HICLOUD_S,
                                  vpc_bsid=_HICLOUD_S,
                                  network_acl_name=_HICLOUD_S,
                                  ip_version=_HICLOUD_S,
                                  business_tag=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "acl_create",
    },
    {
        "name": "hicloud_acl_delete",
        "desc": "刪除防火牆（deleteNetworkAcl，多值）。需要 confirm=true。",
        "schema": _hicloud_schema(["network_acl_ids", "confirm"],
                                  network_acl_ids=_hicloud_sa(
                                      "networkAclId 清單"),
                                  confirm=_hicloud_b()),
        "method": "acl_delete",
    },
    {
        "name": "hicloud_acl_list",
        "desc": ("列出防火牆（describeNetworkAcls；可 network_acl_ids / "
                 "order_uuids / business_tag 過濾）。"),
        "schema": _hicloud_schema(None, network_acl_ids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa(),
                                  business_tag=_HICLOUD_S),
        "method": "acl_list",
    },
    {
        "name": "hicloud_acl_replace",
        "desc": ("異動防火牆組態（replaceNetworkAcl — 整組替換）。"
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(["network_acl_id", "policy_strings",
                                   "confirm"],
                                  network_acl_id=_HICLOUD_S,
                                  policy_strings=_hicloud_sa(
                                      "防火牆組態字串清單"),
                                  ip_version=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "acl_replace",
    },
    # ── VPC ──
    {
        "name": "hicloud_vpc_create",
        "desc": ("建立 CVPC 軟體資料中心（createVpc — 產生計費！）。"
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(["vpc_name", "confirm"],
                                  vpc_name=_HICLOUD_S,
                                  region_id=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "vpc_create",
    },
    {
        "name": "hicloud_vpc_delete",
        "desc": "刪除 CVPC（deleteVpc，多值）。需要 confirm=true。",
        "schema": _hicloud_schema(["vpc_bsids", "confirm"],
                                  vpc_bsids=_hicloud_sa(
                                      "vpcBusinessServiceId 清單"),
                                  confirm=_hicloud_b()),
        "method": "vpc_delete",
    },
    {
        "name": "hicloud_vpc_list",
        "desc": "列出 CVPC（describeVpcs；可 vpc_bsids / order_uuids 過濾）。",
        "schema": _hicloud_schema(None, vpc_bsids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa()),
        "method": "vpc_list",
    },
    {
        "name": "hicloud_vpc_modify",
        "desc": "異動 CVPC 名稱/描述（modifyVpcAttribute）。",
        "schema": _hicloud_schema(["vpc_bsid", "confirm"],
                                  vpc_bsid=_HICLOUD_S, name=_HICLOUD_S,
                                  description=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "vpc_modify",
    },
    # ── Internet Gateway + Internet IP ──
    {
        "name": "hicloud_igw_create",
        "desc": ("建立 CVPC 網際網路連線（createInternetGateway — 產生"
                 "計費！）。需要 confirm=true。"),
        "schema": _hicloud_schema(["vpc_bsid", "confirm"],
                                  vpc_bsid=_HICLOUD_S, name=_HICLOUD_S,
                                  ip_version=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "igw_create",
    },
    {
        "name": "hicloud_igw_delete",
        "desc": ("刪除網際網路連線（deleteInternetGateway，多值 — 需相同 "
                 "IP Version）。需要 confirm=true。"),
        "schema": _hicloud_schema(["internet_bsids", "confirm"],
                                  internet_bsids=_hicloud_sa(
                                      "internetBusinessServiceId 清單"),
                                  ip_version=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "igw_delete",
    },
    {
        "name": "hicloud_igw_list",
        "desc": ("列出網際網路連線（describeInternetGateways；vpc_bsids / "
                 "internet_bsids / order_uuids / ip_version 擇一）。"),
        "schema": _hicloud_schema(None, vpc_bsids=_hicloud_sa(),
                                  internet_bsids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa(),
                                  ip_version=_HICLOUD_S),
        "method": "igw_list",
    },
    {
        "name": "hicloud_internet_ip_create",
        "desc": "建立網際網路連線 IP 服務（createInternetIp）。"
                "需要 confirm=true。",
        "schema": _hicloud_schema(["internet_bsid", "confirm"],
                                  internet_bsid=_HICLOUD_S,
                                  name=_HICLOUD_S, confirm=_hicloud_b()),
        "method": "internet_ip_create",
    },
    {
        "name": "hicloud_internet_ip_delete",
        "desc": ("刪除網際網路 IP 服務（deleteInternetIp，多值）。"
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(["internet_ip_bsids", "confirm"],
                                  internet_ip_bsids=_hicloud_sa(
                                      "internetIpBusinessServiceId 清單"),
                                  confirm=_hicloud_b()),
        "method": "internet_ip_delete",
    },
    {
        "name": "hicloud_internet_ip_list",
        "desc": "列出網際網路 IP 服務（describeInternetIps）。",
        "schema": _hicloud_schema(None, internet_ip_bsids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa(),
                                  internet_bsid=_HICLOUD_S),
        "method": "internet_ip_list",
    },
    # ── Intranet ──
    {
        "name": "hicloud_intranet_gw_create",
        "desc": ("建立內網路由閘道器（createIntranetGateway — 產生計費！"
                 "region_id 如 region-tw-1）。需要 confirm=true。"),
        "schema": _hicloud_schema(["region_id", "confirm"],
                                  region_id=_HICLOUD_S, name=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "intranet_gw_create",
    },
    {
        "name": "hicloud_intranet_gw_delete",
        "desc": ("刪除內網路由閘道器（deleteIntranetGateway，多值）。"
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(["intranet_gateway_bsids", "confirm"],
                                  intranet_gateway_bsids=_hicloud_sa(
                                      "intranetGatewayBusinessServiceId"
                                      " 清單"),
                                  confirm=_hicloud_b()),
        "method": "intranet_gw_delete",
    },
    {
        "name": "hicloud_intranet_gw_list",
        "desc": "列出內網路由閘道器（describeIntranetGateways）。",
        "schema": _hicloud_schema(None,
                                  intranet_gateway_bsids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa()),
        "method": "intranet_gw_list",
    },
    {
        "name": "hicloud_intranet_gw_modify",
        "desc": "異動內網路由閘道器（modifyIntranetGatewayAttribute）。",
        "schema": _hicloud_schema(["intranet_gateway_bsid", "confirm"],
                                  intranet_gateway_bsid=_HICLOUD_S,
                                  name=_HICLOUD_S, description=_HICLOUD_S,
                                  confirm=_hicloud_b()),
        "method": "intranet_gw_modify",
    },
    {
        "name": "hicloud_intranet_link_create",
        "desc": "接取 VPC 與內網路由閘道器（createIntranetLink）。"
                "需要 confirm=true。",
        "schema": _hicloud_schema(["vpc_bsid", "intranet_gateway_bsid",
                                   "confirm"],
                                  vpc_bsid=_HICLOUD_S,
                                  intranet_gateway_bsid=_HICLOUD_S,
                                  name=_HICLOUD_S, confirm=_hicloud_b()),
        "method": "intranet_link_create",
    },
    {
        "name": "hicloud_intranet_link_delete",
        "desc": ("刪除內網接取服務（deleteIntranetLink，多值）。"
                 "需要 confirm=true。"),
        "schema": _hicloud_schema(["intranet_link_bsids", "confirm"],
                                  intranet_link_bsids=_hicloud_sa(
                                      "intranetLinkBusinessServiceId 清單"),
                                  confirm=_hicloud_b()),
        "method": "intranet_link_delete",
    },
    {
        "name": "hicloud_intranet_link_list",
        "desc": ("列出內網接取服務（describeIntranetLinks；可 link_bsids / "
                 "order_uuids / gw_bsid / vpc_bsid 過濾）。"),
        "schema": _hicloud_schema(None, intranet_link_bsids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa(),
                                  gw_bsid=_HICLOUD_S, vpc_bsid=_HICLOUD_S),
        "method": "intranet_link_list",
    },
    # ── NAT + Port Forwarding ──
    {
        "name": "hicloud_nat_create",
        "desc": "建立網路地址轉換（createNat — 產生計費！）。"
                "需要 confirm=true。",
        "schema": _hicloud_schema(
            ["vpc_bsid", "original_ip", "translated_ip", "confirm"],
            vpc_bsid=_HICLOUD_S, original_ip=_HICLOUD_S,
            translated_ip=_HICLOUD_S, nat_name=_HICLOUD_S,
            confirm=_hicloud_b()),
        "method": "nat_create",
    },
    {
        "name": "hicloud_nat_delete",
        "desc": "刪除 NAT（deleteNat，多值）。需要 confirm=true。",
        "schema": _hicloud_schema(["nat_bsids", "confirm"],
                                  nat_bsids=_hicloud_sa(
                                      "natBusinessServiceId 清單"),
                                  confirm=_hicloud_b()),
        "method": "nat_delete",
    },
    {
        "name": "hicloud_nat_list",
        "desc": ("列出 NAT（describeNats；需 nat_bsids / order_uuids 或 "
                 "vpc_bsid — 全空不回傳資料）。"),
        "schema": _hicloud_schema(None, nat_bsids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa(),
                                  vpc_bsid=_HICLOUD_S),
        "method": "nat_list",
    },
    {
        "name": "hicloud_pfwd_create",
        "desc": "建立連接埠轉換（createPortFwd — 產生計費！）。"
                "需要 confirm=true。",
        "schema": _hicloud_schema(
            ["vpc_bsid", "original_ip", "translated_ip", "original_port",
             "translated_port", "confirm"],
            vpc_bsid=_HICLOUD_S, original_ip=_HICLOUD_S,
            translated_ip=_HICLOUD_S,
            original_port={"type": "integer"},
            translated_port={"type": "integer"},
            port_fwd_name=_HICLOUD_S, confirm=_hicloud_b()),
        "method": "pfwd_create",
    },
    {
        "name": "hicloud_pfwd_delete",
        "desc": "刪除連接埠轉換（deletePortFwd，多值）。需要 confirm=true。",
        "schema": _hicloud_schema(["port_fwd_bsids", "confirm"],
                                  port_fwd_bsids=_hicloud_sa(
                                      "portFwdBusinessServiceId 清單"),
                                  confirm=_hicloud_b()),
        "method": "pfwd_delete",
    },
    {
        "name": "hicloud_pfwd_list",
        "desc": ("列出連接埠轉換（describePortFwds；需 port_fwd_bsids / "
                 "order_uuids 或 vpc_bsid）。"),
        "schema": _hicloud_schema(None, port_fwd_bsids=_hicloud_sa(),
                                  order_uuids=_hicloud_sa(),
                                  vpc_bsid=_HICLOUD_S),
        "method": "pfwd_list",
    },
]

class HicloudLocalHost:
    """hicloud HWS (中華電信 CaaS/CVPC) 雲服務 tools (v0.9.64.0+).

    Eight tools over src/hicloud_tools.py — mock-first (HICLOUD_MODE
    defaults to mock: a fully simulated in-memory VM book, NO HTTP).
    Read tools (list_instances / describe_images / list_instance_types)
    need no confirm; lifecycle writes (run/start/stop/reboot/terminate)
    require per-call confirm=true, and live writes additionally
    HICLOUD_WRITES_ENABLED=on.
    """

    SERVER_NAME = "hicloud-local"

    def __init__(self):
        from src.hicloud_tools import HicloudEngine

        self.engine = HicloudEngine()

    def _engine_call(self, method: str, args: dict) -> dict:
        """Generic bridge — tool args map 1:1 to the engine kwargs
        (both camel-free snake_case per the v0.9.64 host schema
        convention). Unknown args are passed through so engine
        signatures stay the single source of truth."""
        kwargs = {}
        for k, v in (args or {}).items():
            kwargs[k] = v
        return getattr(self.engine, method)(**kwargs)

    def list_tools(self) -> list[LocalTool]:
        tools = self._base_tools()
        for spec in _HICLOUD_TOOL_SPECS:
            tools.append(LocalTool(
                name=spec["name"], description=spec["desc"],
                input_schema=spec["schema"],
                server_name=self.SERVER_NAME,
                handler=self._make_handler(spec["method"]),
            ))
        return tools

    @staticmethod
    def _make_handler(method: str):
        def _h(host: "HicloudLocalHost", args: dict) -> dict:
            return host._engine_call(method, args)
        return _h

    def _base_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="hicloud_list_instances",
                description=(
                    "列出 hicloud 雲伺服器（VM）— 狀態/名稱/IP/區域。"
                    "可選 instance_ids / order_uuids 過濾（多值）。"
                    "例：'我的 hicloud VM 有哪些' / 'cloud server list'。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "instance_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "instanceId 清單（可選）。",
                        },
                        "order_uuids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "訂單編號清單（可選）。",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list_instances,
            ),
            LocalTool(
                name="hicloud_describe_images",
                description=(
                    "列出 hicloud 範本（公用 hi-* / 用戶 BT*）— 建立 VM "
                    "前查 image_id 用。可選 image_ids 過濾。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "image_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "範本識別碼清單（可選）。",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._describe_images,
            ),
            LocalTool(
                name="hicloud_list_instance_types",
                description=(
                    "hicloud 運算規格參考表（runInstances 的 "
                    "instanceType 合法值；可選 os 過濾 linux/windows）。"
                    "建立 VM 前先查合法規格。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "os": {
                            "type": "string",
                            "enum": ["linux", "windows"],
                            "description": "作業系統過濾（可選）。",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list_instance_types,
            ),
            LocalTool(
                name="hicloud_run_instances",
                description=(
                    "建立雲伺服器（runInstances — 會產生計費資源！）。"
                    "三種來源：公用範本 (image_id+instance_type)、"
                    "雲伺服器複製 (clone_instance_id)、用戶範本複製 "
                    "(image_id=BT*)。必須在使用者明確指示下才可 "
                    "confirm=true；mock 模式為模擬。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "image_id": {
                            "type": "string",
                            "description": (
                                "範本識別碼（公用 hi-* / 用戶範本 BT*）。"
                            ),
                        },
                        "instance_type": {
                            "type": "string",
                            "description": (
                                "運算規格（公用範本必填；先用 "
                                "hicloud_list_instance_types 查）。"
                            ),
                        },
                        "clone_instance_id": {
                            "type": "string",
                            "description": "來源 VM id（複製模式，可選）。",
                        },
                        "instance_name": {
                            "type": "string",
                            "description": "雲伺服器名稱（可選）。",
                        },
                        "count": {
                            "type": "integer",
                            "description": "數量 1-10（預設 1）。",
                        },
                        "monitoring_enabled": {
                            "type": "boolean",
                            "description": "供裝監控（預設 false）。",
                        },
                        "vpc_business_service_id": {
                            "type": "string",
                            "description": (
                                "CVPC 軟體資料中心服務識別碼（可選）。"
                            ),
                        },
                        "region_id": {
                            "type": "string",
                            "description": "區域機房代碼（可選）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "必須 true 才會執行（防意外建立計費資源）。"
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._run_instances,
            ),
            LocalTool(
                name="hicloud_start_instances",
                description=(
                    "開機一組以上雲伺服器（startInstances — 非同步）。"
                    "需要 confirm=true；回 statusMap，稍後可用 "
                    "hicloud_list_instances 確認。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "instance_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "instanceId 清單（必填）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true 才會執行。",
                        },
                    },
                    "required": ["instance_ids"],
                },
                server_name=self.SERVER_NAME,
                handler=self._start_instances,
            ),
            LocalTool(
                name="hicloud_stop_instances",
                description=(
                    "關機一組以上雲伺服器（stopInstances — 非同步）。"
                    "需要 confirm=true。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "instance_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "instanceId 清單（必填）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true 才會執行。",
                        },
                    },
                    "required": ["instance_ids"],
                },
                server_name=self.SERVER_NAME,
                handler=self._stop_instances,
            ),
            LocalTool(
                name="hicloud_reboot_instances",
                description=(
                    "重啟一組以上雲伺服器（rebootInstances — 非同步）。"
                    "需要 confirm=true。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "instance_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "instanceId 清單（必填）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true 才會執行。",
                        },
                    },
                    "required": ["instance_ids"],
                },
                server_name=self.SERVER_NAME,
                handler=self._reboot_instances,
            ),
            LocalTool(
                name="hicloud_terminate_instances",
                description=(
                    "刪除一組以上雲伺服器（terminateInstances — 不可逆！"
                    "若有子產品（EBS/SLB/防火牆/VPN）綁定會失敗，需先移除"
                    "）。需要 confirm=true。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "instance_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "instanceId 清單（必填）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true 才會執行。",
                        },
                    },
                    "required": ["instance_ids"],
                },
                server_name=self.SERVER_NAME,
                handler=self._terminate_instances,
            ),
        ]

    # ── handlers (LocalTool convention: (self, args: dict)) ────

    def _list_instances(self, args: dict) -> dict:
        args = args or {}
        return self.engine.list_instances(
            instance_ids=args.get("instance_ids") or None,
            order_uuids=args.get("order_uuids") or None,
        )

    def _describe_images(self, args: dict) -> dict:
        args = args or {}
        return self.engine.describe_images(
            image_ids=args.get("image_ids") or None,
        )

    def _list_instance_types(self, args: dict) -> dict:
        args = args or {}
        return self.engine.list_instance_types(
            os_filter=str(args.get("os") or "") or None,
        )

    def _run_instances(self, args: dict) -> dict:
        args = args or {}
        return self.engine.run_instances(
            image_id=str(args.get("image_id") or ""),
            instance_type=str(args.get("instance_type") or ""),
            clone_instance_id=str(args.get("clone_instance_id") or ""),
            instance_name=str(args.get("instance_name") or ""),
            count=args.get("count") or 1,
            monitoring_enabled=bool(args.get("monitoring_enabled")),
            vpc_business_service_id=str(
                args.get("vpc_business_service_id") or ""),
            region_id=str(args.get("region_id") or ""),
            confirm=args.get("confirm") or False,
        )

    def _start_instances(self, args: dict) -> dict:
        args = args or {}
        return self.engine.start_instances(
            list(args.get("instance_ids") or []),
            confirm=args.get("confirm") or False,
        )

    def _stop_instances(self, args: dict) -> dict:
        args = args or {}
        return self.engine.stop_instances(
            list(args.get("instance_ids") or []),
            confirm=args.get("confirm") or False,
        )

    def _reboot_instances(self, args: dict) -> dict:
        args = args or {}
        return self.engine.reboot_instances(
            list(args.get("instance_ids") or []),
            confirm=args.get("confirm") or False,
        )

    def _terminate_instances(self, args: dict) -> dict:
        args = args or {}
        return self.engine.terminate_instances(
            list(args.get("instance_ids") or []),
            confirm=args.get("confirm") or False,
        )


class CredLocalHost:
    """Central credential-set tools (v0.9.39.0+).

    Five tools over src/credential_store.py — the SAME store the GUI
    Credentials tab and the ``cli.py creds`` commands manage:
    cred_add / cred_list / cred_get / cred_remove / cred_resolve.
    Every output is masked; ``cred_resolve`` is what services call
    to turn a named set into (username, password) — ONVIF favorites
    reference sets via their ``cred_set`` field.
    """

    SERVER_NAME = "cred-local"

    def __init__(self):
        self._cred_store: Any = None

    def _store(self) -> Any:
        if self._cred_store is None:
            from src.credential_store import CredentialStore

            self._cred_store = CredentialStore()
        return self._cred_store

    # ── handlers (LocalTool convention: (self, args: dict)) ────

    def _add(self, args: dict) -> dict:
        args = args or {}
        return self._store().add(
            name=str(args.get("name") or ""),
            username=str(args.get("username") or ""),
            password=str(args.get("password") or ""),
            password_env=str(args.get("password_env") or ""),
            service=str(args.get("service") or "generic"),
            notes=str(args.get("notes") or ""),
            overwrite=bool(args.get("overwrite") or False),
        )

    def _list(self, args: dict) -> dict:
        args = args or {}
        service = str(args.get("service") or "") or None
        entries = self._store().list(service=service, masked=True)
        return {"ok": True, "count": len(entries), "credentials": entries}

    def _get(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        if not name:
            return {"error": "'name' is required"}
        entry = self._store().get(name)
        if not entry:
            return {"error": f"credential set '{name}' not found"}
        masked = self._store().mask_entry(entry)
        env_name = str(entry.get("password_env") or "").strip()
        masked["env_var_set"] = (
            bool(os.environ.get(env_name)) if env_name else None
        )
        return {"ok": True, "credential": masked}

    def _remove(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        if not name:
            return {"error": "'name' is required"}
        if not self._store().remove(name):
            return {"error": f"credential set '{name}' not found"}
        return {"ok": True, "removed": name}

    def _resolve(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        if not name:
            return {"error": "'name' is required"}
        resolved = self._store().resolve(name)
        if resolved.get("error"):
            return resolved
        return {
            "ok": True,
            "name": name,
            "source": resolved.get("source"),
            "env_var": resolved.get("env_var"),
            "password_masked": "***resolved***",
        }

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="cred_add",
                description=(
                    "建立 / 覆蓋一組中央帳號集（帳號+密碼）— 密碼優先"
                    "用 password_env 指向環境變數（JSON 不存密碼）,"
                    "或明文 base64 遮蔽存檔。供 ONVIF 裝置與其他服務"
                    "引用（onvif_favorite_add 的 cred_set）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "帳號集唯一名稱。"},
                        "username": {"type": "string",
                                     "description": "帳號。"},
                        "password": {
                            "type": "string",
                            "description": (
                                "明文密碼（base64 遮蔽存檔；推薦改用 "
                                "password_env）。"
                            ),
                        },
                        "password_env": {
                            "type": "string",
                            "description": "存密碼的環境變數名（推薦）。",
                        },
                        "service": {
                            "type": "string",
                            "description": "服務標籤（onvif / generic）。"
                        },
                        "notes": {"type": "string"},
                        "overwrite": {
                            "type": "boolean",
                            "description": "同名覆蓋（預設 false）。",
                        },
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._add,
            ),
            LocalTool(
                name="cred_list",
                description=(
                    "列出中央帳號集 — 帳號/密碼一律遮罩，可 service 過濾。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": "過濾服務標籤（可選）。",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list,
            ),
            LocalTool(
                name="cred_get",
                description=(
                    "查看一組帳號集（遮罩）— 含 credential 來源與 env "
                    "變數是否存在。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "帳號集名稱。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get,
            ),
            LocalTool(
                name="cred_remove",
                description="刪除一組中央帳號集。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "帳號集名稱。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._remove,
            ),
            LocalTool(
                name="cred_resolve",
                description=(
                    "解析帳號集（遮罩輸出）— 顯示解析來源（env / b64）"
                    "與 env 變數是否已設定；不回傳明文密碼。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "帳號集名稱。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._resolve,
            ),
        ]


class OnvifLocalHost:
    """ONVIF camera tools (v0.9.37.0+, Phase A — READ-ONLY).

    Seven tools over src/onvif_tools.py: onvif_discover /
    onvif_favorite_add / onvif_favorite_list / onvif_favorite_remove /
    onvif_test_credentials / onvif_get_profiles / onvif_get_snapshot.
    Credentials resolve env-first (``password_env``) then base64 from
    favorites.json; list output is ALWAYS masked. Snapshots land in
    the filesystem write sandbox so the chat displays them inline
    (v0.9.30) and they can be forwarded with send_telegram_photo
    (v0.9.31). Phase B (recording) / Phase C (settings) pending.
    """

    SERVER_NAME = "onvif-local"

    def __init__(self):
        self._favorites: Any = None
        # v0.9.41.0 — Phase C watches (events → snapshot / webhook).
        self._watches: Any = None
        self._watch_threads: dict[str, tuple[Any, Any]] = {}
        self._webhook_fire: Any = None

    def _store(self) -> Any:
        if self._favorites is None:
            from src.onvif_tools import FavoriteStore

            self._favorites = FavoriteStore()
        return self._favorites

    def _watch_store(self) -> Any:
        if self._watches is None:
            from src.onvif_tools import WatchStore

            self._watches = WatchStore()
        return self._watches

    # ── Phase C wiring + watcher engine (v0.9.41.0+) ──

    def attach_webhook_fire(self, callback: Any) -> None:
        """Wire the webhook fire engine (mirror of
        ``WebhookLocalHost.attach_fire``) so event watches can fire a
        registered webhook trigger (motion → prompt)."""
        self._webhook_fire = callback

    def resume_watches(self) -> int:
        """(Re)start watcher threads for all persisted watches.

        Idempotent — a watch with a live thread is skipped. Called by
        the GUI boot prewarm (threads die with the process; the
        configs survive in ``onvif_watches.json``).
        """
        started = 0
        try:
            for watch in self._watch_store().list():
                if self._start_watch_thread(
                    str(watch.get("name") or ""),
                ):
                    started += 1
        except Exception:
            pass
        return started

    def _start_watch_thread(self, name: str) -> bool:
        """Spawn the poll thread if not already running. Idempotent."""
        import threading

        if not name:
            return False
        existing = self._watch_threads.get(name)
        if existing and existing[0].is_alive():
            return False
        stop = threading.Event()
        thread = threading.Thread(
            target=self._watch_loop, args=(name, stop),
            daemon=True, name=f"onvif-watch-{name}",
        )
        self._watch_threads[name] = (thread, stop)
        thread.start()
        return True

    def _stop_watch_thread(self, name: str, timeout: float = 2.0) -> bool:
        pair = self._watch_threads.pop(name, None)
        if pair is None:
            return False
        _, stop = pair
        stop.set()
        try:
            pair[0].join(timeout=timeout)
        except RuntimeError:
            pass
        return True

    def _timeout(self, env_key: str, default: float) -> float:
        try:
            return float(os.environ.get(env_key) or default)
        except (TypeError, ValueError):
            return float(default)

    def _watch_loop(self, name: str, stop: Any) -> None:
        """Long-poll PullPoint for one watch; fire actions on match.

        Re-subscribes every poll (cheap + resilient to camera
        reboots); failures stamp ``last_error`` and the loop keeps
        polling until stopped or the watch is removed.
        """
        import time as _time

        watch: dict[str, Any] = {}
        while not stop.is_set():
            try:
                watch = self._watch_store().get(name) or {}
                if not watch:
                    break  # removed mid-run
                matched = self._poll_watch_once(watch)
                for event in matched:
                    self._handle_watch_event(watch, event)
            except Exception as e:
                try:
                    self._watch_store().patch(
                        name, last_error=repr(e)[:300],
                    )
                except Exception:
                    pass
            interval = max(2.0, float(watch.get("poll_interval") or 5.0))
            stop.wait(interval)
        try:
            self._watch_store().patch(name, status="stopped")
        except Exception:
            pass

    def _poll_watch_once(self, watch: dict) -> list[dict]:
        """One CreatePullPointSubscription + PullMessages round."""
        from src.onvif_tools import (
            create_pull_point_subscription, get_service_xaddr,
            match_events, pull_messages,
        )

        device, creds, err = self._device_and_creds(
            str(watch.get("device") or ""),
        )
        if err:
            raise RuntimeError(err.get("error") or "device unavailable")
        user = creds.get("username") or ""
        pw = creds.get("password") or ""
        to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
        events_xaddr = get_service_xaddr(
            device["xaddr"], "Events", user, pw, to,
        )
        sub = create_pull_point_subscription(events_xaddr, user, pw, to)
        events = pull_messages(
            sub, user, pw, to,
            poll_seconds=min(float(watch.get("poll_interval") or 5.0),
                             10.0),
        )
        keywords = str(watch.get("keywords") or "motion").replace(
            ";", ",",
        ).split(",")
        return match_events(events, keywords)

    def _handle_watch_event(self, watch: dict, event: dict) -> None:
        """History ring + optional snapshot + optional webhook fire."""
        import time as _time

        from src.onvif_tools import _iso_now

        name = str(watch.get("name") or "")
        record = {
            "ts": _iso_now(),
            "topic": event.get("topic") or "",
            "data": event.get("data") or {},
            "watch": name,
            "device": watch.get("device") or "",
        }
        self._watch_store().append_history(name, record)
        self._watch_store().patch(
            name,
            event_count=int(watch.get("event_count") or 0) + 1,
            last_event_at=record["ts"],
            last_error="",
            status="running",
        )

        # anti-flood: at most one ACTION per poll interval
        last_ts = str(watch.get("last_event_at") or "")
        try:
            last_epoch = _time.mktime(
                _time.strptime(last_ts[:19], "%Y-%m-%dT%H:%M:%S"),
            ) if last_ts else 0.0
        except (ValueError, OverflowError):
            last_epoch = 0.0
        interval = max(2.0, float(watch.get("poll_interval") or 5.0))
        if last_epoch and (_time.time() - last_epoch) < interval:
            return

        payload: dict[str, Any] = {
            "watch": name,
            "device": watch.get("device") or "",
            "event": event.get("topic") or "",
            "data": event.get("data") or {},
            "event_text": (
                f"攝影機事件：{(event.get('topic') or '未知')} — "
                f"資料 {event.get('data') or {}}（watch {name}，"
                f"裝置 {watch.get('device') or '?'}）"
            ),
            "ts": record["ts"],
        }
        if watch.get("snapshot_on_event"):
            try:
                snap = self._snapshot_for_watch(watch)
                if snap:
                    payload["snapshot"] = snap
            except Exception as e:
                payload["snapshot_error"] = repr(e)[:200]
        trigger_id = str(watch.get("webhook_trigger_id") or "").strip()
        fired = False
        if trigger_id and self._webhook_fire is not None:
            try:
                payload["webhook"] = self._fire_watch_webhook(
                    trigger_id, payload,
                )
                fired = True
            except Exception as e:
                payload["webhook_error"] = repr(e)[:200]
        record.update({
            "snapshot": payload.get("snapshot") or "",
            "webhook_fired": fired,
        })
        self._watch_store().append_history(name, record)

    def _snapshot_for_watch(self, watch: dict) -> str:
        """Save one JPEG into the write sandbox; return the path."""
        from datetime import datetime as _dt
        from pathlib import Path as _Path

        from src.onvif_tools import (
            fetch_snapshot, get_profiles, get_service_xaddr,
            get_snapshot_uri, resolve_snapshot_path,
        )

        device, creds, err = self._device_and_creds(
            str(watch.get("device") or ""),
        )
        if err:
            raise RuntimeError(err.get("error") or "device unavailable")
        user = creds.get("username") or ""
        pw = creds.get("password") or ""
        to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
        media = get_service_xaddr(device["xaddr"], "Media", user, pw, to)
        profiles = get_profiles(media, user, pw, to)
        token = (profiles[0].get("token") if profiles else "") or ""
        uri = get_snapshot_uri(media, token, user, pw, to)
        data, _mime = fetch_snapshot(uri, user, pw, to)
        write_root = os.environ.get("QDRANT_FILESYSTEM_WRITE_ROOT") or ""
        if not write_root:
            raise RuntimeError("write sandbox not configured")
        out = resolve_snapshot_path(
            f"uploads/onvif-event-{watch.get('name') or 'watch'}-"
            f"{_dt.now().strftime('%Y%m%d-%H%M%S')}.jpg",
            _Path(write_root).resolve(),
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out.as_posix()

    def _fire_watch_webhook(self, trigger_id: str,
                            payload: dict) -> dict:
        """Resolve the trigger ident and fire (fresh Agent per fire)."""
        from src.agent.webhook import WebhookManager

        mgr = WebhookManager()
        w = mgr.get(trigger_id) or mgr.find_by_name(trigger_id)
        if w is None:
            return {"error": f"webhook trigger {trigger_id!r} not found"}
        if not w.enabled:
            return {"error": f"trigger {trigger_id!r} is disabled"}
        return self._webhook_fire(w, payload) or {"ok": True}

    # --- part: Phase C tools below ---

    def _device_and_creds(
        self, name: str,
    ) -> tuple[dict, dict, Any]:
        """Resolve a favorite → (device, creds, error-dict)."""
        from src.onvif_tools import resolve_credentials

        name = str(name or "").strip()
        if not name:
            return {}, {}, {
                "error": (
                    "device name is required — call onvif_favorite_list "
                    "first to see the available names"
                ),
            }
        store = self._store()
        device = store.get(name)
        if not device:
            return {}, {}, {
                "error": (
                    f"favorite '{name}' not found — call "
                    "onvif_favorite_list to see available names"
                ),
            }
        creds = resolve_credentials(device)
        if creds.get("error"):
            return device, {}, {"error": creds["error"]}
        xaddr = str(device.get("xaddr") or "").strip()
        if not xaddr:
            xaddr = (
                f"http://{device.get('ip')}:{device.get('port')}"
                "/onvif/device_service"
            )
        device = dict(device)
        device["xaddr"] = xaddr
        return device, creds, None

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="onvif_discover",
                description=(
                    "區網內 ONVIF 裝置探索（WS-Discovery UDP Probe，"
                    "預設 5 秒，唯讀）。回傳 xaddr / ip / port / 名稱"
                    "提示。要用帳密存取請再 onvif_favorite_add。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "timeout_s": {
                            "type": "number",
                            "description": "探測秒數（0.5-30，預設 5）。",
                        },
                        "interface_ip": {
                            "type": "string",
                            "description": (
                                "限定本機介面 IP（多網卡 / VPN 時建議）。"
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._discover,
            ),
            LocalTool(
                name="onvif_favorite_add",
                description=(
                    "把攝影機加入我的最愛（含帳密）。password_env 指向"
                    "環境變數（推薦，JSON 不存密碼）或 password 明文"
                    "（base64 遮蔽存檔）。名稱是之後呼叫的鍵。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置唯一名稱。"},
                        "ip": {"type": "string", "description": "IP。"},
                        "port": {"type": "integer",
                                 "description": "ONVIF 埠（預設 8000）。"},
                        "username": {"type": "string",
                                     "description": "帳號（預設 admin）。"},
                        "password": {
                            "type": "string",
                            "description": (
                                "明文密碼（base64 遮蔽存檔；推薦改用 "
                                "password_env）。"
                            ),
                        },
                        "password_env": {
                            "type": "string",
                            "description": (
                                "存密碼的環境變數名（推薦，例 "
                                "ONVIF_FRONTDOOR_PASSWORD）。"
                            ),
                        },
                        "cred_set": {
                            "type": "string",
                            "description": (
                                "引用中央帳號集名稱（cred_add 建立的，"
                                "v0.9.39.0+）— 設定後優先於本裝置帳密。"
                            ),
                        },
                        "manufacturer": {"type": "string"},
                        "model": {"type": "string"},
                        "xaddr": {
                            "type": "string",
                            "description": (
                                "device service URL（discovery 給的）。"
                            ),
                        },
                        "notes": {"type": "string"},
                        "overwrite": {
                            "type": "boolean",
                            "description": "同名覆蓋（預設 false）。",
                        },
                    },
                    "required": ["name", "ip"],
                },
                server_name=self.SERVER_NAME,
                handler=self._favorite_add,
            ),
            LocalTool(
                name="onvif_favorite_list",
                description=(
                    "列出我的最愛攝影機 — 帳號與密碼一律遮罩，顯示 "
                    "credential 來源（env / b64 / none）。每台附實際"
                    "網址：http_url（ONVIF 裝置端點）+ rtsp_url_guess"
                    "（慣用格式）。resolve=true 會對每台做即時 SOAP "
                    "查詢，附真正的 rtsp_url（GetStreamUri + 帳密）、"
                    "rtsp_variants（vendor 格式）與 snapshot_uri — "
                    "離線攝影機會拖慢清單（每台最多數秒），失敗記在 "
                    "resolve_error。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "resolve": {
                            "type": "boolean",
                            "description": (
                                "即時解析每台的實際 RTSP / snapshot "
                                "URL（SOAP；離線相機拖慢回應）。"
                                "預設 false = 只附靜態網址。"
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._favorite_list,
            ),
            LocalTool(
                name="onvif_favorite_remove",
                description="移除一個我的最愛裝置。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._favorite_remove,
            ),
            LocalTool(
                name="onvif_test_credentials",
                description=(
                    "用最愛裡存的帳密測試 ONVIF 連線（輕量 "
                    "GetDeviceInformation）— snapshot 失敗時先跑這個。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._test_credentials,
            ),
            LocalTool(
                name="onvif_get_profiles",
                description=(
                    "列出裝置的 media profile（token / 解析度 / 幀率）— "
                    "snapshot 可指定 profile_token。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get_profiles,
            ),
            LocalTool(
                name="onvif_get_snapshot",
                description=(
                    "監視畫面截圖：GetSnapshotUri → HTTP 下載 JPEG → "
                    "存進沙箱（預設 uploads/）。存檔後 chat 會直接"
                    "顯示圖片，也可用 send_telegram_photo 發送。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                        "profile_token": {
                            "type": "string",
                            "description": (
                                "media profile token（可選，預設第一個）。"
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "存檔路徑（可選，須在寫入沙箱內；預設 "
                                "uploads/onvif-snapshot-<時間>.jpg）。"
                            ),
                        },
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get_snapshot,
            ),
            LocalTool(
                name="onvif_record",
                description=(
                    "錄影：GetStreamUri → RTSP → ffmpeg -c copy 存成 "
                    "MP4（沙箱內，預設 uploads/）。需本機 ffmpeg。"
                    "失敗回 rtsp_variants（XMEye/Hikvision/Dahua 格式）"
                    "→ 用 uri= 重試。seconds 上限 600。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                        "seconds": {
                            "type": "number",
                            "description": "錄影秒數（1-600，預設 10）。",
                        },
                        "profile_token": {
                            "type": "string",
                            "description": "media profile token（可選）。",
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "存檔路徑（可選，須在寫入沙箱內；預設 "
                                "uploads/onvif-record-<時間>.mp4）。"
                            ),
                        },
                        "uri": {
                            "type": "string",
                            "description": (
                                "直接指定 RTSP URL（可選；覆蓋 "
                                "GetStreamUri — 失敗後用 rtsp_variants "
                                "的 vendor 格式重試）。"
                            ),
                        },
                        "channel": {
                            "type": "integer",
                            "description": (
                                "DVR 通道號（vendor variants 用，預設 1）。"
                            ),
                        },
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._record,
            ),
            LocalTool(
                name="onvif_rtsp_snapshot",
                description=(
                    "RTSP + ffmpeg 截圖（v0.9.44.0+）：給「沒有 ONVIF "
                    "snapshot 功能」的裝置（XMEye DVR 等）。依序嘗試 "
                    "GetStreamUri 與 vendor RTSP 格式（xmeye/hikvision/"
                    "dahua）直到抓到一張 JPEG 存沙箱。onvif_get_snapshot "
                    "失敗時也會自動退回此法。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                        "uri": {
                            "type": "string",
                            "description": (
                                "直接指定 RTSP URL（可選；跳過自動"
                                "嘗試但仍會試 vendor 變體）。"
                            ),
                        },
                        "channel": {
                            "type": "integer",
                            "description": (
                                "DVR 通道號（1-64，預設 1；XMEye 的"
                                "channel 對應 rtsp 通道）。"
                            ),
                        },
                        "settle_s": {
                            "type": "number",
                            "description": (
                                "截圖前先跳過的秒數（0-10，預設 0；"
                                "前幾張可能全黑時用 0.5-1）。"
                            ),
                        },
                        "path": {"type": "string",
                                 "description": "輸出 JPEG 路徑（沙箱內；"
                                                "可選）。"},
                        "profile_token": {"type": "string",
                                          "description": "可選。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._rtsp_snapshot,
            ),
            LocalTool(
                name="onvif_get_resources",
                description=(
                    "一次列出裝置的 ONVIF 資源：device 資訊、media "
                    "service XAddr、media profiles（含 RTSP stream URI "
                    "與 snapshot URI）。發現攝影機能力/資源用。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                        "profile_token": {
                            "type": "string",
                            "description": (
                                "指定 profile（可選；預設第一個 — 其 "
                                "stream/snapshot URI 會一併回傳）。"
                            ),
                        },
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get_resources,
            ),
            LocalTool(
                name="onvif_ptz_move",
                description=(
                    "PTZ 轉動（Phase C，需 confirm=true）：ContinuousMove，"
                    "pan/tilt/zoom 速度為 -1..1 比例，duration 0.1-10 秒"
                    "（到時自動停）。攝影機會實際轉動。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                        "pan": {"type": "number",
                                "description": "左右速度 -1..1（正=右）。"},
                        "tilt": {"type": "number",
                                 "description": "上下速度 -1..1（正=上）。"},
                        "zoom": {"type": "number",
                                 "description": "變焦速度 -1..1（正=放大）。"},
                        "duration": {"type": "number",
                                     "description": "移動秒數（0.1-10，預設 1）。"},
                        "confirm": {"type": "boolean",
                                    "description": "必須明確傳 true。"},
                    },
                    "required": ["name", "confirm"],
                },
                server_name=self.SERVER_NAME,
                handler=self._ptz_move,
            ),
            LocalTool(
                name="onvif_ptz_stop",
                description="立即停止攝影機的 PTZ 動作。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._ptz_stop,
            ),
            LocalTool(
                name="onvif_get_presets",
                description="列出攝影機的 PTZ 預設點（token + 名稱）。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get_presets,
            ),
            LocalTool(
                name="onvif_goto_preset",
                description=(
                    "將攝影機轉到指定 PTZ 預設點（需 confirm=true；"
                    "先用 onvif_get_presets 查 token）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                        "preset": {"type": "string",
                                   "description": "Preset token。"},
                        "confirm": {"type": "boolean",
                                    "description": "必須明確傳 true。"},
                    },
                    "required": ["name", "preset", "confirm"],
                },
                server_name=self.SERVER_NAME,
                handler=self._goto_preset,
            ),
            LocalTool(
                name="onvif_get_events",
                description=(
                    "一次性事件探測：CreatePullPointSubscription + "
                    "PullMessages，回這輪收到的事件（topic + 資料）。"
                    "建立 watch 前先看攝影機會發什麼事件（motion/"
                    "tamper/input…）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "裝置名稱。"},
                        "poll_seconds": {
                            "type": "number",
                            "description": "長輪詢秒數（0.5-30，預設 2）。",
                        },
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get_events,
            ),
            LocalTool(
                name="onvif_watch_start",
                description=(
                    "建立事件監看（背景輪詢 PullPoint）：keywords 過濾"
                    "（如 motion），命中時可（a）存事件歷史（b）拍一張"
                    "快照進沙箱（snapshot_on_event）（c）觸發一個已註冊"
                    "的 webhook 觸發器（webhook_trigger_id → prompt 執行）。"
                    "先 add_webhook_trigger 建觸發器、onvif_get_events "
                    "確認關鍵字。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "監看名稱。"},
                        "device": {"type": "string",
                                   "description": "裝置名稱（favorites）。"},
                        "keywords": {
                            "type": "string",
                            "description": (
                                "逗號分隔關鍵字（預設 motion；topic 與"
                                "資料值的不分大小寫子字串比對）。"
                            ),
                        },
                        "poll_interval": {
                            "type": "number",
                            "description": "輪詢間隔秒（2-300，預設 5；"
                                           "也是動作防洪水位）。",
                        },
                        "webhook_trigger_id": {
                            "type": "string",
                            "description": "命中時觸發的 webhook 觸發器"
                                           "（id 或名稱；可選）。",
                        },
                        "snapshot_on_event": {
                            "type": "boolean",
                            "description": "命中時存一張快照到沙箱（可選）。",
                        },
                        "overwrite": {"type": "boolean",
                                      "description": "同名覆蓋（可選）。"},
                    },
                    "required": ["name", "device"],
                },
                server_name=self.SERVER_NAME,
                handler=self._watch_start,
            ),
            LocalTool(
                name="onvif_watch_list",
                description=(
                    "列出所有事件監看：狀態 / 計數 / 最近事件 / 錯誤。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._watch_list,
            ),
            LocalTool(
                name="onvif_watch_stop",
                description=(
                    "停止並刪除事件監看（背景輪詢執行緒也一併停止）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "監看名稱。"},
                    },
                    "required": ["name"],
                },
                server_name=self.SERVER_NAME,
                handler=self._watch_stop,
            ),
        ]

    # ── handlers (LocalTool convention: (self, args: dict)) ────

    def _discover(self, args: dict) -> dict:
        args = args or {}
        from src.onvif_tools import wsdiscovery_probe

        try:
            timeout_s = float(args.get("timeout_s") or 5.0)
        except (TypeError, ValueError):
            timeout_s = 5.0
        timeout_s = max(0.5, min(timeout_s, 30.0))
        try:
            devices = wsdiscovery_probe(
                timeout_s=timeout_s,
                interface_ip=str(args.get("interface_ip") or "") or None,
            )
        except OSError as e:
            return {"error": f"WS-Discovery probe failed: {e!r}"}
        return {
            "ok": True,
            "timeout_s": timeout_s,
            "count": len(devices),
            "devices": devices,
            "note": (
                "唯讀探測；要用帳密存取請 onvif_favorite_add（密碼建議 "
                "password_env）。"
            ),
        }

    def _favorite_add(self, args: dict) -> dict:
        args = args or {}
        return self._store().add(
            name=str(args.get("name") or ""),
            ip=str(args.get("ip") or ""),
            port=args.get("port") or 8000,
            username=str(args.get("username") or "admin"),
            password=str(args.get("password") or ""),
            password_env=str(args.get("password_env") or ""),
            manufacturer=str(args.get("manufacturer") or ""),
            model=str(args.get("model") or ""),
            xaddr=str(args.get("xaddr") or ""),
            notes=str(args.get("notes") or ""),
            overwrite=bool(args.get("overwrite") or False),
            cred_set=str(args.get("cred_set") or ""),
        )

    def _favorite_list(self, args: dict) -> dict:
        args = args or {}
        resolve = bool(args.get("resolve") or False)
        devices = self._store().list(masked=True)
        # v0.9.56.0: every entry carries the STATIC URLs (http device
        # endpoint + conventional rtsp guess) — no network calls.
        from src.onvif_tools import device_urls

        for d in devices:
            d.update(device_urls(d))
        if resolve:
            # live resolution: actual GetStreamUri RTSP URL per camera
            # (SOAP round-trips; per-camera failures are contained).
            for d in devices:
                d.update(self._resolve_favorite_urls(d))
        return {
            "ok": True,
            "count": len(devices),
            "resolved": resolve,
            "favorites": devices,
        }

    def _resolve_favorite_urls(self, d: dict) -> dict:
        """Live-resolve the ACTUAL RTSP / snapshot URLs for one
        favorite (SOAP round-trips; every failure is contained so one
        offline camera cannot break the whole list)."""
        name = str(d.get("name") or "")
        device, creds, err = self._device_and_creds(name)
        if err:
            return {"resolve_error": "device or credentials unresolvable"}
        user = creds.get("username") or ""
        pw = creds.get("password") or ""
        # Capped timeout — an offline camera must not hang the sweep
        # (each candidate media endpoint waits up to this long).
        to = min(self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0), 5.0)
        try:
            from src.onvif_tools import (
                _embed_rtsp_creds,
                get_profiles_anywhere,
                get_snapshot_uri,
                get_stream_uri,
                rtsp_variants,
            )

            profiles, media_xaddr = get_profiles_anywhere(
                device["xaddr"], user, pw, to,
            )
            token = (profiles[0].get("token") or "") if profiles else ""
            out: dict = {
                "media_xaddr": media_xaddr,
                "profile_count": len(profiles),
            }
            if token:
                try:
                    rtsp = get_stream_uri(media_xaddr, token, user, pw, to)
                    out["rtsp_url"] = _embed_rtsp_creds(rtsp, user, pw)
                    out["rtsp_variants"] = rtsp_variants(
                        rtsp, user, pw, channel=1,
                    )
                except Exception as e:
                    out["rtsp_url"] = f"<unavailable: {e!r}>"
                try:
                    out["snapshot_uri"] = get_snapshot_uri(
                        media_xaddr, token, user, pw, to,
                    )
                except Exception as e:
                    out["snapshot_uri"] = f"<unavailable: {e!r}>"
            return out
        except Exception as e:
            return {"resolve_error": repr(e)[:300]}

    def _favorite_remove(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        if not name:
            return {"error": "'name' is required"}
        if not self._store().remove(name):
            return {"error": f"favorite '{name}' not found"}
        return {"ok": True, "removed": name}

    def _rtsp_snapshot(self, args: dict) -> dict:
        """v0.9.44.0 — RTSP + ffmpeg frame grab for cameras WITHOUT
        an ONVIF snapshot capability (XMEye etc.). Tries the
        GetStreamUri (when profiles are reachable), then the vendor
        RTSP variants, until one produces a JPEG."""
        args = args or {}
        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        from src.onvif_tools import (
            OnvifError, _embed_rtsp_creds, ffmpeg_available,
            get_profiles_anywhere, grab_frame_via_rtsp, get_stream_uri,
            resolve_snapshot_path, rtsp_variants,
        )

        if not ffmpeg_available():
            return {
                "error": (
                    "ffmpeg is not installed — RTSP snapshots require it "
                    "(install with: winget install Gyan.FFmpeg, then "
                    "restart the app)"
                ),
            }
        write_root = _resolve_filesystem_root(
            "QDRANT_FILESYSTEM_WRITE_ROOT",
        )
        user = creds.get("username") or ""
        pw = creds.get("password") or ""
        to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
        try:
            channel = int(args.get("channel") or 1)
        except (TypeError, ValueError):
            channel = 1
        channel = max(1, min(64, channel))
        try:
            settle = float(args.get("settle_s") or 0.0)
        except (TypeError, ValueError):
            settle = 0.0

        # candidate URIs (ordered, creds embedded)
        candidates: list[dict[str, str]] = []
        override = str(args.get("uri") or "").strip()
        if override:
            if not override.lower().startswith("rtsp://"):
                return {"error": "uri must be rtsp://"}
            candidates.append({
                "style": "override",
                "uri": _embed_rtsp_creds(override, user, pw),
            })
            variants = rtsp_variants(override, user, pw, channel=channel)
        else:
            base = None
            try:
                profiles, media_xaddr = get_profiles_anywhere(
                    device["xaddr"], user, pw, to,
                )
                token = str(args.get("profile_token") or "").strip() \
                    or (profiles[0].get("token") if profiles else "") \
                    or ""
                base = get_stream_uri(media_xaddr, token, user, pw, to)
            except Exception:
                base = None
            if not base:
                from urllib.parse import urlparse

                p = urlparse(device["xaddr"])
                base = f"rtsp://{p.hostname or '127.0.0.1'}:554/onvif1"
            candidates.append({
                "style": "stream-uri",
                "uri": _embed_rtsp_creds(base, user, pw),
            })
            variants = rtsp_variants(base, user, pw, channel=channel)
        seen: set[str] = set()
        for v in variants:
            if v["uri"] not in seen:
                seen.add(v["uri"])
                candidates.append(v)

        target = resolve_snapshot_path(
            str(args.get("path") or ""), write_root,
        )
        attempts: list[dict[str, str]] = []
        for cand in candidates:
            try:
                res = grab_frame_via_rtsp(
                    cand["uri"], target, settle_s=settle,
                    timeout_s=self._timeout(
                        "QDRANT_ONVIF_RTSP_SNAPSHOT_TIMEOUT", 20.0,
                    ),
                )
            except OnvifError as e:
                attempts.append({"style": cand["style"],
                                 "error": str(e)[:220]})
                continue
            self._store().touch(
                name, last_seen=True, last_snapshot=target.as_posix(),
            )
            return {
                "ok": True,
                "name": name,
                "path": target.as_posix(),
                "mime": "image/jpeg",
                "size": res.get("size"),
                "via": "rtsp",
                "style_used": cand["style"],
                "attempts": attempts,
            }
        return {
            "error": (
                "RTSP frame grab failed on every candidate "
                f"({len(candidates)} tried)"
            ),
            "attempts": attempts,
            "hint": (
                "確認 RTSP 埠（554）與憑證；XMEye DVR 的通道號用 "
                "channel=N 指定。"
            ),
        }
        args = args or {}
        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        from src.onvif_tools import get_device_information

        try:
            info = get_device_information(
                device["xaddr"],
                creds.get("username") or "",
                creds.get("password") or "",
                timeout=self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0),
            )
        except Exception as e:
            self._store().touch(name)
            return {"ok": False, "error": repr(e), "name": name}
        self._store().touch(name, last_seen=True)
        # v0.9.43.2: a device that answers but returns EMPTY info is
        # NOT a working ONVIF device service (live-box: an xaddr
        # pointing at a web page answered 200 with no fields) —
        # surface that instead of a misleading ok:true.
        if not any(str(info.get(k) or "").strip() for k in
                   ("manufacturer", "model", "serial", "firmware")):
            return {
                "ok": False,
                "error": (
                    "device responded but returned no device "
                    "information — the xaddr may not be the ONVIF "
                    "device service (check ip:port / path)"
                ),
                "name": name,
                "xaddr": device.get("xaddr"),
            }
        return {"ok": True, "name": name, **info}

    def _get_profiles(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        from src.onvif_tools import get_profiles

        try:
            profiles = get_profiles(
                device["xaddr"],
                creds.get("username") or "",
                creds.get("password") or "",
                timeout=self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0),
            )
        except Exception as e:
            return {"error": repr(e), "name": name}
        return {
            "ok": True, "name": name,
            "count": len(profiles), "profiles": profiles,
        }

    def _test_credentials(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        from src.onvif_tools import get_device_information

        try:
            info = get_device_information(
                device["xaddr"],
                creds.get("username") or "",
                creds.get("password") or "",
                timeout=self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0),
            )
        except Exception as e:
            self._store().touch(name)
            return {"ok": False, "error": repr(e), "name": name}
        self._store().touch(name, last_seen=True)
        # v0.9.43.2: a device that answers but returns EMPTY info is
        # NOT a working ONVIF device service (live-box: an xaddr
        # pointing at a web page answered 200 with no fields) —
        # surface that instead of a misleading ok:true.
        if not any(str(info.get(k) or "").strip() for k in
                   ("manufacturer", "model", "serial", "firmware")):
            return {
                "ok": False,
                "error": (
                    "device responded but returned no device "
                    "information — the xaddr may not be the ONVIF "
                    "device service (check ip:port / path)"
                ),
                "name": name,
                "xaddr": device.get("xaddr"),
            }
        return {"ok": True, "name": name, **info}

    def _get_snapshot(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        force_rtsp = bool(args.get("via_rtsp"))
        if not force_rtsp:
            try:
                out = self._onvif_snapshot_inner(args, name, device,
                                                 creds)
                if out.get("ok"):
                    return out
                onvif_err = out.get("error") or "unknown"
            except Exception as e:
                onvif_err = repr(e)
        else:
            onvif_err = "via_rtsp=true — ONVIF snapshot skipped"
        # v0.9.44.0 — automatic RTSP fallback: the device has no
        # ONVIF snapshot capability (XMEye DVRs etc.) → grab one
        # frame from the RTSP stream with ffmpeg instead.
        fb = self._rtsp_snapshot({**args, "name": name})
        if fb.get("ok"):
            fb["onvif_error"] = str(onvif_err)[:200]
            fb["note"] = (
                "此裝置無 ONVIF snapshot — 已自動改用 RTSP + ffmpeg "
                "截圖。"
            )
            return fb
        return {
            "error": (
                f"ONVIF snapshot failed ({onvif_err[:180]}) AND the "
                "RTSP fallback failed"
            ),
            "rtsp_error": fb.get("error"),
            "rtsp_attempts": fb.get("attempts"),
            "hint": (
                "確認 RTSP 埠 554 可連、憑證正確；XMEye 通道用 "
                "channel=N。也可直接 onvif_rtsp_snapshot(uri=...)。"
            ),
        }

    def _onvif_snapshot_inner(self, args: dict, name: str,
                              device: dict, creds: dict) -> dict:
        from src.onvif_tools import (
            fetch_snapshot,
            get_profiles_anywhere,
            get_snapshot_uri,
            resolve_snapshot_path,
        )

        write_root = _resolve_filesystem_root(
            "QDRANT_FILESYSTEM_WRITE_ROOT",
        )
        to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
        # v0.9.43.1: probe the media endpoints in order (XMEye
        # DVRs keep GetProfiles off the device service) — falls
        # back across media_service / :8899 / http_media_service
        # / device xaddr until one returns profiles.
        profiles, media_xaddr = get_profiles_anywhere(
            device["xaddr"],
            creds.get("username") or "",
            creds.get("password") or "",
            timeout=to,
        )
        token = str(args.get("profile_token") or "").strip()
        if not token:
            token = profiles[0].get("token") or ""
        uri = get_snapshot_uri(
            media_xaddr, token,
            creds.get("username") or "",
            creds.get("password") or "",
            timeout=to,
        )
        try:
            max_bytes = int(os.environ.get(
                "QDRANT_ONVIF_SNAPSHOT_MAX_BYTES",
                str(8 * 1024 * 1024),
            ) or 8 * 1024 * 1024)
        except ValueError:
            max_bytes = 8 * 1024 * 1024
        body, mime = fetch_snapshot(
            uri,
            creds.get("username") or "",
            creds.get("password") or "",
            timeout=to,
            max_bytes=max_bytes,
        )
        target = resolve_snapshot_path(
            str(args.get("path") or ""), write_root,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        self._store().touch(
            name, last_seen=True, last_snapshot=target.as_posix(),
        )
        return {
            "ok": True,
            "name": name,
            "path": target.as_posix(),
            "mime": mime,
            "size": len(body),
            "profile_token": token,
            "media_xaddr_used": media_xaddr,
        }

    def _record(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        try:
            from src.onvif_tools import (
                OnvifError,
                _embed_rtsp_creds,
                ffmpeg_available,
                get_profiles_anywhere,
                get_stream_uri,
                record_stream,
                resolve_record_path,
                rtsp_variants,
            )

            if not ffmpeg_available():
                return {
                    "error": (
                        "ffmpeg is not installed — recording requires it "
                        "(install with: winget install Gyan.FFmpeg, then "
                        "restart the app)"
                    ),
                }
            write_root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT",
            )
            try:
                seconds = float(args.get("seconds") or 10.0)
            except (TypeError, ValueError):
                seconds = 10.0
            try:
                max_seconds = float(os.environ.get(
                    "QDRANT_ONVIF_RECORD_MAX_SECONDS", "600",
                ) or 600)
            except ValueError:
                max_seconds = 600.0
            seconds = max(1.0, min(seconds, max_seconds))
            to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
            try:
                channel = int(args.get("channel") or 1)
            except (TypeError, ValueError):
                channel = 1
            channel = max(1, min(64, channel))
            # v0.9.43.1: optional explicit RTSP URI override — the LLM
            # picks a working vendor variant after a failed attempt.
            override_uri = str(args.get("uri") or "").strip()
            if override_uri:
                if not override_uri.lower().startswith("rtsp://"):
                    return {"error": "uri override must be rtsp://"}
                rtsp = _embed_rtsp_creds(
                    override_uri,
                    creds.get("username") or "",
                    creds.get("password") or "",
                )
                token = "override"
            else:
                profiles, media_xaddr = get_profiles_anywhere(
                    device["xaddr"],
                    creds.get("username") or "",
                    creds.get("password") or "",
                    timeout=to,
                )
                token = str(args.get("profile_token") or "").strip()
                if not token:
                    token = profiles[0].get("token") or ""
                rtsp = get_stream_uri(
                    media_xaddr, token,
                    creds.get("username") or "",
                    creds.get("password") or "",
                    timeout=to,
                )
                rtsp = _embed_rtsp_creds(
                    rtsp, creds.get("username") or "",
                    creds.get("password") or "",
                )
            target = resolve_record_path(
                str(args.get("path") or ""), write_root,
            )
            try:
                result = record_stream(rtsp, target, seconds=seconds)
            except OnvifError as rec_err:
                msg = str(rec_err)
                if any(k in msg for k in (
                    "DESCRIBE", "401", "Unauthorized",
                    "Error opening input",
                )):
                    return {
                        "error": msg,
                        "rtsp_variants": rtsp_variants(
                            rtsp,
                            creds.get("username") or "",
                            creds.get("password") or "",
                            channel=channel,
                        ),
                        "hint": (
                            "重試時把任一 style 的 uri 用 "
                            "onvif_record(name=..., uri=...) 傳入"
                            "（XMEye 類 DVR 要 xmeye query-string 格式）。"
                        ),
                    }
                raise
            self._store().touch(
                name, last_seen=True, last_snapshot=target.as_posix(),
            )
            out = {
                "ok": result.get("ok", False),
                "name": name,
                "path": target.as_posix(),
                "size": result.get("size"),
                "seconds": seconds,
                "profile_token": token,
                "note": (
                    "MP4 存進沙箱；可直接用 send_file / Telegram 分享"
                    if result.get("ok") else None
                ),
                **({"warning": result["warning"]}
                   if result.get("warning") else {}),
            }
            return out
        except Exception as e:
            return {"error": repr(e), "name": name}

    def _ptz_move(self, args: dict) -> dict:
        args = args or {}
        from src.onvif_tools import (
            get_profiles, get_service_xaddr, ptz_continuous_move,
        )

        if args.get("confirm") is not True:
            return {
                "error": "PTZ move requires confirm=true — the camera "
                         "will physically move.",
            }
        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        try:
            to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
            user = creds.get("username") or ""
            pw = creds.get("password") or ""
            ptz = get_service_xaddr(device["xaddr"], "PTZ", user, pw, to)
            profiles = get_profiles(device["xaddr"], user, pw, to)
            token = (profiles[0].get("token") if profiles else "") or ""
            return {"ok": True, "result": ptz_continuous_move(
                ptz, token,
                pan=float(args.get("pan") or 0.0),
                tilt=float(args.get("tilt") or 0.0),
                zoom=float(args.get("zoom") or 0.0),
                duration_s=float(args.get("duration") or 1.0),
                username=user, password=pw, timeout=to,
            )}
        except Exception as e:
            return {"error": repr(e), "name": name}

    def _ptz_stop(self, args: dict) -> dict:
        args = args or {}
        from src.onvif_tools import (
            get_profiles, get_service_xaddr, ptz_stop,
        )

        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        try:
            to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
            user, pw = (creds.get("username") or "",
                        creds.get("password") or "")
            ptz = get_service_xaddr(device["xaddr"], "PTZ", user, pw, to)
            profiles = get_profiles(device["xaddr"], user, pw, to)
            token = (profiles[0].get("token") if profiles else "") or ""
            return {"ok": True,
                    "result": ptz_stop(ptz, token, user, pw, to)}
        except Exception as e:
            return {"error": repr(e), "name": name}

    def _get_presets(self, args: dict) -> dict:
        args = args or {}
        from src.onvif_tools import (
            get_presets as _gp, get_profiles, get_service_xaddr,
        )

        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        try:
            to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
            user, pw = (creds.get("username") or "",
                        creds.get("password") or "")
            ptz = get_service_xaddr(device["xaddr"], "PTZ", user, pw, to)
            profiles = get_profiles(device["xaddr"], user, pw, to)
            token = (profiles[0].get("token") if profiles else "") or ""
            presets = _gp(ptz, token, user, pw, to)
            return {"ok": True, "name": name, "profile": token,
                    "count": len(presets), "presets": presets}
        except Exception as e:
            return {"error": repr(e), "name": name}

    def _goto_preset(self, args: dict) -> dict:
        args = args or {}
        from src.onvif_tools import (
            get_profiles, get_service_xaddr, ptz_goto_preset,
        )

        if args.get("confirm") is not True:
            return {
                "error": "GotoPreset requires confirm=true — the camera "
                         "will physically move.",
            }
        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        try:
            to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
            user, pw = (creds.get("username") or "",
                        creds.get("password") or "")
            ptz = get_service_xaddr(device["xaddr"], "PTZ", user, pw, to)
            profiles = get_profiles(device["xaddr"], user, pw, to)
            token = (profiles[0].get("token") if profiles else "") or ""
            return {"ok": True, "result": ptz_goto_preset(
                ptz, token, str(args.get("preset") or ""), user, pw, to,
            )}
        except Exception as e:
            return {"error": repr(e), "name": name}

    def _get_events(self, args: dict) -> dict:
        args = args or {}
        from src.onvif_tools import drain_events, get_service_xaddr

        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        try:
            to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
            user, pw = (creds.get("username") or "",
                        creds.get("password") or "")
            ev = get_service_xaddr(device["xaddr"], "Events", user, pw, to)
            try:
                poll = float(args.get("poll_seconds") or 2.0)
            except (TypeError, ValueError):
                poll = 2.0
            events = drain_events(ev, user, pw, to, rounds=1,
                                  poll_seconds=poll, limit=10)
            return {"ok": True, "name": name, "events_xaddr": ev,
                    "count": len(events), "events": events,
                    "note": "topic 常見 tns1:RuleEngine/CellMotionDetector/"
                            "Motion；用關鍵字（如 motion）建 watch。"}
        except Exception as e:
            return {"error": repr(e), "name": name}

    def _watch_start(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        device = str(args.get("device") or "").strip()
        if not name or not device:
            return {"error": "name and device are required"}
        store = self._watch_store()
        try:
            entry = store.add(
                name, device,
                keywords=str(args.get("keywords") or "motion"),
                poll_interval=float(args.get("poll_interval") or 5.0),
                webhook_trigger_id=str(
                    args.get("webhook_trigger_id") or "",
                ),
                snapshot_on_event=bool(args.get("snapshot_on_event")),
                overwrite=bool(args.get("overwrite")),
            )
        except Exception as e:
            return {"error": repr(e)}
        self._start_watch_thread(entry["name"])
        return {"ok": True, "watch": entry, "thread": "running"}

    def _watch_list(self, args: dict) -> dict:
        store = self._watch_store()
        out = []
        for w in store.list():
            w = dict(w)
            w["recent_events"] = store.read_history(
                str(w.get("name") or ""), limit=5,
            )
            pair = self._watch_threads.get(str(w.get("name") or ""))
            w["thread_alive"] = bool(
                pair and pair[0].is_alive(),
            )
            out.append(w)
        return {"ok": True, "count": len(out), "watches": out}

    def _watch_stop(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        if not name:
            return {"error": "name is required"}
        stopped = self._stop_watch_thread(name)
        removed = self._watch_store().remove(name)
        if not stopped and not removed:
            return {"error": f"watch {name!r} not found"}
        return {"ok": True, "name": name, "thread_stopped": stopped,
                "removed": removed}

    def _get_resources(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        device, creds, err = self._device_and_creds(name)
        if err:
            return err
        user = creds.get("username") or ""
        pw = creds.get("password") or ""
        to = self._timeout("QDRANT_ONVIF_HTTP_TIMEOUT", 10.0)
        try:
            from src.onvif_tools import (
                get_device_information,
                get_profiles_anywhere,
                get_snapshot_uri,
                get_stream_uri,
                rtsp_variants,
            )

            info = get_device_information(device["xaddr"], user, pw, to)
            # v0.9.43.1: media endpoints probed in order
            profiles, media_xaddr = get_profiles_anywhere(
                device["xaddr"], user, pw, to,
            )
            token = str(args.get("profile_token") or "").strip()
            if not token and profiles:
                token = profiles[0].get("token") or ""
            rtsp_uri = snapshot_uri = None
            variants: list = []
            if token:
                try:
                    rtsp_uri = get_stream_uri(media_xaddr, token,
                                              user, pw, to)
                    variants = rtsp_variants(rtsp_uri, user, pw,
                                             channel=1)
                except Exception as e:
                    rtsp_uri = f"<unavailable: {e!r}>"
                try:
                    snapshot_uri = get_snapshot_uri(
                        media_xaddr, token, user, pw, to,
                    )
                except Exception as e:
                    snapshot_uri = f"<unavailable: {e!r}>"
            return {
                "ok": True,
                "name": name,
                "device": info,
                "media_xaddr": media_xaddr,
                "profile_count": len(profiles),
                "profiles": profiles,
                "selected_profile": token or None,
                "rtsp_stream_uri": rtsp_uri,
                "rtsp_variants": variants,
                "snapshot_uri": snapshot_uri,
                "note": (
                    "RTSP URI 可直接餵 ffmpeg / VLC；錄影用 "
                    "onvif_record、截圖用 onvif_get_snapshot。若標準"
                    "格式連不上，改用 rtsp_variants 的 vendor 格式"
                    "（XMEye DVR 要 xmeye）。"
                ),
            }
        except Exception as e:
            return {"error": repr(e), "name": name}


class NetScanLocalHost:
    """v0.9.40.0+ — LAN IP scanner + TCP port scanner (server
    ``netscan-local``). Private-network-only by default; public
    targets need QDRANT_NETSCAN_ALLOW_PUBLIC=on."""

    SERVER_NAME = "netscan-local"

    def _guard_targets(self, targets: list[str]) -> str | None:
        """Refuse public targets unless ALLOW_PUBLIC — clear message."""
        from src.net_scan_tools import _public_allowed, is_public_target

        public = [ip for ip in targets if is_public_target(ip)]
        if public and not _public_allowed():
            return (
                "public/internet targets refused (e.g. "
                f"{public[0]}) — set QDRANT_NETSCAN_ALLOW_PUBLIC=on "
                "to scan addresses you own"
            )
        return None

    def _timeout(self, env_key: str, default: float) -> float:
        try:
            return float(os.environ.get(env_key) or default)
        except (TypeError, ValueError):
            return default

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="net_scan_hosts",
                description=(
                    "IP scanner：對 CIDR（192.168.1.0/24）/ 單一 IP / "
                    "範圍（192.168.1.10-50）做存活探測。method=tcp "
                    "（預設，探測常見服務埠）或 ping（ICMP）。預設僅"
                    "允許私網目標。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "targets": {
                            "type": "string",
                            "description": (
                                "CIDR、單一 IP 或 start-end 範圍，"
                                "如 192.168.1.0/24 或 192.168.1.10-50。"
                            ),
                        },
                        "method": {
                            "type": "string",
                            "enum": ["tcp", "ping"],
                            "description": "存活判定法（預設 tcp）。",
                        },
                        "ports": {
                            "type": "string",
                            "description": (
                                "tcp 法的探測埠，如 80,443,8000-8100 "
                                "（預設常見服務埠清單）。"
                            ),
                        },
                        "timeout": {
                            "type": "number",
                            "description": "每個探測的秒數（預設 1，上限 5）。",
                        },
                        "resolve_names": {
                            "type": "boolean",
                            "description": "對存活主機做反解 DNS（預設否）。",
                        },
                    },
                    "required": ["targets"],
                },
                server_name=self.SERVER_NAME,
                handler=self._scan_hosts,
            ),
            LocalTool(
                name="net_scan_ports",
                description=(
                    "Port scanner：對單一主機做 TCP connect 掃描，"
                    "回報開啟埠 + 服務名稱 + 可選 banner（recv-only，"
                    "不送 payload）。ports 如 80,443,554,8000-8100。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "host": {"type": "string",
                                 "description": "目標主機 IP 或主機名。"},
                        "ports": {
                            "type": "string",
                            "description": (
                                "埠清單/範圍，如 22,80,443,554,"
                                "8000-8100（預設常見服務埠）。"
                            ),
                        },
                        "timeout": {
                            "type": "number",
                            "description": "每埠連線秒數（預設 1，上限 5）。",
                        },
                        "grab_banner": {
                            "type": "boolean",
                            "description": (
                                "對開啟埠嘗試讀 banner（預設否）。"
                            ),
                        },
                    },
                    "required": ["host"],
                },
                server_name=self.SERVER_NAME,
                handler=self._scan_ports,
            ),
            LocalTool(
                name="net_my_interfaces",
                description=(
                    "列出本機非 loopback 的 IPv4 位址 — 掃描前先用它"
                    "決定要掃哪個網段（如 192.168.50.0/24）。唯讀、"
                    "零參數。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._my_interfaces,
            ),
        ]

    def _scan_hosts(self, args: dict) -> dict:
        args = args or {}
        from src.net_scan_tools import NetScanError, scan_hosts

        spec = str(args.get("targets") or "").strip()
        if not spec:
            return {"error": "targets is required"}
        try:
            from src.net_scan_tools import parse_targets

            guard = self._guard_targets(parse_targets(spec))
            if guard:
                return {"error": guard}
            try:
                to = float(args.get("timeout") or 1.0)
            except (TypeError, ValueError):
                to = 1.0
            return {
                "ok": True,
                **scan_hosts(
                    spec,
                    method=str(args.get("method") or "tcp"),
                    ports=args.get("ports"),
                    timeout=self._timeout("QDRANT_NETSCAN_TIMEOUT", to),
                    resolve_names=bool(args.get("resolve_names")),
                ),
            }
        except NetScanError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": repr(e)}

    def _scan_ports(self, args: dict) -> dict:
        args = args or {}
        from src.net_scan_tools import (
            NetScanError, parse_port_spec, scan_host_ports,
        )

        host = str(args.get("host") or "").strip()
        if not host:
            return {"error": "host is required"}
        try:
            import socket as _s

            host_ip = _s.gethostbyname(host)
        except OSError as e:
            return {"error": f"cannot resolve {host!r}: {e!r}"}
        guard = self._guard_targets([host_ip])
        if guard:
            return {"error": guard}
        try:
            try:
                to = float(args.get("timeout") or 1.0)
            except (TypeError, ValueError):
                to = 1.0
            results = scan_host_ports(
                host_ip,
                parse_port_spec(args.get("ports")),
                timeout=self._timeout("QDRANT_NETSCAN_TIMEOUT", to),
                grab_banner=bool(args.get("grab_banner")),
            )
        except NetScanError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": repr(e)}
        open_ports = [r for r in results if r["state"] == "open"]
        return {
            "ok": True,
            "host": host_ip,
            "ports_scanned": len(results),
            "open_count": len(open_ports),
            "open": open_ports,
            "closed_count": len(results) - len(open_ports),
        }

    def _my_interfaces(self, args: dict) -> dict:
        from src.net_scan_tools import local_interfaces

        ips = local_interfaces()
        return {
            "ok": True,
            "interfaces": ips,
            "suggested_scan": [
                f"{ip.rsplit('.', 1)[0]}.0/24" for ip in ips if "." in ip
            ],
            "note": "先確認網段再 net_scan_hosts；預設僅允許私網目標。",
        }


class RuntimeLocalHost:
    """v0.9.43.0+ — runtime control MCP tools (server
    ``runtime-local``): config hot reload, daemon-service restarts,
    MCP fleet reconnect and runtime status — the LLM-facing core
    functions of the v0.9.42.0 reload/restart mechanism.

    Wiring (v0.8.6.3 pattern): ``Agent.mcp_manager`` wires
    ``set_runtime_ops_callback(self._runtime_ops)`` — reload /
    restart_services / status delegate to the Agent. The MCP-reconnect
    op is wired at ``from_config`` seed time straight to
    ``McpManager.reconnect_remote_servers`` (no Agent needed).
    stdio/CLI hosts without wiring degrade to clear error envelopes.
    """

    SERVER_NAME = "runtime-local"
    _COOLDOWN_S = 5.0

    def __init__(self):
        self._ops_cb: Any = None       # (op, args) -> dict (Agent-wired)
        self._reconnect_cb: Any = None  # () -> dict (manager-wired)
        self._last_reload: float = 0.0

    def attach_ops(self, callback: Any) -> None:
        self._ops_cb = callback

    def attach_reconnect(self, callback: Any) -> None:
        self._reconnect_cb = callback

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="reload_config",
                description=(
                    "重新載入 .env 設定並重置 LLM provider 鏈（下一次"
                    "對話用新設定）、按啟用條件重啟常駐服務、可選重連"
                    "MCP fleet。回傳變更鍵清單。5 秒冷卻。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "restart_services": {
                            "type": "boolean",
                            "description": (
                                "同時重啟常駐服務（預設 true）。"
                            ),
                        },
                        "reconnect_mcp": {
                            "type": "boolean",
                            "description": (
                                "同時重連 config/mcp.json 的遠端 "
                                "servers（預設 true）。"
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._reload,
            ),
            LocalTool(
                name="restart_services",
                description=(
                    "重啟常駐服務（不改設定）：scheduler（有 enabled "
                    "排程）/ webhook listener（有 enabled 觸發器）/"
                    "telegram bot（token+allowlist）/ ONVIF 事件監看。"
                    "services 可過濾，如 scheduler,webhook。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "services": {
                            "type": "string",
                            "description": (
                                "逗號分隔的服務名過濾（可選；預設全部）："
                                "scheduler,webhook,telegram,onvif_watches。"
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._restart_services,
            ),
            LocalTool(
                name="restart_mcp_servers",
                description=(
                    "重讀 config/mcp.json 並重建全部遠端 MCP 連線"
                    "（stdio / HTTP）。本地 LocalHosts 不受影響；"
                    "MCP_ACTIVE_SERVERS gate 下一輪重新套用。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "MCP 設定檔路徑（可選；預設 "
                                "config/mcp.json）。"
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._restart_mcp,
            ),
            LocalTool(
                name="get_runtime_status",
                description=(
                    "目前 runtime 狀態：生效中的 provider / model、"
                    "LLM client 是否就緒、最後回應的 provider（failover "
                    "診斷用）。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._status,
            ),
        ]

    def _reload(self, args: dict) -> dict:
        import time as _t

        args = args or {}
        now = _t.time()
        if now - self._last_reload < self._COOLDOWN_S:
            return {
                "error": (
                    f"reload cooldown ({self._COOLDOWN_S:.0f}s) — "
                    "a reload just ran; check the previous result."
                ),
            }
        if self._ops_cb is None:
            return {
                "error": (
                    "runtime ops not wired — reload_config is only "
                    "available in the in-process chat (GUI / CLI); "
                    "external MCP clients should use the host's CLI "
                    "reload command."
                ),
            }
        self._last_reload = now
        out = self._ops_cb("reload", {
            "restart_services": bool(args.get("restart_services", True)),
            "reconnect_mcp": bool(args.get("reconnect_mcp", True)),
        })
        return out or {"ok": True}

    def _restart_services(self, args: dict) -> dict:
        args = args or {}
        if self._ops_cb is None:
            return {"error": "runtime ops not wired (in-process only)"}
        out = self._ops_cb("restart_services", {})
        services = out.get("services") if isinstance(out, dict) else None
        flt = str(args.get("services") or "").strip()
        if flt and isinstance(services, dict):
            keep = {s.strip() for s in flt.split(",") if s.strip()}
            out = {
                **out,
                "services": {k: v for k, v in services.items()
                             if k in keep},
            }
        return out

    def _restart_mcp(self, args: dict) -> dict:
        if self._reconnect_cb is None:
            return {"error": "MCP reconnect not wired"}
        path = (args or {}).get("path")
        out = self._reconnect_cb(str(path) if path else None)
        return out or {"ok": True}

    def _status(self, args: dict) -> dict:
        if self._ops_cb is None:
            return {
                "ok": True,
                "note": (
                    "runtime ops not wired — provider/model details "
                    "are only available in the in-process chat."
                ),
            }
        return self._ops_cb("status", {}) or {"ok": True}


class WebhookLocalHost:
    """Webhook-trigger tools (v0.9.25.0+) — the event-driven
    counterpart to the SchedulerLocalHost.

    Each trigger carries its OWN URL (``/trigger/<id>``) and random
    access key; an HTTP call to that URL fires the pre-configured
    prompt through ``Agent.chat_turn``. Setup mirrors the schedule
    tools (add / list / get / delete / enable / disable / run-now /
    runs / errors). The access key is returned in FULL only by
    ``add_webhook_trigger`` (and by ``get_webhook_trigger_url`` with
    ``reveal=true``) — every other view masks it.

    The HTTP listener is ``WebhookServer`` (src/agent/webhook.py,
    stdlib ThreadingHTTPServer on QDRANT_WEBHOOK_HOST:PORT, default
    127.0.0.1:8335). ``add_webhook_trigger`` auto-starts the daemon
    via the ``_auto_start`` callback (wired by ``Agent`` through
    ``McpManager.set_webhook_auto_start_callback``) so an LLM-built
    trigger is reachable immediately.
    """

    SERVER_NAME = "webhook-local"

    def __init__(self):
        self._mgr: Any = None
        self._auto_start: Callable[[], None] | None = None
        self._fire: Any = None  # fire(trigger, payload) -> dict

    def attach_manager(self, mgr_or_root: Any) -> None:
        try:
            from src.agent.webhook import WebhookManager

            if isinstance(mgr_or_root, WebhookManager):
                self._mgr = mgr_or_root
            elif isinstance(mgr_or_root, (str, bytes, os.PathLike)):
                self._mgr = WebhookManager(root=mgr_or_root)
            else:
                self._mgr = WebhookManager()
        except Exception:
            self._mgr = None

    def attach_auto_start(self, callback: Callable[[], None]) -> None:
        """Start the HTTP listener after a successful add (idempotent
        — mirrors ``SchedulerLocalHost.attach_auto_start``)."""
        self._auto_start = callback

    def attach_fire(self, callback: Any) -> None:
        """Wire the fire engine used by ``run_webhook_trigger_now``
        (set by ``Agent`` via ``McpManager``)."""
        self._fire = callback

    def _ensure_mgr(self) -> Any:
        if self._mgr is None:
            from src.agent.webhook import WebhookManager

            self._mgr = WebhookManager()
        return self._mgr

    def _resolve(self, ident: Any) -> Any:
        """Accept an id or a name; return (trigger, error-dict)."""
        mgr = self._ensure_mgr()
        if not isinstance(ident, str) or not ident.strip():
            return None, {"error": "'trigger_id' must be a non-empty string."}
        w = mgr.get(ident.strip())
        if w is None:
            w = mgr.find_by_name(ident.strip())
        if w is None:
            return None, {
                "error": (
                    f"no webhook trigger found for {ident!r}. Use "
                    "list_webhook_triggers to see available ids."
                ),
            }
        return w, None

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="add_webhook_trigger",
                description=(
                    "建立一個 webhook 觸發器：每個觸發器有自己的 URL"
                    "與 access key，HTTP 呼叫該 URL 即啟動預先設定好"
                    "的 prompt（有別於排程的時間觸發）。回傳 url 與 "
                    "access_key — key 只在這次回覆顯示一次，請提醒"
                    "使用者立即保存。支援 max_fires / expires_at "
                    "生命週期上限（與排程相同）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "觸發器名稱。"},
                        "prompt": {
                            "type": "string",
                            "description": (
                                "觸發時執行的 prompt；可含 {payload} 佔位符。"
                            ),
                        },
                        "max_fires": {"type": "integer",
                                      "description": "次數上限（可選）。"},
                        "expires_at": {"type": "string",
                                       "description": "ISO-8601 到期（可選）。"},
                        "llm": {
                            "type": "string",
                            "description": (
                                "可選。fire 時的模型 pin（provider:model，"
                                "限 openrouter / ollama；投放時就對目錄"
                                "驗證，fail-closed）。省略 = 預設 LLM 鏈。"
                            ),
                        },
                    },
                    "required": ["name", "prompt"],
                },
                server_name=self.SERVER_NAME,
                handler=self._add,
            ),
            LocalTool(
                name="list_webhook_triggers",
                description=(
                    "列出所有 webhook 觸發器（id / name / enabled / "
                    "status / fire_count / masked URL）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "enabled_only": {"type": "boolean",
                                         "description": "預設 false。"},
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list,
            ),
            LocalTool(
                name="get_webhook_trigger",
                description="讀單一觸發器的詳細狀態（lifecycle 快照，key 遮罩）。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "trigger_id": {"type": "string",
                                       "description": "id 或名稱。"},
                    },
                    "required": ["trigger_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get,
            ),
            LocalTool(
                name="get_webhook_trigger_url",
                description=(
                    "取得觸發器的完整 URL 與 access key（reveal=true "
                    "顯示完整 key；預設遮罩）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "trigger_id": {"type": "string",
                                       "description": "id 或名稱。"},
                        "reveal": {"type": "boolean",
                                   "description": "預設 false。"},
                    },
                    "required": ["trigger_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._url,
            ),
            LocalTool(
                name="delete_webhook_trigger",
                description="刪除觸發器（含其歷史記錄）。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "trigger_id": {"type": "string",
                                       "description": "id 或名稱。"},
                    },
                    "required": ["trigger_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._delete,
            ),
            LocalTool(
                name="enable_webhook_trigger",
                description="啟用觸發器（HTTP 呼叫才會生效）。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "trigger_id": {"type": "string",
                                       "description": "id 或名稱。"},
                    },
                    "required": ["trigger_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._enable,
            ),
            LocalTool(
                name="disable_webhook_trigger",
                description="停用觸發器（URL 仍在，呼叫回 403）。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "trigger_id": {"type": "string",
                                       "description": "id 或名稱。"},
                    },
                    "required": ["trigger_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._disable,
            ),
            LocalTool(
                name="run_webhook_trigger_now",
                description=(
                    "立刻手動執行一次觸發器（等同模擬一次 HTTP 呼叫，"
                    "可帶 payload 測試；寫入 history 與 session）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "trigger_id": {"type": "string",
                                       "description": "id 或名稱。"},
                        "payload": {"type": "string",
                                    "description": "測試用 payload（可選）。"},
                    },
                    "required": ["trigger_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._run_now,
            ),
            LocalTool(
                name="list_webhook_trigger_runs",
                description=(
                    "列出觸發器最近的執行記錄（ts / answer 前 500 字 / "
                    "error）—「這個觸發器上次跑得怎樣」用。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "trigger_id": {"type": "string",
                                       "description": "id 或名稱。"},
                        "limit": {"type": "integer",
                                  "description": "1-100，預設 10。"},
                    },
                    "required": ["trigger_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._runs,
            ),
            LocalTool(
                name="get_webhook_trigger_errors",
                description=(
                    "查觸發器為什麼失敗（last_error / 最近失敗記錄）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "trigger_id": {"type": "string",
                                       "description": "id 或名稱。"},
                    },
                    "required": ["trigger_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._errors,
            ),
        ]

    # ── handlers (LocalTool convention: (self, args: dict)) ────

    def _add(self, args: dict) -> dict:
        args = args or {}
        name = str(args.get("name") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        if not name:
            return {"error": "'name' is required"}
        if not prompt:
            return {"error": "'prompt' is required"}
        mgr = self._ensure_mgr()
        kwargs: dict[str, Any] = {"name": name, "prompt": prompt}
        if args.get("max_fires") is not None:
            try:
                mf = int(args["max_fires"])
            except (TypeError, ValueError):
                return {
                    "error": "'max_fires' must be a positive "
                             "integer or null.",
                }
            if mf <= 0:
                return {
                    "error": "'max_fires' must be a positive "
                             "integer or null.",
                }
            kwargs["max_fires"] = mf
        if args.get("expires_at"):
            kwargs["expires_at"] = str(args["expires_at"])
        # v0.9.111.0 — optional fire-time model pin (same ADD-time
        # catalog validation as the scheduler; fail-closed).
        if args.get("llm"):
            from src.agent.task_llm import coerce_llm_pin

            pin_val, pin_err = coerce_llm_pin(args["llm"])
            if pin_err:
                return {"error": str(pin_err)}
            kwargs["llm"] = pin_val
        try:
            from src.agent.webhook import WebhookTrigger

            w = mgr.save(WebhookTrigger(**kwargs))
        except (ValueError, TypeError) as e:
            return {"error": f"invalid webhook trigger: {e}"}
        # auto-start the HTTP listener so the new trigger is
        # reachable immediately (mirror SchedulerLocalHost._add).
        if self._auto_start is not None:
            try:
                self._auto_start()
            except Exception as e:
                logger.debug(
                    "[webhook-local] auto_start raised: %r", e,
                )
        url = None
        try:
            from src.agent.webhook import _webhook_host, _webhook_port

            url = (
                f"http://{_webhook_host()}:{_webhook_port()}"
                f"/trigger/{w.id}?key={w.access_key}"
            )
        except Exception:
            url = None
        return {
            "ok": True,
            "id": w.id,
            "name": w.name,
            "url": url,
            "access_key": w.access_key,
            "note": (
                "access_key 只顯示這一次 — 請提醒使用者立即保存；"
                "之後可用 get_webhook_trigger_url(reveal=true) 取回。"
            ),
            "max_fires": w.max_fires,
            "expires_at": w.expires_at,
            "status": "active",
        }

    def _list(self, args: dict) -> dict:
        args = args or {}
        enabled_only = bool(args.get("enabled_only", False))
        mgr = self._ensure_mgr()
        from src.agent.webhook import _webhook_host, _webhook_port

        base = f"http://{_webhook_host()}:{_webhook_port()}"
        rows = []
        for w in mgr.list():
            if enabled_only and not w.enabled:
                continue
            rows.append({
                "id": w.id,
                "name": w.name,
                "prompt_preview": w.prompt[:120],
                "enabled": w.enabled,
                "status": w.status or ("active" if w.enabled
                                       else "disabled"),
                "fire_count": w.fire_count,
                "last_fire_at": w.last_fire_at,
                "url": f"{base}/trigger/{w.id}?key={w.masked_key()}",
            })
        return {"count": len(rows), "triggers": rows}

    def _get(self, args: dict) -> dict:
        w, err = self._resolve((args or {}).get("trigger_id"))
        if err:
            return err
        mgr = self._ensure_mgr()
        prog = mgr.get_progress(w.id)
        if prog is None:
            return {"error": f"trigger {w.id!r} disappeared mid-call."}
        prog["access_key_masked"] = w.masked_key()
        return {"ok": True, "id": w.id, "progress": prog}

    def _url(self, args: dict) -> dict:
        args = args or {}
        w, err = self._resolve(args.get("trigger_id"))
        if err:
            return err
        reveal = bool(args.get("reveal", False))
        from src.agent.webhook import _webhook_host, _webhook_port

        base = f"http://{_webhook_host()}:{_webhook_port()}"
        key = w.access_key if reveal else w.masked_key()
        return {
            "ok": True,
            "id": w.id,
            "name": w.name,
            "url": f"{base}/trigger/{w.id}?key={key}",
            "access_key": key if reveal else None,
            "note": (
                "HTTP 呼叫（GET 或 POST）此 URL 即啟動 prompt；"
                "POST body 可帶 {\"payload\": ...} 注入 prompt。"
            ) if reveal else None,
        }

    def _delete(self, args: dict) -> dict:
        w, err = self._resolve((args or {}).get("trigger_id"))
        if err:
            return err
        mgr = self._ensure_mgr()
        return {"ok": True, "deleted": mgr.delete(w.id), "id": w.id}

    def _enable(self, args: dict) -> dict:
        w, err = self._resolve((args or {}).get("trigger_id"))
        if err:
            return err
        out = self._ensure_mgr().set_enabled(w.id, True)
        if out is None:
            return {"error": f"trigger {w.id!r} disappeared mid-call."}
        if self._auto_start is not None:
            try:
                self._auto_start()
            except Exception:
                pass
        return {"ok": True, "id": out.id, "enabled": out.enabled,
                "status": out.status or "active"}

    def _disable(self, args: dict) -> dict:
        w, err = self._resolve((args or {}).get("trigger_id"))
        if err:
            return err
        out = self._ensure_mgr().set_enabled(w.id, False)
        if out is None:
            return {"error": f"trigger {w.id!r} disappeared mid-call."}
        return {"ok": True, "id": out.id, "enabled": out.enabled,
                "status": out.status or "disabled"}

    def _run_now(self, args: dict) -> dict:
        args = args or {}
        w, err = self._resolve(args.get("trigger_id"))
        if err:
            return err
        if self._fire is None:
            return {
                "error": (
                    "fire engine not wired — start the webhook "
                    "server first (add_webhook_trigger auto-starts "
                    "it, or the boot auto-resume)."
                ),
            }
        try:
            return self._fire(w, args.get("payload"))
        except Exception as e:
            return {"error": f"fire failed: {e!r}"}

    def _runs(self, args: dict) -> dict:
        args = args or {}
        w, err = self._resolve(args.get("trigger_id"))
        if err:
            return err
        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            return {"error": "'limit' must be an integer."}
        if limit <= 0 or limit > 100:
            return {"error": "'limit' must be between 1 and 100."}
        rows = self._ensure_mgr().get_history(w.id, limit=limit)
        return {"ok": True, "id": w.id, "count": len(rows),
                "runs": rows}

    def _errors(self, args: dict) -> dict:
        w, err = self._resolve((args or {}).get("trigger_id"))
        if err:
            return err
        mgr = self._ensure_mgr()
        head = mgr.get(w.id)
        if head is None:
            return {"error": f"trigger {w.id!r} disappeared mid-call."}
        rows = [
            r for r in mgr.get_history(w.id, limit=20)
            if r.get("error")
        ]
        return {
            "ok": True, "id": w.id,
            "last_error": head.last_error,
            "last_error_at": head.last_error_at,
            "status": head.status or ("active" if head.enabled
                                      else "disabled"),
            "history_count": len(rows),
            "errors": rows[:5],
        }


# ─── Teams desktop-assist (v0.9.69.0+) ────────────────────────────


class TeamsAssistLocalHost:
    """Teams desktop-assist tools (v0.9.69.0+) — the zero-permission
    Teams helper that operates the user's OWN logged-in Teams desktop
    client via the windows-mcp fleet (no Teams API / no OAuth).

    Seven tools over src/teams_assist_tools.py: teams_assist_status /
    teams_assist_check / teams_assist_start_watch /
    teams_assist_stop_watch / teams_assist_reply / teams_assist_pause
    / teams_assist_resume. v0.9.70.0+ adds the interference-free UIA
    channel (``QDRANT_TEAMS_ASSIST_SEND_MODE=uia``): teams_assist_
    send_uia (ValuePattern fill + InvokePattern 傳送 — no keyboard/
    mouse, no focus steal) and teams_assist_read_uia (TextPattern chat
    read — replaces screenshot OCR).

    The screen-watch thread lives INSIDE this host (OnvifLocalHost
    pattern); on a screen change it fires the fire engine (``attach_
    fire`` push-down — WebhookLocalHost pattern) which runs a
    fresh-Agent judge-then-act turn. Mock-first:
    ``QDRANT_TEAMS_ASSIST_MODE=mock`` (default) never grabs the real
    screen. ``teams_assist_reply`` is the write gate (hicloud
    pattern): ``confirm=true`` requires the AUTO switch (fired turns)
    or ``manual=true`` (user-requested sends); ``confirm=false``
    records a draft only.
    """

    SERVER_NAME = "teams-assist"

    def __init__(self):
        self._watcher: Any = None
        self._fire: Any = None

    # -- wiring (mirror of WebhookLocalHost.attach_fire) -----------

    def attach_fire(self, callback: Any) -> None:
        """Wire the fire engine used by the watch thread (set by
        ``Agent`` via ``McpManager.set_teams_assist_fire_callback``)."""
        self._fire = callback

    def _ensure_watcher(self) -> Any:
        if self._watcher is None:
            from src.teams_assist_tools import TeamsAssistWatcher

            self._watcher = TeamsAssistWatcher(fire=self._fire)
        return self._watcher

    def resume_watch(self) -> int:
        """Boot auto-resume: restart the watch thread only when the
        persisted state says it was running (threads die with the
        process; state survives in ``teams_assist/state.json``).
        Idempotent — a live thread is skipped. Fail-closed: an empty
        ``TEAMS_ASSIST_ALLOW_CHATS`` never starts the watcher."""
        try:
            from src.teams_assist_tools import _load_state

            state = _load_state()
            if not (state.get("watch") or {}).get("running"):
                return 0
            if self._ensure_watcher().start():
                return 1
        except Exception as e:
            logger.debug(
                "[teams-assist] resume_watch failed: %r", e,
            )
        return 0

    def stop_watch(self) -> None:
        """Stop the watch thread (Agent.shutdown)."""
        w = self._watcher
        if w is not None:
            try:
                w.stop()
            except Exception as e:
                logger.debug("[teams-assist] stop_watch: %r", e)

    # -- handlers (LocalTool convention: (self, args: dict)) ────────

    def _status(self, args: dict) -> dict:
        from src.teams_assist_tools import (
            _allow_all, _allow_chats, _assist_mode, _auto_enabled,
            _load_state, _min_fire_interval, _persona,
            _poll_interval, _send_mode, _watch_mode, _window_rect,
        )

        state = _load_state()
        w = self._watcher
        audit = list(state.get("audit") or [])[-10:]
        return {
            "ok": True,
            "mode": _assist_mode(),
            "auto": _auto_enabled(),
            "send_mode": _send_mode(),
            "watch_mode": _watch_mode(),
            "allow_chats": _allow_chats(),
            "allow_all": _allow_all(),
            "window_rect": list(_window_rect() or ()) or None,
            "persona": _persona(),
            "watch": {
                "running": bool(w and w.is_running),
                "interval_s": _poll_interval(),
                "persisted": state.get("watch"),
            },
            "min_fire_interval_s": _min_fire_interval(),
            "paused": bool(state.get("paused")),
            "auto_send_allowed": bool(state.get("auto_send_allowed")),
            "grab_failures": state.get("grab_failures") or 0,
            "last_check_at": state.get("last_check_at"),
            "last_fire_at": state.get("last_fire_at"),
            "gate": state.get("gate") or {},
            "recent_audit": audit,
        }

    def _config(self, args: dict) -> dict:
        """LLM-facing TEMPORARY config (v0.9.72.0+) — validates and
        writes os.environ only (dies with the process; .env wins again
        on the next boot)."""
        from src.teams_assist_tools import _set_config

        return _set_config(args or {})

    def _list_chats(self, args: dict) -> dict:
        """List the Teams chat titles visible in the client UI plus
        their filter/watch status (v0.9.76.0+)."""
        from src.teams_assist_tools import (
            _active_chat_title, _allow_all, _allow_chats, _chat_allowed,
            _chat_slug, _engaged_chat_sessions, _load_state,
            _uia_chat_titles, _watch_mode,
        )

        engaged = _engaged_chat_sessions()
        engaged_sids = {r["sid"] for r in engaged}
        titles = _uia_chat_titles()
        chats = [
            {
                "title": t,
                "allowed": _chat_allowed(t),
                "engaged": f"teamsassist-{_chat_slug(t)}" in engaged_sids,
            }
            for t in titles
        ]
        state = _load_state()
        active = _active_chat_title()
        # Deterministic presentation table — the LLM echoes this
        # instead of summarizing the list down to one line.
        running = bool((state.get("watch") or {}).get("running"))
        watch_mode = _watch_mode()
        filters = _allow_chats()
        lines = [
            f"watch：{'執行中' if running else '未執行'}"
            f"（watch_mode={watch_mode}）",
            "過濾條件："
            + ("*（所有聊天）" if _allow_all()
               else ("、".join(filters) if filters else "（空白 — "
                                      "fail-closed）")),
            (
                f"目前作用中：{active}（"
                + ("符合過濾條件" if _chat_allowed(active)
                   else "不符合過濾條件") + "）"
            ) if active else "目前作用中：（無法解析）",
            f"聊天清單（UIA 直讀，{len(chats)} 個）：",
        ]
        for c in chats:
            lines.append(
                f"• {c['title']}\u3000"
                f"{'✅ 符合' if c['allowed'] else '❌ 不符合'}過濾"
                f"\u3000{'👁 已watch' if c['engaged'] else '— 未watch'}"
            )
        if engaged:
            lines.append(f"已 watch 的 session（{len(engaged)} 個）：")
            for r in engaged:
                lines.append(
                    f"• {r['title']}\u3000{r['messages']} 則"
                    f"\u3000{str(r.get('updated_at') or '')[:19]}"
                )
        else:
            lines.append("已 watch 的 session：（尚無 — 沒有任何聊天"
                         "被助理處理過）")
        summary = "\n".join(lines)
        return {
            "ok": True,
            "watch_running": running,
            "watch_mode": watch_mode,
            "filters": filters,
            "allow_all": _allow_all(),
            "active_chat": active,
            "active_chat_allowed": _chat_allowed(active),
            "chats": chats,
            "count": len(chats),
            "engaged_sessions": engaged,
            "summary": summary,
            "note": (
                "chats = UIA 直讀 Teams 左側清單（best-effort 啟發式，"
                "可能含少數雜訊）；engaged = 已有該聊天的助理 session"
                "（被 watch 過/正在 watch）；allowed = 符合目前過濾條件。"
                "請把 summary 表格完整呈現給使用者（不要壓縮成一句話）。"
            ),
        }

    def _drafts(self, args: dict) -> dict:
        """Pending / sent draft queue from the audit ring (v0.9.78.0+)
        — the user-facing way to SEE what the watch drafted while the
        fire ran in a background task."""
        from src.teams_assist_tools import _load_state

        state = _load_state()
        rows = [
            r for r in (state.get("audit") or [])
            if r.get("kind") in ("draft", "sent", "sent-uia",
                                 "send-uia-failed")
        ][-20:][::-1]
        lines = [
            f"草稿佇列（{len(rows)} 筆，新→舊；AUTO="
            f"{'on' if state.get('auto_send_allowed') else 'off'}）：",
        ]
        for r in rows:
            label = {
                "draft": "✏ 草稿（未送出）",
                "sent": "✅ 已送出（windows-mcp）",
                "sent-uia": "✅ 已送出（UIA）",
                "send-uia-failed": "⚠ UIA 送出失敗",
            }.get(r.get("kind"), r.get("kind"))
            lines.append(
                f"• [{str(r.get('ts') or '')[:19]}] {label}"
                f"\u3000聊天：{r.get('chat') or '—'}\n"
                f"  {str(r.get('text') or '')[:200]}"
            )
        summary = "\n".join(lines) if rows else "草稿佇列：（空）"
        return {
            "ok": True,
            "count": len(rows),
            "drafts": [
                {
                    "ts": r.get("ts"),
                    "status": r.get("kind"),
                    "chat": r.get("chat"),
                    "text": r.get("text"),
                    "manual": r.get("manual"),
                }
                for r in rows
            ],
            "summary": summary,
            "note": (
                "請把 summary 完整呈現給使用者。核准送出某筆草稿 → "
                "teams_assist_send_uia(text, chat, confirm=true) 或"
                "使用者明確要求時 manual=true。"
            ),
        }

    def _pause(self, args: dict) -> dict:
        from src.teams_assist_tools import _audit, _load_state, _save_state

        state = _load_state()
        state["paused"] = True
        _audit(state, "paused", by="teams_assist_pause")
        _save_state(state)
        return {"ok": True, "paused": True,
                "note": "watch 已暫停（poll 仍會跑但不觸發 fire）"}

    def _resume(self, args: dict) -> dict:
        from src.teams_assist_tools import _audit, _load_state, _save_state

        state = _load_state()
        state["paused"] = False
        state["grab_failures"] = 0
        _audit(state, "resumed", by="teams_assist_resume")
        _save_state(state)
        return {"ok": True, "paused": False}

    def list_tools(self) -> list["LocalTool"]:
        return [
            LocalTool(
                name="teams_assist_status",
                description=(
                    "Teams 桌面助理狀態：watch 是否運行、mode（mock/"
                    "live）、AUTO 開關、白名單、最近 audit（fire/"
                    "draft/sent）。觸發詞：『teams 助理狀態』『暫停了"
                    "嗎』。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._status,
            ),
            LocalTool(
                name="teams_assist_config",
                description=(
                    "暫時設定 Teams 助理組態（mode/send_mode/watch_mode/"
                    "auto/allow_chats/my_name/reply_style/window_rect/"
                    "poll_interval/min_fire_interval；只改 process，"
                    "重啟回復 .env）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["mock", "live"],
                        },
                        "send_mode": {
                            "type": "string",
                            "enum": ["windows-mcp", "uia"],
                        },
                        "watch_mode": {
                            "type": "string",
                            "enum": ["screen", "uia"],
                        },
                        "auto": {
                            "type": "string",
                            "enum": ["on", "off"],
                        },
                        "allow_chats": {
                            "type": "string",
                            "description": (
                                "聊天過濾條件（逗號分隔；每項為標題"
                                "完全比對或含 * 的過濾樣式；'*' = 全部）"
                            ),
                        },
                        "my_name": {"type": "string"},
                        "my_role": {"type": "string"},
                        "reply_style": {"type": "string"},
                        "window_rect": {
                            "type": "string",
                            "description": "'x,y,w,h'；null = 清除",
                        },
                        "poll_interval": {
                            "type": "number",
                            "description": "輪詢秒數（10-300）",
                        },
                        "min_fire_interval": {
                            "type": "number",
                            "description": "fire 最小間隔秒（0-3600）",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._config,
            ),
            LocalTool(
                name="teams_assist_list_chats",
                description=(
                    "列出 Teams 聊天標題（UIA 直讀）與 allowed/engaged "
                    "狀態。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._list_chats,
            ),
            LocalTool(
                name="teams_assist_drafts",
                description=(
                    "列出 watch 記下的草稿佇列（時間/聊天/狀態/全文）。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._drafts,
            ),
            LocalTool(
                name="teams_assist_check",
                description=(
                    "截取目前螢幕（Teams 視窗）並存進 write sandbox，"
                    "回傳截圖路徑 — 之後用 describe_image 讀內容。"
                    "mock 模式不截真實畫面。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._check,
            ),
            LocalTool(
                name="teams_assist_start_watch",
                description=(
                    "啟動 Teams 畫面監看（daemon thread）：畫面變化才"
                    "觸發 LLM 回合（有冷卻與失敗自動暫停保護）。需要 "
                    "TEAMS_ASSIST_ALLOW_CHATS 白名單（fail-closed）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "interval_s": {
                            "type": "integer",
                            "description": (
                                "輪詢間隔秒數（10-300；預設取 env "
                                "QDRANT_TEAMS_ASSIST_POLL_INTERVAL=15）。"
                            ),
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._start_watch,
            ),
            LocalTool(
                name="teams_assist_stop_watch",
                description="停止 Teams 畫面監看執行緒。",
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._stop_watch,
            ),
            LocalTool(
                name="teams_assist_reply",
                description=(
                    "註冊一次 Teams 回覆（write gate）：AUTO 自動回合用 "
                    "confirm=true；手動送出 confirm=true+manual=true；"
                    "記草稿 confirm=false。不打字 — 僅送出後註冊+審計。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "回覆內容（使用者本人的口吻）。",
                        },
                        "chat": {
                            "type": "string",
                            "description": "聊天標題（可從截圖辨識）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "true = 記錄為已送出（需要 AUTO 或 "
                                "manual=true）；false = 只記草稿。"
                            ),
                        },
                        "manual": {
                            "type": "boolean",
                            "description": (
                                "使用者明確要求送出時設 true（繞過 "
                                "AUTO 閘門）。"
                            ),
                        },
                    },
                    "required": ["text", "confirm"],
                },
                server_name=self.SERVER_NAME,
                handler=self._reply,
            ),
            LocalTool(
                name="teams_assist_send_uia",
                description=(
                    "（UIA 模式）背景送出 Teams 訊息：ValuePattern 填入 + "
                    "InvokePattern 按傳送（零鍵鼠零搶焦點）。輸入框有"
                    "內容 → 讓路不送。write gate 同 teams_assist_reply。"
                    "回覆保持單一段落。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "回覆內容（使用者本人的口吻，單一段落）。",
                        },
                        "chat": {
                            "type": "string",
                            "description": "聊天標題（審計用）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "true = 執行 UIA 送出（需要 AUTO 或 "
                                "manual=true）；false = 只記草稿。"
                            ),
                        },
                        "manual": {
                            "type": "boolean",
                            "description": (
                                "使用者明確要求送出時設 true（繞過 "
                                "AUTO 閘門）。"
                            ),
                        },
                    },
                    "required": ["text", "confirm"],
                },
                server_name=self.SERVER_NAME,
                handler=self._send_uia,
            ),
            LocalTool(
                name="teams_assist_read_uia",
                description=(
                    "（UIA 模式）以 TextPattern 直接讀出 Teams 聊天文字"
                    "內容 — 零干擾、比截圖 OCR 可靠。輸出含左側清單與"
                    "訊息樹（作者/時間/引文），請自行判讀出聊天標題與"
                    "最新「他人」訊息。唯讀。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._read_uia,
            ),
            LocalTool(
                name="teams_assist_pause",
                description=(
                    "暫停 Teams 自動助理（watch 繼續 poll 但不觸發）。"
                    "觸發詞：『暫停 teams』。"
                ),
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._pause,
            ),
            LocalTool(
                name="teams_assist_resume",
                description="恢復 Teams 自動助理（清除暫停與失敗計數）。",
                input_schema={"type": "object", "properties": {}},
                server_name=self.SERVER_NAME,
                handler=self._resume,
            ),
        ]

    def _start_watch(self, args: dict) -> dict:
        args = args or {}
        from src.teams_assist_tools import (
            _assist_mode, _audit, _load_state, _save_state,
        )

        # Validate arguments BEFORE the idempotency check — bad input
        # must fail loudly even when a watch is already running.
        interval = args.get("interval_s")
        if interval is not None:
            try:
                os.environ["QDRANT_TEAMS_ASSIST_POLL_INTERVAL"] = str(
                    max(10, min(300, int(interval))),
                )
            except (TypeError, ValueError):
                return {"error": "'interval_s' must be an integer."}
        w = self._ensure_watcher()
        if w.is_running:
            return {"ok": True, "started": False,
                    "note": "watch 已在運行（idempotent）"}
        if not w.start():
            return {
                "error": (
                    "watch 無法啟動 — TEAMS_ASSIST_ALLOW_CHATS 為空"
                    "（fail-closed）。請先在 .env 設定允許處理的聊天"
                    "標題。"
                ),
            }
        state = _load_state()
        _audit(state, "watch-started",
               interval=state.get("watch", {}).get("interval_s"))
        _save_state(state)
        return {
            "ok": True, "started": True,
            "interval_s": state.get("watch", {}).get("interval_s"),
            "mode": _assist_mode(),
        }

    def _stop_watch(self, args: dict) -> dict:
        stopped = self.stop_watch()
        return {"ok": True, "stopped": bool(stopped)}

    def _check(self, args: dict) -> dict:
        import hashlib as _hashlib

        from src.teams_assist_tools import (
            _assist_mode, _default_grab, _mock_grab, _screen_signature,
        )

        if _assist_mode() == "mock":
            # Mock mode: the "screen" is mock_screen.txt content — no
            # image to sign, no real grab ever happens.
            png = _mock_grab()
            return {
                "ok": True, "mode": "mock", "path": None,
                "uniform": False,
                "hash12": _hashlib.sha1(png).hexdigest()[:12],
                "note": (
                    "mock 模式 — 不截取真實畫面；live 模式才會回傳"
                    "可 describe_image 的截圖路徑。"
                ),
            }
        try:
            png = _default_grab()
            sig = _screen_signature(png)
        except Exception as e:
            return {"error": f"screen grab failed: {e!r}"}
        try:
            root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT", "./sandbox",
            )
            d = root / "teams_assist"
            d.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = d / f"screen-{ts}.png"
            path.write_bytes(png)
            # Keep the last 3 shots — don't litter the sandbox.
            old = sorted(d.glob("screen-*.png"))
            for p in old[:-3]:
                try:
                    p.unlink()
                except OSError:
                    pass
        except Exception as e:
            return {"error": f"sandbox write failed: {e!r}"}
        return {
            "ok": True, "mode": "live",
            "path": path.resolve().as_posix(),
            "uniform": sig["uniform"],
            "hash12": sig["hash"][:12],
            "note": (
                "用 describe_image(path) 讀出聊天標題與最新訊息。"
            ),
        }

    def _read_uia(self, args: dict) -> dict:
        """UIA TextPattern chat read — zero interference (no OCR)."""
        from src.teams_assist_tools import _uia_ops

        out = _uia_ops("read")
        if not out.get("ok"):
            return {
                "ok": False, "uia": out,
                "note": (
                    "UIA 讀取失敗 — 改用 teams_assist_check + "
                    "describe_image（截圖 OCR）路徑。"
                ),
            }
        return {
            "ok": True,
            "chars": out.get("chars"),
            "text": out.get("text"),
            "window": out.get("window"),
            "note": (
                "文字含左側聊天清單與訊息樹 — 請自行判讀出：目前"
                "聊天標題、最新「他人」訊息與時間。"
            ),
        }

    def _send_uia(self, args: dict) -> dict:
        """UIA background send (fill via ValuePattern + invoke 傳送 via
        InvokePattern — no keyboard/mouse, no focus steal). Write gate
        identical to ``teams_assist_reply``; the PS safety chain yields
        when the compose box already has content."""
        from src.teams_assist_tools import (
            _audit, _load_state, _save_state, _uia_ops,
        )

        args = args or {}
        text = str(args.get("text") or "").strip()
        if not text:
            return {"error": "'text' is required"}
        chat = str(args.get("chat") or "").strip() or None
        confirm = bool(args.get("confirm", False))
        manual = bool(args.get("manual", False))
        state = _load_state()
        if not confirm:
            _audit(state, "draft", chat=chat, text=text[:500],
                   manual=manual)
            _save_state(state)
            return {
                "ok": True, "recorded": "draft", "chat": chat,
                "note": "已登記為草稿（confirm=false — 未送出）。",
            }
        if not manual and not state.get("auto_send_allowed"):
            return {
                "error": (
                    "AUTO 閘門：QDRANT_TEAMS_ASSIST_AUTO 未開啟 —"
                    " 自動觸發的回合只允許記錄草稿（confirm="
                    "false）；使用者明確要求送出時才帶 manual="
                    "true。"
                ),
            }
        out = _uia_ops("send", text)
        if not out.get("ok") or not out.get("sent"):
            _audit(state, "send-uia-failed", chat=chat,
                   reason=str(out.get("reason")), text=text[:200])
            _save_state(state)
            return {
                "ok": False, "sent": False, "uia": out,
                "note": (
                    "未送出 — 依 reason 處理：compose-not-empty-"
                    "yield = 讓路（改記草稿）；send-button-still-"
                    "present = 請人工確認，絕不重試送出。"
                ),
            }
        _audit(state, "sent-uia", chat=chat, text=text[:500],
               manual=manual)
        _save_state(state)
        return {
            "ok": True, "sent": True, "chat": chat,
            "uia": {"chars": out.get("chars"),
                    "elapsed_s": out.get("elapsed_s")},
            "note": (
                "已透過 UIA 背景送出（無鍵鼠干擾）；可用 "
                "teams_assist_read_uia 複核訊息是否出現。"
            ),
        }

    def _reply(self, args: dict) -> dict:
        from src.teams_assist_tools import (
            _audit, _load_state, _save_state,
        )

        args = args or {}
        text = str(args.get("text") or "").strip()
        if not text:
            return {"error": "'text' is required"}
        chat = str(args.get("chat") or "").strip() or None
        confirm = bool(args.get("confirm", False))
        manual = bool(args.get("manual", False))
        state = _load_state()
        if confirm:
            if not manual and not state.get("auto_send_allowed"):
                return {
                    "error": (
                        "AUTO 閘門：QDRANT_TEAMS_ASSIST_AUTO 未開啟 —"
                        " 自動觸發的回合只允許記錄草稿（confirm="
                        "false）；使用者明確要求送出時才帶 manual="
                        "true。"
                    ),
                }
            kind = "sent"
        else:
            kind = "draft"
        _audit(state, kind, chat=chat, text=text[:500],
               manual=manual)
        _save_state(state)
        return {
            "ok": True, "recorded": kind, "chat": chat,
            "length": len(text),
            "note": (
                "已登記審計紀錄；請用截圖比對確認訊息真的出現在 "
                "Teams 視窗中。"
            ) if kind == "sent" else "已登記為草稿（未送出）。",
        }


class KeepLocalHost:
    """Google Keep / 本地筆記本工具（v0.9.89.0+, server ``keep-local``).

    三種模式（``KEEP_MODE``）：
    * ``local``（★自建本地筆記本）— JSON 檔持久化
      （$QDRANT_RAG_HOME/notes/notes.json，可用 KEEP_NOTES_PATH 覆寫），
      免 Google 授權 — 個人帳戶的預設筆記本；
    * ``mock`` — 完全模擬的記憶體記事本（不落盤）；
    * ``live`` — 官方 Keep API v1 REST（keep.googleapis.com —
      create/get/list/delete，**無 update**；enterprise-only，個人帳戶
      consent 會回 invalid_scope）。
    寫入需 per-call ``confirm=true``（live 另需
    ``KEEP_WRITES_ENABLED=on``）。
    """

    SERVER_NAME = "keep-local"

    def __init__(self):
        from src.keep_tools import KeepEngine

        self.engine = KeepEngine()

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="keep_list_notes",
                description=(
                    "列出/搜尋記事（標題+內文關鍵字；local 筆記本支援 "
                    "category 過濾）。例：'我的採購清單' / '列一下日記'。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "關鍵字（可空 = 全部）。",
                        },
                        "category": {
                            "type": "string",
                            "description": "分類過濾（僅 local 模式）。",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "最多回傳筆數（預設 20）。",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list_notes,
            ),
            LocalTool(
                name="keep_create_note",
                description=(
                    "建立一則記事（KEEP_MODE=local 時寫入本地筆記本；"
                    "需要 confirm=true）。body 支援 markdown（原樣"
                    "保存）。例：'幫我記下 XXX'。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string",
                                  "description": "記事標題（必填）。"},
                        "body": {"type": "string",
                                 "description": "記事內文（markdown）。"},
                        "category": {
                            "type": "string",
                            "description": "分類（local 模式支援）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "防意外寫入 — 必須 true。",
                        },
                    },
                    "required": ["title", "confirm"],
                },
                server_name=self.SERVER_NAME,
                handler=self._create_note,
            ),
            LocalTool(
                name="keep_update_note",
                description=(
                    "修改既有記事（標題/內文/分類擇一或全部；需要 "
                    "confirm=true）。僅 local/mock 模式支援 — 官方 "
                    "Keep API 沒有 update 方法。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "note_id": {
                            "type": "string",
                            "description": "記事 id。",
                        },
                        "title": {"type": "string",
                                  "description": "新標題（可空 = 不改）。"},
                        "body": {"type": "string",
                                 "description": "新內文 markdown"
                                                "（可空 = 不改）。"},
                        "category": {
                            "type": "string",
                            "description": "新分類（可空 = 不改）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "防意外寫入 — 必須 true。",
                        },
                    },
                    "required": ["note_id", "confirm"],
                },
                server_name=self.SERVER_NAME,
                handler=self._update_note,
            ),
            LocalTool(
                name="keep_preview_note",
                description=(
                    "產生記事的 markdown 檢視檔（寫入 write sandbox 的 "
                    "notes/ 目錄，回傳路徑）— 供檢視/下載。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "note_id": {
                            "type": "string",
                            "description": "記事 id。",
                        },
                    },
                    "required": ["note_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._preview_note,
            ),
            LocalTool(
                name="keep_get_note",
                description=(
                    "讀取一則 Keep 記事的完整內容（id 來自 "
                    "keep_list_notes 的回傳）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "note_id": {
                            "type": "string",
                            "description": "記事 id（notes/<id> 或純 id）。",
                        },
                    },
                    "required": ["note_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._get_note,
            ),
            LocalTool(
                name="keep_delete_note",
                description=(
                    "刪除一則 Keep 記事（需要 confirm=true — 不可逆）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "note_id": {
                            "type": "string",
                            "description": "記事 id。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "防意外寫入 — 必須 true。",
                        },
                    },
                    "required": ["note_id", "confirm"],
                },
                server_name=self.SERVER_NAME,
                handler=self._delete_note,
            ),
            LocalTool(
                name="keep_status",
                description=(
                    "Keep 整合狀態（mode / token 是否就緒 / 寫入閘）。"
                ),
                server_name=self.SERVER_NAME,
                handler=self._status,
            ),
        ]

    def _list_notes(self, args: dict) -> dict:
        args = args or {}
        return self.engine.list_notes(
            str(args.get("query") or ""),
            max_results=args.get("max_results") or 20,
        )

    def _create_note(self, args: dict) -> dict:
        args = args or {}
        return self.engine.create_note(
            str(args.get("title") or ""),
            str(args.get("body") or ""),
            confirm=args.get("confirm"),
            category=str(args.get("category") or ""),
        )

    def _update_note(self, args: dict) -> dict:
        args = args or {}
        return self.engine.update_note(
            str(args.get("note_id") or ""),
            title=(str(args["title"])
                   if args.get("title") is not None else None),
            body=(str(args["body"])
                  if args.get("body") is not None else None),
            category=(str(args["category"])
                      if args.get("category") is not None else None),
            confirm=args.get("confirm"),
        )

    def _preview_note(self, args: dict) -> dict:
        args = args or {}
        out = self.engine.note_markdown(str(args.get("note_id") or ""))
        if out.get("error"):
            return out
        try:
            root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT", "./sandbox",
            )
            d = root / "notes"
            d.mkdir(parents=True, exist_ok=True)
            nid = out["note"].get("id") or "note"
            title = out["note"].get("title") or "note"
            slug = "".join(
                c for c in title if c.isalnum() or c in "-_ ")[:40]
            slug = slug.strip().replace(" ", "-") or "note"
            path = d / f"{nid}-{slug}.md"
            tmp = path.with_suffix(".md.tmp")
            tmp.write_text(out["markdown"], encoding="utf-8")
            os.replace(tmp, path)  # atomic write
        except Exception as e:
            return {"error": f"sandbox write failed: {e!r}"}
        return {
            "ok": True,
            "path": path.resolve().as_posix(),
            "markdown_chars": len(out["markdown"]),
            "markdown": out["markdown"],
            "note": (
                "檢視檔已寫入 write sandbox（chat 內可下載）；在回覆中"
                "以 fenced markdown 呈現內容即為檢視圖。"
            ),
        }

    def _get_note(self, args: dict) -> dict:
        args = args or {}
        return self.engine.get_note(str(args.get("note_id") or ""))

    def _delete_note(self, args: dict) -> dict:
        args = args or {}
        return self.engine.delete_note(
            str(args.get("note_id") or ""), confirm=args.get("confirm"),
        )

    def _status(self, args: dict) -> dict:
        return self.engine.status()


class JevLocalHost:
    """Jev 分類器工具（v0.9.93.0+, server ``jev-classifier-local``).

    以 Jev 決策模型做「分類器」：輸入吃指定 JSON 格式（inline
    ``payload_json`` 或 sandbox 內 JSON ``path``），批次對每個 item
    跑一次 jev_choice（ThreadPoolExecutor 併發），回傳 choice +
    probabilities + 每類別統計；可選寫出結果 JSON 到 sandbox。
    ``JEV_MODE=mock``（預設）為確定性模擬（不連網）；live 走真實
    Jev 決策（重用 OPENROUTER_API_KEY）。
    """

    SERVER_NAME = "jev-classifier-local"

    def __init__(self):
        from src.jev_tools import JevClassifierEngine

        self.engine = JevClassifierEngine()

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="jev_classify",
                description=(
                    "批次文字分類（Jev 分類器）：輸入指定 JSON 格式"
                    "（inline payload_json 或 sandbox 內 JSON 檔案 "
                    "path），對每個 item 回傳最符合的類別 + 機率。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "payload_json": {
                            "type": "string",
                            "description": "分類請求 JSON（字串形式；"
                                           "與 path 擇一）。",
                        },
                        "path": {
                            "type": "string",
                            "description": "sandbox 內 JSON 檔案路徑"
                                           "（與 payload_json 擇一）。",
                        },
                        "write_result": {
                            "type": "boolean",
                            "description": "將結果 JSON 寫入 sandbox 的 "
                                           "jev/ 目錄（預設 false）。",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._classify,
            ),
            LocalTool(
                name="jev_schema",
                description=(
                    "回傳 jev_classify 的指定 JSON 輸入格式與範例"
                    "（組請求前先呼叫確認格式）。"
                ),
                server_name=self.SERVER_NAME,
                handler=self._schema,
            ),
            LocalTool(
                name="jev_classify_status",
                description=(
                    "Jev 分類器狀態（mode/model/批次上限/併發/key 就緒）。"
                ),
                server_name=self.SERVER_NAME,
                handler=self._status,
            ),
        ]

    # ── sandbox helpers ─────────────────────────────────────────
    @staticmethod
    def _resolve_file(path: str) -> Any | None:
        """JSON 輸入檔：write root → read root 皆試。"""
        for env_name in ("QDRANT_FILESYSTEM_WRITE_ROOT",
                         "QDRANT_FILESYSTEM_READ_ROOT"):
            try:
                root = _resolve_filesystem_root(env_name, None)
            except Exception:
                continue
            if not root:
                continue
            cand = root / path
            if cand.is_file():
                return cand
            cand2 = root / os.path.basename(path)
            if cand2.is_file():
                return cand2
        return None

    def _classify(self, args: dict) -> dict:
        args = args or {}
        payload_json = str(args.get("payload_json") or "").strip()
        path = str(args.get("path") or "").strip()
        if not payload_json and not path:
            return {"error": "需要 payload_json（inline JSON）或 "
                             "path（sandbox JSON 檔案）— 格式可用 "
                             "jev_schema 查詢"}
        if payload_json and path:
            payload_json = ""  # inline 優先
        if not payload_json:
            cand = self._resolve_file(path)
            if cand is None:
                return {"error": f"找不到 JSON 檔案: {path}（write/"
                                 "read sandbox 皆未命中）"}
            try:
                payload_json = cand.read_text(encoding="utf-8")
            except Exception as e:
                return {"error": f"JSON 檔案讀取失敗: {e!r}"}
        parsed = self.engine.parse_payload(payload_json)
        if parsed.get("error"):
            return parsed
        out = self.engine.classify_request(parsed)
        if out.get("error"):
            return out
        if bool(args.get("write_result", False)):
            try:
                root = _resolve_filesystem_root(
                    "QDRANT_FILESYSTEM_WRITE_ROOT", "./sandbox",
                )
                d = root / "jev"
                d.mkdir(parents=True, exist_ok=True)
                name = (f"classify-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                        f"-{os.urandom(2).hex()}.json")
                path = d / name
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(
                    out, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, path)
                out["result_path"] = path.resolve().as_posix()
            except Exception as e:
                out["result_path_error"] = repr(e)
        return out

    def _schema(self, args: dict) -> dict:
        return self.engine.schema()

    def _status(self, args: dict) -> dict:
        return self.engine.live_status()


class ImageGenLocalHost:
    """OpenRouter Image API 產圖工具（v0.9.104.0+, server
    ``imagegen-local``).

    Mock-first: ``IMAGEGEN_MODE=mock``（預設）= Pillow 確定性
    placeholder 圖（NO network — 管線演練）；live 走真實 OpenRouter
    Image API（``POST /api/v1/images/generations``，重用
    OPENROUTER_API_KEY，每張圖收真實費用 usage.cost）。寫入閘
    （hicloud/keep 收斂模式）：圖檔一律 confirm=true 才寫
    sandbox；live 另需 IMAGEGEN_WRITES_ENABLED=on。產出的圖寫進
    write sandbox 的 ``images/`` 子目錄（IMAGEGEN_DIR 可改），
    path 原樣寫回訊息即可獲得 chat 的內嵌預覽。
    """

    SERVER_NAME = "imagegen-local"

    def __init__(self) -> None:
        from src.imagegen_tools import ImageGenEngine

        self.engine = ImageGenEngine()

    def list_tools(self) -> list[LocalTool]:
        return [
            LocalTool(
                name="image_generate",
                description=(
                    "文生圖（OpenRouter Image API）：prompt 描述構圖"
                    "/風格/文字；回傳 sandbox 檔案路徑（chat 可內嵌"
                    "預覽）。需 confirm=true；mock 用 placeholder。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "圖片描述（必填；構圖/風格/"
                                           "文字/比例寫清楚）。",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true（已得使用者"
                                           "明確同意產圖）。",
                        },
                        "n": {
                            "type": "integer",
                            "description": "產生張數（預設 1；上限"
                                           " IMAGEGEN_MAX_IMAGES=4）。",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "description": "比例 N:N（例 16:9 / 9:16 / "
                                           "1:1 / 4:3）。",
                        },
                        "resolution": {
                            "type": "string",
                            "description": "解析度 tier: 512|1K|2K|4K。",
                        },
                        "quality": {
                            "type": "string",
                            "description": "auto|low|medium|high。",
                        },
                        "output_format": {
                            "type": "string",
                            "description": "png|jpeg|webp|svg。",
                        },
                        "model": {
                            "type": "string",
                            "description": "模型 id（預設 "
                                           "gemini-2.5-flash-image；"
                                           "可用 image_gen_models 查）。",
                        },
                        "filename": {
                            "type": "string",
                            "description": "輸出檔名（可省；自動帶時間戳）。",
                        },
                        "seed": {
                            "type": "integer",
                            "description": "可選 seed（重現性）。",
                        },
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._generate,
            ),
            LocalTool(
                name="image_gen_list",
                description=(
                    "列出 sandbox images/ 目錄既有圖檔（唯讀，不需 "
                    "confirm）。"
                ),
                server_name=self.SERVER_NAME,
                handler=self._list,
            ),
            LocalTool(
                name="image_gen_models",
                description=(
                    "可用的產圖模型清單（live 拉即時目錄；mock 回常備"
                    "清單）。查模型/定價時不需 confirm。"
                ),
                server_name=self.SERVER_NAME,
                handler=self._models,
            ),
            LocalTool(
                name="image_gen_status",
                description=(
                    "產圖擴充狀態（mode/model/單次上限/輸出目錄/閘狀態）。"
                ),
                server_name=self.SERVER_NAME,
                handler=self._status,
            ),
        ]

    # ── 寫入落盤（host 責任 — atomic tmp + os.replace） ──────────
    def _generate(self, args: dict) -> dict:
        args = args or {}
        out = self.engine.generate(
            str(args.get("prompt") or ""),
            confirm=args.get("confirm"),
            n=args.get("n", 1),
            model=str(args.get("model") or ""),
            aspect_ratio=str(args.get("aspect_ratio") or ""),
            resolution=str(args.get("resolution") or ""),
            size=str(args.get("size") or ""),
            quality=str(args.get("quality") or ""),
            output_format=str(args.get("output_format") or ""),
            seed=args.get("seed", 0))
        if out.get("error"):
            return out
        try:
            dir_env = (os.environ.get("IMAGEGEN_DIR", "") or "").strip()
            if dir_env:
                root = _resolve_filesystem_root(
                    "QDRANT_FILESYSTEM_WRITE_ROOT", "./sandbox")
                d = Path(root) / dir_env
            else:
                d = self.engine.images_dir()
            if d is None:
                d = Path("./sandbox") / "images"
            d = Path(d)
            d.mkdir(parents=True, exist_ok=True)
            stem = self._stem_from(
                str(args.get("filename") or ""),
                (out.get("images") or [{"ext": ".png"}])[0].get(
                    "ext", ".png"))
            files = []
            display_paths = []
            for i, img in enumerate(out.get("images") or []):
                name = stem if i == 0 else f"{stem}-{i + 1}"
                dest = d / f"{name}{img.get('ext', '.png')}"
                k = 2
                while dest.exists():
                    dest = d / f"{name}-{k}{img.get('ext')}"
                    k += 1
                tmp = dest.with_name(dest.name + ".tmp")
                tmp.write_bytes(img.get("bytes") or b"")
                os.replace(tmp, dest)  # atomic write
                display_paths.append(dest.resolve().as_posix())
                files.append({
                    "path": dest.resolve().as_posix(),
                    "name": dest.name,
                    "size": dest.stat().st_size,
                    "media_type":
                        str(img.get("media_type") or "image/png"),
                })
        except Exception as e:
            return {"error": f"圖檔寫入 sandbox 失敗: {e!r}",
                    "mode": out.get("mode")}
        usage = out.get("usage") or {}
        resp: dict[str, Any] = {
            "ok": True,
            "mode": out.get("mode"),
            "simulated": out.get("simulated", False),
            "model": out.get("model"),
            "n": len(files),
            "files": files,
            "display": "\n".join(
                f"📷 圖片已生成：{p}" for p in display_paths),
            "note": out.get("note"),
        }
        if out.get("mode") == "live":
            resp["usage_cost"] = usage.get("cost")
            resp["elapsed_s"] = out.get("elapsed_s")
        return resp

    @staticmethod
    def _stem_from(user_name: str, media_ext: str) -> str:
        from src.imagegen_tools import ImageGenEngine

        return os.path.splitext(
            ImageGenEngine.sanitized_filename(user_name, media_ext))[0]

    # ── 唯讀工具 ─────────────────────────────────────────────────
    def _list(self, args: dict) -> dict:
        return self.engine.list_images()

    def _models(self, args: dict) -> dict:
        return self.engine.models_discovery()

    def _status(self, args: dict) -> dict:
        return self.engine.status()
class TactiLocalHost:
    """Tacti (tactical plan) / Workflow compiler + plan-executor
    MCP tools (v0.9.109.0 — server ``tacti-local`` since v0.9.120.3;
    previously ``skill-local`` — renamed so the EXECUTABLE-plan system
    never collides with the SKILL.md FILE library, skill-library-local).

    「多個回合分工運作、非單一 chat 回合可完成的工作」 → **先 compile
    （驗證 + 依賴分波 + 併發上限分批 + 驗證節點注入 + 預算標記）再執行
    （波次驱动 + 每 stage 獨立回合 + 硬超時 + 機械驗證）**：

      compile_plan          驗證 + 優化 → plan_id（持久 plans/<id>.json）
      run_plan              啟動/RESUME：dead-process running 歸位、
                            done 跳過、依序驅動 waves
      get_plan_status       每 stage 狀態/驗證結果/錯誤
      list_plans            已編譯計畫清單（計畫庫）
      run_plan_stage        重跑單一 failed/skipped stage
      stop_plan             停止（running→failed、pending→skipped）

    Stages run through a FRESH Agent sharing THIS process's McpManager
    (full tool surface; their task-registry rows use source="plan" so
    the v0.9.108.0 notify hook notifies per-stage as well). All plan
    logic lives in ``src/agent/skill_compiler.py`` (internal name kept
    for history) — this host is a thin surface.
    """

    SERVER_NAME = "tacti-local"

    def __init__(self, mcp_manager=None):
        self._mcp_manager = mcp_manager
        from src.agent import skill_compiler as core

        core.set_agent_factory(self._fresh_agent)

    def _fresh_agent(self, llm: str | None = None):
        from src.agent.agent import Agent

        # v0.9.110.0 — per-stage llm pin; no pin = exact legacy call.
        if llm:
            return Agent(mcp_manager=self._mcp_manager, task_llm=str(llm))
        return Agent(mcp_manager=self._mcp_manager)

    def list_tools(self) -> list:
        from src.mcp_client.host import LocalTool  # local import (module)

        return [
            LocalTool(
                name="compile_plan",
                description=(
                    "把「多步驟、需多個回合分工、非單一回合可完成」的"
                    "工作（workflow/戰術/策略/tacti）編譯成可執行的 "
                    "wave-plan：自動驗證（空 prompt/未知依賴/環偵測）、"
                    "依賴分波、併發上限分批、逐 stage 注入驗證節點"
                    "(verify_artifact)/預算/檢查點/完成通知指令。"
                    "stages=[{title,prompt,id?,budget?,depends_on?,"
                    "llm?,tools_allow?,verify?:{path,checks?}}]"
                    "（≤20 stages）。plan 級可另設 default_llm 與"
                    " tools_allow（工具白名單，機械作用域）。"
                    "回傳 plan_id + waves 摘要；之後用 run_plan 執行。"
                    "單一回合能完成的工作直接做，不要編譯。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "Plan/tacti 名稱"},
                        "description": {"type": "string"},
                        "on_failure": {"type": "string",
                                       "enum": ["stop", "continue"],
                                       "description":
                                       "某 stage 失敗時：stop（預設）"
                                       "=整個 plan 失敗並跳出後續波；"
                                       "continue=後續獨立 stage 照跑"},
                        "notify_chat_id": {"type": "integer",
                                           "description":
                                           "完成/失敗時 send_telegram "
                                           "通知的 chat_id（可選）"},
                        "default_budget": {"type": "integer",
                                           "description":
                                           "每 stage 預設回合預算"
                                           "（預設 = MAX_TOOL_"
                                           "ITERATIONS resolution）"},
                        "wave_cap": {"type": "integer",
                                     "description":
                                     "每波最多並行 stage 數（預設 4；"
                                     "0 = 不限）"},
                        "default_llm": {"type": "string",
                                        "description":
                                        "可選。plan 級預設模型 pin "
                                        "(provider:model，限 openrouter/"
                                        "ollama)；每 stage 的 llm 覆寫它。"
                                        "未指定 = 預設 LLM 鏈"},
                        "tools_allow": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "可選。plan 級工具白名單（機械作用域，"
                                "v0.9.111）：此 plan 的每個 stage 回合"
                                "只暴露清單內的工具（作用域由機制強制"
                                "，非 prompt 規勸）；stage 級 "
                                "tools_allow 可覆寫。省略 = 完整工具面"
                                "（零變化）。例如 "
                                "['read_file','web_search']。"
                            ),
                        },
                        "stages": {
                            "type": "array",
                            "description": (
                                "Stage 清單（1-20 個，依執行/依賴順序）"
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "prompt": {"type": "string",
                                               "description":
                                               "這個 stage 的完整執行"
                                               " prompt（獨立回合，要"
                                               "自足）"},
                                    "id": {"type": "string"},
                                    "budget": {"type": "integer"},
                                    "llm": {"type": "string",
                                            "description":
                                            "可選。此 stage 專用模型 pin "
                                            "(provider:model)；覆寫 "
                                            "default_llm"},
                                    "tools_allow": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "可選。本 stage 的工具白名單"
                                            "（機械作用域，v0.9.111；覆寫"
                                            " plan 級 tools_allow）。"
                                        ),
                                    },
                                    "depends_on": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "stage id 或"
                                                       "標題",
                                    },
                                    "verify": {
                                        "type": "object",
                                        "description": "機械驗證節點",
                                        "properties": {
                                            "path": {"type": "string"},
                                            "checks": {"type": "array"},
                                        },
                                        "required": ["path"],
                                    },
                                },
                                "required": ["title", "prompt"],
                            },
                        },
                    },
                    "required": ["name", "stages"],
                },
                server_name=self.SERVER_NAME,
                handler=self._compile,
            ),
            LocalTool(
                name="run_plan",
                description=(
                    "執行（或 RESUME）一個已編譯的 plan：波次驅動、"
                    "每 stage 一個背景回合（硬超時保護、task-registry "
                    "可見 source=plan）。done 的 stage 自動跳過 — "
                    "失敗/重啟後再 run_plan 就是續跑。進度用 "
                    "get_plan_status。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                    },
                    "required": ["plan_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._run,
            ),
            LocalTool(
                name="get_plan_status",
                description="查詢 plan 每 stage 狀態/驗證結果/錯誤"
                            "與波次進度（READ-ONLY）。",
                input_schema={
                    "type": "object",
                    "properties": {"plan_id": {"type": "string"}},
                    "required": ["plan_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._status,
            ),
            LocalTool(
                name="list_plans",
                description=(
                    "列出已編譯的 plans（計畫庫 — 名稱/狀態/波次/進度；"
                    "READ-ONLY）。注意：這是 TACTI 計畫庫，不是 SKILL.md "
                    "技能檔案庫（那是 skill_list，skill-library-local）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer",
                                  "description": "1-200, default 20"},
                    },
                },
                server_name=self.SERVER_NAME,
                handler=self._list,
            ),
            LocalTool(
                name="run_plan_stage",
                description=(
                    "只重跑 ONE failed/skipped stage（不動其他；跑完後"
                    "可用 run_plan 續跑依賴它的後續波 — done 自跳過）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                        "stage_id": {"type": "string"},
                    },
                    "required": ["plan_id", "stage_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._retry_stage,
            ),
            LocalTool(
                name="stop_plan",
                description=(
                    "請求停止執行中的 plan（running stage → failed、"
                    "pending → skipped；已完成的保留）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {"plan_id": {"type": "string"}},
                    "required": ["plan_id"],
                },
                server_name=self.SERVER_NAME,
                handler=self._stop,
            ),
            LocalTool(
                name="delete_plan",
                description=(
                    "刪除一個已編譯的 plan（IRREVERSIBLE）：移除 "
                    "plans/<id>.json 與記憶體快取；task-registry 列與 "
                    "stage session 保留（歷史完整）。fail-closed：需 "
                    "confirm=true；RUNNING 中的 plan 要先 stop_plan 或 "
                    "帶 force=true（=停止+刪除一次完成）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true（不可復原）。",
                        },
                        "force": {
                            "type": "boolean",
                            "description": (
                                "RUNNING 中仍強制刪（先撒 stop 旗再刪，"
                                "stage 回合於下一個邊界結束）。預設 false。"
                            ),
                        },
                    },
                    "required": ["plan_id", "confirm"],
                },
                server_name=self.SERVER_NAME,
                handler=self._delete,
            ),
        ]

    # ── handlers ──────────────────────────────────────────────
    def _compile(self, args: dict) -> dict:
        from src.agent.skill_compiler import (
            PlanError, compile_plan,
        )

        a = args if isinstance(args, dict) else {}
        try:
            rec = compile_plan(
                name=a.get("name"),
                stages=list(a.get("stages") or []),
                description=str(a.get("description") or ""),
                on_failure=str(a.get("on_failure") or "stop"),
                notify_chat_id=a.get("notify_chat_id"),
                default_budget=a.get("default_budget"),
                wave_cap=a.get("wave_cap"),
                stage_timeout_s=a.get("stage_timeout_s"),
                default_llm=str(a.get("default_llm") or "").strip()
                or None,
                tools_allow=a.get("tools_allow"),
            )
        except PlanError as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": f"compile crashed: {e!r}"}
        return {
            "ok": True, "plan_id": rec["id"], "name": rec["name"],
            "status": rec["status"],
            "waves": len(rec["waves"]), "stages": len(rec["stages"]),
            "wave_detail": [
                {"wave": i + 1, "stage_ids": w,
                 "titles": [next(s["title"] for s in rec["stages"]
                                 if s["id"] == sid) for sid in w]}
                for i, w in enumerate(rec["waves"])
            ],
            "budget_total": sum(s["budget"] for s in rec["stages"]),
            "notify_chat_id": rec.get("notify_chat_id"),
            "plans_dir": rec.get("plans_dir") or "",
        }

    def _run(self, args: dict) -> dict:
        from src.agent.skill_compiler import run_plan as _runp

        return _runp(str((args or {}).get("plan_id") or "").strip())

    def _status(self, args: dict) -> dict:
        from src.agent.skill_compiler import get_plan_status

        out = get_plan_status(
            str((args or {}).get("plan_id") or "").strip())
        return out or {"error": "plan not found — list_plans?"}

    def _list(self, args: dict) -> dict:
        from src.agent.skill_compiler import list_plans

        return list_plans((args or {}).get("limit") or 20)

    def _retry_stage(self, args: dict) -> dict:
        from src.agent.skill_compiler import run_plan_stage

        a = args or {}
        return run_plan_stage(str(a.get("plan_id") or "").strip(),
                              str(a.get("stage_id") or "").strip())

    def _stop(self, args: dict) -> dict:
        from src.agent.skill_compiler import stop_plan

        return stop_plan(str((args or {}).get("plan_id") or "").strip())

    def _delete(self, args: dict) -> dict:
        """v0.9.121.2 — delete one compiled plan (confirm-gated)."""
        from src.agent.skill_compiler import delete_plan

        a = args if isinstance(args, dict) else {}
        return delete_plan(
            str(a.get("plan_id") or "").strip(),
            confirm=bool(a.get("confirm")),
            force=bool(a.get("force")),
        )


class SkillLibraryLocalHost:
    """v0.9.116.0+ — skill FILE library manager, six tools under server
    ``skill-library-local``: import (local folder / single file / URL
    into ``QDRANT_SKILLS_DIR``), list, get, keyword search, remove
    (confirm-gated irreversible), and ``skill_use`` — the one-time
    引用 path that returns the SKILL.md body INLINE so the current
    turn applies the rules WITHOUT another read_file round-trip.

    Companion of ``TactiLocalHost`` (tacti-local): that server stores
    compiled EXECUTABLE plans; this one stores the reusable skill
    document bundles. All logic lives in ``src/skill_library.py`` —
    this host is a thin surface and never raises (error dicts).
    """

    SERVER_NAME = "skill-library-local"

    def list_tools(self) -> list:
        from src.mcp_client.host import LocalTool

        return [
            LocalTool(
                name="skill_import",
                description=(
                    "把 skill 檔案包匯入本機技能庫（QDRANT_SKILLS_DIR）。"
                    "source 可為：(a) 含 SKILL.md 的本機資料夾 — "
                    "references/examples 等附屬檔一起複製；(b) 單一 "
                    "SKILL.md 型檔案；(c) URL — github blob 自動轉 "
                    "raw（只抓 SKILL.md 本檔）。名稱預設取 frontmatter "
                    "name。fail-closed 寫入閘：需 confirm=true；同名"
                    "已存在需 overwrite=true。回傳 name/path/files/"
                    "notes。"),
                handler=self._import,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string",
                                   "description":
                                   "本機路徑（資料夾或 SKILL.md 檔）或 URL"},
                        "name": {"type": "string",
                                 "description":
                                 "覆寫技能名（省略 = 取 frontmatter name）"},
                        "overwrite": {"type": "boolean",
                                      "description":
                                      "同名技能已存在時取代"},
                        "confirm": {"type": "boolean",
                                    "description":
                                    "必須 true 才會寫入（寫入閘）"},
                    },
                    "required": ["source"],
                },
            ),
            LocalTool(
                name="skill_list",
                description=(
                    "列出技能庫全部技能（名稱/描述/檔案清單/大小/"
                    "來源）。無參數。"),
                handler=self._list,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer",
                                  "description": "最多列幾筆（預設 50）"},
                    },
                },
            ),
            LocalTool(
                name="skill_get",
                description=(
                    "查詢單一技能：frontmatter + SKILL.md 原文（含 "
                    "max_chars 截斷保護）+ 附屬檔清單 + 來源。"),
                handler=self._get,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "技能名稱"},
                        "max_chars": {"type": "integer",
                                      "description":
                                      "SKILL.md 文字上限（預設 20000）"},
                    },
                    "required": ["name"],
                },
            ),
            LocalTool(
                name="skill_search",
                description=(
                    "關鍵字搜尋技能庫：掃 name + description + SKILL.md "
                    "內文（不分大小寫），回傳評分 + 命中範圍 + 摘句。"),
                handler=self._search,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string",
                                  "description": "關鍵字"},
                        "limit": {"type": "integer",
                                  "description": "最多回傳幾筆（預設 10）"},
                    },
                    "required": ["query"],
                },
            ),
            LocalTool(
                name="skill_remove",
                description=(
                    "刪除技能庫中的一個技能資料夾（連同附屬檔，不可逆）。"
                    "fail-closed：需 confirm=true。"),
                handler=self._remove,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "技能名稱"},
                        "confirm": {"type": "boolean",
                                    "description":
                                    "必須 true 才會刪除（不可逆）"},
                    },
                    "required": ["name"],
                },
            ),
            LocalTool(
                name="skill_use",
                description=(
                    "引用技能（一次性載入）：把 SKILL.md 規範內文直接"
                    "注入本回合工具結果 — 之後的回覆與產出文件即遵守"
                    "該技能，不需再 read_file。附 usage_note（照做）與"
                    "附屬檔清單（需要時 read_file 同層檔案）。語意同 "
                    "「/ste」這類 Claude skill 觸發。"),
                handler=self._use,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "技能名稱"},
                        "max_chars": {"type": "integer",
                                      "description":
                                      "內文上限（預設 20000）"},
                    },
                    "required": ["name"],
                },
            ),
        ]

    # ── handlers ──────────────────────────────────────────────
    def _import(self, args: dict) -> dict:
        a = args if isinstance(args, dict) else {}
        try:
            from src.skill_library import import_skill

            return import_skill(
                str(a.get("source") or ""),
                name=(str(a.get("name")) if a.get("name") else None),
                overwrite=bool(a.get("overwrite")),
                confirm=bool(a.get("confirm")),
            )
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def _list(self, args: dict) -> dict:
        a = args if isinstance(args, dict) else {}
        try:
            from src.skill_library import list_skills

            return list_skills(int(a.get("limit") or 50))
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def _get(self, args: dict) -> dict:
        a = args if isinstance(args, dict) else {}
        try:
            from src.skill_library import get_skill

            mx = a.get("max_chars")
            return get_skill(
                str(a.get("name") or ""),
                int(mx) if str(mx or "").strip() else None)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def _search(self, args: dict) -> dict:
        a = args if isinstance(args, dict) else {}
        try:
            from src.skill_library import search_skills

            return search_skills(
                str(a.get("query") or ""),
                int(a.get("limit") or 10))
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def _remove(self, args: dict) -> dict:
        a = args if isinstance(args, dict) else {}
        try:
            from src.skill_library import SkillError, remove_skill
        except Exception as e:
            return {"error": f"import failed: {type(e).__name__}: {e}"}
        try:
            return remove_skill(
                str(a.get("name") or ""),
                confirm=bool(a.get("confirm")))
        except SkillError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def _use(self, args: dict) -> dict:
        a = args if isinstance(args, dict) else {}
        try:
            from src.skill_library import SkillError, use_skill
        except Exception as e:
            return {"error": f"import failed: {type(e).__name__}: {e}"}
        try:
            mx = a.get("max_chars")
            return use_skill(
                str(a.get("name") or ""),
                int(mx) if str(mx or "").strip() else None)
        except SkillError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


class SnippetsLocalHost:
    """v0.9.122.0 — ⚡ quick-phrases (snippets) MCP management, server
    ``snippets-local``, FIVE tools over ``src.snippet_store`` (the
    SAME global file the chat panel uses: $QDRANT_RAG_HOME/
    snippets.json — a change from ANY device surfaces everywhere):

      snippet_list()               index + preview + multi flag + full
                                   text (an editor needs it)
      snippet_add(text, at?)       MULTI-LINE verbatim (\\n kept)
      snippet_edit(index, text)    replace one (multi-line verbatim)
      snippet_delete(index)        remove one
      snippet_move(index, to)      reorder

    Caps: 100 entries × 2000 chars per entry (every write path).
    Low-stakes ops — no confirm gate (the GUI ✕ has none either).
    """

    SERVER_NAME = "snippets-local"

    def list_tools(self) -> list:
        from src.mcp_client.host import LocalTool

        return [
            LocalTool(
                name="snippet_list",
                description=(
                    "列出全部 ⚡ 快速片語（snippets）：index / 首行 "
                    "preview / multi 旗標 / 完整 text（多行片語含 "
                    "\\n）。零參數；limit 可選。"),
                handler=self._op_list,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer",
                                  "description": "前 N 筆（可選）"},
                    },
                },
            ),
            LocalTool(
                name="snippet_add",
                description=(
                    "新增一個片語（快取的常用回覆/命令模板）— "
                    "MULTI-LINE：text 內的換行原樣保留；at = 插入"
                    "位置（0-based，省略 = 加到最後）。使用者說"
                    "「把這段加入片語」時用。"),
                handler=self._op_add,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": (
                                "片段內容（可多行 — 逐字保留換行）"),
                        },
                        "at": {"type": "integer",
                               "description": "插入位置 index（可選）"},
                    },
                    "required": ["text"],
                },
            ),
            LocalTool(
                name="snippet_edit",
                description=(
                    "取代一個既有片語（snippet_edit(index, text)）— "
                    "text 為完整的新內容（可多行）。先 snippet_list "
                    "取得 index 與現文。"),
                handler=self._op_edit,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "text": {"type": "string",
                                 "description": "新內容（可多行）"},
                    },
                    "required": ["index", "text"],
                },
            ),
            LocalTool(
                name="snippet_delete",
                description=(
                    "刪除一個片語（snippet_delete(index)）— "
                    "低風險操作（片語可重建），不需 confirm。"),
                handler=self._op_delete,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {"index": {"type": "integer"}},
                    "required": ["index"],
                },
            ),
            LocalTool(
                name="snippet_move",
                description=(
                    "調整順序：把 index 的片語移到位置 to "
                    "（snippet_move(2, 0) = 移到第一個）。"),
                handler=self._op_move,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "to": {"type": "integer"},
                    },
                    "required": ["index", "to"],
                },
            ),
        ]

    # ── handlers ──────────────────────────────────────────────
    def _op_list(self, args: dict) -> dict:
        from src.snippet_store import list_snippets

        a = args if isinstance(args, dict) else {}
        return list_snippets(a.get("limit") or 0)

    def _op_add(self, args: dict) -> dict:
        from src.snippet_store import add_snippet

        a = args if isinstance(args, dict) else {}
        return add_snippet(a.get("text"), a.get("at"))

    def _op_edit(self, args: dict) -> dict:
        from src.snippet_store import set_snippet

        a = args if isinstance(args, dict) else {}
        return set_snippet(a.get("index"), a.get("text"))

    def _op_delete(self, args: dict) -> dict:
        from src.snippet_store import delete_snippet

        a = args if isinstance(args, dict) else {}
        return delete_snippet(a.get("index"))

    def _op_move(self, args: dict) -> dict:
        from src.snippet_store import move_snippet

        a = args if isinstance(args, dict) else {}
        return move_snippet(a.get("index"), a.get("to"))


class RulebookLocalHost:
    """v0.9.124.0+ — the agent-maintained RULE BOOK (server
    ``rulebook-local``), SEVEN tools over ``src.agent.rulebook`` (+ the
    v0.9.127.0 ToolRoute-override pair, ``src.agent.toolroute_overrides``
    with the same evidence-gate/snapshot-ring discipline) — the
    Memento-3 rulebook工件 borrowed per the self-evolving-agent 譜系
    report: a natural-language rule file the LLM revises WHEN THE
    ENVIRONMENT BREAKS ITS EXPECTATIONS (a failed tool call = a
    prediction error), not an event log.

      rules_list()                    id / title / text / evidence
      rules_add(title, text, ev?)     new rule (confirm-gated; evidence
                                      encouraged on ADD, REQUIRED on
                                      revise)
      rules_revise(id, text, ev)      REVISE — evidence REQUIRED
                                      (fail-closed Evo² gate)
      rules_rollback(steps)           zero-dep snapshot-ring undo
      rules_history()                 ring listing + latest diff

    All logic lives in ``src/agent/rulebook.py`` (mtime-cached read,
    atomic writes, snapshot ring, never raises); this host is a thin
    surface. The file rides the system prompt as the AGENT RULEBOOK
    block on both prompt flavours.
    """

    SERVER_NAME = "rulebook-local"

    def list_tools(self) -> list:
        from src.mcp_client.host import LocalTool

        return [
            LocalTool(
                name="rules_list",
                description=(
                    "規則簿清單：id / 標題 / 規則內文 / 證據 / updated / errors（該規則生效期間的工具失敗計數 — v0.9.127.0 療效度控）。"
                    "零參數唯讀。本檔即每回合 system prompt 的 "
                    "AGENT RULEBOOK 區塊。"),
                handler=self._op_list,
                server_name=self.SERVER_NAME,
                input_schema={"type": "object", "properties": {}},
            ),
            LocalTool(
                name="rules_add",
                description=(
                    "新增一條規則到規則簿（agent_rules.md，下一回合自動"
                    "上 prompt）。工具失敗=預測錯誤（Memento "
                    "observe→reflect→revise），系統性失敗就該寫入。"
                    "evidence 建議附（失敗回執/事件；add 可選，"
                    "revise 必填）。confirm=true 閘。"),
                handler=self._op_add,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "規則標題（一行）",
                        },
                        "text": {
                            "type": "string",
                            "description": "規則內文（可多行；未理解的機制標 underspecified）",
                        },
                        "evidence": {
                            "type": "string",
                            "description": "證據（失敗回執 / 事件描述；建議填）",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true（寫入閘）",
                        },
                    },
                    "required": ["title", "text"],
                },
            ),
            LocalTool(
                name="rules_revise",
                description=(
                    "修訂一條既有規則（rules_revise(rule_id, text, "
                    "evidence)）— evidence 必填 fail-closed：引用失敗"
                    "回執/事件證明舊規則錯了；修訂原因未附證據一律"
                    "拒絕。舊版自動進快照 ring，rules_rollback 可回。"),
                handler=self._op_revise,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "rule_id": {
                            "type": "string",
                            "description": "規則 id（如 r3）",
                        },
                        "text": {
                            "type": "string",
                            "description": "修訂後的完整規則內文",
                        },
                        "evidence": {
                            "type": "string",
                            "description": "必填證據（失敗回執/事件）",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true（寫入閘）",
                        },
                    },
                    "required": ["rule_id", "text", "evidence"],
                },
            ),
            LocalTool(
                name="rules_rollback",
                description=(
                    "回滾規則簿到第 N 個快照之前（steps 預設 1 = 最新"
                    "一次寫入前）。回滾前現況會先存快照，永不失資料。"
                    "confirm=true 閘。"),
                handler=self._op_rollback,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "integer",
                            "description": "回滾幾個快照（預設 1）",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true（寫入閘）",
                        },
                    },
                },
            ),
            LocalTool(
                name="rules_history",
                description=(
                    "rulebook 快照 ring + 最新 diff（唯讀）。診斷「哪次"
                    "修訂造成退化」用。"),
                handler=self._op_history,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "最多列幾筆（預設 10）",
                        },
                    },
                },
            ),
            LocalTool(
                name="toolroute_override_add",
                description=(
                    "提議一條 ToolRoute 觸發列增補（agent_toolroute.md，"
                    "v0.9.127.0 rulebook 模式）：server + regex patterns"
                    "（evidence 門）。寫入 = confirm=true；套用與否由 "
                    "QDRANT_TOOLROUTE_OVERRIDES 決定（shadow=只記 log、"
                    "on=union 掛工具面、off=不用）。"),
                handler=self._op_tro_add,
                server_name=self.SERVER_NAME,
                input_schema={
                    "type": "object",
                    "properties": {
                        "server": {
                            "type": "string",
                            "description": "目標 server 名（如 "
                                           "firefox_craw）",
                        },
                        "patterns": {
                            "type": "string",
                            "description": "regex 交替式（如 加密貨幣|"
                                           "crypto）",
                        },
                        "title": {"type": "string"},
                        "evidence": {
                            "type": "string",
                            "description": "為什麼內建觸發列漏了這類題"
                                           "（建議附）",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "必須 true（寫入閘）",
                        },
                    },
                    "required": ["server", "patterns"],
                },
            ),
            LocalTool(
                name="toolroute_override_list",
                description=(
                    "列出已提議的 ToolRoute 增補與目前模式（shadow/on/"
                    "off；唯讀）+ 軌跡說明。"),
                handler=self._op_tro_list,
                server_name=self.SERVER_NAME,
                input_schema={"type": "object", "properties": {}},
            ),
        ]

    # ── handlers ──────────────────────────────────────────────
    def _op_list(self, args: dict) -> dict:
        from src.agent import rulebook as rb

        a = args if isinstance(args, dict) else {}
        text = rb.load_rulebook()
        if not text:
            if not rb.rulebook_enabled():
                return {"error": (
                    "rulebook disabled (QDRANT_RULEBOOK_ENABLED=off)")}
            return {
                "ok": True,
                "rules": [],
                "count": 0,
                "path": (rb.rulebook_path().as_posix()
                         if rb.rulebook_path() else ""),
                "note": ("empty rulebook — rules_add starts it; the "
                         "file rides the system prompt once non-empty"),
                "_echo": bool(a),
            }
        rules = rb.parse_rules(text)
        out = {
            "ok": True,
            "count": len(rules),
            "path": (rb.rulebook_path().as_posix()
                     if rb.rulebook_path() else ""),
            "rules": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "text": r["body"],
                    "evidence": r["evidence"] or "(none)",
                    "updated": r["updated"] or "(unknown)",
                    "errors": int(r.get("errors") or 0),
                    "last_error": r.get("last_error") or "",
                }
                for r in rules
            ],
        }
        if len(text) >= rb._max_chars():
            out["truncated_note"] = (
                "rulebook at the prompt cap — old rules may be cut off; "
                "prune (rules_rollback / hand edit) to keep it useful.")
        return out

    def _op_add(self, args: dict) -> dict:
        from src.agent import rulebook as rb

        a = args if isinstance(args, dict) else {}
        if not a.get("confirm"):
            return {"error": (
                "confirm=true required to WRITE the rulebook "
                "(fail-closed write gate; false = nothing stored).")}
        return rb.rules_add(
            a.get("title"), a.get("text"), a.get("evidence"),
        )

    def _op_revise(self, args: dict) -> dict:
        from src.agent import rulebook as rb

        a = args if isinstance(args, dict) else {}
        if not a.get("confirm"):
            return {"error": (
                "confirm=true required to WRITE the rulebook "
                "(fail-closed write gate; false = nothing stored).")}
        return rb.rules_revise(
            a.get("rule_id"), a.get("text"), a.get("evidence"),
        )

    def _op_rollback(self, args: dict) -> dict:
        from src.agent import rulebook as rb

        a = args if isinstance(args, dict) else {}
        if not a.get("confirm"):
            return {"error": (
                "confirm=true required to ROLL BACK the rulebook "
                "(fail-closed write gate; false = nothing changed).")}
        return rb.rules_rollback(a.get("steps") or 1)

    def _op_history(self, args: dict) -> dict:
        from src.agent import rulebook as rb

        a = args if isinstance(args, dict) else {}
        return rb.rules_history(a.get("limit") or 10)

    # ── v0.9.127.0 — ToolRoute pattern overrides (rulebook pattern) ──
    def _op_tro_add(self, args: dict) -> dict:
        from src.agent import toolroute_overrides as tro

        a = args if isinstance(args, dict) else {}
        if not a.get("confirm"):
            return {"error": (
                "confirm=true required to WRITE a ToolRoute override "
                "(fail-closed write gate; false = nothing stored).")}
        return tro.add_override(
            a.get("server"), a.get("patterns"),
            a.get("title") or "", a.get("evidence") or "",
        )

    def _op_tro_list(self, args: dict) -> dict:
        from src.agent import toolroute_overrides as tro

        a = args if isinstance(args, dict) else {}
        entries = tro.load_overrides()
        return {
            "ok": True,
            "mode": tro.mode(),
            "path": (tro.overrides_path().as_posix()
                     if tro.overrides_path() else ""),
            "count": len(entries),
            "entries": [
                {"id": e["id"], "title": e["title"], "server": e["server"],
                 "pattern": e["pattern"], "evidence": e["evidence"]}
                for e in entries
            ],
            "note": (
                "mode=shadow (default) 的命中只進 log（"
                "[toolroute-override] shadow hit），不掛工具面；"
                "確認軌跡後 QDRANT_TOOLROUTE_OVERRIDES=on 才生效。"
                if tro.mode() != "off" else
                "mode=off — 目前完全不讀取與套用。"),
        }
