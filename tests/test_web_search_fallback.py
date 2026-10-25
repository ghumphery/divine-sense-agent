"""v0.9.54.0 — Ollama web search + OpenRouter web-plugin fallback.

* ``OpenRouterWebSearch`` — the web plugin's ``url_citation``
  annotations parsed into ``WebResult`` (deduped); graceful ``[]``/
  ``None`` on failure (same contract as ``OllamaWebSearch``).
* ``WebSearchWithFallback`` — Ollama first; on exception OR empty
  results → OpenRouter fallback (when OPENROUTER_API_KEY is set);
  ``WEB_SEARCH_FALLBACK=off`` disables; ``fetch`` falls back to a
  best-effort direct HTTP GET + HTML strip (no OR fetch endpoint).
* ``Agent`` / ``WebSearchLocalHost`` construct via
  ``build_web_search()``.
* ``TestServerTools`` (v0.9.84.4) — OpenRouter SERVER TOOLS (beta):
  ``openrouter:web_fetch`` preferred for fetch (real tool-calling
  model, live-verified; bare direct-GET stays the degradation);
  ``openrouter:web_search`` opt-in via OPENROUTER_WEB_SEARCH_MODE=
  server (the deterministic legacy plugin stays the search primary —
  live probes showed the beta tool was not invoked by either model).
* ``TestWebFetchFixes`` (v0.9.84.3) — live E2E caught two web_fetch
  chain bugs: the handler read ``FetchedPage.text`` (AttributeError —
  the field is ``.content``) and a 200-with-null-fields Ollama body
  raised inside the FetchedPage construction OUTSIDE the try, which
  killed the chain INCLUDING the fallback (now coalesced + inside).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for _k in ("OLLAMA_API_KEY", "OPENROUTER_API_KEY",
               "WEB_SEARCH_FALLBACK", "OPENROUTER_WEB_MODEL",
               "OPENROUTER_WEB_SEARCH_MODE",
               "OPENROUTER_WEB_SEARCH_ENGINE",
               "OPENROUTER_WEB_FETCH_MODEL",
               "OPENROUTER_WEB_FETCH_ENGINE"):
        monkeypatch.delenv(_k, raising=False)
    yield


def _ollama_ok(results=None):
    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": results if results is not None else [
                {"title": "Ollama hit", "url": "https://a.example/",
                 "content": "content a"},
            ]}
    return _R()


def _or_ok(n=2):
    anns = []
    for i in range(n):
        anns.append({
            "type": "url_citation",
            "url_citation": {
                "title": f"OR hit {i}",
                "url": f"https://or{i}.example/",
                "content": f"or content {i} " * 20,
            },
        })

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {
                "content": "answer text",
                "annotations": anns,
            }}]}
    return _R()


def _wire(monkeypatch, *, ollama=None, or_post=None, or_get=None):
    import src.tools.web_search as ws

    def fake_post(url, **kw):
        if "ollama.com" in url:
            if isinstance(ollama, Exception):
                raise ollama
            return ollama
        if isinstance(or_post, Exception):
            raise or_post
        return or_post

    monkeypatch.setattr(ws.requests, "post", fake_post)
    if or_get is not None:
        monkeypatch.setattr(ws.requests, "get", lambda url, **kw: or_get)


class TestOpenRouterWebSearch:
    def test_annotations_parsed(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        _wire(monkeypatch, or_post=_or_ok(3))
        from src.tools.web_search import OpenRouterWebSearch

        out = OpenRouterWebSearch().search("q")
        assert len(out) == 3
        assert out[0].title == "OR hit 0"
        assert out[0].url == "https://or0.example/"

    def test_dedup_by_url(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        anns = {
            "data": [],
            "choices": [{"message": {"content": "x", "annotations": [
                {"type": "url_citation", "url_citation": {
                    "title": "a", "url": "https://same.example/",
                    "content": "one"}},
                {"type": "url_citation", "url_citation": {
                    "title": "b", "url": "https://same.example/",
                    "content": "two"}},
                {"type": "other", "url_citation": {
                    "title": "c", "url": "https://ignored.example/",
                    "content": "three"}},
            ]}}]}

        class _R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return anns

        _wire(monkeypatch, or_post=_R())
        from src.tools.web_search import OpenRouterWebSearch

        out = OpenRouterWebSearch().search("q")
        assert len(out) == 1
        assert out[0].title == "a"

    def test_no_key_returns_empty(self):
        from src.tools.web_search import OpenRouterWebSearch

        e = OpenRouterWebSearch()
        assert e.is_configured() is False
        assert e.search("q") == []
        assert e.fetch("https://x.example/") is None

    def test_http_error_graceful(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        _wire(monkeypatch, or_post=RuntimeError("down"))
        from src.tools.web_search import OpenRouterWebSearch

        assert OpenRouterWebSearch().search("q") == []


class TestChain:
    def test_ollama_success_no_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        called = {"or": 0}

        def fake_post(url, **kw):
            if "openrouter" in url:
                called["or"] += 1
                return _or_ok()
            return _ollama_ok()

        import src.tools.web_search as ws

        monkeypatch.setattr(ws.requests, "post", fake_post)
        from src.tools.web_search import WebSearchWithFallback

        w = WebSearchWithFallback()
        out = w.search("q")
        assert out[0].title == "Ollama hit"
        assert called["or"] == 0
        assert w.last_backend == "ollama"

    def test_ollama_failure_falls_back(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        _wire(monkeypatch, ollama=RuntimeError("ollama down"),
              or_post=_or_ok(2))
        from src.tools.web_search import WebSearchWithFallback

        w = WebSearchWithFallback()
        out = w.search("q")
        assert [r.url for r in out] == [
            "https://or0.example/", "https://or1.example/",
        ]
        assert w.last_backend == "openrouter"

    def test_ollama_empty_falls_back(self, monkeypatch):
        """An empty Ollama result set is also a failure to find — the
        fallback widens coverage."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        _wire(monkeypatch, ollama=_ollama_ok(results=[]), or_post=_or_ok(1))
        from src.tools.web_search import WebSearchWithFallback

        w = WebSearchWithFallback()
        out = w.search("q")
        assert out[0].title == "OR hit 0"
        assert w.last_backend == "openrouter"

    def test_both_fail_graceful(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        _wire(monkeypatch, ollama=RuntimeError("down"),
              or_post=RuntimeError("or down"))
        from src.tools.web_search import WebSearchWithFallback

        w = WebSearchWithFallback()
        assert w.search("q") == []

    def test_fallback_needs_or_key(self, monkeypatch):
        _wire(monkeypatch, ollama=RuntimeError("down"))
        from src.tools.web_search import WebSearchWithFallback

        w = WebSearchWithFallback()
        assert w.fallback is None
        assert w.search("q") == []

    def test_off_switch(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("WEB_SEARCH_FALLBACK", "off")
        _wire(monkeypatch, ollama=RuntimeError("down"),
              or_post=_or_ok())
        from src.tools.web_search import WebSearchWithFallback

        w = WebSearchWithFallback()
        assert w.fallback is None
        assert w.search("q") == []

    def test_is_configured_either_key(self, monkeypatch):
        from src.tools.web_search import WebSearchWithFallback

        assert WebSearchWithFallback().is_configured() is False
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        w = WebSearchWithFallback()
        assert w.is_configured() is True
        assert w.fallback is not None

    def test_fetch_falls_back_to_direct_get(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

        class _Get:
            status_code = 200
            headers = {"Content-Type": "text/html; charset=utf-8"}
            text = ("<html><head><title>測試頁</title></head>"
                    "<body><h1>hi</h1>"
                    "<a href='https://x.example/1'>x</a>"
                    "<script>bad()</script>"
                    "<p>內文段落</p></body></html>")

            def raise_for_status(self):
                pass

        import src.tools.web_search as ws

        def fake_post(url, **kw):
            if "ollama.com" in url:
                raise RuntimeError("ollama down")
            return _or_ok()

        monkeypatch.setattr(ws.requests, "post", fake_post)
        monkeypatch.setattr(ws.requests, "get",
                            lambda url, **kw: _Get())
        from src.tools.web_search import WebSearchWithFallback

        w = WebSearchWithFallback()
        page = w.fetch("https://x.example/")
        assert page is not None
        assert page.title == "測試頁"
        assert "內文段落" in page.content
        assert "bad()" not in page.content
        assert "https://x.example/1" in page.links
        assert w.last_backend == "openrouter"

    def test_build_web_search(self):
        from src.tools.web_search import (
            OllamaWebSearch,
            WebSearchWithFallback,
            build_web_search,
        )

        w = build_web_search()
        assert isinstance(w, OllamaWebSearch)
        assert isinstance(w, WebSearchWithFallback)
    def test_build_web_search(self):
        from src.tools.web_search import (
            OllamaWebSearch,
            WebSearchWithFallback,
            build_web_search,
        )

        w = build_web_search()
        assert isinstance(w, OllamaWebSearch)
        assert isinstance(w, WebSearchWithFallback)


class TestServerTools:
    """v0.9.84.4 — OpenRouter SERVER TOOLS (beta). Live probes
    2026-09-22: web_fetch executes reliably with a real tool-calling
    model → fetch prefers it; web_search was NOT invoked by either
    test model → the legacy plugin stays the search primary and the
    server tool is opt-in via OPENROUTER_WEB_SEARCH_MODE=server."""

    def _capture(self, monkeypatch, *, ollama=None, posts=None,
                 get=None):
        """posts = list of json responses served per openrouter POST
        (last one repeats); get = response for requests.get."""
        import src.tools.web_search as ws
        seen = []
        posts = list(posts or [])

        class _R:
            def __init__(self, data):
                self._d = data
                self.status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return self._d

        def fake_post(url, **kw):
            seen.append(kw.get("json") or {})
            if "ollama.com" in url:
                if isinstance(ollama, Exception):
                    raise ollama
                return ollama
            if isinstance(posts, Exception):
                raise posts
            if posts:
                data = posts.pop(0) if len(posts) > 1 else posts[0]
            else:
                data = {}
            return _R(data)

        monkeypatch.setattr(ws.requests, "post", fake_post)
        if get is not None:
            monkeypatch.setattr(ws.requests, "get",
                                lambda url, **kw: get)
        return seen

    @staticmethod
    def _resp(content="answer", annotations=None):
        return {"choices": [{"message": {
            "content": content, "annotations": annotations or [],
        }}]}

    def test_search_uses_server_tool_body(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("OPENROUTER_WEB_SEARCH_MODE", "server")
        anns = [{"type": "url_citation", "url_citation": {
            "title": "T", "url": "https://a.example/",
            "content": "excerpt"}}]
        seen = self._capture(monkeypatch, posts=[
            self._resp(annotations=anns),
            self._resp(annotations=anns),  # never reached
        ])
        from src.tools.web_search import OpenRouterWebSearch

        out = OpenRouterWebSearch().search("q", max_results=3)
        assert out[0].url == "https://a.example/"
        body = seen[0]
        assert body["tools"] == [{
            "type": "openrouter:web_search",
            "parameters": {"engine": "exa", "max_results": 3},
        }]
        assert len(seen) == 1  # annotations found → no plugin retry

    def test_search_default_mode_is_plugin(self, monkeypatch):
        """No wasted RTT: the beta server tool is NOT attempted unless
        the user opts in."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        anns = [{"type": "url_citation", "url_citation": {
            "title": "P", "url": "https://p.example/",
            "content": "c"}}]
        seen = self._capture(monkeypatch, posts=[
            self._resp(annotations=anns)])
        from src.tools.web_search import OpenRouterWebSearch

        out = OpenRouterWebSearch().search("q")
        assert out[0].title == "P"
        assert len(seen) == 1
        assert seen[0]["plugins"] == [{"id": "web", "max_results": 5}]
        assert "tools" not in seen[0]

    def test_search_plugin_retry_when_no_annotations(
        self, monkeypatch,
    ):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("OPENROUTER_WEB_SEARCH_MODE", "server")
        anns = [{"type": "url_citation", "url_citation": {
            "title": "P", "url": "https://p.example/",
            "content": "c"}}]
        seen = self._capture(monkeypatch, posts=[
            self._resp(content="no links here"),   # server tool: empty
            self._resp(annotations=anns),          # the plugin retry
        ])
        from src.tools.web_search import OpenRouterWebSearch

        out = OpenRouterWebSearch().search("q")
        assert out[0].title == "P"
        assert len(seen) == 2
        assert seen[1]["plugins"] == [{"id": "web", "max_results": 5}]

    def test_search_markdown_link_scan(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("OPENROUTER_WEB_SEARCH_MODE", "server")
        self._capture(monkeypatch, posts=[
            self._resp(
                content="- [Alpha](https://a.example/)\n"
                        "- [Beta](https://b.example/)"),
        ])
        from src.tools.web_search import OpenRouterWebSearch

        out = OpenRouterWebSearch().search("q")
        assert [r.url for r in out] == [
            "https://a.example/", "https://b.example/"]
        assert out[0].title == "Alpha"

    def test_fetch_server_tool_first(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        page_text = "x" * 80
        seen = self._capture(
            monkeypatch, posts=[self._resp(content=page_text)],
            get=AssertionError("direct GET must NOT run"))
        from src.tools.web_search import OpenRouterWebSearch

        w = OpenRouterWebSearch()
        page = w.fetch("https://x.example/article")
        assert page is not None and page.content == page_text
        body = seen[0]
        assert body["model"] == "openai/gpt-4o-mini"
        assert body["tools"] == [{
            "type": "openrouter:web_fetch",
            "parameters": {"max_content_tokens": 20000},
        }]
        assert "plugins" not in body

    def test_fetch_short_answer_falls_to_direct_get(
        self, monkeypatch,
    ):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")

        class _Get:
            status_code = 200
            headers = {"Content-Type": "text/html"}
            text = "<html><head><title>D</title></head><body>" + \
                "direct body " * 10 + "</body></html>"

            def raise_for_status(self):
                pass

        seen = self._capture(
            monkeypatch, posts=[self._resp(content="too short")],
            get=_Get())
        from src.tools.web_search import OpenRouterWebSearch

        page = OpenRouterWebSearch().fetch("https://x.example/a")
        assert page is not None and page.title == "D"
        assert len(seen) == 1  # direct GET is requests.get, not post

    def test_fetch_engine_and_model_overrides(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("OPENROUTER_WEB_FETCH_ENGINE", "exa")
        monkeypatch.setenv("OPENROUTER_WEB_FETCH_MODEL",
                           "openai/gpt-4o")
        seen = self._capture(monkeypatch, posts=[
            self._resp(content="y" * 60),
        ])
        from src.tools.web_search import OpenRouterWebSearch

        OpenRouterWebSearch().fetch("https://x.example/a")
        body = seen[0]
        assert body["model"] == "openai/gpt-4o"
        assert body["tools"][0]["parameters"] == {
            "max_content_tokens": 20000, "engine": "exa"}


class TestOllamaCooldown:
    """v0.9.85.0 — the 2026-09-22 live log paid a guaranteed 404 RTT on
    EVERY ollama /api/web_fetch while the endpoint was down. A 404 now
    puts the endpoint in cooldown: subsequent calls skip the dead leg
    instantly (the wrapper falls through to OpenRouter)."""

    @staticmethod
    def _http_error(status):
        resp = MagicMock(status_code=status)
        return requests.HTTPError(f"{status}", response=resp)

    @staticmethod
    def _ok(data):
        class _R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return data
        return _R()

    def _ws(self, monkeypatch, posts, post_log):
        import src.tools.web_search as ws

        def fake_post(url, **kw):
            post_log.append(url)
            if isinstance(posts, Exception):
                raise posts
            if posts is None:
                raise self._http_error(404)
            return posts

        monkeypatch.setattr(ws.requests, "post", fake_post)
        from src.tools.web_search import OllamaWebSearch

        return OllamaWebSearch(api_key="k")

    def test_404_sets_fetch_cooldown(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_WEB_ENDPOINT_COOLDOWN_S", "300")
        post_log: list[str] = []
        w = self._ws(monkeypatch, None, post_log)  # always 404
        assert w.fetch("https://x.example/a") is None  # 404 → cooldown
        assert w.fetch("https://x.example/a") is None  # skipped, no HTTP
        assert len(post_log) == 1  # only the FIRST call hit the wire
        monkeypatch.setenv("OLLAMA_WEB_ENDPOINT_COOLDOWN_S", "0")
        w2 = self._ws(monkeypatch, None, post_log)  # 0=off → no gate
        assert w2.fetch("https://x.example/a") is None
        assert getattr(w2, "_fetch_down_until", 0.0) == 0.0

    def test_search_404_cooldown_independent(self, monkeypatch):
        import src.tools.web_search as ws

        post_log: list[str] = []

        def fake_post(url, **kw):
            post_log.append(url)
            if "web_search" in url:
                raise self._http_error(404)
            return self._ok({"title": "t", "content": "c" * 50,
                             "links": []})

        monkeypatch.setattr(ws.requests, "post", fake_post)
        monkeypatch.setenv("OLLAMA_WEB_ENDPOINT_COOLDOWN_S", "300")
        from src.tools.web_search import OllamaWebSearch

        w = OllamaWebSearch(api_key="k")
        assert w.search("q") == []          # search 404 → cooldown
        assert w.search("q") == []          # skipped, no HTTP
        assert len(post_log) == 1
        assert w.fetch("https://x.example/") is not None  # other leg OK

    def test_non_404_no_cooldown(self, monkeypatch):
        post_log: list[str] = []
        w = self._ws(monkeypatch, self._http_error(500), post_log)
        assert w.fetch("https://x.example/a") is None
        assert w.fetch("https://x.example/a") is None
        assert len(post_log) == 2  # every call still tries (5xx)

    def test_success_clears_cooldown(self, monkeypatch):
        import src.tools.web_search as ws

        post_log: list[str] = []

        def fake_post(url, **kw):
            post_log.append(url)
            if len(post_log) == 1:
                raise self._http_error(404)
            return self._ok({"title": "t", "content": "c" * 50,
                             "links": []})

        monkeypatch.setattr(ws.requests, "post", fake_post)
        monkeypatch.setenv("OLLAMA_WEB_ENDPOINT_COOLDOWN_S", "300")
        from src.tools.web_search import OllamaWebSearch

        w = OllamaWebSearch(api_key="k")
        assert w.fetch("https://x.example/a") is None   # 404 + cooldown
        w._fetch_down_until = 0.0                        # TTL elapsed
        page = w.fetch("https://x.example/a")
        assert page is not None and page.content == "c" * 50
        assert w._fetch_down_until == 0.0                # success cleared


class TestWebFetchBatch:
    """v0.9.85.0 — web_fetch_batch: ONE tool call fetches N URLs in
    parallel (4 workers) with per-URL failure containment."""

    def _host(self):
        from src.mcp_client.host import WebSearchLocalHost

        h = WebSearchLocalHost.__new__(WebSearchLocalHost)
        h._web = MagicMock()
        return h

    def test_advertised(self):
        from src.mcp_client.host import WebSearchLocalHost

        names = [t.name for t in WebSearchLocalHost().list_tools()]
        assert "web_fetch_batch" in names

    def test_batch_parallel_results(self):
        h = self._host()
        h._fetch = lambda u: {"url": u, "content": f"body-{u}"}
        out = h._fetch_batch(
            {"urls": ["https://a.example/", "https://b.example/"]})
        assert out["count"] == 2 and out["ok_count"] == 2
        assert out["results"][0]["content"] == "body-https://a.example/"

    def test_batch_failure_containment(self):
        h = self._host()

        def _f(u):
            if "bad" in u:
                raise RuntimeError("boom")
            return {"url": u, "content": "ok"}

        h._fetch = _f
        out = h._fetch_batch(
            {"urls": ["https://ok.example/", "https://bad.example/"]})
        assert out["ok_count"] == 1 and out["failed_count"] == 1
        assert out["results"][1]["error"]

    def test_batch_cap_and_empty(self, monkeypatch):
        monkeypatch.setenv("QDRANT_WEB_FETCH_BATCH_MAX", "2")
        h = self._host()
        seen: list[str] = []
        h._fetch = lambda u: (seen.append(u),
                              {"url": u, "content": "ok"})[1]
        out = h._fetch_batch({"urls": [f"https://{i}.example/"
                                       for i in range(5)]})
        assert out["count"] == 2 and len(seen) == 2
        assert h._fetch_batch({}) == {
            "error": "urls must be a non-empty list of URLs"}


class TestWebFetchFixes:
    """v0.9.84.3 — live E2E 2026-09-22 10:49 web_fetch chain bugs:

    (a) the host handler read ``FetchedPage.text`` — no such attribute
    (the v0.2.8 contract is ``.content``) → AttributeError on EVERY
    successful fallback page (the only page shape that ever reached
    the handler, since ollama 404'd);

    (b) Ollama's 200-with-null-fields body (``"links": null``) raised
    ``TypeError: 'NoneType' object is not iterable`` inside the
    FetchedPage construction — which sat OUTSIDE the try, so the
    exception escaped ``fetch()`` and the OpenRouter fallback leg
    never ran.
    """

    def _host(self, web):
        from src.mcp_client.host import WebSearchLocalHost

        h = WebSearchLocalHost.__new__(WebSearchLocalHost)
        h._web = web
        return h

    def test_fetch_uses_content_attribute(self):
        from src.tools.web_search import FetchedPage

        class _Web:
            def fetch(self, url):
                return FetchedPage(
                    title="T", content="第一段\n\n\n\n第二段", links=[])

        out = self._host(_Web())._fetch("https://x.example/a")
        assert out["content"] == "第一段\n\n第二段"
        assert out["title"] == "T"

    def test_fetch_defensive_against_wrong_shaped_page(self):
        class _Weird:  # neither .text nor .content
            def fetch(self, url):
                return _Weird()

        out = self._host(_Weird())._fetch("https://x.example")
        assert out == {"url": "https://x.example", "title": "",
                       "content": ""}

    def test_ollama_null_fields_coalesced(self, monkeypatch):
        from src.tools.web_search import OllamaWebSearch

        w = OllamaWebSearch(api_key="k")
        monkeypatch.setattr(
            w, "_post",
            lambda *a, **k: {"title": None, "content": None,
                             "links": None},
        )
        page = w.fetch("https://x.example/a")
        assert page is not None
        assert page.title == "" and page.content == "" and page.links == []

    def test_construction_error_degrades_to_none(self, monkeypatch):
        """Any construction-time failure must degrade to ``None`` (→
        the caller's fallback runs) — never propagate out of fetch."""

        from src.tools.web_search import WebSearchWithFallback

        w = WebSearchWithFallback(api_key="k")
        w.fallback = MagicMock()
        w.fallback.is_configured.return_value = True
        w.fallback.fetch.return_value = None
        monkeypatch.setattr(
            w, "_post",
            lambda *a, **k: (_ for _ in ()).throw(
                TypeError("'NoneType' object is not iterable")),
        )
        page = w.fetch("https://x.example/a")
        assert page is None  # both legs failed → None (handled upstream)
        w.fallback.fetch.assert_called_once()  # the fallback DID run