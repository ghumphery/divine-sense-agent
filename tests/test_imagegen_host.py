"""Tests for the ImageGen 產圖 MCP extension (v0.9.104.0+).

OpenRouter dedicated Image API (POST /api/v1/images/generations) wrapped
as the ``imagegen-local`` in-process LocalHost + stdio mirror.

* Mock-first engine: IMAGEGEN_MODE=mock (default) NEVER touches the
  network (deterministic Pillow placeholder, simulated: true).
* Params validated (n cap / aspect_ratio / resolution / quality /
  output_format / seed); errors are envelopes, never raises.
* TWO-gate write model (hicloud/keep pattern): confirm=true (mock AND
  live); live additionally needs IMAGEGEN_WRITES_ENABLED=on.
* The engine NEVER returns base64 strings (bytes only — the LLM
  context must never carry image blobs; the HOST writes them).
* ImageGenLocalHost advertises 4 tools; from_config registers the
  imagegen-local server; the stdio mirror matches the host surface.
* agent.py: the 產圖 prompt block + the imagegen toolroute hint/class.
All network-touching paths are injected via monkeypatched
urllib.request.urlopen or engine._post_generate — no real HTTP.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    for var in ("IMAGEGEN_MODE", "IMAGEGEN_MODEL",
                "IMAGEGEN_WRITES_ENABLED", "IMAGEGEN_MAX_IMAGES",
                "IMAGEGEN_TIMEOUT_S", "IMAGEGEN_DIR",
                "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "sandbox").mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT",
                       str(tmp_path / "sandbox"))
    yield


def _engine(monkeypatch=None, mode="mock"):
    if monkeypatch is not None and mode is not None:
        monkeypatch.setenv("IMAGEGEN_MODE", mode)
    from src.imagegen_tools import ImageGenEngine

    return ImageGenEngine()


def _fake_urlopen(payload, status=200):
    class _Resp:
        def __init__(self):
            self.status = status

        def read(self):
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):  # matches urllib signature
        if status >= 400:
            err = urllib.error.HTTPError(
                req.full_url, status, "err", None,
                __import__("io").BytesIO(
                    json.dumps(payload).encode("utf-8")))
            raise err
        return _Resp()

    return fake


class TestResolvers:
    def test_default_mode_mock(self):
        from src.imagegen_tools import MODE_MOCK

        assert _engine().mode == MODE_MOCK and _engine().is_mock

    def test_mode_values(self, monkeypatch):
        from src.imagegen_tools import MODE_LIVE, MODE_MOCK

        monkeypatch.setenv("IMAGEGEN_MODE", "live")
        assert _engine().mode == MODE_LIVE
        monkeypatch.setenv("IMAGEGEN_MODE", "yolo")
        assert _engine().mode == MODE_MOCK
        monkeypatch.setenv("IMAGEGEN_MODE", "MOCK")
        assert _engine().mode == MODE_MOCK

    def test_writes_enabled_default_off(self, monkeypatch):
        assert _engine().writes_enabled is False
        monkeypatch.setenv("IMAGEGEN_MODE", "live")
        assert _engine().writes_enabled is False
        monkeypatch.setenv("IMAGEGEN_WRITES_ENABLED", "on")
        assert _engine().writes_enabled is True

    def test_max_images_clamp(self, monkeypatch):
        from src.imagegen_tools import DEFAULT_MAX_IMAGES

        assert len(str(DEFAULT_MAX_IMAGES)) >= 1
        monkeypatch.setenv("IMAGEGEN_MAX_IMAGES", "6")
        e = _engine()
        n, err = e._norm_n("6")
        assert (n, err) == (6, "")
        n, err = e._norm_n("99")
        assert n == 0 and "上限" in err
        monkeypatch.setenv("IMAGEGEN_MAX_IMAGES", "bogus")
        assert _engine()._norm_n("1") == (1, "")

    def test_timeout_clamp(self, monkeypatch):
        from src.imagegen_tools import _timeout_s

        monkeypatch.setenv("IMAGEGEN_TIMEOUT_S", "60")
        assert _timeout_s() == 60.0
        monkeypatch.setenv("IMAGEGEN_TIMEOUT_S", "bogus")
        assert _timeout_s() == 120.0
        monkeypatch.setenv("IMAGEGEN_TIMEOUT_S", "5")
        assert _timeout_s() >= 30.0

    def test_dir_name_default_and_override(self, monkeypatch):
        from src.imagegen_tools import _dir_name

        assert _dir_name() == "images"
        monkeypatch.setenv("IMAGEGEN_DIR", "generated")
        assert _dir_name() == "generated"
        monkeypatch.setenv("IMAGEGEN_DIR", "../evil")
        assert _dir_name() == "images"

    def test_default_model_env(self, monkeypatch):
        from src.imagegen_tools import DEFAULT_MODEL, _default_model

        assert _default_model() == DEFAULT_MODEL
        monkeypatch.setenv("IMAGEGEN_MODEL", "recraft/recraft-v4.1-flash")
        assert _default_model() == "recraft/recraft-v4.1-flash"


class TestMockGenerate:
    """mock mode = deterministic Pillow placeholder, NO network."""

    def test_mock_generate_placeholder(self, monkeypatch):
        e = _engine(monkeypatch)
        out = e.generate("a red circle", confirm=True)
        assert out["ok"] is True and out["mode"] == "mock"
        assert out["simulated"] is True
        imgs = out["images"]
        assert len(imgs) == 1
        assert bytes(imgs[0]["bytes"][:8]) == PNG_MAGIC
        assert imgs[0]["ext"] == ".png"
        assert out["model"]  # default model echoed
        assert "placeholder" in out["note"]

    def test_confirm_gate_even_in_mock(self, monkeypatch):
        e = _engine(monkeypatch)
        r = e.generate("anything", confirm=None)
        assert "error" in r and "confirm=true" in r["error"]
        r = e.generate("anything", confirm=False)
        assert "error" in r
        r = e.generate("anything", confirm="true")
        assert r["ok"] is True

    def test_n_and_caps(self, monkeypatch):
        e = _engine(monkeypatch)
        out = e.generate("x", confirm=True, n=3)
        assert len(out["images"]) == 3
        assert out["n"] >= 1
        assert "超過上限" in e.generate("x", confirm=True, n=99)["error"]
        assert "整數" in e.generate("x", confirm=True, n="abc")["error"]

    def test_param_validation(self, monkeypatch):
        e = _engine(monkeypatch)
        cases = (
            ({"aspect_ratio": "wide"}, "aspect_ratio"),
            ({"resolution": "7K"}, "resolution"),
            ({"quality": "max"}, "quality"),
            ({"output_format": "bmp"}, "output_format"),
            ({"seed": "abc"}, "seed"),
        )
        for extra, needle in cases:
            out = e.generate("x", confirm=True, **extra)
            needle_map = {"aspect_ratio": "aspect_ratio",
                          "resolution": "resolution",
                          "quality": "quality",
                          "output_format": "output_format",
                          "seed": "seed"}
            assert needle_map[needle] in out["error"], extra
        # tolerance paths
        ok = e.generate("x", confirm=True, aspect_ratio=" 16 : 9 ")
        assert ok["ok"] is True
        ok2 = e.generate("x", confirm=True, output_format="JPG")
        assert ok2["ok"] is True
        ok3 = e.generate("x", confirm=True, resolution="1k")
        assert ok2["ok"] is True

    def test_prompt_required(self):
        from src.imagegen_tools import MODE_MOCK

        e = _engine()
        out = e.generate("", confirm=True)
        assert "error" in out and "prompt 必填" in out["error"]
        assert out["mode"] == MODE_MOCK

    def test_mock_never_returns_base64(self, monkeypatch):
        e = _engine(monkeypatch)
        out = e.generate("b64-free test", confirm=True)
        blob = json.dumps(out, default=repr)
        assert "b64_json" not in blob

    def test_sanitized_filename(self, monkeypatch):
        from src.imagegen_tools import ImageGenEngine

        f = ImageGenEngine.sanitized_filename
        assert f("My Logo.png", ".png") == "My Logo.png"
        # wrong ext replaced by the REAL media ext (webp stays webp)
        r = f("cover.jpg", ".webp")
        assert r == "cover.webp"
        r2 = f('..\\..\\evil:name*.webp', ".svg")
        assert "/" not in r2 and "\\" not in r2 and ":" not in r2
        assert r2.endswith(".svg")
        r3 = f("", ".png")
        assert r3.startswith("img-") and r3.endswith(".png")
        r4 = f("bad:name*", ".png")
        assert r4.endswith(".png") and ":" not in r4


class TestLiveGates:
    def test_live_without_writes_switch(self, monkeypatch):
        from src.imagegen_tools import ImageGenEngine

        monkeypatch.setenv("IMAGEGEN_MODE", "live")
        monkeypatch.delenv("IMAGEGEN_WRITES_ENABLED", raising=False)
        e = ImageGenEngine()
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        out = e.generate("x", confirm=True)
        assert "error" in out
        assert "IMAGEGEN_WRITES_ENABLED=on" in out["error"]

    def test_live_needs_key(self, monkeypatch):
        from src.imagegen_tools import ImageGenEngine

        monkeypatch.setenv("IMAGEGEN_MODE", "live")
        monkeypatch.setenv("IMAGEGEN_WRITES_ENABLED", "on")
        e = ImageGenEngine()
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        out = e.generate("x", confirm=True)
        assert "error" in out and "OPENROUTER_API_KEY" in out["error"]

    def test_live_passes_gate_to_post(self, monkeypatch):
        from src.imagegen_tools import ImageGenEngine

        monkeypatch.setenv("IMAGEGEN_MODE", "live")
        monkeypatch.setenv("IMAGEGEN_WRITES_ENABLED", "on")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        e = ImageGenEngine()
        captured = {}

        def fake_post(body):
            captured["body"] = dict(body)
            return {"ok": True, "mode": "live", "model": body["model"],
                    "images": [{"bytes": PNG_MAGIC, "media_type":
                                "image/png", "ext": ".png"}],
                    "usage": {"cost": 0.0}}

        monkeypatch.setattr(e, "_post_generate", fake_post)
        out = e.generate("黑熊 插畫", confirm=True, n=2,
                     aspect_ratio="16:9", output_format="png",
                     seed=7)
        assert out["ok"] is True
        b = captured["body"]
        assert b["model"]
        assert b["n"] == 2 and b["aspect_ratio"] == "16:9"
        assert b["seed"] == 7 and b["output_format"] == "png"

    def test_mock_skips_post_even_with_key(self, monkeypatch):
        e = _engine(monkeypatch, mode="mock")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setattr(
            e, "_post_generate",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError(
                "mock must not post")))
        assert e.generate("x", confirm=True)["ok"] is True


class TestLiveHttp:
    """Injected urlopen — the REAL parse/extension/error paths."""

    def _live_engine(self, monkeypatch, writes=True):
        """env BEFORE construction — the engine resolves mode/writes in
        __init__ (keep/jev 同規則；先建後 setenv 無效)."""
        if writes:
            monkeypatch.setenv("IMAGEGEN_WRITES_ENABLED", "on")
        else:
            monkeypatch.delenv("IMAGEGEN_WRITES_ENABLED", raising=False)
        e = _engine(monkeypatch, mode="live")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        return e

    def test_generation_response_parsed(self, monkeypatch):
        e = self._live_engine(monkeypatch)
        payload = {
            "created": 1790670554,
            "data": [{"b64_json": base64.b64encode(PNG_MAGIC + b"x" * 8)
                      .decode("ascii"), "media_type": "image/webp"}],
            "usage": {"total_tokens": 4188, "cost": 0.007},
        }
        monkeypatch.setattr(urllib.request, "urlopen",
                            _fake_urlopen(payload))
        out = e.generate("circle", confirm=True)
        assert out["ok"] is True and out["mode"] == "live"
        img = out["images"][0]
        assert img["media_type"] == "image/webp" and img["ext"] == ".webp"
        assert bytes(img["bytes"][:8]) == PNG_MAGIC
        assert out["usage"]["cost"] == 0.007
        assert out["created"] == 1790670554

    def test_http_error_envelope(self, monkeypatch):
        e = self._live_engine(monkeypatch)
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(
            {"error": {"message": "bad params"}}, 400))
        out = e.generate("x", confirm=True)
        assert "error" in out and "HTTP 400" in out["error"]
        assert "bad params" in out["error"]

    def test_missing_data_envelope(self, monkeypatch):
        e = self._live_engine(monkeypatch)
        monkeypatch.setattr(urllib.request, "urlopen",
                            _fake_urlopen({"created": 1}))
        out = e.generate("x", confirm=True)
        assert "error" in out and "data" in out["error"]

    def test_network_error_envelope(self, monkeypatch):
        e = self._live_engine(monkeypatch)

        def boom(req, timeout=None):
            raise OSError("connection reset")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        out = e.generate("x", confirm=True)
        assert "error" in out and "呼叫失敗" in out["error"]

    def test_models_discovery_live(self, monkeypatch):
        e = self._live_engine(monkeypatch)
        payload = {"data": [
            {"id": "a/model-1", "name": "A1", "description": "d" * 300},
            {"id": "", "name": "skip"},  # no id → skipped
        ]}
        monkeypatch.setattr(urllib.request, "urlopen",
                            _fake_urlopen(payload))
        out = e.models_discovery()
        assert out["mode"] == "live" and out["count"] == 1
        assert out["models"][0]["id"] == "a/model-1"
        assert len(out["models"][0]["description"]) == 100

    def test_models_discovery_error(self, monkeypatch):
        e = self._live_engine(monkeypatch)
        monkeypatch.setattr(urllib.request, "urlopen",
                            _fake_urlopen({}, 500))
        assert "error" in e.models_discovery()


class TestImageGenLocalHost:
    def _host(self):
        from src.mcp_client.host import ImageGenLocalHost

        return ImageGenLocalHost()

    def test_four_tools_exposed(self):
        host = self._host()
        tools = host.list_tools()
        assert [t.name for t in tools] == [
            "image_generate", "image_gen_list", "image_gen_models",
            "image_gen_status"]
        assert all(t.server_name == "imagegen-local" for t in tools)
        schemas = [t.input_schema for t in tools]
        assert "confirm" in schemas[0]["properties"]

    def test_generate_writes_sandbox(self, monkeypatch, tmp_path):
        host = self._host()
        out = host._generate({"prompt": "黑熊插畫", "confirm": True})
        assert out["ok"] is True and out["n"] == 1
        p = out["files"][0]
        assert p["path"].endswith(".png")
        assert (tmp_path / "sandbox" / "images" / p["name"]).exists()
        assert "📷 圖片已生成：" in out["display"]
        assert out["display"].endswith(p["path"])

    def test_generate_confirm_gate_writes_nothing(self, monkeypatch,
                                                  tmp_path):
        host = self._host()
        out = host._generate({"prompt": "x"})
        assert "error" in out
        assert not (tmp_path / "sandbox" / "images").exists()

    def test_generate_filename_sanitized(self, monkeypatch, tmp_path):
        host = self._host()
        out = host._generate({"prompt": "x", "confirm": True,
                              "filename": "..\\..\\我的 海報!.png"})
        assert out["ok"] is True
        assert (tmp_path / "sandbox") in Path(
            out["files"][0]["path"]).parents
        name = Path(out["files"][0]["path"]).name
        assert name.endswith(".png")
        assert ".." not in name and ":" not in name and "*" not in name

    def test_generate_collision_versioning(self, monkeypatch):
        host = self._host()
        r1 = host._generate({"prompt": "x", "confirm": True,
                             "filename": "same.png"})
        r2 = host._generate({"prompt": "x", "confirm": True,
                             "filename": "same.png"})
        names = [Path(f["path"]).name
                 for f in (r1["files"] + r2["files"])]
        assert len(set(names)) == 2  # -2 suffix versioning
        assert any("-2" in n for n in names)

    def test_live_generate_reports_cost(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IMAGEGEN_MODE", "live")
        monkeypatch.setenv("IMAGEGEN_WRITES_ENABLED", "on")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        host = self._host()
        monkeypatch.setattr(host.engine, "_post_generate", lambda body: {
            "ok": True, "mode": "live", "model": body["model"],
            "images": [{"bytes": PNG_MAGIC, "media_type": "image/jpeg",
                        "ext": ".jpg"}],
            "usage": {"cost": 0.021}, "elapsed_s": 3.2})
        out = host._generate({"prompt": "x", "confirm": True,
                              "filename": "r.png"})
        assert out["ok"] is True
        assert out["files"][0]["name"].endswith(".jpg")  # ext from media
        assert out["usage_cost"] == 0.021
        assert out["simulated"] is False

    def test_list_status_models(self, monkeypatch):
        host = self._host()
        host._generate({"prompt": "x", "confirm": True,
                        "filename": "a.png"})
        lst = host._list({})
        assert lst["count"] == 1 and lst["files"][0]["name"] == "a.png"
        st = host._status({})
        assert st["mode"] == "mock" and st["max_images"] == 4
        assert st["images_count"] == 1
        assert st["key_ready"] is False
        mdl = host._models({})
        assert mdl["ok"] is True and mdl["count"] >= 5
        ids = [m["id"] for m in mdl["models"]]
        assert "google/gemini-2.5-flash-image" in ids


class TestManagerWiring:
    def test_from_config_registers_imagegen_host(self, tmp_path,
                                                 monkeypatch):
        monkeypatch.setenv("IMAGEGEN_MODE", "mock")
        from src.mcp_client.manager import McpManager

        cfg = tmp_path / "mcp.json"
        cfg.write_text('{"servers": []}', encoding="utf-8")
        mgr = McpManager.from_config(str(cfg))
        names = [t.name for t in mgr.list_tools()
                 if t.name.startswith("image_gen")]
        assert "image_generate" in names and len(names) >= 4
        assert (mgr._imagegen_local_host.SERVER_NAME
                == "imagegen-local")


class TestStdioParity:
    def test_specs_match_host(self):
        from src.mcp_client.host import ImageGenLocalHost
        from src.mcp_server.image_gen_server import ImageGenMCPServer

        server = ImageGenMCPServer()
        host_names = [t.name for t in ImageGenLocalHost().list_tools()]
        server_names = [t.name for t in server._tool_specs()]
        assert server_names == host_names

    def test_handler_map_complete(self):
        from src.mcp_server.image_gen_server import ImageGenMCPServer

        specs = {t.name for t in ImageGenMCPServer()._tool_specs()}
        assert specs == {"image_generate", "image_gen_list",
                         "image_gen_models", "image_gen_status"}


class TestSystemPrompt:
    def _agent(self):
        from src.agent.agent import Agent

        a = Agent()
        a._client = MagicMock()
        a._model = "stub-model"
        a._provider = "mock"
        return a

    def test_imagegen_block_present_with_tools(self):
        prompt = self._agent()._system_prompt(tools_enabled=True)
        assert "image_generate" in prompt
        assert "image_gen_models" in prompt
        assert "imagegen-local" in prompt
        assert "產圖" in prompt

    def test_imagegen_block_absent_legacy(self):
        prompt = self._agent()._system_prompt(tools_enabled=False)
        assert "image_generate" not in prompt


class TestToolrouteHint:
    """v0.9.104.0 — the 產圖 union (hint + image class)."""

    def test_hint_detector(self):
        import src.agent.agent as A

        assert A._toolroute_imagegen_hint("幫我畫一張台灣黑熊插圖")
        assert A._toolroute_imagegen_hint("產一張台灣地圖的插畫")
        assert A._toolroute_imagegen_hint("幫我做一張公司 logo 海報")
        assert A._toolroute_imagegen_hint("please generate an image")
        assert A._toolroute_imagegen_hint("畫一個可愛的貓")
        assert not A._toolroute_imagegen_hint("現在幾點")
        assert not A._toolroute_imagegen_hint("描述這張圖片內容")
        assert not A._toolroute_imagegen_hint("")
        # diagram family must NOT union the imagegen tools
        assert not A._toolroute_imagegen_hint("用 drawio 畫流程圖表")
        assert not A._toolroute_imagegen_hint("畫一張架構 diagram")

    def test_image_class_maps_server(self):
        import src.agent.agent as A

        srvs, cls = A._toolroute_servers_for("image")
        assert "imagegen-local" in srvs
        assert cls == ["image"]

    def test_hint_unions_regardless_of_class(self):
        import src.agent.agent as A

        srvs, cls = A._toolroute_servers_for(
            "stock", "幫我畫一張產品示意圖然後查 2330")
        assert "stock-local" in srvs
        assert "imagegen-local" in srvs
        assert cls == ["stock"]
        srvs2, _ = A._toolroute_servers_for("stock")
        assert "imagegen-local" not in srvs2

    def test_instructions_mention_image_class(self):
        src = open(ROOT / "src" / "agent" / "agent.py",
                   encoding="utf-8").read()
        assert "image=產圖/文生圖" in src
        assert src.count('_toolroute_imagegen_hint(question)'), src
        # v0.9.104.1 — the chat site threads prev_reply; ask stays 2-arg.
        assert src.count("_toolroute_servers_for(_tcls, question)") == 1
        assert src.count("prev_reply=_prev_reply") == 2