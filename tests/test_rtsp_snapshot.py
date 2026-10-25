"""Tests for v0.9.44.0 — RTSP frame grab (no-ONVIF-snapshot devices)
+ the automatic fallback in onvif_get_snapshot."""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import src.onvif_tools as ot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

@pytest.fixture()
def write_root(tmp_path, monkeypatch):
    root = tmp_path / "write"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(root))
    return root


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _dvr_favorites(host):
    host._favorite_add({
        "name": "dvr", "ip": "1.2.3.4", "username": "admin",
        "password": "pw",
        "xaddr": "http://1.2.3.4:80/onvif/device_service",
    })


def _dvr_soap(get_snapshot_uri_raises=False):
    """Mock SOAP: device reports no media xaddr; :8899 serves
    profiles; GetStreamUri returns the standard rtsp URL."""

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
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
                    '<trt:Profiles token="p1"><trt:Name>x</trt:Name>'
                    "</trt:Profiles></trt:GetProfilesResponse>"
                )
            return ET.fromstring(
                '<trt:GetProfilesResponse '
                'xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>'
            )
        if "GetStreamUri" in body:
            if get_snapshot_uri_raises:
                raise ot.OnvifError("stream uri unsupported")
            return ET.fromstring(
                '<trt:GetStreamUriResponse '
                'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                + TT + "><trt:MediaUri><tt:Uri>"
                "rtsp://1.2.3.4:554/onvif1</tt:Uri></trt:MediaUri>"
                "</trt:GetStreamUriResponse>"
            )
        if "GetSnapshotUri" in body:
            if get_snapshot_uri_raises:
                raise ot.OnvifError("snapshot unsupported on this cam")
            return ET.fromstring(
                '<trt:GetSnapshotUriResponse '
                'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                + TT + "><trt:MediaUri><tt:Uri>"
                "http://1.2.3.4/onvif-http/snapshot"
                "</tt:Uri></trt:MediaUri>"
                "</trt:GetSnapshotUriResponse>"
            )
        raise AssertionError("unexpected call: " + body[:80])

    return fake_post


class TestGrabFrameViaRtsp:
    def test_success_and_cmd_shape(self, monkeypatch, tmp_path):
        out = tmp_path / "frame.jpg"
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            out.write_bytes(JPEG)
            r = type("P", (), {"returncode": 0, "stderr": ""})()
            return r

        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")
        monkeypatch.setattr("subprocess.run", fake_run)
        res = ot.grab_frame_via_rtsp(
            "rtsp://u:p@1.2.3.4:554/onvif1", out, timeout_s=10.0,
        )
        assert res["ok"] is True
        cmd = seen["cmd"]
        assert "-frames:v" in cmd and "1" in cmd
        assert "-rtsp_transport" in cmd and "tcp" in cmd
        assert str(out) in cmd

    def test_settle_adds_ss(self, monkeypatch, tmp_path):
        out = tmp_path / "frame.jpg"
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            out.write_bytes(JPEG)
            return type("P", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")
        monkeypatch.setattr("subprocess.run", fake_run)
        ot.grab_frame_via_rtsp(
            "rtsp://1.2.3.4/x", out, settle_s=0.8, timeout_s=10.0,
        )
        cmd = seen["cmd"]
        i = cmd.index("-ss")
        assert abs(float(cmd[i + 1]) - 0.8) < 0.01

    def test_failure_raises_with_tail(self, monkeypatch, tmp_path):
        out = tmp_path / "frame.jpg"

        def fake_run(cmd, **kw):
            return type("P", (), {
                "returncode": 1,
                "stderr": "Error opening input: DESCRIBE 401",
            })()

        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")
        monkeypatch.setattr("subprocess.run", fake_run)
        try:
            ot.grab_frame_via_rtsp("rtsp://1.2.3.4/x", out, 0, 10.0)
        except ot.OnvifError as e:
            assert "401" in str(e) and "frame grab failed" in str(e)
        else:
            raise AssertionError("expected OnvifError")

    def test_non_jpeg_rejected(self, monkeypatch, tmp_path):
        out = tmp_path / "frame.jpg"

        def fake_run(cmd, **kw):
            out.write_bytes(b"<html>not an image</html>")
            return type("P", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")
        monkeypatch.setattr("subprocess.run", fake_run)
        try:
            ot.grab_frame_via_rtsp("rtsp://1.2.3.4/x", out, 0, 10.0)
        except ot.OnvifError as e:
            assert "non-JPEG" in str(e)
        else:
            raise AssertionError("expected OnvifError")

    def test_no_ffmpeg(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ot, "ffmpeg_available", lambda: None)
        try:
            ot.grab_frame_via_rtsp(
                "rtsp://1.2.3.4/x", tmp_path / "f.jpg",
            )
        except ot.OnvifError as e:
            assert "ffmpeg" in str(e)
        else:
            raise AssertionError("expected OnvifError")


class TestRtspSnapshotHostTool:
    def _host(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        _dvr_favorites(host)
        monkeypatch.setattr(ot, "post_soap", _dvr_soap())
        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")
        return host

    def test_eighteen_tools_advertised(self):
        from src.mcp_client.host import OnvifLocalHost

        names = [t.name for t in OnvifLocalHost().list_tools()]
        assert "onvif_rtsp_snapshot" in names
        assert len(names) == 18

    def test_rtsp_snapshot_success_standard(self, monkeypatch, tmp_path,
                                            write_root):
        host = self._host(monkeypatch, tmp_path)
        grabs = []

        def fake_grab(uri, out, settle_s=0.0, timeout_s=20.0):
            grabs.append(uri)
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(JPEG)
            return {"ok": True, "size": len(JPEG)}

        monkeypatch.setattr(ot, "grab_frame_via_rtsp", fake_grab)
        out = host._rtsp_snapshot({"name": "dvr"})
        assert out.get("ok") is True, out
        assert out["via"] == "rtsp"
        assert out["style_used"] == "stream-uri"
        assert grabs[0].startswith("rtsp://admin:pw@1.2.3.4:554")
        assert out["path"].endswith(".jpg")

    def test_rtsp_snapshot_falls_to_xmeye(self, monkeypatch, tmp_path,
                                          write_root):
        host = self._host(monkeypatch, tmp_path)

        def fake_grab(uri, out, settle_s=0.0, timeout_s=20.0):
            if "user=admin&password=pw" in uri:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_bytes(JPEG)
                return {"ok": True, "size": len(JPEG)}
            raise ot.OnvifError(
                "frame grab failed (ffmpeg rc=1): DESCRIBE 401",
            )

        monkeypatch.setattr(ot, "grab_frame_via_rtsp", fake_grab)
        out = host._rtsp_snapshot({"name": "dvr"})
        assert out.get("ok") is True, out
        assert out["style_used"] == "xmeye"
        assert out["attempts"][0]["style"] == "stream-uri"

    def test_rtsp_snapshot_all_fail(self, monkeypatch, tmp_path,
                                    write_root):
        host = self._host(monkeypatch, tmp_path)
        monkeypatch.setattr(
            ot, "grab_frame_via_rtsp",
            lambda uri, out, settle_s=0.0, timeout_s=20.0: (_ for _ in ()
            ).throw(ot.OnvifError("DESCRIBE 401")),
        )
        out = host._rtsp_snapshot({"name": "dvr"})
        assert "error" in out
        assert len(out["attempts"]) >= 4  # stream-uri + 3 vendors

    def test_rtsp_snapshot_channel(self, monkeypatch, tmp_path,
                                   write_root):
        host = self._host(monkeypatch, tmp_path)

        def fake_grab(uri, out, settle_s=0.0, timeout_s=20.0):
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(JPEG)
            return {"ok": True, "size": len(JPEG)}

        monkeypatch.setattr(ot, "grab_frame_via_rtsp", fake_grab)
        out = host._rtsp_snapshot({"name": "dvr", "channel": 3})
        assert out.get("ok") is True

class TestSnapshotAutoFallback:
    def test_get_snapshot_falls_back_to_rtsp(
        self, monkeypatch, tmp_path, write_root,
    ):
        """Live-box regression: no ONVIF snapshot capability → the
        get_snapshot call transparently returns an RTSP frame."""
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        _dvr_favorites(host)
        monkeypatch.setattr(ot, "post_soap", _dvr_soap())
        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")

        def fake_grab(uri, out, settle_s=0.0, timeout_s=20.0):
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(JPEG)
            return {"ok": True, "size": len(JPEG)}

        monkeypatch.setattr(ot, "grab_frame_via_rtsp", fake_grab)
        out = host._get_snapshot({"name": "dvr"})
        assert out.get("ok") is True, out
        assert out["via"] == "rtsp"
        assert "無 ONVIF snapshot" in out["note"]
        assert "onvif_error" in out

    def test_get_snapshot_onvif_path_still_first(
        self, monkeypatch, tmp_path, write_root,
    ):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        _dvr_favorites(host)
        monkeypatch.setattr(ot, "post_soap", _dvr_soap())

        monkeypatch.setattr(
            ot, "fetch_snapshot",
            lambda uri, u, p, timeout=10.0, max_bytes=8388608: (
                JPEG, "image/jpeg",
            ),
        )
        grab_calls = []

        def fake_grab(uri, out, settle_s=0.0, timeout_s=20.0):
            grab_calls.append(uri)
            return {"ok": True, "size": 1}

        monkeypatch.setattr(ot, "grab_frame_via_rtsp", fake_grab)
        out = host._get_snapshot({"name": "dvr"})
        assert out.get("ok") is True
        assert "via" not in out  # ONVIF path succeeded
        assert grab_calls == []  # fallback never ran

    def test_both_fail_reports_both(
        self, monkeypatch, tmp_path, write_root,
    ):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
        from src.mcp_client.host import OnvifLocalHost

        host = OnvifLocalHost()
        _dvr_favorites(host)
        monkeypatch.setattr(ot, "post_soap", _dvr_soap())
        monkeypatch.setattr(ot, "ffmpeg_available", lambda: "ffmpeg")
        monkeypatch.setattr(
            ot, "grab_frame_via_rtsp",
            lambda uri, out, settle_s=0.0, timeout_s=20.0: (_ for _ in ()
            ).throw(ot.OnvifError("DESCRIBE 401")),
        )
        out = host._get_snapshot({"name": "dvr"})
        assert "error" in out
        assert "RTSP fallback failed" in out["error"]
        assert out.get("rtsp_attempts")


class TestStdioFallback:
    def test_stdio_has_rtsp_snapshot(self):
        src = Path("src/mcp_server/onvif_server.py").read_text(
            encoding="utf-8",
        )
        assert '"onvif_rtsp_snapshot"' in src