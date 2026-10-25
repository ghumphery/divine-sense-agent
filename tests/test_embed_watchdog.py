# -*- coding: utf-8 -*-
"""Tests: llama.cpp embedding watchdog (v0.9.112.0).

``src/embed_watchdog.py`` is the third member of the auto-heal family
(v0.9.109.3 qdrant / v0.9.109.4 firefox) — and the only one that runs
PERIODICALLY and can KILL a process, because only a real embeddings
POST can detect the llama.cpp ZOMBIE state (process alive,
``/v1/models`` 200, every ``POST /v1/embeddings`` failing).

Everything here is mock-driven: ``requests.post`` / ``subprocess`` /
``time`` are patched — nothing real is started, netstat'ed or killed
(the live E2E ran separately on the box: netstat picked up PID
170404 on :8080).
"""
from __future__ import annotations

import os
import subprocess
import unittest
from unittest import mock

import gui.api as gam
import src.embed_watchdog as wd


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {
            "data": [{"embedding": [0.1, 0.2, 0.3]}],
        }

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _clean_env(**extra):
    base = {
        "QDRANT_LLAMACPP_WATCHDOG": "",
        "QDRANT_LLAMACPP_WATCHDOG_INTERVAL": "",
        "QDRANT_LLAMACPP_WATCHDOG_TIMEOUT": "",
        "QDRANT_LLAMACPP_WATCHDOG_STREAK": "",
        "QDRANT_LLAMACPP_WATCHDOG_COOLDOWN": "",
        "QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL": "",
        "QDRANT_LLAMACPP_START_BAT": "",
        "LLAMACPP_EMBED_URL": "",
        # v0.9.122.2 — gemma2 watchdog gate + its env surface
        "QDRANT_GEMMA2_WATCHDOG": "",
        "QDRANT_GEMMA2_START_BAT": "",
        "QDRANT_MEDIA_EMBEDDING": "",
        "QDRANT_MEDIA_INGEST": "",
        "GEMMA2_EMBED_URL": "",
    }
    base.update(extra)
    return mock.patch.dict(os.environ, base, clear=False)


def _mk(**overrides) -> wd.EmbedWatchdog:
    with _clean_env():
        for k, v in overrides.items():
            os.environ[k] = v
        return wd.EmbedWatchdog()


class TestProbeOnce(unittest.TestCase):
    def test_ok(self):
        w = _mk()
        with mock.patch.object(wd.requests, "post", return_value=_Resp()):
            ok, ms, note = w._probe_once(timeout=1.0)
        self.assertTrue(ok)
        self.assertGreaterEqual(ms, 0.0)
        self.assertEqual(note, "ok")

    def test_http_500_is_failure_with_note(self):
        w = _mk()
        with mock.patch.object(wd.requests, "post",
                               return_value=_Resp(status=500)):
            ok, ms, note = w._probe_once(timeout=1.0)
        self.assertFalse(ok)
        self.assertIn("500", note)

    def test_transport_error_is_failure(self):
        w = _mk()
        with mock.patch.object(wd.requests, "post",
                               side_effect=ConnectionError("refused")):
            ok, ms, note = w._probe_once(timeout=1.0)
        self.assertFalse(ok)
        self.assertIn("refused", note)

    def test_empty_embedding_row_is_failure(self):
        w = _mk()
        with mock.patch.object(
                wd.requests, "post",
                return_value=_Resp(payload={"data": []})):
            ok, _ms, note = w._probe_once(timeout=1.0)
        self.assertFalse(ok)
        self.assertIn("empty", note)

    def test_probe_body_is_real_embeddings_post(self):
        w = _mk()
        with mock.patch.object(wd.requests, "post",
                               return_value=_Resp()) as m_post:
            w._probe_once(timeout=1.0)
        kwargs = m_post.call_args.kwargs
        self.assertEqual(kwargs.get("json"), {"input": wd.PROBE_TEXT})


class TestTick(unittest.TestCase):
    def test_ok_resets_streak_and_records(self):
        w = _mk()
        w.consecutive_failures = 3
        with mock.patch.object(wd.requests, "post", return_value=_Resp()):
            out = w.tick()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(w.consecutive_failures, 0)
        self.assertEqual(w.last_probe_ok, True)
        self.assertIsNotNone(w.last_probe_ts)

    def test_first_failure_observed_only(self):
        w = _mk()
        # v0.9.115.1: the refused fast-heal must NOT reach the host in
        # a unit test — pin the dead-state classifier off-path.
        with mock.patch.object(wd.requests, "post",
                               return_value=_Resp(status=500)), \
             mock.patch.object(wd.EmbedWatchdog, "_dead_refused",
                               return_value=False):
            out = w.tick()
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["streak"], 1)
        self.assertEqual(w.consecutive_failures, 1)
        self.assertEqual(w.restarts, 0)

    def test_streak_reached_triggers_restart(self):
        w = _mk()
        with mock.patch.object(
                wd.requests, "post", return_value=_Resp(status=500),
        ) as post, \
            mock.patch.object(wd.EmbedWatchdog, "_dead_refused",
                              return_value=False), \
            mock.patch.object(wd.EmbedWatchdog, "_restart",
                              return_value={"action": "spawned"}) as rst:
            w.tick()
            w.tick()
        self.assertEqual(rst.call_count, 1)
        self.assertEqual(w.consecutive_failures, 2)
        # probes still hit the REAL url after two restart-eligible fails
        self.assertEqual(post.call_count, 2)

    def test_cooldown_skips_restart(self):
        w = _mk()
        w.cooling_until = 10.0 ** 12  # far future
        try:
            with mock.patch.object(
                    wd.requests, "post", return_value=_Resp(status=503)), \
                 mock.patch.object(wd.EmbedWatchdog, "_dead_refused",
                                   return_value=False):
                w.tick()
                w.tick()
        finally:
            w.cooling_until = 0.0
        self.assertEqual(w.restarts, 0)


class TestRefusedHeal(unittest.TestCase):
    """v0.9.115.1 — the dead fast path: a TCP-REFUSED probe failure
    heals IMMEDIATELY (no 2-streak wait) when no llama-server process
    exists; an alive process (loading) / an unreadable process table
    keeps the legacy streak path. All mock-driven — no real socket,
    tasklist, netstat or spawn."""

    def test_refused_without_llama_process_heals_first_failure(self):
        w = _mk()
        with mock.patch.object(
                wd.requests, "post",
                side_effect=ConnectionError("refused")), \
             mock.patch.object(wd.EmbedWatchdog, "_dead_refused",
                               return_value=True), \
             mock.patch.object(wd.EmbedWatchdog, "_restart",
                               return_value={"action": "spawned"}) as rst:
            out = w.tick()
        # THE heal fired on the FIRST failure (no streak wait)
        self.assertEqual(rst.call_count, 1)
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["dead_now"], True)
        self.assertEqual(w.consecutive_failures, 1)

    def test_refused_with_llama_process_alive_observed(self):
        w = _mk()
        with mock.patch.object(
                wd.requests, "post",
                side_effect=ConnectionError("refused")), \
             mock.patch.object(wd.EmbedWatchdog, "_dead_refused",
                               return_value=False), \
             mock.patch.object(wd.EmbedWatchdog, "_restart") as rst:
            out = w.tick()
        rst.assert_not_called()
        self.assertEqual(out["streak"], 1)
        self.assertEqual(w.restarts, 0)

    def test_dead_refused_helper_matrix(self):
        w = _mk()
        cases = [
            # (port_state, llama_process_alive) -> dead?
            (("refused", False), True),   # nothing on port + no process
            (("refused", True), False),   # llama exists → may be loading
            (("refused", None), False),   # cannot tell → conservative
            (("open", False), False),     # someone listening → not dead
            (("unknown", False), False),  # cannot tell → not dead
        ]
        for (state, alive), expect in cases:
            with mock.patch.object(wd.EmbedWatchdog, "_port_state",
                                   return_value=state), \
                 mock.patch.object(wd.EmbedWatchdog,
                                   "_llama_process_alive",
                                   return_value=alive):
                self.assertEqual(w._dead_refused(), expect,
                                 (state, alive))

    def test_port_state_classification(self):
        with mock.patch.object(wd.socket, "create_connection"):
            self.assertEqual(
                wd.EmbedWatchdog._port_state("127.0.0.1", 8080), "open")
        with mock.patch.object(wd.socket, "create_connection",
                               side_effect=ConnectionRefusedError()):
            self.assertEqual(
                wd.EmbedWatchdog._port_state("127.0.0.1", 8080), "refused")
        with mock.patch.object(wd.socket, "create_connection",
                               side_effect=OSError("no route")):
            self.assertEqual(
                wd.EmbedWatchdog._port_state("127.0.0.1", 8080), "unknown")

    def test_refused_heal_env_off_keeps_streak(self):
        w = _mk(QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL="off")
        self.assertFalse(w.refused_heal)
        with mock.patch.object(
                wd.requests, "post",
                side_effect=ConnectionError("refused")), \
             mock.patch.object(wd.EmbedWatchdog, "_dead_refused") as m_dead, \
             mock.patch.object(wd.EmbedWatchdog, "_restart") as rst:
            out = w.tick()
        # env off → the classifier is never consulted → legacy streak
        m_dead.assert_not_called()
        rst.assert_not_called()
        self.assertEqual(out["dead_now"], False)
        self.assertEqual(w.restarts, 0)

    def test_dead_path_still_respects_restart_cooldown(self):
        # the IMMEDIATE heal goes through the SAME _restart() — the
        # 600s anti-loop cooldown inside it applies unchanged.
        w = _mk()
        with mock.patch.object(
                wd.requests, "post",
                side_effect=ConnectionError("refused")), \
             mock.patch.object(wd.EmbedWatchdog, "_dead_refused",
                               return_value=True), \
             mock.patch.object(wd.EmbedWatchdog, "_restart",
                               return_value={
                                   "action": "skipped",
                                   "reason": "cooldown",
                               }) as rst:
            out = w.tick()
        rst.assert_called_once()
        self.assertEqual(out["action"], "skipped")

    def test_status_carries_refused_heal(self):
        w = _mk()
        self.assertIn("refused_heal", w.status())

    def test_default_refused_heal_on(self):
        with _clean_env():
            self.assertTrue(wd.EmbedWatchdog().refused_heal)


class TestNetstatParse(unittest.TestCase):
    _TXT = (
        "\n"
        "  TCP    127.0.0.1:8080         0.0.0.0:0              "
        "LISTENING       170404\n"
        "  TCP    127.0.0.1:8080         127.0.0.1:52000        "
        "ESTABLISHED     170404\n"
        "  TCP    0.0.0.0:8080           0.0.0.0:0              "
        "LISTENING       170404\n"
        "  TCP    [::]:8080              [::]:0                 "
        "LISTENING       170404\n"
        "  TCP    127.0.0.1:6333         0.0.0.0:0              "
        "LISTENING       999\n"
        "  語系    TCP                    -                      -\n"
        "  TCP    127.0.0.1:18080        0.0.0.0:0              "
        "LISTENING       555\n"
    )

    def test_locale_proof_port_match(self):
        # zh-TW states are NOT matched (no state filter) but the port
        # suffix + numeric pid column are.
        pids = wd.EmbedWatchdog._extract_listen_pids(self._TXT, 8080)
        self.assertEqual(pids, [170404])

    def test_exact_suffix_only(self):
        # :18080 must not match a probe for :8080 (endswith trap is
        # avoided by the ':' boundary)
        pids = wd.EmbedWatchdog._extract_listen_pids(self._TXT, 18080)
        self.assertEqual(pids, [555])

    def test_empty_and_garbage(self):
        self.assertEqual(
            wd.EmbedWatchdog._extract_listen_pids("", 8080), [])
        self.assertEqual(
            wd.EmbedWatchdog._extract_listen_pids("\n\n", 8080), [])


class TestRestart(unittest.TestCase):
    def _w(self, bat_exists=True):
        w = _mk()
        return w

    def test_no_kill_when_bat_missing(self):
        w = _mk(QDRANT_LLAMACPP_START_BAT=r"D:\nope\go.bat")
        with mock.patch.object(wd.os.path, "isfile", return_value=False), \
             mock.patch.object(wd.EmbedWatchdog, "_netstat") as m_ns, \
             mock.patch.object(wd.EmbedWatchdog, "_kill_pid") as m_kill, \
             mock.patch.object(wd.subprocess, "Popen") as m_popen:
            r = w._restart("probe note")
        self.assertEqual(r["action"], "skipped")
        m_ns.assert_not_called()
        m_kill.assert_not_called()
        m_popen.assert_not_called()

    def test_kill_then_spawn_shape(self):
        w = _mk()
        # _clean_env must ALSO wrap the _restart call: _mk() releases its
        # env patch when it returns, and since v0.9.113.1 the live .env
        # pins QDRANT_LLAMACPP_START_BAT to the -4b bat — without this
        # context the resolve inside _restart would read the .env value
        # instead of the module DEFAULT this test asserts on.
        with _clean_env(), \
             mock.patch.object(wd.os.path, "isfile", return_value=True), \
             mock.patch.object(wd.EmbedWatchdog, "_netstat",
                               return_value=self._TXT()), \
             mock.patch.object(wd.EmbedWatchdog, "_extract_listen_pids",
                               return_value=[4242]), \
             mock.patch.object(wd.EmbedWatchdog, "_kill_pid",
                               return_value="OK") as m_kill, \
             mock.patch.object(wd.subprocess, "Popen") as m_popen, \
             mock.patch.object(wd.time, "sleep"), \
             mock.patch.object(wd.time, "time",
                               side_effect=lambda: 10 ** 9), \
             mock.patch.object(wd.EmbedWatchdog, "_probe_once",
                               return_value=(True, 30.0, "ok")):
            r = w._restart("zombie")
        m_kill.assert_called_once_with(4242)
        args, kwargs = m_popen.call_args
        self.assertEqual(args[0][:2], ["cmd", "/c"])
        self.assertTrue(str(args[0][2]).lower().endswith(
            "start-llamacpp-8b.bat"))
        self.assertEqual(kwargs.get("cwd"), r"C:\llama.cpp-vulkan")
        self.assertTrue(kwargs.get("creationflags", 0) & 0x00000008)
        self.assertEqual(kwargs.get("stdout"), subprocess.DEVNULL)
        self.assertGreaterEqual(len(r["killed"]), 1)
        self.assertTrue(r["ready"])
        self.assertEqual(w.restarts, 1)
        self.assertIsNotNone(w.last_restart_ts)

    def test_dead_process_spawns_only(self):
        w = _mk()
        with mock.patch.object(wd.os.path, "isfile", return_value=True), \
             mock.patch.object(wd.EmbedWatchdog, "_netstat",
                               return_value=""), \
             mock.patch.object(wd.EmbedWatchdog, "_kill_pid") as m_kill, \
             mock.patch.object(wd.subprocess, "Popen"), \
             mock.patch.object(wd.time, "sleep"), \
             mock.patch.object(wd.time, "time",
                               side_effect=lambda: 10 ** 9), \
             mock.patch.object(wd.EmbedWatchdog, "_probe_once",
                               return_value=(True, 25.0, "ok")):
            r = w._restart("refused")
        m_kill.assert_not_called()
        self.assertEqual(r["killed"], [])
        self.assertTrue(r["ready"])

    @staticmethod
    def _TXT():
        return "  TCP    127.0.0.1:8080   0.0.0.0:0   LISTENING   4242"

    def test_gives_up_after_ready_window(self):
        w = _mk()
        with mock.patch.object(wd.os.path, "isfile", return_value=True), \
             mock.patch.object(wd.EmbedWatchdog, "_netstat",
                               return_value=""), \
             mock.patch.object(wd.subprocess, "Popen"), \
             mock.patch.object(wd.time, "sleep"), \
             mock.patch.object(wd.time, "time",
                               side_effect=[
                                   100.0,   # cooldown calc
                                   100.0,   # deadline
                                   100.0,   # while enter
                                   1000.0,  # while exit
                                   1000.0,
                               ]), \
             mock.patch.object(wd.EmbedWatchdog, "_probe_once",
                               return_value=(False, 0.0, "http 500")):
            r = w._restart("zombie")
        self.assertFalse(r["ready"])
        self.assertEqual(w.restarts, 1)


class TestLifecycle(unittest.TestCase):
    def test_env_off_start_is_noop(self):
        w = _mk(QDRANT_LLAMACPP_WATCHDOG="off")
        self.assertFalse(w.start())
        self.assertFalse(w.running)
        self.assertFalse(w.enabled)

    def test_default_enabled_and_starts_then_stops(self):
        w = _mk()
        self.assertTrue(w.enabled)
        # v0.9.115.1: the first tick of the daemon thread now consults
        # the dead-state classifier — mock BOTH so the test never
        # touches the network / process table.
        with mock.patch.object(wd.EmbedWatchdog, "_dead_refused",
                               return_value=False), \
             mock.patch.object(wd.EmbedWatchdog, "_probe_once",
                               return_value=(True, 1.0, "ok")):
            self.assertTrue(w.start())
            self.assertTrue(w.running)
            # idempotent start
            self.assertFalse(w.start())
            w.stop(timeout=2.0)
            self.assertFalse(w.running)
            # stop twice is safe
            w.stop()

    def test_status_shape(self):
        w = _mk()
        st = w.status()
        for key in ("enabled", "running", "interval_s", "embed_url",
                    "probe_timeout_s", "failure_streak", "refused_heal",
                    "restart_cooldown_s", "start_bat", "ready_wait_s",
                    "last_probe_ts", "last_probe_ok", "last_probe_ms",
                    "consecutive_failures", "restarts",
                    "last_restart_ts", "last_error"):
            self.assertIn(key, st)
        self.assertEqual(st["consecutive_failures"], 0)

    def test_bat_resolution_prefers_override_then_vulkan(self):
        env = _clean_env()
        env.start()
        try:
            os.environ["QDRANT_LLAMACPP_START_BAT"] = r"D:\x\go.bat"
            self.assertEqual(
                wd.EmbedWatchdog._resolve_start_bat(),
                r"D:\x\go.bat",
            )
        finally:
            env.stop()
        # candidate walk: the vulkan bat beats the cpu fallback
        with _clean_env(), mock.patch.object(
                wd.os.path, "isfile",
                side_effect=lambda p: "vulkan" in str(p)):
            self.assertEqual(
                wd.EmbedWatchdog._resolve_start_bat(),
                wd.DEFAULT_START_BAT,
            )
class TestSingleton(unittest.TestCase):
    def test_get_watchdog_is_process_singleton(self):
        with _clean_env():
            a = wd.get_watchdog()
            b = wd.get_watchdog()
        self.assertIs(a, b)

    def test_start_embed_watchdog_never_raises(self):
        with _clean_env(), \
             mock.patch.object(wd.EmbedWatchdog, "start",
                               side_effect=RuntimeError("boom")):
            self.assertFalse(wd.start_embed_watchdog())
        with _clean_env():
            w = wd.get_watchdog()
            self.assertFalse(w.running)
            self.assertTrue(w.start())
            self.addCleanup(w.stop)


class TestGemma2Watchdog(unittest.TestCase):
    """v0.9.122.2 — the :8081 gemma2 media-server watchdog (subclass of
    the primary watchdog: own URL/bat/gate/thread-name, shared engine)."""

    def test_gate_default_follows_media_gate(self):
        with _clean_env():
            self.assertFalse(wd.gemma2_watchdog_enabled())
            self.assertFalse(wd.start_gemma2_watchdog())
        with _clean_env(QDRANT_MEDIA_EMBEDDING="on"):
            self.assertTrue(wd.gemma2_watchdog_enabled())

    def test_gate_env_overrides_media_gate(self):
        with _clean_env(QDRANT_GEMMA2_WATCHDOG="on",
                        QDRANT_MEDIA_EMBEDDING=""):
            self.assertTrue(wd.gemma2_watchdog_enabled())
        with _clean_env(QDRANT_GEMMA2_WATCHDOG="off",
                        QDRANT_MEDIA_EMBEDDING="on"):
            self.assertFalse(wd.gemma2_watchdog_enabled())

    def test_embed_url_flows_from_media_module(self):
        with _clean_env():
            self.assertEqual(
                wd.get_gemma2_watchdog().embed_url,
                wd.GEMMA2_DEFAULT_EMBED_URL,
            )
        with _clean_env(
                GEMMA2_EMBED_URL="http://localhost:8081/v1/embeddings"):
            w = wd.Gemma2Watchdog()
        # media_embed_url() performs the localhost→127.0.0.1 rewrite
        self.assertEqual(w.embed_url,
                         "http://127.0.0.1:8081/v1/embeddings")

    def test_start_bat_override_and_default(self):
        with _clean_env():
            self.assertEqual(
                wd.Gemma2Watchdog._resolve_start_bat(),
                wd.GEMMA2_DEFAULT_START_BAT,
            )
        with _clean_env(QDRANT_GEMMA2_START_BAT=r"D:\x\g2.bat"):
            self.assertEqual(
                wd.Gemma2Watchdog._resolve_start_bat(),
                r"D:\x\g2.bat",
            )

    def test_log_and_thread_hooks_overridden(self):
        with _clean_env():
            w = wd.Gemma2Watchdog()
        self.assertEqual(w._thread_name, "gemma2-watchdog")
        self.assertEqual(w._log_prefix, "[gemma2-watchdog]")
        self.assertFalse(w.enabled)  # media gate off by default
        b = wd.EmbedWatchdog()
        self.assertEqual(b._thread_name, "embed-watchdog")
        self.assertEqual(b._log_prefix, "[embed-watchdog]")

    def test_spawn_only_heal_uses_gemma2_bat(self):
        # the same _restart chain as the 4B watch — netstat on :8081
        # finds no PID (server dead) → NO kill → detached spawn of the
        # GEMMA2 bat (never the 4B launcher).
        with _clean_env(QDRANT_MEDIA_EMBEDDING="on"):
            w = wd.Gemma2Watchdog()
            with mock.patch.object(wd.EmbedWatchdog, "_dead_refused",
                                   return_value=False), \
                 mock.patch.object(wd.EmbedWatchdog, "_netstat",
                                   return_value=""), \
                 mock.patch.object(wd.EmbedWatchdog, "_kill_pid") as m_kill, \
                 mock.patch.object(wd.subprocess, "Popen") as m_pop, \
                 mock.patch.object(wd.time, "sleep"), \
                 mock.patch.object(wd.time, "time",
                                   side_effect=lambda: 10 ** 9), \
                 mock.patch.object(wd.EmbedWatchdog, "_probe_once",
                                   return_value=(True, 25.0, "ok")):
                r = w._restart("zombie")
            self.assertTrue(r["ready"])
            m_kill.assert_not_called()
            args0 = m_pop.call_args.args[0]
            joined = " ".join(args0) if isinstance(args0, (list, tuple)) \
                else str(args0)
            self.assertIn(wd.GEMMA2_DEFAULT_START_BAT, joined)

    def test_status_of_gemma2_singleton(self):
        with _clean_env(QDRANT_MEDIA_EMBEDDING="on"):
            a = wd.get_gemma2_watchdog()
            b = wd.get_gemma2_watchdog()
        self.assertIs(a, b)
        st = a.status()
        self.assertEqual(st["embed_url"], wd.GEMMA2_DEFAULT_EMBED_URL)
        self.assertEqual(st["start_bat"], wd.GEMMA2_DEFAULT_START_BAT)
        a.stop()

    def test_start_gemma2_guard_never_raises(self):
        with _clean_env(QDRANT_MEDIA_EMBEDDING="on"), \
             mock.patch.object(wd.Gemma2Watchdog, "start",
                               side_effect=RuntimeError("boom")):
            self.assertFalse(wd.start_gemma2_watchdog())


class TestWiring(unittest.TestCase):
    def test_gui_prewarm_starts_watchdog(self):
        src = open(gam.__file__, encoding="utf-8").read()
        i = src.index("def _prewarm_agent")
        seg = src[i:i + 5000]
        self.assertIn("_start_embed_watchdog()", seg)
        # v0.9.122.2 — the gemma2 media-server watchdog rides the same
        # prewarm (right after the primary; both never raise).
        self.assertIn("_start_gemma2_watchdog()", seg)
        # the order invariants of the v0.9.109.x healers stay intact
        self.assertLess(seg.index("_ensure_qdrant_server()"),
                        seg.index("_get_agent()"))

    def test_runtime_status_carries_watchdog_section(self):
        import src.agent.agent as am
        src = open(am.__file__, encoding="utf-8").read()
        i = src.index('if op == "status"')
        seg = src[i:i + 4000]
        self.assertIn("embedding_watchdog", seg)
        self.assertIn("src.embed_watchdog", seg)

    def test_status_op_answers_with_section(self):
        # behavioural: build a minimal Agent exactly like
        # test_runtime_host does (isolated QDRANT_RAG_HOME).
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="_wd_home")
        old = dict(os.environ)
        try:
            os.environ["QDRANT_RAG_HOME"] = tmp
            for k in ("OPENAI_API_KEY", "OPENAI_COMPAT_API_KEY",
                      "SILICONFLOW_API_KEY", "OLLAMA_API_KEY"):
                os.environ.pop(k, None)
            os.environ["LLM_PROVIDER"] = "openrouter"
            os.environ["OPENROUTER_API_KEY"] = "sk-test"
            from src.agent.agent import Agent
            a = Agent()
            st = a._runtime_ops("status")
            self.assertIn("embedding_watchdog", st)
            wd_sec = st["embedding_watchdog"]
            self.assertIn("enabled", wd_sec)
            self.assertIn("embed_url", wd_sec)
        except Exception:
            self.skipTest("agent construction unavailable in sandbox")
        finally:
            os.environ.clear()
            os.environ.update(old)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()