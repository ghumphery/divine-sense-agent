"""Log anomaly analysis package (v0.9.7+).

Parses web-server access logs (Apache / Nginx combined format) and
system logs (syslog RFC3164 / RFC5424), embeds every log line into a
dedicated Qdrant collection, then analyses the corpus for anomalies:

* rule-based statistical detection (HTTP error rates, 404 scanning,
  brute-force IPs, suspicious paths / user-agents, SSH auth failures,
  OOM / kernel events, log floods), and
* semantic outlier detection — the lines whose embedding sits far
  from every other line's are the ones that "don't look like the
  rest of the log" (semantic outliers).

Modules:

* :mod:`src.log_analyzer.parser`    — pure line parsers, zero deps
* :mod:`src.log_analyzer.anomalies` — rule engine + numpy outliers
* :mod:`src.log_analyzer.store`     — ``LogStore`` (Qdrant wrapper)
"""

from src.log_analyzer.parser import (  # noqa: F401
    detect_log_format,
    parse_access_log_line,
    parse_log_lines,
    parse_syslog_line,
)
from src.log_analyzer.store import LogStore  # noqa: F401

__all__ = [
    "LogStore",
    "detect_log_format",
    "parse_access_log_line",
    "parse_log_lines",
    "parse_syslog_line",
]