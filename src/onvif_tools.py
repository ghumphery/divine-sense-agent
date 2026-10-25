"""ONVIF camera tools core (v0.9.37.0+, Phase A — READ-ONLY).

Covers the four Phase-A capabilities without any new dependency:

* **WS-Discovery** — a single UDP multicast Probe (239.255.255.250:3702)
  finds ``NetworkVideoTransmitter`` devices on the LAN; ProbeMatch
  XAddrs + onvif.org scopes are parsed for ip / port / name hints.
* **Favorites with credentials** — ``FavoriteStore`` persists named
  devices to ``<QDRANT_RAG_HOME>/onvif/favorites.json`` (atomic
  writes). Passwords are either stored base64-OBSCURED (NOT
  encryption) or referenced via ``password_env`` (RECOMMENDED —
  the JSON never holds the secret). ``resolve_credentials``
  resolves env-first and every list output is MASKED.
* **Device/media SOAP** — hand-built SOAP 1.2 envelopes with a
  WS-Security UsernameToken Digest header (the ONVIF standard):
  GetDeviceInformation (also the credential test), GetProfiles,
  GetCapabilities(Media) and GetSnapshotUri.
* **Snapshot** — HTTP GET of the snapshot URI with Digest → Basic
  auth fallback, magic-byte sniff (JPEG only) and a size cap.

Phase B (recording) and Phase C (PTZ / imaging settings) are NOT in
this module by design — see the v0.9.37.0 plan.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "OnvifError",
    "FavoriteStore",
    "resolve_credentials",
    "mask_username",
    "mask_password",
    "build_wsse_header",
    "soap_envelope",
    "post_soap",
    "parse_probe_matches",
    "wsdiscovery_probe",
    "get_device_information",
    "get_profiles",
    "get_media_xaddr",
    "get_snapshot_uri",
    "fetch_snapshot",
    "resolve_snapshot_path",
]

# ─── constants ───────────────────────────────────────────────────
_WSSE_NS = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-"
    "wssecurity-secext-1.0.xsd"
)
_WSU_NS = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-"
    "wssecurity-utility-1.0.xsd"
)
_PASSWORD_DIGEST_TYPE = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-"
    "username-token-profile-1.0#PasswordDigest"
)
_DISCOVERY_ADDR = ("239.255.255.250", 3702)
_JPEG_MAGIC = b"\xff\xd8\xff"
_DEFAULT_SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024


class OnvifError(RuntimeError):
    """Raised by ONVIF round-trips; host handlers convert to error
    dicts instead of tearing down the tool loop."""


def _now_epoch() -> float:
    return time.time()


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ─── credentials ─────────────────────────────────────────────────


def mask_username(username: str) -> str:
    """``admin`` → ``ad***`` — never echo the full account."""
    u = str(username or "")
    if len(u) <= 2:
        return (u[:1] + "***") if u else ""
    return u[:2] + "***"


def mask_password(password: str) -> str:
    """Length-only mask — the secret NEVER reaches tool output."""
    p = str(password or "")
    return f"******** ({len(p)})" if p else "(empty)"


def resolve_credentials(device: dict | None) -> dict[str, Any]:
    """Resolve a favorite device's credentials.

    Resolution order: ``cred_set`` (a central CredentialStore entry,
    v0.9.39.0+) → ``password_env`` (WINS over ``password_b64``) →
    ``password_b64`` → none. Returns
    ``{username, password, source}`` or ``{error}``.
    """
    device = device or {}
    cred_set = str(device.get("cred_set") or "").strip()
    if cred_set:
        from src.credential_store import CredentialStore

        resolved = CredentialStore().resolve(cred_set)
        if resolved.get("error"):
            return resolved
        return {
            "username": resolved.get("username") or "",
            "password": resolved.get("password") or "",
            "source": "cred_set",
            "cred_set": cred_set,
        }
    creds = (device or {}).get("credentials") or {}
    username = str(creds.get("username") or "")
    env_name = str(creds.get("password_env") or "").strip()
    if env_name:
        val = os.environ.get(env_name)
        if not val:
            return {
                "error": (
                    f"ONVIF password env var '{env_name}' is not set "
                    "(set it in .env or the environment)"
                ),
            }
        return {
            "username": username,
            "password": str(val),
            "source": "env",
            "env_var": env_name,
        }
    b64 = str(creds.get("password_b64") or "").strip()
    if b64:
        try:
            pw = base64.b64decode(b64).decode("utf-8")
        except Exception as e:
            return {"error": f"corrupt password_b64: {e!r}"}
        return {"username": username, "password": pw, "source": "b64"}
    return {"username": username, "password": "", "source": "none"}


# ─── WS-Security + SOAP ──────────────────────────────────────────


def build_wsse_header(username: str, password: str) -> str:
    """WS-Security UsernameToken Digest header (the ONVIF standard)."""
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(
            nonce + created.encode("utf-8")
            + str(password or "").encode("utf-8"),
        ).digest(),
    ).decode("ascii")
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    u = str(username or "")
    return (
        f'<wsse:Security s:mustUnderstand="1" '
        f'xmlns:wsse="{_WSSE_NS}" '
        f'xmlns:wsu="{_WSU_NS}">'
        "<wsse:UsernameToken>"
        f"<wsse:Username>{u}</wsse:Username>"
        f'<wsse:Password Type="{_PASSWORD_DIGEST_TYPE}">'
        f"{digest}</wsse:Password>"
        f"<wsse:Nonce>{nonce_b64}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken>"
        "</wsse:Security>"
    )


def soap_envelope(body_xml: str, wsse_xml: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Header>{wsse_xml}</s:Header>"
        f"<s:Body>{body_xml}</s:Body>"
        "</s:Envelope>"
    )


def _local(tag: str) -> str:
    """Namespace-stripped localname of an ElementTree tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def post_soap(
    url: str,
    body_xml: str,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
) -> Any:
    """POST one SOAP envelope; return the parsed response element.

    Raises ``OnvifError`` on transport / HTTP / SOAP-Fault errors so
    handlers can format a clean ``{"error": ...}`` for the LLM.
    """
    import httpx

    envelope = soap_envelope(
        body_xml,
        build_wsse_header(username, password) if username else "",
    )
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    try:
        resp = httpx.post(
            url, content=envelope.encode("utf-8"),
            headers=headers, timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise OnvifError(f"ONVIF transport error: {e!r}") from e
    if resp.status_code in (401, 403):
        raise OnvifError(
            f"ONVIF authentication failed (HTTP {resp.status_code}) — "
            "check the device credentials "
            "(onvif_test_credentials can verify them)"
        )
    if resp.status_code >= 400:
        raise OnvifError(f"ONVIF HTTP {resp.status_code} from {url}")
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise OnvifError(f"ONVIF non-XML response: {e!r}") from e
    for elem in root.iter():
        if _local(elem.tag) == "Fault":
            text = "SOAP fault"
            for sub in elem.iter():
                if _local(sub.tag) in ("faultstring", "Text"):
                    text = sub.text or text
                    break
            raise OnvifError(f"ONVIF SOAP fault: {text}")
    return root


def _text_of(root: Any, *localnames: str) -> str:
    """First descendant text matching any localname."""
    wanted = set(localnames)
    for elem in root.iter():
        if _local(elem.tag) in wanted:
            return (elem.text or "").strip()
    return ""


# ─── WS-Discovery ────────────────────────────────────────────────


def parse_probe_matches(xml_text: str) -> list[dict[str, Any]]:
    """Parse ProbeMatch elements → [{xaddr, ip, port, name_hint}]."""
    out: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for pm in root.iter():
        if _local(pm.tag) != "ProbeMatch":
            continue
        xaddrs = scopes = types = ""
        for sub in pm.iter():
            ln = _local(sub.tag)
            if ln == "XAddrs" and not xaddrs:
                xaddrs = (sub.text or "").strip()
            elif ln == "Scopes" and not scopes:
                scopes = (sub.text or "").strip()
            elif ln == "Types" and not types:
                types = (sub.text or "").strip()
        first = xaddrs.split()[0] if xaddrs else ""
        ip, port = "", 80
        if "://" in first:
            try:
                from urllib.parse import urlparse

                p = urlparse(first)
                ip = p.hostname or ""
                port = p.port or (443 if p.scheme == "https" else 80)
            except Exception:
                pass
        name_hint = ""
        for token in scopes.split():
            if "onvif://www.onvif.org/name/" in token:
                name_hint = token.split(
                    "onvif://www.onvif.org/name/",
                )[-1]
                break
        out.append({
            "xaddr": first, "ip": ip, "port": port,
            "name_hint": name_hint, "types": types,
        })
    return out


def wsdiscovery_probe(
    timeout_s: float = 5.0,
    interface_ip: str | None = None,
) -> list[dict[str, Any]]:
    """One UDP multicast Probe; collect ProbeMatch responses.

    Read-only and LAN-local: the packet only reaches the multicast
    group on the chosen interface. De-duplicated by xaddr.
    """
    probe = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        "<e:Header>"
        f"<w:MessageID>urn:uuid:{uuid.uuid4()}</w:MessageID>"
        '<w:To e:mustUnderstand="true">'
        "urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
        '<w:Action e:mustUnderstand="true">'
        "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe"
        "</w:Action>"
        "</e:Header>"
        "<e:Body>"
        "<d:Probe>"
        "<d:Types>dn:NetworkVideoTransmitter</d:Types>"
        "</d:Probe></e:Body></e:Envelope>"
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(max(0.5, float(timeout_s)))
    try:
        if interface_ip:
            try:
                import struct

                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(str(interface_ip)),
                )
            except OSError:
                pass
        sock.sendto(probe.encode("utf-8"), _DISCOVERY_ADDR)
        deadline = time.time() + max(0.5, float(timeout_s))
        matches: list[dict[str, Any]] = []
        seen: set[str] = set()
        while time.time() < deadline:
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            for m in parse_probe_matches(data.decode("utf-8", "replace")):
                key = m.get("xaddr") or f"{m.get('ip')}:{m.get('port')}"
                if key and key not in seen:
                    seen.add(key)
                    matches.append(m)
        return matches
    finally:
        try:
            sock.close()
        except OSError:
            pass


# ─── device / media service round-trips ──────────────────────────

_NS_TDS = "http://www.onvif.org/ver10/device/wsdl"
_NS_TRT = "http://www.onvif.org/ver10/media/wsdl"


def get_device_information(
    xaddr: str, username: str, password: str, timeout: float = 10.0,
) -> dict[str, Any]:
    """GetDeviceInformation — ALSO the credential test (lightweight)."""
    root = post_soap(
        xaddr,
        f'<tds:GetDeviceInformation xmlns:tds="{_NS_TDS}"/>',
        username, password, timeout,
    )
    return {
        "manufacturer": _text_of(root, "Manufacturer"),
        "model": _text_of(root, "Model"),
        "firmware": _text_of(root, "FirmwareVersion"),
        "serial": _text_of(root, "SerialNumber"),
        "name": _text_of(root, "Name"),
    }


def get_profiles(
    xaddr: str, username: str, password: str, timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """GetProfiles — token / name / video resolution + framerate."""
    root = post_soap(
        xaddr,
        f'<trt:GetProfiles xmlns:trt="{_NS_TRT}"/>',
        username, password, timeout,
    )
    out: list[dict[str, Any]] = []
    for profiles in root.iter():
        if _local(profiles.tag) != "Profiles":
            continue
        token = profiles.get("token") or ""
        name = ""
        width = height = fps = 0
        for sub in profiles.iter():
            ln = _local(sub.tag)
            if ln == "Name" and not name:
                name = (sub.text or "").strip()
            elif ln == "Width" and not width:
                try:
                    width = int((sub.text or "0").strip() or 0)
                except ValueError:
                    width = 0
            elif ln == "Height" and not height:
                try:
                    height = int((sub.text or "0").strip() or 0)
                except ValueError:
                    height = 0
            elif ln == "FrameRateLimit" and not fps:
                try:
                    fps = int(float((sub.text or "0").strip() or 0))
                except ValueError:
                    fps = 0
        out.append({
            "token": token, "name": name,
            "width": width, "height": height, "fps": fps,
        })
    return out


def get_media_xaddr(
    xaddr: str, username: str, password: str, timeout: float = 10.0,
) -> str:
    """GetCapabilities(Media) → the media service XAddr."""
    root = post_soap(
        xaddr,
        f'<tds:GetCapabilities xmlns:tds="{_NS_TDS}">'
        "<tds:Category>Media</tds:Category>"
        "</tds:GetCapabilities>",
        username, password, timeout,
    )
    for elem in root.iter():
        if _local(elem.tag) == "XAddr":
            return (elem.text or "").strip()
    return xaddr.replace("device_service", "media_service")


def get_service_xaddr(
    xaddr: str,
    category: str,
    username: str,
    password: str,
    timeout: float = 10.0,
) -> str:
    """GetCapabilities(Category=…) → that service's XAddr.

    ``category`` ∈ {Media, PTZ, Events, Imaging, Device}. Falls back
    to the conventional ``<category>_service`` sibling URL when the
    device omits the capability block.
    """
    root = post_soap(
        xaddr,
        f'<tds:GetCapabilities xmlns:tds="{_NS_TDS}">'
        f"<tds:Category>{category}</tds:Category>"
        "</tds:GetCapabilities>",
        username, password, timeout,
    )
    for elem in root.iter():
        if _local(elem.tag) == "XAddr":
            return (elem.text or "").strip()
    cat = category.lower()
    if cat == "media":
        return xaddr.replace("device_service", "media_service")
    return xaddr.replace("device_service", f"{cat}_service")


# ─── PTZ (Phase C, v0.9.41.0+) ──────────────────────────────────

_NS_TPTZ = "http://www.onvif.org/ver20/ptz/wsdl"
_NS_TEV = "http://www.onvif.org/ver10/events/wsdl"
_NS_WSA = "http://www.w3.org/2005/08/addressing"
_NS_TT = "http://www.onvif.org/ver10/schema"


def _clamp_speed(value: Any) -> float:
    """Clamp a PTZ velocity component into [-1.0, 1.0]."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(-1.0, min(1.0, v))


def ptz_continuous_move(
    ptz_xaddr: str,
    profile_token: str,
    pan: float = 0.0,
    tilt: float = 0.0,
    zoom: float = 0.0,
    duration_s: float = 1.0,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """ContinuousMove with an explicit Duration (auto-stops).

    Speeds are normalised ratios in [-1, 1] (0.5 ≈ half speed);
    duration is clamped into [0.1, 10] seconds — this tool is a
    nudge, not a joyride.
    """
    pan = _clamp_speed(pan)
    tilt = _clamp_speed(tilt)
    zoom = _clamp_speed(zoom)
    duration_s = max(0.1, min(10.0, float(duration_s or 1.0)))
    post_soap(
        ptz_xaddr,
        f'<tptz:ContinuousMove xmlns:tptz="{_NS_TPTZ}">'
        f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
        '<tptz:Velocity>'
        f'<tt:PanTilt xmlns:tt="{_NS_TT}" x="{pan}" y="{tilt}"/>'
        f'<tt:Zoom xmlns:tt="{_NS_TT}" x="{zoom}"/>'
        "</tptz:Velocity>"
        f"<tptz:Duration>PT{round(duration_s, 2)}S</tptz:Duration>"
        "</tptz:ContinuousMove>",
        username, password, timeout,
    )
    return {
        "ok": True, "pan": pan, "tilt": tilt, "zoom": zoom,
        "duration_s": round(duration_s, 2),
    }


def ptz_stop(
    ptz_xaddr: str,
    profile_token: str,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Stop any ongoing pan/tilt/zoom motion."""
    post_soap(
        ptz_xaddr,
        f'<tptz:Stop xmlns:tptz="{_NS_TPTZ}">'
        f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
        "</tptz:Stop>",
        username, password, timeout,
    )
    return {"ok": True}


def get_presets(
    ptz_xaddr: str,
    profile_token: str,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """GetPresets — the PTZ preset tokens stored on the device."""
    root = post_soap(
        ptz_xaddr,
        f'<tptz:GetPresets xmlns:tptz="{_NS_TPTZ}">'
        f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
        "</tptz:GetPresets>",
        username, password, timeout,
    )
    out: list[dict[str, Any]] = []
    for elem in root.iter():
        if _local(elem.tag) != "Preset":
            continue
        out.append({
            "token": elem.get("token") or "",
            "name": _text_of(elem, "Name"),
        })
    return out


def ptz_goto_preset(
    ptz_xaddr: str,
    profile_token: str,
    preset_token: str,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """GotoPreset — drive the camera to a stored preset position."""
    post_soap(
        ptz_xaddr,
        f'<tptz:GotoPreset xmlns:tptz="{_NS_TPTZ}">'
        f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
        f"<tptz:PresetToken>{preset_token}</tptz:PresetToken>"
        "</tptz:GotoPreset>",
        username, password, timeout,
    )
    return {"ok": True, "preset": preset_token}

# ─── Events / PullPoint (Phase C, v0.9.41.0+) ───────────────────


def create_pull_point_subscription(
    events_xaddr: str,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
    termination: str = "PT5M",
) -> str:
    """CreatePullPointSubscription → the PullPoint service URL.

    The returned address is where ``pull_messages`` POSTs. The
    subscription auto-expires after ``termination`` (ISO 8601
    duration) — the watcher simply re-subscribes each poll.
    """
    root = post_soap(
        events_xaddr,
        f'<tev:CreatePullPointSubscription xmlns:tev="{_NS_TEV}">'
        f"<tev:InitialTerminationTime>{termination}"
        "</tev:InitialTerminationTime>"
        "</tev:CreatePullPointSubscription>",
        username, password, timeout,
    )
    for elem in root.iter():
        if _local(elem.tag) == "Address":
            return (elem.text or "").strip()
    raise OnvifError(
        "CreatePullPointSubscription returned no SubscriptionReference",
    )


def pull_messages(
    sub_xaddr: str,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
    poll_seconds: float = 2.0,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """PullMessages — long-poll the PullPoint once, parse events.

    Returns ``[{topic, utc_time, data: {name: value}, source: {...}}]``.
    Topics look like ``tns1:RuleEngine/CellMotionDetector/Motion`` —
    motion-alarm events carry ``State=true`` in their data items.
    """
    poll_seconds = max(0.5, min(30.0, float(poll_seconds or 2.0)))
    limit = max(1, min(100, int(limit or 10)))
    root = post_soap(
        sub_xaddr,
        f'<tev:PullMessages xmlns:tev="{_NS_TEV}">'
        f"<tev:Timeout>PT{round(poll_seconds, 1)}S</tev:Timeout>"
        f"<tev:MessageLimit>{limit}</tev:MessageLimit>"
        "</tev:PullMessages>",
        username, password, timeout + poll_seconds + 2.0,
    )
    out: list[dict[str, Any]] = []
    for msg in root.iter():
        if _local(msg.tag) != "NotificationMessage":
            continue
        topic = ""
        utc_time = (msg.get("UtcTime") or "").strip()
        items: dict[str, str] = {}
        source: dict[str, str] = {}
        for part in msg:
            ln = _local(part.tag)
            if ln == "Topic" and not topic:
                topic = (part.text or "").strip()
            elif ln == "Message":
                for section in part:
                    sln = _local(section.tag)
                    if sln not in ("Source", "Data"):
                        continue
                    bucket = source if sln == "Source" else items
                    for item in section:
                        if _local(item.tag) == "SimpleItem":
                            name = item.get("Name") or ""
                            if name:
                                bucket = (
                                    source if sln == "Source" else items
                                )
                                bucket[name] = item.get("Value") or ""
        out.append({
            "topic": topic, "utc_time": utc_time, "data": items,
            "source": source,
        })
    return out


def match_events(events: list[dict[str, Any]],
                 keywords: list[str]) -> list[dict[str, Any]]:
    """Keyword filter (case-insensitive substring on topic + values)."""
    kws = [k.strip().lower() for k in keywords if str(k).strip()]
    if not kws:
        return events
    out: list[dict[str, Any]] = []
    for e in events:
        data_blob = " ".join(
            f"{k}={v}"
            for k, v in (e.get("data") or {}).items()
        )
        blob = (e.get("topic") or "") + " " + data_blob
        low = blob.lower()
        if any(k in low for k in kws):
            out.append(e)
    return out


def drain_events(
    events_xaddr: str,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
    rounds: int = 1,
    poll_seconds: float = 2.0,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """One-shot event drain — subscribe, pull ``rounds`` times, done."""
    sub = create_pull_point_subscription(
        events_xaddr, username, password, timeout,
    )
    events: list[dict[str, Any]] = []
    for _ in range(max(1, min(5, int(rounds or 1)))):
        events.extend(pull_messages(
            sub, username, password, timeout, poll_seconds, limit,
        ))
    return events

# ─── watch store (v0.9.41.0+ — events → snapshot / webhook) ─────


class WatchStore:
    """Persistent event-watch configs — ``onvif_watches.json``.

    Mirror of ``FavoriteStore``: atomic temp+replace writes in
    ``$QDRANT_RAG_HOME``; the watcher threads themselves live in the
    app process (``OnvifLocalHost``) and are auto-resumed at GUI boot.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        import json

        self._json = json
        if path is None:
            home = Path(os.environ.get("QDRANT_RAG_HOME") or
                        Path.home() / ".qdrant-rag-agent")
            self.path = Path(home) / "onvif_watches.json"
        else:
            self.path = Path(path)
        self._load()

    def _load(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        try:
            if self.path.exists():
                self._data = (json.loads(self.path.read_text(
                    encoding="utf-8")) or {}).get("watches", {})
        except (OSError, ValueError):
            self._data = {}

    def _save(self) -> None:
        import json
        import tempfile

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"watches": self._data}, f,
                          ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def add(self, name: str, device: str, keywords: str = "motion",
            poll_interval: float = 5.0, webhook_trigger_id: str = "",
            snapshot_on_event: bool = False, overwrite: bool = False,
            ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise OnvifError("watch name is required")
        if (name in self._data) and not overwrite:
            raise OnvifError(
                f"watch {name!r} already exists — pass overwrite=true",
            )
        entry = {
            "name": name,
            "device": (device or "").strip(),
            "keywords": (keywords or "motion").strip(),
            "poll_interval": max(2.0, min(300.0, float(
                poll_interval or 5.0))),
            "webhook_trigger_id": (webhook_trigger_id or "").strip(),
            "snapshot_on_event": bool(snapshot_on_event),
            "status": "running",
            "event_count": 0,
            "last_event_at": "",
            "last_error": "",
            "created_at": _iso_now(),
        }
        self._data[name] = entry
        self._save()
        return dict(entry)

    def get(self, name: str) -> dict[str, Any] | None:
        entry = self._data.get((name or "").strip())
        return dict(entry) if entry else None

    def remove(self, name: str) -> bool:
        name = (name or "").strip()
        if name not in self._data:
            return False
        del self._data[name]
        self._save()
        return True

    def list(self) -> list[dict[str, Any]]:
        return [dict(v) for v in self._data.values()]

    def patch(self, name: str, **fields: Any) -> dict[str, Any] | None:
        name = (name or "").strip()
        entry = self._data.get(name)
        if entry is None:
            return None
        entry.update(fields)
        self._save()
        return dict(entry)

    # ── per-watch event history (50-entry ring, jsonl) ──

    def history_path(self, name: str) -> Path:
        return self.path.parent / "onvif_watch_history" / (
            f"{(name or 'watch').strip()}.jsonl"
        )

    def append_history(self, name: str, event: dict[str, Any],
                       limit: int = 50) -> None:
        import json

        p = self.history_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()[-(
                limit - 1):]
        lines.append(json.dumps(event, ensure_ascii=False))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def read_history(self, name: str, limit: int = 10) -> list[dict]:
        import json

        p = self.history_path(name)
        if not p.exists():
            return []
        rows = p.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for r in rows:
            try:
                out.append(json.loads(r))
            except ValueError:
                continue
        return out

    @staticmethod
    def mask_watch(entry: dict[str, Any]) -> dict[str, Any]:
        masked = dict(entry)
        return masked

# --- part 1c anchor: keep ---


def get_snapshot_uri(
    media_xaddr: str,
    profile_token: str,
    username: str,
    password: str,
    timeout: float = 10.0,
) -> str:
    """GetSnapshotUri for one media profile."""
    root = post_soap(
        media_xaddr,
        f'<trt:GetSnapshotUri xmlns:trt="{_NS_TRT}">'
        f"<trt:ProfileToken>{profile_token}</trt:ProfileToken>"
        "</trt:GetSnapshotUri>",
        username, password, timeout,
    )
    for elem in root.iter():
        if _local(elem.tag) == "Uri":
            return (elem.text or "").strip()
    raise OnvifError("ONVIF GetSnapshotUri returned no Uri")


# ─── snapshot download ───────────────────────────────────────────


def fetch_snapshot(
    uri: str,
    username: str,
    password: str,
    timeout: float = 10.0,
    max_bytes: int = 8 * 1024 * 1024,
) -> tuple[bytes, str]:
    """HTTP GET the snapshot image; Digest → Basic auth fallback.

    Returns ``(jpeg_bytes, "image/jpeg")``; raises ``OnvifError`` on
    HTTP / auth / oversize / non-JPEG-magic failures.
    """
    import httpx

    auth_chain = [None]
    if username or password:
        auth_chain = [
            httpx.DigestAuth(username, password),
            httpx.BasicAuth(username, password),
            None,
        ]
    last_status = 0
    for auth in auth_chain:
        try:
            resp = httpx.get(
                uri, auth=auth, timeout=timeout, follow_redirects=True,
            )
        except httpx.HTTPError as e:
            raise OnvifError(f"snapshot transport error: {e!r}") from e
        last_status = resp.status_code
        if resp.status_code == 200:
            body = resp.content or b""
            if len(body) > max_bytes:
                raise OnvifError(
                    f"snapshot too large ({len(body) // 1024} KB > "
                    f"{max_bytes // 1024} KB cap)"
                )
            if not body.startswith(b"\xff\xd8\xff"):
                raise OnvifError(
                    "snapshot is not a JPEG image "
                    "(the device returned non-image content)"
                )
            return body, "image/jpeg"
        if resp.status_code in (401, 403):
            continue  # try the next auth scheme
        raise OnvifError(f"snapshot HTTP {resp.status_code} from device")
    raise OnvifError(
        f"snapshot authentication failed (HTTP {last_status}) — "
        "check the device credentials"
    )


def resolve_snapshot_path(raw_path: str, write_root: Path) -> Path:
    """Resolve the snapshot output path INSIDE the write sandbox.

    Empty → ``uploads/onvif-snapshot-<timestamp>.jpg``; traversal
    outside the root is refused (mirrors the v0.9.30 bridge).
    """
    root = Path(write_root).resolve()
    raw = str(raw_path or "").strip()
    if not raw:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return root / "uploads" / f"onvif-snapshot-{stamp}.jpg"
    p = Path(raw)
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    low_p, low_root = str(p).lower(), str(root).lower()
    if not (low_p.startswith(low_root + os.sep) or p == root):
        raise OnvifError(
            f"path {p.as_posix()} is outside the write sandbox "
            f"{root.as_posix()}"
        )
    if p.suffix.lower() not in (".jpg", ".jpeg"):
        p = p.with_suffix(".jpg")
    return p


# ─── favorites store ─────────────────────────────────────────────


def device_urls(device: dict) -> dict[str, str]:
    """Static URLs for one favorite entry — NO network calls (v0.9.56.0).

    ``http_url``      — the ONVIF device endpoint: the stored ``xaddr``
                        when it is an http(s) URL, else the conventional
                        ``http://<ip>:<port>/onvif/device_service``.
    ``rtsp_url_guess``— the conventional ``rtsp://<ip>:554/`` base.
                        Vendor paths vary (Hikvision / Dahua / XMEye) —
                        use ``onvif_favorite_list(resolve=true)`` for
                        the ACTUAL GetStreamUri result.
    """
    ip = str(device.get("ip") or "").strip()
    xaddr = str(device.get("xaddr") or "").strip()
    try:
        port = int(device.get("port") or 80)
    except (TypeError, ValueError):
        port = 80
    http_url = xaddr if xaddr.startswith("http") else (
        f"http://{ip}:{port}/onvif/device_service" if ip else ""
    )
    rtsp_guess = f"rtsp://{ip}:554/" if ip else ""
    return {"http_url": http_url, "rtsp_url_guess": rtsp_guess}


class FavoriteStore:
    """Persistent favorite-camera list with credential management.

    Atomic JSON writes; passwords are stored base64-OBSCURED or as a
    ``password_env`` reference (recommended). Every list output goes
    through the mask helpers — the plain secret NEVER leaves
    :meth:`get`.
    """

    def __init__(self, path: str | Path | None = None):
        if path:
            self.path = Path(path)
        else:
            home = os.environ.get(
                "QDRANT_RAG_HOME",
                str(Path.home() / ".qdrant-rag-agent"),
            )
            self.path = Path(home) / "onvif" / "favorites.json"
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(
                    data.get("devices"), list):
                return data
        except Exception:
            pass
        return {"version": 1, "devices": []}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def add(
        self,
        name: str,
        ip: str,
        port: int = 8000,
        username: str = "admin",
        password: str = "",
        password_env: str = "",
        manufacturer: str = "",
        model: str = "",
        xaddr: str = "",
        notes: str = "",
        overwrite: bool = False,
        cred_set: str = "",
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name or not str(ip or "").strip():
            return {"error": "'name' and 'ip' are required"}
        with self._lock:
            data = self.load()
            devices = data.setdefault("devices", [])
            for d in devices:
                if d.get("name") == name and not overwrite:
                    return {
                        "error": (
                            f"favorite '{name}' already exists — "
                            "pass overwrite=true to replace it"
                        ),
                    }
            entry = {
                "name": name,
                "ip": str(ip).strip(),
                "port": int(port or 80),
                "manufacturer": str(manufacturer or ""),
                "model": str(model or ""),
                "xaddr": str(xaddr or "").strip(),
                "cred_set": str(cred_set or "").strip(),
                "credentials": {
                    "username": str(username or ""),
                    "password_b64": (
                        base64.b64encode(
                            str(password or "").encode("utf-8"),
                        ).decode("ascii")
                        if password else ""
                    ),
                    "password_env": str(password_env or "").strip(),
                },
                "added_at": _iso_now(),
                "last_seen": None,
                "last_snapshot": None,
                "notes": str(notes or ""),
            }
            devices[:] = [d for d in devices if d.get("name") != name]
            devices.append(entry)
            self.save(data)
        return {"ok": True, "name": name, "path": self.path.as_posix()}

    def remove(self, name: str) -> bool:
        name = str(name or "").strip()
        with self._lock:
            data = self.load()
            devices = data.get("devices", [])
            kept = [d for d in devices if d.get("name") != name]
            if len(kept) == len(devices):
                return False
            data["devices"] = kept
            self.save(data)
        return True

    @staticmethod
    def mask_device(device: dict) -> dict:
        """Masked view — safe for tool output."""
        creds = device.get("credentials") or {}
        source = "env" if creds.get("password_env") else (
            "b64" if creds.get("password_b64") else "none"
        )
        env_name = str(creds.get("password_env") or "")
        return {
            "name": device.get("name"),
            "ip": device.get("ip"),
            "port": device.get("port"),
            "manufacturer": device.get("manufacturer"),
            "model": device.get("model"),
            "xaddr": device.get("xaddr"),
            "cred_set": device.get("cred_set") or None,
            "added_at": device.get("added_at"),
            "last_seen": device.get("last_seen"),
            "last_snapshot": device.get("last_snapshot"),
            "notes": device.get("notes"),
            "credential_source": source,
            "credential_username_masked": mask_username(
                str(creds.get("username") or "")
            ),
            "credential_masked": (
                f"env:{env_name}" if source == "env"
                else "******** (b64)" if source == "b64"
                else "(none)"
            ),
        }

    def list(self, masked: bool = True) -> list[dict[str, Any]]:
        devices = self.load().get("devices", [])
        if masked:
            return [self.mask_device(d) for d in devices]
        return devices

    def get(self, name: str) -> dict[str, Any] | None:
        name = str(name or "").strip()
        for d in self.load().get("devices", []):
            if d.get("name") == name:
                return d
        return None

    def touch(
        self,
        name: str,
        last_seen: bool = False,
        last_snapshot: str | None = None,
    ) -> None:
        with self._lock:
            data = self.load()
            for d in data.get("devices", []):
                if d.get("name") == name:
                    if last_seen:
                        d["last_seen"] = _iso_now()
                    if last_snapshot is not None:
                        d["last_snapshot"] = last_snapshot
                    self.save(data)
                    return


# ─── Phase B (v0.9.38.0+): recording via ffmpeg ──────────────────


def get_stream_uri(
    media_xaddr: str,
    profile_token: str,
    username: str,
    password: str,
    timeout: float = 10.0,
) -> str:
    """GetStreamUri for one media profile → the RTSP URL."""
    root = post_soap(
        media_xaddr,
        f'<trt:GetStreamUri xmlns:trt="{_NS_TRT}">'
        "<trt:StreamSetup>"
        '<tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast'
        "</tt:Stream>"
        '<tt:Transport xmlns:tt="http://www.onvif.org/ver10/schema">'
        "<tt:Protocol>UDP</tt:Protocol>"
        "</tt:Transport>"
        "</trt:StreamSetup>"
        f"<trt:ProfileToken>{profile_token}</trt:ProfileToken>"
        "</trt:GetStreamUri>",
        username, password, timeout,
    )
    for elem in root.iter():
        if _local(elem.tag) == "Uri":
            return (elem.text or "").strip()
    raise OnvifError("ONVIF GetStreamUri returned no Uri")


def _embed_rtsp_creds(uri: str, username: str, password: str) -> str:
    """Insert credentials into an rtsp:// URL userinfo if missing.

    ffmpeg answers the RTSP DESCRIBE challenge itself, but it needs
    the credentials reachable — the userinfo form
    ``rtsp://user:pass@host:554/...`` is the reliable way.
    """
    from urllib.parse import quote, urlparse

    if not (username or password) or "://" not in uri:
        return uri
    p = urlparse(uri)
    if p.username:
        return uri  # creds already embedded
    # v0.9.43.1: XMEye query-string style carries creds in the PATH
    # (rtsp://host:554/user=..&password=..&channel=1&stream=0.sdp?real=1
    # — only "?real=1" is the actual query) — adding an (often empty)
    # userinfo would corrupt the URL. Leave it alone.
    full = (p.path or "") + "?" + (p.query or "")
    if "user=" in full and "password=" in full:
        return uri
    from urllib.parse import quote

    user = quote(str(username or ""), safe="")
    pw = quote(str(password or ""), safe="")
    netloc = f"{user}:{pw}@{p.netloc}" if p.netloc else p.netloc
    return p._replace(netloc=netloc).geturl()


def resolve_record_path(raw_path: str, write_root: Path) -> Path:
    """Resolve the recording output path INSIDE the write sandbox.

    Empty → ``uploads/onvif-record-<timestamp>.mp4``; traversal
    outside the root is refused; non-.mp4 suffixes are forced.
    """
    root = Path(write_root).resolve()
    raw = str(raw_path or "").strip()
    if not raw:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return root / "uploads" / f"onvif-record-{stamp}.mp4"
    p = Path(raw)
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    low_p, low_root = str(p).lower(), str(root).lower()
    if not (low_p.startswith(low_root + os.sep) or p == root):
        raise OnvifError(
            f"path {p.as_posix()} is outside the write sandbox "
            f"{root.as_posix()}"
        )
    if p.suffix.lower() != ".mp4":
        p = p.with_suffix(".mp4")
    return p


# ─── media discovery fallbacks + vendor RTSP (v0.9.43.1) ─────────
#
# Live-box lesson (2026-09-14, XMEye DVR "五股邊巷"): GetProfiles on
# the DEVICE service xaddr returned zero profiles → snapshot died
# with "device returned no media profiles". Many Chinese NVR/DVR
# firmwares (XMEye / Hisi) serve the media service on a DIFFERENT
# endpoint (often :8899/onvif/media_service) and their RTSP stack
# does NOT accept the standard userinfo form — XMEye expects
# query-string creds (rtsp://ip:554/user=..&password=..&channel=..).


def _media_candidates(device_xaddr: str) -> list[str]:
    """Ordered media-service endpoint guesses for quirky devices."""
    from urllib.parse import urlparse, urlunparse

    base = str(device_xaddr or "").strip()
    if not base:
        return []
    p = urlparse(base)
    host = p.hostname or ""
    scheme = p.scheme or "http"
    out: list[str] = []
    if host:
        # conventional sibling (v0.9.37 fallback)
        out.append(urlunparse(
            p._replace(netloc=p.netloc,
                       path="/onvif/media_service"),
        ))
        # XMEye / Hisi NVR: ONVIF on :8899
        out.append(urlunparse(p._replace(
            netloc=f"{host}:8899", path="/onvif/media_service",
        )))
        # http media service variant
        out.append(urlunparse(p._replace(
            netloc=p.netloc, path="/onvif/http_media_service",
        )))
    # device xaddr itself (some DVRs serve media there)
    out.append(base)
    seen: set[str] = set()
    return [u for u in out if not (u in seen or seen.add(u))]


def get_profiles_anywhere(
    device_xaddr: str,
    username: str,
    password: str,
    timeout: float = 10.0,
) -> tuple[list[dict[str, Any]], str]:
    """Find media profiles across the device's media endpoints.

    Tries, in order: the GetCapabilities(Media) XAddr (when the
    device reports one), the conventional sibling path, the XMEye
    :8899 variant, the http_media_service variant, and finally the
    device xaddr itself. Returns ``(profiles, xaddr_used)``; raises
    ``OnvifError`` when every candidate comes back empty — v0.9.43.2:
    the message lists EACH candidate with its per-candidate failure
    reason (401 / transport refused / SOAP fault / empty), so one
    live log line pinpoints which endpoint failed and why.
    """
    tried: list[str] = []
    candidates: list[str] = []
    try:
        caps = get_media_xaddr(device_xaddr, username, password, timeout)
        if caps and caps not in _media_candidates(device_xaddr):
            candidates.append(caps)
    except Exception:
        caps = ""
    candidates.extend(_media_candidates(device_xaddr))
    reasons: list[str] = []
    for cand in candidates:
        tried.append(cand)
        try:
            profiles = get_profiles(cand, username, password, timeout)
        except Exception as e:
            reasons.append(f"{cand} -> {type(e).__name__}: {e}")
            continue
        if profiles:
            return profiles, cand
        reasons.append(f"{cand} -> HTTP OK but 0 profiles")
    raise OnvifError(
        "device returned no media profiles — tried media endpoints: "
        + " | ".join(reasons or tried),
    )


def rtsp_variants(rtsp_uri: str, username: str, password: str,
                  channel: int = 1) -> list[dict[str, str]]:
    """Vendor RTSP URL variants with embedded credentials.

    Some devices' RTSP stacks reject the standard userinfo form.
    Returns an ordered list of {style, uri} — ``standard`` userinfo,
    Hikvision, Dahua, and the XMEye query-string format — so the LLM
    can pass a working one to ``onvif_record(uri=...)``. The password
    IS embedded in these URLs (same policy as the recording flow —
    ffmpeg needs the creds reachable).
    """
    from urllib.parse import quote, urlsplit

    uri = str(rtsp_uri or "").strip()
    if not uri or not uri.lower().startswith("rtsp://"):
        return []
    sp = urlsplit(uri)
    host = sp.hostname or ""
    if not host:
        return []
    port = sp.port or 554
    user = quote(str(username or ""), safe="")
    pw = quote(str(password or ""), safe="")
    path = (sp.path or "").strip() or "/onvif1"
    out = []
    if username or password:
        userinfo = f"{user}:{pw}@"
    else:
        userinfo = ""
    out.append({
        "style": "standard",
        "uri": f"rtsp://{userinfo}{host}:{sp.port or 554}{path}",
    })
    out.append({
        "style": "hikvision",
        "uri": f"rtsp://{userinfo}{host}:{sp.port or 554}"
               f"/Streaming/Channels/{channel * 100 + 1}",
    })
    out.append({
        "style": "dahua",
        "uri": f"rtsp://{userinfo}{host}:{sp.port or 554}"
               f"/cam/realmonitor?channel={channel}&subtype=0",
    })
    # XMEye: credentials as QUERY STRING (no userinfo)
    q = (f"user={user}&password={pw}&channel={channel}"
         f"&stream=0.sdp?real=1")
    out.append({
        "style": "xmeye",
        "uri": f"rtsp://{host}:{sp.port or 554}/{q}",
    })
    return out


def grab_frame_via_rtsp(
    rtsp_uri: str,
    out_path: Path,
    settle_s: float = 0.0,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    """Grab ONE video frame from an RTSP stream via ffmpeg → JPEG.

    For cameras/DVRs without an ONVIF snapshot capability (XMEye etc.)
    — the RTSP stream decodes fine even when GetSnapshotUri is
    unsupported. ``-frames:v 1`` takes the first decoded frame;
    ``settle_s`` optionally skips initial black frames (input seek).
    Success requires a non-empty file that starts with the JPEG magic.
    """
    exe = ffmpeg_available()
    if not exe:
        raise OnvifError(
            "ffmpeg is not installed — RTSP snapshots require it "
            "(install with: winget install Gyan.FFmpeg, then "
            "restart the app)"
        )
    out_path = Path(out_path)
    if out_path.suffix.lower() not in (".jpg", ".jpeg"):
        out_path = out_path.with_suffix(".jpg")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y"]
    if str(rtsp_uri).lower().startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp"]
    if settle_s and settle_s > 0:
        cmd += ["-ss", f"{max(0.0, min(10.0, float(settle_s))):.2f}"]
    cmd += [
        "-i", str(rtsp_uri),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ]
    import subprocess

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            timeout=max(5.0, float(timeout_s)),
        )
    except subprocess.TimeoutExpired:
        raise OnvifError(
            f"RTSP frame grab timed out after {float(timeout_s):.0f} s "
            f"({rtsp_uri})"
        )
    tail = (proc.stderr or "").splitlines()[-3:]
    if proc.returncode != 0 or not out_path.is_file() \
            or out_path.stat().st_size == 0:
        raise OnvifError(
            f"RTSP frame grab failed (ffmpeg rc={proc.returncode}): "
            + " | ".join(tail)
        )
    head = out_path.read_bytes()[:3]
    if head != _JPEG_MAGIC:
        raise OnvifError(
            "RTSP frame grab produced a non-JPEG file "
            f"(rc={proc.returncode}): {' | '.join(tail)}"
        )
    return {"ok": True, "size": out_path.stat().st_size}


def ffmpeg_available() -> str | None:
    """Absolute ffmpeg path or None (``shutil.which``)."""
    import shutil

    return shutil.which("ffmpeg")


def record_stream(
    input_uri: str,
    out_path: Path,
    seconds: float = 10.0,
    timeout_margin_s: float = 30.0,
) -> dict[str, Any]:
    """Record ``seconds`` of ``input_uri`` into an MP4 via ffmpeg.

    ``-c copy`` stream-copies the camera's H.264 (no re-encode →
    near-zero CPU). The subprocess is bounded by
    ``seconds + timeout_margin_s``; rc 0/255 with a non-empty file
    counts as success (255 = ffmpeg's SIGINT-ish stop after ``-t``).
    """
    exe = ffmpeg_available()
    if not exe:
        raise OnvifError(
            "ffmpeg is not installed — recording requires it "
            "(install with: winget install Gyan.FFmpeg, then "
            "restart the app)"
        )
    seconds = max(1.0, min(float(seconds), 600.0))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y"]
    if str(input_uri).lower().startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp"]
    cmd += [
        "-i", str(input_uri),
        "-t", f"{seconds:.1f}",
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    import subprocess

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=seconds + timeout_margin_s,
        )
    except subprocess.TimeoutExpired:
        raise OnvifError(
            f"recording timed out after {seconds + timeout_margin_s:.0f} s"
        )
    ok_rc = proc.returncode in (0, 255)
    if not out_path.is_file() or out_path.stat().st_size == 0:
        tail = (proc.stderr or "").splitlines()[-3:]
        raise OnvifError(
            f"recording failed (ffmpeg rc={proc.returncode}): "
            + " | ".join(tail)
        )
    if not ok_rc:
        # file exists but ffmpeg signalled an error — keep it but warn
        return {
            "ok": True,
            "warning": f"ffmpeg exit code {proc.returncode}",
            "size": out_path.stat().st_size,
        }
    return {"ok": True, "size": out_path.stat().st_size}