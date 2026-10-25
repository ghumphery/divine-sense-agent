# -*- coding: utf-8 -*-
"""Tests: firefox_craw middle-service auto-heal (v0.9.109.4).

Api._ensure_firefox_service probes http://127.0.0.1:<PORT>/health and,
when refused AND firefox_craw is in the active MCP surface, spawns
mcp_getweb/run_service.bat DETACHED and waits. Everything mocked.
"""
import os
import subprocess
import unittest
import urllib.error
import urllib.request
from unittest import mock

import gui.api as gam


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_REFUSED = urllib.error.URLError("[WinError 10061] refused")


def _ok(url, timeout=None):
    return _Resp()


def _ref(url, timeout=None):
    raise _REFUSED


def _api():
    return gam.Api.__new__(gam.Api)


def _env(**kw):
    base = {
        "MCP_SERVICE_PORT": "",
        "MCP_ACTIVE_SERVERS": "qdrant-local,web-local,firefox_craw",
        "QDRANT_SPAWN_FIREFOX_SERVICE": "",
        "QDRANT_FIREFOX_SERVICE_START_BAT": "",
    }
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=False)


def _down_then_ok(calls):
    def _u(url, timeout=None):
        calls["n"] += 1
        if calls["n"] >= 2:
            return _Resp()
        raise _REFUSED
    return _u
class TestFirefoxServiceAutoHeal(unittest.TestCase):
    def test_healthy_service_is_noop(self):
        with _env(), mock.patch.object(
                urllib.request, "urlopen", side_effect=_ok), \
             mock.patch.object(gam.time, "sleep") as m_sleep, \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_firefox_service(_api())
        popen.assert_not_called()
        m_sleep.assert_not_called()

    def test_down_spawns_launcher_detached_and_waits_ready(self):
        with _env(), \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=_down_then_ok({"n": 0})), \
             mock.patch.object(gam.time, "sleep"), \
             mock.patch.object(gam.time,
                               "time", side_effect=lambda: 100.0), \
             mock.patch.object(gam.os.path, "isfile", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_firefox_service(_api())
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0][:2], ["cmd", "/c"])
        self.assertIn("run_service.bat", args[0][2])
        self.assertEqual(kwargs.get("cwd"),
                         r"C:\Users\humphery\coding\mcp_getweb")
        self.assertTrue(kwargs.get("creationflags", 0) & 0x00000008)
        self.assertIn(kwargs.get("stdin"),
                      (subprocess.DEVNULL, subprocess.PIPE, None))

    def test_gate_without_firefox_craw_skips(self):
        with _env(MCP_ACTIVE_SERVERS="qdrant-local,web-local"), \
             mock.patch.object(urllib.request, "urlopen") as uo, \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_firefox_service(_api())
        uo.assert_not_called()
        popen.assert_not_called()

    def test_unset_gate_full_surface_still_heals(self):
        with _env(MCP_ACTIVE_SERVERS=""), \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=_down_then_ok({"n": 0})), \
             mock.patch.object(gam.time, "sleep"), \
             mock.patch.object(gam.time,
                               "time", side_effect=lambda: 100.0), \
             mock.patch.object(gam.os.path, "isfile", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_firefox_service(_api())
        popen.assert_called_once()

    def test_optout_env_skips_probe_and_spawn(self):
        with _env(QDRANT_SPAWN_FIREFOX_SERVICE="off"), \
             mock.patch.object(urllib.request, "urlopen") as uo, \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_firefox_service(_api())
        uo.assert_not_called()
        popen.assert_not_called()

    def test_missing_launcher_warns_no_spawn(self):
        with _env(), mock.patch.object(urllib.request, "urlopen",
                                       side_effect=_ref), \
             mock.patch.object(gam.os.path, "isfile", return_value=False), \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_firefox_service(_api())
        popen.assert_not_called()

    def test_spawn_failure_never_raises(self):
        with _env(), mock.patch.object(urllib.request, "urlopen",
                                       side_effect=_ref), \
             mock.patch.object(gam.os.path, "isfile", return_value=True), \
             mock.patch("subprocess.Popen", side_effect=OSError("x")):
            gam.Api._ensure_firefox_service(_api())

    def test_gives_up_after_wait_window(self):
        with _env(), mock.patch.object(urllib.request, "urlopen",
                                       side_effect=_ref), \
             mock.patch.object(gam.time, "sleep"), \
             mock.patch.object(gam.time, "time",
                               side_effect=[0.0, 10**9]), \
             mock.patch.object(gam.os.path, "isfile", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_firefox_service(_api())
        popen.assert_called_once()

    def test_custom_port_and_bat_env_are_honoured(self):
        urls = []

        def _u(url, timeout=None):
            urls.append(url)
            if len(urls) >= 2:
                return _Resp()
            raise _REFUSED

        with _env(MCP_SERVICE_PORT="9999",
                  QDRANT_FIREFOX_SERVICE_START_BAT=r"D:\svc\go.bat"), \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=_u), \
             mock.patch.object(gam.time, "sleep"), \
             mock.patch.object(gam.time,
                               "time", side_effect=lambda: 100.0), \
             mock.patch.object(gam.os.path, "isfile", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_firefox_service(_api())
        self.assertTrue(any("127.0.0.1:9999/health" in u for u in urls))
        self.assertIn("go.bat", popen.call_args[0][0][2])

    def test_prewarm_heals_qdrant_then_firefox_before_agent(self):
        src = open(gam.__file__, encoding="utf-8").read()
        i = src.index("def _prewarm_agent")
        seg = src[i:i + 3000]
        self.assertIn("_ensure_qdrant_server()", seg)
        self.assertIn("_ensure_firefox_service()", seg)
        self.assertLess(seg.index("_ensure_qdrant_server()"),
                        seg.index("_get_agent()"))
        self.assertLess(seg.index("_ensure_qdrant_server()"),
                        seg.index("_ensure_firefox_service()"))


if __name__ == "__main__":
    unittest.main()