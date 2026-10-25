"""Local core-tool executors — bash / awk / grep / python (v0.9.9+).

Pure execution logic: no MCP, no sandbox enforcement (that is the
caller's job — ``ExecLocalHost`` in ``src/mcp_client/host.py`` pins
the cwd to the write sandbox, applies the blocklist, timeouts and
output caps before anything here spawns a process).

Purpose
-------
Give the LLM *local compute* for jobs that would otherwise burn its
context window: format conversion (json/csv field surgery), bulk
regex extraction, log summarisation — run in the sandbox, return
only the (capped) result.

Sub-commands:

* ``run_shell``  — platform-native shell (Windows ``cmd /c``,
  POSIX ``/bin/sh -c``).
* ``run_bash``   — GNU bash. On Windows prefers the Git-for-Windows
  bash (its PATH bundles awk/grep/sed); the WSL stub and POSIX
  /bin/bash are fallbacks.
* ``grep_files`` — a pure-Python grep subset (regex/fixed, ignore
  case, invert, line numbers, count, filenames-only, context
  windows) — guaranteed available without a system grep.
* ``run_awk``    — the system awk (Git Bash / WSL / Linux / macOS);
  unavailable → clear error suggesting run_bash / run_python.
* ``exec_python``— ``sys.executable -c <code>``; the most flexible
  converter, with the sandbox as cwd.

Security note: the *disaster* blocklist here only matches
machine-level destruction (whole-disk wipes, boot damage, fork
bombs, shutdown) — file operations inside the sandbox are the whole
point of the tool and stay allowed. The host re-checks inputs and
enforces timeouts / output caps itself.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_MAX_OUTPUT",
    "DEFAULT_TIMEOUT",
    "MAX_TIMEOUT",
    "find_awk",
    "find_bash",
    "find_grep_binary",
    "is_dangerous",
    "truncate_output",
    "build_scripts_dir",
    "detect_script_lang",
    "save_script",
    "list_scripts",
    "run_script",
    "auto_save_script",
]

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120
DEFAULT_MAX_OUTPUT = 64 * 1024

# ─── disaster blocklist ──────────────────────────────────────────
#
# Narrow by design: only machine-level destruction. Sandbox file
# operations (even deletes) are the point of the tool.
_DISASTER_RES = [
    re.compile(r"rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+/(?:\s|$)"),
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"dd\s+[^\n;|&]*of=/dev/(?:sd[a-z]|nvme|hd[a-z]|mmcblk)"),
    re.compile(r":\(\)\s*\{\s*:\|\:&\s*\}\s*;:"),           # fork bomb
    re.compile(r">\s*/dev/sd[a-z]"),
    re.compile(r"\bformat\b[^&|;]*[a-zA-Z]:", re.IGNORECASE),
    re.compile(r"\bdel\s+/[fsq]", re.IGNORECASE),
    re.compile(r"\brd\s+/s\b", re.IGNORECASE),
    re.compile(r"\brmdir\s+/s\b", re.IGNORECASE),
    re.compile(r"\b(shutdown|reboot|poweroff|halt)\b", re.IGNORECASE),
    re.compile(r"\bbcdedit\b", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\bvssadmin\s+delete\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+-R\s+777\s+/(?:\s|$)"),
]


def is_dangerous(command: str) -> str | None:
    """Return the matched blocklist snippet, or None when allowed.

    Only machine-level destruction is refused — file operations
    inside the sandbox are what this tool is FOR.
    """
    for rx in _DISASTER_RES:
        m = rx.search(command or "")
        if m:
            return m.group(0)
    return None


# ─── tool discovery ──────────────────────────────────────────────

_GIT_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)
_GIT_USR_BIN = (
    r"C:\Program Files\Git\usr\bin",
    r"C:\Program Files (x86)\Git\usr\bin",
)


def find_bash() -> str | None:
    """Locate a usable bash.

    Priority: PATH (skipping the WSL system32 stub — it fails when
    WSL is not enabled) → Git Bash install paths → WSL stub →
    POSIX /bin/bash.
    """
    on_path = shutil.which("bash")
    if on_path and "system32" not in on_path.lower():
        return on_path
    for cand in _GIT_BASH_CANDIDATES:
        if os.path.exists(cand):
            return cand
    if on_path:  # WSL stub as a last resort on Windows
        return on_path
    return "/bin/bash" if os.name != "nt" else None


def find_awk() -> str | None:
    """System awk: PATH first, then Git-for-Windows usr/bin."""
    found = shutil.which("awk")
    if found:
        return found
    for d in _GIT_USR_BIN:
        cand = os.path.join(d, "awk.exe" if os.name == "nt" else "awk")
        if os.path.exists(cand):
            return cand
    return None


def find_grep_binary() -> str | None:
    """System GNU grep (informational; grep_files is pure Python)."""
    found = shutil.which("grep")
    if found:
        return found
    for d in _GIT_USR_BIN:
        cand = os.path.join(d, "grep.exe" if os.name == "nt" else "grep")
        if os.path.exists(cand):
            return cand
    return None


def truncate_output(text: str | None, max_output: int) -> tuple[str, bool]:
    """Cap a stream to ``max_output`` chars → (text, truncated)."""
    text = text or ""
    if len(text) <= max_output:
        return text, False
    return text[:max_output], True


# ─── subprocess executors ────────────────────────────────────────

def _run_capture(
    cmd: list[str],
    cwd: str,
    timeout: int,
    max_output: int,
    read_root: str | None = None,
) -> dict[str, Any]:
    """Run ``cmd`` captured; never raises for non-zero exits.

    Returns {stdout, stderr, exit_code, duration_ms, truncated,
    timed_out}. Timeouts are reported as a dict (with whatever
    partial output the OS captured), not raised.
    """
    env = dict(os.environ)
    env["QDRANT_WRITE_ROOT"] = cwd
    env["QDRANT_READ_ROOT"] = read_root or cwd
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
        )
        stdout, stderr = proc.stdout or "", proc.stderr or ""
        code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        stdout = (
            e.stdout.decode("utf-8", "replace")
            if isinstance(e.stdout, bytes) else (e.stdout or "")
        )
        stderr = (
            e.stderr.decode("utf-8", "replace")
            if isinstance(e.stderr, bytes) else (e.stderr or "")
        )
        stderr = (stderr or "") + f"\n[timeout after {timeout}s]"
        code = -1
        timed_out = True
    except FileNotFoundError as e:
        return {
            "stdout": "",
            "stderr": f"[tool not found] {e!r}",
            "exit_code": -1,
            "duration_ms": 0,
            "truncated": False,
            "timed_out": False,
        }

    stdout, trunc1 = truncate_output(stdout, max_output)
    stderr, trunc2 = truncate_output(stderr, max_output)
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": code,
        "timed_out": timed_out,
        "truncated": trunc1 or trunc2,
        "duration_ms": 0,  # filled by callers that measure
    }


def _with_duration(result: dict, started: float) -> dict:
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    return result


def run_shell(
    command: str,
    cwd: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
    read_root: str | None = None,
) -> dict[str, Any]:
    """Platform-native shell: Windows ``cmd /c``, POSIX ``sh -c``."""
    started = time.monotonic()
    if os.name == "nt":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        cmd = [comspec, "/d", "/s", "/c", command]
    else:
        cmd = ["/bin/sh", "-c", command]
    out = _run_capture(cmd, cwd, timeout, max_output, read_root)
    out["tool"] = "shell"
    return _with_duration(out, started)


def run_bash(
    script: str,
    cwd: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
    read_root: str | None = None,
) -> dict[str, Any]:
    """GNU bash -c with the Git-Bash toolchain (awk/grep/sed) on PATH."""
    bash = find_bash()
    if not bash:
        return {
            "stdout": "",
            "stderr": (
                "bash not found — install Git for Windows (which "
                "also bundles awk/grep/sed) or enable WSL."
            ),
            "exit_code": -1,
            "timed_out": False,
            "truncated": False,
            "tool": "bash",
            "duration_ms": 0,
        }
    started = time.monotonic()
    out = _run_capture(
        [bash, "-c", script], cwd, timeout, max_output, read_root,
    )
    out["tool"] = "bash"
    return _with_duration(out, started)


def run_awk(
    script: str,
    file: str,
    cwd: str,
    field_separator: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
) -> dict[str, Any]:
    """System awk over ONE file inside the sandbox."""
    awk = find_awk()
    if not awk:
        return {
            "stdout": "",
            "stderr": (
                "awk not found on this machine — use run_bash "
                "(Git Bash bundles awk), run_python, or install "
                "Git for Windows."
            ),
            "exit_code": -1,
            "duration_ms": 0,
            "truncated": False,
            "timed_out": False,
            "tool": "awk",
        }
    started = time.monotonic()
    cmd = [awk]
    if field_separator:
        cmd += ["-F", field_separator]
    # MSYS/Git-Bash awk chokes on backslash paths — always hand it
    # the forward-slash form (C:/...), which every awk variant and
    # POSIX awk accepts.
    cmd += [script, Path(file).as_posix()]
    out = _run_capture(cmd, cwd, timeout, max_output)
    out["tool"] = "awk"
    return _with_duration(out, started)


# ─── matplotlib CJK bootstrap (v0.9.34.0+) ───────────────────────
# matplotlib's default DejaVu Sans has NO CJK glyphs — every Chinese
# label in an LLM-generated chart renders as tofu (□□□). The LLM
# repeatedly forgets the rcParams incantation (v0.9.5.2 lesson), so
# exec_python PREPENDS a no-op-when-unavailable bootstrap: the first
# installed CJK font wins; user code can still override afterwards.

_MPL_TRIGGER_WORDS = ("matplotlib", "mplfinance", "pyplot", "plt.")

_MPL_CJK_BOOTSTRAP = """\
try:
    import matplotlib
    from matplotlib import font_manager as _fm
    _names = {f.name for f in _fm.fontManager.ttflist}
    _cjk = [
        n for n in (
            "Microsoft JhengHei", "Microsoft JhengHei UI",
            "DFKai-SB", "Microsoft YaHei", "SimHei",
            "PMingLiU", "Noto Sans CJK TC", "PingFang TC",
        ) if n in _names
    ]
    if _cjk:
        matplotlib.rcParams["font.sans-serif"] = (
            _cjk + list(matplotlib.rcParams["font.sans-serif"]))
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    pass
"""


def _prepend_mpl_cjk(code: str) -> str:
    """Prepend the matplotlib CJK-font bootstrap (v0.9.34.0+).

    Only when the code looks chart-ish (mentions matplotlib /
    mplfinance / pyplot / plt.) — non-plot exec calls keep their
    zero-overhead startup. The bootstrap is wrapped in try/except so
    a matplotlib-less interpreter is completely unaffected.
    """
    if not any(k in code for k in _MPL_TRIGGER_WORDS):
        return code
    return _MPL_CJK_BOOTSTRAP + "\n" + code


# ─── oversized-code temp-file path (v0.9.36.0+) ──────────────────
# Windows CreateProcessW caps the whole command line at ~32,767
# chars — a run_python call that inlines a 1000-row dataset (~30 KB
# code) dies with WinError 206 (檔名或副檔名太長). Oversized code is
# therefore written to a temp .py INSIDE the sandbox and run as a
# script file; the file is removed after the run.
_CMDLINE_SAFE_CHARS = 24_000


def _run_python_via_file(
    code: str,
    cwd: str,
    timeout: int,
    max_output: int,
    read_root: str | None,
) -> dict[str, Any]:
    """Run oversized code as a script file in the sandbox.

    The temp file is deleted after the run (best effort); the unique
    name keeps concurrent exec_python calls from colliding.
    """
    import uuid as _uuid

    fpath = os.path.join(
        cwd, f".qdrant_exec_{_uuid.uuid4().hex[:8]}.py",
    )
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        return _run_capture(
            [sys.executable, fpath], cwd, timeout, max_output, read_root,
        )
    finally:
        try:
            os.remove(fpath)
        except OSError:
            pass


def exec_python(
    code: str,
    cwd: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
    read_root: str | None = None,
) -> dict[str, Any]:
    """sys.executable -c <code> with the sandbox as cwd.

    v0.9.36.0+ — code longer than ~24k chars runs as a temp script
    file instead of ``-c`` (Windows caps the command line at ~32k
    chars; a 1000-bar K-line pasted inline would die with
    WinError 206). The CJK bootstrap (v0.9.34.0+) rides along in
    both paths.
    """
    started = time.monotonic()
    code = _prepend_mpl_cjk(code)
    if len(code) > _CMDLINE_SAFE_CHARS:
        out = _run_python_via_file(
            code, cwd, timeout, max_output, read_root,
        )
    else:
        out = _run_capture(
            [sys.executable, "-c", code],
            cwd, timeout, max_output, read_root,
        )
    out["tool"] = "python"
    return _with_duration(out, started)


# ─── pure-Python grep ────────────────────────────────────────────

_GREP_MAX_FILE_BYTES = 4 * 1024 * 1024   # skip binaries/huge files
_TEXT_EXTS = frozenset({
    ".txt", ".log", ".md", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".html", ".css",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".sql",
    ".conf", ".env", ".properties", ".hpp", ".h", ".c", ".cpp",
})


def grep_files(
    pattern: str,
    root: Path,
    paths: list[str] | None = None,
    *,
    fixed: bool = False,
    ignore_case: bool = False,
    invert: bool = False,
    count: bool = False,
    filenames_only: bool = False,
    line_numbers: bool = True,
    after_context: int = 0,
    before_context: int = 0,
    max_results: int = 200,
) -> dict[str, Any]:
    """grep semantics over the sandbox — pure Python, no deps.

    ``paths`` are sandbox-relative names (files or directories —
    directories are walked recursively, text-like files only, each
    capped at 4 MB). Returns {matches|counts|files, searched,
    truncated} in grep-style output: ``path:line: text`` (single
    file drops the path prefix, like real grep).
    """
    flags = re.IGNORECASE if ignore_case else 0
    needle = re.escape(pattern) if fixed else pattern
    try:
        rx = re.compile(needle, flags)
    except re.error as e:
        raise ValueError(f"invalid pattern: {e!r}") from e

    # Collect candidate files.
    searched: list[str] = []
    targets: list[Path] = []
    if paths:
        for raw in paths:
            p = (root / raw).resolve()
            if p.is_dir():
                targets.extend(
                    q for q in sorted(p.rglob("*")) if q.is_file()
                )
            elif p.is_file():
                targets.append(p)
            # nonexistent paths are silently ignored (grep exit 2
            # semantics are noise for the LLM; searched list reports
            # what was actually scanned)
    else:
        targets = [
            q for q in sorted(root.rglob("*")) if q.is_file()
        ]

    lines_out: list[str] = []
    counts: list[dict[str, Any]] = []
    files_hit: list[str] = []
    truncated = False
    total_matches = 0

    for f in targets:
        rel = f.relative_to(root).as_posix() if _within(f, root) else f.name
        try:
            if f.stat().st_size > _GREP_MAX_FILE_BYTES:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue  # binary
        searched.append(rel)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")

        file_lines = text.splitlines()
        hits: list[int] = []
        for idx, line in enumerate(file_lines, 1):
            if bool(rx.search(line)) != invert:
                hits.append(idx)

        if not hits:
            continue
        files_hit.append(rel)
        if count:
            counts.append({"path": rel, "count": len(hits)})
            total_matches += len(hits)
            continue
        if filenames_only:
            total_matches += len(hits)
            continue

        shown: set[int] = set()
        for h in hits:
            lo = max(1, h - before_context)
            hi = min(len(file_lines), h + after_context)
            shown.update(range(lo, hi + 1))
        # Emit in line order; context lines are marked with '-'.
        for k in sorted(shown):
            total_matches += 1
            prefix = f"{rel}:{k}" if line_numbers else rel
            sep = ":" if k in hits else "-"
            lines_out.append(f"{prefix}{sep} {file_lines[k - 1]}")
            if len(lines_out) >= max_results:
                truncated = True
                break
        if truncated:
            break

    out: dict[str, Any] = {
        "pattern": pattern,
        "searched_files": len(searched),
        "truncated": truncated,
    }
    if count:
        out["counts"] = counts
        out["total_matches"] = total_matches
    elif filenames_only:
        out["files"] = files_hit
        out["total_matches"] = total_matches
    else:
        out["matches"] = lines_out
        out["total_matches"] = total_matches
    return out


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False  
  
# ---- exec script library (v0.9.102.5+) ----  
# LLM-generated python/powershell/bash/cmd code is AUTO-SAVED (sha1 dedup)  
# after a SUCCESSFUL run_ call into (WRITE_ROOT)/scripts/ so recurring  
# jobs re-run with ONE script_run call instead of re-generating the code  
# (1-3k output tokens + 2-5 s each time). Isolation backends for  
# script_run: direct (inherit interpreter) / conda run -n ENV / docker  
# run (write sandbox = the ONLY mount, network none by default, bridge  
# opt-in per script).  
_SCRIPT_SUF = {'py': '.py', 'cmd': '.cmd', 'ps1': '.ps1', 'sh': '.sh'}  
_SCRIPT_SAVE_MAX_CHARS = 500000  
_SCRIPT_GET_MAX_CHARS = 200000  
_AUTO_SAVE_MIN_CHARS = 200  
_AUTO_SCRIPT_CAP = 50  

# ---- exec script library (v0.9.102.5+) ----  
# LLM-generated python/powershell/bash/cmd code is AUTO-SAVED (sha1 dedup)  
# after a SUCCESSFUL run_ call into (WRITE_ROOT)/scripts/ so recurring  
# jobs re-run with ONE script_run call instead of re-generating the code  
# (1-3k output tokens + 2-5 s each time). Isolation backends for  
# script_run: direct (inherit interpreter) / conda run -n ENV / docker  
# run (write sandbox = the ONLY mount, network none by default, bridge  
# opt-in per script).  
_SCRIPT_SUF = {'py': '.py', 'cmd': '.cmd', 'ps1': '.ps1', 'sh': '.sh'}  
_SCRIPT_SAVE_MAX_CHARS = 500000  
_SCRIPT_GET_MAX_CHARS = 200000  
_AUTO_SAVE_MIN_CHARS = 200  
_AUTO_SCRIPT_CAP = 50  
def _env_flag(name, default=''):  
    return str(os.environ.get(name, default) or default).strip()  
def script_library_enabled():  
    return _env_flag('QDRANT_EXEC_SCRIPT_LIBRARY', 'on').lower() not in ('off', 'false', '0', 'no')  
def _global_runtime():  
    v = _env_flag('QDRANT_SCRIPT_RUNTIME').lower()  
    return v if v in ('conda', 'docker') else ''  
def _now_iso():  
    from datetime import datetime  
    return datetime.now().isoformat(timespec='seconds') 
def _envname_ok(s):  
    return (1 <= len(s) <= 32 and not s.startswith('-') and all(c.isalnum() or c in '_-' for c in s))  
def _image_ok(s):  
    return bool(s) and all(c.isalnum() or c in './:_-' for c in s) and ' ' not in s and not s.startswith('-')  
def scripts_dir(write_root):  
    d = (Path(write_root) / 'scripts').resolve()  
    d.mkdir(parents=True, exist_ok=True)  
    return d  
def _load_index(sdir):  
    import json  
    try:  
        raw = json.loads((sdir / 'index.json').read_text(encoding='utf-8'))  
        if isinstance(raw, dict):  
            return {str(k): v for k, v in raw.items() if isinstance(v, dict)}  
    except Exception:  
        pass  
    return {}  
def _save_index(sdir, idx):  
    import json  
    p = sdir / 'index.json'  
    tmp = p.with_suffix('.json.tmp')  
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')  
    os.replace(tmp, p) 
def detect_script_lang(lang, code):  
    l_ = (lang or 'auto').strip().lower()  
    aliases = {'python': 'py', 'python3': 'py', 'py': 'py', 'powershell': 'ps1', 'ps': 'ps1', 'ps1': 'ps1', 'bash': 'sh', 'shell': 'sh', 'sh': 'sh', 'unix': 'sh', 'cmd': 'cmd', 'bat': 'cmd', 'batch': 'cmd', 'windows': 'cmd', 'auto': 'auto'}  
    if l_ not in aliases:  
        return 'py'  
    if aliases[l_] != 'auto':  
        return aliases[l_]  
    low = (code or '').lower()  
    if 'import ' in low or 'print(' in low or 'def ' in low:  
        return 'py'  
    lines2 = [ln.strip() for ln in low.splitlines()]  
    for s in lines2:  
        if s.startswith(('$', 'param(', 'get-', 'set-', 'write-')):  
            return 'ps1'  
        if s.startswith('#!'):  
            if 'python' in s:  
                return 'py'  
            if 'bash' in s or 'sh' in s:  
                return 'sh'  
    if '-command' in low or 'rem ' in low or 'cmdlet' in low:  
        return 'cmd'  
    return 'py' 
def scripts_dir(write_root):  
    d = (Path(write_root) / 'scripts').resolve()  
    d.mkdir(parents=True, exist_ok=True)  
    return d  
def _load_index(sdir):  
    import json  
    try:  
        raw = json.loads((sdir / 'index.json').read_text(encoding='utf-8'))  
        if isinstance(raw, dict):  
            return {str(k): v for k, v in raw.items() if isinstance(v, dict)}  
    except Exception:  
        pass  
    return {}  
def _save_index(sdir, idx):  
    import json  
    p = sdir / 'index.json'  
    tmp = p.with_suffix('.json.tmp')  
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')  
    os.replace(tmp, p) 
def save_script(code, lang='auto', name='', description='', conda_env='', docker_image='', network='', scripts_root=None):  
    code = (code or '').strip()  
    if not code:  
        return {'error': "'code' is required."}  
    if len(code) > _SCRIPT_SAVE_MAX_CHARS:  
        return {'error': f'code too large: {len(code)} chars (cap {_SCRIPT_SAVE_MAX_CHARS}).'}  
    hit = is_dangerous(code)  
    if hit:  
        return {'error': f'refused: destructive pattern in code ({hit}) - the library only stores code the run_ tools would execute.'}  
    l_ = detect_script_lang(lang, code)  
    ce = str(conda_env or '').strip()  
    di = str(docker_image or '').strip()  
    nw = str(network or '').strip().lower()  
    if ce and not _envname_ok(ce):  
        return {'error': 'invalid conda_env: letters/digits/underscore/hyphen, max 32, no leading hyphen.'}  
    if ce and l_ != 'py':  
        return {'error': 'conda_env only applies to python scripts (lang=py).'}  
    if nw and nw not in ('none', 'bridge'):  
        return {'error': "network must be 'none' or 'bridge'."}  
    if di and l_ == 'cmd':  
        return {'error': 'docker does not apply to cmd scripts (use sh, or run directly).'}  
    if di and not _image_ok(di):  
        return {'error': 'invalid docker_image: use repo/path:tag - letters/digits/dot/slash/dash/underscore only.'}  
    if ce and di:  
        return {'error': 'choose ONE isolation backend: conda_env or docker_image (not both).'} 
    import hashlib  
    sha1 = hashlib.sha1(code.encode('utf-8')).hexdigest()  
    sdir = scripts_dir(scripts_root)  
    idx = _load_index(sdir)  
    for ename, e in idx.items():  
        if e.get('sha1') == sha1:  
            changed = False  
            if description and description != e.get('description'):  
                e['description'] = str(description)[:200]  
                changed = True  
            for kkey, v2 in (('conda_env', ce), ('docker_image', di), ('network', nw)):  
                if v2 and e.get(kkey) != v2:  
                    e[kkey] = v2  
                    changed = True  
            if changed:  
                _save_index(sdir, idx)  
            return {'ok': True, 'name': ename, 'duplicate': True, 'lang': e.get('lang'), 'chars': e.get('chars'), 'runs': e.get('runs', 0), 'file': (sdir / ename).as_posix()}  
    fname = _script_file_name(name or ('script-' + sha1[:6]), l_)  
    if fname in idx:  
        stem, dot, suf = fname.rpartition('.')  
        n = 2  
        while fname in idx:  
            fname = stem + '-' + str(n) + '.' + suf  
            n = n + 1  
    path = sdir / fname  
    if not _within(path.resolve(), sdir):  
        return {'error': 'resolved path escaped the scripts sandbox.'}  
    path.write_bytes(code.encode('utf-8'))  
    idx[fname] = {'lang': l_, 'sha1': sha1, 'chars': len(code), 'description': str(description or '')[:200], 'saved_at': _now_iso(), 'runs': 0, 'last_run_at': None, 'conda_env': ce, 'docker_image': di, 'network': nw, 'auto': False}  
    _save_index(sdir, idx)  
    backend = ('docker:' + di) if di else (('conda:' + ce) if ce else 'direct')  
    return {'ok': True, 'name': fname, 'file': path.as_posix(), 'chars': len(code), 'lang': l_, 'runtime': backend, 'note': 'replay with script_run; runtime=' + backend} 
def list_scripts(scripts_root=None):  
    sdir = scripts_dir(scripts_root)  
    idx = _load_index(sdir)  
    rows = []  
    for ename, e in sorted(idx.items(), key=lambda kv: str(kv[1].get('saved_at', '')), reverse=True):  
        rows.append({'name': ename, 'lang': e.get('lang'), 'chars': e.get('chars'), 'description': e.get('description', ''), 'saved_at': e.get('saved_at'), 'runs': e.get('runs', 0), 'last_run_at': e.get('last_run_at'), 'conda_env': e.get('conda_env', ''), 'docker_image': e.get('docker_image', ''), 'auto': bool(e.get('auto'))})  
    return {'ok': True, 'scripts': rows, 'total': len(rows), 'dir': sdir.as_posix()}  
def _split_args(args):  
    args = str(args or '').strip()  
    if not args:  
        return []  
    try:  
        import shlex  
        return shlex.split(args)  
    except ValueError:  
        return args.split() 
def run_script(name, args='', cwd=None, timeout=DEFAULT_TIMEOUT, max_output=DEFAULT_MAX_OUTPUT, read_root=None, scripts_root=None):  
    sdir = scripts_dir(scripts_root)  
    fname, e, _ = _resolve_script(name, sdir)  
    if fname is None:  
        return {'error': 'no such script: ' + repr(str(name)) + ' - try script_list.'}  
    l_ = str(e.get('lang', 'py'))  
    path = sdir / fname  
    if not _within(path.resolve(), sdir):  
        return {'error': 'script path escaped the scripts sandbox.'}  
    if not path.is_file():  
        return {'error': 'script file missing on disk: ' + fname + ' - re-save it with script_save.'}  
    import hashlib  
    sha_now = hashlib.sha1(path.read_bytes()).hexdigest()  
    want = str(e.get('sha1', '') or '')  
    if want and sha_now != want:  
        return {'error': 'sha1 mismatch: the script file changed after save (re-save with script_save to update).'}  
    argv = _split_args(args)  
    started = time.monotonic()  
    di = str(e.get('docker_image', '') or '').strip()  
    ce = str(e.get('conda_env', '') or '').strip()  
    runtime_g = _global_runtime()  
    if l_ == 'py' and runtime_g == 'docker' and not di and not ce:  
        di = _env_flag('QDRANT_SCRIPT_DOCKER_IMAGE', 'python:3.12-slim')  
    if l_ == 'py' and runtime_g == 'conda' and not di and not ce:  
        ce = _env_flag('QDRANT_SCRIPT_CONDA_ENV')  
    code_txt = path.read_text(encoding='utf-8', errors='replace')  
    run_path = path  
    if l_ == 'py' and any(k in code_txt for k in _MPL_TRIGGER_WORDS):  
        run_path = sdir / ('.wrap-' + fname)  
        run_path.write_bytes(_prepend_mpl_cjk(code_txt).encode('utf-8'))  
    nw = str(e.get('network', '') or '').strip().lower()  
    if nw not in ('none', 'bridge'):  
        nw = _env_flag('QDRANT_SCRIPT_DOCKER_NETWORK', 'none').lower() or 'none'  
    mount_src = Path(cwd or sdir).resolve().as_posix()  
    try: 
        if di:  
            docker = shutil.which('docker')  
            if not docker:  
                return {'error': 'docker not found on PATH - install Docker Desktop or save with conda_env instead.'}  
            rel = run_path.name  
            pp = run_path.as_posix()  
            if cwd and Path(cwd).resolve() in run_path.resolve().parents:  
                rel = run_path.relative_to(Path(cwd).resolve()).as_posix()  
            cmds = [docker, 'run', '--rm', '-i', '--workdir', '/sandbox', '-v', (mount_src + ':/sandbox'), '--network', nw, '--memory', _env_flag('QDRANT_SCRIPT_DOCKER_MEM', '2g'), '--cpus', _env_flag('QDRANT_SCRIPT_DOCKER_CPUS', '2')]  
            inside = '/sandbox/' + rel  
            if l_ == 'py':  
                cmds = cmds + [di, 'python', inside]  
            elif l_ == 'sh':  
                cmds = cmds + [di, 'bash', inside]  
            elif l_ == 'ps1':  
                cmds = cmds + [di, 'pwsh', '-NoProfile', '-File', inside]  
            else:  
                return {'error': 'docker runtime does not apply to cmd scripts (run it directly instead).'}  
        elif ce and l_ == 'py':  
            conda = shutil.which('conda')  
            if not conda:  
                return {'error': 'conda not found on PATH - launch python from a conda env, or use the docker backend.'}  
            cmds = [conda, 'run', '--no-capture-output', '-n', ce, 'python', run_path.as_posix()] 
        elif l_ == 'py':  
            cmds = [sys.executable, run_path.as_posix()]  
        elif l_ == 'ps1':  
            cmds = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', run_path.as_posix()]  
        elif l_ == 'sh':  
            bash = find_bash()  
            cmds = [(bash or '/bin/sh'), run_path.as_posix()]  
        else:  
            cmds = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', run_path.as_posix()]  
        out = _run_capture(cmds + argv, (cwd or os.getcwd()), timeout, max_output, read_root)  
    finally:  
        if run_path != path:  
            try:  
                run_path.unlink()  
            except OSError:  
                pass  
    backend = ('docker:' + di) if di else (('conda:' + ce) if ce else 'direct')  
    out['tool'] = 'script:' + l_ + '/' + backend  
    res = _with_duration(out, started)  
    try:  
        idx2 = _load_index(sdir)  
        if fname in idx2:  
            idx2[fname]['runs'] = int(idx2[fname].get('runs', 0)) + 1  
            idx2[fname]['last_run_at'] = _now_iso()  
            _save_index(sdir, idx2)  
            res['run_count'] = idx2[fname]['runs']  
    except Exception:  
        pass  
    return res 
def auto_save_script(code, lang='py', scripts_root=None):  
    try:  
        if not script_library_enabled():  
            return None  
        code = (code or '').strip()  
        if len(code) < _AUTO_SAVE_MIN_CHARS:  
            return None  
        import hashlib  
        l_ = detect_script_lang(lang, code)  
        sha1 = hashlib.sha1(code.encode('utf-8')).hexdigest()  
        sdir = scripts_dir(scripts_root)  
        idx = _load_index(sdir)  
        for ename, e in idx.items():  
            if e.get('sha1') == sha1:  
                e['runs'] = int(e.get('runs', 0)) + 1  
                e['last_run_at'] = _now_iso()  
                _save_index(sdir, idx)  
                return {'name': ename, 'dedup': True, 'lang': l_, 'runs': e['runs']}  
        fname = _script_file_name('auto-' + sha1[:10], l_)  
        first = next((ln.strip() for ln in code.splitlines() if ln.strip()), '')  
        path = sdir / fname  
        if not _within(path.resolve(), sdir):  
            return None  
        path.write_bytes(code.encode('utf-8'))  
        idx[fname] = {'lang': l_, 'sha1': sha1, 'chars': len(code), 'description': ((first[:120] + ' - auto') if first else 'auto-saved from a run_ call'), 'saved_at': _now_iso(), 'runs': 1, 'last_run_at': _now_iso(), 'conda_env': '', 'docker_image': '', 'network': '', 'auto': True}  
        autos = sorted(((k, v) for k, v in idx.items() if v.get('auto')), key=lambda kv: str(kv[1].get('saved_at', '')))  
        for k, _v in autos[: max(0, len(autos) - _AUTO_SCRIPT_CAP)]:  
            try:  
                (sdir / k).unlink()  
            except OSError:  
                pass  
            idx.pop(k, None)  
        _save_index(sdir, idx)  
        return {'name': fname, 'dedup': False, 'lang': l_, 'runs': 1}  
    except Exception:  
        return None 

def _script_file_name(name, lang):
    s = (name or '').strip().lower()
    for suf in _SCRIPT_SUF.values():
        if s.endswith(suf):
            s = s[:len(s) - len(suf)]
            break
    chars = []
    for ch in s:
        okk = ch.isascii() and (ch.isalnum() or ch == '_' or ch == '-')
        chars.append(ch if okk else '-')
    s = ''.join(chars)
    while '--' in s:
        s = s.replace('--', '-')
    s = s.strip('-')[:64]
    if not s:
        s = 'script'
    return s + _SCRIPT_SUF.get(lang, '.py')
def _resolve_script(name, sdir):  
    idx = _load_index(sdir)  
    n = str(name or '').strip()  
    if not n:  
        return None, None, idx  
    if n in idx:  
        return n, idx[n], idx  
    for l_ in _SCRIPT_SUF:  
        cand = _script_file_name(n, l_)  
        if cand in idx:  
            return cand, idx[cand], idx  
    low = n.lower()  
    for ename, e in idx.items():  
        el = ename.lower()  
        if el.startswith(low) or el.endswith(low):  
            return ename, e, idx  
    return None, None, idx  
