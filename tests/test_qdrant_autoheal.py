# -*- coding: utf-8 -*-
"""Tests: Qdrant boot auto-heal (v0.9.109.3).

`gui.api.Api._ensure_qdrant_server` probes `<QDRANT_URL>/readyz` and,
when refused AND the URL is a loopback address, spawns the Qdrant
start bat DETACHED and waits for readiness. All tests mock
`urllib.request.urlopen` / `time` / `subprocess.Popen` — nothing real
is started or killed here (the live E2E was run separately on the box).
"""
from __future__ import annotations

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


def _urlopen_ok(url, timeout=None):  # noqa: ANN001, ARG001
    return _Resp()


def _urlopen_refused(url, timeout=None):  # noqa: ANN001, ARG001
    raise _REFUSED


def _make_api() -> gam.Api:
    return gam.Api.__new__(gam.Api)


class TestQdrantAutoHeal(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {
            "QDRANT_SPAWN_QDRANT": "",
            "QDRANT_QDRANT_START_BAT": "",
        }, clear=False)
        p.start()
        self.addCleanup(p.stop)

    # -- probe behaviour ------------------------------------------------
    def test_healthy_server_is_noop(self):
        api = _make_api()
        with mock.patch.object(gam.time, "sleep") as m_sleep, \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=_urlopen_ok), \
             mock.patch.object(gam.os.path, "isfile") as m_isfile, \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_qdrant_server(api)
            popen.assert_not_called()
            m_isfile.assert_not_called()
            m_sleep.assert_not_called()

    def test_down_loopback_spawns_and_waits_for_ready(self):
        api = _make_api()
        calls = {"n": 0}

        def _urlopen(url, timeout=None):
            calls["n"] += 1
            if calls["n"] >= 2:
                return _Resp()
            raise _REFUSED

        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=_urlopen), \
             mock.patch.object(gam.time, "sleep"), \
             mock.patch.object(gam.time, "time", side_effect=lambda: 100.0), \
             mock.patch.object(gam.os.path, "isfile", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_qdrant_server(api)
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0][0], "cmd")
        self.assertEqual(args[0][1], "/c")
        self.assertIn("start-qdrant.bat", args[0][2])
        self.assertEqual(kwargs.get("cwd"), r"C:\qdrant")
        self.assertTrue(kwargs.get("creationflags", 0) & 0x00000008,
                        "must spawn DETACHED")
        self.assertIn(kwargs.get("stdin"),
                      (subprocess.DEVNULL, subprocess.PIPE, None),
                      "stdin must be detached/null")

    def test_missing_start_bat_warns_and_does_not_spawn(self):
        api = _make_api()
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=_urlopen_refused), \
             mock.patch.object(gam.os.path, "isfile", return_value=False), \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_qdrant_server(api)  # must not raise
        popen.assert_not_called()

    def test_remote_qdrant_url_is_skipped(self):
        api = _make_api()
        import src.config as cfgmod
        with mock.patch.object(cfgmod.config, "QDRANT_URL",
                               "http://192.168.1.50:6333", create=True), \
             mock.patch.object(urllib.request, "urlopen") as uo, \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_qdrant_server(api)
        uo.assert_not_called()
        popen.assert_not_called()

    def test_optout_env_skips_probe_and_spawn(self):
        api = _make_api()
        with mock.patch.dict(os.environ,
                             {"QDRANT_SPAWN_QDRANT": "off"}, clear=False), \
             mock.patch.object(urllib.request, "urlopen") as uo, \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_qdrant_server(api)
        uo.assert_not_called()
        popen.assert_not_called()

    def test_spawn_failure_never_raises(self):
        api = _make_api()
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=_urlopen_refused), \
             mock.patch.object(gam.os.path, "isfile", return_value=True), \
             mock.patch("subprocess.Popen", side_effect=OSError("no cmd")):
            gam.Api._ensure_qdrant_server(api)  # swallow + warn

    def test_gives_up_after_wait_window(self):
        api = _make_api()
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=_urlopen_refused), \
             mock.patch.object(gam.time, "sleep"), \
             mock.patch.object(gam.time, "time",
                               side_effect=[0.0, 10**9]), \
             mock.patch.object(gam.os.path, "isfile", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            gam.Api._ensure_qdrant_server(api)
        popen.assert_called_once()  # tried once, then warns


class TestPrewarmWiring(unittest.TestCase):
    def test_prewarm_calls_heal_before_agent_build(self):
        src = open(gam.__file__, encoding="utf-8").read()
        i_prew = src.index("def _prewarm_agent")
        self.assertIn("_ensure_qdrant_server()", src[i_prew:i_prew + 2000])
        self.assertLess(src.index("_ensure_qdrant_server()", i_prew),
                        src.index("_get_agent()", i_prew),
                        "heal must run BEFORE building the agent")


if __name__ == "__main__":
    unittest.main()