"""Tests for the ONVIF camera MCP tools (v0.9.37.0+, Phase A).

Mock-driven — no real camera, no LAN traffic: the HTTP layer
(httpx.post / httpx.get) and the UDP socket are monkeypatched.
Covers: credentials resolution (env / b64 / none / env-wins),
masking, FavoriteStore CRUD + atomic writes + corruption recovery,
SOAP envelope shape (WS-Security Digest), ProbeMatch parsing,
snapshot flow (auth fallback / oversize / bad magic / sandbox
traversal), host tool surface, stdio parity, prompt mentions.
"""
from __future__ import annotations

import base64
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import src.onvif_tools as ot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def fav_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture()
def write_root(tmp_path, monkeypatch):
    root = tmp_path / "write"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(root))
    return root


JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 32


# ─── credentials ─────────────────────────────────────────────────


class TestCredentials:
    def test_env_source_wins(self, monkeypatch):
        from src.onvif_tools import resolve_credentials

        monkeypatch.setenv("ONVIF_PW", "secret1")
        dev = {"credentials": {
            "username": "admin", "password_b64": "xxxx",
            "password_env": "ONVIF_PW",
        }}
        out = resolve_credentials(dev)
        assert out["password"] == "secret1" or out["source"] == "env"
        assert out["source"] == "env"

    def test_env_missing_is_error(self, monkeypatch):
        from src.onvif_tools import resolve_credentials

        monkeypatch.delenv("ONVIF_NOPE", raising=False)
        dev = {"credentials": {"username": "admin",
                               "password_env": "ONVIF_NOPE"}}
        out = resolve_credentials(dev)
        assert "error" in out
        assert "ONVIF_NOPE" in out["error"]

    def test_b64_roundtrip(self):
        from src.onvif_tools import resolve_credentials

        dev = {"credentials": {
            "username": "admin",
            "password_b64": base64.b64encode(b"pw123").decode(),
        }}
        out = resolve_credentials(dev)
        assert out["password"] == "pw123"
        assert out["source"] == "b64"

    def test_none_credentials(self):
        from src.onvif_tools import resolve_credentials

        out = resolve_credentials({"credentials": {"username": "x"}})
        assert out["source"] == "none"
        assert not out.get("error")

    def test_masks_never_leak(self):
        from src.onvif_tools import mask_password, mask_username

        assert mask_username("admin") == "ad***"
        assert mask_password("secret123") == "******** (9)"
        assert "secret" not in mask_password("secret123")


class TestWsSecurity:
    def test_wsse_header_contains_digest_fields(self):
        from src.onvif_tools import build_wsse_header

        h = build_wsse_header("admin", "pw")
        assert "wsse:UsernameToken" in h
        assert "<wsse:Username>admin</wsse:Username>" in h
        assert "PasswordDigest" in h
        assert "wsse:Nonce" in h
        assert "wsu:Created" in h

    def test_soap_envelope_wraps_body_and_header(self):
        from src.onvif_tools import build_wsse_header, soap_envelope

        env = soap_envelope(
            "<tds:GetDeviceInformation/>",
            build_wsse_header("u", "p"),
        )
        assert "s:Envelope" in env and "s:Body" in env
        assert "GetDeviceInformation" in env


class TestProbeParsing:
    FIXTURE = (
        '<?xml version="1.0"?><s:Envelope '
        'xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
        "<s:Body><d:ProbeMatches>"
        "<d:ProbeMatch>"
        "<d:XAddrs>http://192.168.1.50:8000/onvif/device_service"
        "</d:XAddrs>"
        "<d:Scopes>onvif://www.onvif.org/name/FrontDoor "
        "onvif://www.onvif.org/Profile/Streaming</d:Scopes>"
        "<d:Types>dn:NetworkVideoTransmitter</d:Types>"
        "</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
    )

    def test_parse_probe_match(self):
        from src.onvif_tools import parse_probe_matches

        out = parse_probe_matches(self.FIXTURE)
        assert len(out) == 1
        assert out[0]["ip"] == "192.168.1.50"
        assert out[0]["port"] == 8000
        assert "FrontDoor" in out[0]["name_hint"]

    def test_garbage_returns_empty(self):
        from src.onvif_tools import parse_probe_matches

        assert parse_probe_matches("not xml") == []


# ─── FavoriteStore ───────────────────────────────────────────────


class TestFavoriteStore:
    def test_add_and_list_masks(self, fav_env):
        from src.onvif_tools import FavoriteStore

        s = FavoriteStore()
        out = s.add("front", "192.168.1.50", 8000, username="admin",
                    password="pw1")
        assert out["ok"] is True
        listed = s.list(masked=True)
        assert listed[0]["credential_source"] == "b64"
        raw = json.dumps(listed)
        assert "pw123" not in raw
        assert "pw" + "123" not in json.dumps(s.load()["devices"])

    def test_password_env_preferred_in_storage(self, fav_env,
                                               monkeypatch):
        from src.onvif_tools import FavoriteStore

        s = FavoriteStore()
        s.add("g", "10.0.0.2", password_env="ONVIF_G_PW")
        raw = s.path.read_text(encoding="utf-8")
        assert "ONVIF_G_PW" in raw
        assert base64.b64encode(b"pw").decode() not in raw

    def test_overwrite_semantics(self, fav_env):
        from src.onvif_tools import FavoriteStore

        s = FavoriteStore()
        s.add("a", "1.1.1.1")
        dup = s.add("a", "2.2.2.2")
        assert "error" in dup
        ok = s.add("a", "2.2.2.2", overwrite=True)
        assert ok["ok"] is True
        assert s.get("a")["ip"] == "2.2.2.2"

    def test_remove_and_missing(self, fav_env):
        from src.onvif_tools import FavoriteStore

        s = FavoriteStore()
        s.add("x", "1.1.1.1")
        assert s.remove("x") is True
        assert s.remove("x") is False

    def test_corrupt_file_recovers(self, fav_env):
        from src.onvif_tools import FavoriteStore

        s = FavoriteStore()
        s.add("x", "1.1.1.1")
        s.path.write_text("{corrupt", encoding="utf-8")
        assert s.load() == {"version": 1, "devices": []}

    def test_atomic_write_leaves_no_tmp(self, fav_env):
        from src.onvif_tools import FavoriteStore

        s = FavoriteStore()
        s.add("x", "1.1.1.1")
        assert not s.path.with_suffix(".tmp").exists()


# ─── snapshot helpers ────────────────────────────────────────────


class TestSnapshotHelpers:
    def test_resolve_path_default_uploads(self, write_root):
        from src.onvif_tools import resolve_snapshot_path

        p = resolve_snapshot_path("", write_root)
        assert p.parent.name == "uploads"
        assert p.suffix == ".jpg"
        assert str(p).startswith(str(write_root))

    def test_traversal_refused(self, write_root, tmp_path):
        from src.onvif_tools import OnvifError, resolve_snapshot_path

        with pytest.raises(OnvifError):
            resolve_snapshot_path("../../evil.jpg", write_root)

    def test_forced_jpg_suffix(self, write_root):
        from src.onvif_tools import resolve_snapshot_path

        p = resolve_snapshot_path("shot.png", write_root)
        assert p.suffix == ".jpg"

    def test_fetch_snapshot_digest_then_ok(self, monkeypatch):
        import httpx

        from src.onvif_tools import fetch_snapshot

        calls = []

        class Resp:
            status_code = 200
            content = JPEG

        def fake_get(uri, auth=None, timeout=None, follow_redirects=True):
            calls.append(auth)
            return Resp()

        monkeypatch.setattr(httpx, "get", fake_get)
        body, mime = fetch_snapshot("http://x/jpg", "u", "p")
        assert mime == "image/jpeg" and body.startswith(JPEG[:3])

    def test_fetch_snapshot_401_then_500(self, monkeypatch):
        import httpx

        from src.onvif_tools import OnvifError, fetch_snapshot

        class Resp:
            status_code = 500
            content = b""

        monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
        with pytest.raises(OnvifError):
            fetch_snapshot("http://x", "u", "p")

    def test_fetch_snapshot_oversize(self, monkeypatch):
        import httpx

        from src.onvif_tools import OnvifError, fetch_snapshot

        class Resp:
            status_code = 200
            content = JPEG + b"\0" * 100

        monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
        with pytest.raises(OnvifError):
            fetch_snapshot("http://x", "", "", max_bytes=8)

    def test_fetch_snapshot_non_jpeg_magic(self, monkeypatch):
        import httpx

        from src.onvif_tools import OnvifError, fetch_snapshot

        class Resp:
            status_code = 200
            content = b"<html>not an image</html>"

        monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
        with pytest.raises(OnvifError):
            fetch_snapshot("http://x", "", "")


class TestOnvifLocalHost:
    def _host(self):
        from src.mcp_client.host import OnvifLocalHost

        return OnvifLocalHost()

    def test_eighteen_tools_advertised(self):
        names = [t.name for t in self._host().list_tools()]
        assert names == [
            "onvif_discover", "onvif_favorite_add", "onvif_favorite_list",
            "onvif_favorite_remove", "onvif_test_credentials",
            "onvif_get_profiles", "onvif_get_snapshot",
            "onvif_record", "onvif_rtsp_snapshot", "onvif_get_resources",
            "onvif_ptz_move", "onvif_ptz_stop",
            "onvif_get_presets", "onvif_goto_preset",
            "onvif_get_events",
            "onvif_watch_start", "onvif_watch_list", "onvif_watch_stop",
        ]

    def test_discover_mocked_socket(self, monkeypatch):
        import socket

        host = self._host()

        class FakeSock:
            calls = 0

            def settimeout(self, t):
                pass

            def setsockopt(self, *a):
                pass

            def sendto(self, data, addr):
                pass

            def recvfrom(self, n):
                type(self).calls += 1
                if type(self).calls > 1:
                    raise socket.timeout()
                return (
                    TestProbeParsing.FIXTURE.encode("utf-8"),
                    ("1.2.3.4", 3702),
                )

            def close(self):
                pass

        import src.onvif_tools as ot

        monkeypatch.setattr(ot.socket, "socket",
                            lambda *a, **k: FakeSock())
        out = host._discover({})
        assert out["count"] == 1
        assert out["devices"][0]["ip"] == "192.168.1.50"

    def test_favorite_flow_masked(self, fav_env):
        host = self._host()
        added = host._favorite_add({
            "name": "front", "ip": "192.168.1.50", "port": 8000,
            "username": "admin", "password": "secret-pw",
        })
        assert added["ok"] is True
        listed = host._favorite_list({})
        entry = listed["favorites"][0]
        assert entry["credential_source"] == "b64"
        blob = json.dumps(listed)
        assert "secret-pw" not in blob
        assert "admin" not in blob
        removed = host._favorite_remove({"name": "front"})
        assert removed["ok"] is True

    def test_snapshot_unknown_device(self, write_root):
        out = self._host()._get_snapshot({"name": "ghost"})
        assert "error" in out

    def test_snapshot_unresolvable_creds(self, fav_env, write_root,
                                         monkeypatch):
        host = self._host()
        host._favorite_add({
            "name": "cam", "ip": "1.2.3.4",
            "password_env": "ONVIF_MISSING_PW",
        })
        monkeypatch.delenv("ONVIF_MISSING_PW", raising=False)
        out = host._get_snapshot({"name": "cam"})
        assert "error" in out
        assert "ONVIF_MISSING_PW" in out["error"]

    def test_snapshot_happy_path(self, fav_env, write_root, monkeypatch):
        """Full flow with the ONVIF SOAP + HTTP layers mocked."""
        import xml.etree.ElementTree as ET

        host = self._host()
        host._favorite_add({
            "name": "cam", "ip": "1.2.3.4",
            "xaddr": "http://1.2.3.4:8000/onvif/device_service",
        })

        def fake_post_soap(url, body, username="", password="",
                           timeout=10.0):
            TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'
            if "GetProfiles" in body:
                xml = (
                    '<trt:GetProfilesResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                    + TT + ">"
                    '<trt:Profiles token="prof1">'
                    "<trt:Name>Profile 1</trt:Name>"
                    "<trt:VideoEncoderConfiguration>"
                    "<tt:Resolution><tt:Width>1920</tt:Width>"
                    "<tt:Height>1080</tt:Height></tt:Resolution>"
                    "<tt:FrameRateLimit>30</tt:FrameRateLimit>"
                    "</trt:VideoEncoderConfiguration>"
                    "</trt:Profiles></trt:GetProfilesResponse>"
                )
                return ET.fromstring(xml)
            if "GetCapabilities" in body:
                xml = (
                    '<tds:GetCapabilitiesResponse '
                    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
                    + TT + ">"
                    "<tds:Capabilities><tt:Media>"
                    "<tt:XAddr>http://1.2.3.4:8000/onvif/media_service"
                    "</tt:XAddr></tt:Media></tds:Capabilities>"
                    "</tds:GetCapabilitiesResponse>"
                )
                return ET.fromstring(xml)
            if "GetSnapshotUri" in body:
                xml = (
                    '<trt:GetSnapshotUriResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                    + TT + ">"
                    "<trt:MediaUri><tt:Uri>"
                    "http://1.2.3.4/onvif-http/snapshot?Profile_1"
                    "</tt:Uri></trt:MediaUri>"
                    "</trt:GetSnapshotUriResponse>"
                )
                return ET.fromstring(xml)
            raise AssertionError("unexpected SOAP call")

        import httpx

        class Resp:
            status_code = 200
            content = JPEG

        monkeypatch.setattr("src.onvif_tools.post_soap", fake_post_soap)
        monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
        out = host._get_snapshot({"name": "cam"})
        assert out["ok"] is True
        assert out["mime"] == "image/jpeg"
        saved = Path(out["path"])
        assert saved.exists()
        assert saved.read_bytes().startswith(JPEG[:3])


class TestRegistrationAndPrompt:
    def test_manager_registration(self, tmp_path):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        names = {t.name for t in mgr.list_tools()}
        assert "onvif_discover" in names
        assert "onvif_get_snapshot" in names
        assert mgr._onvif_local_host is not None
        assert mgr._onvif_local_host.SERVER_NAME == "onvif-local"
        mgr.close_all()

    def test_stdio_server_has_dispatch(self):
        import io

        src = io.open(
            "src/mcp_server/onvif_server.py", encoding="utf-8",
        ).read()
        assert "onvif_get_snapshot" in src
        assert "onvif_test_credentials" in src

    def test_prompt_mentions_onvif(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        a._mcp_manager = None
        prompt = a._system_prompt(tools_enabled=True)
        assert "onvif_discover" in prompt
        assert "onvif_get_snapshot" in prompt
        assert "password_env" in prompt
        legacy = a._system_prompt(tools_enabled=False)
        assert "onvif_discover" not in legacy


# ─── Phase B (v0.9.38.0+) — recording ────────────────────────────


class TestRecording:
    def test_embed_rtsp_creds(self):
        from src.onvif_tools import _embed_rtsp_creds

        out = _embed_rtsp_creds(
            "rtsp://1.2.3.4:554/Streaming/Channels/101", "admin", "pw%",
        )
        assert out.startswith("rtsp://admin:pw%25@1.2.3.4:554/")
        assert _embed_rtsp_creds(
            "rtsp://admin:pw@1.2.3.4/x", "u", "p",
        ) == "rtsp://admin:pw@1.2.3.4/x"

    def test_record_path_forced_mp4_and_sandbox(self, write_root):
        from src.onvif_tools import OnvifError, resolve_record_path

        p = resolve_record_path("", write_root)
        assert p.parent.name == "uploads" and p.suffix == ".mp4"
        assert resolve_record_path("rec.avi", write_root).suffix == ".mp4"
        with pytest.raises(OnvifError):
            resolve_record_path("../../evil.mp4", write_root)

    def test_record_stream_requires_ffmpeg(self, tmp_path, monkeypatch):
        import src.onvif_tools as ot
        from src.onvif_tools import OnvifError, record_stream

        monkeypatch.setattr(ot, "ffmpeg_available", lambda: None)
        with pytest.raises(OnvifError):
            record_stream("rtsp://x/y", tmp_path / "a.mp4", seconds=1)

    def test_record_stream_builds_command(self, tmp_path, monkeypatch):
        import subprocess

        import src.onvif_tools as ot

        captured = {}

        class Proc:
            returncode = 0
            stderr = ""

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            (tmp_path / "r.mp4").write_bytes(b"\x00\x00\x00ftypmp42")
            return Proc()

        monkeypatch.setattr(ot, "ffmpeg_available",
                            lambda: "C:/ffmpeg/ffmpeg.exe")
        monkeypatch.setattr(subprocess, "run", fake_run)
        out = ot.record_stream(
            "rtsp://1.2.3.4:554/x", tmp_path / "r.mp4", seconds=5,
        )
        assert out["ok"] is True
        cmd = captured["cmd"]
        assert cmd[0] == "C:/ffmpeg/ffmpeg.exe"
        assert "-rtsp_transport" in cmd and "tcp" in cmd
        assert "-c" in cmd and "copy" in cmd

    def test_record_stream_fails_on_empty_output(self, tmp_path,
                                                 monkeypatch):
        import subprocess

        import src.onvif_tools as ot
        from src.onvif_tools import OnvifError

        class Proc:
            returncode = 1
            stderr = "Connection refused"

        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
        with pytest.raises(OnvifError):
            ot.record_stream(
                "rtsp://1.2.3.4/x", tmp_path / "x.mp4", seconds=1,
            )

    def test_get_stream_uri_parses(self, monkeypatch):
        import xml.etree.ElementTree as ET

        import src.onvif_tools as ot

        xml = (
            '<trt:GetStreamUriResponse '
            'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
            'xmlns:tt="http://www.onvif.org/ver10/schema">'
            "<trt:MediaUri><tt:Uri>"
            "rtsp://1.2.3.4:554/Streaming/Channels/101"
            "</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse>"
        )
        monkeypatch.setattr(
            ot, "post_soap", lambda *a, **k: ET.fromstring(xml),
        )
        assert ot.get_stream_uri("http://m", "p1", "u", "p").startswith(
            "rtsp://",
        )

    def test_record_tool_ffmpeg_missing(self, fav_env, write_root,
                                        monkeypatch):
        import src.onvif_tools as ot
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        host._favorite_add({
            "name": "cam", "ip": "1.2.3.4",
            "xaddr": "http://x/onvif/device_service",
        })
        monkeypatch.setattr(ot, "ffmpeg_available", lambda: None)
        out = host._record({"name": "cam", "seconds": 1})
        assert "error" in out
        assert "ffmpeg" in out["error"]

    def test_record_tool_full_flow(self, fav_env, write_root,
                                   monkeypatch):
        import xml.etree.ElementTree as ET

        import src.onvif_tools as ot
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        host._favorite_add({
            "name": "cam", "ip": "1.2.3.4",
            "xaddr": "http://1.2.3.4:8000/onvif/device_service",
        })

        def fake_post_soap(url, body, username="", password="",
                           timeout=10.0):
            TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'
            if "GetProfiles" in body:
                return ET.fromstring(
                    '<trt:GetProfilesResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
                    '<trt:Profiles token="prof1"><trt:Name>x</trt:Name>'
                    "</trt:Profiles></trt:GetProfilesResponse>"
                )
            if "GetCapabilities" in body:
                return ET.fromstring(
                    '<tds:GetCapabilitiesResponse '
                    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
                    + TT + ">"
                    "<tds:Capabilities><tt:Media><tt:XAddr>"
                    "http://1.2.3.4:8000/onvif/media_service"
                    "</tt:XAddr></tt:Media></tds:Capabilities>"
                    "</tds:GetCapabilitiesResponse>"
                )
            if "GetStreamUri" in body:
                return ET.fromstring(
                    '<trt:GetStreamUriResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                    + TT + ">"
                    "<trt:MediaUri><tt:Uri>"
                    "rtsp://1.2.3.4:554/Streaming/Channels/101"
                    "</tt:Uri></trt:MediaUri>"
                    "</trt:GetStreamUriResponse>"
                )
            raise AssertionError("unexpected SOAP call")

        recorded = {}

        def fake_record(uri, out_path, seconds=10.0, **kw):
            recorded["uri"] = uri
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"\x00\x00\x00\x18ftypmp42")
            return {"ok": True, "size": 24}

        monkeypatch.setattr(ot, "post_soap", fake_post_soap)
        monkeypatch.setattr(ot, "record_stream", fake_record)
        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")
        out = host._record({"name": "cam", "seconds": 5})
        assert out.get("ok") is True, out
        assert out["path"].endswith(".mp4")
        assert "admin%3Apw" in recorded["uri"] or "admin:" in recorded["uri"]


class TestPhaseCSoap:
    """v0.9.41.0 — PTZ + events SOAP layer (mocked post_soap)."""

    def _patch_soap(self, monkeypatch):
        import src.onvif_tools as ot

        bodies = []

        def fake_post_soap(url, body, username="", password="",
                           timeout=10.0):
            bodies.append((url, body))
            TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'
            if "GetProfiles" in body:
                return ET.fromstring(
                    '<trt:GetProfilesResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
                    '<trt:Profiles token="prof1"><trt:Name>x</trt:Name>'
                    "</trt:Profiles></trt:GetProfilesResponse>"
                )
            if "GetCapabilities" in body:
                cat = "PTZ" if "<tds:Category>PTZ<" in body else "Events"
                return ET.fromstring(
                    f'<tds:GetCapabilitiesResponse '
                    f'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
                    f'{TT}><tds:Capabilities>'
                    f'<tt:{cat}><tt:XAddr>'
                    f"http://1.2.3.4:8000/onvif/{cat.lower()}_service"
                    f"</tt:XAddr></tt:{cat}></tds:Capabilities>"
                    f"</tds:GetCapabilitiesResponse>"
                )
            if "ContinuousMove" in body:
                return ET.fromstring(
                    '<tptz:ContinuousMoveResponse '
                    'xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"/>'
                )
            if "Stop" in body:
                return ET.fromstring(
                    '<tptz:StopResponse '
                    'xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"/>'
                )
            if "GetPresets" in body:
                return ET.fromstring(
                    '<tptz:GetPresetsResponse '
                    'xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">'
                    '<tptz:Preset token="P1"><tptz:Name>Gate</tptz:Name>'
                    "</tptz:Preset></tptz:GetPresetsResponse>"
                )
            if "GotoPreset" in body:
                return ET.fromstring(
                    '<tptz:GotoPresetResponse '
                    'xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"/>'
                )
            if "CreatePullPointSubscription" in body:
                return ET.fromstring(
                    '<tev:CreatePullPointSubscriptionResponse '
                    'xmlns:tev="http://www.onvif.org/ver10/events/wsdl" '
                    'xmlns:wsa="http://www.w3.org/2005/08/addressing">'
                    "<tev:SubscriptionReference><wsa:Address>"
                    "http://1.2.3.4/onvif/subscription?idx=7"
                    "</wsa:Address></tev:SubscriptionReference>"
                    "</tev:CreatePullPointSubscriptionResponse>"
                )
            if "PullMessages" in body:
                return ET.fromstring(
                    '<tev:PullMessagesResponse '
                    'xmlns:tev="http://www.onvif.org/ver10/events/wsdl" '
                    'xmlns:tt="http://www.onvif.org/ver10/schema">'
                    "<tev:NotificationMessage UtcTime="
                    "\"2026-09-13T09:00:00Z\">"
                    "<tev:Topic Dialect="
                    "\"http://www.onvif.org/ver10/tev/topicPath/1\">"
                    "tns1:RuleEngine/CellMotionDetector/Motion"
                    "</tev:Topic><tev:Message>"
                    "<tt:Source><tt:SimpleItem Name=\"Source\" "
                    "Value=\"VideoSrc1\"/></tt:Source>"
                    "<tt:Data><tt:SimpleItem Name=\"State\" "
                    "Value=\"true\"/></tt:Data></tev:Message>"
                    "</tev:NotificationMessage>"
                    "</tev:PullMessagesResponse>"
                )
            raise AssertionError("unexpected SOAP call: " + body[:120])

        monkeypatch.setattr(ot, "post_soap", fake_post_soap)
        return bodies

    def test_ptz_move_builds_body_and_clamps(self, monkeypatch):
        bodies = self._patch_soap(monkeypatch)
        out = ot.ptz_continuous_move(
            "http://1.2.3.4/onvif/ptz_service", "prof1",
            pan=5.0, tilt=-0.5, duration_s=99.0,
        )
        assert out["pan"] == 1.0 and out["tilt"] == -0.5
        assert out["duration_s"] == 10.0
        url, body = bodies[-1]
        assert "ContinuousMove" in body and "prof1" in body
        assert 'x="1.0"' in body and "PT10.0S" in body

    def test_presets_and_goto(self, monkeypatch):
        self._patch_soap(monkeypatch)
        presets = ot.get_presets(
            "http://1.2.3.4/onvif/ptz_service", "prof1",
        )
        assert presets == [{"token": "P1", "name": "Gate"}]
        assert ot.ptz_goto_preset(
            "http://1.2.3.4/onvif/ptz_service", "prof1", "P1",
        )["preset"] == "P1"

    def test_events_round_trip(self, monkeypatch):
        self._patch_soap(monkeypatch)
        events = ot.drain_events(
            "http://1.2.3.4/onvif/events_service", poll_seconds=0.5,
        )
        assert len(events) == 1
        ev = events[0]
        assert "Motion" in ev["topic"]
        assert ev["data"] == {"State": "true"}
        assert ev["source"] == {"Source": "VideoSrc1"}
        assert ot.match_events(events, ["motion"]) == events
        assert ot.match_events(events, ["tamper"]) == []

    def test_get_service_xaddr_fallback(self, monkeypatch):
        def no_caps(url, body, username="", password="", timeout=10.0):
            return ET.fromstring(
                '<tds:GetCapabilitiesResponse '
                'xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>'
            )

        monkeypatch.setattr(ot, "post_soap", no_caps)
        assert ot.get_service_xaddr(
            "http://1.2.3.4/onvif/device_service", "PTZ", "", "",
        ) == "http://1.2.3.4/onvif/ptz_service"


class TestPhaseCHostTools:
    def _host(self, monkeypatch, soap=False):
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        if soap:
            TestPhaseCSoap()._patch_soap(monkeypatch)
        return host

    def test_phase_c_tools_advertised(self):
        from src.mcp_client.host import OnvifLocalHost

        names = [t.name for t in OnvifLocalHost().list_tools()]
        for need in ("onvif_ptz_move", "onvif_ptz_stop",
                     "onvif_get_presets", "onvif_goto_preset",
                     "onvif_get_events", "onvif_watch_start",
                     "onvif_watch_list", "onvif_watch_stop"):
            assert need in names

    def test_ptz_move_requires_confirm(self, monkeypatch):
        host = self._host(monkeypatch, soap=True)
        out = host._ptz_move({"name": "cam", "pan": 1})
        assert "confirm" in out["error"]

    def test_ptz_move_happy_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = self._host(monkeypatch, soap=True)
        host._favorite_add({
            "name": "cam", "ip": "1.2.3.4",
            "xaddr": "http://1.2.3.4:8000/onvif/device_service",
        })
        out = host._ptz_move({"name": "cam", "pan": 0.5, "duration": 2,
                              "confirm": True})
        assert out.get("ok") is True, out
        assert out["result"]["duration_s"] == 2.0

    def test_events_tool(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = self._host(monkeypatch, soap=True)
        host._favorite_add({
            "name": "cam", "ip": "1.2.3.4",
            "xaddr": "http://1.2.3.4:8000/onvif/device_service",
        })
        out = host._get_events({"name": "cam"})
        assert out.get("ok") is True, out
        assert out["count"] == 1

    def test_watch_start_list_stop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = self._host(monkeypatch)
        started = host._watch_start({
            "name": "door", "device": "cam", "keywords": "motion",
        })
        assert started.get("ok") is True, started
        time.sleep(0.1)  # settle: device missing → last_error, no crash
        listed = host._watch_list({})
        assert listed["count"] == 1
        assert listed["watches"][0]["name"] == "door"
        stopped = host._watch_stop({"name": "door"})
        assert stopped.get("ok") is True
        assert host._watch_list({})["count"] == 0

    def test_watch_event_fires_webhook_and_snapshot(
        self, monkeypatch, tmp_path, write_root,
    ):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = self._host(monkeypatch)
        host._watch_start({"name": "door", "device": "cam",
                           "keywords": "motion", "poll_interval": 2.0,
                           "webhook_trigger_id": "motion-alert",
                           "snapshot_on_event": True})
        monkeypatch.setattr(host, "_device_and_creds", lambda _n: (
            {"xaddr": "http://1.2.3.4/onvif/device_service"},
            {"username": "u", "password": "p"}, None,
        ))
        monkeypatch.setattr(host, "_snapshot_for_watch",
                            lambda w: "/sandbox/snap.jpg")
        fired = {}

        def fake_fire(trigger, payload):
            fired["trigger"] = getattr(trigger, "name", str(trigger))
            fired["payload"] = payload
            return {"ok": True, "answer": "notified"}

        host.attach_webhook_fire(fake_fire)

        class FakeTrigger:
            name = "motion-alert"
            enabled = True

        monkeypatch.setattr(
            "src.agent.webhook.WebhookManager.get",
            lambda self, _i: None,
        )
        monkeypatch.setattr(
            "src.agent.webhook.WebhookManager.find_by_name",
            lambda self, _i: FakeTrigger(),
        )
        event = {"topic": "tns1:RuleEngine/CellMotionDetector/Motion",
                 "utc_time": "2026-09-13T09:00:00Z",
                 "data": {"State": "true"}, "source": {}}
        host._handle_watch_event(
            host._watch_store().get("door"), event,
        )
        assert fired["trigger"] == "motion-alert"
        assert fired["payload"]["snapshot"] == "/sandbox/snap.jpg"
        assert "Motion" in fired["payload"]["event"]
        hist = host._watch_store().read_history("door")
        assert any(r.get("webhook_fired") for r in hist)

    def test_watch_payload_has_event_text_and_prompt_compose(
        self, monkeypatch, tmp_path, write_root,
    ):
        """v0.9.41.1 — the judge-then-notify chain: payload carries a
        human-readable event_text + snapshot path, and _compose_prompt
        injects BOTH into the fired prompt so the LLM can judge first."""
        from src.agent.webhook import _compose_prompt
        from src.mcp_client.host import OnvifLocalHost

        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = OnvifLocalHost()
        host._watch_start({"name": "door", "device": "cam",
                           "keywords": "motion", "poll_interval": 2.0,
                           "webhook_trigger_id": "motion-alert",
                           "snapshot_on_event": True})
        monkeypatch.setattr(host, "_device_and_creds", lambda _n: (
            {"xaddr": "http://1.2.3.4/onvif/device_service"},
            {"username": "u", "password": "p"}, None,
        ))
        monkeypatch.setattr(host, "_snapshot_for_watch",
                            lambda w: "/sandbox/snap.jpg")
        captured = {}

        def fake_fire(trigger, payload):
            captured["payload"] = payload
            return {"ok": True}

        host.attach_webhook_fire(fake_fire)

        class FakeTrigger:
            name = "motion-alert"
            enabled = True

        monkeypatch.setattr(
            "src.agent.webhook.WebhookManager.get",
            lambda self, _i: None,
        )
        monkeypatch.setattr(
            "src.agent.webhook.WebhookManager.find_by_name",
            lambda self, _i: FakeTrigger(),
        )
        event = {"topic": "tns1:RuleEngine/CellMotionDetector/Motion",
                 "data": {"State": "true"}, "source": {}}
        host._handle_watch_event(
            host._watch_store().get("door"), event,
        )
        payload = captured["payload"]
        assert "Motion" in payload["event_text"]
        assert payload["snapshot"] == "/sandbox/snap.jpg"
        # the fired prompt (user's trigger prompt + injected payload)
        # lets the judge-LLM see the snapshot path → describe_image
        trigger_prompt = "判斷畫面內容再決定是否通知。快照: {payload}"
        composed = _compose_prompt(
            type("T", (), {"prompt": trigger_prompt})(),
            payload,
        )
        assert "/sandbox/snap.jpg" in composed
        assert "event_text" in composed

    def test_watch_resume_idempotent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = self._host(monkeypatch)
        host._watch_start({"name": "door", "device": "cam"})
        assert host.resume_watches() == 0  # thread already alive
        host._watch_stop({"name": "door"})


class TestMediaFallbackAndVariants:
    """v0.9.43.1 — the XMEye lesson: profiles off the device service
    + vendor RTSP variants."""

    def test_get_profiles_anywhere_finds_8899(self, monkeypatch):
        calls = []

        def fake_post(url, body, username="", password="", timeout=10.0):
            calls.append(url)
            TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'
            if "GetCapabilities" in body:
                return ET.fromstring(
                    '<tds:GetCapabilitiesResponse '
                    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
                    + TT + "><tds:Capabilities></tds:Capabilities>"
                    "</tds:GetCapabilitiesResponse>"
                )  # device reports NO media xaddr
            if "GetProfiles" in body:
                if ":8899" in url:
                    return ET.fromstring(
                        '<trt:GetProfilesResponse '
                        'xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
                        '<trt:Profiles token="prof_8899"><trt:Name>x'
                        "</trt:Name></trt:Profiles>"
                        "</trt:GetProfilesResponse>"
                    )
                raise AssertionError("empty profiles on " + url)

        monkeypatch.setattr(ot, "post_soap", fake_post)
        profiles, used = ot.get_profiles_anywhere(
            "http://1.2.3.4:80/onvif/device_service", "u", "p", 10.0,
        )
        assert profiles[0]["token"] == "prof_8899"
        assert ":8899" in used
        # the 8899 candidate comes AFTER the conventional sibling
        assert any(":80/onvif/media_service" in c for c in calls)

    def test_get_profiles_anywhere_all_empty(self, monkeypatch):
        def fake_post(url, body, username="", password="", timeout=10.0):
            if "GetCapabilities" in body:
                return ET.fromstring(
                    '<tds:GetCapabilitiesResponse '
                    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>'
                )
            if "GetProfiles" in body:
                return ET.fromstring(
                    '<trt:GetProfilesResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>'
                )
            raise AssertionError("unexpected call")

        monkeypatch.setattr(ot, "post_soap", fake_post)
        try:
            ot.get_profiles_anywhere(
                "http://1.2.3.4/onvif/device_service", "u", "p", 10.0,
            )
        except ot.OnvifError as e:
            # v0.9.43.2: per-candidate REASONS in the message
            assert "tried media endpoints" in str(e)
            assert "-> HTTP OK but 0 profiles" in str(e)
        else:
            raise AssertionError("expected OnvifError")

    def test_profiles_anywhere_reason_transport(self, monkeypatch):
        def fake_post(url, body, username="", password="", timeout=10.0):
            raise ot.OnvifError(
                f"ONVIF authentication failed (HTTP 401) for {url}",
            )

        monkeypatch.setattr(ot, "post_soap", fake_post)
        try:
            ot.get_profiles_anywhere(
                "http://1.2.3.4/onvif/device_service", "u", "p", 10.0,
            )
        except ot.OnvifError as e:
            assert "401" in str(e)          # reason surfaced
            assert "8899" in str(e)          # candidates listed
        else:
            raise AssertionError("expected OnvifError")

    def test_empty_name_refused(self, monkeypatch, tmp_path):
        from src.mcp_client.host import OnvifLocalHost

        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = OnvifLocalHost()
        # a stale favorites.json entry with an EMPTY name (live-box
        # leftover) must never be resolvable by the empty name
        store = host._store()
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"devices": [
            {"ip": "1.2.3.4", "port": 8000},  # name missing
        ]}), encoding="utf-8")
        store.load()
        _, _, err = host._device_and_creds("")
        assert err and "name is required" in err["error"]

    def test_test_credentials_empty_info_flagged(
        self, monkeypatch, tmp_path, write_root,
    ):
        import xml.etree.ElementTree as ET

        from src.mcp_client.host import OnvifLocalHost

        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = OnvifLocalHost()
        host._favorite_add({
            "name": "web", "ip": "1.2.3.4",
            "xaddr": "http://1.2.3.4:80/",  # a WEB PAGE, not ONVIF
        })

        def fake_post(url, body, username="", password="", timeout=10.0):
            # a 200 response that is NOT ONVIF (no device-info fields)
            return ET.fromstring(
                '<Envelope xmlns="http://schemas.xmlsoap.org/soap/'
                'envelope/"/>'
            )

        monkeypatch.setattr(ot, "post_soap", fake_post)
        out = host._test_credentials({"name": "web"})
        assert out.get("ok") is False
        assert "no device information" in out["error"]

    def test_rtsp_variants_xmeye(self):
        vs = ot.rtsp_variants(
            "rtsp://1.2.3.4:554/onvif1", "admin", "pw&", channel=2,
        )
        styles = {v["style"] for v in vs}
        assert styles == {"standard", "hikvision", "dahua", "xmeye"}
        x = next(v for v in vs if v["style"] == "xmeye")
        assert "user=admin" in x["uri"] and "password=pw%26" in x["uri"]
        assert "channel=2" in x["uri"]
        h = next(v for v in vs if v["style"] == "hikvision")
        assert h["uri"].endswith("/Streaming/Channels/201")

    def test_rtsp_variants_non_rtsp(self):
        assert ot.rtsp_variants("http://x/y", "u", "p") == []

    def test_snapshot_uses_fallback_chain(self, monkeypatch, tmp_path,
                                          write_root):
        """Live-box regression: GetProfiles on the DEVICE xaddr is
        empty, but the :8899 media endpoint works → snapshot works."""
        from src.mcp_client.host import OnvifLocalHost

        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = OnvifLocalHost()
        host._favorite_add({
            "name": "dvr", "ip": "1.2.3.4",
            "xaddr": "http://1.2.3.4:80/onvif/device_service",
        })

        def fake_post(url, body, username="", password="", timeout=10.0):
            TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'
            if "GetCapabilities" in body:
                return ET.fromstring(
                    '<tds:GetCapabilitiesResponse '
                    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
                    + TT + "><tds:Capabilities></tds:Capabilities>"
                    "</tds:GetCapabilitiesResponse>"
                )
            if "GetProfiles" in body:
                if ":8899" in url:
                    return ET.fromstring(
                        '<trt:GetProfilesResponse '
                        'xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
                        '><trt:Profiles token="p1"><trt:Name>x</trt:Name>'
                        "</trt:Profiles></trt:GetProfilesResponse>"
                    )
                return ET.fromstring(
                    '<trt:GetProfilesResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>'
                )
            if "GetSnapshotUri" in body:
                return ET.fromstring(
                    '<trt:GetSnapshotUriResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                    + TT + "><trt:MediaUri><tt:Uri>"
                    "http://1.2.3.4/onvif-http/snapshot"
                    "</tt:Uri></trt:MediaUri>"
                    "</trt:GetSnapshotUriResponse>"
                )
            raise AssertionError("unexpected call: " + body[:80])

        monkeypatch.setattr(ot, "post_soap", fake_post)
        monkeypatch.setattr(
            ot, "fetch_snapshot",
            lambda uri, u, p, timeout=10.0, max_bytes=8388608: (
                JPEG, "image/jpeg",
            ),
        )
        out = host._get_snapshot({"name": "dvr"})
        assert out.get("ok") is True, out
        assert ":8899" in out["media_xaddr_used"]

    def test_record_describe_failure_returns_variants(
        self, monkeypatch, tmp_path, write_root,
    ):
        from src.mcp_client.host import OnvifLocalHost

        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = OnvifLocalHost()
        host._favorite_add({
            "name": "dvr", "ip": "1.2.3.4", "username": "admin",
            "password": "pw",
            "xaddr": "http://1.2.3.4:80/onvif/device_service",
        })

        def fake_post(url, body, username="", password="", timeout=10.0):
            TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'
            if "GetCapabilities" in body:
                return ET.fromstring(
                    '<tds:GetCapabilitiesResponse '
                    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
                    + TT + "><tds:Capabilities></tds:Capabilities>"
                    "</tds:GetCapabilitiesResponse>"
                )
            if "GetProfiles" in body:
                if ":8899" in url:
                    return ET.fromstring(
                        '<trt:GetProfilesResponse '
                        'xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
                        '><trt:Profiles token="p1"><trt:Name>x</trt:Name>'
                        "</trt:Profiles></trt:GetProfilesResponse>"
                    )
                return ET.fromstring(
                    '<trt:GetProfilesResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>'
                )
            if "GetStreamUri" in body:
                return ET.fromstring(
                    '<trt:GetStreamUriResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                    + TT + "><trt:MediaUri><tt:Uri>"
                    "rtsp://1.2.3.4:554/onvif1</tt:Uri></trt:MediaUri>"
                    "</trt:GetStreamUriResponse>"
                )
            raise AssertionError("unexpected call")

        monkeypatch.setattr(ot, "post_soap", fake_post)
        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")

        def fail_record(uri, out_path, seconds=10.0, **kw):
            raise ot.OnvifError(
                "recording failed (ffmpeg rc=1): Error opening input: "
                "rtsp DESCRIBE failed: 401 Unauthorized",
            )

        monkeypatch.setattr(ot, "record_stream", fail_record)
        out = host._record({"name": "dvr", "seconds": 2})
        assert "error" in out
        assert out.get("rtsp_variants"), out
        x = next(v for v in out["rtsp_variants"]
                 if v["style"] == "xmeye")
        assert "user=admin" in x["uri"] and "password=pw" in x["uri"]
        assert "uri=" in out["hint"]

    def test_record_uri_override(self, monkeypatch, tmp_path,
                                 write_root):
        from src.mcp_client.host import OnvifLocalHost

        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        host = OnvifLocalHost()
        host._favorite_add({
            "name": "dvr", "ip": "1.2.3.4",
            "xaddr": "http://1.2.3.4:80/onvif/device_service",
        })
        used = {}

        def fake_record(uri, out_path, seconds=10.0, **kw):
            used["rtsp"] = uri
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"\x00\x00\x00\x18ftypmp42")
            return {"ok": True, "size": 24}

        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")
        monkeypatch.setattr(ot, "record_stream", fake_record)
        out = host._record({
            "name": "dvr", "seconds": 2,
            "uri": "rtsp://1.2.3.4:554/user=admin&password=pw"
                   "&channel=1&stream=0.sdp?real=1",
        })
        assert out.get("ok") is True, out
        # the override went through with creds embedded
        assert used["rtsp"].startswith(
            "rtsp://1.2.3.4:554/user=admin",
        )


class TestPhaseCWiring:
    def test_manager_wires_onvif_fire(self, tmp_path):
        from src.mcp_client.host import OnvifLocalHost, WebhookLocalHost
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        mgr._webhook_local_host = MagicMock(spec=WebhookLocalHost)
        mgr._onvif_local_host = MagicMock(spec=OnvifLocalHost)
        mgr.set_webhook_fire_callback(lambda t, p=None: {})
        mgr._onvif_local_host.attach_webhook_fire.assert_called_once()
        mgr.close_all()

    def test_prompt_mentions_phase_c(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        a._mcp_manager = None
        prompt = a._system_prompt(tools_enabled=True)
        assert "onvif_ptz_move" in prompt
        assert "onvif_watch_start" in prompt

    def test_stdio_has_phase_c(self):
        src = Path("src/mcp_server/onvif_server.py").read_text(
            encoding="utf-8",
        )
        for key in ("onvif_ptz_move", "onvif_get_events",
                    "onvif_watch_start", "onvif_watch_stop"):
            assert f'"{key}"' in src


class TestOnvifGetResources:
    """v0.9.38.1 — one SOAP sweep exposing RTSP + snapshot URIs."""

    def test_resources_include_rtsp_and_snapshot(
        self, write_root, monkeypatch,
    ):
        import xml.etree.ElementTree as ET

        import src.onvif_tools as ot
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        host._favorite_add({
            "name": "cam", "ip": "1.2.3.4",
            "xaddr": "http://1.2.3.4:8000/onvif/device_service",
        })

        def fake_post_soap(url, body, username="", password="",
                           timeout=10.0):
            TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'
            if "GetDeviceInformation" in body:
                return ET.fromstring(
                    '<tds:GetDeviceInformationResponse '
                    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
                    "<tds:Manufacturer>Acme</tds:Manufacturer>"
                    "<tds:Model>C1</tds:Model>"
                    "</tds:GetDeviceInformationResponse>"
                )
            if "GetProfiles" in body:
                return ET.fromstring(
                    '<trt:GetProfilesResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
                    '<trt:Profiles token="prof1"><trt:Name>x</trt:Name>'
                    "</trt:Profiles></trt:GetProfilesResponse>"
                )
            if "GetCapabilities" in body:
                return ET.fromstring(
                    '<tds:GetCapabilitiesResponse '
                    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
                    + TT + ">"
                    "<tds:Capabilities><tt:Media><tt:XAddr>"
                    "http://1.2.3.4:8000/onvif/media_service"
                    "</tt:XAddr></tt:Media></tds:Capabilities>"
                    "</tds:GetCapabilitiesResponse>"
                )
            if "GetStreamUri" in body:
                return ET.fromstring(
                    '<trt:GetStreamUriResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                    + TT + ">"
                    "<trt:MediaUri><tt:Uri>"
                    "rtsp://1.2.3.4:554/Streaming/Channels/101"
                    "</tt:Uri></trt:MediaUri>"
                    "</trt:GetStreamUriResponse>"
                )
            if "GetSnapshotUri" in body:
                return ET.fromstring(
                    '<trt:GetSnapshotUriResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                    + TT + ">"
                    "<trt:MediaUri><tt:Uri>"
                    "http://1.2.3.4/onvif-http/snapshot?Profile_1"
                    "</tt:Uri></trt:MediaUri>"
                    "</trt:GetSnapshotUriResponse>"
                )
            raise AssertionError("unexpected SOAP call: " + body[:120])

        monkeypatch.setattr(ot, "post_soap", fake_post_soap)
        out = host._get_resources({"name": "cam"})
        assert out.get("ok") is True, out
        assert out["device"]["manufacturer"] == "Acme"
        assert out["media_xaddr"].endswith("media_service")
        assert out["profile_count"] == 1
        assert out["selected_profile"] == "prof1"
        assert out["rtsp_stream_uri"].startswith("rtsp://")
        assert out["snapshot_uri"].startswith("http://")
        # the SOAP sweep itself never leaks the resolved password
        assert "pw" != (out.get("password") or "")

    def test_resources_unknown_device(self, write_root):
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        out = host._get_resources({"name": "nope"})
        assert "error" in out