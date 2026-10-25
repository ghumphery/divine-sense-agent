"""Tests for the log anomaly analysis MCP extension (v0.9.7+).

Covers the whole chain — parser, rule engine, semantic outliers,
LogStore, LogAnalysisLocalHost tool surface + sandbox, manager
registration, stdio MCP server, and the system-prompt trigger block.

All tests are mock-driven: ``LogStore`` and ``Embedder`` are replaced
with in-memory fakes on the host instance, so nothing touches a real
Qdrant server or embedding API.
"""
from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ─── sample log lines ────────────────────────────────────────────

ACCESS_LINE = (
    '203.0.113.42 - - [10/Sep/2026:13:55:36 +0800] '
    '"GET /index.html HTTP/1.1" 200 2326 '
    '"https://example.com/" "Mozilla/5.0 (Windows NT 10.0)"'
)
ACCESS_LINE_NO_REF = (
    '198.51.100.7 - alice [10/Sep/2026:13:56:01 +0800] '
    '"POST /api/login HTTP/1.1" 401 126'
)
SYSLOG_LINE = (
    "Sep 10 13:55:36 myhost sshd[1234]: Failed password for root "
    "from 10.0.0.8 port 55222 ssh2"
)
SYSLOG_LINE_PAM = (
    "Sep 10 13:55:40 myhost sshd[1234]: pam_unix(sshd:auth): "
    "authentication failure; logname= uid=0 euid=0 tty=ssh ruser= "
    "rhost=10.0.0.8 user=root"
)
RFC5424_LINE = (
    "<34>1 2026-09-10T13:55:36.123Z myhost sshd 1234 - - "
    "Failed password for admin from 10.9.9.9 port 55222 ssh2"
)
GARBAGE_LINE = "this is not a log line at all ???"


def _access(
    ip="203.0.113.42", status=200, path="/index.html",
    ua="Mozilla/5.0", ts="10/Sep/2026:13:55:36 +0800",
    line_no=0,
):
    line = (
        f'{ip} - - [{ts}] "GET {path} HTTP/1.1" {status} 100 '
        f'"-" "{ua}"'
    )
    from src.log_analyzer.parser import parse_access_log_line

    return parse_access_log_line(line, line_no=line_no)


def _syslog(msg, proc="sshd", ts="Sep 10 13:55:36", line_no=0):
    line = f"{ts} myhost {proc}[123]: {msg}"
    from src.log_analyzer.parser import parse_syslog_line

    return parse_syslog_line(line, line_no=line_no)


# ─── parser tests ────────────────────────────────────────────────


class TestParserAccessLog:
    def test_combined_line_full_parse(self):
        from src.log_analyzer.parser import parse_access_log_line

        e = parse_access_log_line(ACCESS_LINE, line_no=1)
        assert e is not None
        assert e["log_type"] == "access"
        assert e["src_ip"] == "203.0.113.42"
        assert e["method"] == "GET"
        assert e["path"] == "/index.html"
        assert e["status"] == 200
        assert e["size"] == 2326
        assert e["user_agent"].startswith("Mozilla/5.0")
        assert e["line_no"] == 1
        assert e["ts_epoch"] > 0
        assert "2026-09-10" in e["ts"]

    def test_common_format_without_referer_ua(self):
        from src.log_analyzer.parser import parse_access_log_line

        e = parse_access_log_line(
            '1.2.3.4 - - [10/Sep/2026:13:55:36 +0000] '
            '"GET / HTTP/1.1" 200 100',
        )
        assert e is not None
        assert e["status"] == 200
        assert e["user_agent"] == ""

    def test_malformed_line_returns_none(self):
        from src.log_analyzer.parser import parse_access_log_line

        assert parse_access_log_line(GARBAGE_LINE) is None
        assert parse_access_log_line("") is None

    def test_bad_timestamp_kept_raw(self):
        from src.log_analyzer.parser import parse_access_log_line

        e = parse_access_log_line(
            '1.2.3.4 - - [not-a-date] "GET / HTTP/1.1" 200 1',
        )
        assert e is not None
        assert e["ts_epoch"] == 0.0
        assert e["ts"] == "not-a-date"

    def test_ipv6_address(self):
        e = _access(ip="2001:db8::1")
        assert e["src_ip"] == "2001:db8::1"

    def test_semantic_text_excludes_ip_and_ts(self):
        e = _access()
        assert "203.0.113.42" not in e["text"]
        assert "GET /index.html" in e["text"]


class TestParserSyslog:
    def test_rfc3164_line(self):
        e = _syslog("Failed password for root from 10.0.0.8 port 22 ssh2")
        assert e["log_type"] == "syslog"
        assert e["host"] == "myhost"
        assert e["proc"] == "sshd"
        assert "Failed password" in e["message"]
        assert e["level"] == "error"
        assert e["ts_epoch"] > 0

    def test_rfc5424_line(self):
        from src.log_analyzer.parser import parse_syslog_line

        e = parse_syslog_line(RFC5424_LINE, line_no=3)
        assert e is not None
        assert e["log_type"] == "syslog"
        assert e["proc"] == "sshd"
        assert "Failed password" in e["message"]
        # PRI 34 -> severity 2 (critical) per the RFC5424 table
        assert e["level"] == "critical"
class TestClassifyLevel:
    def test_critical_keywords(self):
        from src.log_analyzer.parser import classify_level

        assert classify_level("Out of memory: Kill process 1") == "critical"
        assert classify_level("kernel panic - not syncing") == "critical"

    def test_error_keywords(self):
        from src.log_analyzer.parser import classify_level

        assert classify_level("Failed password for root") == "error"
        assert classify_level("Invalid user bob") == "error"
        assert classify_level("segfault at 0 ip") == "error"

    def test_warning_keywords(self):
        from src.log_analyzer.parser import classify_level

        assert classify_level("connection timeout, retrying") == "warning"
        assert classify_level("deprecated option used") == "warning"

    def test_info_default(self):
        from src.log_analyzer.parser import classify_level

        assert classify_level("session opened for user root") == "info"


class TestFormatDetection:
    def test_detect_access(self):
        from src.log_analyzer.parser import detect_log_format

        lines = [ACCESS_LINE, ACCESS_LINE_NO_REF, _access()["raw"]]
        assert detect_log_format(lines) == "access"

    def test_detect_syslog(self):
        from src.log_analyzer.parser import detect_log_format

        lines = [SYSLOG_LINE, SYSLOG_LINE_PAM, RFC5424_LINE]
        assert detect_log_format(lines) == "syslog"

    def test_detect_unknown(self):
        from src.log_analyzer.parser import detect_log_format

        assert detect_log_format([GARBAGE_LINE, "x y z"]) == "unknown"


class TestParseLogLines:
    def test_auto_detect_access(self):
        from src.log_analyzer.parser import parse_log_lines

        out = parse_log_lines([ACCESS_LINE, ACCESS_LINE_NO_REF])
        assert out["log_type"] == "access"
        assert len(out["entries"]) == 2
        assert out["skipped"] == 0

    def test_mixed_lines_skip_malformed(self):
        from src.log_analyzer.parser import parse_log_lines

        out = parse_log_lines([SYSLOG_LINE, GARBAGE_LINE, SYSLOG_LINE_PAM])
        assert out["log_type"] == "syslog"
        assert len(out["entries"]) == 2
        assert out["skipped"] == 1

    def test_unknown_format_reported(self):
        from src.log_analyzer.parser import parse_log_lines

        out = parse_log_lines([GARBAGE_LINE, "nope"])
        assert out["log_type"] == "unknown"
        assert out["entries"] == []
        assert out["skipped"] == 2

    def test_max_lines_truncates(self):
        from src.log_analyzer.parser import parse_log_lines

        lines = [_syslog(f"msg {i}", proc="p")["raw"] for i in range(5)]
        out = parse_log_lines(lines, max_lines=3)
        assert len(out["entries"]) == 3
        assert out["truncated"] is True
        assert out["skipped"] == 2

    def test_forced_syslog_parser_on_access_lines(self):
        from src.log_analyzer.parser import parse_log_lines

        out = parse_log_lines([ACCESS_LINE], log_type="syslog")
        assert out["log_type"] == "syslog"
        assert out["entries"] == []
        assert out["skipped"] == 1

    def test_garbage_log_type_falls_back_to_auto(self):
        from src.log_analyzer.parser import parse_log_lines

        out = parse_log_lines([SYSLOG_LINE], log_type="bogus")
        assert out["log_type"] == "syslog"
        assert len(out["entries"]) == 1


# ─── anomaly rule tests ──────────────────────────────────────────


class TestAccessRules:
    def test_empty_entries_no_findings(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        assert analyze_access_entries([]) == []

    def test_http_error_rate_warning(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        entries = [_access(status=200) for _ in range(3)]
        entries += [_access(status=404) for _ in range(2)]
        findings = analyze_access_entries(entries)
        rule = next(f for f in findings if f["rule"] == "http_error_rate")
        assert rule["severity"] == "warning"  # 40% errors
        assert any(ev["status"] == 404 for ev in rule["evidence"])

    def test_http_error_rate_critical(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        entries = [_access(status=200) for _ in range(2)]
        entries += [_access(status=500) for _ in range(6)]
        findings = analyze_access_entries(entries)
        rule = next(f for f in findings if f["rule"] == "http_error_rate")
        assert rule["severity"] == "critical"  # 75% errors

    def test_scan_404_detected(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        entries = [
            _access(ip="9.9.9.9", status=404, path=f"/p{i}.php")
            for i in range(12)
        ]
        findings = analyze_access_entries(entries)
        rule = next(f for f in findings if f["rule"] == "scan_404")
        assert rule["severity"] == "warning"
        assert rule["evidence"][0]["src_ip"] == "9.9.9.9"
        assert rule["evidence"][0]["distinct_404_paths"] == 12

    def test_suspicious_path_rule(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        entries = [
            _access(path="/page?id=1 union select * from users"),
            _access(path="/../../etc/passwd"),
        ]
        findings = analyze_access_entries(entries)
        rule = next(f for f in findings if f["rule"] == "suspicious_path")
        assert rule["severity"] == "warning"
        assert len(rule["evidence"]) == 2

    def test_benign_path_not_flagged(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        entries = [_access(path="/about-us"), _access(path="/products")]
        findings = analyze_access_entries(entries)
        assert not any(f["rule"] == "suspicious_path" for f in findings)

    def test_scanner_user_agent(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        entries = [_access(ua="sqlmap/1.5.2#stable")]
        findings = analyze_access_entries(entries)
        rule = next(f for f in findings if f["rule"] == "scanner_user_agent")
        assert "sqlmap" in rule["evidence"][0]["user_agent"]

    def test_high_frequency_ip(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        entries = [_access(ip="6.6.6.6") for _ in range(200)]
        entries += [_access(ip=f"10.0.0.{i % 9}") for i in range(9)]
        findings = analyze_access_entries(entries)
        rule = next(f for f in findings if f["rule"] == "high_frequency_ip")
        assert rule["severity"] == "critical"
        assert rule["evidence"][0]["src_ip"] == "6.6.6.6"

    def test_auth_failure_ip(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        entries = [_access(ip="7.7.7.7", status=401) for _ in range(8)]
        findings = analyze_access_entries(entries)
        rule = next(f for f in findings if f["rule"] == "auth_failure_ip")
        assert rule["severity"] == "critical"
        assert rule["evidence"][0]["auth_failures"] == 8

    def test_findings_sorted_by_severity(self):
        from src.log_analyzer.anomalies import analyze_access_entries

        entries = [_access(ip="7.7.7.7", status=401) for _ in range(8)]
        entries += [_access(status=200, path="/ok")]
        findings = analyze_access_entries(entries)
        sev = {"info": 0, "warning": 1, "critical": 2}
        levels = [sev.get(f["severity"], 0) for f in findings]
        assert levels == sorted(levels, reverse=True)


class TestSyslogRules:
    def test_empty_entries_no_findings(self):
        from src.log_analyzer.anomalies import analyze_syslog_entries

        assert analyze_syslog_entries([]) == []

    def test_severity_breakdown(self):
        from src.log_analyzer.anomalies import analyze_syslog_entries

        entries = [
            _syslog("Out of memory: Kill process 123"),
            _syslog("Failed password for root from 1.1.1.1"),
            _syslog("normal operation"),
        ]
        findings = analyze_syslog_entries(entries)
        rule = next(f for f in findings if f["rule"] == "severity_breakdown")
        assert rule["severity"] == "critical"  # one critical event

    def test_ssh_brute_force_by_ip(self):
        from src.log_analyzer.anomalies import analyze_syslog_entries

        entries = [
            _syslog("Failed password for root from 10.0.0.8 port 22")
            for _ in range(6)
        ]
        findings = analyze_syslog_entries(entries)
        rule = next(f for f in findings if f["rule"] == "ssh_brute_force")
        assert rule["severity"] == "critical"
        assert rule["evidence"][0]["src_ip"] == "10.0.0.8"
        assert rule["evidence"][0]["failed_attempts"] == 6
        assert rule["evidence"][0]["top_users"][0]["user"] == "root"

    def test_below_threshold_no_brute_force(self):
        from src.log_analyzer.anomalies import analyze_syslog_entries

        entries = [_syslog("Failed password for root from 1.1.1.1")]
        findings = analyze_syslog_entries(entries)
        assert not any(f["rule"] == "ssh_brute_force" for f in findings)

    def test_system_health_oom(self):
        from src.log_analyzer.anomalies import analyze_syslog_entries

        entries = [_syslog("Out of memory: Kill process 4321 (java)")]
        findings = analyze_syslog_entries(entries)
        rule = next(f for f in findings if f["rule"] == "system_health")
        assert rule["severity"] == "critical"

    def test_privilege_activity(self):
        from src.log_analyzer.anomalies import analyze_syslog_entries

        entries = [
            _syslog(
                "sudo: alice : TTY=pts/0 ; PWD=/ ; USER=root ; "
                "COMMAND=/bin/ls",
                proc="sudo",
            ),
        ]
        findings = analyze_syslog_entries(entries)
        assert any(f["rule"] == "privilege_activity" for f in findings)

    def test_log_flood_detected(self):
        from src.log_analyzer.anomalies import analyze_syslog_entries

        entries = [
            _syslog("same message over and over", proc="app") for _ in range(40)
        ]
        # 30 distinct patterns (digit-normalised) so the mean is low
        entries += [
            _syslog(f"{'x' * (i + 1)} unique marker", proc="app")
            for i in range(30)
        ]
        findings = analyze_syslog_entries(entries)
        rule = next((f for f in findings if f["rule"] == "log_flood"), None)
        assert rule is not None
        assert rule["evidence"][0]["count"] == 40

    def test_no_flood_on_varied_messages(self):
        from src.log_analyzer.anomalies import analyze_syslog_entries

        entries = [
            _syslog(f"unique message {i}", proc="app") for i in range(15)
        ]
        findings = analyze_syslog_entries(entries)
        assert not any(f["rule"] == "log_flood" for f in findings)

    def test_auth_failures_aggregate(self):
        from src.log_analyzer.anomalies import analyze_syslog_entries

        entries = [
            _syslog("Failed password for admin from 5.5.5.5"),
            _syslog("Invalid user oracle from 5.5.5.5"),
        ]
        findings = analyze_syslog_entries(entries)
        rule = next(f for f in findings if f["rule"] == "auth_failures")
        assert "2" in rule["title"]


# ─── semantic outlier tests ──────────────────────────────────────


class TestSemanticOutliers:
    def test_too_few_vectors_no_outliers(self):
        from src.log_analyzer.anomalies import semantic_outliers

        assert semantic_outliers([[1, 0], [0, 1]], [{"raw": "a"}, {"raw": "b"}]) == []

    def test_isolated_vector_flagged(self):
        from src.log_analyzer.anomalies import semantic_outliers

        vecs = [[1, 0], [0.95, 0.05], [0.9, 0.1], [0, 1]]
        payloads = [{"raw": f"line{i}"} for i in range(4)]
        out = semantic_outliers(vecs, payloads, threshold=0.5)
        assert len(out) >= 1
        assert out[0]["raw"] == "line3"  # [0,1] is most isolated

    def test_tight_cluster_no_outliers(self):
        from src.log_analyzer.anomalies import semantic_outliers

        vecs = [[1, 0], [0.99, 0.01], [0.98, 0.02]]
        payloads = [{"raw": f"line{i}"} for i in range(3)]
        out = semantic_outliers(vecs, payloads, threshold=0.35)
        assert out == []

    def test_top_n_cap(self):
        from src.log_analyzer.anomalies import semantic_outliers

        vecs = [
            [float(i % 5), float((i * 7) % 5), float((i * 3) % 5)]
            for i in range(30)
        ]
        payloads = [{"raw": f"line{i}"} for i in range(30)]
        out = semantic_outliers(vecs, payloads, threshold=1.1, top_n=5)
        assert len(out) == 5

    def test_length_mismatch_returns_empty(self):
        from src.log_analyzer.anomalies import semantic_outliers

        out = semantic_outliers(
            [[1, 0], [0, 1], [1, 1]],
            [{"raw": "a"}, {"raw": "b"}],
        )
        assert out == []


# ─── in-memory fakes for the host layer ──────────────────────────


class FakeLogStore:
    """In-memory stand-in for ``LogStore`` (no Qdrant)."""

    def __init__(self):
        self.collection = "log_docs_fake"
        self.points: dict[str, dict] = {}

    def make_entry_id(self, source, raw_line):
        import hashlib as _h

        return _h.sha1(
            f"{source}\n{raw_line}".encode("utf-8"),
        ).hexdigest()[:24]

    def upsert_entries(self, entries, source, vectors, batch_size=256):
        for e, v in zip(entries, vectors):
            pid = self.make_entry_id(source, e.get("raw", ""))
            self.points[pid] = {"vector": v, "payload": {**e, "source": source}}
        return len(entries)

    def search(self, qvec, top_k=10, source=None, log_type=None,
               level=None, min_status=None, score_threshold=0.2):
        rows = self._rows(source, log_type, level, min_status)
        scored = []
        for row in rows:
            sim = sum(a * b for a, b in zip(qvec, row["vector"]))
            if sim >= score_threshold:
                scored.append((sim, row))
        scored.sort(key=lambda p: -p[0])
        return [
            {**r["payload"], "id": pid, "score": s}
            for s, r in [
                (s, r) for s, r in scored[:top_k]
            ]
            for pid in [self._pid_of(r)]
        ]

    def _pid_of(self, row):
        for pid, p in self.points.items():
            if p is row:
                return pid
        return ""

    def _rows(self, source=None, log_type=None, level=None, min_status=None):
        out = []
        for p in self.points.values():
            pl = p["payload"]
            if source and pl.get("source") != source:
                continue
            if log_type and pl.get("log_type") != log_type:
                continue
            if level and pl.get("level") != level:
                continue
            if min_status and int(pl.get("status") or 0) < min_status:
                continue
            out.append(p)
        return out

    def scroll_points(self, source=None, log_type=None, level=None,
                      min_status=None, limit=10000, with_vectors=False):
        rows = self._rows(source, log_type, level, min_status)[:limit]
        return [
            {**r["payload"], **({"_vector": r["vector"]} if with_vectors else {})}
            for r in rows
        ]

    def count_points(self, source=None, log_type=None):
        return len(self._rows(source, log_type))

    def delete_by_source(self, source):
        doomed = [
            pid for pid, p in self.points.items()
            if p["payload"].get("source") == source
        ]
        for pid in doomed:
            del self.points[pid]
        return len(doomed)

    def distinct_sources(self):
        counts: dict[str, int] = {}
        for p in self.points.values():
            src = p["payload"].get("source", "")
            if src:
                counts[src] = counts.get(src, 0) + 1
        return [
            {"source": s, "count": c} for s, c in sorted(counts.items())
        ]


class FakeEmbedder:
    """Deterministic hash embedder for tests (3 dims)."""

    @staticmethod
    def _vec(text):
        h = hashlib.sha1(text.encode("utf-8")).digest()
        return [h[0] / 255.0, h[1] / 255.0, h[2] / 255.0]

    def embed(self, text):
        return self._vec(text)

    def embed_batch(self, texts):
        return [self._vec(t) for t in texts]


# ─── host fixtures ───────────────────────────────────────────────


@pytest.fixture
def log_root(tmp_path, monkeypatch):
    """Pin every sandbox env var at a per-test tmp dir."""
    monkeypatch.setenv("QDRANT_LOG_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(tmp_path))
    monkeypatch.setenv("WATCH_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def host(log_root):
    """LogAnalysisLocalHost with fake store + embedder injected."""
    from src.mcp_client.host import LogAnalysisLocalHost

    h = LogAnalysisLocalHost()
    h._store = FakeLogStore()
    h._embedder = FakeEmbedder()
    return h


def _write_log(root, name, lines):
    p = root / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _matches_filter(payload, qfilter):
    if qfilter is None:
        return True
    for cond in getattr(qfilter, "must", None) or []:
        key = getattr(cond, "key", None)
        if key is None:
            continue
        mv = getattr(cond, "match", None)
        rng = getattr(cond, "range", None)
        if mv is not None:
            if payload.get(key) != mv.value:
                return False
        elif rng is not None:
            v = payload.get(key) or 0
            try:
                if float(v) < float(rng.gte):
                    return False
            except (TypeError, ValueError):
                return False
    return True


class FakeQdrantClient:
    """Minimal QdrantClient stand-in for LogStore unit tests."""

    def __init__(self):
        self.points: dict[str, dict] = {}
        self.collections: set[str] = set()
        self.created_indexes: list[tuple[str, str]] = []

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, collection_name, **kwargs):
        self.collections.add(collection_name)

    def create_payload_index(self, collection_name, field_name, field_schema):
        self.created_indexes.append((field_name, str(field_schema)))

    def upsert(self, collection_name, points, wait=True):
        for p in points:
            self.points[str(p.id)] = {
                "vector": list(p.vector), "payload": dict(p.payload),
            }

    def query_points(self, collection_name, query=None, limit=10,
                     with_payload=True, with_vectors=False,
                     query_filter=None, score_threshold=0.0):
        class _Resp:
            pass

        scored = []
        for pid, p in self.points.items():
            if not _matches_filter(p["payload"], query_filter):
                continue
            sim = sum(a * b for a, b in zip(query, p["vector"]))
            if sim >= score_threshold:
                scored.append((sim, pid))
        scored.sort(key=lambda pair: -pair[0])

        class _Hit:
            def __init__(self, pid, score, payload):
                self.id = pid
                self.score = score
                self.payload = payload

        r = _Resp()
        r.points = [
            _Hit(pid, s, self.points[pid]["payload"])
            for s, pid in scored[:limit]
        ]
        return r

    def scroll(self, collection_name, scroll_filter=None, limit=1000,
               with_payload=True, with_vectors=False, offset=None):
        class _P:
            def __init__(self, pid, payload, vector):
                self.id = pid
                self.payload = payload
                self.vector = vector

        ids = list(self.points.keys())
        if offset is not None and offset in ids:
            idx = ids.index(offset)
            ids = ids[idx + 1:]
        page = ids[:limit]
        next_offset = page[-1] if len(ids) > limit else None
        return (
            [
                _P(
                    i,
                    self.points[i]["payload"],
                    self.points[i]["vector"] if with_vectors else None,
                )
                for i in page
            ],
            next_offset,
        )

    def count(self, collection_name, count_filter=None, exact=True):
        class _C:
            pass

        c = _C()
        c.count = len([
            1 for p in self.points.values()
            if _matches_filter(p["payload"], count_filter)
        ])
        return c

    def delete(self, collection_name, points_selector):
        f = getattr(points_selector, "filter", None)
        doomed = [
            pid for pid, p in self.points.items()
            if _matches_filter(p["payload"], f)
        ]
        for pid in doomed:
            del self.points[pid]


class TestLogStore:
    def test_make_entry_id_stable_and_differs_by_source(self):
        from src.log_analyzer.store import LogStore

        a = LogStore.make_entry_id("/logs/a.log", "line one")
        a2 = LogStore.make_entry_id("/logs/a.log", "line one")
        b = LogStore.make_entry_id("/logs/b.log", "line one")
        assert a == a2
        assert a != b
        # Qdrant point IDs must be an unsigned integer or a UUID — the
        # old 24-hex truncated sha1 was rejected with HTTP 400
        # ("not a valid point ID", fixed v0.9.9.1). Must parse as UUID.
        import uuid as _uuid

        assert str(_uuid.UUID(a)) == a
        assert str(_uuid.UUID(b)) == b

    def test_default_collection_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_LOG_COLLECTION", "custom_log_docs")
        from src.log_analyzer.store import LogStore

        assert LogStore().collection == "custom_log_docs"

    def test_ensure_collection_creates_indexes(self):
        from src.log_analyzer.store import LogStore

        store = LogStore(collection="log_docs_test")
        fake = FakeQdrantClient()
        store._client = fake
        store._ensure_collection()
        assert "log_docs_test" in fake.collections
        fields = [f for f, _ in fake.created_indexes]
        for expected in (
            "source", "log_type", "level", "src_ip", "status", "ts_epoch",
        ):
            assert expected in fields

    def test_upsert_search_roundtrip(self):
        from src.log_analyzer.store import LogStore

        store = LogStore(collection="log_docs_test")
        fake = FakeQdrantClient()
        store._client = fake
        entries = [
            _syslog("Failed password for root from 10.0.0.8", line_no=1),
            _syslog("disk cleanup finished", line_no=2),
        ]
        vecs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        n = store.upsert_entries(
            entries, source="/logs/syslog", vectors=vecs,
        )
        assert n == 2
        hits = store.search([1.0, 0.0, 0.0], top_k=2, score_threshold=0.0)
        assert len(hits) == 2
        assert hits[0]["message"].startswith("Failed password")
        assert hits[0]["source"] == "/logs/syslog"

    def test_scroll_with_and_without_vectors(self):
        from src.log_analyzer.store import LogStore

        store = LogStore(collection="log_docs_test")
        fake = FakeQdrantClient()
        store._client = fake
        store.upsert_entries(
            [_syslog("hello world", line_no=1)],
            source="/l/x", vectors=[[1, 2, 3]],
        )
        rows = store.scroll_points(with_vectors=True)
        assert len(rows) == 1
        assert rows[0]["_vector"] == [1, 2, 3]
        assert "_vector" not in store.scroll_points()[0]

    def test_delete_by_source(self):
        from src.log_analyzer.store import LogStore

        store = LogStore(collection="log_docs_test")
        fake = FakeQdrantClient()
        store._client = fake
        store.upsert_entries(
            [_syslog("one", line_no=1), _syslog("two", line_no=2)],
            source="/a.log", vectors=[[1], [2]],
        )
        assert store.count_points(source="/a.log") == 2
        assert store.delete_by_source("/a.log") == 2
        assert store.count_points(source="/a.log") == 0
        assert store.delete_by_source("/a.log") == 0

    def test_build_filter_none_when_no_filters(self):
        from src.log_analyzer.store import LogStore

        store = LogStore(collection="log_docs_test")
        assert store._build_filter() is None


# ─── host tests ──────────────────────────────────────────────────


class TestHostToolSurface:
    def test_six_tools_advertised(self, host):
        names = [t.name for t in host.list_tools()]
        assert names == [
            "ingest_log", "analyze_anomalies", "search_logs",
            "get_log_stats", "list_log_sources", "remove_logs",
        ]

    def test_server_name_and_args_convention(self, host):
        for t in host.list_tools():
            assert t.server_name == "log-analyzer-local"
            params = list(
                inspect.signature(t._handler).parameters.values(),
            )
            assert len(params) == 1 and params[0].name == "args", t.name


class TestHostIngest:
    def test_missing_path_arg(self, host):
        assert "error" in host._ingest_log({})

    def test_happy_path(self, host, log_root):
        _write_log(log_root, "access.log", [ACCESS_LINE, ACCESS_LINE_NO_REF])
        out = host._ingest_log({"path": "access.log"})
        assert out.get("ok") is True, out
        assert out["log_type"] == "access"
        assert out["ingested"] == 2
        assert out["skipped"] == 0
        assert out["source"].endswith("access.log")
        assert len(host._store.points) == 2
        for p in host._store.points.values():
            assert p["payload"]["source"] == out["source"]
            assert len(p["vector"]) == 3

    def test_absolute_path_inside_sandbox(self, host, log_root):
        p = _write_log(log_root, "s.log", [SYSLOG_LINE])
        out = host._ingest_log({"path": str(p)})
        assert out.get("ok") is True

    def test_outside_sandbox_rejected(self, host, tmp_path):
        outside = tmp_path.parent / "outside.log"
        outside.write_text(SYSLOG_LINE, encoding="utf-8")
        out = host._ingest_log({"path": str(outside)})
        assert "error" in out
        assert "sandbox" in out["error"]

    def test_traversal_rejected(self, host, log_root):
        out = host._ingest_log({"path": "../secrets.log"})
        assert "error" in out

    def test_nonexistent_file(self, host, log_root):
        out = host._ingest_log({"path": "ghost.log"})
        assert "error" in out

    def test_unknown_format_suggests_forcing(self, host, log_root):
        _write_log(log_root, "weird.log", [GARBAGE_LINE, "nope"])
        out = host._ingest_log({"path": "weird.log"})
        assert "error" in out
        assert "log_type" in out["error"]

    def test_forced_parser(self, host, log_root):
        _write_log(log_root, "forced.log", [GARBAGE_LINE, SYSLOG_LINE])
        out = host._ingest_log({"path": "forced.log", "log_type": "syslog"})
        assert out.get("ok") is True
        assert out["ingested"] == 1
        assert out["skipped"] == 1

    def test_reingest_same_file_no_duplicates(self, host, log_root):
        _write_log(log_root, "a.log", [SYSLOG_LINE, SYSLOG_LINE_PAM])
        host._ingest_log({"path": "a.log"})
        host._ingest_log({"path": "a.log"})
        assert len(host._store.points) == 2  # upsert, not append

    def test_max_lines_cap(self, host, log_root):
        lines = [_syslog(f"msg {i}", proc="p")["raw"] for i in range(10)]
        _write_log(log_root, "big.log", lines)
        out = host._ingest_log({"path": "big.log", "max_lines": 4})
        assert out["ingested"] == 4
        assert out["truncated"] is True
        assert "note" in out


class TestHostAnalyze:
    def test_no_points_error(self, host):
        out = host._analyze({})
        assert "error" in out

    def test_empty_corpus_hint(self, host):
        out = host._analyze({})
        assert "error" in out
        assert "list_log_sources" in out["error"]

    def test_access_analysis_end_to_end(self, host, log_root):
        lines = (
            [
                _access(
                    ip="6.6.6.6", status=200, path=f"/page{i}",
                )["raw"]
                for i in range(6)
            ]
            + [
                _access(ip="9.9.9.9", status=404, path=f"/p{i}.php")["raw"]
                for i in range(10)
            ]
            + [_access(ua="sqlmap/1.5", status=200, path="/x")["raw"]]
        )
        _write_log(log_root, "acc.log", lines)
        host._ingest_log({"path": "acc.log"})
        out = host._analyze({})
        assert out.get("ok") is True
        assert out["scanned"] == 17
        assert out["log_types"] == {"access": 17}
        rules = {f["rule"] for f in out["findings"]}
        assert "scan_404" in rules
        assert "scanner_user_agent" in rules
        assert "http_error_rate" in rules

    def test_syslog_analysis_end_to_end(self, host, log_root):
        lines = [
            _syslog(
                f"Failed password for root from 10.0.0.8 port {50000 + i}",
            )["raw"]
            for i in range(7)
        ]
        _write_log(log_root, "sys.log", lines)
        host._ingest_log({"path": "sys.log"})
        out = host._analyze({})
        rules = {f["rule"] for f in out["findings"]}
        assert "ssh_brute_force" in rules
        assert "auth_failures" in rules

    def test_semantic_outliers_key_present(self, host, log_root):
        lines = [
            _syslog(f"routine check {i}", proc="app")["raw"] for i in range(6)
        ]
        lines.append(SYSLOG_LINE)
        _write_log(log_root, "mix.log", lines)
        host._ingest_log({"path": "mix.log"})
        out = host._analyze({"top_n": 5})
        assert "semantic_outliers" in out
        assert out["outlier_threshold"] > 0

    def test_source_filter_scopes(self, host, log_root):
        _write_log(log_root, "one.log", [SYSLOG_LINE])
        _write_log(log_root, "two.log", [SYSLOG_LINE_PAM])
        host._ingest_log({"path": "one.log"})
        host._ingest_log({"path": "two.log"})
        src_one = [
            s["source"] for s in host._store.distinct_sources()
            if s["source"].endswith("one.log")
        ][0]
        out = host._analyze({"source": src_one})
        assert out["scanned"] == 1

    def test_log_type_filter(self, host, log_root):
        _write_log(log_root, "acc.log", [ACCESS_LINE])
        _write_log(log_root, "sys.log", [SYSLOG_LINE])
        host._ingest_log({"path": "acc.log"})
        host._ingest_log({"path": "sys.log"})
        out = host._analyze({"log_type": "syslog"})
        assert out["scanned"] == 1
        assert out["log_types"] == {"syslog": 1}


class TestHostSearch:
    def test_search_roundtrip(self, host, log_root):
        _write_log(log_root, "sys.log", [SYSLOG_LINE])
        host._ingest_log({"path": "sys.log"})
        out = host._search({"query": "failed login", "top_k": 5})
        assert out.get("ok") is True
        assert out["count"] >= 1
        assert "raw" in out["hits"][0]
        assert "score" in out["hits"][0]

    def test_search_requires_query(self, host):
        assert "error" in host._search({})

    def test_search_min_status_filter(self, host, log_root):
        _write_log(log_root, "acc.log", [ACCESS_LINE, ACCESS_LINE_NO_REF])
        host._ingest_log({"path": "acc.log"})
        out = host._search(
            {"query": "login", "min_status": 400, "score_threshold": 0.0},
        )
        assert out.get("ok") is True
        for h in out["hits"]:
            assert h["status"] >= 400


class TestHostStatsSourcesRemove:
    def test_stats_aggregation(self, host, log_root):
        _write_log(
            log_root, "acc.log",
            [ACCESS_LINE, ACCESS_LINE_NO_REF, _access(status=500)["raw"]],
        )
        host._ingest_log({"path": "acc.log"})
        out = host._stats({})
        assert out["total_points"] == 3
        assert out["by_log_type"] == {"access": 3}
        assert "203.0.113.42" in {r["src_ip"] for r in out["top_ips"]}
        assert out["by_status"]["500"] == 1
        assert out["time_range"] is not None

    def test_stats_source_filter(self, host, log_root):
        _write_log(log_root, "one.log", [SYSLOG_LINE])
        _write_log(log_root, "two.log", [SYSLOG_LINE_PAM])
        host._ingest_log({"path": "one.log"})
        host._ingest_log({"path": "two.log"})
        src_two = [
            s["source"] for s in host._store.distinct_sources()
            if s["source"].endswith("two.log")
        ][0]
        out = host._stats({"source": src_two})
        assert out["total_points"] == 1

    def test_list_sources_empty_then_populated(self, host, log_root):
        empty = host._list_sources({})
        assert empty["sources"] == []
        _write_log(log_root, "one.log", [SYSLOG_LINE])
        host._ingest_log({"path": "one.log"})
        out = host._list_sources({})
        assert out["count"] == 1
        assert out["sources"][0]["count"] == 1

    def test_remove_requires_source(self, host):
        assert "error" in host._remove({})

    def test_remove_deletes_points(self, host, log_root):
        _write_log(log_root, "one.log", [SYSLOG_LINE])
        host._ingest_log({"path": "one.log"})
        src = host._store.distinct_sources()[0]["source"]
        out = host._remove({"source": src})
        assert out.get("ok") is True
        assert out["removed"] == 1
        assert host._store.count_points() == 0


# ─── manager / stdio / prompt integration ────────────────────────


class TestManagerRegistration:
    def test_from_config_registers_log_tools(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(None, include_local_hosts=True)
        names = {t.name for t in mgr.list_tools()}
        for expected in (
            "ingest_log", "analyze_anomalies", "search_logs",
            "get_log_stats", "list_log_sources", "remove_logs",
        ):
            assert expected in names, f"{expected} not registered"
        assert mgr._log_local_host is not None

    def test_tool_dispatch_reaches_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(None, include_local_hosts=True)
        # call with no args → clean error envelope (not a crash); the
        # LocalTool convention returns the error dict as JSON text.
        result = mgr.call("ingest_log", {})
        assert "'path' is required" in result.text


class TestStdioServer:
    def test_tool_specs_match_host_surface(self):
        from src.mcp_server.log_analyzer_server import _build_tool_specs

        from src.mcp_client.host import LogAnalysisLocalHost

        spec_names = {s.name for s in _build_tool_specs()}
        host_names = {t.name for t in LogAnalysisLocalHost().list_tools()}
        assert spec_names == host_names

    def test_handlers_delegate_to_host(self):
        from src.mcp_server.log_analyzer_server import LogAnalyzerMCPServer

        s = LogAnalyzerMCPServer()
        s.host = MagicMock()
        s.host._list_sources.return_value = {"ok": True, "sources": []}
        out = s._do_list_sources({})
        import json as _json

        payload = _json.loads(out[0].text)
        assert payload == {"ok": True, "sources": []}
        s.host._list_sources.assert_called_once()

    def test_unknown_tool_returns_error(self):
        from src.mcp_server.log_analyzer_server import _err

        assert "Error" in _err("Unknown tool: nope")[0].text


# ─── system-prompt trigger block ─────────────────────────────────


@pytest.fixture
def stub_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent.agent import Agent

    a = Agent()
    a._provider = "stub"
    a._model = "stub-model"
    a._mcp_manager = None
    return a


class TestSystemPromptTriggerBlock:
    def test_block_present_when_tools_enabled(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Log anomaly analysis" in prompt, "trigger header missing"
        for tool in (
            "ingest_log", "analyze_anomalies", "search_logs",
            "get_log_stats", "list_log_sources", "remove_logs",
        ):
            assert tool in prompt, f"{tool} missing from trigger block"

    def test_trigger_keywords_zh_and_en(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        for kw in ("分析 access log", "系統日誌", "異常登入", "暴力破解"):
            assert kw in prompt, f"zh trigger {kw!r} missing"
        for kw in ("log anomaly", "brute force", "check the logs"):
            assert kw in prompt, f"en trigger {kw!r} missing"

    def test_flow_order_taught(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "list_log_sources`` first" in prompt or (
            "call ``list_log_sources`` first" in prompt
        )

    def test_remove_logs_marked_destructive(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "remove_logs" in prompt
        assert "DESTRUCTIVE" in prompt

    def test_no_raw_json_dumping(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "do NOT dump" in prompt

    def test_block_absent_in_legacy_mode(self, stub_agent):
        legacy = stub_agent._system_prompt(tools_enabled=False)
        assert "Log anomaly analysis" not in legacy
        assert "ingest_log" not in legacy

    def test_composes_with_response_language(self, stub_agent):
        prompt = stub_agent._system_prompt(
            tools_enabled=True, response_language="zh-TW",
        )
        assert "Log anomaly analysis" in prompt
        assert "LANGUAGE DIRECTIVE" in prompt

    def test_does_not_displace_other_blocks(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        # drawio + open-with + telegram + schedule blocks all intact
        assert "create_diagram" in prompt
        assert "open_content" in prompt
        assert "send_telegram_message" in prompt
        assert "add_scheduled_task" in prompt


# ─── compressed-archive ingest (v0.9.8.1+) ───────────────────────


def _make_zip(path, members: dict[str, str]) -> None:
    import zipfile as _zf

    with _zf.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


def _make_tar_gz(path, members: dict[str, str]) -> None:
    import io
    import tarfile as _tf
    import time as _t

    with _tf.open(path, "w:gz") as tf:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = _tf.TarInfo(name=name)
            info.size = len(data)
            info.mtime = int(_t.time())
            tf.addfile(info, io.BytesIO(data))


def _make_gz(path, content: str) -> None:
    import gzip as _gzip

    with _gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(content)


def _make_7z(path, members: dict[str, str]) -> None:
    """Stage member files next to the archive, then pack with py7zr
    (the 1.x API has no writestr)."""
    import py7zr

    staged: list[tuple[Path, str]] = []
    for i, (name, content) in enumerate(members.items()):
        staged_file = Path(f"{path}.staged_{i}")
        staged_file.write_text(content, encoding="utf-8")
        staged.append((staged_file, name))
    with py7zr.SevenZipFile(str(path), "w") as z:
        for staged_file, arcname in staged:
            z.write(str(staged_file), arcname=arcname)
    for staged_file, _ in staged:
        try:
            staged_file.unlink()
        except OSError:
            pass


class TestHostCompressedIngest:
    def test_zip_archive_ingest(self, host, log_root):
        lines = [
            _syslog(
                f"Failed password for root from 10.0.0.8 port {500 + i}",
            )["raw"]
            for i in range(6)
        ]
        arch = log_root / "logs.zip"
        _make_zip(arch, {"sys/auth.log": "\n".join(lines)})
        out = host._ingest_log({"path": "logs.zip"})
        assert out.get("ok") is True, out
        assert out["archive_format"] == "zip"
        assert out["total_ingested"] == 6
        f0 = out["files"][0]
        assert f0["member"] == "sys/auth.log"
        assert f0["log_type"] == "syslog"
        assert f0["source_id"].endswith("logs.zip!sys/auth.log")
        stored = {
            p["payload"]["source"] for p in host._store.points.values()
        }
        assert stored == {f0["source_id"]}

    def test_tar_gz_archive_ingest(self, host, log_root):
        lines = [_access(status=200, path=f"/p{i}")["raw"] for i in range(5)]
        arch = log_root / "logs.tar.gz"
        _make_tar_gz(arch, {"access.log": "\n".join(lines)})
        out = host._ingest_log({"path": "logs.tar.gz"})
        assert out.get("ok") is True, out
        assert out["archive_format"] == "tar.gz"
        assert out["total_ingested"] == 5
        assert out["files"][0]["log_type"] == "access"

    def test_gz_single_file_ingest(self, host, log_root):
        lines = [_access(status=404, path=f"/x{i}")["raw"] for i in range(4)]
        arch = log_root / "access.log.gz"
        _make_gz(arch, "\n".join(lines))
        out = host._ingest_log({"path": "access.log.gz"})
        assert out.get("ok") is True, out
        assert out["archive_format"] == "gz"
        assert out["total_ingested"] == 4
        # gunzip convention: extracted name drops .gz
        assert out["files"][0]["member"] == "access.log"

    def test_7z_archive_ingest(self, host, log_root):
        from src.archive_tools import sevenzip_available

        if not sevenzip_available():
            pytest.skip("py7zr not installed")
        lines = [_access(status=200, path=f"/s{i}")["raw"] for i in range(4)]
        arch = log_root / "logs.7z"
        _make_7z(arch, {"sys/messages": "\n".join(lines)})
        out = host._ingest_log({"path": "logs.7z"})
        assert out.get("ok") is True, out
        assert out["archive_format"] == "7z"
        assert out["total_ingested"] == 4

    def test_mixed_types_auto_detect_per_member(self, host, log_root):
        acc_lines = [_access(status=200, path=f"/a{i}")["raw"] for i in range(4)]
        sys_lines = [_syslog(f"ok {i}", proc="app")["raw"] for i in range(4)]
        arch = log_root / "mix.zip"
        _make_zip(arch, {
            "access.log": "\n".join(acc_lines),
            "sys.log": "\n".join(sys_lines),
        })
        out = host._ingest_log({"path": "mix.zip"})
        assert out.get("ok") is True
        assert out["log_types"] == {"access": 1, "syslog": 1}
        assert out["total_ingested"] == 8

    def test_non_log_member_skipped(self, host, log_root):
        lines = [_access(status=200, path="/p")["raw"]]
        arch = log_root / "mixed.zip"
        _make_zip(arch, {
            "access.log": "\n".join(lines),
            "notes.txt": "this is just a note, not a log line at all",
        })
        out = host._ingest_log({"path": "mixed.zip"})
        assert out.get("ok") is True
        assert out["total_ingested"] >= 1
        assert len(out["files_skipped"]) == 1
        assert out["files_skipped"][0]["reason"] == "no parsable log lines"

    def test_dangerous_member_never_ingested(self, host, log_root):
        lines = [_access(status=200, path="/p")["raw"]]
        arch = log_root / "withexe.zip"
        _make_zip(arch, {
            "access.log": "\n".join(lines),
            "evil.exe": "MZ binary junk",
        })
        out = host._ingest_log({"path": "withexe.zip"})
        assert out.get("ok") is True
        members_ingested = {f["member"] for f in out["files"]}
        assert "evil.exe" not in members_ingested

    def test_members_allowlist(self, host, log_root):
        acc = [_access(status=200, path=f"/a{i}")["raw"] for i in range(3)]
        sysl = [_syslog(f"msg {i}", proc="p")["raw"] for i in range(3)]
        arch = log_root / "two.zip"
        _make_zip(arch, {
            "one.log": "\n".join(acc),
            "two.log": "\n".join(sysl),
        })
        out = host._ingest_log({
            "path": "two.zip", "members": ["one.log"],
        })
        assert out.get("ok") is True
        assert out["total_ingested"] == 3
        assert [f["member"] for f in out["files"]] == ["one.log"]

    def test_members_allowlist_all_filtered(self, host, log_root):
        lines = [_access(status=200, path="/p")["raw"]]
        arch = log_root / "sel.zip"
        _make_zip(arch, {"one.log": "\n".join(lines)})
        out = host._ingest_log({
            "path": "sel.zip", "members": ["nope.log"],
        })
        assert "error" in out

    def test_reingest_archive_no_duplicates(self, host, log_root):
        lines = [_syslog(f"line {i}", proc="p")["raw"] for i in range(4)]
        arch = log_root / "again.zip"
        _make_zip(arch, {"s.log": "\n".join(lines)})
        host._ingest_log({"path": "again.zip"})
        host._ingest_log({"path": "again.zip"})
        assert len(host._store.points) == 4  # upsert, not append

    def test_max_lines_per_file_cap(self, host, log_root):
        lines = [_syslog(f"m {i}", proc="p")["raw"] for i in range(10)]
        arch = log_root / "cap.zip"
        _make_zip(arch, {"s.log": "\n".join(lines)})
        out = host._ingest_log({"path": "cap.zip", "max_lines": 4})
        assert out.get("ok") is True
        assert out["total_ingested"] == 4
        assert out["files"][0]["truncated"] is True

    def test_archive_outside_sandbox_rejected(self, host, tmp_path):
        outside = tmp_path.parent / "outside_for_log.zip"
        _make_zip(outside, {"s.log": "Sep 10 13:55:36 h p[1]: x"})
        out = host._ingest_log({"path": str(outside)})
        assert "error" in out
        assert "sandbox" in out["error"]

    def test_analyze_scopes_to_member_source(self, host, log_root):
        acc = [_access(status=200, path=f"/a{i}")["raw"] for i in range(4)]
        sysl = [
            _syslog(
                f"Failed password for root from 10.0.0.8 port {600 + i}",
            )["raw"]
            for i in range(7)
        ]
        arch = log_root / "dual.zip"
        _make_zip(arch, {
            "acc.log": "\n".join(acc),
            "sys.log": "\n".join(sysl),
        })
        out = host._ingest_log({"path": "dual.zip"})
        member_source = out["files"][0]["source_id"]
        res = host._analyze({"source": member_source})
        assert res.get("ok") is True
        assert res["scanned"] == 4
        # analyze's log_types counts ROWS (per point), not files
        assert res["log_types"] == {"access": 4}

    def test_still_plain_file_ingest(self, host, log_root):
        # regression: plain files keep the original single-file path
        lines = [_syslog("plain line one", proc="p")["raw"]]
        p = log_root / "plain.log"
        p.write_text("\n".join(lines), encoding="utf-8")
        out = host._ingest_log({"path": "plain.log"})
        assert out.get("ok") is True
        assert "source_archive" not in out
        assert out["source"].endswith("plain.log")
        assert out["ingested"] == 1