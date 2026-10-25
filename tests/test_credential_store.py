"""Tests for the central credential store (v0.9.39.0+).

One JSON store (<QDRANT_RAG_HOME>/credentials.json, atomic writes)
shared by THREE interfaces — MCP tools (CredLocalHost), the GUI
Credentials tab (gui/api.py bridge) and the CLI (cli.py creds …) —
and consumed by services (ONVIF favorites reference sets via their
``cred_set`` field). Mock-driven, no real network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture()
def write_root(tmp_path, monkeypatch):
    root = tmp_path / "write"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(root))
    return root


class TestStore:
    def test_add_roundtrip_masked(self, store_env):
        from src.credential_store import CredentialStore

        s = CredentialStore()
        out = s.add("front", username="admin",
                    password_env="ONVIF_FRONT_PW", service="onvif")
        assert out["ok"] is True and out["source"] == "env"
        listed = s.list()
        assert listed[0]["username_masked"] == "ad***"
        assert listed[0]["credential_masked"] == "env:ONVIF_FRONT_PW"
        blob = json.dumps(listed)
        assert "ad***" in blob  # masked username visible

    def test_add_requires_secret(self, store_env):
        from src.credential_store import CredentialStore

        assert "error" in CredentialStore().add("x")

    def test_duplicate_needs_overwrite(self, store_env):
        from src.credential_store import CredentialStore

        s = CredentialStore()
        s.add("a", password="p1")
        dup = s.add("a", password="p2")
        assert "error" in dup and "overwrite" in dup["error"]
        ok = s.add("a", password="p2", overwrite=True)
        assert ok["ok"] is True
        assert s.resolve("a")["password"] == "p2"

    def test_resolve_env_wins_and_missing(self, store_env, monkeypatch):
        from src.credential_store import CredentialStore

        s = CredentialStore()
        s.add("e", username="u", password="backup",
              password_env="MISSING_PW")
        out = s.resolve("e")
        assert "error" in out and "MISSING_PW" in out["error"]
        monkeypatch.setenv("MISSING_PW", "live-secret")
        out = s.resolve("e")
        assert out["password"] == "live-secret"
        assert out["source"] == "env"

    def test_resolve_b64_and_unknown(self, store_env):
        from src.credential_store import CredentialStore

        s = CredentialStore()
        s.add("b", username="u", password="plain")
        assert s.resolve("b")["password"] == "plain"
        assert "error" in s.resolve("nope")

    def test_remove_and_service_filter(self, store_env):
        from src.credential_store import CredentialStore

        s = CredentialStore()
        s.add("onvif1", password="p", service="onvif")
        s.add("misc", password="p", service="generic")
        assert s.remove("onvif1") is True
        assert s.list(service="onvif") == []
        assert s.list(service="generic")[0]["name"] == "misc"

    def test_corrupt_recovers_and_atomic(self, store_env):
        from src.credential_store import CredentialStore

        s = CredentialStore()
        s.add("x", password="p")
        s.path.write_text("{corrupt", encoding="utf-8")
        assert s.load() == {"version": 1, "credentials": []}
        s.add("y", password="p")
        assert not s.path.with_suffix(".tmp").exists()


class TestCredLocalHost:
    def _host(self):
        from src.mcp_client.host import CredLocalHost

        return CredLocalHost()

    def test_five_tools_advertised(self):
        names = [t.name for t in self._host().list_tools()]
        assert names == [
            "cred_add", "cred_list", "cred_get", "cred_remove",
            "cred_resolve",
        ]

    def test_add_list_get_remove(self, store_env, monkeypatch):
        monkeypatch.setenv("CAM_PW", "s1")
        h = self._host()
        assert h._add({
            "name": "cam", "username": "admin",
            "password_env": "CAM_PW", "service": "onvif",
        })["ok"] is True
        listed = h._list({"service": "onvif"})
        assert listed["count"] == 1
        blob = json.dumps(listed)
        assert "s1" not in blob  # the plain secret never leaks
        got = h._get({"name": "cam"})
        assert got["credential"]["env_var_set"] is True
        resolved = h._resolve({"name": "cam"})
        assert resolved["source"] == "env"
        assert "s1" not in json.dumps(resolved)
        assert h._remove({"name": "cam"})["ok"] is True

    def test_resolve_missing_env_is_error(self, store_env):
        h = self._host()
        h._add({"name": "x", "password_env": "NOPE_PW"})
        out = h._resolve({"name": "x"})
        assert "error" in out and "NOPE_PW" in out["error"]


class TestOnvifCredSetIntegration:
    def test_favorite_with_cred_set_resolves(self, store_env,
                                             monkeypatch, write_root):
        from src.mcp_client.host import CredLocalHost, OnvifLocalHost

        monkeypatch.setenv("CAM_PW", "live-secret")
        CredLocalHost()._add({
            "name": "front_cam", "username": "admin",
            "password_env": "CAM_PW", "service": "onvif",
        })
        host = OnvifLocalHost()
        host._favorite_add({
            "name": "front", "ip": "192.168.1.50",
            "cred_set": "front_cam",
        })
        listed = host._favorite_list({})
        assert listed["favorites"][0]["cred_set"] == "front_cam"
        device, creds, err = host._device_and_creds("front")
        assert err is None
        assert creds["source"] == "cred_set"
        assert creds["password"] == "live-secret"

    def test_favorite_missing_cred_set_is_error(self, store_env,
                                                write_root):
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        host._favorite_add({
            "name": "front", "ip": "192.168.1.50",
            "cred_set": "nope_set",
        })
        _, _, err = host._device_and_creds("front")
        assert err is not None
        assert "nope_set" in err["error"]


class TestRegistrationPromptGui:
    def test_manager_registers_cred_host(self, tmp_path):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        names = {t.name for t in mgr.list_tools()}
        assert "cred_add" in names and "cred_resolve" in names
        assert mgr._cred_local_host is not None
        assert mgr._cred_local_host.SERVER_NAME == "cred-local"
        mgr.close_all()

    def test_prompt_mentions_cred(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        a._mcp_manager = None
        prompt = a._system_prompt(tools_enabled=True)
        assert "cred_add" in prompt

    def test_gui_api_bridge(self, store_env):
        from gui.api import Api

        api = Api.__new__(Api)
        saved = api.save_credential({
            "name": "ui_cam", "username": "admin",
            "password": "plain-secret-42", "service": "onvif",
        })
        assert saved["status"] == "ok"
        listed = api.list_credentials("onvif")
        assert listed["count"] == 1
        blob = json.dumps(listed)
        # the plain secret AND its b64 form never leak through the mask
        assert "plain-secret-42" not in blob
        import base64 as _b

        assert _b.b64encode(b"plain-secret-42").decode() not in blob
        assert api.remove_credential("ui_cam")["status"] == "ok"

    def test_cli_handler_masked(self, store_env, capsys):
        from argparse import Namespace

        import cli

        cli._handle_creds(Namespace(creds_cmd="add", name="c1",
                                    username="admin", password="p1",
                                    password_env="", service="onvif",
                                    notes="", overwrite=False))
        cli._handle_creds(Namespace(creds_cmd="list", service=""))
        out = capsys.readouterr().out
        assert "p1" not in out and "ad***" in out