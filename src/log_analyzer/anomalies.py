"""Anomaly rule engine for access / syslog entries (v0.9.7+).

Pure functions — no network, no Qdrant. The caller (the in-process
``LogAnalysisLocalHost`` or the stdio MCP server) scrolls entries out
of Qdrant and feeds them here; every rule returns a finding dict::

    {
      "rule":      <stable rule id, e.g. "scan_404">,
      "severity":  "info" | "warning" | "critical",
      "title":     <human readable one-liner>,
      "evidence":  [ ... rule-specific rows (ip / path / count ...) ],
      "hint":      <what the LLM should tell the user>,
    }

Two entry shapes are understood — the flat payload dicts produced by
:mod:`src.log_analyzer.parser` (one per log line). Access-log rules
fire on ``log_type == "access"`` entries; syslog rules on
``"syslog"`` entries.

Semantic outlier detection lives here too: :func:`semantic_outliers`
takes a matrix of embedding vectors (already L2-normalised by the
Embedder) plus the matching payloads and flags the lines whose
nearest-neighbour cosine similarity falls below a threshold — i.e.
lines that don't look like anything else in the log.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

__all__ = [
    "analyze_access_entries",
    "analyze_syslog_entries",
    "semantic_outliers",
]

# ─── tunables (overridable via env at the host layer) ────────────
DEFAULT_OUTLIER_THRESHOLD = 0.35
DEFAULT_MAX_POINTS = 5000

# Per-IP request count considered "heavy" even without statistics.
BRUTE_FORCE_ABS = 100
# 401/403 hits from one IP before it's flagged.
AUTH_FAIL_IP_THRESHOLD = 5
# Distinct 404 paths from one IP before it's flagged as a scan.
SCAN_404_PATHS = 8
# Failed password attempts per IP / per user before flagged.
SSH_BRUTE_THRESHOLD = 5

# ─── access-log rule data ────────────────────────────────────────
_SUSPICIOUS_PATH_RE = re.compile(
    r"(\.\./|/etc/passwd|/etc/shadow|\.env\b|\.git\b|\.aws\b|"
    r"wp-admin|wp-login|phpmyadmin|xmlrpc\.php|"
    r"union[\s+%20]+select|information_schema|"
    r"<script|%3cscript|onerror=|"
    r"cmd\.exe|\$\{jndi:|/actuator\b|/manager/html|"
    r"exec\(|sleep\(|benchmark\(|load_file|"
    r"id_rsa|\.sql$|\.zip$)",
    re.IGNORECASE,
)

_SUSPICIOUS_UA_RE = re.compile(
    r"(sqlmap|nikto|nmap|hydra|masscan|zgrab|gobuster|dirbuster|"
    r"dirb\b|wpscan|acunetix|nessus|openvas|scrapy|libwww)",
    re.IGNORECASE,
)

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _finding(
    rule: str, severity: str, title: str,
    evidence: list[dict], hint: str,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "hint": hint,
    }


def analyze_access_entries(
    entries: list[dict[str, Any]], top_n: int = 10,
) -> list[dict[str, Any]]:
    """Rule engine over access-log entries. Returns findings list."""
    findings: list[dict[str, Any]] = []
    if not entries:
        return findings

    total = len(entries)
    status_counts: Counter[int] = Counter()
    ip_counts: Counter[str] = Counter()
    ip_404_paths: dict[str, set[str]] = defaultdict(set)
    ip_auth_fails: Counter[str] = Counter()
    suspicious_paths: Counter[str] = Counter()
    suspicious_ua: dict[str, set[str]] = defaultdict(set)
    error_samples: list[dict[str, Any]] = []
    path_hits: dict[str, list[dict]] = defaultdict(list)

    for e in entries:
        status = int(e.get("status") or 0)
        ip = e.get("src_ip") or ""
        path = e.get("path") or ""
        ua = e.get("user_agent") or ""
        status_counts[status] += 1
        if ip:
            ip_counts[ip] += 1
        if status == 404 and ip and path:
            ip_404_paths[ip].add(path)
        if status in (401, 403) and ip:
            ip_auth_fails[ip] += 1
        if path and _SUSPICIOUS_PATH_RE.search(path):
            suspicious_paths[path] += 1
            if len(path_hits[path]) < 3:
                path_hits[path].append({
                    "src_ip": ip, "ts": e.get("ts", ""),
                })
        if ua and _SUSPICIOUS_UA_RE.search(ua):
            suspicious_ua[ua].add(ip)
        if status >= 500 and len(error_samples) < top_n:
            error_samples.append({
                "src_ip": ip, "status": status, "path": path,
                "ts": e.get("ts", ""),
            })

    # 1. HTTP error rate.
    err4 = sum(c for s, c in status_counts.items() if 400 <= s < 500)
    err5 = sum(c for s, c in status_counts.items() if s >= 500)
    err_ratio = (err4 + err5) / total if total else 0.0
    if err4 + err5:
        sev = "critical" if err_ratio > 0.4 else (
            "warning" if err_ratio > 0.2 else "info"
        )
        findings.append(_finding(
            "http_error_rate", sev,
            f"HTTP 4xx/5xx 佔比 {err_ratio:.1%}（{err4} 個 4xx、"
            f"{err5} 個 5xx，共 {total} 行）",
            ([
                {"status": s, "count": c}
                for s, c in status_counts.most_common()
                if s >= 400
            ][:top_n]),
            "大量非 2xx 回應：5xx 代表伺服器端錯誤、集中 4xx 可能是"
            "掃描或服務異常，建議交叉比對時間軸與來源 IP。",
        ))

    # 2. Directory scanning (many distinct 404 paths from one IP).
    for ip, paths in sorted(
        ip_404_paths.items(), key=lambda kv: -len(kv[1]),
    ):
        if len(paths) >= SCAN_404_PATHS:
            findings.append(_finding(
                "scan_404", "warning",
                f"IP {ip} 對 {len(paths)} 個不存在的路徑發出 404"
                "（目錄掃描特徵）",
                [{"src_ip": ip, "distinct_404_paths": len(paths),
                  "sample_paths": sorted(paths)[:top_n]}],
                "典型的自動化目錄/漏洞掃描。建議封鎖該 IP 或加上 WAF"
                "規則，並確認是否有後續成功請求（200）。",
            ))
            break  # one representative finding per analysis run

    # 3. Brute-force / high-frequency IPs (statistical outliers).
    if len(ip_counts) >= 5:
        counts = sorted(ip_counts.values(), reverse=True)
        mean = sum(counts) / len(counts)
        var = sum((c - mean) ** 2 for c in counts) / len(counts)
        std = var ** 0.5
        cut = max(mean + 3 * std, BRUTE_FORCE_ABS)
        for ip, c in ip_counts.most_common(top_n):
            if c >= cut and c > counts[1] * 3:  # >3x the 2nd busiest
                findings.append(_finding(
                    "high_frequency_ip", "critical",
                    f"IP {ip} 發出 {c} 個請求（平均值 {mean:.1f}、"
                    f"第 2 高 {counts[1]}）",
                    [{"src_ip": ip, "requests": c,
                      "share_pct": round(100 * c / total, 1)}],
                    "單一 IP 流量遠高於其他來源：可能是爆力破解、CC "
                    "攻擊或爬蟲，建議檢視其請求路徑分布。",
                ))
                break
    # 4. Suspicious paths (injection / traversal / admin probing).
    if suspicious_paths:
        top = suspicious_paths.most_common(top_n)
        findings.append(_finding(
            "suspicious_path", "warning",
            f"偵測到 {sum(suspicious_paths.values())} 個疑似攻擊特徵"
            "的請求路徑（SQLi / 路徑穿越 / 後台探測）",
            ([
                {"path": p, "count": c, "samples": path_hits[p]}
                for p, c in top
            ]),
            "常見攻擊 payload（../、union select、<script>、wp-admin、"
            ".env 等）。確認目標系統是否被成功入侵：查對應 200 回應。",
        ))

    # 5. Scanner user-agents.
    if suspicious_ua:
        rows = [
            {"user_agent": ua[:80], "src_ips": sorted(ips)[:top_n]}
            for ua, ips in list(suspicious_ua.items())[:top_n]
        ]
        findings.append(_finding(
            "scanner_user_agent", "warning",
            f"偵測到掃描工具 User-Agent（{len(suspicious_ua)} 種）",
            rows,
            "sqlmap / nikto / nmap 等工具的主動掃描跡象；若非資安"
            "團隊的授權掃描，視為攻擊前兆。",
        ))

    # 6. Auth failures concentrated on one IP.
    for ip, c in ip_auth_fails.most_common(top_n):
        if c >= AUTH_FAIL_IP_THRESHOLD:
            findings.append(_finding(
                "auth_failure_ip", "critical",
                f"IP {ip} 收到 {c} 次 401/403（帳號密碼爆破特徵）",
                [{"src_ip": ip, "auth_failures": c}],
                "單一來源大量 401/403：憑證填充或爆力破解。建議啟用"
                "帳號鎖定 / IP 封鎖並檢查是否有成功登入。",
            ))
            break

    # 7. Server-side errors summary.
    if err5:
        findings.append(_finding(
            "server_errors", "warning",
            f"{err5} 個 5xx 回應（伺服器端錯誤）",
            error_samples,
            "5xx 集中出現代表應用程式異常；比對發生時間與部署紀錄。",
        ))

    findings.sort(
        key=lambda f: -_SEVERITY_ORDER.get(f["severity"], 0),
    )
    return findings
# ─── syslog rules ────────────────────────────────────────────────

_AUTH_FAIL_RE = re.compile(
    r"(failed password|invalid user|authentication failure|"
    r"authentication error|failed publickey|"
    r"pam_unix\(sshd:auth\): authentication failure)",
    re.IGNORECASE,
)

_SYSTEM_HEALTH_RE = re.compile(
    r"(out of memory|oom-killer|killed process|segfault|"
    r"general protection|kernel panic|i/o error|"
    r"failed to start|unit entered failed state)",
    re.IGNORECASE,
)

_PRIVILEGE_RE = re.compile(
    r"\b(sudo|su\[|session opened for user root|"
    r"user NOT in sudoers)\b",
    re.IGNORECASE,
)


def _AUTH_MATCHES(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries whose message matches the auth-failure regex."""
    return [
        e for e in entries
        if _AUTH_FAIL_RE.search(e.get("message") or "")
    ]


def analyze_syslog_entries(
    entries: list[dict[str, Any]], top_n: int = 10,
) -> list[dict[str, Any]]:
    """Rule engine over syslog entries. Returns findings list."""
    findings: list[dict[str, Any]] = []
    if not entries:
        return findings

    total = len(entries)
    level_counts: Counter[str] = Counter()
    auth_fail_samples: list[dict[str, Any]] = []
    ssh_fail_by_ip: Counter[str] = Counter()
    ssh_fail_by_user: Counter[str] = Counter()
    health_samples: list[dict[str, Any]] = []
    priv_samples: list[dict[str, Any]] = []
    msg_counter: Counter[str] = Counter()

    for e in entries:
        msg = e.get("message") or ""
        level_counts[e.get("level") or "info"] += 1
        norm = re.sub(r"\d+", "#", msg)[:120]
        msg_counter[norm] += 1

        if _AUTH_FAIL_RE.search(msg):
            m = re.search(
                r"failed password for (?:invalid user\s+)?(\S+)",
                msg, re.IGNORECASE,
            )
            user = m.group(1) if m else "?"
            m_ip = re.search(r"from (\S+)", msg)
            ip = m_ip.group(1) if m_ip else ""
            if m_ip:
                ssh_fail_by_ip[m_ip.group(1)] += 1
            if user != "?":
                ssh_fail_by_user[user] += 1
            if len(auth_fail_samples) < top_n:
                auth_fail_samples.append({
                    "user": user,
                    "src_ip": ip,
                    "ts": e.get("ts", ""),
                    "message": msg[:160],
                })

        if _SYSTEM_HEALTH_RE.search(msg) and len(health_samples) < top_n:
            health_samples.append({
                "proc": e.get("proc", ""),
                "ts": e.get("ts", ""),
                "message": msg[:160],
            })

        if _PRIVILEGE_RE.search(msg) and len(priv_samples) < top_n:
            priv_samples.append({
                "proc": e.get("proc", ""),
                "ts": e.get("ts", ""),
                "message": msg[:160],
            })

    # 1. Severity breakdown.
    n_err = level_counts.get("error", 0) + level_counts.get("critical", 0)
    if n_err:
        sev = "critical" if level_counts.get("critical") else "warning"
        findings.append(_finding(
            "severity_breakdown", sev,
            f"{n_err} / {total} 行為 error / critical 級別",
            [
                {"level": lv, "count": c}
                for lv, c in level_counts.most_common()
            ],
            "系統日誌出現錯誤級事件；以下規則會進一步定位根因。",
        ))

    # 2. SSH brute force by source IP.
    for ip, c in ssh_fail_by_ip.most_common(top_n):
        if c >= SSH_BRUTE_THRESHOLD:
            findings.append(_finding(
                "ssh_brute_force", "critical",
                f"SSH 登入失敗 {c} 次來自 {ip}（爆力破解特徵）",
                [{
                    "src_ip": ip,
                    "failed_attempts": c,
                    "top_users": [
                        {"user": u, "count": cc}
                        for u, cc in ssh_fail_by_user.most_common(top_n)
                    ],
                }],
                "單一來源連續 SSH 密碼錯誤：建議改用金鑰認證、fail2ban "
                "或防火牆封鎖，並確認該 IP 是否曾成功登入。",
            ))
            break

    # 3. Auth failures overall.
    if auth_fail_samples:
        findings.append(_finding(
            "auth_failures", "warning",
            f"共 {len(_AUTH_MATCHES(entries))} 次認證失敗訊息",
            auth_fail_samples,
            "包含 SSH / PAM 認證失敗。比對來源 IP 與使用者名稱是否為"
            "內部已知行為。",
        ))

    # 4. System health (OOM / segfault / kernel).
    if health_samples:
        findings.append(_finding(
            "system_health", "critical",
            f"偵測到 {len(health_samples)} 條系統健康事件"
            "（OOM / segfault / kernel panic / 服務失敗）",
            health_samples,
            "記憶體不足或程式崩潰會直接造成服務中斷；建議檢查資源"
            "水位與 core dump。",
        ))

    # 5. Privilege escalation activity.
    if priv_samples:
        findings.append(_finding(
            "privilege_activity", "info",
            f"{len(priv_samples)} 條 sudo / su 權限切換紀錄",
            priv_samples,
            "確認這些提權操作皆為預期行為（維護窗口、自動化作業）。",
        ))

    # 6. Log flood — same message repeated abnormally often.
    if len(msg_counter) >= 3 and total >= 10:
        counts = sorted(msg_counter.values(), reverse=True)
        mean = sum(counts) / len(counts)
        top_msg, top_c = msg_counter.most_common(1)[0]
        if top_c >= max(mean * 10, 20):
            findings.append(_finding(
                "log_flood", "warning",
                f"單一訊息模式重複 {top_c} 次（平均值 {mean:.1f}）",
                [{"normalized_message": top_msg, "count": top_c}],
                "同一訊息異常大量重複：迴圈錯誤、重試風暴或攻擊流量，"
                "建議找出訊息來源 proc。",
            ))

    findings.sort(
        key=lambda f: -_SEVERITY_ORDER.get(f["severity"], 0),
    )
    return findings
# ─── semantic outliers ───────────────────────────────────────────

def semantic_outliers(
    vectors: list[list[float]],
    payloads: list[dict[str, Any]],
    threshold: float = DEFAULT_OUTLIER_THRESHOLD,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Flag the semantically isolated lines via cosine kNN.

    For every point, compute its best cosine similarity to any OTHER
    point. If even the best match sits below ``threshold``, the line
    doesn't resemble anything else in the log — a semantic outlier.
    Results are sorted ascending by best-similarity (most isolated
    first) and capped at ``top_n``.

    Chunked matrix multiplication bounds peak memory at
    ``chunk_size × N`` floats, so a 5000 × 4096 scan stays well
    under a few hundred MB.
    """
    import numpy as np

    n = len(vectors)
    if n < 3 or len(payloads) != n:
        return []
    mat = np.asarray(vectors, dtype=np.float32)
    # Re-normalise defensively (Embedder already L2-normalises, but
    # a caller might pass raw vectors).
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms

    best_sim = np.zeros(n, dtype=np.float32)
    chunk = 512
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        block = mat[start:end]                 # (c, D)
        sims = block @ mat.T                   # (c, N)
        # Exclude self-similarity by setting the diagonal positions
        # of this chunk to a very low value before the row max.
        rows = np.arange(start, end)
        sims[np.arange(end - start), rows] = -2.0
        best_sim[start:end] = sims.max(axis=1)

    order = np.argsort(best_sim)  # ascending = most isolated first
    out: list[dict[str, Any]] = []
    for idx in order:
        sim = float(best_sim[idx])
        if sim >= threshold:
            break
        p = payloads[int(idx)]
        out.append({
            "best_similarity": round(sim, 4),
            "threshold": threshold,
            "raw": (p.get("raw") or p.get("text") or "")[:300],
            "src_ip": p.get("src_ip", ""),
            "ts": p.get("ts", ""),
            "level": p.get("level", ""),
            "status": p.get("status", 0),
            "line_no": p.get("line_no", 0),
        })
        if len(out) >= top_n:
            break
    return out