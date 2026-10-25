"""Jev 分類器核心（v0.9.93.0+）— MCP core extension, mock-first.

以 Jev 決策模型（typesafe/jev，與 rerank 同後端）做「分類器」：
輸入吃指定 JSON 格式（inline 或 JSON 檔案），批次對每個 item 跑一次
jev_choice，回傳 choice + probabilities + 每類別統計。

**指定 JSON 格式**（payload / 檔案內容皆此形）::

    {
      "classes": ["帳單", "會議", {"name": "其他", "description": "…"}],
      "items":   [{"id": "a1", "text": "要分類的內容"}, …],
      "instructions": "額外分類指引（可空）",
      "default_class": "其他"
    }

批次：items 陣列逐筆分類（ThreadPoolExecutor 併發，
``QDRANT_JEV_CLASSIFY_WORKERS`` 預設 4；上限
``QDRANT_JEV_CLASSIFY_BATCH_MAX`` 50）。單筆失敗含在該筆的 ``error``
欄位，不拖垮整批。

模式（``JEV_MODE``）: ``mock``（預設 — 確定性雜湊挑選，不連網）|
``live``（真實 Jev 決策呼叫 — 重用 OPENROUTER_API_KEY 與 rerank 同
後端）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.agent.rerank import jev_choice

MODE_MOCK = "mock"
MODE_LIVE = "live"
DEFAULT_MODE = MODE_MOCK

DEFAULT_BATCH_MAX = 50
DEFAULT_WORKERS = 4
DEFAULT_TIMEOUT_S = 10

__all__ = [
    "MODE_MOCK", "MODE_LIVE", "DEFAULT_MODE",
    "DEFAULT_BATCH_MAX", "DEFAULT_WORKERS", "DEFAULT_TIMEOUT_S",
    "JevClassifierEngine",
]


def _batch_max() -> int:
    try:
        return max(1, min(500, int(os.environ.get(
            "QDRANT_JEV_CLASSIFY_BATCH_MAX", str(DEFAULT_BATCH_MAX)))))
    except (TypeError, ValueError):
        return DEFAULT_BATCH_MAX


def _workers() -> int:
    try:
        return max(1, min(16, int(os.environ.get(
            "QDRANT_JEV_CLASSIFY_WORKERS", str(DEFAULT_WORKERS)))))
    except (TypeError, ValueError):
        return DEFAULT_WORKERS


def _timeout_s() -> float:
    try:
        return max(2.0, min(60.0, float(os.environ.get(
            "QDRANT_JEV_CLASSIFY_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))))
    except (TypeError, ValueError):
        return float(DEFAULT_TIMEOUT_S)


class JevClassifierEngine:
    """Jev 分類器引擎（mock-first + live jev_choice）。"""

    def __init__(self) -> None:
        raw = (os.environ.get("JEV_MODE", "") or "").strip().lower()
        self.mode = raw if raw in (MODE_MOCK, MODE_LIVE) else MODE_MOCK
        self.is_mock = self.mode == MODE_MOCK

    # ── request normalization ───────────────────────────────────
    @staticmethod
    def parse_payload(raw: str) -> dict[str, Any]:
        raw = str(raw or "").strip()
        if not raw:
            return {"error": "payload_json/path 不可同時為空 — 請提供"
                             "指定 JSON 格式的分類請求"}
        try:
            d = json.loads(raw)
        except Exception as e:
            return {"error": f"JSON 解析失敗: {type(e).__name__}: "
                             f"{e}"[:200]}
        if not isinstance(d, dict):
            return {"error": "JSON 頂層必須是物件（classes + items）"}
        return d

    @staticmethod
    def normalize_request(d: dict[str, Any]) -> tuple[Any, ...]:
        """→ (class_names, class_descs, items, instructions,
        default_class) 或 (None, error_dict)。"""
        classes = d.get("classes")
        if not isinstance(classes, list) or not classes:
            return None, {"error": (
                '"classes" 必填：字串陣列或 '
                '[{"name":…,"description":…}]')}
        names: list[str] = []
        descs: dict[str, str] = {}
        for c in classes:
            if isinstance(c, dict):
                name = str(c.get("name") or "").strip()
                if not name:
                    return None, {"error": "class 物件缺少 name"}
                names.append(name)
                desc = str(c.get("description") or "").strip()
                if desc:
                    descs[name] = desc
            else:
                name = str(c).strip()
                if not name:
                    return None, {"error": "classes 含空字串"}
                if name in names:
                    return None, {"error": f"classes 重複: {name}"}
                names.append(name)
        if len(names) < 2:
            return None, {"error": "至少需要 2 個類別（分類器定義）"}
        items = d.get("items")
        if not isinstance(items, list) or not items:
            return None, {"error": '"items" 必填：[{…}] 陣列'}
        norm_items: list[dict[str, Any]] = []
        for i, it in enumerate(items):
            if isinstance(it, str):
                norm_items.append({"id": f"item-{i + 1}", "text": it})
                continue
            if not isinstance(it, dict):
                return None, {"error": f"items[{i}] 必須是字串或物件"}
            text = str(it.get("text") or it.get("content") or "").strip()
            if not text:
                return None, {"error": f"items[{i}] 缺少 text"}
            norm_items.append({
                "id": str(it.get("id") or f"item-{i + 1}"),
                "text": text,
            })
        instructions = str(d.get("instructions") or "").strip()
        default_class = str(d.get("default_class") or "").strip()
        if default_class and default_class not in names:
            return None, {"error": f"default_class {default_class!r} "
                                   "不在 classes 內"}
        return (names, descs, norm_items, instructions, default_class)

    # ── classification core ─────────────────────────────────────
    @staticmethod
    def _instructions(names: list[str], descs: dict[str, str],
                      extra: str, default_class: str) -> str:
        lines = [
            "你是精確的文字分類器。將「狀態」內容歸入最符合的單一類別，"
            "從下列選項中選擇：",
        ]
        for n in names:
            desc = descs.get(n)
            lines.append(f"- {n}" + (f"：{desc}" if desc else ""))
        if default_class:
            lines.append(f"若都不符合，選「{default_class}」。")
        if extra:
            lines.append(f"額外規則：{extra}")
        lines.append("只回覆類別名稱。")
        return "\n".join(lines)

    def _classify_one(self, item: dict[str, Any], names: list[str],
                      instructions: str, default_class: str,
                      ) -> dict[str, Any]:
        try:
            if self.is_mock:
                h = int(hashlib.sha1(
                    (instructions + item["text"]).encode("utf-8")
                ).hexdigest()[:8], 16)
                choice = names[h % len(names)]
                prob = round(0.5 + (h % 45) / 100.0, 2)
                return {"id": item["id"],
                        "text": item["text"][:120],
                        "choice": choice,
                        "prob": prob,
                        "mode": self.mode}
            choice, prob, meta = jev_choice(
                instructions, item["text"], names, timeout_s=_timeout_s())
            if choice is None:
                return {"id": item["id"],
                        "text": item["text"][:120],
                        "error": (meta.get("error")
                                  or "jev_choice 無回應")[:200]}
            return {"id": item["id"],
                    "text": item["text"][:120],
                    "choice": choice,
                    "prob": prob,
                    "elapsed_s": meta.get("elapsed_s"),
                    "mode": self.mode}
        except Exception as e:
            return {"id": item["id"],
                    "error": f"{type(e).__name__}: {e}"[:200]}

    def classify_batch(self, names: list[str], descs: dict[str, str],
                       items: list[dict[str, Any]], instructions: str,
                       default_class: str) -> dict[str, Any]:
        ins = self._instructions(names, descs, instructions, default_class)
        cap = _batch_max()
        if len(items) > cap:
            return {"error": f"items 數量 {len(items)} 超過批次上限 "
                             f"{cap}（QDRANT_JEV_CLASSIFY_BATCH_MAX）",
                    "mode": self.mode}
        t0 = time.time()
        workers = _workers()
        if self.is_mock or workers <= 1:
            results = [self._classify_one(it, names, ins, default_class)
                       for it in items]
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(
                    lambda it: self._classify_one(
                        it, names, ins, default_class),
                    items))
        counts: dict[str, int] = {}
        errors = 0
        for r in results:
            if r.get("error"):
                errors += 1
                continue
            c = str(r.get("choice") or "")
            counts[c] = counts.get(c, 0) + 1
        return {
            "ok": True,
            "mode": self.mode,
            "results": results,
            "counts": counts,
            "errors": errors,
            "total": len(items),
            "elapsed_s": round(time.time() - t0, 2),
            "model": (os.environ.get("JEV_MODEL", "").strip()
                      or "typesafe/jev-1.13"),
        }

    def classify_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        norm = self.normalize_request(payload)
        if norm[0] is None:
            return norm[1]
        names, descs, items, instructions, default_class = norm
        return self.classify_batch(names, descs, items, instructions,
                                   default_class)

    def status(self) -> dict[str, Any]:
        key_ready = bool((os.environ.get("OPENROUTER_API_KEY", "")
                          or "").strip())
        return {
            "mode": self.mode,
            "key_ready": key_ready,
            "batch_max": _batch_max(),
            "workers": _workers(),
            "timeout_s": _timeout_s(),
            "model": (os.environ.get("JEV_MODEL", "").strip()
                      or "typesafe/jev-1.13"),
        }

    @staticmethod
    def schema() -> dict[str, Any]:
        return {
            "format": {
                "classes": "字串陣列或 [{\"name\": \"類別名\", "
                           "\"description\": \"說明\"}]（至少 2 個）",
                "items": "[{\"id\": \"自訂 id（可空）\", "
                         "\"text\": \"要分類的內容（必填）\"}]",
                "instructions": "額外分類指引（可空）",
                "default_class": "都不符合時的預設類別（可空）",
            },
            "example": {
                "classes": ["帳單", "會議", "待辦"],
                "items": [
                    {"id": "a1", "text": "中華電信 8 月帳單 899 元"},
                    {"id": "a2", "text": "週三下午與設計師開會"},
                ],
                "instructions": "依內容主題分類",
            },
            "batch": "items 陣列即批次；上限 QDRANT_JEV_CLASSIFY_"
                     "BATCH_MAX（50），併發 QDRANT_JEV_CLASSIFY_WORKERS（4）",
        }

    def live_status(self) -> dict[str, Any]:
        out = self.status()
        out["backend"] = ("jev_choice @ openrouter.ai/api/alpha/decisions"
                          if not self.is_mock else "mock（hash pick）")
        return out