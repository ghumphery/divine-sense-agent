# -*- coding: utf-8 -*-
"""Tests: task-level LLM model pins (v0.9.110.0).

``src.agent.task_llm`` (parse/validate/catalogs, HTTP mocked via
urllib), ``Agent._pick_provider`` pin honour (config pinned directly
on the CURRENT config object — the reload-safe test pattern), the
``TaskLocalHost.run_task_once(llm=...)`` validation gate, and the
skill-compiler per-stage/default llm passthrough.
"""
import json
import os
import unittest
import urllib.error
import urllib.request
from unittest import mock

import src.agent.task_llm as task_llm


_OR_MODELS = {"data": [{"id": "z-ai/glm-4.6"},
                       {"id": "z-ai/glm-5.3"},
                       {"id": "openai/gpt-5.1"},
                       {"id": "", "broken": True}]}
_OR_IDS = sorted({"z-ai/glm-4.6", "z-ai/glm-5.3", "openai/gpt-5.1"})


def _or_response(url, timeout=None):  # noqa: ANN001, ARG001
    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(_OR_MODELS).encode()
    return _R()


class TestParse(unittest.TestCase):
    def test_valid_with_second_colon_ollama(self):
        self.assertEqual(
            task_llm.parse_task_llm(" ollama:gpt-oss:120b-cloud "),
            ("ollama", "gpt-oss:120b-cloud"))

    def test_valid_openrouter(self):
        self.assertEqual(
            task_llm.parse_task_llm("OpenRouter:z-ai/glm-4.6"),
            ("openrouter", "z-ai/glm-4.6"))

    def test_rejects_no_separator(self):
        with self.assertRaises(task_llm.TaskLlmError):
            task_llm.parse_task_llm("just-model-name")

    def test_rejects_empty_model(self):
        with self.assertRaises(task_llm.TaskLlmError):
            task_llm.parse_task_llm("openrouter:")

    def test_rejects_disallowed_provider(self):
        with self.assertRaises(task_llm.TaskLlmError):
            task_llm.parse_task_llm("siliconflow:whatever")

    def test_rejects_empty(self):
        with self.assertRaises(task_llm.TaskLlmError):
            task_llm.parse_task_llm("")
class TestCatalogs(unittest.TestCase):
    def setUp(self):
        task_llm.clear_catalog_cache()

    def tearDown(self):
        task_llm.clear_catalog_cache()

    def test_openrouter_parse_and_cache(self):
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=_or_response):
            cat = task_llm.catalog_for("openrouter")
            cat2 = task_llm.catalog_for("openrouter")
        self.assertTrue(cat["ok"])
        self.assertEqual(cat["models"], _OR_IDS)

    def test_ollama_tags_parse_and_auth_header(self):
        calls = []

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"models": [
                    {"name": "gpt-oss:120b", "model": "gpt-oss:120b-cloud"},
                    {"name": "glm-5.3"},
                ]}).encode()

        def _urlopen(req, timeout=None):
            calls.append(dict(req.headers))
            return _R()

        with mock.patch.dict(os.environ, {
            "OLLAMA_API_KEY": "sk-test", "OLLAMA_BASE_URL": "",
        }, clear=False), \
                mock.patch.object(urllib.request, "urlopen",
                                  side_effect=_urlopen) as uo:
            cat = task_llm.catalog_for("ollama")
        self.assertTrue(cat["ok"])
        self.assertEqual(cat["models"], ["glm-5.3", "gpt-oss:120b",
                                         "gpt-oss:120b-cloud"])
        self.assertTrue(uo.called)
        self.assertTrue(any(
            "Bearer sk-test" in " ".join(map(str, c.values()))
            for c in calls), "Bearer auth header must be sent")

    def test_unreachable_catalog_degrades_with_warning(self):
        def _boom(url, timeout=None):
            raise urllib.error.URLError("refused")

        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=_boom):
            v = task_llm.validate_task_llm("openrouter:z-ai/glm-4.6")
        self.assertTrue(v["ok"])
        self.assertIn("warning", v)

    def test_validate_known_unknown_alias(self):
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=_or_response):
            ok = task_llm.validate_task_llm("openrouter:z-ai/glm-4.6")
            bad = task_llm.validate_task_llm("openrouter:z-ai/gx-9.9")
        self.assertTrue(ok["ok"])
        self.assertFalse(bad["ok"])
        self.assertIn("suggestions", bad)

    def test_bad_spec_rejected(self):
        v = task_llm.validate_task_llm("siliconflow:whatever")
        self.assertFalse(v["ok"])

    def test_list_task_models_filter_and_cap(self):
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=_or_response):
            out = task_llm.list_task_models(contains="glm")
            one = task_llm.list_task_models(provider="openrouter",
                                            per_provider_cap=2)
        self.assertEqual(out["openrouter"]["models"],
                         ["z-ai/glm-4.6", "z-ai/glm-5.3"])
        self.assertTrue(one["openrouter"]["truncated"])
        self.assertEqual(len(one["openrouter"]["models"]), 2)


class TestAgentPinProvider(unittest.TestCase):
    """Agent._pick_provider honours the pin verbatim (no failover)."""

    def _agent(self, pin):
        import src.agent.agent  # noqa: F401

        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a._task_llm = pin
        a._client = None
        a._extra_body = None
        return a

    def test_openrouter_pin_bypasses_chain(self):
        import src.config as cfgmod

        with mock.patch.object(cfgmod.config, "OLLAMA_API_KEY", "K-OLL",
                               create=True), \
             mock.patch.object(cfgmod.config, "OPENROUTER_API_KEY",
                               "K-OR", create=True), \
             mock.patch.object(cfgmod.config, "LLM_PROVIDER", "",
                               create=True), \
             mock.patch("src.agent.agent.OpenAI") as fake_openai:
            a = self._agent("openrouter:z-ai/glm-4.6")
            name, model, client = a._pick_provider()
        self.assertEqual(name, "openrouter")
        self.assertEqual(model, "z-ai/glm-4.6")
        self.assertIs(client, fake_openai.return_value)
        self.assertIsInstance(a._extra_body, dict)

    def test_ollama_pin_with_colon_model_exact(self):
        import src.config as cfgmod

        with mock.patch.object(cfgmod.config, "OLLAMA_API_KEY", "K-OLL",
                               create=True), \
             mock.patch.object(cfgmod.config, "OLLAMA_BASE_URL",
                               "https://ollama.com", create=True), \
             mock.patch.object(cfgmod.config, "LLM_PROVIDER",
                               "openrouter", create=True), \
             mock.patch("src.agent.agent.OpenAI") as fake_openai:
            a = self._agent("ollama:gpt-oss:120b-cloud")
            name, model, client = a._pick_provider()
        self.assertEqual(name, "ollama")
        # the pin WINS over both the forced LLM_PROVIDER and the
        # default model resolution (no DEFAULT_CHAT_MODEL leak)
        self.assertEqual(model, "gpt-oss:120b-cloud")
        self.assertIs(client, fake_openai.return_value)
        self.assertEqual(a._extra_body, {})

    def test_pin_missing_key_raises_clearly(self):
        import src.config as cfgmod

        with mock.patch.object(cfgmod.config, "OLLAMA_API_KEY", "",
                               create=True):
            a = self._agent("ollama:gpt-oss:120b-cloud")
            with self.assertRaises(ValueError) as ctx:
                a._pick_provider()
        self.assertIn("OLLAMA_API_KEY", str(ctx.exception))
        self.assertIn("task_llm", str(ctx.exception))


class TestTaskHostGate(unittest.TestCase):
    def _host(self):
        from src.mcp_client.host import TaskLocalHost

        h = TaskLocalHost(mcp_manager=mock.Mock())
        import src.agent.task_runner as tr

        tr.reset_tasks()
        self.addCleanup(tr.reset_tasks)
        return h, tr

    def test_valid_llm_threads_to_agent(self):
        h, tr = self._host()
        seen = {}
        h._fresh_agent = lambda llm=None: seen.setdefault("llm", llm) or "A"  # noqa: E731
        with mock.patch.object(task_llm, "validate_task_llm",
                               return_value={"ok": True,
                                             "provider": "openrouter",
                                             "model": "z-ai/glm-4.6"}):
            out = h._run_once({"prompt": "job", "title": "T",
                               "llm": "openrouter:z-ai/glm-4.6"})
        self.assertTrue(out["ok"])
        self.assertEqual(seen.get("llm"), "openrouter:z-ai/glm-4.6")
        rec = tr.list_tasks(limit=5)["tasks"][0]
        self.assertEqual(rec["llm"], "openrouter:z-ai/glm-4.6")

    def test_invalid_llm_fails_fast_no_task(self):
        h, tr = self._host()
        with mock.patch.object(
                task_llm, "validate_task_llm",
                return_value={"ok": False,
                              "error": "model not in catalog",
                              "suggestions": ["z-ai/glm-4.6"]}):
            out = h._run_once({"prompt": "job",
                               "llm": "openrouter:missing-model"})
        self.assertIn("error", out)
        self.assertEqual(out.get("suggestions"), ["z-ai/glm-4.6"])
        self.assertEqual(len(tr.list_tasks(limit=50)["tasks"]), 0)

    def test_no_llm_is_legacy_default(self):
        h, tr = self._host()
        seen = {}
        h._fresh_agent = lambda llm=None: seen.setdefault("llm", llm) or "A"  # noqa: E731
        out = h._run_once({"prompt": "job"})
        self.assertTrue(out["ok"])
        self.assertIsNone(out.get("llm"))
        self.assertIsNone(seen.get("llm"))

    def test_list_task_models_tool_exists(self):
        from src.mcp_client.host import TaskLocalHost

        names = [t.name for t in TaskLocalHost().list_tools()]
        self.assertIn("run_task_once", names)
        self.assertIn("list_task_models", names)
        rt = [t for t in TaskLocalHost().list_tools()
              if t.name == "run_task_once"][0]
        self.assertIn("llm", rt.input_schema["properties"])

    def test_stdio_mirror_has_list_task_models(self):
        import importlib

        ts = importlib.import_module("src.mcp_server.task_server")
        specs = list(ts._TOOL_SPECS)
        names = [s.name for s in specs]
        self.assertIn("list_task_models", names)
        rt = [s for s in specs if s.name == "run_task_once"][0]
        self.assertIn("llm", rt.inputSchema["properties"])


class TestSkillStagePin(unittest.TestCase):
    """compile_plan validates stage/default pins; _run_stage passes
    the pin to the factory (legacy zero-arg factory tolerated)."""

    def _core(self):
        from src.agent import skill_compiler as sc

        sc._reset_for_tests()
        sc.set_agent_factory(None)
        self.addCleanup(sc._reset_for_tests)
        self.addCleanup(lambda: sc.set_agent_factory(None))
        return sc

    def test_compile_validates_bad_default_llm(self):
        sc = self._core()
        with mock.patch.object(task_llm, "validate_task_llm",
                               return_value={"ok": False,
                                             "error": "no such model"}):
            with self.assertRaises(sc.PlanError) as ctx:
                sc.compile_plan(
                    name="p",
                    stages=[{"title": "a", "prompt": "pa"}],
                    default_llm="openrouter:missing",
                )
        self.assertIn("default_llm", str(ctx.exception))

    def test_stage_llm_recorded_and_factory_receives_it(self):
        sc = self._core()
        seen = {}

        class _FA:
            def __init__(self, llm=None):
                seen["llm"] = llm

        sc.set_agent_factory(lambda llm=None: _FA(llm=llm))

        def _v(spec):  # validate passthrough: real parse, always ok
            name, model = task_llm.parse_task_llm(spec)
            return {"ok": True, "provider": name, "model": model}

        with mock.patch.object(task_llm, "validate_task_llm",
                               side_effect=_v):
            rec = sc.compile_plan(
                name="p2",
                stages=[{"title": "a", "prompt": "pa",
                         "llm": "ollama:gpt-oss:120b-cloud"}],
                default_llm="openrouter:z-ai/glm-4.6",
            )
        s = rec["stages"][0]
        self.assertEqual(s["llm"], "ollama:gpt-oss:120b-cloud")
        self.assertEqual(rec["default_llm"],
                         "openrouter:z-ai/glm-4.6")
        sc.run_plan(rec["id"])
        import time as _t

        for _ in range(40):
            r = sc.get_plan_status(rec["id"])
            if r["stages"][0]["status"] in ("done", "failed", "skipped"):
                break
            _t.sleep(0.2)
        self.assertEqual(seen.get("llm"), "ollama:gpt-oss:120b-cloud")

    def test_legacy_zero_arg_factory_still_runs(self):
        sc = self._core()
        sc.set_agent_factory(lambda: "legacy-agent")

        with mock.patch.object(
                task_llm, "validate_task_llm",
                return_value={"ok": True, "provider": "openrouter",
                              "model": "z-ai/glm-4.6"}):
            rec = sc.compile_plan(
                name="p3",
                stages=[{"title": "a", "prompt": "pa",
                         "llm": "openrouter:z-ai/glm-4.6"}],
            )
        sc.run_plan(rec["id"])
        import time as _t

        for _ in range(40):
            r = sc.get_plan_status(rec["id"])
            if r["stages"][0]["status"] in ("done", "failed", "skipped"):
                break
            _t.sleep(0.2)
        self.assertIn(r["stages"][0]["status"], ("done", "failed"))


class TestLlamacppPinV0133_11(unittest.TestCase):
    """v0.9.133.11 — the LOCAL llama.cpp chat server (:8090) as a pin
    target: parse accepts it, validate probes its /v1/models, and
    Agent._pick_provider honours it WITHOUT any provider-table entry
    (an auto-detect leak of the weak CPU summary model into the chat
    chain would be a regression)."""

    def _agent(self, pin):
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a._task_llm = pin
        a._client = None
        a._extra_body = None
        return a

    def test_parse_accepts_llamacpp(self):
        self.assertEqual(
            task_llm.parse_task_llm("llamacpp:qwen2.5-7b-cpu"),
            ("llamacpp", "qwen2.5-7b-cpu"))

    def test_validate_llamacpp_catalog_hit(self):
        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"data": [{"id": "qwen2.5-7b-cpu"}]
                                   }).encode()

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            fake_urlopen.url = req.full_url
            return R()

        task_llm.clear_catalog_cache()
        with mock.patch.object(task_llm.urllib.request, "urlopen",
                               fake_urlopen):
            v = task_llm.validate_task_llm("llamacpp:qwen2.5-7b-cpu")
        self.assertTrue(v.get("ok"), v)
        self.assertEqual(v.get("provider"), "llamacpp")
        self.assertEqual(v.get("model"), "qwen2.5-7b-cpu")
        self.assertIn("/v1/models", fake_urlopen.url)
        self.assertIn("8090", fake_urlopen.url)

    def test_validate_llamacpp_server_down_degrades(self):
        task_llm.clear_catalog_cache()
        with mock.patch.object(
                task_llm.urllib.request, "urlopen",
                mock.Mock(side_effect=ConnectionError("refused"))):
            v = task_llm.validate_task_llm("llamacpp:qwen2.5-7b-cpu")
        self.assertTrue(v.get("ok"), "catalog down degrades to unverified")
        self.assertIn("UNVERIFIED", v.get("warning", ""))

    def test_agent_pin_llamacpp_direct_base(self):
        import src.agent.agent as amod
        import src.config as cfgmod

        with mock.patch.object(cfgmod.config, "LLM_PROVIDER", "",
                               create=True), \
             mock.patch("src.agent.agent.OpenAI") as fake_openai:
            a = self._agent("llamacpp:qwen2.5-7b-cpu")
            name, model, client = a._pick_provider()
        self.assertEqual(name, "llamacpp")
        self.assertEqual(model, "qwen2.5-7b-cpu")
        self.assertIs(client, fake_openai.return_value)
        self.assertEqual(a._extra_body, {})
        _, kwargs = fake_openai.call_args
        base = str(kwargs.get("base_url") or "")
        self.assertIn("8090", base)
        self.assertTrue(base.endswith("/v1"))
        self.assertIsInstance(amod, object)

    def test_agent_pin_llamacpp_custom_env(self):
        with mock.patch("src.agent.agent.OpenAI") as fake_openai, \
             mock.patch.dict(os.environ, {
                 "QDRANT_SESSION_SUMMARY_LLAMACPP_URL":
                     "http://127.0.0.1:9999/v1"}):
            a = self._agent("llamacpp:qwen2.5-7b-cpu")
            a._pick_provider()
        _, kwargs = fake_openai.call_args
        self.assertEqual(kwargs.get("base_url"), "http://127.0.0.1:9999/v1")


    def test_narrow_prompt_for_llamacpp_pin(self):
        """v0.9.133.11 — a llamacpp-pinned turn's system prompt is the
        NARROW batch-job flavour (the generic manual alone measured
        ~19.3k tokens > the CPU server's 12,288 ctx)."""
        import src.agent.agent as amod
        import src.config as cfgmod

        with mock.patch.object(cfgmod.config, "LLM_PROVIDER", "",
                               create=True), \
             mock.patch("src.agent.agent.OpenAI"):
            a = self._agent("llamacpp:qwen2.5-7b-cpu")
            msgs = a._messages_with_user("每日對話摘要沉澱", None,
                                         web_enabled=False,
                                         tools_enabled=True)
        sysc = msgs[0]["content"]
        self.assertIn("scheduled-task runner", sysc)
        self.assertNotIn("You are a helpful assistant with access to "
                         "a tool surface", sysc)
        # and the generic flavour is untouched for unpinned agents
        with mock.patch.object(cfgmod.config, "LLM_PROVIDER", "",
                               create=True), \
             mock.patch("src.agent.agent.OpenAI"):
            a2 = self._agent("")
            msgs2 = a2._messages_with_user("hello", None,
                                           web_enabled=False,
                                           tools_enabled=True)
        self.assertIn("tool surface", msgs2[0]["content"])
        self.assertNotIn("scheduled-task runner", msgs2[0]["content"])
        self.assertIsInstance(amod, object)

    def test_narrow_prompt_respects_language(self):
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a._task_llm = "llamacpp:qwen2.5-7b-cpu"
        p = a._pin_narrow_prompt("zh-TW")
        self.assertIn("Respond in zh-TW", p)


class TestPromptBlocks(unittest.TestCase):
    def test_system_prompt_mentions_pin(self):
        import src.agent.agent as am

        src_txt = open(am.__file__, encoding="utf-8").read()
        self.assertIn("list_task_models", src_txt)
        self.assertIn("Task-level model pin", src_txt)
        self.assertIn("default_llm", src_txt)


if __name__ == "__main__":
    unittest.main()