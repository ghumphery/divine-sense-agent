"""v0.9.64.0 — hicloud HWS (中華電信 CaaS/CVPC) MCP extension tests.

Contract locked:
* ``sign_params`` reproduces the OFFICIAL document example verbatim
  (https://hws.hicloud.hinet.net/hws-doc/zh_TW/ -> Authentication of
  API Calling): sorted params -> lowercase joined string -> HMAC-SHA1
  -> Base64 -> '+'->'*' '/'->'-' '='->removed.
* ``build_signed_url`` URL-encodes values (':'->%3A) and appends the
  signature unencoded.
* Mock-first engine: HICLOUD_MODE=mock (default) NEVER touches the
  network; lifecycle writes require confirm=true (mock included);
  live writes additionally require HICLOUD_WRITES_ENABLED=on.
* HicloudLocalHost advertises 8 tools; from_config registers the
  hicloud-local server; the stdio mirror matches the host surface.
* The system prompt gains the hicloud trigger block (tools_enabled
  only).
"""
from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import io
import json
import re
import urllib.error
from unittest.mock import patch

import pytest

HICLOUD_ENV_VARS = (
    "HICLOUD_MODE", "HICLOUD_WRITES_ENABLED", "HICLOUD_ACCESS_KEY",
    "HICLOUD_SECRET_KEY", "HICLOUD_TIMEOUT_S",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Env isolation — hicloud knobs must never leak between tests."""
    for var in HICLOUD_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# 官方文件範例（Authentication of API Calling 頁）— 簽章測試向量
_DOC_PARAMS = {
    "action": ["runInstances"],
    "version": ["2013-03-29"],
    "chtAuthType": ["hwspass"],
    "imageId": ["hi-olajtpss"],
    "instanceType": ["HC1.S.LINUX"],
    "monitoringEnabled": ["false"],
    "instanceName": ["haha"],
    "count": ["1"],
    "accessKey": ["U0U0MU5UQXhNREF3TVRFek5qSTVPRFkxTURneU1UWT0"],
    "expires": ["2013-03-29T17:50:04Z"],
}
_DOC_SECRET = (
    "WWpJNU16a3pOV1JsWWpNeU5HVXdOMkkxTURNd1lUbG1OMlEwTXpSaFptST0"
)
_DOC_SIGNATURE = "VBUfKTt48Wf6xbdny98N4Gi07f4"


class TestSignature:
    def test_official_document_example(self):
        from src.hicloud_tools import sign_params

        sig = sign_params(_DOC_PARAMS, _DOC_SECRET)
        assert sig == _DOC_SIGNATURE

    def test_special_char_replacement(self):
        """Base64 輸出含 + / = 時必須替換成 * - (刪除)。"""
        from src.hicloud_tools import sign_params

        raw = base64.b64encode(
            hmac_mod.new(b"k", b"a=x", hashlib.sha1).digest()
        ).decode("ascii")
        expected = raw.replace("+", "*").replace("/", "-").replace("=", "")
        assert sign_params({"a": ["x"]}, "k") == expected

    def test_multi_value_params_keep_input_order(self):
        from src.hicloud_tools import sign_params

        sig = sign_params({"instanceId": ["B", "A"]}, "k")
        expected_raw = hmac_mod.new(
            b"k", b"instanceid=b&instanceid=a", hashlib.sha1).digest()
        expected = base64.b64encode(expected_raw).decode("ascii")
        expected = expected.replace("+", "*").replace("/", "-").replace(
            "=", "")
        assert sig == expected

    def test_build_signed_url_encodes_and_appends_signature(self):
        from src.hicloud_tools import API_BASE, build_signed_url

        url = build_signed_url(API_BASE, _DOC_PARAMS, _DOC_SECRET)
        assert url.startswith(
            "https://hws.hicloud.hinet.net/cloud_hws/api/hws/?")
        assert "expires=2013-03-29T17%3A50%3A04Z" in url
        assert "instanceName=haha" in url
        assert url.endswith(f"&signature={_DOC_SIGNATURE}")

    def test_url_keeps_trailing_slash_before_question(self):
        from src.hicloud_tools import API_BASE, build_signed_url

        url = build_signed_url(API_BASE, {"action": ["x"]}, "k")
        assert "/cloud_hws/api/hws/?" in url


class TestEngineMockMode:
    def _engine(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_MODE", "mock")
        from src.hicloud_tools import HicloudEngine

        return HicloudEngine()

    def test_default_mode_is_mock(self):
        from src.hicloud_tools import HicloudEngine, MODE_MOCK

        assert HicloudEngine().mode == MODE_MOCK

    def test_invalid_mode_falls_back_to_mock(self, monkeypatch):
        from src.hicloud_tools import HicloudEngine, MODE_MOCK

        monkeypatch.setenv("HICLOUD_MODE", "yolo")
        assert HicloudEngine().mode == MODE_MOCK

    def test_list_instances_lists_mock_book(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.list_instances()
        assert r["simulated"] is True
        assert r["count"] == 3
        assert all(v["instanceId"].startswith("BV") for v in r["instances"])

    def test_list_instances_filters_by_id(self, monkeypatch):
        e = self._engine(monkeypatch)
        iid = e.list_instances()["instances"][0]["instanceId"]
        r = e.list_instances(instance_ids=[iid])
        assert r["count"] == 1 and r["instances"][0]["instanceId"] == iid

    def test_describe_images_mock(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.describe_images()
        assert r["count"] == 3
        r2 = e.describe_images(image_ids=["hi-mock00001"])
        assert r2["count"] == 1
        assert r2["images"][0]["imageId"] == "hi-mock00001"

    def test_instance_types_reference(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.list_instance_types()
        assert r["count"] >= 5
        r2 = e.list_instance_types(os_filter="windows")
        assert all(t["os"] == "WINDOWS" for t in r2["instanceTypes"])


class TestWriteGates:
    def _engine(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_MODE", "mock")
        from src.hicloud_tools import HicloudEngine

        return HicloudEngine()

    def test_writes_require_confirm(self, monkeypatch):
        e = self._engine(monkeypatch)
        for call in (
            lambda: e.start_instances(["BV1"]),
            lambda: e.stop_instances(["BV1"]),
            lambda: e.reboot_instances(["BV1"]),
            lambda: e.terminate_instances(["BV1"]),
            lambda: e.run_instances(image_id="hi-mock00001",
                                    instance_type="HC1.S.LINUX"),
        ):
            r = call()
            assert "error" in r and "confirm=true" in r["error"]

    def test_string_confirm_true_passes(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.start_instances(["BV550100000003"], confirm="true")
        assert "error" not in r
        assert r["statusMap"]["BV550100000003"] == "vm_start"

    def test_start_stop_reboot_lifecycle(self, monkeypatch):
        e = self._engine(monkeypatch)
        vm = e.list_instances()["instances"][2]  # dev-01 = stopped
        iid = vm["instanceId"]
        assert vm["operationStatus"] == "vm_stop"
        assert e.start_instances([iid], confirm=True)[
            "statusMap"][iid] == "vm_start"
        assert e.stop_instances([iid], confirm=True)[
            "statusMap"][iid] == "vm_stop"
        assert e.reboot_instances([iid], confirm=True)[
            "statusMap"][iid] == "vm_starting"
        assert e.list_instances(instance_ids=[iid])[
            "instances"][0]["operationStatus"] == "vm_starting"

    def test_unknown_instance_fails_gracefully(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.stop_instances(["BV-UNKNOWN"], confirm=True)
        assert "process fail" in r["statusMap"]["BV-UNKNOWN"]

    def test_run_instances_mock_creates_vms(self, monkeypatch):
        e = self._engine(monkeypatch)
        before = e.list_instances()["count"]
        r = e.run_instances(
            image_id="hi-mock00001",
            instance_type="HC1.caas-r1z1.b.2c8g-Linux",
            instance_name="demo-vm", count=2, confirm=True,
        )
        assert r["count"] == 2
        assert len(r["orderUuidList"]) == 2
        assert e.list_instances()["count"] == before + 2

    def test_run_requires_image_or_clone(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.run_instances(confirm=True)
        assert "error" in r and "image_id" in r["error"]

    def test_public_image_requires_instance_type(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.run_instances(image_id="hi-mock00001", confirm=True)
        assert "error" in r and "instance_type" in r["error"]

    def test_clone_mode_skips_type_requirement(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.run_instances(clone_instance_id="BV550100000001",
                            confirm=True)
        assert "error" not in r
        assert r["instances"][0]["clonedFrom"] == "BV550100000001"

    def test_terminate_removes_mock_vm(self, monkeypatch):
        e = self._engine(monkeypatch)
        before = e.list_instances()["count"]
        r = e.terminate_instances(["BV550100000001"], confirm=True)
        assert r["removed"] == ["BV550100000001"]
        assert e.list_instances()["count"] == before - 1

    def test_run_count_clamped(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.run_instances(image_id="hi-mock00001",
                            instance_type="HC1.S.LINUX",
                            count=99, confirm=True)
        assert r["count"] == 10  # MAX_RUN_COUNT


class TestLiveMode:
    def _live_engine(self, monkeypatch, **kw):
        monkeypatch.setenv("HICLOUD_MODE", "live")
        from src.hicloud_tools import HicloudEngine

        return HicloudEngine(**kw)

    def test_live_queries_without_creds_error(self, monkeypatch):
        e = self._live_engine(monkeypatch)
        r = e.list_instances()
        assert "error" in r and "HICLOUD_ACCESS_KEY" in r["error"]

    def test_live_writes_locked_without_writes_flag(self, monkeypatch):
        e = self._live_engine(monkeypatch)
        r = e.start_instances(["BV1"], confirm=True)
        assert "error" in r and "HICLOUD_WRITES_ENABLED" in r["error"]

    def test_live_writes_unlocked_no_http_without_creds(
        self, monkeypatch,
    ):
        monkeypatch.setenv("HICLOUD_WRITES_ENABLED", "on")
        e = self._live_engine(monkeypatch)
        r = e.stop_instances(["BV1"], confirm=True)
        assert "error" in r and "HICLOUD_ACCESS_KEY" in r["error"]

    def test_live_query_with_creds_and_mocked_http(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_ACCESS_KEY", "AK")
        monkeypatch.setenv("HICLOUD_SECRET_KEY", "SK")
        e = self._live_engine(monkeypatch)
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            captured["timeout"] = timeout
            return io.BytesIO(json.dumps({
                "requestId": "R1",
                "instanceList": [{"instanceId": "BV1",
                                  "operationStatus": "vm_start"}],
            }).encode("utf-8"))

        with patch("urllib.request.urlopen", fake_urlopen):
            r = e.list_instances(instance_ids=["BV1"])
        assert "error" not in r
        assert r["instances"][0]["instanceId"] == "BV1"
        assert r["simulated"] is False
        url = captured["url"]
        assert url.startswith(
            "https://hws.hicloud.hinet.net/cloud_hws/api/hws/?")
        assert "action=describeInstances" in url
        assert "accessKey=AK" in url
        assert "signature=" in url

    def test_live_lifecycle_maps_statusmap(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_ACCESS_KEY", "AK")
        monkeypatch.setenv("HICLOUD_SECRET_KEY", "SK")
        monkeypatch.setenv("HICLOUD_WRITES_ENABLED", "on")
        e = self._live_engine(monkeypatch)
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            return io.BytesIO(json.dumps({
                "requestId": "R2",
                "statusMap": {"BV1": "vm_stop"},
            }).encode("utf-8"))

        with patch("urllib.request.urlopen", fake_urlopen):
            r = e.stop_instances(["BV1"], confirm=True)
        assert r["statusMap"] == {"BV1": "vm_stop"}
        assert r["simulated"] is False
        assert "action=stopInstances" in captured["url"]

    def test_http_error_clean_envelope(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_ACCESS_KEY", "AK")
        monkeypatch.setenv("HICLOUD_SECRET_KEY", "SK")
        e = self._live_engine(monkeypatch)

        def raise_http(url, timeout=None):
            raise urllib.error.HTTPError(
                url, 403, "forbidden", {}, io.BytesIO(b"denied"))

        with patch("urllib.request.urlopen", raise_http):
            r = e.list_instances()
        assert "error" in r and "403" in r["error"]

    def test_non_json_response_clean_envelope(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_ACCESS_KEY", "AK")
        monkeypatch.setenv("HICLOUD_SECRET_KEY", "SK")
        e = self._live_engine(monkeypatch)
        with patch("urllib.request.urlopen",
                   lambda url, timeout=None: io.BytesIO(b"<html>404</html>")):
            r = e.list_instances()
        assert "error" in r and "JSON" in r["error"]

    def test_timeout_env_clamped(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_TIMEOUT_S", "999")
        e = self._live_engine(monkeypatch)
        assert e.timeout_s == 120.0
        monkeypatch.setenv("HICLOUD_TIMEOUT_S", "0.5")
        e2 = self._live_engine(monkeypatch)
        assert e2.timeout_s == 5.0

    def test_url_contains_signed_expires(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_ACCESS_KEY", "AK")
        monkeypatch.setenv("HICLOUD_SECRET_KEY", "SK")
        e = self._live_engine(monkeypatch)
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            return io.BytesIO(b"{}")

        with patch("urllib.request.urlopen", fake_urlopen):
            e.list_instances()
        url = captured["url"]
        m = re.search(r"expires=(\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}Z)",
                      url)
        assert m is not None


class TestHostSurface:
    def _host(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_MODE", "mock")
        from src.mcp_client.host import HicloudLocalHost

        return HicloudLocalHost()

    def test_eight_tools_advertised(self, monkeypatch):
        """v0.9.64 的 8 個手寫工具 + v0.9.66 的 48 個資料驅動工具 = 56。"""
        h = self._host(monkeypatch)
        tools = h.list_tools()
        names = [t.name for t in tools]
        base8 = [
            "hicloud_list_instances",
            "hicloud_describe_images",
            "hicloud_list_instance_types",
            "hicloud_run_instances",
            "hicloud_start_instances",
            "hicloud_stop_instances",
            "hicloud_reboot_instances",
            "hicloud_terminate_instances",
        ]
        assert names[:8] == base8
        assert len(names) == 56
        assert len(set(names)) == 56  # no duplicates
        # the 48 data-driven specs carry server_name + schema too
        assert all(t.server_name == "hicloud-local" for t in tools)

    def test_new_service_tools_advertised(self, monkeypatch):
        """v0.9.66 — the 48 data-driven tools cover every doc action."""
        h = self._host(monkeypatch)
        by_name = {t.name: t for t in h.list_tools()}
        expected = [
            "hicloud_modify_instance_attribute",
            "hicloud_restore_instance",
            "hicloud_get_instance_stats",
            "hicloud_log_list",
            "hicloud_volume_create",
            "hicloud_volume_attach",
            "hicloud_volume_detach",
            "hicloud_volume_delete",
            "hicloud_volume_list",
            "hicloud_image_create",
            "hicloud_image_delete",
            "hicloud_image_modify",
            "hicloud_slb_create",
            "hicloud_slb_delete",
            "hicloud_slb_list",
            "hicloud_slb_vip_health",
            "hicloud_slb_replace",
            "hicloud_subnet_create",
            "hicloud_subnet_delete",
            "hicloud_subnet_list",
            "hicloud_subnet_modify",
            "hicloud_acl_create",
            "hicloud_acl_delete",
            "hicloud_acl_list",
            "hicloud_acl_replace",
            "hicloud_vpc_create",
            "hicloud_vpc_delete",
            "hicloud_vpc_list",
            "hicloud_vpc_modify",
            "hicloud_igw_create",
            "hicloud_igw_delete",
            "hicloud_igw_list",
            "hicloud_internet_ip_create",
            "hicloud_internet_ip_delete",
            "hicloud_internet_ip_list",
            "hicloud_intranet_gw_create",
            "hicloud_intranet_gw_delete",
            "hicloud_intranet_gw_list",
            "hicloud_intranet_gw_modify",
            "hicloud_intranet_link_create",
            "hicloud_intranet_link_delete",
            "hicloud_intranet_link_list",
            "hicloud_nat_create",
            "hicloud_nat_delete",
            "hicloud_nat_list",
            "hicloud_pfwd_create",
            "hicloud_pfwd_delete",
            "hicloud_pfwd_list",
        ]
        for name in expected:
            assert name in by_name, name
            schema = by_name[name].input_schema
            assert schema["type"] == "object", name
            assert schema.get("properties"), name

    def test_lifecycle_schemas_require_confirm_field(self, monkeypatch):
        h = self._host(monkeypatch)
        by_name = {t.name: t for t in h.list_tools()}
        for name in ("hicloud_run_instances", "hicloud_start_instances",
                     "hicloud_stop_instances", "hicloud_reboot_instances",
                     "hicloud_terminate_instances",
                     "hicloud_modify_instance_attribute",
                     "hicloud_restore_instance",
                     "hicloud_volume_create",
                     "hicloud_volume_attach",
                     "hicloud_volume_detach",
                     "hicloud_volume_delete",
                     "hicloud_image_create",
                     "hicloud_image_delete",
                     "hicloud_image_modify",
                     "hicloud_slb_create",
                     "hicloud_slb_delete",
                     "hicloud_slb_replace",
                     "hicloud_subnet_create",
                     "hicloud_subnet_delete",
                     "hicloud_subnet_modify",
                     "hicloud_acl_create",
                     "hicloud_acl_delete",
                     "hicloud_acl_replace",
                     "hicloud_vpc_create",
                     "hicloud_vpc_delete",
                     "hicloud_vpc_modify",
                     "hicloud_igw_create",
                     "hicloud_igw_delete",
                     "hicloud_internet_ip_create",
                     "hicloud_internet_ip_delete",
                     "hicloud_intranet_gw_create",
                     "hicloud_intranet_gw_delete",
                     "hicloud_intranet_gw_modify",
                     "hicloud_intranet_link_create",
                     "hicloud_intranet_link_delete",
                     "hicloud_nat_create",
                     "hicloud_nat_delete",
                     "hicloud_pfwd_create",
                     "hicloud_pfwd_delete"):
            props = by_name[name].input_schema["properties"]
            assert "confirm" in props, name
        for name in ("hicloud_start_instances", "hicloud_stop_instances",
                     "hicloud_reboot_instances",
                     "hicloud_terminate_instances"):
            assert by_name[name].input_schema["required"] == [
                "instance_ids"], name

    def test_handlers_end_to_end(self, monkeypatch):
        h = self._host(monkeypatch)
        r = h._list_instances({})
        assert r["count"] == 3
        iid = r["instances"][0]["instanceId"]
        r2 = h._start_instances({"instance_ids": [iid], "confirm": True})
        assert r2["statusMap"][iid] == "vm_start"
        r3 = h._run_instances({
            "image_id": "hi-mock00001",
            "instance_type": "HC1.S.LINUX",
            "instance_name": "e2e", "confirm": True,
        })
        assert r3["count"] == 1

    def test_new_service_handlers_e2e(self, monkeypatch):
        """v0.9.66 — data-driven dispatch round-trips (mock book)."""
        h = self._host(monkeypatch)
        r = h._engine_call("volume_create", {
            "instance_id": "BV550100000001", "radix": 1,
            "volume_name": "v1", "confirm": True})
        assert r["orderUuidList"]
        vid = r["volumeId"][0]
        assert h._engine_call("volume_attach", {
            "volume_id": vid, "instance_id": "BV550100000002",
            "confirm": True})["statusMap"]
        assert h._engine_call("volume_detach", {
            "volume_id": vid, "confirm": True})["statusMap"]
        assert h._engine_call("volume_delete", {
            "volume_ids": [vid], "confirm": True})["removed"] == [vid]
        assert h._engine_call("slb_create", {
            "instance_ids": ["BV550100000001"], "ports": [80],
            "confirm": True})["orderUuidList"]
        assert h._engine_call("slb_vip_health", {
            "load_balancer_ids": ["VL550100000001"]})["healthList"]
        assert h._engine_call("vpc_create", {
            "vpc_name": "v1", "confirm": True})["orderUuidList"]
        assert h._engine_call("subnet_create", {
            "subnet": "10.20.0.0/24", "vpc_bsid": "VV550100000001",
            "confirm": True})["orderUuidList"]
        assert h._engine_call("acl_create", {
            "policy_strings": ["1;;1;;all;;accept;;any"],
            "vpc_bsid": "VV550100000001",
            "confirm": True})["orderUuidList"]
        assert h._engine_call("igw_create", {
            "vpc_bsid": "VV550100000001", "confirm": True})["orderUuidList"]
        assert h._engine_call("internet_ip_create", {
            "internet_bsid": "VI550100000001",
            "confirm": True})["orderUuidList"]
        assert h._engine_call("intranet_gw_create", {
            "region_id": "region-tw-1", "confirm": True})["orderUuidList"]
        assert h._engine_call("intranet_link_create", {
            "vpc_bsid": "VV550100000001",
            "intranet_gateway_bsid": "VG550100000001",
            "confirm": True})["orderUuidList"]
        assert h._engine_call("nat_create", {
            "vpc_bsid": "VV550100000001", "original_ip": "203.0.0.1",
            "translated_ip": "10.20.0.5", "confirm": True})["orderUuidList"]
        assert h._engine_call("pfwd_create", {
            "vpc_bsid": "VV550100000001", "original_ip": "203.0.0.1",
            "translated_ip": "10.20.0.5", "original_port": 8080,
            "translated_port": 80, "confirm": True})["orderUuidList"]
        assert h._engine_call("modify_instance_attribute", {
            "instance_id": "BV550100000003",
            "instance_type": "HC1.caas-r1z1.b.4c16g-Linux",
            "confirm": True})["statusMap"]
        assert h._engine_call("restore_instance", {
            "snapshot_id": "SNAP1", "confirm": True})["statusMap"]
        assert h._engine_call("get_instance_stats", {
            "instance_ids": ["BV550100000001"]})["InstancesStatsList"]
        assert h._engine_call("log_list", {
            "start_date": "2026-01-01T00:00:00Z"})["logList"]

    # ── v0.9.66 — every lifecycle write requires confirm=true ──

    def test_new_write_gates(self, monkeypatch):
        h = self._host(monkeypatch)
        for method, args in (
            ("volume_create", {"instance_id": "BV1", "radix": 1}),
            ("volume_attach", {"volume_id": "V1", "instance_id": "B1"}),
            ("volume_detach", {"volume_id": "V1"}),
            ("volume_delete", {"volume_ids": ["V1"]}),
            ("image_create", {"instance_id": "B1"}),
            ("image_delete", {"image_ids": ["B1"]}),
            ("image_modify", {"image_id": "B1", "name": "x"}),
            ("slb_create", {"instance_ids": ["B1"], "ports": [80]}),
            ("slb_delete", {"load_balancer_ids": ["L1"]}),
            ("slb_replace", {"load_balancer_id": "L1"}),
            ("subnet_create", {"subnet": "10.0.0.0/24",
                               "intranet_gateway_bsid": "G1"}),
            ("subnet_delete", {"subnet_bsids": ["S1"]}),
            ("subnet_modify", {"subnet_bsid": "S1", "name": "x"}),
            ("acl_create", {"policy_strings": ["1;;1;;all;;accept;;any"],
                            "instance_id": "B1"}),
            ("acl_delete", {"network_acl_ids": ["F1"]}),
            ("acl_replace", {"network_acl_id": "F1",
                             "policy_strings": ["1;;1;;all;;accept;;any"]}),
            ("vpc_create", {"vpc_name": "v"}),
            ("vpc_delete", {"vpc_bsids": ["V"]}),
            ("vpc_modify", {"vpc_bsid": "V", "name": "x"}),
            ("igw_create", {"vpc_bsid": "V"}),
            ("igw_delete", {"internet_bsids": ["I"]}),
            ("internet_ip_create", {"internet_bsid": "I"}),
            ("internet_ip_delete", {"internet_ip_bsids": ["IP1"]}),
            ("intranet_gw_create", {"region_id": "region-tw-1"}),
            ("intranet_gw_delete", {"intranet_gateway_bsids": ["G"]}),
            ("intranet_gw_modify", {"intranet_gateway_bsid": "G"}),
            ("intranet_link_create", {"vpc_bsid": "V",
                                      "intranet_gateway_bsid": "G"}),
            ("intranet_link_delete", {"intranet_link_bsids": ["IL1"]}),
            ("nat_create", {"vpc_bsid": "V", "original_ip": "1.2.3.4",
                            "translated_ip": "10.0.0.5"}),
            ("nat_delete", {"nat_bsids": ["N1"]}),
            ("pfwd_create", {"vpc_bsid": "V", "original_ip": "1.2.3.4",
                             "translated_ip": "10.0.0.5",
                             "original_port": 8080,
                             "translated_port": 80}),
            ("pfwd_delete", {"port_fwd_bsids": ["PF1"]}),
            ("modify_instance_attribute", {"instance_id": "BV1"}),
            ("restore_instance", {"snapshot_id": "S1"}),
        ):
            r = h._engine_call(method, args)
            assert "error" in r and "confirm=true" in r["error"], method

    def test_wrapper_dict_coercion(self, monkeypatch):
        """v0.8.6.1 — LLM 常把參數包在 {"args": {...}} — coerced OK."""
        from src.mcp_client.host import _coerce_arguments, HicloudLocalHost

        h = self._host(monkeypatch)
        r = h._start_instances(_coerce_arguments(
            {"args": {"instance_ids": ["BV550100000003"],
                      "confirm": True}}))
        assert r["statusMap"]["BV550100000003"] == "vm_start"
        # sanity — the coercion helper exists for the shape above
        assert _coerce_arguments({"args": {"a": 1}}) == {"a": 1}


class TestManagerWiring:
    """from_config registers hicloud-local alongside the other hosts."""

    def _mgr(self, tmp_path):
        from src.mcp_client.manager import McpManager

        cfg = tmp_path / "mcp.json"
        cfg.write_text('{"servers": []}', encoding="utf-8")
        return McpManager.from_config(cfg)

    def test_from_config_registers_hicloud_host(self, tmp_path,
                                                monkeypatch):
        # setenv (NOT delenv) — Agent-side load_dotenv(override=False)
        # re-injects the live .env gate; an explicit env value wins.
        monkeypatch.setenv("MCP_ACTIVE_SERVERS",
                           "hicloud-local,meta-local")
        mgr = self._mgr(tmp_path)
        try:
            names = [t.name for t in mgr.active_tools()]
            assert "hicloud_list_instances" in names
            assert "hicloud_terminate_instances" in names
            assert mgr._hicloud_local_host is not None
            assert mgr._hicloud_local_host.SERVER_NAME == "hicloud-local"
        finally:
            mgr.close_all()

    def test_call_dispatches_to_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_ACTIVE_SERVERS",
                           "hicloud-local,meta-local")
        mgr = self._mgr(tmp_path)
        try:
            res = mgr.call("hicloud_list_instance_types", {})
            assert not res.is_error
            payload = json.loads(res.text)
            assert payload["count"] >= 5
        finally:
            mgr.close_all()


class TestStdioParity:
    def test_specs_match_host(self, monkeypatch):
        monkeypatch.setenv("HICLOUD_MODE", "mock")
        from src.mcp_client.host import HicloudLocalHost
        from src.mcp_server.hicloud_server import HicloudMCPServer

        server = HicloudMCPServer()
        host_names = [t.name for t in HicloudLocalHost().list_tools()]
        server_names = [t.name for t in server._tool_specs()]
        assert server_names == host_names

    def test_stdio_module_importable(self):
        import src.mcp_server.hicloud_server as m

        assert hasattr(m, "HicloudMCPServer")
        assert hasattr(m, "main")


class TestSystemPrompt:
    """Agent prompt gains the hicloud trigger block (tools only)."""

    @pytest.fixture
    def stub_agent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        monkeypatch.setenv("HICLOUD_MODE", "mock")
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        a._mcp_manager = None
        return a

    def test_block_header_present(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "hicloud 雲伺服器 (v0.9.64.0+)" in prompt

    def test_prompt_names_all_eight_tools(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        for tool in ("hicloud_list_instances", "hicloud_describe_images",
                     "hicloud_list_instance_types", "hicloud_run_instances",
                     "hicloud_start_instances", "hicloud_stop_instances",
                     "hicloud_reboot_instances",
                     "hicloud_terminate_instances"):
            assert tool in prompt

    def test_prompt_documents_gates_and_mock(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "confirm=true" in prompt
        assert "HICLOUD_WRITES_ENABLED" in prompt
        assert "HICLOUD_ACCESS_KEY" in prompt
        assert "simulated" in prompt

    def test_prompt_lists_trigger_keywords(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        for kw in ("hicloud", "雲伺服器", "開一台 VM", "雲伺服器清單"):
            assert kw in prompt, f"missing trigger {kw!r}"

    def test_block_not_in_legacy_mode(self, stub_agent):
        legacy = stub_agent._system_prompt(tools_enabled=False)
        assert "hicloud_list_instances" not in legacy
        assert "hicloud 雲伺服器" not in legacy

    def test_composes_with_response_language(self, stub_agent):
        prompt = stub_agent._system_prompt(
            tools_enabled=True, response_language="zh-TW",
        )
        assert "hicloud 雲伺服器 (v0.9.64.0+)" in prompt
        assert "LANGUAGE DIRECTIVE" in prompt