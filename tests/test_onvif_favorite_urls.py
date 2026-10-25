"""Tests for v0.9.56.0 — ONVIF favorite_list carries ACTUAL URLs.

* Static (no network): every entry gains ``http_url`` (ONVIF device
  endpoint from xaddr / ip:port) + ``rtsp_url_guess``.
* ``resolve=true``: live SOAP per camera → the REAL ``rtsp_url``
  (GetStreamUri + embedded creds), ``rtsp_variants``, ``snapshot_uri``,
  ``media_xaddr``; offline cameras degrade to ``resolve_error``
  without breaking the list.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import src.onvif_tools as ot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    from src.mcp_client.host import OnvifLocalHost

    return OnvifLocalHost()


def _add(host, **over):
    args = {"name": "cam", "ip": "1.2.3.4", "port": 8000,
            "username": "admin", "password": "pw"}
    args.update(over)
    out = host._favorite_add(args)
    assert out.get("ok") is True, out


def _mock_soap():
    """Mock SOAP: :80 device xaddr serves 0 profiles; :8899 serves
    profile p1; GetStreamUri → rtsp://1.2.3.4:554/onvif1;
    GetSnapshotUri → http://1.2.3.4/onvif-http/snapshot."""
    TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'

    def fake_post(url, body, username="", password="", timeout=10.0):
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
            return ET.fromstring(
                '<trt:GetStreamUriResponse '
                'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                + TT + "><trt:MediaUri><tt:Uri>"
                "rtsp://1.2.3.4:554/onvif1</tt:Uri></trt:MediaUri>"
                "</trt:GetStreamUriResponse>"
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

    return fake_post


class TestStaticUrls:
    def test_static_urls_always_present(self, host):
        _add(host)
        out = host._favorite_list({})
        assert out["ok"] is True
        entry = out["favorites"][0]
        assert entry["http_url"] == (
            "http://1.2.3.4:8000/onvif/device_service"
        )
        assert entry["rtsp_url_guess"] == "rtsp://1.2.3.4:554/"
        assert "rtsp_url" not in entry  # no live resolution by default

    def test_xaddr_http_preferred(self, host):
        _add(host, xaddr="http://10.0.0.5:8899/onvif/device_service",
             name="cam2", ip="10.0.0.5")
        entry = host._favorite_list({})["favorites"][0]
        assert entry["http_url"] == (
            "http://10.0.0.5:8899/onvif/device_service"
        )

    def test_resolved_flag_reflects_arg(self, host):
        _add(host)
        assert host._favorite_list({})["resolved"] is False
        assert host._favorite_list({"resolve": True})["resolved"] is True


class TestResolveLive:
    def test_resolve_true_returns_actual_rtsp(self, host, monkeypatch):
        _add(host, xaddr="http://1.2.3.4:80/onvif/device_service")
        monkeypatch.setattr(ot, "post_soap", _mock_soap())
        out = host._favorite_list({"resolve": True})
        entry = out["favorites"][0]
        assert entry.get("resolve_error") is None
        # the REAL GetStreamUri URL with embedded credentials
        assert entry["rtsp_url"] == "rtsp://admin:pw@1.2.3.4:554/onvif1"
        assert ":8899" in entry["media_xaddr"]
        assert entry["profile_count"] == 1
        assert entry["snapshot_uri"] == (
            "http://1.2.3.4/onvif-http/snapshot"
        )
        styles = {v["style"] for v in entry["rtsp_variants"]}
        assert {"standard", "hikvision", "dahua", "xmeye"} <= styles

    def test_resolve_offline_camera_contained(self, host, monkeypatch):
        _add(host, xaddr="http://1.2.3.4:80/onvif/device_service")

        def dead_post(url, body, username="", password="", timeout=10.0):
            raise ConnectionError("refused")

        monkeypatch.setattr(ot, "post_soap", dead_post)
        out = host._favorite_list({"resolve": True})
        entry = out["favorites"][0]
        assert "resolve_error" in entry
        # static URLs survive the failure (xaddr preferred over ip:port)
        assert entry["http_url"] == "http://1.2.3.4:80/onvif/device_service"

    def test_resolve_multiple_cameras_isolated(self, host, monkeypatch):
        _add(host, name="offline", ip="10.9.9.9", port=8000,
             username="a", password="b",
             xaddr="http://10.9.9.9:8000/onvif/device_service")
        _add(host, name="live", ip="1.2.3.4", port=80,
             username="admin", password="pw",
             xaddr="http://1.2.3.4:80/onvif/device_service")

        def fake_post(url, body, username="", password="", timeout=10.0):
            TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'
            if "10.9.9.9" in url:
                raise ConnectionError("refused")
            if "GetProfiles" in body:
                return ET.fromstring(
                    '<trt:GetProfilesResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
                    '<trt:Profiles token="p1"><trt:Name>x</trt:Name>'
                    "</trt:Profiles></trt:GetProfilesResponse>"
                )
            if "GetStreamUri" in body:
                return ET.fromstring(
                    '<trt:GetStreamUriResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                    + TT + "><trt:MediaUri><tt:Uri>"
                    "rtsp://1.2.3.4:554/onvif1</tt:Uri></trt:MediaUri>"
                    "</trt:GetStreamUriResponse>"
                )
            if "GetSnapshotUri" in body:
                return ET.fromstring(
                    '<trt:GetSnapshotUriResponse '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
                    + TT + "><trt:MediaUri><tt:Uri>"
                    "http://1.2.3.4/snap</tt:Uri></trt:MediaUri>"
                    "</trt:GetSnapshotUriResponse>"
                )
            if "GetCapabilities" in body:
                return ET.fromstring(
                    '<tds:GetCapabilitiesResponse '
                    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
                    + TT + "></tds:GetCapabilitiesResponse>"
                )
            raise AssertionError("unexpected call")

        monkeypatch.setattr(ot, "post_soap", fake_post)
        out = host._favorite_list({"resolve": True})
        by_name = {d["name"]: d for d in out["favorites"]}
        assert "resolve_error" in by_name["offline"]
        assert by_name["live"]["rtsp_url"] == (
            "rtsp://admin:pw@1.2.3.4:554/onvif1"
        )


def test_device_urls_helper_pure():
    assert ot.device_urls({"ip": "1.2.3.4", "port": 80}) == {
        "http_url": "http://1.2.3.4:80/onvif/device_service",
        "rtsp_url_guess": "rtsp://1.2.3.4:554/",
    }
    assert ot.device_urls({"ip": "", "xaddr": ""}) == {
        "http_url": "", "rtsp_url_guess": "",
    }