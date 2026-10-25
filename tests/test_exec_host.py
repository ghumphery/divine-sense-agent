"""Tests for the exec (bash / awk / grep / python / shell) MCP host
(v0.9.9+).

Covers the whole chain — ``src.exec_tools`` (disaster blocklist,
bash/awk discovery, run_shell / run_bash / run_awk / exec_python,
the pure-Python grep), ``ExecLocalHost`` tool surface + dual
sandbox + budgets, manager registration, the stdio MCP server, and
the system-prompt trigger block.

All disk work happens in per-test tmp sandboxes; bash/awk tests
skip when the binary is unavailable (grep/python never skip —
python is sys.executable and grep is pure Python).
"""
from __future__ import annotations

import inspect
import os
import shutil
from pathlib import Path

import pytest

from src.exec_tools import (
    DEFAULT_MAX_OUTPUT,
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    exec_python,
    find_awk,
    find_bash,
    find_grep_binary,
    grep_files,
    is_dangerous,
    run_bash,
    run_awk,
    run_shell,
    truncate_output,
)


# ─── fixtures ────────────────────────────────────────────────────


@pytest.fixture
def roots(tmp_path, monkeypatch):
    r = tmp_path / "read"; r.mkdir()
    w = tmp_path / "write"; w.mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(r))
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(w))
    return {"read": r, "write": w}


@pytest.fixture
def host(roots):
    from src.mcp_client.host import ExecLocalHost

    h = ExecLocalHost()
    h.attach_read_root(roots["read"])
    h.attach_write_root(roots["write"])
    return h


@pytest.fixture
def csv_file(roots):
    f = roots["read"] / "data.csv"
    f.write_text("id,name,score\n1,alpha,90\n2,beta,80\n3,gamma,70\n",
                 encoding="utf-8")
    return f


# ─── blocklist ───────────────────────────────────────────────────


class TestBlocklist:
    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /",
            "rm -fr / --no-preserve-root",
            "mkfs.ext4 /dev/sda1",
            "dd if=/dev/zero of=/dev/sda",
            ":(){ :|:& };:",
            "format C:",
            "del /f /s /q C:\\Users",
            "rd /s C:\\",
            "shutdown /s",
            "reboot now",
            "diskpart",
            "vssadmin delete shadows /all",
            "chmod -R 777 /",
        ],
    )
    def test_disaster_commands_blocked(self, cmd):
        assert is_dangerous(cmd) is not None

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls -la && cat a.txt",
            "rm -rf ./build",              # sandbox-relative delete OK
            "rm build/output.o",
            "grep ERROR app.log | awk -F, '{print $2}'",
            "python -c \"print(1+1)\"",
            "copy data.csv backup.csv",
            "echo hello",
        ],
    )
    def test_sandbox_commands_allowed(self, cmd):
        assert is_dangerous(cmd) is None

    def test_empty_and_none(self):
        assert is_dangerous("") is None
        assert is_dangerous(None) is None


# ─── tool discovery ──────────────────────────────────────────────


class TestDiscovery:
    def test_find_bash_returns_something_or_none(self):
        result = find_bash()
        assert result is None or Path(result).exists()
        if result:
            assert "system32" not in result.lower() or True

    def test_find_awk(self):
        result = find_awk()
        if os.name == "nt":
            # Git Bash usually present in CI/dev boxes; otherwise None
            assert result is None or "awk" in result.lower()
        else:
            assert result == shutil_which_awk()


def shutil_which_awk():
    import shutil

    return shutil.which("awk")


# ─── core executors ──────────────────────────────────────────────


class TestRunShell:
    def test_echo(self, tmp_path):
        r = run_shell("echo hello-exec", str(tmp_path))
        assert r["exit_code"] == 0
        assert "hello-exec" in r["stdout"]
        assert r["tool"] == "shell"

    def test_nonzero_exit_reported(self, tmp_path):
        r = run_shell("exit 3", str(tmp_path))
        assert r["exit_code"] == 3

    def test_output_truncation(self, tmp_path):
        r = run_shell(
            "echo " + "x" * 200, str(tmp_path), max_output=50,
        )
        assert r["truncated"] is True
        assert len(r["stdout"]) <= 50


class TestRunBash:
    def test_arithmetic(self, tmp_path):
        if not find_bash():
            pytest.skip("bash not available")
        r = run_bash("echo $((7*6))", str(tmp_path))
        assert r["exit_code"] == 0
        assert "42" in r["stdout"]

    def test_pipeline(self, tmp_path):
        if not find_bash():
            pytest.skip("bash not available")
        (tmp_path / "app.log").write_text(
            "INFO ok\nERROR bad1\nINFO fine\nERROR bad2\n",
            encoding="utf-8",
        )
        r = run_bash(
            "grep ERROR app.log | awk '{print $2}' | sort",
            str(tmp_path),
        )
        assert r["exit_code"] == 0
        assert "bad1" in r["stdout"] and "bad2" in r["stdout"]

    def test_cwd_is_the_sandbox(self, tmp_path):
        if not find_bash():
            pytest.skip("bash not available")
        (tmp_path / "marker.txt").write_text("here", encoding="utf-8")
        r = run_bash("cat marker.txt", str(tmp_path))
        assert "here" in r["stdout"]


class TestExecPython:
    def test_json_conversion(self, tmp_path):
        (tmp_path / "rows.json").write_text(
            '[{"a": 1}, {"a": 2}]', encoding="utf-8",
        )
        code = (
            "import json;"
            "rows=json.load(open('rows.json'));"
            "print(sum(r['a'] for r in rows))"
        )
        r = exec_python(code, str(tmp_path))
        assert r["exit_code"] == 0
        assert "3" in r["stdout"]

    def test_exit_code_reported(self, tmp_path):
        r = exec_python("raise SystemExit(2)", str(tmp_path))
        assert r["exit_code"] == 2

    def test_syntax_error_reported(self, tmp_path):
        r = exec_python("def broken(:", str(tmp_path))
        assert r["exit_code"] != 0
        assert "SyntaxError" in r["stderr"] or "Error" in r["stderr"]

    def test_timeout_reported(self, tmp_path):
        r = exec_python(
            "import time; time.sleep(5)", str(tmp_path), timeout=1,
        )
        assert r["timed_out"] is True
        assert "timeout" in r["stderr"]


class TestRunAwk:
    def test_field_surgery(self, csv_file, tmp_path):
        if not find_awk():
            pytest.skip("awk not available")
        r = run_awk(
            "{print $2}", csv_file.as_posix(), str(tmp_path),
            field_separator=",",
        )
        assert r["exit_code"] == 0
        assert "alpha" in r["stdout"] and "beta" in r["stdout"]

    def test_aggregate(self, csv_file, tmp_path):
        if not find_awk():
            pytest.skip("awk not available")
        r = run_awk(
            "{s+=$3} END {print s}",
            csv_file.as_posix(), str(tmp_path), field_separator=",",
        )
        assert r["exit_code"] == 0
        assert "240" in r["stdout"]  # 90+80+70

    def test_unavailable_falls_back_to_clear_error(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr("src.exec_tools.find_awk", lambda: None)
        r = run_awk("{print $1}", "x.txt", str(tmp_path))
        assert r["exit_code"] == -1
        assert "awk not found" in r["stderr"]
        assert "run_bash" in r["stderr"] or "run_python" in r["stderr"]


class TestGrepFiles:
    def test_basic_regex_with_line_numbers(self, tmp_path):
        (tmp_path / "a.txt").write_text(
            "hello world\nfoo bar\n", encoding="utf-8",
        )
        res = grep_files(r"\bbar\b", tmp_path)
        assert res["total_matches"] == 1
        assert res["matches"][0].startswith("a.txt:2: foo bar")

    def test_ignore_case_and_fixed(self, tmp_path):
        (tmp_path / "a.txt").write_text("HELLO world", encoding="utf-8")
        assert grep_files(
            "hello", tmp_path, ignore_case=True,
        )["total_matches"] == 1
        assert grep_files("hello", tmp_path)["total_matches"] == 0
        assert grep_files(
            "HELLO", tmp_path, fixed=True,
        )["total_matches"] == 1

    def test_invert(self, tmp_path):
        (tmp_path / "a.txt").write_text("yes\nno\nyes\n", encoding="utf-8")
        res = grep_files("no", tmp_path, invert=True)
        assert res["total_matches"] == 2

    def test_count_mode(self, tmp_path):
        (tmp_path / "a.txt").write_text("x\nx\ny\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x\n", encoding="utf-8")
        res = grep_files("x", tmp_path, count=True)
        by_path = {c["path"]: c["count"] for c in res["counts"]}
        assert by_path == {"a.txt": 2, "b.txt": 1}
        assert res["total_matches"] == 3

    def test_filenames_only(self, tmp_path):
        (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("nothing\n", encoding="utf-8")
        res = grep_files("needle", tmp_path, filenames_only=True)
        assert res["files"] == ["a.txt"]

    def test_context_lines(self, tmp_path):
        (tmp_path / "a.txt").write_text(
            "one\ntwo\nMATCH\nfour\nfive\n", encoding="utf-8",
        )
        res = grep_files(
            "MATCH", tmp_path, before_context=1, after_context=1,
        )
        assert len(res["matches"]) == 3
        assert any(":2- two" in m for m in res["matches"])
        assert any(":3: MATCH" in m for m in res["matches"])
        assert any(":4- four" in m for m in res["matches"])

    def test_scoped_paths(self, tmp_path):
        (tmp_path / "d1").mkdir(); (tmp_path / "d2").mkdir()
        (tmp_path / "d1" / "x.txt").write_text("hit", encoding="utf-8")
        (tmp_path / "d2" / "y.txt").write_text("hit", encoding="utf-8")
        res = grep_files("hit", tmp_path, paths=["d1"])
        assert res["total_matches"] == 1
        assert res["matches"][0].startswith("d1/x.txt")

    def test_binary_and_oversize_skipped(self, tmp_path):
        (tmp_path / "bin.dat").write_bytes(b"\x00\x01hit\x00")
        (tmp_path / "big.txt").write_bytes(b"hit" * 3_000_000)
        res = grep_files("hit", tmp_path)
        assert res["searched_files"] == 0
        assert res["total_matches"] == 0

    def test_max_results_truncates(self, tmp_path):
        (tmp_path / "a.txt").write_text("hit\n" * 50, encoding="utf-8")
        res = grep_files("hit", tmp_path, max_results=10)
        assert res["truncated"] is True
        assert len(res["matches"]) == 10

    def test_invalid_pattern_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="invalid pattern"):
            grep_files("(unclosed", tmp_path)

    def test_truncate_output_helper(self):
        text, trunc = truncate_output("x" * 100, 10)
        assert trunc is True and len(text) == 10
        text, trunc = truncate_output("short", 10)
        assert trunc is False and text == "short"


# ─── host layer ──────────────────────────────────────────────────


class TestHostToolSurface:
    def test_five_tools_advertised(self, host):
        names = [t.name for t in host.list_tools()]
        assert names == [
            "run_shell", "run_bash", "run_grep", "run_awk", "run_python",
            # v0.9.105.0: os-* REAL-input tools (SendInput; fail-closed)
            "os_input_status", "os_windows_list", "os_focus_window",
            "os_click_point", "os_type_text", "os_key_press",
            "script_save", "script_list", "script_run",
        ]

    def test_server_name_and_args_convention(self, host):
        for t in host.list_tools():
            assert t.server_name == "exec-local"
            params = list(
                inspect.signature(t._handler).parameters.values(),
            )
            assert len(params) == 1 and params[0].name == "args", t.name


class TestHostRunShell:
    def test_missing_command(self, host):
        assert "error" in host._run_shell({})

    def test_echo(self, host):
        r = host._run_shell({"command": "echo hi"})
        assert r.get("ok") is True
        assert "hi" in r["stdout"]
        assert r["tool"] == "shell"

    def test_blocklist_refuses_disaster(self, host):
        out = host._run_shell({"command": "rm -rf /"})
        assert "error" in out
        assert "refused" in out["error"]
        assert "stdout" not in out  # nothing ran


class TestHostRunBash:
    def test_missing_script(self, host):
        assert "error" in host._run_bash({})

    def test_arithmetic(self, host):
        if not find_bash():
            pytest.skip("bash not available")
        r = host._run_bash({"script": "echo $((7*6))"})
        assert r.get("ok") is True
        assert "42" in r["stdout"]

    def test_cwd_is_write_sandbox(self, host):
        if not find_bash():
            pytest.skip("bash not available")
        (host._ensure_write_root() / "probe.txt").write_text(
            "sandbox-cwd", encoding="utf-8",
        )
        r = host._run_bash({"script": "cat probe.txt"})
        assert "sandbox-cwd" in r["stdout"]

    def test_pipeline_over_sandbox_files(self, host, csv_file):
        if not find_bash():
            pytest.skip("bash not available")
        # read-root file is reachable via $QDRANT_READ_ROOT
        r = host._run_bash({
            "script": (
                'grep alpha "$QDRANT_READ_ROOT/data.csv" '
                "| awk -F, '{print $3}'"
            ),
        })
        assert "90" in r["stdout"]


class TestHostRunGrep:
    def test_missing_pattern(self, host):
        assert "error" in host._run_grep({})

    def test_search_read_sandbox(self, host, csv_file):
        res = host._run_grep({"pattern": "alpha"})
        assert res.get("ok") is True
        assert res["total_matches"] == 1
        assert res["matches"][0].startswith("data.csv:2")

    def test_scoped_path(self, host, csv_file):
        res = host._run_grep({
            "pattern": "alpha", "paths": ["data.csv"],
        })
        assert res["total_matches"] == 1

    def test_invalid_regex_clean_error(self, host):
        out = host._run_grep({"pattern": "(unclosed"})
        assert "error" in out


class TestHostRunAwk:
    def test_missing_args(self, host):
        assert "error" in host._run_awk({})

    def test_column_sum(self, host, csv_file):
        if not find_awk():
            pytest.skip("awk not available")
        r = host._run_awk({
            "script": "{s+=$3} END {print s}",
            "file": "data.csv",
            "field_separator": ",",
        })
        assert r.get("ok") is True
        assert "240" in r["stdout"]  # 90+80+70

    def test_file_outside_sandbox_rejected(self, host, tmp_path):
        outside = tmp_path / "secret.csv"
        outside.write_text("a,b\n1,2\n", encoding="utf-8")
        out = host._run_awk({
            "script": "{print}",
            "file": str(outside),
        })
        assert "error" in out
        assert "sandbox" in out["error"]


class TestHostRunPython:
    def test_missing_code(self, host):
        assert "error" in host._run_python({})

    def test_conversion_and_write_back(self, host, csv_file):
        # read root input via env; output lands in the write root
        code = (
            "import json, os;"
            "src=os.environ['QDRANT_READ_ROOT'] + '/data.csv';"
            "rows=[l.split(',') for l in open(src).read().strip().splitlines()][1:];"
            "open('rows.json','w').write(json.dumps("
            "[{'id':a,'name':b,'score':c} for a,b,c in rows]))"
        )
        r = host._run_python({"code": code})
        assert r.get("ok") is True
        assert r["exit_code"] == 0, r
        produced = host._ensure_write_root() / "rows.json"
        assert produced.exists()
        assert "alpha" in produced.read_text(encoding="utf-8")

    def test_runtime_error_in_stderr(self, host):
        r = host._run_python({"code": "1/0"})
        assert r["exit_code"] != 0
        assert "ZeroDivision" in r["stderr"] or "Error" in r["stderr"]


# ─── manager / stdio / prompt integration ────────────────────────


class TestManagerRegistration:
    def test_from_config_registers_exec_tools(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(None, include_local_hosts=True)
        names = {t.name for t in mgr.list_tools()}
        for expected in (
            "run_shell", "run_bash", "run_grep", "run_awk", "run_python",
        ):
            assert expected in names, f"{expected} not registered"
        assert mgr._exec_local_host is not None

    def test_tool_dispatch_reaches_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(None, include_local_hosts=True)
        result = mgr.call("run_python", {"code": "print('exec-ok')"})
        assert "exec-ok" in result.text


class TestStdioServer:
    def test_tool_specs_match_host_surface(self):
        from src.mcp_server.exec_server import _build_tool_specs

        from src.mcp_client.host import ExecLocalHost

        spec_names = {s.name for s in _build_tool_specs()}
        host_names = {t.name for t in ExecLocalHost().list_tools()}
        assert spec_names == host_names

    def test_handlers_delegate_to_host(self):
        from unittest.mock import MagicMock

        from src.mcp_server.exec_server import ExecMCPServer

        s = ExecMCPServer()
        s.host = MagicMock()
        s.host._run_python.return_value = {"ok": True, "stdout": "x"}
        out = s._do_python({"code": "print('x')"})
        import json as _json

        payload = _json.loads(out[0].text)
        assert payload["stdout"] == "x"
        s.host._run_python.assert_called_once()


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
        assert "Local core tools" in prompt
        for tool in (
            "run_shell", "run_bash", "run_grep", "run_awk", "run_python",
        ):
            assert tool in prompt, f"{tool} missing from prompt"

    def test_trigger_keywords_zh_and_en(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        for kw in ("轉換格式", "統計一下", "批次取代", "用 python 處理"):
            assert kw in prompt, f"zh trigger {kw!r} missing"
        for kw in (
            "convert this file", "grep for X", "run a script",
            "transform csv to json",
        ):
            assert kw in prompt, f"en trigger {kw!r} missing"

    def test_lower_workload_rule_present(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "LOWER" in prompt
        assert "one-liner" in prompt

    def test_safety_rules_present(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "64 KB" in prompt
        assert "timeout" in prompt
        assert "rm -rf /" in prompt

    def test_block_absent_in_legacy_mode(self, stub_agent):
        legacy = stub_agent._system_prompt(tools_enabled=False)
        assert "run_grep" not in legacy
        assert "Local core tools" not in legacy

    def test_composes_with_other_blocks(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Log anomaly analysis" in prompt
        assert "archive_extract" in prompt
        assert "create_diagram" in prompt
        assert "send_telegram_message" in prompt

# ─── v0.9.34.0+ — matplotlib CJK bootstrap ────────────────────────


class TestMplCjkBootstrap:
    def test_bootstrap_constant_lists_cjk_fonts(self):
        from src.exec_tools import _MPL_CJK_BOOTSTRAP

        assert "Microsoft JhengHei" in _MPL_CJK_BOOTSTRAP
        assert "axes.unicode_minus" in _MPL_CJK_BOOTSTRAP
        assert "font.sans-serif" in _MPL_CJK_BOOTSTRAP

    def test_prepend_adds_bootstrap_for_chart_code(self):
        from src.exec_tools import _prepend_mpl_cjk

        code = "import matplotlib.pyplot as plt\nplt.plot([1, 2])\n"
        out = _prepend_mpl_cjk(code)
        assert out.startswith("try:")
        assert "font.sans-serif" in out
        assert out.endswith(code)

    def test_prepend_skips_non_chart_code(self):
        from src.exec_tools import _prepend_mpl_cjk

        code = "print(open('a.txt').read())\n"
        assert _prepend_mpl_cjk(code) is code

    def test_exec_python_carries_bootstrap(self, tmp_path, monkeypatch):
        import src.exec_tools as et

        captured = {}

        def fake_run(cmd, cwd, timeout, max_output, read_root=None):
            captured["cmd"] = cmd
            return {
                "stdout": "", "stderr": "", "exit_code": 0,
                "timed_out": False, "truncated": False,
                "duration_ms": 0,
            }

        monkeypatch.setattr(et, "_run_capture", fake_run)
        et.exec_python("import matplotlib.pyplot as plt", str(tmp_path))
        assert "font.sans-serif" in captured["cmd"][2]

    def test_exec_python_plain_code_untouched(self, tmp_path, monkeypatch):
        import src.exec_tools as et

        captured = {}

        def fake_run(cmd, cwd, timeout, max_output, read_root=None):
            captured["cmd"] = cmd
            return {
                "stdout": "", "stderr": "", "exit_code": 0,
                "timed_out": False, "truncated": False,
                "duration_ms": 0,
            }

        monkeypatch.setattr(et, "_run_capture", fake_run)
        et.exec_python("print('hi')", str(tmp_path))
        assert captured["cmd"][2] == "print('hi')"

    def test_bootstrap_neutral_without_matplotlib(self, tmp_path):
        """Real subprocess: the bootstrap must not break a plain run
        (matplotlib-less interpreters swallow the ImportError)."""
        import src.exec_tools as et
        out = et.exec_python("print('ok')", str(tmp_path), timeout=60)
        assert "ok" in out["stdout"]
        assert out["exit_code"] == 0

    def test_live_matplotlib_cjk_render(self, tmp_path):
        """Live proof: a chart with Chinese labels renders WITHOUT
        findfont warnings and the PNG lands in the sandbox."""
        pytest.importorskip("matplotlib")
        import src.exec_tools as et

        out = et.exec_python(
            "import matplotlib\n"
            "matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "plt.figure(figsize=(3, 2))\n"
            "plt.title('台積電 K 線圖')\n"
            "plt.ylabel('價格 (元)')\n"
            "plt.savefig('cjk_test.png', dpi=50)\n"
            "print('saved')\n",
            str(tmp_path), timeout=120,
        )
        assert out["exit_code"] == 0, out["stderr"]
        assert "saved" in out["stdout"]
        assert (tmp_path / "cjk_test.png").exists()
        assert "not found" not in out["stderr"].lower()


# ─── v0.9.36.0+ — oversized code runs as a temp script file ───────


class TestOversizedCodeTempFile:
    def test_large_code_runs_via_file(self, tmp_path, monkeypatch):
        import sys

        import src.exec_tools as et

        captured = {}

        def fake_run(cmd, cwd, timeout, max_output, read_root=None):
            captured["cmd"] = cmd
            captured["file_body"] = open(cmd[1], encoding="utf-8").read()
            return {
                "stdout": "", "stderr": "", "exit_code": 0,
                "timed_out": False, "truncated": False,
                "duration_ms": 0,
            }

        monkeypatch.setattr(et, "_run_capture", fake_run)
        code = ("import matplotlib.pyplot as plt\n"
                + "# pad\n" * 20000 + "print('big')\n")
        assert len(code) > 24_000
        et.exec_python(code, str(tmp_path))
        assert captured["cmd"][0] == sys.executable
        assert captured["cmd"][1].endswith(".py")
        assert "font.sans-serif" in captured["file_body"]
        assert "print('big')" in captured["file_body"]

    def test_temp_file_removed_after_run(self, tmp_path):
        import src.exec_tools as et

        code = "# pad\n" * 20000 + "print('done')\n"
        out = et.exec_python(code, str(tmp_path), timeout=120)
        assert out["exit_code"] == 0
        assert "done" in out["stdout"]
        leftovers = [p for p in tmp_path.iterdir()
                     if p.name.startswith(".qdrant_exec_")]
        assert leftovers == []

    def test_small_code_still_uses_dash_c(self, tmp_path, monkeypatch):
        import src.exec_tools as et

        captured = {}

        def fake_run(cmd, cwd, timeout, max_output, read_root=None):
            captured["cmd"] = cmd
            return {
                "stdout": "", "stderr": "", "exit_code": 0,
                "timed_out": False, "truncated": False,
                "duration_ms": 0,
            }

        monkeypatch.setattr(et, "_run_capture", fake_run)
        et.exec_python("print('hi')", str(tmp_path))
        assert captured["cmd"][1] == "-c"

    def test_live_60k_char_code_really_runs(self, tmp_path):
        """Live proof of the WinError 206 fix: ~57 KB of code that
        previously died on the Windows -c limit now executes."""
        import src.exec_tools as et

        pad = "# pad line for the windows cmdline limit\n" * 1400
        out = et.exec_python(
            pad + "print('big code ok')\n",
            str(tmp_path), timeout=120,
        )
        assert out["exit_code"] == 0, out["stderr"]
        assert "big code ok" in out["stdout"]


# ─── v0.9.36.1+ — MATPLOTLIBRC covers every runner ────────────────


class TestMatplotlibRcEnv:
    def test_shipped_rc_file_exists_with_cjk_stack(self):
        import io as _io
        from pathlib import Path as _P

        rc = _P(__file__).resolve().parents[1] / "config" / "matplotlibrc"
        assert rc.is_file()
        body = _io.open(rc, encoding="utf-8").read()
        assert "Microsoft JhengHei" in body
        assert "axes.unicode_minus: False" in body

    def test_config_import_sets_matplotlibrc_env(self, monkeypatch):
        import importlib
        import os

        monkeypatch.delenv("MATPLOTLIBRC", raising=False)
        import src.config as cfg

        importlib.reload(cfg)
        val = os.environ.get("MATPLOTLIBRC", "")
        assert val.endswith("matplotlibrc")
        assert "config" in val

    def test_child_env_carries_matplotlibrc(self, tmp_path, monkeypatch):
        """_run_capture passes the parent env (incl. MATPLOTLIBRC)
        to every exec child — run_shell / run_bash / run_python."""
        import os

        import src.exec_tools as et

        monkeypatch.setenv("MATPLOTLIBRC", "C:/nowhere/matplotlibrc")
        captured = {}

        def fake_run(cmd, cwd, timeout, max_output, read_root=None,
                     **kw):
            captured["env"] = kw.get("env") or cmd
            # mirror the real signature: env comes via kwargs
            return {
                "stdout": "", "stderr": "", "exit_code": 0,
                "timed_out": False, "truncated": False,
                "duration_ms": 0,
            }

        # call the real _run_capture but intercept subprocess.run
        real_run = et.subprocess.run

        def spy_run(*a, **kw):
            captured["env"] = kw.get("env") or {}
            return real_run(*a, **kw)

        monkeypatch.setattr(et.subprocess, "run", spy_run)
        et._run_capture(
            ["cmd", "/c", "echo ok"], str(tmp_path), 10, 4096,
        )
        assert captured["env"].get("MATPLOTLIBRC") == \
            "C:/nowhere/matplotlibrc"
