# -*- coding: utf-8 -*-
"""Tests: list-parameter coercion (v0.9.110.1).

The 2026-10-05 16:48 incident: the LLM passed
``remove_documents(filenames='[\\\"a.md\\\", ...]')`` (a JSON-encoded
array string) and even bare single-name strings; the handler iterated
the string's CHARACTERS -> missing=[\"[\",\"\\\\\",\"2\",...] and
nothing was removed. ``_as_str_list`` normalises all shapes; the
three list-param handlers (``remove_documents`` / ``web_fetch_batch``
/ ``stock_get_quotes``) route through it.
"""
import json
import unittest
from unittest import mock

from src.mcp_client.host import (
    QdrantLocalHost, WebSearchLocalHost, _as_str_list,
)


class TestAsStringList(unittest.TestCase):
    def test_none_and_empty(self):
        self.assertEqual(_as_str_list(None, "x"), [])
        self.assertEqual(_as_str_list("", "x"), [])

    def test_proper_list(self):
        self.assertEqual(
            _as_str_list(["a.md", " b.md "], "x"), ["a.md", "b.md"])

    def test_json_array_string(self):
        s = json.dumps(["20261005-142719-README.md", "a.md"])
        self.assertEqual(
            _as_str_list(s, "filenames"),
            ["20261005-142719-README.md", "a.md"])

    def test_json_array_string_with_quotes_angled(self):
        self.assertEqual(
            _as_str_list('["a.md", "b.md"]', "x"), ["a.md", "b.md"])

    def test_comma_separated_string(self):
        self.assertEqual(
            _as_str_list("a.md, b.md , c.md", "x"),
            ["a.md", "b.md", "c.md"])

    def test_bare_single_string(self):
        self.assertEqual(
            _as_str_list("20261005-142719-README.md", "x"),
            ["20261005-142719-README.md"])

    def test_quoted_single_string(self):
        self.assertEqual(_as_str_list('"a.md"', "x"), ["a.md"])

    def test_uncoercible_shape_is_none(self):
        self.assertIsNone(_as_str_list(42, "x"))


class TestRemoveDocumentsShapes(unittest.TestCase):
    """The exact incident shapes must NOT produce char-iteration."""

    def _host(self, docs):
        h = QdrantLocalHost.__new__(QdrantLocalHost)
        h._store = mock.Mock()
        h._store.list_documents.return_value = docs
        h._store.delete_by_filename.return_value = 3
        return h

    def test_json_string_form_deletes(self):
        h = self._host(["OLD-README.md"])
        out = h._remove(json.dumps(["OLD-README.md", "x.md"]))
        self.assertEqual(out["removed"], {"OLD-README.md": 3})
        self.assertEqual(out["missing"], ["x.md"])

    def test_bare_string_form_deletes(self):
        h = self._host(["OLD-README.md"])
        out = h._remove("OLD-README.md")
        self.assertEqual(out["removed"], {"OLD-README.md": 3})
        self.assertEqual(out["missing"], [])

    def test_comma_form_deletes(self):
        h = self._host(["a.md", "b.md"])
        out = h._remove("a.md, b.md")
        self.assertEqual(set(out["removed"]), {"a.md", "b.md"})
        self.assertEqual(out["missing"], [])

    def test_list_form_still_works(self):
        h = self._host(["a.md"])
        out = h._remove(["a.md"])
        self.assertEqual(out["removed"], {"a.md": 3})

    def test_int_payload_clean_error(self):
        h = self._host([])
        out = h._remove(42)
        self.assertIn("error", out)
        h._store.list_documents.assert_not_called()


class TestWebFetchBatchShape(unittest.TestCase):
    def test_single_string_url_coerced(self):
        h = WebSearchLocalHost.__new__(WebSearchLocalHost)
        seen = {}

        def _one(url):
            seen["url"] = url
            return {"url": url, "content": "ok"}

        h._fetch = _one
        out = h._fetch_batch({"urls": "https://example.com"})
        self.assertEqual(seen["url"], "https://example.com")
        self.assertEqual(out["count"], 1)

    def test_json_string_urls_coerced(self):
        h = WebSearchLocalHost.__new__(WebSearchLocalHost)

        def _one(url):
            return {"url": url, "content": "ok"}

        h._fetch = _one
        out = h._fetch_batch(
            {"urls": json.dumps(["https://a.com", "https://b.com"])})
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["ok_count"], 2)