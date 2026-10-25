"""v0.9.40.0+ — stdlib-only IP scanner + port scanner core.

Pure-logic module consumed by ``NetScanLocalHost`` (server
``netscan-local``) and the stdio mirror
``src.mcp_server.net_scan_server``. Zero new dependencies —
socket / ipaddress / concurrent.futures / subprocess only.

Tools
-----
* ``scan_hosts(spec, ...)``  — IP scanner: enumerate targets from a
  CIDR / single IP / range (``192.168.1.10-50``), then probe each
  host. Two methods:
  - ``tcp`` (default): host is ALIVE when ANY probe port accepts a
    TCP connection (no admin rights, no subprocess, firewall-friendlier
    than ICMP on Windows).
  - ``ping``: system ``ping`` subprocess (ICMP echo) — works even when
    every TCP port is filtered, needs ping.exe in PATH.
* ``scan_ports(host, ...)`` — port scanner for ONE host: TCP connect
  sweep over an explicit list / range, with service-name lookup and
  optional banner grab (recv-only, tiny timeout).
* ``local_interfaces()``    — this machine's IPv4 addresses (helps the
  LLM pick the right subnet before scanning).

安全模型（mirror exec/archive blocklist philosophy）
--------------------------------------------------
* **預設只允許私網目標** — loopback / RFC1918 / link-local / CGNAT
  (``ipaddress ... .is_private``)。公網 IP 一律拒絕並提示
  ``QDRANT_NETSCAN_ALLOW_PUBLIC=on``；對外掃描有濫用與法務風險，
  必須由使用者明確開啟。
* 規模上限：``QDRANT_NETSCAN_MAX_HOSTS``（4096）/
  ``QDRANT_NETSCAN_MAX_PORTS``（1024）— 10.0.0.0/8 這類巨網直接拒絕。
* 並行上限 ``QDRANT_NETSCAN_MAX_WORKERS``（128）；probe timeout 上限 5s。
* 本模組只做 connect/recv 探測 — 不送 payload、不做漏洞利用。
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import platform
import re
import socket
import subprocess
from typing import Any

__all__ = [
    "NetScanError", "parse_targets", "is_public_target",
    "parse_port_spec", "service_name", "tcp_probe", "scan_host_ports",
    "ping_host", "scan_hosts", "local_interfaces",
]


class NetScanError(ValueError):
    """Raised for invalid targets / refused scans / budget breaches."""


# Well-known service labels for ports the LLM will actually meet on a
# LAN (socket.getservbyport misses qdrant / agent / camera ports).
_SERVICE_NAMES: dict[int, str] = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns",
    80: "http", 111: "rpcbind", 135: "msrpc", 139: "netbios-ssn",
    443: "https", 445: "smb", 554: "rtsp", 631: "ipp",
    993: "imaps", 995: "pop3s", 1433: "mssql", 1883: "mqtt",
    2020: "onvif-alt", 3000: "node-dev", 3306: "mysql", 3389: "rdp",
    34567: "hikvision", 37777: "dahua", 5000: "upnp/http",
    5432: "postgres", 5555: "adb", 5900: "vnc", 6333: "qdrant",
    6334: "qdrant-grpc", 8000: "http-alt", 8006: "onvif-http",
    8009: "cast", 8080: "http-proxy", 8081: "http-alt2",
    8123: "home-assistant", 8334: "divine-sense-agent-web", 8335: "webhook",
    8899: "onvif-media", 9000: "portainer/http", 9100: "jetdirect",
    27017: "mongodb", 49152: "upnp-ep",
}

DEFAULT_PROBE_PORTS = [80, 443, 22, 445, 554, 3389, 5000, 6333,
                       8000, 8080, 8334, 8899, 34567, 37777]

_RANGE_RE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*"
    r"(?:(\d{1,3}(?:\.\d{1,3}){3})|(\d{1,3}))\s*$",
)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default


def _cap_workers() -> int:
    return max(1, min(128, _env_int("QDRANT_NETSCAN_MAX_WORKERS", 128)))


def _cap_timeout(timeout: float) -> float:
    return max(0.05, min(5.0, timeout))


def _public_allowed() -> bool:
    return (os.environ.get("QDRANT_NETSCAN_ALLOW_PUBLIC") or ""
            ).strip().lower() in {"1", "on", "true", "yes"}


def is_public_target(ip: str) -> bool:
    """True when the address is a GLOBAL (routable internet) target.

    Loopback / private / link-local / CGNAT addresses are NOT public.
    Unparsable values are treated as public (fail-closed refusal).
    """
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return True
    if addr.version == 4:
        return addr.is_global and not addr.is_private
    return addr.is_global


def parse_targets(spec: str) -> list[str]:
    """Enumerate IPv4 targets from CIDR / single IP / range.

    Accepted shapes:
      - ``192.168.1.0/30``            (ip_network, strict=False)
      - ``192.168.1.10``              (single)
      - ``192.168.1.10-192.168.1.13`` (full range)
      - ``192.168.1.10-13``           (last-octet shorthand)
    Ordered, de-duplicated. Raises NetScanError over budget or junk.
    """
    raw = str(spec or "").strip()
    if not raw:
        raise NetScanError("target spec is empty")
    max_hosts = max(1, _env_int("QDRANT_NETSCAN_MAX_HOSTS", 4096))

    if "/" in raw:
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError as e:
            raise NetScanError(f"invalid CIDR {raw!r}: {e}") from e
        if net.version != 4:
            raise NetScanError(
                f"{raw!r}: only IPv4 ranges are supported "
                "(IPv6 hosts can be scanned individually)",
            )
        ips = ([str(h) for h in net.hosts()]
               or [str(net.network_address)])
    else:
        m = _RANGE_RE.match(raw)
        if m:
            start_s, end_full, end_short = (
                m.group(1), m.group(2), m.group(3),
            )
            start = ipaddress.IPv4Address(start_s)
            if end_full:
                end = ipaddress.IPv4Address(end_full)
            else:
                end = ipaddress.IPv4Address(
                    ".".join(start_s.split(".")[:3]) + "." + end_short,
                )
            if int(end) < int(start):
                raise NetScanError(
                    f"range end {end} is before start {start}",
                )
            span = int(end) - int(start) + 1
            if span > max_hosts:
                raise NetScanError(
                    f"range spans {span} hosts > "
                    f"QDRANT_NETSCAN_MAX_HOSTS={max_hosts}",
                )
            ips = [str(ipaddress.IPv4Address(int(start) + i))
                   for i in range(span)]
        else:
            try:
                ipaddress.ip_address(raw)
            except ValueError as e:
                raise NetScanError(
                    f"invalid target {raw!r} — use CIDR, a single IP, "
                    "or a start-end range",
                ) from e
            ips = [raw]
    if len(ips) > max_hosts:
        raise NetScanError(
            f"{len(ips)} targets > QDRANT_NETSCAN_MAX_HOSTS={max_hosts}"
            " — narrow the range",
        )
    seen: set[str] = set()
    return [ip for ip in ips if not (ip in seen or seen.add(ip))]


def parse_port_spec(spec: str | list[int] | None) -> list[int]:
    """Parse ``"80,443,8000-8002"`` / list into an ordered port list."""
    if spec is None or str(spec).strip() == "":
        return list(DEFAULT_PROBE_PORTS)
    if isinstance(spec, (list, tuple)):
        parts: list[str] = [str(p) for p in spec]
    else:
        parts = str(spec).replace(" ", "").split(",")
    max_ports = max(1, _env_int("QDRANT_NETSCAN_MAX_PORTS", 1024))
    ports: list[int] = []
    seen: set[int] = set()
    for part in parts:
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError as e:
                raise NetScanError(f"invalid port range {part!r}") from e
            if not (1 <= lo <= 65535 and 1 <= hi <= 65535):
                raise NetScanError(f"port range {part!r} out of 1-65535")
            if hi < lo:
                raise NetScanError(f"port range {part!r} reversed")
            if len(ports) + (hi - lo + 1) > max_ports:
                raise NetScanError(
                    f"port spec exceeds QDRANT_NETSCAN_MAX_PORTS="
                    f"{max_ports}",
                )
            for p in range(lo, hi + 1):
                if p not in seen:
                    seen.add(p)
                    ports.append(p)
        else:
            try:
                p = int(part)
            except ValueError as e:
                raise NetScanError(f"invalid port {part!r}") from e
            if not (1 <= p <= 65535):
                raise NetScanError(f"port {p} out of 1-65535")
            if p not in seen:
                seen.add(p)
                ports.append(p)
    if not ports:
        raise NetScanError("port spec resolved to an empty list")
    return ports


def service_name(port: int) -> str:
    """Best-effort service label for a port (local map → getservbyport)."""
    known = _SERVICE_NAMES.get(int(port))
    if known:
        return known
    try:
        return socket.getservbyport(int(port)) or "unknown"
    except OSError:
        return "unknown"


def tcp_probe(ip: str, port: int, timeout: float = 1.0,
              grab_banner: bool = False) -> dict[str, Any]:
    """One TCP-connect probe. Never raises — errors become closed."""
    out: dict[str, Any] = {
        "port": int(port), "state": "closed",
        "service": service_name(port), "banner": "",
    }
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(max(0.05, timeout))
            rc = sock.connect_ex((ip, int(port)))
            if rc != 0:
                return out
            out["state"] = "open"
            if grab_banner:
                try:
                    sock.settimeout(0.6)
                    data = sock.recv(256)
                    out["banner"] = "".join(
                        chr(b) if 32 <= b < 127 or b in (9, 10, 13)
                        else "."
                        for b in data[:256]
                    ).strip()
                except (OSError, socket.timeout):
                    pass
    except (OSError, socket.timeout):
        # unreachable host / local firewall drop — report closed
        out["state"] = "closed"
    return out


def scan_host_ports(ip: str, ports: list[int], timeout: float = 1.0,
                    grab_banner: bool = False,
                    max_workers: int | None = None) -> list[dict[str, Any]]:
    """Parallel TCP-connect sweep over ONE host; sorted by port."""
    workers = max_workers or _cap_workers()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(ports), workers) or 1,
    ) as pool:
        results = list(pool.map(
            lambda p: tcp_probe(ip, p, timeout, grab_banner), ports,
        ))
    results.sort(key=lambda r: r["port"])
    return results


def ping_host(ip: str, timeout: float = 1.0) -> bool:
    """ICMP echo via the system ping binary (no admin on Windows)."""
    is_win = platform.system().lower().startswith("win")
    if is_win:
        cmd = ["ping", "-n", "1", "-w",
               str(max(100, int(timeout * 1000))), ip]
    else:
        cmd = ["ping", "-c", "1", "-W",
               str(max(1, int(timeout))), ip]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout + 3.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode == 0:
        return True
    # Some Windows locales return 0 with "unreachable" text; require
    # an affirmative TTL/time token to call it alive.
    blob = (proc.stdout or b"").decode("utf-8", "ignore").lower()
    return ("ttl=" in blob or "time=" in blob) \
        and "unreachable" not in blob


def scan_hosts(spec: str, method: str = "tcp", ports=None,
               timeout: float = 1.0, resolve_names: bool = False,
               ) -> dict[str, Any]:
    """IP scanner over a CIDR / range / single host.

    Returns {targets_scanned, method, probe_ports, alive: [{ip,
    hostname?, open_ports}], alive_count, down_count}. For ``tcp``, a
    host counts alive when at least one probe port connects.
    """
    targets = parse_targets(spec)
    timeout = _cap_timeout(_env_float("QDRANT_NETSCAN_TIMEOUT", timeout))
    method = (method or "tcp").strip().lower()
    if method not in {"tcp", "ping"}:
        raise NetScanError(
            f"unknown method {method!r} — use 'tcp' or 'ping'",
        )
    probe_ports = parse_port_spec(ports) if method == "tcp" else []
    public = [ip for ip in targets if is_public_target(ip)]
    if public and not _public_allowed():
        raise NetScanError(
            "public/internet targets refused (e.g. "
            f"{public[0]}) — set QDRANT_NETSCAN_ALLOW_PUBLIC=on "
            "to scan addresses you own",
        )
    workers = _cap_workers()

    def probe_one(ip: str) -> dict[str, Any]:
        if method == "ping":
            entry: dict[str, Any] = {
                "ip": ip, "alive": ping_host(ip, timeout),
                "open_ports": [],
            }
        else:
            results = scan_host_ports(ip, probe_ports, timeout,
                                      grab_banner=False,
                                      max_workers=workers)
            open_ports = [r["port"] for r in results
                          if r["state"] == "open"]
            entry = {"ip": ip, "alive": bool(open_ports),
                     "open_ports": open_ports}
        if entry["alive"] and resolve_names:
            try:
                entry["hostname"] = socket.gethostbyaddr(ip)[0]
            except (OSError, socket.herror):
                pass
        return entry

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(targets), workers) or 1,
    ) as pool:
        entries = list(pool.map(probe_one, targets))
    alive = [e for e in entries if e["alive"]]
    return {
        "targets_scanned": len(targets),
        "method": method,
        "probe_ports": probe_ports if method == "tcp" else [],
        "alive": alive,
        "alive_count": len(alive),
        "down_count": len(entries) - len(alive),
    }


def local_interfaces() -> list[str]:
    """This machine's non-loopback IPv4 addresses (dedup'd)."""
    found: list[str] = []
    # UDP-connect trick: no packet is sent, just picks a local route.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out: list[str] = []
    seen: set[str] = set()
    for ip in found:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_loopback or str(addr) in seen:
            continue
        seen.add(str(addr))
        out.append(str(addr))
    return out