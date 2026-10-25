"""Google Keep 記事本工具核心（v0.9.88.0+）— mock-first 雙閘模式。

官方 Google Keep API v1（keep.googleapis.com/v1）方法面：
  * notes.create  POST /v1/notes
  * notes.delete  DELETE /v1/notes/{id}
  * notes.get     GET    /v1/notes/{id}
  * notes.list    GET    /v1/notes（filter 支援 title: 搜尋）
  * media.download（附件，本工具面暫不涵蓋）
  * **沒有 update 方法** — 既有記事無法透過官方 API 修改（工具面
    如實反映：建立/讀取/搜尋/刪除，不提供「編輯」工具）。

**mock-first 安全**：``KEEP_MODE=mock``（預設）執行完全模擬的記憶體
記事本 — 不連網、不動 Google 帳戶。live 模式需要：
  * Cloud Console 啟用 Google Keep API
  * OAuth client secret（桌面型）存於 ``KEEP_CLIENT_SECRET_PATH``
  * ``python cli.py keep-auth`` 跑一次 OAuth 同意（token 存
    ``KEEP_TOKEN_PATH``，含 refresh_token，工具執行期自動換新）
  * 寫入另需 ``KEEP_WRITES_ENABLED=on`` 總開關

寫入閘（hicloud 模式）：每通 create/delete 都要 ``confirm=true`` +
live 總開關。任何 HTTP/解析失敗回 ``{"error": ...}`` 包絡，不 raise。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid

from datetime import datetime, timezone
from typing import Any

import requests

MODE_MOCK = "mock"
MODE_LOCAL = "local"     # v0.9.89.0 — 自建本地筆記本（JSON 檔持久化）
MODE_LIVE = "live"
DEFAULT_MODE = MODE_MOCK

API_BASE = "https://keep.googleapis.com/v1"
KEEP_SCOPE = "https://www.googleapis.com/auth/keep"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_TIMEOUT_S = 30

__all__ = [
    "MODE_MOCK", "MODE_LIVE", "DEFAULT_MODE",
    "API_BASE", "KEEP_SCOPE", "KeepEngine",
]


class KeepEngine:
    """Google Keep 記事本引擎（mock-first + live REST）。"""

    def __init__(self) -> None:
        raw = (os.environ.get("KEEP_MODE", "") or "").strip().lower()
        self.mode = raw if raw in (MODE_MOCK, MODE_LOCAL, MODE_LIVE) \
            else DEFAULT_MODE
        self.is_mock = self.mode == MODE_MOCK
        self.writes_enabled = (
            os.environ.get("KEEP_WRITES_ENABLED", "").strip().lower()
            in ("on", "1", "true", "yes")
        )
        self._lock = threading.Lock()
        # mock 記憶體記事本（確定性種子 + 自增 id）
        self._mock_notes: dict[str, dict[str, Any]] = {}
        self._mock_seq = 0
        self._seed_mock()

    # ── resolvers ────────────────────────────────────────────────
    CALENDAR_SCOPES = (
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/calendar.events",
    )

    def _calendar_creds_entry(self) -> dict[str, Any] | None:
        """Locate the google-calendar MCP's client-secret file
        (config/mcp.json → env GOOGLE_OAUTH_CREDENTIALS), so Keep can
        share the SAME OAuth client + token file as Google Calendar
        (v0.9.88.1 — one consent covers calendar + keep)."""
        import glob as _glob

        for cfg_path in ("config/mcp.json", "mcp.json"):
            try:
                with open(cfg_path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            for s in (data.get("servers") or []):
                if "calendar" not in str(s.get("name", "")).lower():
                    continue
                raw = str((s.get("env") or {})
                          .get("GOOGLE_OAUTH_CREDENTIALS", "") or "")
                return {
                    "path": os.path.abspath(raw) if raw else "",
                    "env_var": "GOOGLE_OAUTH_CREDENTIALS",
                }
        for hits in (sorted(_glob.glob(
                os.path.join("google_oauth", "client_secret_*.json"))),):
            if hits:
                return {"path": os.path.abspath(hits[0]),
                        "env_var": "google_oauth glob"}
        return None

    def _client_secret_path(self) -> str:
        raw = (os.environ.get("KEEP_CLIENT_SECRET_PATH", "")
               or "").strip()
        if raw:
            return raw
        entry = self._calendar_creds_entry() or {}
        return str(entry.get("path") or "")

    def _token_path(self) -> str:
        raw = (os.environ.get("KEEP_TOKEN_PATH", "") or "").strip()
        if raw:
            return raw
        # v0.9.88.1: share the token file WITH the google-calendar MCP —
        # the calendar server keeps its token next to its client secret
        # (google_oauth/token.json). One consent, one refresh_token, and
        # both consumers refresh the same file.
        cs = self._client_secret_path()
        if cs:
            return os.path.join(os.path.dirname(cs), "token.json")
        home = (os.environ.get("QDRANT_RAG_HOME", "") or "").strip() or \
            os.path.expanduser("~/.qdrant-rag-agent")
        return os.path.join(home, "google_keep", "token.json")

    def _gate_write(self, what: str, confirm: Any) -> str | None:
        """寫入的兩道閘。回 None = 通過；回字串 = 錯誤訊息。
        local/mock 模式只需 confirm=true；live 另需總開關。"""
        if not (confirm is True or str(confirm).lower()
                in ("true", "1", "yes", "on")):
            return (
                f"{what} 需要 confirm=true（防意外操作 — 會真的新增/"
                "刪除記事，需使用者明確指示才可確認）"
            )
        if self.mode == MODE_LIVE and not self.writes_enabled:
            return (
                f"{what} 未啟用：live 寫入需 KEEP_WRITES_ENABLED=on"
                "（總開關，預設 off）。開發/測試請保持 KEEP_MODE=mock"
                " 或 local。"
            )
        return None

    # ── local note book（v0.9.89.0 自建筆記本 — JSON 檔持久化） ──
    def _local_store_path(self) -> str:
        raw = (os.environ.get("KEEP_NOTES_PATH", "") or "").strip()
        if raw:
            return raw
        home = (os.environ.get("QDRANT_RAG_HOME", "") or "").strip() or \
            os.path.expanduser("~/.qdrant-rag-agent")
        return os.path.join(home, "notes", "notes.json")

    def _local_load(self) -> dict[str, Any]:
        p = self._local_store_path()
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict) and isinstance(d.get("notes"), dict):
                return d
        except Exception:
            pass
        return {"notes": {}}

    def _local_save(self, data: dict[str, Any]) -> None:
        p = self._local_store_path()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)  # atomic write

    @staticmethod
    def _local_id() -> str:
        return (f"n-{int(time.time() * 1000)}-"
                f"{uuid.uuid4().hex[:4]}")

    def local_create(self, title: str, body_text: str,
                     category: str = "") -> dict[str, Any]:
        with self._lock:
            data = self._local_load()
            nid = self._local_id()
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            note = {
                "name": f"notes/{nid}",
                "title": title,
                "body": {"text": {"text": body_text}},
                "category": str(category or "").strip(),
                "createTime": now,
                "updateTime": now,
            }
            data["notes"][nid] = note
            self._local_save(data)
        return self._render(note)

    def local_update(self, note_id: str, title: str | None = None,
                     body: str | None = None,
                     category: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            data = self._local_load()
            note = data.get("notes", {}).get(note_id)
            if not note:
                return None
            if title is not None and str(title).strip():
                note["title"] = str(title).strip()
            if body is not None:
                note["body"] = {"text": {"text": str(body)}}
            if category is not None:
                note["category"] = str(category).strip()
            note["updateTime"] = datetime.now(
                timezone.utc).isoformat(timespec="seconds")
            self._local_save(data)
        return self._render(note)

    def local_list(self, query: str = "", max_results: int = 20,
                   category: str = "") -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        want_cat = (category or "").strip().lower()
        notes = self._local_load().get("notes", {})
        out = []
        for nid in sorted(
            notes,
            key=lambda k: (notes[k].get("createTime") or "", k),
            reverse=True,
        ):
            note = notes[nid]
            if want_cat and str(note.get("category") or "").lower() \
                    != want_cat:
                continue
            title = (note.get("title") or "").lower()
            text = self._body_text(note).lower()
            if not q or q in title or q in text:
                out.append(self._render(note))
        return out[:max(1, min(50, max_results))]

    def local_get(self, note_id: str) -> dict[str, Any] | None:
        note = self._local_load().get("notes", {}).get(note_id)
        return self._render(note) if note else None

    def local_delete(self, note_id: str) -> bool:
        with self._lock:
            data = self._local_load()
            if data.get("notes", {}).pop(note_id, None) is None:
                return False
            self._local_save(data)
        return True

    # ── paged search（v0.9.95.0 — Notes UI；永不整表傾倒） ───────
    def local_search(self, query: str = "", category: str = "",
                     date_from: str = "", date_to: str = "",
                     offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """分頁 + 過濾瀏覽（標題/內文關鍵字、分類、updateTime 日期
        區間）— 回傳 total + 當頁 notes，永不一次列完整清單
        （假設可達上億筆：UI 逐頁非同步載入）。"""
        q = (query or "").strip().lower()
        want_cat = (category or "").strip().lower()
        df = (date_from or "").strip()[:10]
        dt = (date_to or "").strip()[:10]
        notes = self._local_load().get("notes", {})
        rows = []
        for nid, note in notes.items():
            upd = (note.get("updateTime")
                   or note.get("createTime") or "")[:10]
            if want_cat and str(note.get("category") or "").lower() \
                    != want_cat:
                continue
            if q:
                title = (note.get("title") or "").lower()
                text = self._body_text(note).lower()
                if q not in title and q not in text:
                    continue
            if df and upd < df:
                continue
            if dt and upd > dt:
                continue
            rows.append((upd, nid, note))
        rows.sort(reverse=True)
        total = len(rows)
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            limit = 50
        page = [self._render(n) for _, _, n in rows[offset:offset + limit]]
        return {"total": total, "offset": offset, "limit": limit,
                "notes": page}

    def local_categories(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for note in self._local_load().get("notes", {}).values():
            c = str(note.get("category") or "").strip()
            if c:
                counts[c] = counts.get(c, 0) + 1
        return [{"name": n, "count": counts[n]}
                for n in sorted(counts)]

    # ── mock book ───────────────────────────────────────────────
    def _seed_mock(self) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        seeds = [
            ("歡迎使用 Keep 記事本",
             "這是 mock 模式的示範記事。\nKEEP_MODE=live 可接上真實 "
             "Google Keep。"),
            ("待辦", "1. 採購清單\n2. 排程回顧"),
        ]
        for title, body in seeds:
            nid = f"mock-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:8]}"
            self._mock_notes[nid] = {
                "name": f"notes/{nid}",
                "title": title,
                "body": {"text": {"text": body}},
                "createTime": now,
                "updateTime": now,
            }

    @staticmethod
    def _body_text(note: dict[str, Any]) -> str:
        body = (note or {}).get("body") or {}
        text = body.get("text") or {}
        if isinstance(text, dict) and text.get("text"):
            return str(text.get("text"))
        lst = body.get("list") or {}
        items = lst.get("listItems") or []
        lines = []
        for it in items:
            box = "☑" if it.get("checked") else "☐"
            lines.append(f"{box} {it.get('text', '')}")
        return "\n".join(lines)

    def _render(self, note: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str((note.get("name") or "").split("/")[-1]),
            "name": note.get("name"),
            "title": note.get("title", ""),
            "text": self._body_text(note),
            "category": str(note.get("category") or ""),
            "createTime": note.get("createTime", ""),
            "updateTime": note.get("updateTime", ""),
            "mode": self.mode,
        }

    def note_markdown(self, note_id: str) -> dict[str, Any]:
        """記事 → markdown 文本（keep_preview_note 的核心）。"""
        out = self.get_note(note_id)
        if out.get("error"):
            return out
        md = "\n".join([
            f"# {out.get('title') or '(無標題)'}",
            "",
            out.get("text") or "",
            "",
            "---",
            (f"*分類: {out.get('category') or '未分類'} · "
             f"建立: {out.get('createTime')} · "
             f"更新: {out.get('updateTime')} · id: {out.get('id')}*"),
        ])
        return {"markdown": md, "note": out, "mode": self.mode}

    # ── mock CRUD ───────────────────────────────────────────────
    def mock_create(self, title: str, body_text: str,
                    category: str = "") -> dict[str, Any]:
        self._mock_seq += 1
        nid = (f"mock{self._mock_seq:04d}-" + hashlib.sha1(
            (title + body_text).encode("utf-8")).hexdigest()[:6])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        note = {
            "name": f"notes/{nid}",
            "title": title,
            "body": {"text": {"text": body_text}},
            "category": str(category or "").strip(),
            "createTime": now,
            "updateTime": now,
        }
        self._mock_notes[nid] = note
        return self._render(note)

    def mock_update(self, note_id: str, title: str | None = None,
                    body: str | None = None,
                    category: str | None = None) -> dict[str, Any] | None:
        note = self._mock_notes.get(note_id)
        if not note:
            return None
        if title is not None and str(title).strip():
            note["title"] = str(title).strip()
        if body is not None:
            note["body"] = {"text": {"text": str(body)}}
        if category is not None:
            note["category"] = str(category).strip()
        note["updateTime"] = datetime.now(
            timezone.utc).isoformat(timespec="seconds")
        return self._render(note)

    def mock_list(self, query: str = "", max_results: int = 20
                  ) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        out = []
        for note in self._mock_notes.values():
            title = (note.get("title") or "").lower()
            text = self._body_text(note).lower()
            if not q or q in title or q in text:
                out.append(self._render(note))
        return out[:max(1, min(50, max_results))]

    def mock_get(self, note_id: str) -> dict[str, Any] | None:
        note = self._mock_notes.get(note_id)
        return self._render(note) if note else None

    def mock_delete(self, note_id: str) -> bool:
        return self._mock_notes.pop(note_id, None) is not None

    # ── auth (live) ─────────────────────────────────────────────
    def token_path(self) -> str:
        return self._token_path()

    @staticmethod
    def _token_expiry_epoch(tok: dict[str, Any]) -> float:
        # gcloud/googleapis shape carries expiry_date in MILLISECONDS;
        # our own shape carries expiry in seconds.
        ed = tok.get("expiry_date")
        if ed:
            try:
                return float(ed) / 1000.0
            except (TypeError, ValueError):
                pass
        try:
            return float(tok.get("expiry") or 0)
        except (TypeError, ValueError):
            return 0.0

    def load_token(self) -> dict[str, Any] | None:
        p = self._token_path()
        if not p or not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            return None
        if not isinstance(d, dict):
            return None
        # Two shapes share this file: ours ({access_token, expiry}) and
        # the googleapis/google-auth one ({token, expiry_date(ms)}).
        access = d.get("access_token") or d.get("token")
        if not access:
            return None
        d = dict(d)
        d.setdefault("access_token", str(access))
        d.setdefault("expiry", self._token_expiry_epoch(d))
        return d

    def save_token(self, tok: dict[str, Any]) -> str:
        p = self._token_path()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        # Write the googleapis-compatible shape so BOTH consumers of the
        # shared file (this engine + the google-calendar MCP) read it.
        out = {
            "access_token": str(tok.get("access_token") or ""),
            "refresh_token": str(tok.get("refresh_token") or ""),
            "scope": str(tok.get("scope") or ""),
            "token_type": str(tok.get("token_type") or "Bearer"),
            "expiry_date": int(self._token_expiry_epoch(tok) * 1000)
                if self._token_expiry_epoch(tok) else 0,
            "client_id": str(tok.get("client_id") or ""),
            "client_secret": str(tok.get("client_secret") or ""),
        }
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return p

    def _consent_scopes(self, scope_mode: str = "combined") -> list[str]:
        """依 scope_mode 組 consent 範圍。
        * ``combined``（預設）— keep + calendar（與 google-calendar
          共用同一把 token — 一次 consent 涵蓋兩者）；
          既有 token 的 scopes 一併保留。
        * ``calendar`` — 只同意 calendar scopes（Keep API 被拒時的
          fallback：calendar token 照樣落 shared 檔，keep 留 mock）。
        * ``keep`` — 只同意 keep scope（隔離診斷 invalid_scope 用）。
        """
        if scope_mode == "calendar":
            return list(self.CALENDAR_SCOPES)
        if scope_mode == "keep":
            return [KEEP_SCOPE]
        scopes: list[str] = [KEEP_SCOPE]
        for s in self.CALENDAR_SCOPES:
            if s not in scopes:
                scopes.append(s)
        existing = self.load_token() or {}
        for s in str(existing.get("scope") or "").split():
            if s and s not in scopes:
                scopes.append(s)
        return scopes

    def _client_id_secret(self) -> tuple[str, str] | None:
        """client_id/secret：token 檔優先，退回 client secret 檔。"""
        tok = self.load_token() or {}
        cid = str(tok.get("client_id") or "").strip()
        csec = str(tok.get("client_secret") or "").strip()
        if cid and csec:
            return cid, csec
        cs = self._client_secret_path()
        if cs and os.path.exists(cs):
            try:
                with open(cs, encoding="utf-8") as fh:
                    data = json.load(fh)
                inst = data.get("installed") or data.get("web") or {}
                cid = str(inst.get("client_id") or "").strip()
                csec = str(inst.get("client_secret") or "").strip()
                if cid and csec:
                    return cid, csec
            except Exception:
                pass
        return None

    def run_auth_flow(self, scope_mode: str = "combined") -> dict[str, Any]:
        """一次性 OAuth 同意（CLI ``keep-auth``）：開瀏覽器 → 存 token。
        需要 ``google-auth-oauthlib``（pip install）。

        scope_mode: ``combined``（預設）| ``calendar`` | ``keep`` —
        Google 回 ``Error 400: invalid_scope`` 時，先用
        ``--scopes keep`` 隔離 Keep scope（多半 = Keep API 未啟用或
        個人帳戶不支援），再 ``--scopes calendar`` 保住行事曆共用
        token。
        """
        scope_mode = (scope_mode or "combined").strip().lower()
        if scope_mode not in ("combined", "calendar", "keep"):
            return {"error": f"未知的 scope_mode: {scope_mode}（可選 "
                             "combined / calendar / keep）"}
        cs = self._client_secret_path()
        if not cs or not os.path.exists(cs):
            return {"error": (
                "live 模式需要 OAuth client secret 檔 — 請到 Google "
                "Cloud Console 建立 OAuth 用戶端（桌面應用），下載 JSON "
                "後設 KEEP_CLIENT_SECRET_PATH 指向它。")}
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            return {"error": (
                "需要 google-auth-oauthlib：pip install "
                "google-auth-oauthlib（僅 keep-auth 一次性流程需要；"
                "工具執行期只靠 requests + token.json）")}
        scopes = self._consent_scopes(scope_mode)
        flow = InstalledAppFlow.from_client_secrets_file(cs, scopes=scopes)
        creds = flow.run_local_server(port=0, prompt="consent")
        tok = {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "scope": " ".join(creds.scopes or scopes),
            "token_type": "Bearer",
            "expiry": (creds.expiry.timestamp()
                       if getattr(creds, "expiry", None) else 0),
        }
        try:
            with open(cs, encoding="utf-8") as fh:
                data = json.load(fh)
            inst = (data.get("installed") or data.get("web") or {})
            tok["client_id"] = inst.get("client_id", "")
            tok["client_secret"] = inst.get("client_secret", "")
        except Exception:
            pass
        path = self.save_token(tok)
        keep_note = ""
        if scope_mode == "combined":
            keep_note = (
                "token 與 google-calendar MCP 共用同一份 — 一次同意即"
                "涵蓋兩者，勿再分開授權。"
            )
        elif scope_mode == "calendar":
            keep_note = (
                "僅同意 calendar scopes — google-calendar 可用；Keep 留"
                "在 mock（Keep API 未授權：多半是 Cloud 專案尚未啟用 "
                "Google Keep API，或此帳戶為個人帳號不支援 Keep API）。"
            )
        else:
            keep_note = "僅同意 keep scope（診斷模式）。"
        return {
            "ok": True,
            "token_path": path,
            "mode": self.mode,
            "scope_mode": scope_mode,
            "scopes": scopes,
            "shared_with_calendar":
                self._calendar_creds_entry() is not None,
            "note": keep_note,
        }

        return str(tok.get("access_token") or "") or None

    def _access_token(self) -> str | None:
        tok = self.load_token()
        if not tok:
            return None
        exp = self._token_expiry_epoch(tok)
        if exp and exp - 60 > datetime.now(timezone.utc).timestamp():
            return str(tok["access_token"])
        refresh = str(tok.get("refresh_token") or "")
        creds = self._client_id_secret()
        if not (refresh and creds):
            return None
        cid, csec = creds
        try:
            resp = requests.post(
                OAUTH_TOKEN_URL,
                data={
                    "client_id": cid, "client_secret": csec,
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                },
                timeout=DEFAULT_TIMEOUT_S,
            )
            resp.raise_for_status()
            d = resp.json()
        except Exception:
            return None
        tok["access_token"] = d.get("access_token", "")
        if d.get("expires_in"):
            tok["expiry"] = (datetime.now(timezone.utc).timestamp()
                             + int(d.get("expires_in", 3600)))
        if d.get("scope"):
            tok["scope"] = d["scope"]
        try:
            self.save_token(tok)
        except Exception:
            pass
        return str(tok.get("access_token") or "") or None

    def has_keep_scope(self) -> bool:
        """Shared token 是否已取得 keep scope。"""
        tok = self.load_token() or {}
        scope = str(tok.get("scope") or "")
        if not scope:
            return False  # scope 未知 = 無法確認 → 視為未授權
        return "https://www.googleapis.com/auth/keep" in scope

    def _api(self, method: str, path: str,
             params: dict | None = None,
             body: dict | None = None) -> dict[str, Any]:
        if self.is_mock:
            return {"error": "mock mode has no live API"}
        token = self._access_token()
        if not token:
            return {"error": (
                "live 模式需要已授權的 token：先在 Cloud Console 啟用 "
                "Google Keep API、設定 KEEP_CLIENT_SECRET_PATH，執行 "
                "`python cli.py keep-auth` 完成一次性 OAuth 同意。")}
        if not self.has_keep_scope():
            return {"error": (
                "token 缺少 keep scope — 官方 Google Keep API 是 "
                "enterprise-only（個人帳戶 consent 會回 invalid_scope，"
                "無法授權）。keep 工具維持 mock；若你有 Google "
                "Workspace 帳戶，用該帳戶的 client secret 重跑 "
                "`python cli.py keep-auth` 即可。")}
        url = f"{API_BASE}{path}"
        try:
            resp = requests.request(
                method, url,
                headers={"Authorization": f"Bearer {token}"},
                params=params, json=body,
                timeout=DEFAULT_TIMEOUT_S,
            )
            resp.raise_for_status()
            if resp.status_code == 200 and not (resp.text or "").strip():
                return {}
            return resp.json()
        except Exception as e:
            return {"error": f"Keep API {method} {path} failed: "
                             f"{type(e).__name__}: {e}"[:300]}

    # ── live CRUD ───────────────────────────────────────────────
    def live_create(self, title: str, body_text: str) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if body_text.strip():
            body = {"text": {"text": body_text}}
        return self._api("POST", "/notes",
                         body={"title": title, "body": body})

    def live_list(self, query: str = "", max_results: int = 20
                  ) -> dict[str, Any]:
        params: dict[str, Any] = {"pageSize": 50}
        if (query or "").strip():
            params["filter"] = query.strip()
        return self._api("GET", "/notes", params=params)

    def live_get(self, note_id: str) -> dict[str, Any]:
        nid = note_id if note_id.startswith("notes/") else \
            f"notes/{note_id}"
        return self._api("GET", f"/{nid}")

    def live_delete(self, note_id: str) -> dict[str, Any]:
        nid = note_id if note_id.startswith("notes/") else \
            f"notes/{note_id}"
        return self._api("DELETE", f"/{nid}")

    # ── unified CRUD (mock/live dispatch) ───────────────────────
    def create_note(self, title: str, body_text: str,
                    confirm: Any = False,
                    category: str = "") -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            return {"error": "title 必填"}
        gate = self._gate_write("建立記事 (notes.create)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if self.mode == MODE_LOCAL:
            return self.local_create(title, body_text, category=category)
        if self.is_mock:
            return self.mock_create(title, body_text, category=category)
        out = self.live_create(title, body_text)
        if out.get("error"):
            return out
        rendered = self._render(out)
        if str(category or "").strip():
            rendered["note"] = (
                "分類為本地筆記本擴充欄位（官方 Keep API 無此欄位）— "
                "live 記事未保存分類。"
            )
        return rendered

    def update_note(self, note_id: str, title: str | None = None,
                    body: str | None = None, category: str | None = None,
                    confirm: Any = False) -> dict[str, Any]:
        nid = str(note_id or "").strip()
        if not nid:
            return {"error": "note_id 必填（notes/<id> 或純 id）"}
        gate = self._gate_write("修改記事 (notes.update)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if self.mode == MODE_LOCAL:
            out = self.local_update(nid, title=title, body=body,
                                    category=category)
            return out if out else {"error": f"找不到記事 {nid}",
                                    "mode": self.mode}
        if self.is_mock:
            out = self.mock_update(nid, title=title, body=body,
                                   category=category)
            return out if out else {"error": f"找不到記事 {nid}",
                                    "mode": self.mode}
        return {"error": (
            "官方 Google Keep API 沒有 update 方法 — live 模式不支援"
            "修改記事（KEEP_MODE=local 的自建筆記本才有）"
        ), "mode": self.mode}

    def list_notes(self, query: str = "", max_results: int = 20,
                   category: str = "") -> dict[str, Any]:
        if self.mode == MODE_LOCAL:
            notes = self.local_list(query, max_results, category=category)
            return {"count": len(notes), "notes": notes,
                    "mode": self.mode}
        if self.is_mock:
            q = (query or "").strip().lower()
            want_cat = (category or "").strip().lower()
            all_notes = self.mock_list("", max_results=50)
            notes = []
            for n in all_notes:
                if want_cat and (n.get("category") or "").lower() \
                        != want_cat:
                    continue
                if q and q not in (n.get("title") or "").lower() \
                        and q not in (n.get("text") or "").lower():
                    continue
                notes.append(n)
            return {"count": len(notes), "notes": notes,
                    "mode": self.mode}
        if (category or "").strip():
            return {"error": (
                "分類為本地筆記本功能（官方 Keep API 無此欄位）— "
                "live 模式不支援 category 過濾"), "mode": self.mode}
        out = self.live_list(query, max_results)
        if out.get("error"):
            return out
        notes = [self._render(n) for n in out.get("notes", [])]
        q = (query or "").strip().lower()
        if q:
            notes = [n for n in notes
                     if q in (n.get("title") or "").lower()
                     or q in (n.get("text") or "").lower()]
        return {"count": len(notes), "notes": notes, "mode": self.mode}

    def get_note(self, note_id: str) -> dict[str, Any]:
        nid = str(note_id or "").strip()
        if not nid:
            return {"error": "note_id 必填（notes/<id> 或純 id）"}
        if self.mode == MODE_LOCAL:
            out = self.local_get(nid)
            return out if out else {"error": f"找不到記事 {nid}",
                                    "mode": self.mode}
        if self.is_mock:
            out = self.mock_get(nid)
            return out if out else {"error": f"找不到記事 {nid}",
                                    "mode": self.mode}
        out = self.live_get(nid)
        if out.get("error"):
            return out
        return self._render(out)

    def delete_note(self, note_id: str,
                    confirm: Any = False) -> dict[str, Any]:
        nid = str(note_id or "").strip()
        if not nid:
            return {"error": "note_id 必填（notes/<id> 或純 id）"}
        gate = self._gate_write("刪除記事 (notes.delete)", confirm)
        if gate:
            return {"error": gate, "mode": self.mode}
        if self.mode == MODE_LOCAL:
            ok = self.local_delete(nid)
            return ({"ok": True, "deleted": nid, "mode": self.mode}
                    if ok else {"error": f"找不到記事 {nid}",
                                "mode": self.mode})
        if self.is_mock:
            ok = self.mock_delete(nid)
            return ({"ok": True, "deleted": nid, "mode": self.mode}
                    if ok else {"error": f"找不到記事 {nid}",
                                "mode": self.mode})
        out = self.live_delete(nid)
        if out.get("error"):
            return out
        return {"ok": True, "deleted": nid, "mode": self.mode}

    def status(self) -> dict[str, Any]:
        tok = self.load_token()
        out = {
            "mode": self.mode,
            "writes_enabled": self.writes_enabled,
            "token_ready": bool(tok),
            "token_path": self._token_path(),
            "client_secret_ready": bool(self._client_secret_path()),
            "mock_notes": (len(self._mock_notes)
                           if self.is_mock else None),
        }
        if self.mode == MODE_LOCAL:
            data = self._local_load()
            out["notes_path"] = self._local_store_path()
            out["notes_count"] = len(data.get("notes", {}))
            out["categories"] = sorted({
                str(n.get("category") or "").strip()
                for n in data.get("notes", {}).values()
                if str(n.get("category") or "").strip()
            })
        return out