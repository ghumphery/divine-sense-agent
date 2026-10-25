"""Log line parsers — pure functions, zero external dependencies.

Two formats are supported (v0.9.7+):

Access log — Apache / Nginx "combined" format::

    203.0.113.42 - - [10/Sep/2026:13:55:36 +0800] "GET /index.html HTTP/1.1" 200 2326
        "https://example.com/" "Mozilla/5.0 ..."

(the referer / user-agent pair is optional; the parser tolerates
"common" format lines too).

System log — syslog RFC3164::

    Sep 10 13:55:36 myhost sshd[1234]: Failed password for root from 10.0.0.8 port 55222 ssh2

and RFC5424 (basic support)::

    <34>1 2026-09-10T13:55:36.123Z myhost sshd 1234 - - Failed password for root

Every parser returns a flat ``dict`` payload with a uniform shape so
the ingest pipeline can upsert it into Qdrant without format-specific
branches downstream (keys documented in the module docstring of
``parse_log_lines``). Malformed lines never raise — they come back
counted in ``skipped`` so the caller can report-and-move-on.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

__all__ = [
    "detect_log_format",
    "classify_level",
    "parse_access_log_line",
    "parse_syslog_line",
    "parse_log_lines",
]

# ─── access log (combined / common) ──────────────────────────────
#
# 203.0.113.42 - - [10/Sep/2026:13:55:36 +0800] "GET /x HTTP/1.1" 200 2326 "ref" "ua"
ACCESS_LOG_RE = re.compile(
    r"^(?P<ip>\S+)\s+\S+\s+\S+\s+"          # ip ident user
    r"\[(?P<ts>[^\]]+)\]\s+"
    r'"(?P<request>[^"]*)"\s+'
    r"(?P<status>\d{3})\s+(?P<size>\S+)"
    r'(?:\s+"(?P<referer>[^"]*)"\s+"(?P<agent>[^"]*)")?'
    r"\s*$"
)

_REQUEST_RE = re.compile(
    r"^(?P<method>[A-Z]+)\s+(?P<path>\S+)(?:\s+(?P<protocol>\S+))?$"
)

# ─── syslog ──────────────────────────────────────────────────────
#
# RFC3164: "Sep 10 13:55:36 host proc[pid]: message"
SYSLOG_RE = re.compile(
    r"^(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+"
    r"(?P<proc>[A-Za-z][\w\-./]*)(?:\[(?P<pid>\d+)\])?:\s+"
    r"(?P<msg>.*)$",
)

# RFC5424: "<34>1 2026-09-10T13:55:36.123Z host app procid msgid [sd] msg"
SYSLOG_RFC5424_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<ver>\d+)\s+"
    r"(?P<ts>\S+)\s+"
    r"(?P<host>\S+)\s+"
    r"(?P<proc>\S+)\s+"
    r"(?P<procid>\S+)\s+"
    r"(?P<msgid>\S+)\s+"
    r"(?P<sd>-|\[.*?\])\s*"
    r"(?P<msg>.*)$",
)


def classify_level(message: str) -> str:
    """Keyword-based severity classification for syslog messages.

    Syslog lines in the wild rarely carry a trustworthy explicit PRI
    severity (many daemons log everything at ``info``); the practical
    signal is the message text itself. Ordered most→least severe so
    the first hit wins.
    """
    m = message.lower()
    if any(
        k in m
        for k in (
            "panic", "emergency", "fatal", "out of memory",
            "oom killer", "kernel bug",
        )
    ):
        return "critical"
    if any(
        k in m
        for k in (
            "error", "fail", "failed", "failure", "denied",
            "refused", "unreachable", "segfault", "corrupt",
            "authentication failure", "invalid user",
            "not authorized", "broken pipe",
        )
    ):
        return "error"
    if any(
        k in m
        for k in (
            "warning", "warn", "invalid", "deprecated", "retry",
            "timeout", "timed out", "slow", "rate limit",
        )
    ):
        return "warning"
    if any(k in m for k in ("debug", "trace")):
        return "debug"
    return "info"


def _parse_access_ts(raw: str) -> tuple[str, float]:
    """``10/Sep/2026:13:55:36 +0800`` → (ISO string, epoch seconds)."""
    try:
        dt = datetime.strptime(raw, "%d/%b/%Y:%H:%M:%S %z")
        return dt.isoformat(), dt.timestamp()
    except Exception:
        return raw or "", 0.0


def _parse_syslog_ts(raw: str) -> tuple[str, float]:
    """RFC3164 ``Sep 10 13:55:36`` → ISO string + epoch.

    RFC3164 has no year — assume the current year. A log written last
    December read this January gets a future-ish timestamp; that is an
    accepted limitation of the format itself (documented in README).
    """
    try:
        now = time.localtime()
        dt = datetime.strptime(
            f"{now.tm_year} {raw}", "%Y %b %d %H:%M:%S",
        ).astimezone()
        return dt.isoformat(), dt.timestamp()
    except Exception:
        return raw or "", 0.0


def _parse_rfc5424_ts(raw: str) -> tuple[str, float]:
    if not raw or raw == "-":
        return "", 0.0
    try:
        # RFC5424 timestamps are already ISO-8601 (with Z or offset).
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.isoformat(), dt.timestamp()
    except Exception:
        return raw, 0.0


def _parse_rfc5424_pri(pri: str) -> int:
    """PRI = facility*8 + severity; severity = PRI % 8 (0=emerg)."""
    try:
        return int(pri) % 8
    except Exception:
        return 6  # info


_PRI_SEVERITY = {
    0: "critical", 1: "critical", 2: "critical",
    3: "error", 4: "warning", 5: "info",
    6: "info", 7: "debug",
}


def _entry_base(line: str, line_no: int) -> dict[str, Any]:
    """Uniform payload skeleton shared by both parsers."""
    return {
        "text": "",
        "raw": line.rstrip("\n"),
        "log_type": "",
        "ts": "",
        "ts_epoch": 0.0,
        "level": "",
        "src_ip": "",
        "method": "",
        "path": "",
        "protocol": "",
        "status": 0,
        "size": 0,
        "referer": "",
        "user_agent": "",
        "host": "",
        "proc": "",
        "message": "",
        "line_no": line_no,
    }


def parse_access_log_line(line: str, line_no: int = 0) -> dict[str, Any] | None:
    """Parse one combined/common-format access log line.

    Returns ``None`` when the line clearly isn't an access-log entry
    (caller counts it as skipped). A regex match with an unparseable
    timestamp still returns an entry — the payload keeps the raw ts.
    """
    m = ACCESS_LOG_RE.match(line.strip())
    if not m:
        return None
    request = m.group("request")
    rm = _REQUEST_RE.match(request) if request else None
    ts_iso, ts_epoch = _parse_access_ts(m.group("ts"))
    try:
        status = int(m.group("status"))
    except Exception:
        status = 0
    size_raw = m.group("size")
    size = 0 if size_raw in ("-", "") else (
        int(size_raw) if size_raw.isdigit() else 0
    )
    method = rm.group("method") if rm else ""
    path = rm.group("path") if rm else (request or "")
    ua = m.group("agent") or ""

    entry = _entry_base(line, line_no)
    entry.update({
        "log_type": "access",
        "ts": ts_iso,
        "ts_epoch": ts_epoch,
        "src_ip": m.group("ip"),
        "method": method,
        "path": path,
        "protocol": (rm.group("protocol") if rm else "") or "",
        "status": status,
        "size": size,
        "referer": m.group("referer") or "",
        "user_agent": ua,
    })
    # Semantic text: everything EXCEPT the noisy timestamp / ip —
    # embedding this keeps kNN clusters aligned with behaviour, not
    # with clock values.
    entry["text"] = " ".join(
        p for p in (
            f"{method} {path}".strip(),
            f"status={status}" if status else "",
            f"agent={ua}" if ua else "",
        ) if p
    ) or line.strip()
    return entry


def parse_syslog_line(line: str, line_no: int = 0) -> dict[str, Any] | None:
    """Parse one syslog line (RFC5424 first, then RFC3164).

    Returns ``None`` when the line matches neither dialect.
    """
    s = line.strip()
    if not s:
        return None

    m = SYSLOG_RFC5424_RE.match(s)
    if m:
        ts_iso, ts_epoch = _parse_rfc5424_ts(m.group("ts"))
        msg = m.group("msg")
        sev = _PRI_SEVERITY.get(_parse_rfc5424_pri(m.group("pri")))
        proc = m.group("proc") if m.group("proc") != "-" else ""
        host = m.group("host") if m.group("host") != "-" else ""
        entry = _entry_base(line, line_no)
        entry.update({
            "log_type": "syslog",
            "ts": ts_iso,
            "ts_epoch": ts_epoch,
            "level": sev or classify_level(msg),
            "host": host,
            "proc": proc,
            "message": msg,
            "text": f"{proc}: {msg}" if proc else msg,
        })
        return entry

    m = SYSLOG_RE.match(s)
    if m:
        ts_iso, ts_epoch = _parse_syslog_ts(m.group("ts"))
        msg = m.group("msg")
        proc = m.group("proc")
        entry = _entry_base(line, line_no)
        entry.update({
            "log_type": "syslog",
            "ts": ts_iso,
            "ts_epoch": ts_epoch,
            "level": classify_level(msg),
            "host": m.group("host"),
            "proc": proc,
            "message": msg,
            "text": f"{proc}: {msg}",
        })
        return entry

    return None

def detect_log_format(sample_lines: list[str]) -> str:
    """Majority vote over a sample: ``access`` | ``syslog`` | ``unknown``."""
    counts = {"access": 0, "syslog": 0}
    for line in sample_lines:
        s = line.strip()
        if ACCESS_LOG_RE.match(s):
            counts["access"] += 1
        elif SYSLOG_RE.match(s) or SYSLOG_RFC5424_RE.match(s):
            counts["syslog"] += 1
    if counts["access"] > counts["syslog"]:
        return "access"
    if counts["syslog"] > counts["access"]:
        return "syslog"
    return "unknown"


def parse_log_lines(
    lines: list[str],
    log_type: str = "auto",
    max_lines: int = 20000,
) -> dict[str, Any]:
    """Parse ``lines`` into Qdrant-ready entry payloads.

    ``log_type``: ``auto`` majority-detects over the first 50 lines;
    ``access`` / ``syslog`` force a single parser for every line.

    Returns::

        {
          "log_type":  detected/effective type,
          "entries":   [payload dicts] (capped at max_lines),
          "skipped":   count of unparseable / over-cap lines,
          "truncated": bool (file had more lines than max_lines),
        }
    """
    if log_type not in ("auto", "access", "syslog"):
        log_type = "auto"

    effective = log_type
    if effective == "auto":
        effective = detect_log_format(lines[:50])
        if effective == "unknown":
            return {
                "log_type": "unknown",
                "entries": [],
                "skipped": len(lines),
                "truncated": False,
            }

    parser = (
        parse_access_log_line if effective == "access" else parse_syslog_line
    )
    entries: list[dict[str, Any]] = []
    skipped = 0
    truncated = False
    for i, line in enumerate(lines, start=1):
        if len(entries) >= max_lines:
            truncated = True
            skipped += len(lines) - i + 1
            break
        parsed = parser(line, line_no=i)
        if parsed is None:
            skipped += 1
            continue
        entries.append(parsed)

    return {
        "log_type": effective,
        "entries": entries,
        "skipped": skipped,
        "truncated": truncated,
    }