"""Tests for the v0.9.40.0 network scanner (NetScanLocalHost +
src.net_scan_tools + stdio mirror). Live sockets bind 127.0.0.1:0
only — nothing leaves the machine."""
import socket
import threading

import pytest

import src.net_scan_tools as nst
from src.net_scan_tools import (
    NetScanError, is_public_target, local_interfaces, parse_port_spec,
    parse_targets, scan_host_ports, service_name, tcp_probe,
)


@pytest.fixture()
def fake_service():
    """A local TCP service that greets with a banner."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    stop = threading.Event()

    import sys as _sys

    def acceptor():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.sendall(b"FAKE-BANNER-1.0\r\n")
                conn.close()
            except OSError:
                if _sys.platform == "win32":
                    continue
                break

    t = threading.Thread(target=acceptor, daemon=True)
    t.start()
    yield port
    stop.set()
    srv.close()


class TestParseTargets:
    def test_cidr_and_range_and_single(self):
        assert parse_targets("192.168.1.10-13") == [
            "192.168.1.10", "192.168.1.11",
            "192.168.1.12", "192.168.1.13",
        ]
        assert parse_targets("192.168.1.16/30") == [
            "192.168.1.17", "192.168.1.18",
        ]
        assert parse_targets("192.168.1.10-192.168.1.11") == [
            "192.168.1.10", "192.168.1.11",
        ]
        assert parse_targets(" 192.168.1.7 ") == ["192.168.1.7"]

    def test_junk_and_reverse_raise(self):
        for bad in ("", "not-an-ip", "192.168.1.5-192.168.1.2"):
            try:
                parse_targets(bad)
            except NetScanError:
                pass
            else:
                raise AssertionError(f"expected NetScanError: {bad!r}")

    def test_budget_cap(self, monkeypatch):
        monkeypatch.setenv("QDRANT_NETSCAN_MAX_HOSTS", "4")
        try:
            parse_targets("192.168.1.10-20")
        except NetScanError as e:
            assert "MAX_HOSTS" in str(e)
        else:
            raise AssertionError("expected budget error")


class TestParsePortSpec:
    def test_mixed_and_list(self):
        assert parse_port_spec("80,443,8000-8002") == [
            80, 443, 8000, 8001, 8002,
        ]
        assert parse_port_spec([22, 80]) == [22, 80]

    def test_default_probe_list(self):
        assert 6333 in parse_port_spec(None)
        assert 554 in parse_port_spec(None)  # rtsp

    def test_invalid(self):
        for bad in ("0", "70000", "10-5", "abc"):
            try:
                parse_port_spec(bad)
            except NetScanError:
                pass
            else:
                raise AssertionError(f"expected NetScanError: {bad}")


class TestPublicGuard:
    def test_classification(self):
        assert is_public_target("8.8.8.8") is True
        assert is_public_target("192.168.1.5") is False
        assert is_public_target("127.0.0.1") is False
        assert is_public_target("10.0.0.1") is False
        assert is_public_target("169.254.1.1") is False

    def test_scan_hosts_refuses_public(self, monkeypatch):
        monkeypatch.delenv("QDRANT_NETSCAN_ALLOW_PUBLIC", raising=False)
        try:
            nst.scan_hosts("8.8.8.8", ports="80")
        except NetScanError as e:
            assert "ALLOW_PUBLIC" in str(e)
        else:
            raise AssertionError("public scan must be refused")


class TestProbe:
    def test_open_and_banner(self, fake_service):
        res = tcp_probe("127.0.0.1", fake_service, 0.5,
                        grab_banner=True)
        assert res["state"] == "open"
        assert "FAKE-BANNER" in res["banner"]

    def test_closed(self):
        assert tcp_probe("127.0.0.1", 1, 0.2)["state"] == "closed"

    def test_sweep_finds_listener(self, fake_service):
        results = scan_host_ports("127.0.0.1", [1, fake_service], 0.5)
        states = {r["port"]: r["state"] for r in results}
        assert states[fake_service] == "open"
        assert states[1] == "closed"

    def test_service_name_known_ports(self):
        assert service_name(554) == "rtsp"
        assert service_name(6333) == "qdrant"
        assert service_name(3389) == "rdp"


class TestScanHosts:
    def test_tcp_method_alive_detection(self, fake_service):
        out = nst.scan_hosts("127.0.0.1", method="tcp",
                             ports=str(fake_service), timeout=0.5)
        assert out["alive_count"] == 1
        assert out["alive"][0]["open_ports"] == [fake_service]
        assert out["down_count"] == 0

    def test_ping_method_localhost(self):
        out = nst.scan_hosts("127.0.0.1", method="ping", timeout=1.0)
        assert out["alive_count"] == 1


class TestLocalInterfaces:
    def test_non_loopback_strings(self):
        ips = local_interfaces()
        assert all("." in ip for ip in ips)
        assert all(not ip.startswith("127.") for ip in ips)


class TestNetScanLocalHost:
    def _host(self):
        from src.mcp_client.host import NetScanLocalHost

        return NetScanLocalHost()

    def test_three_tools_advertised(self):
        names = [t.name for t in self._host().list_tools()]
        assert names == [
            "net_scan_hosts", "net_scan_ports", "net_my_interfaces",
        ]

    def test_scan_hosts_handler_live(self, fake_service):
        out = self._host()._scan_hosts({
            "targets": "127.0.0.1", "method": "tcp",
            "ports": str(fake_service), "timeout": 0.5,
        })
        assert out.get("ok") is True, out
        assert out["alive_count"] == 1

    def test_scan_hosts_refuses_public(self, monkeypatch):
        monkeypatch.delenv("QDRANT_NETSCAN_ALLOW_PUBLIC", raising=False)
        out = self._host()._scan_hosts({"targets": "8.8.8.8",
                                        "ports": "80"})
        assert "error" in out and "ALLOW_PUBLIC" in out["error"]

    def test_scan_ports_handler_live(self, fake_service):
        out = self._host()._scan_ports({
            "host": "127.0.0.1", "ports": f"1,{fake_service}",
            "timeout": 0.5, "grab_banner": True,
        })
        assert out.get("ok") is True, out
        assert out["open_count"] == 1
        assert "FAKE-BANNER" in out["open"][0]["banner"]

    def test_scan_ports_hostname_resolution(self):
        out = self._host()._scan_ports({"host": "localhost",
                                        "ports": "1"})
        assert out.get("ok") is True, out
        assert out["host"] == "127.0.0.1"

    def test_my_interfaces_handler(self):
        out = self._host()._my_interfaces({})
        assert out.get("ok") is True
        assert isinstance(out["interfaces"], list)
        assert all("/24" in s for s in out["suggested_scan"])


class TestRegistrationPromptStdio:
    def test_manager_registers_netscan(self):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        names = {t.name for t in mgr.list_tools()}
        assert "net_scan_hosts" in names
        assert "net_scan_ports" in names
        assert mgr._net_scan_local_host is not None
        assert mgr._net_scan_local_host.SERVER_NAME == "netscan-local"
        mgr.close_all()

    def test_prompt_mentions_scanner(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        a._mcp_manager = None
        prompt = a._system_prompt(tools_enabled=True)
        assert "net_scan_hosts" in prompt
        assert "QDRANT_NETSCAN_ALLOW_PUBLIC" in prompt

    def test_stdio_specs_match_host(self):
        from src.mcp_client.host import NetScanLocalHost
        from src.mcp_server.net_scan_server import _TOOL_SPECS

        host_names = [t.name for t in NetScanLocalHost().list_tools()]
        assert [s.name for s in _TOOL_SPECS] == host_names