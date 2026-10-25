"""CredentialStore — central account/credential sets (v0.9.39.0+).

One JSON store (``<QDRANT_RAG_HOME>/credentials.json`` — atomic
writes, chmod 600 best-effort) shared by EVERY service that needs an
account. Managed through THREE interfaces:

* **MCP tools** — ``CredLocalHost`` (server ``cred-local``):
  cred_add / cred_list / cred_get / cred_remove / cred_resolve.
* **GUI** — the 🔑 Credentials tab (``gui/api.py`` bridge methods).
* **CLI** — ``python cli.py creds {list,show,add,remove,test}``.

Password handling (mirrors the ONVIF favorites design):

* ``password_env`` — RECOMMENDED: the JSON stores only the env var
  NAME; the secret lives in the environment (``resolve`` reads it).
* ``password_b64`` — base64-OBSCURED plaintext (NOT encryption);
  for when an env var is impractical.

``resolve`` implements the same env-wins semantics for any entry,
and every list/show output is MASKED — the plain secret never
leaves :class:`CredentialStore`.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["CredentialStore", "mask_username", "mask_password"]


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


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class CredentialStore:
    """Central named credential sets with env-first resolution."""

    def __init__(self, path: str | Path | None = None):
        if path:
            self.path = Path(path)
        else:
            home = os.environ.get(
                "QDRANT_RAG_HOME",
                str(Path.home() / ".qdrant-rag-agent"),
            )
            self.path = Path(home) / "credentials.json"
        self._lock = threading.Lock()

    # ─── persistence ─────────────────────────────────────────────
    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(
                    data.get("credentials"), list):
                return data
        except Exception:
            pass
        return {"version": 1, "credentials": []}

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

    # ─── CRUD ────────────────────────────────────────────────────
    def add(
        self,
        name: str,
        username: str = "",
        password: str = "",
        password_env: str = "",
        service: str = "generic",
        notes: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            return {"error": "'name' is required"}
        if not str(password or "").strip() and not str(
                password_env or "").strip():
            return {
                "error": (
                    "provide 'password' or 'password_env' — a "
                    "credential set without a secret is pointless"
                ),
            }
        with self._lock:
            data = self.load()
            entries = data.setdefault("credentials", [])
            for e in entries:
                if e.get("name") == name and not overwrite:
                    return {
                        "error": (
                            f"credential set '{name}' already exists — "
                            "pass overwrite=true to replace it"
                        ),
                    }
            entry = {
                "name": name,
                "username": str(username or ""),
                "password_b64": (
                    base64.b64encode(
                        str(password or "").encode("utf-8"),
                    ).decode("ascii")
                    if password else ""
                ),
                "password_env": str(password_env or "").strip(),
                "service": str(service or "generic"),
                "notes": str(notes or ""),
                "created_at": _iso_now(),
                "updated_at": _iso_now(),
            }
            entries[:] = [e for e in entries if e.get("name") != name]
            entries.append(entry)
            self.save(data)
        return {
            "ok": True,
            "name": name,
            "path": self.path.as_posix(),
            "source": "env" if entry["password_env"] else "b64",
        }

    def remove(self, name: str) -> bool:
        name = str(name or "").strip()
        with self._lock:
            data = self.load()
            entries = data.get("credentials", [])
            kept = [e for e in entries if e.get("name") != name]
            if len(kept) == len(entries):
                return False
            data["credentials"] = kept
            self.save(data)
        return True

    def get(self, name: str) -> dict[str, Any] | None:
        name = str(name or "").strip()
        for e in self.load().get("credentials", []):
            if e.get("name") == name:
                return e
        return None

    def list(self, service: str | None = None,
             masked: bool = True) -> list[dict[str, Any]]:
        entries = self.load().get("credentials", [])
        if service:
            entries = [
                e for e in entries
                if str(e.get("service") or "") == str(service)
            ]
        if masked:
            return [self.mask_entry(e) for e in entries]
        return entries

    @staticmethod
    def mask_entry(entry: dict) -> dict:
        source = "env" if entry.get("password_env") else (
            "b64" if entry.get("password_b64") else "none"
        )
        return {
            "name": entry.get("name"),
            "username_masked": mask_username(
                str(entry.get("username") or "")
            ),
            "credential_source": source,
            "credential_masked": (
                f"env:{entry.get('password_env')}" if source == "env"
                else "******** (b64)" if source == "b64"
                else "(none)"
            ),
            "service": entry.get("service"),
            "notes": entry.get("notes"),
            "created_at": entry.get("created_at"),
            "updated_at": entry.get("updated_at"),
        }

    # ─── resolution (service consumption) ────────────────────────
    def resolve(self, name: str) -> dict[str, Any]:
        """Resolve one credential set → {username, password, source}
        or {error}. ``password_env`` wins over ``password_b64``; a
        missing env var is an explicit error."""
        entry = self.get(name)
        if not entry:
            return {
                "error": (
                    f"credential set '{name}' not found — call "
                    "cred_list to see available names"
                ),
            }
        username = str(entry.get("username") or "")
        env_name = str(entry.get("password_env") or "").strip()
        if env_name:
            val = os.environ.get(env_name)
            if not val:
                return {
                    "error": (
                        f"credential password env var '{env_name}' "
                        "is not set (set it in .env or the environment)"
                    ),
                }
            return {
                "username": username,
                "password": str(val),
                "source": "env",
                "env_var": env_name,
            }
        b64 = str(entry.get("password_b64") or "").strip()
        if b64:
            try:
                pw = base64.b64decode(b64).decode("utf-8")
            except Exception as e:
                return {"error": f"corrupt password_b64: {e!r}"}
            return {"username": username, "password": pw, "source": "b64"}
        return {
            "error": f"credential set '{name}' has no secret configured",
        }