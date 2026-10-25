"""Ollama Web Search / Web Fetch client (v0.2.8+).

Lets the RAG agent augment Qdrant-retrieved context with up-to-the-
minute web results, reducing hallucinations when the local knowledge
base is missing or stale.

Two endpoints are wrapped:

* ``/api/web_search`` — query → list of {title, url, content}.
* ``/api/web_fetch``  — single URL → {title, content, links}.

Both require a valid ``OLLAMA_API_KEY`` (free Ollama.com account).
Results are returned as plain Python ``list[dict]`` / ``dict`` and
are JSON-serialisable, so they can be slotted straight into the
Agent's prompt context.

The functions are *opt-in*: the Agent only calls them when the user
opt flag (``--web-search`` on ``ask`` / ``/web`` on chat REPL) is set,
to avoid a network round-trip on every turn.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Iterable

import requests


WEB_SEARCH_ENDPOINT = "https://ollama.com/api/web_search"
WEB_FETCH_ENDPOINT = "https://ollama.com/api/web_fetch"

# Cap individual `content` snippets so they don't blow up the LLM's
# context window when combined with many hits or long documents.
_SNIPPET_CHARS = 800
_FETCH_CHARS = 4000


@dataclass(slots=True)
class WebResult:
    """One row from ``/api/web_search``."""
    title: str
    url: str
    content: str

    def as_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "content": self.content}

    def to_prompt(self) -> str:
        """Render as a Markdown snippet suitable for LLM context."""
        snip = self.content
        if len(snip) > _SNIPPET_CHARS:
            snip = snip[: _SNIPPET_CHARS - 1] + "…"
        return f"- **{self.title}**\n  {self.url}\n  {snip}"


@dataclass(slots=True)
class FetchedPage:
    """One row from ``/api/web_fetch``."""
    title: str
    content: str
    links: list[str]

    def to_prompt(self) -> str:
        body = self.content
        if len(body) > _FETCH_CHARS:
            body = body[: _FETCH_CHARS - 1] + "…"
        head = f"### {self.title}\n" if self.title else ""
        links = ("\n- " + "\n- ".join(self.links[:10])) if self.links else ""
        return f"{head}{body}{links}"


class OllamaWebSearch:
    """Thin client over Ollama's hosted web search / fetch APIs.

    Auth is via the ``OLLAMA_API_KEY`` env var (the same key used by
    the chat / vision providers). When the key is missing, the
    instance still constructs but every call returns ``[]`` / empty
    ``FetchedPage`` instead of raising — callers can detect "web search
    not configured" via :py:meth:`is_configured`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get(
            "OLLAMA_API_KEY", "",
        )
        self.timeout = timeout

    def is_configured(self) -> bool:
        """Return True iff an API key is set."""
        return bool(self.api_key)

    # ── low-level HTTP ──────────────────────────────────────
    def _post(self, url: str, payload: dict) -> dict:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ── public API ──────────────────────────────────────────
    def search(
        self,
        query: str,
        max_results: int = 5,
    ) -> list[WebResult]:
        """Run a web search. Returns ``[]`` on auth failure / no key.

        ``max_results`` is clamped to ``[1, 10]`` per Ollama docs.
        """
        if not self.is_configured():
            return []
        # v0.9.85.0 — ollama endpoint cooldown: skip the dead leg for a
        # TTL after a 404 (the live box paid a guaranteed 404 RTT on
        # EVERY call while the endpoint was down).
        if time.monotonic() < getattr(self, "_search_down_until", 0.0):
            print("  [WebSearch] ollama search endpoint in cooldown "
                  "— skipping")
            return []
        n = max(1, min(10, max_results))
        try:
            data = self._post(
                WEB_SEARCH_ENDPOINT,
                {"query": query, "max_results": n},
            )
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code",
                             None)
            if status == 404 and _ollama_cooldown_s():
                self._search_down_until = (
                    time.monotonic() + _ollama_cooldown_s()
                )
                print(f"  [WebSearch] ollama search endpoint 404 — "
                      f"cooldown {_ollama_cooldown_s()}s")
            print(f"  [WebSearch] error: {type(e).__name__}: {e}")
            return []
        except Exception as e:
            print(f"  [WebSearch] error: {type(e).__name__}: {e}")
            return []
        self._search_down_until = 0.0
        return [
            WebResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
            )
            for r in data.get("results", [])
        ]

    def fetch(self, url: str) -> FetchedPage | None:
        """Fetch a single URL. Returns ``None`` on auth failure / no key."""
        if not self.is_configured():
            return None
        # v0.9.85.0 — endpoint cooldown (see search()).
        if time.monotonic() < getattr(self, "_fetch_down_until", 0.0):
            print("  [WebFetch] ollama fetch endpoint in cooldown "
                  "— skipping")
            return None
        try:
            data = self._post(WEB_FETCH_ENDPOINT, {"url": url})
            self._fetch_down_until = 0.0
            # v0.9.84.3 — the endpoint may return 200 with null fields
            # ("links": null → list(None) TypeError escaped the try and
            # killed the whole chain INCLUDING the fallback); coalesce
            # the fields and keep construction inside the try.
            return FetchedPage(
                title=str(data.get("title") or ""),
                content=str(data.get("content") or ""),
                links=list(data.get("links") or []),
            )
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code",
                             None)
            if status == 404 and _ollama_cooldown_s():
                self._fetch_down_until = (
                    time.monotonic() + _ollama_cooldown_s()
                )
                print(f"  [WebFetch] ollama fetch endpoint 404 — "
                      f"cooldown {_ollama_cooldown_s()}s")
            print(f"  [WebFetch] error: {type(e).__name__}: {e}")
            return None
        except Exception as e:
            print(f"  [WebFetch] error: {type(e).__name__}: {e}")
            return None

    # ── prompt helpers ──────────────────────────────────────
    def results_to_context(self, results: Iterable[WebResult]) -> str:
        """Render ``[WebResult, ...]`` as a Markdown block for LLM ctx."""
        results = list(results)
        if not results:
            return ""
        head = f"## Web Search Results ({len(results)})"
        body = "\n\n".join(r.to_prompt() for r in results)
        return f"{head}\n\n{body}"


# ─── v0.9.54.0 — OpenRouter web search fallback ─────────────────
#
# OpenRouter exposes web search as a CHAT-COMPLETIONS PLUGIN (no
# dedicated search endpoint): ``plugins: [{"id": "web",
# "max_results": N}]`` runs an Exa-backed search, augments the prompt
# and returns the hits as ``url_citation`` annotations (title / url /
# ~2-4k-char content excerpt). There is NO web-fetch endpoint —
# ``fetch()`` degrades to a best-effort plain HTTP GET with a
# dependency-free HTML strip.
#
# Live-verified 2026-09-14 with ``meta-llama/llama-3.1-8b-instruct`` +
# web plugin: 200, 3 url_citation annotations (Qdrant GitHub releases
# etc.), ~$0.007 (Exa auto) + negligible model tokens. The model's
# own answer is DISCARDED — only the citations are consumed, so the
# cheapest working model is fine (override with
# ``OPENROUTER_WEB_MODEL``).

_OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_WEB_MODEL = "meta-llama/llama-3.1-8b-instruct"
_DIRECT_FETCH_CHARS = 40000


def _fallback_enabled() -> bool:
    raw = (os.environ.get("WEB_SEARCH_FALLBACK", "") or "").strip().lower()
    return raw not in ("off", "0", "false", "no")


# v0.9.85.0 — Ollama endpoint cooldown: a 404 from ollama.com means the
# endpoint is down (the 2026-09-22 live log paid a guaranteed 404 RTT
# on EVERY web_fetch); skip the dead leg for a TTL instead.
def _ollama_cooldown_s() -> int:
    raw = (os.environ.get("OLLAMA_WEB_ENDPOINT_COOLDOWN_S", "") or "").strip()
    if not raw:
        return 300
    try:
        return max(0, min(3600, int(raw)))
    except ValueError:
        return 300


# v0.9.84.4 — SERVER TOOLS (beta): https://openrouter.ai/docs/guides/
# features/server-tools/web-search + /web-fetch. Live probes: web_fetch
# executes reliably with a real tool-calling model (preferred for
# fetch); web_search is model-driven and was NOT invoked by either test
# model → the legacy plugin stays the search primary (server mode is
# opt-in via OPENROUTER_WEB_SEARCH_MODE=server).
_SEARCH_COLLECTOR_PROMPT = (
    "You are a search-result collector. Use the web_search tool once "
    "for the user's query and reply with ONLY a numbered list of the "
    "results in the form `- [title](url)` — no commentary, no prose."
)
_FETCH_EXTRACT_PROMPT = (
    "Fetch the URL and reply with ONLY the raw page text verbatim — "
    "no commentary, no markdown reformatting.\n\nURL: {url}"
)


def _annotations_to_results(annotations, n: int) -> list[WebResult]:
    """url_citation annotations (server tools AND the legacy plugin
    emit the same shape) → deduped ``WebResult`` list."""
    out: list[WebResult] = []
    seen: set[str] = set()
    for a in annotations or []:
        if not isinstance(a, dict) or a.get("type") != "url_citation":
            continue
        c = a.get("url_citation") or {}
        url = str(c.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(WebResult(
            title=str(c.get("title") or ""),
            url=url,
            content=str(c.get("content") or ""),
        ))
    return out[:n]


def _links_from_text(text: str, n: int) -> list[WebResult]:
    """Markdown-link scan fallback (``- [title](url)`` lists) for
    server-tool answers the provider did not annotate."""
    out: list[WebResult] = []
    seen: set[str] = set()
    for m in re.finditer(r"\[([^\]\n]{0,200})\]\((https?://[^)\s]+)\)",
                         text or ""):
        url = m.group(2)
        if url in seen:
            continue
        seen.add(url)
        out.append(WebResult(
            title=m.group(1).strip(),
            url=url,
            content="",
        ))
        if len(out) >= n:
            break
    return out


class OpenRouterWebSearch:
    """OpenRouter web tools client (the Ollama fallback). v0.9.84.4 —
    ``search`` runs the deterministic legacy web plugin (the beta
    ``openrouter:web_search`` server tool is opt-in via
    ``OPENROUTER_WEB_SEARCH_MODE=server``) and ``fetch`` prefers the
    beta ``openrouter:web_fetch`` server tool (real tool-calling model
    required; bare direct-GET remains the final degradation).

    Same graceful contract as ``OllamaWebSearch``: ``search`` returns
    ``[]`` and ``fetch`` returns ``None`` on any failure (auth missing,
    HTTP error, parse error) — callers never need try/except.
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 90.0,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get(
            "OPENROUTER_API_KEY", "",
        )
        self.timeout = timeout
        self.model = os.environ.get(
            "OPENROUTER_WEB_MODEL", _DEFAULT_WEB_MODEL,
        )
        # v0.9.84.4 — OpenRouter SERVER TOOLS (beta). Live probes
        # 2026-09-22: web_fetch executes reliably with a real
        # tool-calling model (gpt-4o-mini; llama-8b text-echoes the
        # call) → fetch prefers it; web_search's model never emitted
        # the call (beta flakiness) → the deterministic legacy web
        # plugin stays the search primary and the server tool is
        # opt-in via OPENROUTER_WEB_SEARCH_MODE=server.
        self.search_mode = os.environ.get(
            "OPENROUTER_WEB_SEARCH_MODE", "plugin",
        )
        self.search_engine = os.environ.get(
            "OPENROUTER_WEB_SEARCH_ENGINE", "exa",
        )
        self.fetch_model = os.environ.get(
            "OPENROUTER_WEB_FETCH_MODEL", "openai/gpt-4o-mini",
        )
        self.fetch_engine = os.environ.get(
            "OPENROUTER_WEB_FETCH_ENGINE", "",
        )

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        query: str,
        max_results: int = 5,
    ) -> list[WebResult]:
        """Run the web search. v0.9.84.4 — the deterministic legacy
        ``plugins: [{"id": "web"}]`` request stays PRIMARY (live probes
        showed the beta ``openrouter:web_search`` server tool is not
        reliably invoked by either test model); the server tool runs
        first ONLY when ``OPENROUTER_WEB_SEARCH_MODE=server`` (its
        annotations parse exactly like the plugin's, then a markdown
        link scan, then the plugin retry). Empty list on any failure
        (same graceful contract)."""
        if not self.is_configured():
            return []
        n = max(1, min(10, max_results))
        if self.search_mode == "server":
            out = self._search_server_tool(query, n)
            if out:
                return out
        return self._search_plugin(query, n)

    def _search_server_tool(self, query: str, n: int) -> list[WebResult]:
        """The beta ``openrouter:web_search`` server tool — executed
        server-side; results come back as ``url_citation`` annotations
        (or embedded in the answer text when the provider does not
        normalize them)."""
        try:
            resp = requests.post(
                _OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "tools": [{
                        "type": "openrouter:web_search",
                        "parameters": {
                            "engine": self.search_engine,
                            "max_results": n,
                        },
                    }],
                    "messages": [
                        {"role": "system",
                         "content": _SEARCH_COLLECTOR_PROMPT},
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": 1000,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            j = resp.json()
        except Exception as e:
            print(f"  [WebSearch/or] server-tool error: "
                  f"{type(e).__name__}: {e}")
            return []
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        out = _annotations_to_results(msg.get("annotations"), n)
        if out:
            return out
        # No annotations — scan the answer for markdown links
        # (the collector prompt makes the model list - [title](url)).
        return _links_from_text(str(msg.get("content") or ""), n)

    def _search_plugin(self, query: str, n: int) -> list[WebResult]:
        """Legacy ``plugins: [{"id": "web"}]`` request (deprecated by
        OpenRouter but still accepted — kept as the deterministic
        second attempt)."""
        try:
            resp = requests.post(
                _OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "plugins": [{"id": "web", "max_results": n}],
                    "messages": [{"role": "user", "content": query}],
                    "max_tokens": 300,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            j = resp.json()
        except Exception as e:
            print(f"  [WebSearch/or] error: {type(e).__name__}: {e}")
            return []
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        return _annotations_to_results(msg.get("annotations"), n)

    def fetch(self, url: str) -> FetchedPage | None:
        """Best-effort fetch. v0.9.84.4 — the ``openrouter:web_fetch``
        SERVER TOOL first (engine ``openrouter`` = FREE, handles JS
        pages; the bare direct-GET remains the final degradation).
        Returns ``None`` on any failure."""
        page = self._fetch_server_tool(url)
        if page is not None:
            return page
        return self._fetch_direct(url)

    def _fetch_server_tool(self, url: str) -> FetchedPage | None:
        """The beta ``openrouter:web_fetch`` server tool — OpenRouter
        fetches and extracts server-side; the model echoes the raw
        text. Live-verified 2026-09-22 (engine auto/exa + the
        max_content_tokens cap accepted; ``engine: openrouter`` is
        rejected 400 → the engine key is sent ONLY when configured)."""
        parameters: dict = {"max_content_tokens": 20000}
        if self.fetch_engine:
            parameters["engine"] = self.fetch_engine
        try:
            resp = requests.post(
                _OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.fetch_model,
                    "tools": [{
                        "type": "openrouter:web_fetch",
                        "parameters": parameters,
                    }],
                    "messages": [{
                        "role": "user",
                        "content": _FETCH_EXTRACT_PROMPT.format(url=url),
                    }],
                    "max_tokens": 8000,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            j = resp.json()
        except Exception as e:
            print(f"  [WebFetch/or] server-tool error: "
                  f"{type(e).__name__}: {e}")
            return None
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        content = str(msg.get("content") or "").strip()
        if len(content) < 40:  # model didn't carry the page back
            return None
        return FetchedPage(title="", content=content, links=[])

    def _fetch_direct(self, url: str) -> FetchedPage | None:
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; divine-sense-agent)"
                    ),
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if ctype and not (
                ctype.startswith("text/") or "json" in ctype
                or "xml" in ctype
            ):
                return None
            raw = resp.text[:_DIRECT_FETCH_CHARS]
        except Exception as e:
            print(f"  [WebFetch/or] error: {type(e).__name__}: {e}")
            return None
        title, text, links = _strip_html(raw)
        if not text.strip():
            return None
        return FetchedPage(title=title, content=text, links=links)


def _strip_html(raw: str) -> tuple[str, str, list[str]]:
    """Dependency-free HTML → (title, text, links)."""
    import html as _html
    import re

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", raw,
                  re.IGNORECASE | re.DOTALL)
    if m:
        title = _html.unescape(m.group(1)).strip()[:200]
    body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>",
                  " ", raw)
    links: list[str] = []
    seen: set[str] = set()
    for href in re.findall(r'href=["\']([^"\']+)["\']', body,
                           re.IGNORECASE):
        if href.startswith(("http://", "https://")) and href not in seen:
            seen.add(href)
            links.append(href)
    text = re.sub(r"<[^>]+>", " ", body)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = text.strip()
    return title, text, links[:10]


class WebSearchWithFallback(OllamaWebSearch):
    """Ollama web search FIRST, OpenRouter web tools as the fallback
    (v0.9.54.0; v0.9.84.4 = OpenRouter server tools). Same public
    contract as ``OllamaWebSearch``:

    * ``search()`` — try Ollama; on exception OR empty results AND an
      OPENROUTER_API_KEY, retry via OpenRouter (deterministic legacy
      web plugin; the beta ``openrouter:web_search`` server tool runs
      first only with ``OPENROUTER_WEB_SEARCH_MODE=server``; an empty
      Ollama result set is also a failure to find — the fallback
      widens coverage; it costs ~$0.007/request).
    * ``fetch()`` — try Ollama's ``web_fetch``; on ``None`` fall back
      to OpenRouter's ``openrouter:web_fetch`` server tool (real
      tool-calling model, live-verified), then the bare direct-GET.
    * ``is_configured()`` — True iff EITHER key is set.

    ``WEB_SEARCH_FALLBACK=off`` disables the fallback (byte-identical
    pre-0.9.54.0 behaviour). ``last_backend`` records who served the
    last call ("ollama" / "openrouter") for diagnostics.
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        super().__init__(api_key=api_key, timeout=timeout)
        self.fallback: OpenRouterWebSearch | None = None
        self.last_backend: str | None = None
        if _fallback_enabled() and os.environ.get(
            "OPENROUTER_API_KEY", "",
        ):
            self.fallback = OpenRouterWebSearch(timeout=max(timeout, 60.0))

    def is_configured(self) -> bool:
        return super().is_configured() or (
            self.fallback is not None and self.fallback.is_configured()
        )

    def search(
        self,
        query: str,
        max_results: int = 5,
    ) -> list[WebResult]:
        primary = super().search(query=query, max_results=max_results)
        if primary:
            self.last_backend = "ollama"
            return primary
        if self.fallback is None or not self.fallback.is_configured():
            return primary
        print("  [WebSearch] Ollama empty/failed — falling back to "
              "OpenRouter web plugin")
        fb = self.fallback.search(query=query, max_results=max_results)
        if fb:
            self.last_backend = "openrouter"
            return fb
        return primary

    def fetch(self, url: str) -> FetchedPage | None:
        page = super().fetch(url=url)
        if page is not None:
            self.last_backend = "ollama"
            return page
        if self.fallback is None or not self.fallback.is_configured():
            return page
        print("  [WebFetch] Ollama fetch failed — falling back to "
              "direct fetch")
        fb = self.fallback.fetch(url=url)
        if fb is not None:
            self.last_backend = "openrouter"
        return fb


def build_web_search() -> OllamaWebSearch:
    """Construct the configured web-search client: Ollama first with
    the OpenRouter web-plugin fallback when ``OPENROUTER_API_KEY`` is
    set (and ``WEB_SEARCH_FALLBACK`` is not off). Used by the Agent
    and ``WebSearchLocalHost`` (v0.9.54.0+)."""
    return WebSearchWithFallback()


def demo() -> None:
    """Tiny smoke: print the first result for a known query."""
    ws = OllamaWebSearch()
    if not ws.is_configured():
        print("OLLAMA_API_KEY not set; nothing to demo.")
        return
    out = ws.search("qdrant vector database", max_results=2)
    print(ws.results_to_context(out))


if __name__ == "__main__":
    demo()