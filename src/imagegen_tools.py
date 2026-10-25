"""ImageGen 產圖核心（v0.9.104.0+）— OpenRouter dedicated Image API,
mock-first.

OpenRouter 的「Image API」（docs/guides/overview/multimodal/
image-generation）::

    GET  https://openrouter.ai/api/v1/images/models        模型目錄
    POST https://openrouter.ai/api/v1/images/generations   產圖

請求 body 例::

    {"model": "google/gemini-2.5-flash-image",
     "prompt": "…",
     "n": 1,                       # 1-10（providers 可能回得更少）
     "aspect_ratio": "16:9",       # 各模型 enum（1:1, 9:16, 4:3 …）
     "resolution": "1K",           # 512 | 1K | 2K | 4K
     "size": "2048x2048",          # tier 或明確像素（與 resolution 擇一）
     "quality": "high",            # auto | low | medium | high
     "output_format": "png",       # png | jpeg | webp | svg
     "seed": 12345}

非串流（buffered）回應::

    {"created": <unix>, "data": [{"b64_json": "…",
                                  "media_type": "image/png|webp|…"}],
     "usage": {"total_tokens": …, "cost": 0.007}}   # cost = USD

live 重用 OPENROUTER_API_KEY；**每次產圖收真實費用**（usage.cost 如實
回傳；全部成功才計費、失敗不計費）。``IMAGEGEN_MODE=mock``（預設）=
Pillow 產生確定性 placeholder 圖（NO network，simulated: true）；
live 模式另需總開關 ``IMAGEGEN_WRITES_ENABLED=on``（hicloud/keep
同款寫入閘）。

安全模型（hicloud/keep 收斂模式）:
* 每次產圖 ``confirm=true``（mock 亦強制 — 產圖會寫檔且 live 產生費用）;
* live 另需總開關 ``IMAGEGEN_WRITES_ENABLED=on``;
* 任何 HTTP/JSON 失敗回 ``{"error": …}`` 信封，絕不 raise；
* 引擎回傳**解碼後的 bytes**（不是 b64 字串）— 大 blob 絕不回流
  LLM context（v0.9.16.2 教訓）；真正落盤由 HOST 做原子寫入。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

MODE_MOCK = "mock"
MODE_LIVE = "live"
DEFAULT_MODE = MODE_MOCK

IMAGE_API_BASE = "https://openrouter.ai/api/v1"
GENERATIONS_PATH = "/images/generations"
MODELS_PATH = "/images/models"

DEFAULT_MODEL = "google/gemini-2.5-flash-image"
DEFAULT_MAX_IMAGES = 4
DEFAULT_TIMEOUT_S = 120.0
DIRNAME_DEFAULT = "images"

_ALLOWED_RESOLUTIONS = ("512", "1K", "2K", "4K")
_ALLOWED_QUALITIES = ("auto", "low", "medium", "high")
_ALLOWED_OUTPUT_FORMATS = ("png", "jpeg", "webp", "svg")
_RATIOS_RE = re.compile(r"^\d{1,2}\s*:\s*\d{1,2}$")
_MEDIA_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/webp": ".webp", "image/svg+xml": ".svg",
}

# mock 模式下的常備模型清單（live 的 image_gen_models 才會拉即時目錄）
_FALLBACK_MODELS = [
    {"id": "google/gemini-2.5-flash-image",
     "name": "Gemini 2.5 Flash Image（便宜快、文字準確）"},
    {"id": "google/gemini-3.1-flash-image", "name": "Gemini 3.1 Flash Image"},
    {"id": "bytedance-seed/seedream-4.5",
     "name": "Seedream 4.5（官方文件範例）"},
    {"id": "recraft/recraft-v4.1-flash",
     "name": "Recraft V4.1 Flash（最快省，~$0.007/張）"},
    {"id": "openai/gpt-image-1-mini", "name": "GPT Image 1 Mini"},
    {"id": "black-forest-labs/flux.2-klein-4b", "name": "FLUX.2 Klein 4B"},
    {"id": "openai/gpt-image-2", "name": "GPT Image 2（精緻；~$0.13/張）"},
]

__all__ = [
    "MODE_MOCK", "MODE_LIVE", "DEFAULT_MODE", "DEFAULT_MODEL",
    "DEFAULT_MAX_IMAGES", "DEFAULT_TIMEOUT_S", "DIRNAME_DEFAULT",
    "IMAGE_API_BASE", "GENERATIONS_PATH", "MODELS_PATH",
    "ImageGenEngine",
]


def _max_images() -> int:
    try:
        return max(1, min(10, int(os.environ.get(
            "IMAGEGEN_MAX_IMAGES", str(DEFAULT_MAX_IMAGES)))))
    except (TypeError, ValueError):
        return DEFAULT_MAX_IMAGES


def _timeout_s() -> float:
    try:
        return max(30.0, min(300.0, float(os.environ.get(
            "IMAGEGEN_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _dir_name() -> str:
    raw = (os.environ.get("IMAGEGEN_DIR", "") or "").strip()
    if not raw or re.search(r'[\\/:*?"<>|]', raw) or raw in (".", ".."):
        return DIRNAME_DEFAULT
    return raw


def _default_model() -> str:
    return (os.environ.get("IMAGEGEN_MODEL", "").strip()
            or DEFAULT_MODEL)


def is_key_ok(key: str) -> bool:
    return bool(str(key or "").strip())


class ImageGenEngine:
    """OpenRouter Image API 產圖引擎（mock-first + live 直呼）。"""

    def __init__(self) -> None:
        raw = (os.environ.get("IMAGEGEN_MODE", "") or "").strip().lower()
        self.mode = raw if raw in (MODE_MOCK, MODE_LIVE) else MODE_MOCK
        self.is_mock = self.mode == MODE_MOCK
        self.writes_enabled = (os.environ.get(
            "IMAGEGEN_WRITES_ENABLED", "") or ""
        ).strip().lower() in ("on", "1", "yes", "true")

    # ── 寫入閘（hicloud/keep 收斂模式） ──────────────────────────
    def _gate_write(self, what: str, confirm: Any) -> str | None:
        """產圖的兩道閘。回 None = 通過；回字串 = 錯誤訊息。
        mock/live 都用 confirm=true 閘；live 另需總開關。"""
        if not (confirm is True or str(confirm).lower()
                in ("true", "1", "yes", "on")):
            return (
                f"{what} 需要 confirm=true（防意外操作 — 產圖會寫入 "
                "sandbox 的 images/ 目錄，且 live 模式每張圖有真實"
                "費用，需使用者明確指示才可確認）"
            )
        if self.mode == MODE_LIVE and not self.writes_enabled:
            return (
                f"{what} 未啟用：live 產圖需 IMAGEGEN_WRITES_ENABLED=on"
                "（總開關，預設 off；live 每張圖會向 OpenRouter 收"
                "真實費用，usage.cost 顯示金額）。開發/測試請保持 "
                "IMAGEGEN_MODE=mock。"
            )
        return None

    # ── 參數正規化 ───────────────────────────────────────────────
    @staticmethod
    def _norm_n(n: Any) -> tuple[int, str]:
        try:
            v = int(str(n if n not in (None, "") else "1").strip())
        except (TypeError, ValueError):
            return 0, f"n 必須是整數（1-{_max_images()}）"
        cap = _max_images()
        if v < 1:
            return 1, ""
        if v > cap:
            return 0, f"n={v} 超過上限 {cap}（IMAGEGEN_MAX_IMAGES）"
        return v, ""

    def _build_body(
        self, prompt: str, n: int, model: str, aspect_ratio: str,
        resolution: str, size: str, quality: str, output_format: str,
        seed: Any,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": (str(model or "").strip() or _default_model()),
            "prompt": prompt,
            "n": n,
        }
        if aspect_ratio:
            a = str(aspect_ratio).strip().replace(" ", "")
            if not _RATIOS_RE.match(a):
                return {"error": "aspect_ratio 需為 N:N 比例（例 "
                                 f"1:1 / 16:9 / 9:16 / 4:3 …），收到 "
                                 f"{aspect_ratio!r}"}
            body["aspect_ratio"] = a
        if resolution:
            r = str(resolution).strip().upper()
            if r not in _ALLOWED_RESOLUTIONS:
                return {"error": "resolution 需為 "
                                 + "/".join(_ALLOWED_RESOLUTIONS)
                                 + f"，收到 {resolution!r}"}
            body["resolution"] = r
        if size:
            body["size"] = str(size).strip()
        if quality:
            q = str(quality).strip().lower()
            if q not in _ALLOWED_QUALITIES:
                return {"error": "quality 需為 "
                                 + "/".join(_ALLOWED_QUALITIES)
                                 + f"，收到 {quality!r}"}
            body["quality"] = q
        if output_format:
            f = str(output_format).strip().lower()
            if f == "jpg":
                f = "jpeg"
            if f not in _ALLOWED_OUTPUT_FORMATS:
                return {"error": "output_format 需為 "
                                 + "/".join(_ALLOWED_OUTPUT_FORMATS)
                                 + f"，收到 {output_format!r}"}
            body["output_format"] = f
        if seed not in (None, "", 0, "0"):
            try:
                body["seed"] = int(seed)
            except (TypeError, ValueError):
                return {"error": f"seed 需為整數，收到 {seed!r}"}
        return body

    # ── 產圖（主入口） ───────────────────────────────────────────
    def generate(
        self, prompt: str, *, confirm: Any = None, n: Any = 1,
        model: str = "", aspect_ratio: str = "",
        resolution: str = "", size: str = "", quality: str = "",
        output_format: str = "", seed: Any = 0,
    ) -> dict[str, Any]:
        """文生圖。回傳 dict（images 內含解碼後 bytes — 絕不含
        base64 字串，避免撐爆 LLM context）。任何失敗回 error 信封。"""
        prompt = str(prompt or "").strip()
        if not prompt:
            return {"error": "prompt 必填（把構圖/風格/要出現的文字"
                             "寫清楚）", "mode": self.mode}
        use_n, err = self._norm_n(n)
        if err:
            return {"error": err, "mode": self.mode}
        body = self._build_body(
            prompt, use_n, model, aspect_ratio, resolution, size,
            quality, output_format, seed)
        if body.get("error"):
            return dict(body, mode=self.mode)
        gate = self._gate_write("產圖（OpenRouter Image API — 寫入"
                                 "圖檔；live 每張圖計費）", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if self.is_mock:
            return self._mock_generate(body)
        key = (os.environ.get("OPENROUTER_API_KEY", "") or "").strip()
        if not is_key_ok(key):
            return {"error": "OPENROUTER_API_KEY 未設定 — live 產圖"
                             "不可用；改回 IMAGEGEN_MODE=mock 使用"
                             "本機 placeholder 模擬。",
                    "mode": self.mode}
        return self._post_generate(body)

    # ── mock：Pillow 確定性 placeholder（NO network） ────────────
    @staticmethod
    def _mock_image(prompt: str) -> tuple[bytes, str]:
        from PIL import Image, ImageDraw

        h = hashlib.sha1(prompt.encode("utf-8")).digest()
        r, g, b = 96 + h[0] % 120, 96 + h[1] % 120, 96 + h[2] % 120
        img = Image.new("RGB", (512, 512), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 501, 501], outline=(r, g, b), width=6)
        draw.ellipse([156, 80, 356, 280], outline=(r, g, b), width=8)
        ascii_head = prompt.encode("ascii", errors="ignore").decode(
            "ascii", errors="ignore")[:58] or "(non-ascii prompt)"
        draw.text((30, 40), ascii_head, fill=(90, 90, 90))
        draw.text((30, 408), "MOCK PLACEHOLDER (imagegen mock mode)",
                  fill=(60, 60, 60))
        draw.text((30, 470), "IMAGEGEN_MODE=live for real generation",
                  fill=(120, 120, 120))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), "image/png"

    def _mock_generate(self, body: dict[str, Any]) -> dict[str, Any]:
        img_bytes, mt = self._mock_image(str(body["prompt"]))
        return {
            "ok": True, "mode": MODE_MOCK, "model": body["model"],
            "created": int(time.time()), "elapsed_s": 0.0,
            "n": int(body.get("n") or 1),
            "usage": {"total_tokens": 0, "cost": 0.0},
            "simulated": True,
            "images": [{"bytes": img_bytes, "media_type": mt,
                        "ext": ".png"}] * int(body.get("n") or 1),
            "note": ("mock 模式 — Pillow 確定性 placeholder（非真實"
                     "產圖；IMAGEGEN_MODE=live 走 OpenRouter Image API"
                     "，每張圖會有真實費用）"),
        }

    # ── live：POST /api/v1/images/generations ────────────────────
    def _post_generate(self, body: dict[str, Any]) -> dict[str, Any]:
        url = IMAGE_API_BASE + GENERATIONS_PATH
        key = (os.environ.get("OPENROUTER_API_KEY", "") or "").strip()
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:8334",
                "X-OpenRouter-Title": "divine-sense-agent",
            }, method="POST")
        t0 = time.time()
        status = 0
        try:
            with urllib.request.urlopen(req, timeout=_timeout_s()) as r:
                status = r.status
                text = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                raw = e.read().decode("utf-8", "replace")[:400]
                detail = raw[:200]
                try:
                    err = json.loads(raw).get("error")
                    if isinstance(err, dict) and err.get("message"):
                        detail = str(err["message"])[:200]
                except Exception:
                    pass
            except Exception:
                pass
            return {"error": f"OpenRouter Image API HTTP {e.code}: "
                             f"{detail}", "status": e.code,
                    "mode": self.mode, "model": body["model"]}
        except Exception as e:  # noqa: BLE001 — error envelope
            return {"error": f"OpenRouter Image API 呼叫失敗: {e!r}",
                    "mode": self.mode, "model": body["model"]}
        try:
            d = json.loads(text)
        except Exception as e:
            return {"error": f"回應 JSON 解析失敗: {e!r}",
                    "mode": self.mode}
        arr = d.get("data")
        if not isinstance(arr, list) or not arr:
            return {"error": "回應沒有 data 陣列 — 模型可能不支援所帶"
                             "參數（先用 image_gen_models 查詢該模型"
                             "支援的參數）", "mode": self.mode,
                    "status": status}
        images = []
        for item in arr:
            if not isinstance(item, dict):
                continue
            b64 = str(item.get("b64_json") or "")
            mt = str(item.get("media_type") or "image/png")
            if not b64:
                continue
            try:
                images.append({"bytes": base64.b64decode(b64),
                               "media_type": mt,
                               "ext": _MEDIA_EXT.get(mt, ".png")})
            except Exception:
                continue
        if not images:
            return {"error": "回應 data 沒有可解碼的 b64_json 圖片",
                    "mode": self.mode, "status": status}
        usage = d.get("usage") or {}
        return {"ok": True, "mode": MODE_LIVE, "model": body["model"],
                "created": d.get("created"),
                "elapsed_s": round(time.time() - t0, 2),
                "n": len(images),
                "usage": {"total_tokens": usage.get("total_tokens"),
                          "cost": usage.get("cost")},
                "images": images}

    # ── 模型目錄（GET /api/v1/images/models） ────────────────────
    def models_discovery(self) -> dict[str, Any]:
        if self.is_mock:
            return {"ok": True, "mode": MODE_MOCK,
                    "count": len(_FALLBACK_MODELS),
                    "models": _FALLBACK_MODELS,
                    "note": "mock 模式回傳內建常備清單；live 才會拉"
                            "openrouter 即時目錄（含定價能力描述）"}
        key = (os.environ.get("OPENROUTER_API_KEY", "") or "").strip()
        if not is_key_ok(key):
            return {"error": "OPENROUTER_API_KEY 未設定 — 拉不到即時"
                             "模型目錄", "mode": self.mode,
                    "models": _FALLBACK_MODELS}
        req = urllib.request.Request(
            IMAGE_API_BASE + MODELS_PATH, headers={
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "http://127.0.0.1:8334",
                "X-OpenRouter-Title": "divine-sense-agent",
            })
        try:
            with urllib.request.urlopen(req, timeout=30.0) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001 — error envelope
            return {"error": f"模型目錄拉取失敗: {e!r}",
                    "mode": self.mode}
        models = []
        for m in (d.get("data") or []):
            if not isinstance(m, dict) or not m.get("id"):
                continue
            models.append({
                "id": str(m["id"]),
                "name": str(m.get("name") or ""),
                "description": str(m.get("description") or "")[:100],
            })
        return {"ok": True, "mode": MODE_LIVE, "count": len(models),
                "models": models}

    # ── sandbox images/ 目錄（唯讀清單 + 狀態） ──────────────────
    def images_dir(self) -> Path | None:
        try:
            from src.mcp_client.host import _resolve_filesystem_root
            root = _resolve_filesystem_root(
                "QDRANT_FILESYSTEM_WRITE_ROOT", "./sandbox")
        except Exception:
            return None
        return Path(root) / _dir_name()

    def list_images(self) -> dict[str, Any]:
        d = self.images_dir()
        files: list[dict[str, Any]] = []
        if d is not None and d.is_dir():
            for p in d.iterdir():
                if not p.is_file():
                    continue
                try:
                    st = p.stat()
                    files.append({
                        "name": p.name,
                        "path": p.resolve().as_posix(),
                        "size": st.st_size,
                        "modified": datetime.fromtimestamp(
                            st.st_mtime).isoformat(timespec="seconds"),
                    })
                except OSError:
                    continue
        files.sort(key=lambda x: x.get("modified") or "", reverse=True)
        files = files[:50]
        return {"ok": True, "count": len(files), "files": files,
                "dir": d.resolve().as_posix() if d else None}

    def status(self) -> dict[str, Any]:
        key = (os.environ.get("OPENROUTER_API_KEY", "") or "").strip()
        d = self.images_dir()
        n_files = 0
        if d is not None and d.is_dir():
            n_files = sum(1 for p in d.iterdir() if p.is_file())
        return {
            "ok": True,
            "mode": self.mode,
            "model": _default_model(),
            "key_ready": bool(key),
            "writes_enabled_live": self.writes_enabled,
            "max_images": _max_images(),
            "timeout_s": _timeout_s(),
            "images_dir": d.resolve().as_posix() if d else None,
            "images_count": n_files,
            "api": IMAGE_API_BASE + GENERATIONS_PATH,
            "gate": "confirm=true（mock/live 皆強制）+ live 另需 "
                    "IMAGEGEN_WRITES_ENABLED=on",
            "note": ("mock 模式 = Pillow placeholder（無網路零費用）；"
                     "live 每張圖收真實費用（usage.cost）"),
        }

    # ── 檔名清理（host 寫檔用） ──────────────────────────────────
    @staticmethod
    def sanitized_filename(user_name: str, media_ext: str) -> str:
        """basename 化 + 非法字元清除 + 以圖檔實際格式強制副檔名。"""
        raw = re.sub(r'[\\/:*?"<>|]+', "",
                     str(user_name or "")).strip().strip(".")[:60]
        ext = (media_ext or ".png").lower()
        stem = os.path.splitext(raw)[0].rstrip(". ") if raw else ""
        if not stem:
            stem = ("img-" + time.strftime("%Y%m%d-%H%M%S")
                    + "-" + os.urandom(2).hex())
        return f"{stem}{ext}"