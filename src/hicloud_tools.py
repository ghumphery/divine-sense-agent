"""hicloud HWS (中華電信 hicloud CaaS / CVPC) 雲服務操作 — MCP core.

參考文件: https://hws.hicloud.hinet.net/hws-doc/zh_TW/
(REST — HTTPS GET 查詢字串; 認證 = AccessKey + SecretKey 簽章:
 參數按 key 自然排序 → ``k=v`` 以 & 連接 → 整串轉小寫 →
 HMAC-SHA1(SecretKey) → Base64 → '+'→'*' '/'→'-' '='→刪除。
 本模組的簽章以官方文件範例為測試向量, 完全匹配。)

雙模式設計（安全第一, 與 mega_stock_tools 同構）:

    HICLOUD_MODE=mock  預設。開發/測試用：完全模擬 — 不連線
                       hws.hicloud.hinet.net、不送出任何真實 API
                       呼叫。VM 清單 / 範本 / 生命週期操作全部在
                       記憶體模擬（回應標記 simulated=true）。
    HICLOUD_MODE=live  真實模式。需要 HICLOUD_ACCESS_KEY +
                       HICLOUD_SECRET_KEY（hicloud 用戶平台 /
                       CloudBOSS 入口取得）。

生命週期寫入兩道閘（run/start/stop/reboot/terminate）:
    1. HICLOUD_MODE=live 才會碰真 API（mock 模擬、無風險）
    2. 每次操作參數 confirm=True（防意外操作）
    live 模式另有總開關 HICLOUD_WRITES_ENABLED（預設 off）—
    off 時 live 寫入一律鎖住。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = [
    "HicloudEngine", "MODE_MOCK", "MODE_LIVE", "DEFAULT_MODE",
    "API_BASE", "API_VERSION", "sign_params", "build_signed_url",
]

MODE_MOCK = "mock"
MODE_LIVE = "live"
DEFAULT_MODE = MODE_MOCK

# 官方 REST 端點 (文件「呼叫REST的方法」): HTTPS :443
API_BASE = "https://hws.hicloud.hinet.net/cloud_hws/api/hws/"
API_VERSION = "2013-03-29"          # version=2013-03-29 (文件值)
DEFAULT_TIMEOUT_S = 30
MIN_TIMEOUT_S = 5
MAX_TIMEOUT_S = 120

# 狀態值 (文件 stopInstances / describeInstances Response):
STATUS_RUNNING = "vm_start"
STATUS_STARTING = "vm_starting"
STATUS_STOPPED = "vm_stop"
STATUS_STOPPING = "vm_stopping"

# 寫入動作 → HWS action 名稱
ACTION_RUN = "runInstances"
ACTION_START = "startInstances"
ACTION_STOP = "stopInstances"
ACTION_REBOOT = "rebootInstances"
ACTION_TERMINATE = "terminateInstances"
ACTION_DESCRIBE_INSTANCES = "describeInstances"
ACTION_DESCRIBE_IMAGES = "describeImages"

# v0.9.66.0 — full-service actions（文件： hicloud_rest_api_v2.md）
ACTION_MODIFY_INSTANCE_ATTRIBUTE = "modifyInstanceAttribute"
ACTION_RESTORE_INSTANCE = "restoreInstance"
ACTION_GET_INSTANCES_STATS = "getInstancesStats"
ACTION_LIST_LOG = "listLog"
ACTION_ATTACH_VOLUME = "attachVolume"
ACTION_DETACH_VOLUME = "detachVolume"
ACTION_QUERY_SLB_VIP_HEALTH = "querySlbVIPHealthStatus"
ACTION_REPLACE_SLB = "replaceLoadBalancerPolicy"
ACTION_REPLACE_ACL = "replaceNetworkAcl"

# 每次最多建立的 VM 數 (文件: count 最大 10、最小 1)
MAX_RUN_COUNT = 10

# v0.9.66.0 — generic resource specs for the data-driven CRUD
# (mock book + live action mapping + id-field/param names, per the
# official doc sections 4.x–14.x).
_RES_SPECS: dict[str, dict[str, str]] = {
    "volume": {
        "id_field": "volumeId", "id_param": "volumeId",
        "list_action": "describeVolumes", "list_key": "volumeList",
        "create_action": "createVolume",
        "delete_action": "deleteVolume", "delete_id_param": "volumeId",
        "mock_prefix": "VD55010", "modify_action": None,
    },
    "slb": {
        "id_field": "loadBalancerId", "id_param": "loadBalancerId",
        "list_action": "describeLoadBalancerPolicies",
        "list_key": "loadBalancerList",
        "create_action": "createLoadBalancerPolicy",
        "delete_action": "deleteLoadBalancerPolicy",
        "delete_id_param": "loadBalancerId",
        "mock_prefix": "VL55010", "modify_action": None,
    },
    "image_user": {
        "id_field": "imageId", "id_param": "imageId",
        "list_action": "describeImages", "list_key": "imageList",
        "create_action": "createImage",
        "delete_action": "deleteImage", "delete_id_param": "imageId",
        "mock_prefix": "BT55010",
        "modify_action": "modifyImageAttribute",
    },
    "subnet": {
        "id_field": "subnetBusinessServiceId",
        "id_param": "subnetBusinessServiceId",
        "list_action": "describeSubnets", "list_key": "subnetList",
        "create_action": "createSubnet",
        "delete_action": "deleteSubnet",
        "delete_id_param": "subnetBusinessServiceId",
        "mock_prefix": "VS55010",
        "modify_action": "modifySubnetAttribute",
    },
    "acl": {
        "id_field": "networkAclId", "id_param": "networkAclId",
        "list_action": "describeNetworkAcls",
        "list_key": "networkAclList",
        "create_action": "createNetworkAcl",
        "delete_action": "deleteNetworkAcl",
        "delete_id_param": "networkAclId",
        "mock_prefix": "VF55010", "modify_action": None,
    },
    "vpc": {
        "id_field": "vpcBusinessServiceId",
        "id_param": "vpcBusinessServiceId",
        "list_action": "describeVpcs", "list_key": "vpcList",
        "create_action": "createVpc",
        "delete_action": "deleteVpc",
        "delete_id_param": "vpcBusinessServiceIdlist",
        "mock_prefix": "VV55010",
        "modify_action": "modifyVpcAttribute",
    },
    "igw": {
        "id_field": "internetBusinessServiceId",
        "id_param": "internetBusinessServiceId",
        "list_action": "describeInternetGateways",
        "list_key": "internetGatewayList",
        "create_action": "createInternetGateway",
        "delete_action": "deleteInternetGateway",
        "delete_id_param": "internetBusinessServiceId",
        "mock_prefix": "VI55010", "modify_action": None,
    },
    "internet_ip": {
        "id_field": "internetIpBusinessServiceId",
        "id_param": "internetIpBusinessServiceId",
        "list_action": "describeInternetIps",
        "list_key": "internetIpList",
        "create_action": "createInternetIp",
        "delete_action": "deleteInternetIp",
        "delete_id_param": "internetIpBusinessServiceId",
        "mock_prefix": "IN55010", "modify_action": None,
    },
    "intranet_gw": {
        "id_field": "intranetGatewayBusinessServiceId",
        "id_param": "intranetGatewayBusinessServiceId",
        "list_action": "describeIntranetGateways",
        "list_key": "intranetGatewayList",
        "create_action": "createIntranetGateway",
        "delete_action": "deleteIntranetGateway",
        "delete_id_param": "intranetGatewayBusinessServiceId",
        "mock_prefix": "VG55010",
        "modify_action": "modifyIntranetGatewayAttribute",
    },
    "intranet_link": {
        "id_field": "intranetLinkBusinessServiceId",
        "id_param": "intranetLinkBusinessServiceId",
        "list_action": "describeIntranetLinks",
        "list_key": "intranetLinkList",
        "create_action": "createIntranetLink",
        "delete_action": "deleteIntranetLink",
        "delete_id_param": "intranetLinkBusinessServiceId",
        "mock_prefix": "IL55010", "modify_action": None,
    },
    "nat": {
        "id_field": "natBusinessServiceId", "id_param": "natBusinessServiceId",
        "list_action": "describeNats", "list_key": "natList",
        "create_action": "createNat",
        "delete_action": "deleteNat",
        "delete_id_param": "natBusinessServiceId",
        "mock_prefix": "NT55010", "modify_action": None,
    },
    "port_fwd": {
        "id_field": "portFwdBusinessServiceId",
        "id_param": "portFwdBusinessServiceId",
        "list_action": "describePortFwds", "list_key": "portFwdList",
        "create_action": "createPortFwd",
        "delete_action": "deletePortFwd",
        "delete_id_param": "portFwdBusinessServiceId",
        "mock_prefix": "PF55010", "modify_action": None,
    },
}

# v0.9.66.0 — generic resource specs for the data-driven CRUD
# (mock book + live action mapping + id-field/param names, per the
# official doc sections 4.x–14.x).


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _clamp_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sign_params(params: dict[str, list[str]], secret_key: str) -> str:
    """hicloud HWS 簽章（官方文件 Authentication of API Calling）:

    1. 參數按 key 自然排序（同 key 保持輸入順序 — 多值參數）
    2. ``k=v`` 用 ``&`` 連接
    3. 整串轉小寫（key 與 value 一併）
    4. HMAC-SHA1(secret_key, string) → Base64
    5. Base64 結果特殊字元替換: '+'→'*'、'/'→'-'、'='→刪除
    """
    items: list[str] = []
    for key in sorted(params):
        for value in params[key]:
            items.append(f"{key}={value}")
    cmd = "&".join(items).lower()
    digest = hmac.new(
        secret_key.encode("utf-8"), cmd.encode("utf-8"), hashlib.sha1,
    ).digest()
    sig = base64.b64encode(digest).decode("ascii")
    return sig.replace("+", "*").replace("/", "-").replace("=", "")


def build_signed_url(base_url: str, params: dict[str, list[str]],
                     secret_key: str) -> str:
    """組出帶簽章的完整 URL。

    - 參數值 URL 編碼（空格→%20、':'→%3A — 文件「呼叫REST的方法」）
    - signature 本身 ('*' '-' 安全字元) 原樣附加在 URL 最後
    """
    sig = sign_params(params, secret_key)
    pairs: list[str] = []
    for key in sorted(params):
        for value in params[key]:
            pairs.append(
                f"{urllib.parse.quote(str(key), safe='')}="
                f"{urllib.parse.quote(str(value), safe='')}"
            )
    base = base_url if base_url.endswith("/") else base_url + "/"
    return f"{base}?" + "&".join(pairs) + f"&signature={sig}"


# ── Mock 公用範本 / 運算規格（確定性內建資料） ────────────────────

_MOCK_IMAGES: list[dict[str, Any]] = [
    {
        "orderUuid": "11111111-1111-4a2a-8078-ca0a29deeff0",
        "imageId": "hi-mock00001",
        "instanceId": None,
        "applyDate": "2026-01-01T01:00:00Z",
        "startDate": "2026-01-01T01:01:00Z",
        "endDate": None,
        "name": "公用範本 — Linux 通用 (mock)",
        "description": "Linux 公用範本 (模擬資料)",
        "provisionStatus": "provisionok",
        "provisionMessage": None,
        "businessTag": "a2931aa4-8751-4f09-8df3-bcc0d18f8b75",
        "regionId": "region-tw-1",
    },
    {
        "orderUuid": "22222222-2222-4df4-bd93-4f355c85b2d8",
        "imageId": "hi-mock00002",
        "instanceId": None,
        "applyDate": "2026-01-01T01:00:00Z",
        "startDate": "2026-01-01T01:01:00Z",
        "endDate": None,
        "name": "公用範本 — Windows 通用 (mock)",
        "description": "Windows 公用範本 (模擬資料)",
        "provisionStatus": "provisionok",
        "provisionMessage": None,
        "businessTag": "cb168f20-c515-4e41-84cd-830dedfe7f49",
        "regionId": "region-tw-1",
    },
    {
        "orderUuid": "33333333-3333-4a2a-8078-ca0a29deeff0",
        "imageId": "BT550100010001",
        "instanceId": "BV550100010001",
        "applyDate": "2026-01-02T01:00:00Z",
        "startDate": "2026-01-02T01:02:00Z",
        "endDate": None,
        "name": "用戶範本 — myTemplate (mock)",
        "description": "由既有 VM 製作的用戶範本 (模擬資料)",
        "provisionStatus": "provisionok",
        "provisionMessage": None,
        "businessTag": "a2931aa4-8751-4f09-8df3-bcc0d18f8b75",
        "regionId": "region-tw-1",
    },
]

# 運算規格（官方「產品與運算規格資訊表」節錄 + 舊式短名樣例）。
# 完整表: /hws-doc/api/hwsipm/listProductPackage?locale=zh_TW
INSTANCE_TYPES: tuple[dict[str, Any], ...] = (
    {"instanceType": "HC1.S.LINUX", "os": "LINUX", "note": "舊式短名樣例"},
    {"instanceType": "HC1.S.WIN", "os": "WINDOWS", "note": "舊式短名樣例"},
    {"instanceType": "HC1.caas-r1z1.b.1c2g-Linux", "os": "LINUX",
     "note": "通用型 1 vCPU / 2GB (region-tw-1 CaaS)"},
    {"instanceType": "HC1.caas-r1z1.b.2c8g-Linux", "os": "LINUX",
     "note": "通用型 2 vCPU / 8GB (region-tw-1 CaaS)"},
    {"instanceType": "HC1.caas-r1z1.b.4c16g-Linux", "os": "LINUX",
     "note": "通用型 4 vCPU / 16GB (region-tw-1 CaaS)"},
    {"instanceType": "HC1.caas-r1z1.b.8c32g-Linux", "os": "LINUX",
     "note": "通用型 8 vCPU / 32GB (region-tw-1 CaaS)"},
    {"instanceType": "HC1.caas-r1z1.c.2c4g-Linux", "os": "LINUX",
     "note": "運算優化型 2 vCPU / 4GB (region-tw-1 CaaS)"},
    {"instanceType": "HC1.caas-r1z1.m.2c16g-Linux", "os": "LINUX",
     "note": "記憶體優化型 2 vCPU / 16GB (region-tw-1 CaaS)"},
    {"instanceType": "HC1.caas-r1z1.b.2c8g-Windows", "os": "WINDOWS",
     "note": "通用型 2 vCPU / 8GB (region-tw-1 CaaS)"},
    {"instanceType": "HC5.cvpc-r1z1.b.2c8g-Linux", "os": "LINUX",
     "note": "CVPC 通用型 2 vCPU / 8GB (region-tw-1)"},
    {"instanceType": "HC5.cvpc-r1z1.b.4c16g-Linux", "os": "LINUX",
     "note": "CVPC 通用型 4 vCPU / 16GB (region-tw-1)"},
)


class HicloudEngine:
    """Mock-first engine for hicloud HWS CaaS / CVPC operations.

    `mode` comes from ``HICLOUD_MODE`` (mock | live; default mock).
    Writes (run/start/stop/reboot/terminate) additionally require a
    per-call ``confirm=True``; live writes also require
    ``HICLOUD_WRITES_ENABLED=on``.
    """

    def __init__(self, mode: str | None = None,
                 writes_enabled: str | None = None):
        self.mode = (mode or os.environ.get("HICLOUD_MODE", "")
                     or DEFAULT_MODE).strip().lower()
        if self.mode not in (MODE_MOCK, MODE_LIVE):
            self.mode = MODE_MOCK
        flag = (writes_enabled
                or os.environ.get("HICLOUD_WRITES_ENABLED", "")).strip().lower()
        self.writes_enabled = flag in ("on", "1", "true", "yes")
        # mock 記憶體狀態
        self._mock_vms: list[dict[str, Any]] = [
            self._make_mock_vm(1, name="web-01", status=STATUS_RUNNING),
            self._make_mock_vm(2, name="db-01", status=STATUS_RUNNING),
            self._make_mock_vm(3, name="dev-01", status=STATUS_STOPPED),
        ]
        self._mock_next_seq = 4
        self._mock_images: list[dict[str, Any]] = [
            dict(img) for img in _MOCK_IMAGES]
        # v0.9.66.0 — mock resource books (in-memory; NO HTTP)
        self._mock_resources: dict[str, list[dict[str, Any]]] = {
            "volume": [], "slb": [], "image_user": [], "subnet": [],
            "acl": [], "vpc": [], "igw": [], "internet_ip": [],
            "intranet_gw": [], "intranet_link": [], "nat": [],
            "port_fwd": [],
        }
        self._mock_res_seq: dict[str, int] = {
            k: 1 for k in self._mock_resources}

    # ── 公用資訊 ────────────────────────────────────────────────

    @property
    def is_mock(self) -> bool:
        return self.mode == MODE_MOCK

    @property
    def timeout_s(self) -> float:
        return _clamp_float(
            os.environ.get("HICLOUD_TIMEOUT_S", ""), DEFAULT_TIMEOUT_S,
            MIN_TIMEOUT_S, MAX_TIMEOUT_S,
        )

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "simulated": self.is_mock,
            "writes_enabled": self.writes_enabled,
            "api_base": API_BASE,
            "api_version": API_VERSION,
            "live_creds_set": self._live_creds() is not None,
            "timeout_s": self.timeout_s,
        }

    # ── mock helpers ────────────────────────────────────────────

    def _make_mock_vm(self, seq: int, *, name: str = "mock-vm",
                      status: str = STATUS_STOPPED) -> dict[str, Any]:
        ts = "2026-01-01T01:01:01Z"
        return {
            "orderUuid": str(uuid.uuid5(
                uuid.NAMESPACE_DNS, f"mock-vm-{seq}")),
            "instanceId": f"BV55010{seq:07d}",
            "applyDate": ts,
            "startDate": ts if status == STATUS_RUNNING else None,
            "endDate": None,
            "name": name,
            "operationStatus": status,
            "provisionStatus": "provisionok",
            "provisionMessage": None,
            "ipForNat": f"172.16.0.{seq}",
            "ipForVm": f"192.168.{seq}.1",
            "businessTag": "a2931aa4-8751-4f09-8df3-bcc0d18f8b75",
            "regionId": "region-tw-1",
            "simulated": True,
        }

    def _mock_note(self) -> str:
        return ("mock 模式 — 資料僅存在記憶體，未呼叫任何真實 hicloud "
                "API。live 需 HICLOUD_MODE=live + AccessKey/SecretKey；"
                "寫入操作另需 confirm=true（live 並需 "
                "HICLOUD_WRITES_ENABLED=on）。")

    # ── 寫入閘 ──────────────────────────────────────────────────

    def _gate_write(self, what: str, confirm: Any) -> str | None:
        """生命週期寫入的兩道閘。回 None = 通過；回字串 = 錯誤訊息。"""
        if not (confirm is True or str(confirm).lower()
                in ("true", "1", "yes", "on")):
            return (
                f"{what} 需要 confirm=true（防意外操作 — 會影響雲伺服器"
                "狀態/計費，需使用者明確指示才可確認）"
            )
        if not self.is_mock and not self.writes_enabled:
            return (
                f"{what} 未啟用：live 寫入需 HICLOUD_WRITES_ENABLED=on"
                "（總開關，預設 off）。開發/測試請保持 "
                "HICLOUD_MODE=mock。"
            )
        return None

    def _live_creds(self) -> tuple[str, str] | None:
        ak = os.environ.get("HICLOUD_ACCESS_KEY", "").strip()
        sk = os.environ.get("HICLOUD_SECRET_KEY", "").strip()
        if not ak or not sk:
            return None
        return ak, sk

    # ── live HTTP ───────────────────────────────────────────────

    def _api_get(self, action: str,
                 extra: dict[str, list[str]] | None = None) -> dict:
        """呼叫 HWS REST API（GET + 簽章查詢字串）。

        Response 以 JSON 回傳（文件「各項欄位和說明」）。
        HTTP 非 200 / JSON 解析失敗 → 回 ``{"error": ...}`` 包絡。
        """
        creds = self._live_creds()
        if creds is None:
            return {"error": (
                "live 模式需要 HICLOUD_ACCESS_KEY 與 HICLOUD_SECRET_KEY"
                "（hicloud 用戶平台取得）。開發/測試請使用 "
                "HICLOUD_MODE=mock。"
            ), "mode": self.mode}
        access_key, secret_key = creds
        expires = (datetime.now(timezone.utc) + timedelta(minutes=15)
                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
        params: dict[str, list[str]] = {
            "action": [action],
            "version": [API_VERSION],
            "chtAuthType": ["hwspass"],
        }
        if extra:
            for k, vs in extra.items():
                vals = [str(v) for v in vs if v is not None]
                if vals:
                    params[k] = vals
        params["accessKey"] = [access_key]
        params["expires"] = [expires]
        url = build_signed_url(API_BASE, params, secret_key)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            text = b""
            try:
                text = e.read()
            except Exception:
                pass
            msg = text.decode("utf-8", "replace")[:2000] if text else ""
            return {"error": f"hicloud API HTTP {e.code}: {msg or e.reason}"}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return {"error": f"hicloud API 連線失敗: {e!r}"}
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            return {"error": (
                f"hicloud API 回應非 JSON: {e!r}; body[:400]={body[:400]!r}"
            )}

    # ── live 動作 wrappers ──────────────────────────────────────

    def _live_list_instances(
        self, instance_ids: list[str] | None,
        order_uuids: list[str] | None,
    ) -> dict:
        extra: dict[str, list[str]] = {}
        if instance_ids:
            extra["instanceId"] = [str(v) for v in instance_ids]
        if order_uuids:
            extra["orderUuid"] = [str(v) for v in order_uuids]
        result = self._api_get(ACTION_DESCRIBE_INSTANCES, extra)
        if "error" in result:
            return result
        return {"instances": _as_list(result.get("instanceList")),
                "requestId": result.get("requestId"),
                "simulated": False}

    def _live_describe_images(self, image_ids: list[str] | None) -> dict:
        extra: dict[str, list[str]] = {}
        if image_ids:
            extra["imageId"] = [str(v) for v in image_ids]
        result = self._api_get(ACTION_DESCRIBE_IMAGES, extra)
        if "error" in result:
            return result
        return {"images": _as_list(result.get("imageList")),
                "requestId": result.get("requestId"),
                "simulated": False}

    def _lifecycle_live(self, action: str, instance_ids: list[str]) -> dict:
        result = self._api_get(action, {"instanceId": list(instance_ids)})
        if "error" in result:
            return result
        return {"statusMap": result.get("statusMap") or {},
                "requestId": result.get("requestId"),
                "simulated": False}

    # ── 查詢工具 ────────────────────────────────────────────────

    def list_instances(self, instance_ids: list[str] | None = None,
                       order_uuids: list[str] | None = None) -> dict:
        """describeInstances — 列出雲伺服器詳細資訊。

        不帶 instanceId/orderUuid 時列出所有雲伺服器。
        """
        if self.is_mock:
            vms = self._mock_vms
            if instance_ids:
                wanted = {str(v).strip() for v in instance_ids
                          if str(v).strip()}
                vms = [v for v in vms if v["instanceId"] in wanted]
            if order_uuids:
                wanted = {str(v).strip() for v in order_uuids
                          if str(v).strip()}
                vms = [v for v in vms if v["orderUuid"] in wanted]
            return {
                "instances": vms,
                "count": len(vms),
                "note": self._mock_note(),
                "simulated": True,
            }
        return self._live_list_instances(instance_ids, order_uuids)

    def describe_images(self, image_ids: list[str] | None = None) -> dict:
        """describeImages — 列出範本（公用 hi-* / 用戶 BT*）資訊。"""
        if self.is_mock:
            images = self._mock_images
            if image_ids:
                wanted = {str(v).strip() for v in image_ids
                          if str(v).strip()}
                images = [i for i in images if i["imageId"] in wanted]
            return {
                "images": images,
                "count": len(images),
                "note": self._mock_note(),
                "simulated": True,
            }
        return self._live_describe_images(image_ids)

    def list_instance_types(self, os_filter: str | None = None) -> dict:
        """運算規格參考表（靜態節錄 + 完整表 API 說明）。

        runInstances 的 instanceType 合法值 — 完整表見
        ``/hws-doc/api/hwsipm/listProductPackage?locale=zh_TW``。
        """
        f = (os_filter or "").strip().lower()
        rows = []
        for t in INSTANCE_TYPES:
            if f and t["os"].lower() != f:
                continue
            rows.append(t)
        return {
            "instanceTypes": rows,
            "count": len(rows),
            "note": (
                "靜態節錄表；完整規格清單見 https://hws.hicloud.hinet.net"
                "/hws-doc/api/hwsipm/listProductPackage?locale=zh_TW"
            ),
            "simulated": self.is_mock,
        }

    # ── 生命週期寫入（兩道閘） ──────────────────────────────────

    def run_instances(
        self, *, image_id: str = "", instance_type: str = "",
        clone_instance_id: str = "", instance_name: str = "",
        count: int = 1, monitoring_enabled: bool = False,
        vpc_business_service_id: str = "", region_id: str = "",
        confirm: Any = False,
    ) -> dict:
        """runInstances — 建立雲伺服器（產生計費資源！）

        三種來源（文件 runInstances）:
        1. 公用/用戶範本: image_id + instance_type 必填
        2. 雲伺服器複製: clone_instance_id 必填（image/type 被忽略）
        3. 用戶範本複製: image_id = BT*（instanceType 必填）
        """
        gate = self._gate_write("建立雲伺服器 (runInstances)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}

        count = _clamp_int(count, 1, 1, MAX_RUN_COUNT)
        image_id = str(image_id or "").strip()
        clone_id = str(clone_instance_id or "").strip()
        itype = str(instance_type or "").strip()
        if not image_id and not clone_id:
            return {"error": (
                "需要 image_id（公用範本 hi-* 或用戶範本 BT*）或 "
                "clone_instance_id（雲伺服器複製雲伺服器）。可先用 "
                "hicloud_describe_images / hicloud_list_instances 查可用值。"
            ), "mode": self.mode}
        if image_id and not clone_id and not itype:
            if not image_id.upper().startswith("BT"):
                return {"error": (
                    "公用範本申請需要 instance_type（運算規格，例如 "
                    "HC1.caas-r1z1.b.2c8g-Linux 或 HC1.S.LINUX）— 可用 "
                    "hicloud_list_instance_types 查清單。"
                ), "mode": self.mode}

        if self.is_mock:
            created = []
            order_uuids = []
            for _ in range(count):
                seq = self._mock_next_seq
                self._mock_next_seq += 1
                vm = self._make_mock_vm(
                    seq,
                    name=instance_name.strip() or f"mock-vm-{seq}",
                    status=STATUS_RUNNING,
                )
                if image_id:
                    vm["imageId"] = image_id
                if itype:
                    vm["instanceType"] = itype
                elif clone_id:
                    vm["clonedFrom"] = clone_id
                if vpc_business_service_id:
                    vm["vpcBusinessServiceId"] = vpc_business_service_id
                self._mock_vms.append(vm)
                created.append(vm)
                order_uuids.append(vm["orderUuid"])
            return {
                "orderUuidList": order_uuids,
                "instances": created,
                "count": len(created),
                "note": self._mock_note(),
                "simulated": True,
            }

        extra: dict[str, list[str]] = {}
        if image_id:
            extra["imageId"] = [image_id]
        if itype:
            extra["instanceType"] = [itype]
        if clone_id:
            extra["instanceId"] = [clone_id]
        if instance_name:
            extra["instanceName"] = [instance_name.strip()]
        extra["count"] = [str(count)]
        extra["monitoringEnabled"] = [
            "true" if monitoring_enabled else "false"]
        if vpc_business_service_id:
            extra["vpcBusinessServiceId"] = [vpc_business_service_id.strip()]
        if region_id:
            extra["regionId"] = [region_id.strip()]
        result = self._api_get(ACTION_RUN, extra)
        if "error" in result:
            return result
        return {"orderUuidList": result.get("orderUuidList") or [],
                "requestId": result.get("requestId"),
                "note": ("建立訂單已送出（非同步供裝），請稍後用 "
                         "hicloud_list_instances 確認 VM 狀態。"),
                "simulated": False}

    def _mock_lifecycle(
        self, instance_ids: list[str], new_status: str,
        fail_missing: bool = True,
    ) -> dict:
        status_map: dict[str, str] = {}
        wanted = [str(v).strip() for v in instance_ids if str(v).strip()]
        by_id = {v["instanceId"]: v for v in self._mock_vms}
        for iid in wanted:
            vm = by_id.get(iid)
            if vm is None:
                if fail_missing:
                    status_map[iid] = "process fail: 查無此 instanceId"
                continue
            vm["operationStatus"] = new_status
            if new_status == STATUS_RUNNING and vm.get("startDate") is None:
                vm["startDate"] = _now_iso()
            status_map[iid] = new_status
        return {
            "statusMap": status_map,
            "note": self._mock_note(),
            "simulated": True,
        }

    def start_instances(self, instance_ids: list[str],
                        confirm: Any = False) -> dict:
        gate = self._gate_write("開機 (startInstances)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if not instance_ids:
            return {"error": "需要 instance_ids（一或多個）",
                    "mode": self.mode}
        if self.is_mock:
            return self._mock_lifecycle(instance_ids, STATUS_RUNNING)
        return self._lifecycle_live(ACTION_START, instance_ids)

    def stop_instances(self, instance_ids: list[str],
                       confirm: Any = False) -> dict:
        gate = self._gate_write("關機 (stopInstances)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if not instance_ids:
            return {"error": "需要 instance_ids（一或多個）",
                    "mode": self.mode}
        if self.is_mock:
            return self._mock_lifecycle(instance_ids, STATUS_STOPPED)
        return self._lifecycle_live(ACTION_STOP, instance_ids)

    def reboot_instances(self, instance_ids: list[str],
                         confirm: Any = False) -> dict:
        gate = self._gate_write("重啟 (rebootInstances)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if not instance_ids:
            return {"error": "需要 instance_ids（一或多個）",
                    "mode": self.mode}
        if self.is_mock:
            return self._mock_lifecycle(instance_ids, STATUS_STARTING)
        return self._lifecycle_live(ACTION_REBOOT, instance_ids)

    def terminate_instances(self, instance_ids: list[str],
                            confirm: Any = False) -> dict:
        gate = self._gate_write(
            "刪除雲伺服器 (terminateInstances — 不可逆！)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if not instance_ids:
            return {"error": "需要 instance_ids（一或多個）",
                    "mode": self.mode}
        if self.is_mock:
            wanted = [str(v).strip() for v in instance_ids
                      if str(v).strip()]
            removed = [v["instanceId"] for v in self._mock_vms
                       if v["instanceId"] in wanted]
            self._mock_vms = [v for v in self._mock_vms
                              if v["instanceId"] not in set(wanted)]
            return {
                "statusMap": {iid: "process success" for iid in wanted},
                "removed": removed,
                "note": self._mock_note(),
                "simulated": True,
            }
        return self._lifecycle_live(ACTION_TERMINATE, instance_ids)

    # ─── v0.9.66.0 — full HWS service surface (per doc) ─────────
    #
    # Data-driven generic CRUD for the remaining actions across 12
    # services (SLB / CloudWatch / EBS / Image / Subnet / NetworkAcl /
    # Log / VPC / InternetGateway+IP / IntranetGateway+Link /
    # NAT+PortFwd) plus the two VM completions
    # (modifyInstanceAttribute / restoreInstance). Mock mode keeps an
    # in-memory book per resource kind (NO HTTP ever); live mode
    # proxies the SAME signature the official doc locks. Lifecycle
    # writes go through _gate_write (per-call confirm=true; live
    # also needs HICLOUD_WRITES_ENABLED=on).

    def _mock_res_add(self, kind: str, entry: dict[str, Any]) -> dict:
        self._mock_resources.setdefault(kind, []).append(entry)
        return entry

    def _mock_res_id(self, kind: str) -> str:
        seq = self._mock_res_seq[kind]
        self._mock_res_seq[kind] = seq + 1
        return f"{_RES_SPECS[kind]['mock_prefix']}{seq:07d}"

    def _mock_res_entry(self, kind: str, name: str,
                        extra: dict[str, Any] | None = None) -> dict:
        now = _now_iso()
        bsid = self._mock_res_id(kind)
        entry: dict[str, Any] = {
            _RES_SPECS[kind]["id_field"]: bsid,
            "orderUuid": str(uuid.uuid5(
                uuid.NAMESPACE_DNS, f"mock-{kind}-{bsid}")),
            "name": name or None,
            "applyDate": now,
            "startDate": now,
            "endDate": None,
            "provisionStatus": "provisionok",
            "provisionMessage": None,
            "regionId": "region-tw-1",
            "simulated": True,
        }
        if extra:
            entry.update(extra)
        return self._mock_res_add(kind, entry)

    def _mock_res_list(
        self, kind: str, ids: list[str] | None,
        order_uuids: list[str] | None,
    ) -> list[dict[str, Any]]:
        rows = list(self._mock_resources.get(kind) or [])
        if ids:
            wanted = {str(v).strip() for v in ids if str(v).strip()}
            id_field = _RES_SPECS[kind]["id_field"]
            rows = [r for r in rows if r.get(id_field) in wanted]
        if order_uuids:
            wanted = {str(v).strip() for v in order_uuids
                      if str(v).strip()}
            rows = [r for r in rows if r.get("orderUuid") in wanted]
        return rows

    def _mock_res_remove(self, kind: str,
                         ids: list[str]) -> list[str]:
        id_field = _RES_SPECS[kind]["id_field"]
        wanted = {str(v).strip() for v in ids if str(v).strip()}
        rows = self._mock_resources.get(kind) or []
        removed = [r.get(id_field) for r in rows
                   if r.get(id_field) in wanted]
        self._mock_resources[kind] = [
            r for r in rows if r.get(id_field) not in wanted]
        return [str(x) for x in removed]

    def _mock_res_find(self, kind: str, id_value: str) -> dict | None:
        id_field = _RES_SPECS[kind]["id_field"]
        for r in self._mock_resources.get(kind) or []:
            if r.get(id_field) == id_value:
                return r
        return None

    def _gate_note(self, result: dict) -> dict:
        result.setdefault("note", self._mock_note())
        return result

    @staticmethod
    def _extract_list(result: dict, key: str) -> list:
        """Live responses key the list differently per service —
        prefer the expected key, else the FIRST list value found."""
        if key in result:
            return _as_list(result.get(key))
        for v in result.values():
            if isinstance(v, list):
                return v
        return []

    def _res_list(self, kind: str, *, ids: list[str] | None = None,
                  order_uuids: list[str] | None = None,
                  extra: dict[str, list[str]] | None = None,
                  key: str | None = None) -> dict:
        spec = _RES_SPECS[kind]
        out_key = key or spec["list_key"]
        if self.is_mock:
            rows = self._mock_res_list(kind, ids, order_uuids)
            return self._gate_note({
                out_key: rows, "count": len(rows), "simulated": True})
        params: dict[str, list[str]] = {}
        if ids:
            params[spec["id_param"]] = [str(v) for v in ids]
        if order_uuids:
            params["orderUuid"] = [str(v) for v in order_uuids]
        if extra:
            for k, vs in extra.items():
                vals = [str(v) for v in vs if v is not None and str(v)]
                if vals:
                    params[k] = vals
        result = self._api_get(spec["list_action"], params)
        if "error" in result:
            return result
        return {
            out_key: self._extract_list(result, out_key),
            "requestId": result.get("requestId"),
            "simulated": False,
        }

    def _res_create(
        self, kind: str, *, name: str = "",
        params: dict[str, list[str]] | None = None,
        extra: dict[str, Any] | None = None, what: str = "",
    ) -> dict:
        spec = _RES_SPECS[kind]
        if self.is_mock:
            entry = self._mock_res_entry(kind, name, extra)
            return self._gate_note({
                "orderUuidList": [entry["orderUuid"]],
                spec["id_param"]: [entry[spec["id_field"]]],
                "simulated": True})
        result = self._api_get(spec["create_action"], params or {})
        if "error" in result:
            return result
        return self._gate_note({
            "orderUuidList": _as_list(result.get("orderUuidList")) or [],
            "requestId": result.get("requestId"),
            "simulated": False})

    def _res_delete(self, kind: str, ids: list[str], *,
                    what: str, confirm: Any = False) -> dict:
        spec = _RES_SPECS[kind]
        gate = self._gate_write(what, confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        ids = [str(v).strip() for v in (ids or []) if str(v).strip()]
        if not ids:
            return {"error": "需要識別碼（一或多個）", "mode": self.mode}
        if self.is_mock:
            removed = self._mock_res_remove(kind, ids)
            return self._gate_note({
                "statusMap": {i: "process success" for i in ids},
                "removed": removed, "simulated": True})
        result = self._api_get(
            spec["delete_action"], {spec["delete_id_param"]: ids})
        if "error" in result:
            return result
        return self._gate_note({
            "statusMap": result.get("statusMap") or {},
            "orderUuidList": _as_list(result.get("orderUuidList")) or [],
            "requestId": result.get("requestId"),
            "simulated": False})

    def _res_modify(
        self, kind: str, id_value: str, updates: dict, *,
        what: str, confirm: Any = False,
    ) -> dict:
        spec = _RES_SPECS[kind]
        action = spec.get("modify_action")
        if not action:
            return {"error": f"{kind} has no modify action",
                    "mode": self.mode}
        gate = self._gate_write(what, confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if not id_value:
            return {"error": f"需要 {spec['id_param']}", "mode": self.mode}
        if self.is_mock:
            row = self._mock_res_find(kind, id_value)
            if row is None:
                return {"error": f"查無此識別碼: {id_value}",
                        "mode": self.mode}
            row.update({k: v for k, v in updates.items() if v})
            return self._gate_note({
                "statusMap": {id_value: "process success"},
                "entry": row, "simulated": True})
        params: dict[str, list[str]] = {spec["id_param"]: [id_value]}
        for k, v in updates.items():
            if v:
                params[k] = [str(v)]
        result = self._api_get(action, params)
        if "error" in result:
            return result
        return self._gate_note({
            "statusMap": result.get("statusMap") or {},
            "requestId": result.get("requestId"), "simulated": False})

    # ── VM completions ──────────────────────────────────────────

    def modify_instance_attribute(
        self, instance_id: str, instance_type: str = "",
        monitoring_enabled: bool | None = None, confirm: Any = False,
    ) -> dict:
        """modifyInstanceAttribute — 原機升降級 / 供裝監控。
        帶 instanceType 時 monitoringEnabled 被忽略（文件 3.3）。"""
        gate = self._gate_write(
            "原機升降級 (modifyInstanceAttribute)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        instance_id = str(instance_id or "").strip()
        if not instance_id:
            return {"error": "需要 instance_id", "mode": self.mode}
        params: dict[str, list[str]] = {"instanceId": [instance_id]}
        if instance_type:
            params["instanceType"] = [instance_type.strip()]
        if monitoring_enabled is not None and not instance_type:
            params["monitoringEnabled"] = [
                "true" if monitoring_enabled else "false"]
        if self.is_mock:
            vm = next((v for v in self._mock_vms
                       if v["instanceId"] == instance_id), None)
            if vm is None:
                return {"error": f"查無此 instanceId: {instance_id}",
                        "mode": self.mode}
            if instance_type:
                vm["instanceType"] = instance_type.strip()
            return self._gate_note({
                "statusMap": {instance_id: "process success"},
                "instance": vm, "simulated": True})
        result = self._api_get(ACTION_MODIFY_INSTANCE_ATTRIBUTE, params)
        if "error" in result:
            return result
        return self._gate_note({
            "statusMap": result.get("statusMap") or {},
            "requestId": result.get("requestId"), "simulated": False})

    def restore_instance(self, snapshot_id: str,
                         confirm: Any = False) -> dict:
        """restoreInstance — 由快照復原雲伺服器。"""
        gate = self._gate_write("由快照復原 (restoreInstance)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        snapshot_id = str(snapshot_id or "").strip()
        if not snapshot_id:
            return {"error": "需要 snapshot_id", "mode": self.mode}
        if self.is_mock:
            return self._gate_note({
                "statusMap": {snapshot_id: "process success"},
                "simulated": True})
        result = self._api_get(ACTION_RESTORE_INSTANCE,
                               {"snapshotId": [snapshot_id]})
        if "error" in result:
            return result
        return self._gate_note({
            "statusMap": result.get("statusMap") or {},
            "requestId": result.get("requestId"), "simulated": False})

    # ── CloudWatch ──────────────────────────────────────────────

    def get_instance_stats(self, instance_ids: list[str]) -> dict:
        """getInstancesStats — VM 的 CPU/DISK/Network 近即時數據。"""
        if not instance_ids:
            return {"error": "需要 instance_ids（一或多個）",
                    "mode": self.mode}
        if self.is_mock:
            stats = [{
                "instanceId": v["instanceId"],
                "cpuUsage": 3.0 + (len(v["instanceId"]) % 17),
                "diskRead": 1024.0, "diskWrite": 2048.0,
                "networkIn": 102400.0, "networkOut": 98304.0,
                "timestamp": _now_iso(),
            } for v in self._mock_vms]
            wanted = {str(v).strip() for v in instance_ids
                      if str(v).strip()}
            stats = [s for s in stats
                     if s["instanceId"] in wanted] or stats
            return self._gate_note({
                "InstancesStatsList": stats, "simulated": True})
        result = self._api_get(
            ACTION_GET_INSTANCES_STATS,
            {"instanceId": [str(v) for v in instance_ids]})
        if "error" in result:
            return result
        return self._gate_note({
            "InstancesStatsList": self._extract_list(
                result, "InstancesStatsList"),
            "requestId": result.get("requestId"), "simulated": False})

    # ── Log ─────────────────────────────────────────────────────

    def log_list(self, start_date: str) -> dict:
        """listLog — 查詢平台操作 Log 紀錄（startDate = ISO 8601）。"""
        start_date = str(start_date or "").strip()
        if not start_date:
            return {"error": "需要 start_date（ISO 8601，如 "
                             "2024-05-01T00:00:00Z）", "mode": self.mode}
        if self.is_mock:
            return self._gate_note({
                "logList": [
                    {"logId": f"LOGMOCK{i:04d}",
                     "message": f"mock log entry {i}",
                     "timestamp": _now_iso()} for i in range(1, 4)],
                "count": 3, "simulated": True})
        result = self._api_get(ACTION_LIST_LOG, {"startDate": [start_date]})
        if "error" in result:
            return result
        return self._gate_note({
            "logList": self._extract_list(result, "logList"),
            "requestId": result.get("requestId"), "simulated": False})

    # ── EBS volumes ─────────────────────────────────────────────

    def volume_list(self, volume_ids: list[str] | None = None,
                    order_uuids: list[str] | None = None,
                    vpc_bsid: str = "") -> dict:
        extra = ({"vpcBusinessServiceId": [vpc_bsid]} if vpc_bsid
                 else None)
        return self._res_list("volume", ids=volume_ids,
                              order_uuids=order_uuids, extra=extra)

    def volume_create(self, instance_id: str, radix: int,
                      volume_name: str = "",
                      confirm: Any = False) -> dict:
        gate = self._gate_write("建立儲存空間 (createVolume — 產生計費！)",
                                confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        instance_id = str(instance_id or "").strip()
        radix = _clamp_int(radix, 0, 0, 20)
        if not instance_id or radix < 1:
            return {"error": ("需要 instance_id 與 radix（1-20，"
                              "每單位 100GB）"), "mode": self.mode}
        params = {"instanceId": [instance_id], "radix": [str(radix)]}
        if volume_name:
            params["volumeName"] = [volume_name.strip()]
        return self._res_create(
            "volume", name=volume_name, params=params,
            extra={"instanceId": instance_id, "radix": radix,
                   "sizeGb": radix * 100})

    def volume_attach(self, volume_id: str, instance_id: str,
                      confirm: Any = False) -> dict:
        gate = self._gate_write("掛載儲存空間 (attachVolume)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        volume_id = str(volume_id or "").strip()
        instance_id = str(instance_id or "").strip()
        if not volume_id or not instance_id:
            return {"error": "需要 volume_id 與 instance_id",
                    "mode": self.mode}
        if self.is_mock:
            vol = self._mock_res_find("volume", volume_id)
            if vol is None:
                return {"error": f"查無此 volumeId: {volume_id}",
                        "mode": self.mode}
            vol["attachment"] = {"instanceId": instance_id,
                                 "status": "attaching"}
            return self._gate_note({
                "statusMap": {volume_id: "attaching"},
                "volume": vol, "simulated": True})
        result = self._api_get(ACTION_ATTACH_VOLUME, {
            "volumeId": [volume_id], "instanceId": [instance_id]})
        if "error" in result:
            return result
        return self._gate_note({
            "statusMap": result.get("statusMap") or {},
            "requestId": result.get("requestId"), "simulated": False})

    def volume_detach(self, volume_id: str,
                      confirm: Any = False) -> dict:
        gate = self._gate_write("卸載儲存空間 (detachVolume)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        volume_id = str(volume_id or "").strip()
        if not volume_id:
            return {"error": "需要 volume_id", "mode": self.mode}
        if self.is_mock:
            vol = self._mock_res_find("volume", volume_id)
            if vol is None:
                return {"error": f"查無此 volumeId: {volume_id}",
                        "mode": self.mode}
            vol.pop("attachment", None)
            return self._gate_note({
                "statusMap": {volume_id: "detaching"},
                "volume": vol, "simulated": True})
        result = self._api_get(ACTION_DETACH_VOLUME,
                               {"volumeId": [volume_id]})
        if "error" in result:
            return result
        return self._gate_note({
            "statusMap": result.get("statusMap") or {},
            "requestId": result.get("requestId"), "simulated": False})

    def volume_delete(self, volume_ids: list[str],
                      confirm: Any = False) -> dict:
        return self._res_delete(
            "volume", volume_ids, what="刪除儲存空間 (deleteVolume)",
            confirm=confirm)

    # ── Image completions ───────────────────────────────────────

    def image_create(self, instance_id: str, name: str = "",
                     description: str = "",
                     confirm: Any = False) -> dict:
        gate = self._gate_write("由 VM 建立範本 (createImage)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        instance_id = str(instance_id or "").strip()
        if not instance_id:
            return {"error": "需要 instance_id", "mode": self.mode}
        params = {"instanceId": [instance_id]}
        if name:
            params["name"] = [name.strip()]
        if description:
            params["description"] = [description.strip()]
        return self._res_create("image_user", name=name, params=params,
                                extra={"sourceInstanceId": instance_id,
                                       "description": description or None})

    def image_delete(self, image_ids: list[str],
                     confirm: Any = False) -> dict:
        return self._res_delete(
            "image_user", image_ids, what="刪除範本 (deleteImage)",
            confirm=confirm)

    def image_modify(self, image_id: str, name: str = "",
                     description: str = "",
                     confirm: Any = False) -> dict:
        return self._res_modify(
            "image_user", str(image_id or "").strip(),
            {"name": name, "description": description},
            what="異動範本屬性 (modifyImageAttribute)", confirm=confirm)

    # ── SLB ─────────────────────────────────────────────────────

    def slb_create(
        self, *, instance_ids: list[str] | None = None,
        ports: list[int] | None = None, name: str = "",
        region_id: str = "", vpc_bsid: str = "",
        load_balancer_type: str = "", ports_info: list[str] | None = None,
        rips_info: list[str] | None = None,
        real_server_weights: list[str] | None = None,
        user_assign_ip: str = "", route_direction: str = "",
        ip_subnet_bsid: str = "", confirm: Any = False,
    ) -> dict:
        """createLoadBalancerPolicy — CaaS 形狀（instanceId+port）或
        CVPC 形狀（vpcBusinessServiceId + loadBalancerType）二擇一。
        帶 vpc_bsid → CVPC；否則 CaaS（文件 4.2）。"""
        gate = self._gate_write(
            "建立負載平衡 (createLoadBalancerPolicy — 產生計費！)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        params: dict[str, list[str]] = {}
        if vpc_bsid:
            params["vpcBusinessServiceId"] = [vpc_bsid.strip()]
            params["loadBalancerType"] = [load_balancer_type.strip() or "0"]
            if ports_info:
                params["portsInfo"] = [str(v) for v in ports_info]
            if rips_info:
                params["ripsInfo"] = [str(v) for v in rips_info]
            if real_server_weights:
                params["realServerWeight"] = [
                    str(v) for v in real_server_weights]
            if user_assign_ip:
                params["userAssignIp"] = [user_assign_ip.strip()]
            if route_direction:
                params["economyLoadBalancerVIPRouteDirection"] = [
                    route_direction.strip()]
            if ip_subnet_bsid:
                params["ipSubnetBusinessServiceId"] = [ip_subnet_bsid.strip()]
        else:
            if not instance_ids or not ports:
                return {"error": ("CaaS 負載平衡需要 instance_ids 與 "
                                  "ports；CVPC 請帶 vpc_bsid"),
                        "mode": self.mode}
            params["instanceId"] = [str(v) for v in instance_ids]
            params["port"] = [str(v) for v in ports]
            if region_id:
                params["regionId"] = [region_id.strip()]
        if name:
            params["loadBalancerPolicyName"] = [name.strip()]
        return self._res_create(
            "slb", name=name, params=params,
            extra={"vpcBusinessServiceId": vpc_bsid or None,
                   "loadBalancerType": load_balancer_type or (
                       "caas" if not vpc_bsid else "0"),
                   "ports": ports or None})

    def slb_delete(self, load_balancer_ids: list[str],
                   confirm: Any = False) -> dict:
        return self._res_delete(
            "slb", load_balancer_ids,
            what="刪除負載平衡 (deleteLoadBalancerPolicy)", confirm=confirm)

    def slb_list(
        self, load_balancer_ids: list[str] | None = None,
        order_uuids: list[str] | None = None, vpc_bsid: str = "",
        load_balancer_type: str = "", business_tag: str = "",
    ) -> dict:
        extra: dict[str, list[str]] = {}
        if vpc_bsid:
            extra["vpcBusinessServiceId"] = [vpc_bsid.strip()]
        if load_balancer_type:
            extra["loadBalancerType"] = [load_balancer_type.strip()]
        if business_tag:
            extra["businessTag"] = [business_tag.strip()]
        return self._res_list("slb", ids=load_balancer_ids,
                              order_uuids=order_uuids, extra=extra or None)

    def slb_vip_health(self, load_balancer_ids: list[str]) -> dict:
        """querySlbVIPHealthStatus — 僅適用 CVPC 負載平衡。"""
        if not load_balancer_ids:
            return {"error": "需要 load_balancer_ids", "mode": self.mode}
        if self.is_mock:
            rows = self._mock_res_list("slb", load_balancer_ids, None)
            return self._gate_note({
                "healthList": [
                    {"loadBalancerId": r.get("loadBalancerId"),
                     "status": "healthy"} for r in rows],
                "simulated": True})
        result = self._api_get(ACTION_QUERY_SLB_VIP_HEALTH, {
            "loadBalancerId": [str(v) for v in load_balancer_ids]})
        if "error" in result:
            return result
        return self._gate_note({
            "healthList": self._extract_list(result, "healthList"),
            "requestId": result.get("requestId"), "simulated": False})

    def slb_replace(
        self, load_balancer_id: str,
        instance_ids: list[str] | None = None,
        ports: list[int] | None = None,
        ports_info: list[str] | None = None,
        rips_info: list[str] | None = None, confirm: Any = False,
    ) -> dict:
        gate = self._gate_write("異動負載平衡 (replaceLoadBalancerPolicy)",
                                confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        load_balancer_id = str(load_balancer_id or "").strip()
        if not load_balancer_id:
            return {"error": "需要 load_balancer_id", "mode": self.mode}
        params: dict[str, list[str]] = {
            "loadBalancerId": [load_balancer_id]}
        if ports_info:
            params["portsInfo"] = [str(v) for v in ports_info]
        if rips_info:
            params["ripsInfo"] = [str(v) for v in rips_info]
        if instance_ids:
            params["instanceId"] = [str(v) for v in instance_ids]
        if ports:
            params["port"] = [str(v) for v in ports]
        if self.is_mock:
            row = self._mock_res_find("slb", load_balancer_id)
            if row is None:
                return {"error": f"查無此 loadBalancerId: "
                                 f"{load_balancer_id}", "mode": self.mode}
            row["lastReplacedAt"] = _now_iso()
            return self._gate_note({
                "statusMap": {load_balancer_id: "process success"},
                "entry": row, "simulated": True})
        result = self._api_get(ACTION_REPLACE_SLB, params)
        if "error" in result:
            return result
        return self._gate_note({
            "statusMap": result.get("statusMap") or {},
            "requestId": result.get("requestId"), "simulated": False})

    # ── Subnets ─────────────────────────────────────────────────

    def subnet_create(
        self, subnet: str = "", vpc_bsid: str = "",
        intranet_gateway_bsid: str = "", subnet_name: str = "",
        ip_version: str = "", confirm: Any = False,
    ) -> dict:
        """createSubnet — CVPC 需 vpc_bsid；CaaS 需
        intranet_gateway_bsid（文件 8.2）。ip_version 預設 IPv4。"""
        gate = self._gate_write("建立子網段 (createSubnet — 產生計費！)",
                                confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        subnet = str(subnet or "").strip()
        if not subnet:
            return {"error": "需要 subnet（IP 網段，如 10.20.0.0/24）",
                    "mode": self.mode}
        params: dict[str, list[str]] = {"subnet": [subnet]}
        if vpc_bsid:
            params["vpcBusinessServiceId"] = [vpc_bsid.strip()]
        elif intranet_gateway_bsid:
            params["intranetGatewayBusinessServiceId"] = [
                intranet_gateway_bsid.strip()]
        else:
            return {"error": ("需要 vpc_bsid（CVPC）或 "
                              "intranet_gateway_bsid（CaaS）"),
                    "mode": self.mode}
        if subnet_name:
            params["subnetName"] = [subnet_name.strip()]
        if ip_version:
            params["ipVersion"] = [ip_version.strip()]
        return self._res_create(
            "subnet", name=subnet_name, params=params,
            extra={"subnet": subnet,
                   "ipVersion": ip_version or "0",
                   "vpcBusinessServiceId": vpc_bsid or None})

    def subnet_delete(self, subnet_bsids: list[str],
                      confirm: Any = False) -> dict:
        return self._res_delete(
            "subnet", subnet_bsids, what="刪除子網段 (deleteSubnet)",
            confirm=confirm)

    def subnet_list(self, subnet_bsids: list[str] | None = None,
                    order_uuids: list[str] | None = None,
                    business_tag: str = "", vpc_bsid: str = "") -> dict:
        extra: dict[str, list[str]] = {}
        if business_tag:
            extra["businessTag"] = [business_tag.strip()]
        if vpc_bsid:
            extra["vpcBusinessServiceId"] = [vpc_bsid.strip()]
        return self._res_list("subnet", ids=subnet_bsids,
                              order_uuids=order_uuids,
                              extra=extra or None)

    def subnet_modify(self, subnet_bsid: str, name: str = "",
                      description: str = "",
                      confirm: Any = False) -> dict:
        return self._res_modify(
            "subnet", str(subnet_bsid or "").strip(),
            {"name": name, "description": description},
            what="異動子網段屬性 (modifySubnetAttribute)", confirm=confirm)

    # ── Network ACL（防火牆） ───────────────────────────────────

    def acl_create(
        self, policy_strings: list[str], instance_id: str = "",
        vpc_bsid: str = "", network_acl_name: str = "",
        ip_version: str = "", business_tag: str = "",
        confirm: Any = False,
    ) -> dict:
        """createNetworkAcl — CaaS 形狀（instanceId + 舊式 policy
        字串）；CVPC 形狀（vpc_bsid + 新式 policy 字串，帶
        ipVersion）。文件 9.2。"""
        gate = self._gate_write("建立防火牆 (createNetworkAcl)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if not policy_strings:
            return {"error": "需要 policy_strings（防火牆組態字串）",
                    "mode": self.mode}
        params: dict[str, list[str]] = {
            "policyString": [str(v) for v in policy_strings]}
        if vpc_bsid:
            params["vpcBusinessServiceId"] = [vpc_bsid.strip()]
            if business_tag:
                params["businessTag"] = [business_tag.strip()]
        elif instance_id:
            params["instanceId"] = [instance_id.strip()]
            if business_tag:
                params["businessTag"] = [business_tag.strip()]
        else:
            return {"error": ("需要 instance_id（CaaS）或 "
                              "vpc_bsid（CVPC）"), "mode": self.mode}
        if network_acl_name:
            params["networkAclName"] = [network_acl_name.strip()]
        if vpc_bsid and ip_version:
            params["ipVersion"] = [ip_version.strip()]
        return self._res_create(
            "acl", name=network_acl_name, params=params,
            extra={"instanceId": instance_id or None,
                   "vpcBusinessServiceId": vpc_bsid or None,
                   "policyCount": len(policy_strings)})

    def acl_delete(self, network_acl_ids: list[str],
                   confirm: Any = False) -> dict:
        return self._res_delete(
            "acl", network_acl_ids,
            what="刪除防火牆 (deleteNetworkAcl)", confirm=confirm)

    def acl_list(self, network_acl_ids: list[str] | None = None,
                 order_uuids: list[str] | None = None,
                 business_tag: str = "") -> dict:
        extra = ({"businessTag": [business_tag.strip()]}
                 if business_tag else None)
        return self._res_list("acl", ids=network_acl_ids,
                              order_uuids=order_uuids, extra=extra)

    def acl_replace(self, network_acl_id: str,
                    policy_strings: list[str], ip_version: str = "",
                    confirm: Any = False) -> dict:
        gate = self._gate_write("異動防火牆 (replaceNetworkAcl)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        network_acl_id = str(network_acl_id or "").strip()
        if not network_acl_id or not policy_strings:
            return {"error": "需要 network_acl_id 與 policy_strings",
                    "mode": self.mode}
        params: dict[str, list[str]] = {
            "networkAclId": [network_acl_id],
            "policyString": [str(v) for v in policy_strings]}
        if ip_version:
            params["ipVersion"] = [ip_version.strip()]
        if self.is_mock:
            row = self._mock_res_find("acl", network_acl_id)
            if row is None:
                return {"error": f"查無此 networkAclId: "
                                 f"{network_acl_id}", "mode": self.mode}
            row["policyStringList"] = [str(v) for v in policy_strings]
            return self._gate_note({
                "statusMap": {network_acl_id: "process success"},
                "entry": row, "simulated": True})
        result = self._api_get(ACTION_REPLACE_ACL, params)
        if "error" in result:
            return result
        return self._gate_note({
            "statusMap": result.get("statusMap") or {},
            "requestId": result.get("requestId"), "simulated": False})

    # ── VPC（軟體資料中心） ─────────────────────────────────────

    def vpc_create(self, vpc_name: str, region_id: str = "",
                   confirm: Any = False) -> dict:
        gate = self._gate_write(
            "建立 CVPC (createVpc — 產生計費！)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        vpc_name = str(vpc_name or "").strip()
        if not vpc_name:
            return {"error": "需要 vpc_name", "mode": self.mode}
        params: dict[str, list[str]] = {"vpcName": [vpc_name]}
        if region_id:
            params["regionId"] = [region_id.strip()]
        return self._res_create("vpc", name=vpc_name, params=params,
                                extra={"vpcName": vpc_name})

    def vpc_delete(self, vpc_bsids: list[str],
                   confirm: Any = False) -> dict:
        return self._res_delete(
            "vpc", vpc_bsids, what="刪除 CVPC (deleteVpc)", confirm=confirm)

    def vpc_list(self, vpc_bsids: list[str] | None = None,
                 order_uuids: list[str] | None = None) -> dict:
        return self._res_list("vpc", ids=vpc_bsids,
                              order_uuids=order_uuids)

    def vpc_modify(self, vpc_bsid: str, name: str = "",
                   description: str = "",
                   confirm: Any = False) -> dict:
        return self._res_modify(
            "vpc", str(vpc_bsid or "").strip(),
            {"name": name, "description": description},
            what="異動 CVPC 屬性 (modifyVpcAttribute)", confirm=confirm)

    # ── Internet Gateway + Internet IP ──────────────────────────

    def igw_create(self, vpc_bsid: str, name: str = "",
                   ip_version: str = "",
                   confirm: Any = False) -> dict:
        gate = self._gate_write(
            "建立網際網路連線 (createInternetGateway — 產生計費！)",
            confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        vpc_bsid = str(vpc_bsid or "").strip()
        if not vpc_bsid:
            return {"error": "需要 vpc_bsid", "mode": self.mode}
        params: dict[str, list[str]] = {
            "vpcBusinessServiceId": [vpc_bsid]}
        if name:
            params["name"] = [name.strip()]
        if ip_version:
            params["ipVersion"] = [ip_version.strip()]
        return self._res_create("igw", name=name, params=params,
                                extra={"vpcBusinessServiceId": vpc_bsid,
                                       "ipVersion": ip_version or "0"})

    def igw_delete(self, internet_bsids: list[str], ip_version: str = "",
                   confirm: Any = False) -> dict:
        spec = _RES_SPECS["igw"]
        gate = self._gate_write("刪除網際網路連線 (deleteInternetGateway)",
                                confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        ids = [str(v).strip() for v in (internet_bsids or [])
               if str(v).strip()]
        if not ids:
            return {"error": "需要 internet_bsids（一或多個）",
                    "mode": self.mode}
        if self.is_mock:
            removed = self._mock_res_remove("igw", ids)
            return self._gate_note({
                "statusMap": {i: "process success" for i in ids},
                "removed": removed, "simulated": True})
        params: dict[str, list[str]] = {spec["delete_id_param"]: ids}
        if ip_version:
            params["ipVersion"] = [ip_version.strip()]
        result = self._api_get(spec["delete_action"], params)
        if "error" in result:
            return result
        return self._gate_note({
            "statusMap": result.get("statusMap") or {},
            "orderUuidList": _as_list(result.get("orderUuidList")) or [],
            "requestId": result.get("requestId"), "simulated": False})

    def igw_list(self, vpc_bsids: list[str] | None = None,
                 internet_bsids: list[str] | None = None,
                 order_uuids: list[str] | None = None,
                 ip_version: str = "") -> dict:
        out_key = "internetGatewayList"
        ids = internet_bsids if internet_bsids else vpc_bsids
        if self.is_mock:
            rows = self._mock_res_list("igw", ids or [], order_uuids or [])
            return self._gate_note({
                out_key: rows, "count": len(rows), "simulated": True})
        params: dict[str, list[str]] = {}
        if ids:
            params["internetBusinessServiceId" if internet_bsids
                    else "vpcBusinessServiceId"] = [str(v) for v in ids]
        if order_uuids:
            params["orderUuid"] = [str(v) for v in order_uuids]
        if ip_version:
            params["ipVersion"] = [ip_version.strip()]
        result = self._api_get(_RES_SPECS["igw"]["list_action"], params)
        if "error" in result:
            return result
        return self._gate_note({
            out_key: self._extract_list(result, out_key),
            "requestId": result.get("requestId"), "simulated": False})

    def internet_ip_create(self, internet_bsid: str, name: str = "",
                           confirm: Any = False) -> dict:
        gate = self._gate_write("建立網際網路 IP 服務 (createInternetIp)",
                                confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        internet_bsid = str(internet_bsid or "").strip()
        if not internet_bsid:
            return {"error": "需要 internet_bsid", "mode": self.mode}
        params: dict[str, list[str]] = {
            "internetBusinessServiceId": [internet_bsid]}
        if name:
            params["name"] = [name.strip()]
        return self._res_create(
            "internet_ip", name=name, params=params,
            extra={"internetBusinessServiceId": internet_bsid})

    def internet_ip_delete(self, internet_ip_bsids: list[str],
                           confirm: Any = False) -> dict:
        return self._res_delete(
            "internet_ip", internet_ip_bsids,
            what="刪除網際網路 IP 服務 (deleteInternetIp)", confirm=confirm)

    def internet_ip_list(self, internet_ip_bsids: list[str] | None = None,
                         order_uuids: list[str] | None = None,
                         internet_bsid: str = "") -> dict:
        extra = ({"internetBusinessServiceId": [internet_bsid.strip()]}
                 if internet_bsid else None)
        return self._res_list("internet_ip", ids=internet_ip_bsids,
                              order_uuids=order_uuids, extra=extra)

    # ── Intranet Gateway + Link ─────────────────────────────────

    def intranet_gw_create(self, region_id: str, name: str = "",
                           confirm: Any = False) -> dict:
        gate = self._gate_write(
            "建立內網路由閘道器 (createIntranetGateway — 產生計費！)",
            confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        region_id = str(region_id or "").strip()
        if not region_id:
            return {"error": "需要 region_id（如 region-tw-1）",
                    "mode": self.mode}
        params: dict[str, list[str]] = {"regionId": [region_id]}
        if name:
            params["intranetGatewayName"] = [name.strip()]
        return self._res_create("intranet_gw", name=name, params=params,
                                extra={"regionId": region_id})

    def intranet_gw_delete(self, intranet_gateway_bsids: list[str],
                           confirm: Any = False) -> dict:
        return self._res_delete(
            "intranet_gw", intranet_gateway_bsids,
            what="刪除內網路由閘道器 (deleteIntranetGateway)",
            confirm=confirm)

    def intranet_gw_list(
        self, intranet_gateway_bsids: list[str] | None = None,
        order_uuids: list[str] | None = None,
    ) -> dict:
        return self._res_list("intranet_gw", ids=intranet_gateway_bsids,
                              order_uuids=order_uuids)

    def intranet_gw_modify(self, intranet_gateway_bsid: str,
                           name: str = "", description: str = "",
                           confirm: Any = False) -> dict:
        return self._res_modify(
            "intranet_gw", str(intranet_gateway_bsid or "").strip(),
            {"name": name, "description": description},
            what="異動內網路由閘道器 (modifyIntranetGatewayAttribute)",
            confirm=confirm)

    def intranet_link_create(self, vpc_bsid: str,
                             intranet_gateway_bsid: str, name: str = "",
                             confirm: Any = False) -> dict:
        gate = self._gate_write("建立內網接取 (createIntranetLink)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        vpc_bsid = str(vpc_bsid or "").strip()
        gw = str(intranet_gateway_bsid or "").strip()
        if not vpc_bsid or not gw:
            return {"error": "需要 vpc_bsid 與 intranet_gateway_bsid",
                    "mode": self.mode}
        params: dict[str, list[str]] = {
            "vpcBusinessServiceId": [vpc_bsid],
            "intranetGatewayBusinessServiceId": [gw]}
        if name:
            params["intranetLinkName"] = [name.strip()]
        return self._res_create(
            "intranet_link", name=name, params=params,
            extra={"vpcBusinessServiceId": vpc_bsid,
                   "intranetGatewayBusinessServiceId": gw})

    def intranet_link_delete(self, intranet_link_bsids: list[str],
                             confirm: Any = False) -> dict:
        return self._res_delete(
            "intranet_link", intranet_link_bsids,
            what="刪除內網接取服務 (deleteIntranetLink)", confirm=confirm)

    def intranet_link_list(
        self, intranet_link_bsids: list[str] | None = None,
        order_uuids: list[str] | None = None,
        gw_bsid: str = "", vpc_bsid: str = "",
    ) -> dict:
        extra: dict[str, list[str]] = {}
        if gw_bsid:
            extra["intranetGatewayBusinessServiceId"] = [gw_bsid.strip()]
        if vpc_bsid:
            extra["vpcBusinessServiceId"] = [vpc_bsid.strip()]
        return self._res_list("intranet_link", ids=intranet_link_bsids,
                              order_uuids=order_uuids,
                              extra=extra or None)

    # ── NAT + Port Forwarding ───────────────────────────────────

    def nat_create(self, vpc_bsid: str, original_ip: str,
                   translated_ip: str, nat_name: str = "",
                   confirm: Any = False) -> dict:
        gate = self._gate_write("建立 NAT (createNat)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        vpc_bsid = str(vpc_bsid or "").strip()
        original_ip = str(original_ip or "").strip()
        translated_ip = str(translated_ip or "").strip()
        if not vpc_bsid or not original_ip or not translated_ip:
            return {"error": ("需要 vpc_bsid、original_ip、"
                              "translated_ip"), "mode": self.mode}
        params: dict[str, list[str]] = {
            "vpcBusinessServiceId": [vpc_bsid],
            "originalIp": [original_ip],
            "translatedIp": [translated_ip]}
        if nat_name:
            params["natName"] = [nat_name.strip()]
        return self._res_create(
            "nat", name=nat_name, params=params,
            extra={"originalIp": original_ip,
                   "translatedIp": translated_ip,
                   "vpcBusinessServiceId": vpc_bsid})

    def nat_delete(self, nat_bsids: list[str],
                   confirm: Any = False) -> dict:
        return self._res_delete(
            "nat", nat_bsids, what="刪除 NAT (deleteNat)", confirm=confirm)

    def nat_list(self, nat_bsids: list[str] | None = None,
                 order_uuids: list[str] | None = None,
                 vpc_bsid: str = "") -> dict:
        extra = ({"vpcBusinessServiceId": [vpc_bsid.strip()]}
                 if vpc_bsid else None)
        return self._res_list("nat", ids=nat_bsids,
                              order_uuids=order_uuids, extra=extra)

    def pfwd_create(self, vpc_bsid: str, original_ip: str,
                    translated_ip: str, original_port: int,
                    translated_port: int, port_fwd_name: str = "",
                    confirm: Any = False) -> dict:
        gate = self._gate_write("建立連接埠轉換 (createPortFwd)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        vpc_bsid = str(vpc_bsid or "").strip()
        original_ip = str(original_ip or "").strip()
        translated_ip = str(translated_ip or "").strip()
        op = _clamp_int(original_port, 0, 1, 65535)
        tp = _clamp_int(translated_port, 0, 1, 65535)
        if not vpc_bsid or not original_ip or not translated_ip \
                or op < 1 or tp < 1:
            return {"error": ("需要 vpc_bsid、original_ip、translated_ip、"
                              "original_port、translated_port"),
                    "mode": self.mode}
        params: dict[str, list[str]] = {
            "vpcBusinessServiceId": [vpc_bsid],
            "originalIp": [original_ip],
            "translatedIp": [translated_ip],
            "originalPort": [str(op)],
            "translatedPort": [str(tp)]}
        if port_fwd_name:
            params["portFwdName"] = [port_fwd_name.strip()]
        return self._res_create(
            "port_fwd", name=port_fwd_name, params=params,
            extra={"originalIp": original_ip,
                   "translatedIp": translated_ip,
                   "originalPort": op, "translatedPort": tp,
                   "vpcBusinessServiceId": vpc_bsid})

    def pfwd_delete(self, port_fwd_bsids: list[str],
                    confirm: Any = False) -> dict:
        return self._res_delete(
            "port_fwd", port_fwd_bsids,
            what="刪除連接埠轉換 (deletePortFwd)", confirm=confirm)

    def pfwd_list(self, port_fwd_bsids: list[str] | None = None,
                  order_uuids: list[str] | None = None,
                  vpc_bsid: str = "") -> dict:
        extra = ({"vpcBusinessServiceId": [vpc_bsid.strip()]}
                 if vpc_bsid else None)
        return self._res_list("port_fwd", ids=port_fwd_bsids,
                              order_uuids=order_uuids, extra=extra)


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []