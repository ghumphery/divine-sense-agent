import sys

sys.path.insert(0, ".")
"""v0.9.133.11 addendum tests — llm-pin fallback env wiring."""
import os
import unittest
from unittest import mock

import src.agent.session_compactor as sc


class TestDefaultLlmFallbackV0133_11(unittest.TestCase):
    """A junk / missing llm argument falls back to the .env pin, and a
    bare model name never silently hits the cloud default chain."""

    def test_bare_model_name_falls_back_to_env(self):
        with mock.patch.dict(os.environ, {
                "QDRANT_SESSION_SUMMARY_LLM":
                    "llamacpp:qwen2.5-7b-cpu"}), \
             mock.patch(
                 "src.agent.session_compactor._llamacpp_direct_call",
                 return_value="SUM") as fake:
            out = sc._default_llm_call("p", "qwen2.5-7b-cpu")
        self.assertEqual(out, "SUM")
        fake.assert_called_once()
        self.assertEqual(fake.call_args[0][0], "qwen2.5-7b-cpu")

    def test_none_llm_reads_env(self):
        with mock.patch.dict(os.environ, {
                "QDRANT_SESSION_SUMMARY_LLM":
                    "llamacpp:qwen2.5-7b-cpu"}), \
             mock.patch(
                 "src.agent.session_compactor._llamacpp_direct_call",
                 return_value="SUM") as fake:
            self.assertEqual(sc._default_llm_call("p", None), "SUM")
        fake.assert_called_once()

    def test_valid_pin_wins_over_env(self):
        with mock.patch.dict(os.environ, {
                "QDRANT_SESSION_SUMMARY_LLM": "llamacpp:other"}, \
             ), \
             mock.patch(
                 "src.agent.session_compactor._llamacpp_direct_call",
                 return_value="OK") as fake:
            sc._default_llm_call("p", "llamacpp:qwen2.5-7b-cpu")
        fake.assert_called_once()
        self.assertEqual(fake.call_args[0][0], "qwen2.5-7b-cpu")

    def test_empty_env_pin_cloud_rule_still_last(self):
        """env unset + no llm → the Agent() legacy behaviour (tests
        stub Agent to avoid networking)."""
        with mock.patch.dict(os.environ, {
                "QDRANT_SESSION_SUMMARY_LLM": ""}), \
             mock.patch("src.agent.agent.Agent.__init__",
                        side_effect=Exception("agent-constructed")):
            self.assertEqual(
                sc._default_llm_call("p", None), "")


if __name__ == "__main__":
    unittest.main()